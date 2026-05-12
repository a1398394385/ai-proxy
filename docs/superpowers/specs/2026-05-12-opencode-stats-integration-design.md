# OpenCode Token 统计接入

**日期**: 2026-05-12
**状态**: 草稿

## 背景

当前 StatsService 已有两个数据源：

| 数据源 | 数据库 | DAO | 来源 |
|--------|--------|-----|------|
| proxy | `data/access_log.db` → `token_stats` 表 | `_TokenStatsDao` | AI Proxy 代理请求 |
| Hermes | `~/.hermes/state.db` → `sessions` 表 | `_SessionDao` | Hermes Agent 对话 |

现在需要接入第三数据源 — OpenCode 的 token 统计。

### OpenCode 数据库结构

- 路径：`~/.local/share/opencode/opencode.db`（默认单项目）
- 核心表：
  - `session`：`id`、`model`（JSON `{"id": "model-name", "providerID": "..."}`）、`time_created`（Unix 毫秒）
  - `message`：`id`、`session_id`（FK）、`data`（JSON，含 `role`、`modelID`、`tokens`、`time.created/completed`）
  - `part`：`id`、`message_id`（FK）、`data`（JSON，含各步骤 token 明细）

- token 字段位置：`message.data.tokens` → `{input, output, reasoning, cache: {read, write}}`
- 时间字段：Unix 毫秒时间戳

### 关键抉择

以下 4 个关键决策已在设计阶段确认：

1. **按 message 级别聚合**（非 session 级别）：`GROUP BY json_extract(m.data, '$.modelID')`
2. **reasoning 合并到 output_tokens**：`tokens.output + tokens.reasoning` → `output_tokens`
3. **单数据库模式**：只读取默认 `opencode.db`，多项目后续扩展
4. **opencode 上游标记为 `[OpenCode]`**：在按上游统计中区别于 `__unknown__`
5. **opencode 记录的 request_type 统一为 `"session"`**：与 Hermes sessions 同属 AI coding 会话，前端 badge 和过滤下拉无需改动，二元分类（session / 代理）天然兼容

### 审阅修正（2026-05-12）

基于审阅意见，以下决策已写入设计正文：

| # | 问题 | 决策 |
|----|------|------|
| 1 | request_type 命名 | opencode 记录使用 `"session"`（与 Hermes sessions 一致），前端无需改动 |
| 3 | avg_duration_ms 合并 | 优先级：proxy > opencode > session（取第一个非零值） |
| 4 | strftime 时区差异 | 标注为已知 tradeoff（proxy 用本地时区 vs opencode 用 UTC），偏差约 8h |
| 5 | 分页算法局限 | 标注为已知 tradeoff，与现有双源行为一致 |
| 6 | [OpenCode] 上游成本 | 返回 cost=0（opencode 模型不在 pricing 表中） |
| 2 | trend time key | _OpenCodeDao 返回 `"time"` key，由 _Merger 统一改为 `"date"`（与现有 DAO 一致） |
| 8 | SQL 样例 | 已补充 aggregate_by_model 完整 SQL |

## 目标

- 新增 `_OpenCodeDao`，从 opencode.db 读取数据，暴露与 `_TokenStatsDao` / `_SessionDao` 一致的聚合接口
- `_Merger` 改造为 N 源合并（不再硬编码双源）
- `StatsService` 所有 fetch 方法集成 openCode 数据
- `fetch_requests` 中每个 openCode message 作为独立记录展示
- 数据库不存在时静默降级（返回空），不影响已有功能
- 515 tests 保持全绿

## 设计

### 架构

```
StatsService
  ├── _TokenStatsDao     →  data/access_log.db  (proxy token_stats 表)
  ├── _SessionDao         →  ~/.hermes/state.db  (Hermes sessions 表)
  ├── _OpenCodeDao (NEW)  →  ~/.local/share/opencode/opencode.db
  ├── _Merger (改造)      →  N 源合并
  ├── _CostCalculator     →  成本计算（查询 model_pricing 表）
  └── _UpstreamResolver   →  上游映射
```

