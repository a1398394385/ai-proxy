# Token 统计重构：统一数据查询层

**日期:** 2026-05-13
**状态:** 设计完成，待实现

## 动机

当前 `StatsService` 的 6 个 `fetch_*` 方法各自独立调用 DAO 聚合方法，导致：

- **数据不一致**：顶部 4 个 KPI 卡片与下方的「按模型统计」「按上游统计」「请求日志」数字对不上
- **重复查询**：`fetch_summary()` 调 6 次 DAO 方法，`fetch_trend()` 额外调 `fetch_by_model()` 算成本
- **hack 逻辑**：趋势图的成本按 token 比例均摊而非精确计算

## 核心设计

新增单一数据源方法 `_fetch_unified_records()`，所有 `fetch_*` 方法改为调用它后在内存中聚合。

```
                        _fetch_unified_records()
                       /    |    |    \
                      /     |    |     \
              summary  by_model by_upstream trend requests
```

数据一致性由数学保证：所有视图来自同一次查询结果，求和必等。

---

## 统一记录 Schema

```python
{
    "request_id": str,           # token_stats: request_id / sessions: "sess-{id}" / opencode: "oc-msg-{id}"
    "model": str,                # 规范化模型名（sessions 已去 [ctx] 后缀）
    "request_type": str,         # "responses"/"messages"/"chat_completions"/"session"
    "request_ts": str,           # "YYYY-MM-DD HH:MM:SS"
    "duration_ms": int | None,
    "status": str,               # "completed"/"failed"

    # 4 项 token
    "input_tokens": int,
    "output_tokens": int,
    "cache_read_tokens": int,
    "cache_write_tokens": int,

    # 4 项独立成本 (CNY)
    "input_cost_cny": float,
    "output_cost_cny": float,
    "cache_read_cost_cny": float,
    "cache_write_cost_cny": float,

    # 上游（区分来源）
    "upstream_id": str,          # proxy: upstreams.id / hermes: "hermes" / opencode: "opencode"
}
```

**不包含的字段**（由调用方填充）：
- `display_name` → 调用方通过 `_CostCalculator.get_display_name()` 获取
- `upstream_name` / `base_url` → 调用方通过 `_UpstreamResolver` 获取
- `_source` → `upstream_id` 已能区分来源

---

## 核心方法签名

```python
def _fetch_unified_records(
    self,
    period: str,                   # "day" / "week" / "month"
    model: str | None = None,      # 内部完成规范化匹配
    request_type: str | None = None,
    limit: int | None = None,      # 指定时启用分页
    offset: int = 0,
) -> list | tuple[list, int]:
```

**返回值：**
- 无分页（`limit=None`）: `[record, ...]`
- 有分页（`limit` 指定）: `([record, ...], total_count)`

**内部流程：**

1. 模型名规范化（一次，各源复用）
2. 依次查询三源 → 各 DAO 返回统一格式 list
3. 合并 → 逐条计算 4 项成本（通过 `_CostCalculator`）
4. 按 `request_ts DESC` 排序
5. 分页则切片返回 `(records[offset:limit], total)`，否则返回全量

---

## 现有方法改造

### fetch_summary(period) → dict

```python
records = self._fetch_unified_records(period)
return {
    "period": period,
    "request_count": len(records),
    "input_tokens": sum(r["input_tokens"] for r in records),
    "output_tokens": sum(r["output_tokens"] for r in records),
    "cache_read_tokens": sum(r["cache_read_tokens"] for r in records),
    "cache_write_tokens": sum(r["cache_write_tokens"] for r in records),
    "total_tokens": sum(...),
    "estimated_cost_cny": round(sum(4 项成本和), 6),
    "avg_duration_ms": avg(...),
}
# 注意：fetch_summary 暂不返回 4 项成本拆分（保持 API 兼容）。
# 4 项成本已在 _fetch_unified_records 的每条记录中计算好，
# 前端若未来需要 KPI 卡片展示成本拆分，只需在此处多加 4 个 sum 即可。
```

### fetch_by_model(period) → list

```python
records = self._fetch_unified_records(period)
# groupby "model" → sum 各项 → 附 display_name → 按 total_tokens 降序
```

