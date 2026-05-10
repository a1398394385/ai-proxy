# Token 统计页面合并代理请求数据设计

**日期**: 2026-05-10
**状态**: 待实施

## 背景

当前 Token 统计页面仅展示 `~/.hermes/state.db` 中 `sessions`/`messages` 表的数据（Claude Code 会话级）。代理请求的 token 使用量记录在 `data/access_log.db` 的 `token_stats` 表中，但未纳入统计页面。

## 目标

将 `token_stats` 表数据合并到现有统计页面，前端无改动，后端数据获取逻辑分离以便后续剔除 Hermes 数据。

## 数据源对比

| 维度 | sessions (state.db) | token_stats (access_log.db) |
|------|---------------------|----------------------------|
| 粒度 | 会话级 | 单次请求级 |
| 模型字段 | `model` | `target_model`（实际调用模型） |
| 时间字段 | `started_at`（UNIX timestamp） | `request_ts`（`YYYY-MM-DD HH:MM:SS`，本地时间） |
| 独有字段 | `message_count`, `title` | `request_type`, `duration_ms` |
| 数据量 | 178 条 | 830 条 |

## 设计决策

1. **合并策略**：同名模型 token 数直接相加，不区分来源
2. **模型名映射**：token_stats 使用 `target_model` 作为模型名
3. **趋势合并**：同一时间点的两个数据源 token 数相加
4. **前端零改动**：接口契约不变
5. **后端分离**：sessions 查询和 token_stats 查询逻辑各自独立，便于后续剔除

## 实现方案：后端合并返回

修改 `server.py` 中的三个主函数，在每个函数内分别查询两份数据后合并。

### 辅助函数

提取三个辅助函数，避免重复查询逻辑：

#### `_get_proxy_token_aggregate(period)`

查询 token_stats 汇总数据。时间过滤将 `get_time_range()` 返回的 timestamp 转为字符串格式匹配 `request_ts`。返回 `request_count`、`total_input`、`total_output`、`total_cache_read`、`total_cache_write`。

- `status='completed'` 过滤：incomplete 表示流式中断，token 统计不完整，不应计入汇总
- `request_count` = `COUNT(*)`，语义为代理 API 调用次数（sessions 的 request_count 是 `SUM(message_count)`，语义同为 API 调用次数，直接相加合理）

**SQL 列名注意**：token_stats 表中缓存列名为 `cached_read_tokens` / `cached_write_tokens`（带 d），与 sessions 的 `cache_read_tokens` / `cache_write_tokens`（无 d）不同，SQL 中必须使用正确的列名。

#### `_get_proxy_token_by_model(period)`

查询 token_stats 按 `target_model` 分组。返回每模型的 `request_count`（COUNT(*)）、`input_tokens`、`output_tokens`、`cached_read_tokens`、`cached_write_tokens`。

#### `_get_proxy_token_trend(period)`

查询 token_stats 按时间粒度分组：
- `day`：按小时 `strftime('%Y-%m-%d %H', request_ts)`
- `week`/`month`：按天 `strftime('%Y-%m-%d', request_ts)`

**返回与 sessions 趋势完全相同的时间线结构**（含补 0 的完整时间点），这样主函数的合并只需逐点相加，无需再处理时间对齐。内部实现：生成与 sessions 相同的完整时间线骨架，将查询结果填入对应时间点，空点补 0。

### 主函数改造

#### `get_token_stats(period)`

1. 原有 sessions 逻辑不变
2. 调用 `_get_proxy_token_aggregate(period)` 获取代理汇总
3. 各字段直接相加
4. 代理数据的成本按 `target_model` 逐模型调用 `calculate_cost()` 后累加到总成本

#### `get_token_stats_by_model(period)`

1. 原有 sessions 逻辑不变，得到 `model → stats` 映射
2. 调用 `_get_proxy_token_by_model(period)` 得到 `target_model → stats` 映射
3. 以模型名为 key 合并：已存在则 token 数相加，不存在则新增
4. 合并后统一计算成本

#### `get_daily_token_trend(period)`

1. 原有 sessions 逻辑不变，生成完整时间线
2. 调用 `_get_proxy_token_trend(period)` 得到代理趋势数据（已含完整时间线+补 0）
3. 逐点相加 token 数和成本
4. 无数据的时间点仍补 0

### 时间过滤适配

sessions 使用 `started_at`（REAL, UNIX timestamp），token_stats 使用 `request_ts`（TEXT, `YYYY-MM-DD HH:MM:SS`）。两者均为本地时间（`request_ts` 由 `time.strftime()` 生成，sessions 通过 `localtime` 转换），无时区错位。

辅助函数内将 `get_time_range()` 的 timestamp 结果转为字符串：

```python
start_str = datetime.fromtimestamp(start_ts).strftime("%Y-%m-%d %H:%M:%S")
end_str = datetime.fromtimestamp(end_ts).strftime("%Y-%m-%d %H:%M:%S")
```

## 变更范围

- `server.py`：3 个主函数改造 + 3 个辅助函数新增
- 前端：无改动
- API 接口：契约不变
- 数据库：无结构变更

## 后续剔除 Hermes 数据

删除 `_get_proxy_token_aggregate()`、`_get_proxy_token_by_model()`、`_get_proxy_token_trend()` 及三个主函数中的 sessions 查询段即可。辅助函数可作为唯一数据源保留。