### `_OpenCodeDao` 设计

新增类，位于 `stats_service.py`，放在 `_SessionDao` 之后。

**路径解析**：

```python
_OPENCODE_DB_DEFAULT = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
```

通过 `StatsService.__init__` 的 `opencode_db_path` 参数传入，默认使用上述路径。

**数据库不存在处理**：`_get_conn()` 检查文件存在性，不存在返回 `None`，所有查询方法返回空结果。

**数据提取规则**：

| 目标字段 | 来源 |
|----------|------|
| `model` | `json_extract(m.data, '$.modelID')` |
| `request_ts` | `datetime(m.time_created / 1000, 'unixepoch')`（Unix 毫秒 → datetime 字符串） |
| `input_tokens` | `CAST(json_extract(m.data, '$.tokens.input') AS INTEGER)` |
| `output_tokens` | `CAST(json_extract(m.data, '$.tokens.output') AS INTEGER) + CAST(json_extract(m.data, '$.tokens.reasoning') AS INTEGER)` |
| `cached_read_tokens` | `CAST(json_extract(m.data, '$.tokens.cache.read') AS INTEGER)` |
| `cached_write_tokens` | `CAST(json_extract(m.data, '$.tokens.cache.write') AS INTEGER)` |
| `duration_ms` | `json_extract(m.data, '$.time.completed') - json_extract(m.data, '$.time.created')` |

**过滤条件**：`json_extract(m.data, '$.tokens.input') IS NOT NULL`（只统计有 token 数据的 message）。

**时间条件**：openCode 的时间是 Unix 毫秒。注意 strftime('%s', 'now') 返回 UTC 时间戳，而 _TokenStatsDao._period_to_condition 使用 datetime('now', '-1 day') 是本地时区。两者对同一 24h 周期可能产生约 8 小时的偏差（已知 tradeoff，后续可统一为 Unix 时间戳）。

```python
@staticmethod
def _period_to_condition(period: str) -> str:
    """将 period 转换为 Unix 毫秒时间戳条件。"""
    mapping = {
        "day": "86400000",
        "24h": "86400000",
        "week": "604800000",
        "7d": "604800000",
        "month": "2592000000",
        "30d": "2592000000",
    }
    delta_ms = mapping.get(period, "604800000")
    return f"m.time_created >= (strftime('%s', 'now') * 1000 - {delta_ms})"
```

**aggregate_by_model SQL 样例**：

```sql
SELECT
    json_extract(m.data, '$.modelID') as model,
    COUNT(*) as request_count,
    SUM(CAST(json_extract(m.data, '$.tokens.input') AS INTEGER)) as total_input,
    SUM(CAST(json_extract(m.data, '$.tokens.output') AS INTEGER)
        + CAST(json_extract(m.data, '$.tokens.reasoning') AS INTEGER)) as total_output,
    SUM(CAST(json_extract(m.data, '$.tokens.cache.read') AS INTEGER)) as total_cache_read,
    SUM(CAST(json_extract(m.data, '$.tokens.cache.write') AS INTEGER)) as total_cache_write
FROM message m
JOIN session s ON s.id = m.session_id
WHERE m.time_created >= (strftime('%s', 'now') * 1000 - 604800000)
  AND json_extract(m.data, '$.tokens.input') IS NOT NULL
GROUP BY json_extract(m.data, '$.modelID')
ORDER BY total_output DESC
```

**提供的方法**：