### fetch_by_upstream(period) → dict

```python
records = self._fetch_unified_records(period)
# groupby "upstream_id" → sum 各项 → 附 upstream_name

# upstream_name 解析优先级：
#   1. upstream_id == "hermes"  → upstream_name = "[Hermes]"
#   2. upstream_id == "opencode" → upstream_name = "[OpenCode]"
#   3. 其他 → 调 _UpstreamResolver.resolve_by_id(upstream_id) 获取真实上游名
#
# 注意：_UpstreamResolver 只认识 upstreams 表中的真实上游，
# "hermes"/"opencode" 不在表中，resolve_by_id() 会返回 "__unknown__"。
# 因此必须在 fetch_by_upstream 中硬编码这两个虚拟上游的展示名。
```

### fetch_trend(period) → list

```python
records = self._fetch_unified_records(period)
# 按时间粒度分桶（24h→小时, 7d/30d→天）
# 每个桶 sum 各项 token 和 4 项成本
# 不再需要按 token 比例均摊成本
```

**时间分桶规则**（从 SQL 迁移到 Python，时区一致性要求见下）：

| period | 粒度 | 分桶 key 格式 | 实现 |
|--------|------|-------------|------|
| day/24h | 小时 | `"YYYY-MM-DD HH:00"` | `request_ts[:13] + ":00"` |
| week/7d | 天 | `"YYYY-MM-DD"` | `request_ts[:10]` |
| month/30d | 天 | `"YYYY-MM-DD"` | `request_ts[:10]` |

由于 `request_ts` 在 DAO 的 `query_raw()` 中已统一为字符串格式（SQLite 的 `datetime()` 返回
localtime 字符串，与现有行为一致），Python 侧直接用字符串切片分桶，无需再做时区转换。
这避免了原来三个 DAO 各自在 SQL 中做 `localtime` 转换的分散逻辑。

### fetch_requests(period, model, request_type, limit, offset) → dict

```python
records, total = self._fetch_unified_records(period, model, request_type, limit, offset)
return {"requests": records, "total": total, "limit": limit, "offset": offset}
```

### fetch_all_summaries() → dict

不变，循环调 `fetch_summary()` 三次。每次内部走统一方法。

### fetch_by_model_requests(model, period, limit, offset) → 删除

功能与 `fetch_requests(period, model=model, ...)` 重复。前端 `/api/token_stats/by_model/{m}/requests`
端点内部改调 `fetch_requests()`，由 API 层补上 `"model"` 字段以保持返回格式兼容：

```python
# token_api.py 中 /api/token_stats/by_model/{m}/requests 的处理
result = handler.stats_service.fetch_requests(
    period=period, model=model, limit=limit, offset=offset
)
result["model"] = model  # 保持与旧 fetch_by_model_requests 返回一致的字段
json_response(handler, result)
```

---

## DAO 层改动

每个 DAO 新增 `query_raw(period, model, request_type)` → `list[dict]`：

| DAO | 关键映射 |
|-----|---------|
| `_TokenStatsDao` | `upstream_id` 从 `ts.upstream_id` 读，NULL 时用 `"__unknown__"` |
| `_SessionDao` | `upstream_id` 固定 `"hermes"`，model 去掉 `[ctx]` 后缀 |
| `_OpenCodeDao` | `upstream_id` 固定 `"opencode"` |

要删除的代码：
- 所有 `aggregate_*` 方法（3 个 DAO × 4 个方法 = 12 个）
- `_Merger` 整个类（`merge_summary/merge_model_lists/merge_trend_lists` 不再需要）
- `query_token_stats()` / `query_sessions_paged()` / `query_messages_paged()`（被 `query_raw` 替代）

保留：
- `_UpstreamResolver` — 调用方用于填充 upstream_name
- `_CostCalculator` — 统一方法内部用于逐条算成本（需新增 `calculate_breakdown()` 方法）

删除：
- `_load_upstream_map()` — 无外部调用者
- `_resolve_upstream()` — 无外部调用者

### 字段归一化映射