| 方法 | 用途 | 返回格式 |
|------|------|----------|
| `aggregate_by_model(period)` | 按 modelID 分组聚合 | `[{model, request_count, input_tokens, output_tokens, cached_read_tokens, cached_write_tokens, total_tokens}]` |
| `aggregate_summary(period)` | 汇总统计 | `{period, request_count, input_tokens, output_tokens, cached_read_tokens, cached_write_tokens, total_tokens, avg_duration_ms}` |
| `aggregate_trend(period)` | 时间趋势 | `[{time, request_count, input_tokens, output_tokens, cached_read_tokens, cached_write_tokens, total_tokens}]` |
| `query_messages_paged(period, model, request_type, limit, offset)` | 分页查询（请求日志） | `(records_list, total_count)`，record 为统一格式 dict；`request_type` 仅 `"session"` 时返回数据，其余返回空 |

**聚合粒度**：
- `aggregate_by_model` 和 `aggregate_summary`：按 message 聚合，不按 session 去重
- `aggregate_trend`：按时间粒度分组（`day` → 按小时，其他 → 按天），使用 `m.time_created` 作为时间基准。返回 `time` key（如 `"2026-05-12 14:00"`），由 `_Merger` 统一改名为 `"date"` — 与 `_TokenStatsDao` 和 `_SessionDao` 的约定一致
- `query_messages_paged`：每条 message 一行

**request_type 约定**：`query_messages_paged` 返回的记录中，`request_type` 统一为 `"session"`。当调用方传入 `request_type` 参数时，只有 `"session"` 匹配（`_OpenCodeDao` 返回数据），其余值（如 `"proxy"`）返回空。这使前端「session / 代理」二元过滤无需改动。

### `_Merger` 改造

现有三个方法的签名从「两个固定参数」改为「可变参数」：

```python
class _Merger:
    """N 数据源合并：按规范化模型名求和，字段名统一为 cache_*，trend key 统一为 date"""

    @staticmethod
    def merge_summary(*summaries: dict) -> dict: ...

    @staticmethod
    def merge_model_lists(*lists: list) -> list: ...

    @staticmethod
    def merge_trend_lists(*lists: list) -> list: ...
```

内部实现：遍历所有参数，同名模型/时间点各数值字段求和。字段 `_rename` 逻辑不变。

### `StatsService` 改动

**新增参数和属性**：

```python
class StatsService:
    def __init__(
        self,
        data_db_path: str,
        state_db_path: str,
        opencode_db_path: str | None = None,
    ) -> None:
        ...
        self.opencode_db_path = Path(opencode_db_path) if opencode_db_path else _OPENCODE_DB_DEFAULT
        self._opencode_dao = None  # 懒加载
```

**新增 DAO 获取方法**：

```python
def _get_opencode_dao(self) -> _OpenCodeDao | None:
    """懒加载获取 OpenCodeDao，数据库不存在返回 None。"""
    if self._opencode_dao is None:
        dao = _OpenCodeDao(self.opencode_db_path)
        if dao.db_path.exists():
            self._opencode_dao = dao
        else:
            return None
    return self._opencode_dao
```

**各方法集成策略**：

| 方法 | 集成方式 |
|------|----------|
| `fetch_summary` | 三源 `merge_summary(proxy, session, opencode)` |
| `fetch_by_model` | 三源 `merge_model_lists(proxy, session, opencode)` + 逐模型计费 |
| `fetch_trend` | 三源 `merge_trend_lists(proxy, session, opencode)` + 成本均摊 |
| `fetch_by_upstream` | openCode 模型通过 `aggregate_by_model` 获取，统一归入 `[OpenCode]` 上游，三源合并 |
| `fetch_requests` | 三源分页：token_stats + sessions + opencode messages，按 `request_ts DESC` 排序后分页 |
| `fetch_by_model_requests` | 同 fetch_requests，加上 model 过滤 |
| `fetch_all_summaries` | 内部循环调用三源 `fetch_summary`，无需额外改动 |

`_get_opencode_dao()` 返回 None 时，对应数据源跳过（传空结果给 Merger）。

### avg_duration_ms 合并策略

三源合并时 `avg_duration_ms` 取优先级：**proxy > opencode > session**。即：
- proxy 侧有值（非 0）→ 使用 proxy 值
- proxy 无值 → 取 opencode 值
- 都无值 → 取 session 值（始终为 0，sessions 表无此字段）

### fetch_requests 分页算法（已知 tradeoff）

当前 `fetch_requests` 的实现是：
1. 从各源取 `limit + offset` 条
2. 合并后按 `request_ts DESC` 排序
3. 切片 `[offset:offset+limit]`

严格来说，当各源的数据密度不均时，前 `limit + offset` 条不一定覆盖合并后的分页窗口。**但此行为与现有双源实现一致**，加入 opencode 第三源不会改变算法性质。生产环境已验证该算法在常见数据分布下工作正常。

### 请求日志中的 openCode 记录

每条 message 映射为统一格式：

```python
{
    "request_id": f"oc-msg-{message_id}",
    "request_type": "session",
    "model": model_id,         # 来自 message.data.modelID
    "target_model": model_id,  # 同 model（不需要 normalize）
    "request_ts": datetime_string,
    "duration_ms": duration_ms,  # time.completed - time.created
    "input_tokens": N,
    "output_tokens": N,
    "cached_read_tokens": N,
    "cached_write_tokens": N,
    "status": "completed",
    "_source": "opencode",
}
```

### 按上游统计中 openCode 的归属

在 `fetch_by_upstream` 中，openCode 模型不在 config.db 的 `target_models` 表中。单独聚合后标记上游为 `[OpenCode]`：

openCode 模型不在 `model_pricing` 表中，`[OpenCode]` 上游的 `estimated_cost_cny` 始终为 **0**，`base_url` 为 **None**（与 `__unknown__` 处理一致，没有对应的上游 URL）。已知限制，后续可为 opencode 模型补充定价数据。

```
token_stats → 按 upstream_map 映射
sessions    → 按 upstream_map 映射
opencode    → 全部归入 "[OpenCode]" 上游
```

```python
# fetch_by_upstream 中新增：
opencode_dao = self._get_opencode_dao()
if opencode_dao:
    oc_models = opencode_dao.aggregate_by_model(period)
    # 合并 opencode 按模型数据到 "[OpenCode]" 桶
    for model_row in oc_models:
        merge_into_upstream_bucket("[OpenCode]", model_row)
```

### 涉及文件

| 文件 | 改动量 | 说明 |
|------|--------|------|
| `stats_service.py` | ~200 行新增 | `_OpenCodeDao` 类 + `_Merger` N 源改造 + `StatsService` 集成 |
| `test/test_stats_service.py` | ~150 行新增 | `TestOpenCodeDao` 测试类 |
| `server/__init__.py` | 0 行 | 默认路径无需显式传参 |

### 测试策略

- 新建 `TestOpenCodeDao` 类，用临时 SQLite 文件模拟 opencode db 结构
- 测试 `aggregate_by_model` / `aggregate_summary` / `aggregate_trend` 三种聚合
- 测试数据库不存在时静默降级
- 测试 `query_messages_paged` 分页逻辑
- 新增 `_Merger` 三源合并测试（验证 3 个参数等价性）
- 现有 515 tests 不能断
- `fetch_requests` 端点冒烟测试不破坏前端 API 兼容性

### 多项目扩展（out of scope，本设计不实现）

后续如需支持多 opencode 项目（`opencode-{safe}.db`），可扩展为：
1. `opencode_db_path` 参数接受目录路径
2. 自动扫描 `opencode*.db`
3. 每个 db 独立创建 `_OpenCodeDao`，结果求和合并

## 参考

- [2026-05-12 StatsService 双源合并重构](./2026-05-12-stats-service-merge-refactor-design.md)
- OpenCode 源码路径解析：`packages/core/src/global.ts:10` → `packages/opencode/src/storage/db.ts:32`