`_TokenStatsDao` 的 DB 列名是 `cached_read_tokens` / `cached_write_tokens`（带 `d`），
需在 `query_raw()` 构造 dict 时主动重命名为 `cache_read_tokens` / `cache_write_tokens`。
`_SessionDao` 和 `_OpenCodeDao` 的 `_row_to_record()` 已输出 `cache_*` 格式，无需改动。

### _CostCalculator 新增 calculate_breakdown()

```python
def calculate_breakdown(
    self,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
) -> dict:
    """返回 4 项独立成本，不求和。

    Returns:
        {"input_cost_cny": float, "output_cost_cny": float,
         "cache_read_cost_cny": float, "cache_write_cost_cny": float}
        模型无定价时四项均为 0.0。
    """
```

`_fetch_unified_records()` 对每条记录调一次 `calculate_breakdown()`，将返回的 4 个值直接赋给 record。

---

## API 兼容性

`server/token_api.py` 改动 1 行：`/api/token_stats/by_model/{m}/requests` 内部从调
`fetch_by_model_requests()` 改为调 `fetch_requests()`，并在返回 dict 上补 `"model"` 字段。
其余端点完全不变。所有端点 JSON 结构保持兼容。

---

## 前端改动

两处独立的 `isSession` 判断需改为 `upstream_id` 判断：

| 位置 | 行号 | 当前 | 改为 |
|------|------|------|------|
| `renderModelTable` 展开行 | tokens.js:528 | `r.request_type === 'session'` | `r.upstream_id === 'hermes' \|\| r.upstream_id === 'opencode'` |
| `renderRequestTable` 行渲染 | tokens.js:700 | `r.request_type === 'session'` | 同上 |

**兼容性验证**：新统一记录中 sessions/opencode 来源的 `input_tokens` 等 4 项字段始终存在
（当前 `_SessionDao._row_to_record()` 已包含这些字段），所以展开行 token 单元格的渲染逻辑无需改动。

| 其他改动 | 说明 |
|---------|------|
| 模型详情展开行成本 | `r.estimated_cost_cny` 改为 `r.input_cost_cny + r.output_cost_cny + r.cache_read_cost_cny + r.cache_write_cost_cny` |

前端改动量 ~8 行。

---

## 错误处理

- 三源独立查询，任一源不可用不影响其他源
- 模型无定价时，4 项成本均为 0，不抛异常
- data db / state.db / opencode.db 不存在时对应源返回空列表

---

## 测试策略

`test/test_stats_service.py`（当前 3308 行）需大幅重写。以下测试类因依赖被删除的内部类，需整类重写：

| 需重写的类 | 原因 |
|-----------|------|
| `TestMerger` (行 2678) | 9 个测试直接测要删除的 `_Merger` 类 |
| `TestFetchRequestsMerged` | 依赖三源各自分页再合并的内部流程 |
| `TestFetchByModelRequestsMerged` | 同上 |
| `TestFetchByUpstreamMerged` (213 行) | 测的是旧的多源聚合路径 |

需新增的测试：

- `_fetch_unified_records` 的单元测试：三源数据合并、字段归一化、成本计算、分页
- 各 `fetch_*` 方法的聚合正确性测试
- 交叉验证：`fetch_summary` 的总 token ≡ `fetch_by_model` 各行求和 ≡ `fetch_by_upstream` 各行求和
- 分页测试：`fetch_requests(limit=10, offset=0)` 的 total 与全量记录数一致
- 成本测试：4 项成本字段非负，总计与 `estimated_cost_cny` 一致
- 虚拟上游测试：hermes/opencode 的 upstream_name 正确展示为 `[Hermes]`/`[OpenCode]`
- `_CostCalculator.calculate_breakdown()` 单独测试

**保留不变**：`setUp` 模式（`tempfile.TemporaryDirectory` + 建表 + 种子数据）和现有的 DB schema 初始化逻辑。

---

## 代码量预估

| 文件 | 预计变动 |
|------|---------|
| `stats_service.py` | 删 ~400 行，增 ~200 行（净减 ~200） |
| `server/token_api.py` | ~3 行改动 |
| `static/js/pages/tokens.js` | ~8 行改动 |
| `test/test_stats_service.py` | 删 ~1200 行（_Merger + 旧聚合测试），增 ~800 行（新统一方法测试），净减 ~400 |
