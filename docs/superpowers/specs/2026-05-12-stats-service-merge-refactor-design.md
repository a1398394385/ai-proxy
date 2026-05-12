# StatsService 双源合并重构

**日期**: 2026-05-12
**状态**: 已批准

## 背景

server.py 中 token 统计代码存在两套并行的数据源和逻辑：

| API 端点 | 当前调用 | 数据源 |
|----------|---------|--------|
| `/api/token_stats` | `get_token_stats()` | state.db sessions 表 |
| `/api/token_stats/by_model` | `get_token_stats_by_model()` | state.db sessions 表 |
| `/api/token_stats/trend` | `get_daily_token_trend()` | sessions + token_stats 混合 |
| `/api/token_stats/summary` | `get_token_stats()` ×3 | 同上 |
| `/api/token_stats/requests` | `stats_service.fetch_requests()` | token_stats + sessions 双源 |
| `/api/token_stats/by_upstream` | `stats_service.fetch_by_upstream()` | token_stats + sessions 双源 |
| `/api/token_stats/by_model/{model}/requests` | `stats_service.fetch_by_model_requests()` | token_stats + sessions 双源 |

问题：
1. 旧 3 个 API 只查 sessions 表，day 期间数据为空（Hermes 未调用代理）
2. 新 3 个 API 走 stats_service 但用"补缺"策略而非求和
3. server.py 有 3 个孤儿函数 `_get_proxy_token_*` 无人调用
4. 成本计算两套：server.py `calculate_cost()` 和 stats_service `_CostCalculator`
5. `_normalize_model_name()` 两处重复

## 目标

- 所有 6 个 API 端点迁移到 stats_service.py，server.py 只做参数校验和路由转发
- 所有方法合并双数据源（proxy token_stats + sessions），策略为**求和**（两边数据完全独立）
- 成本计算统一在 stats_service.py
- 后续 Hermes 接入代理后，移除 sessions 源只需删 Merger 中 sessions 部分

## 设计

### 架构

```
server.py (路由层：参数校验 + 调用 + json_response)
  → StatsService (编排层)
    → _TokenStatsDao (proxy 数据，access_log.db)
    → _SessionDao (Hermes 数据，state.db)
    → _Merger (合并层：按模型名求和)
    → _CostCalculator (成本计算)
    → _UpstreamResolver (上游映射)
```

### _Merger 合并层

```python
class _Merger:
    """双数据源合并：按规范化模型名求和"""

    @staticmethod
    def merge_model_lists(proxy_models: list, session_models: list) -> list:
        """合并两个 by_model 列表，同名模型 token 求和"""

    @staticmethod
    def merge_trend_lists(proxy_trend: list, session_trend: list) -> list:
        """合并两个趋势列表，同日期同指标求和"""

    @staticmethod
    def merge_summary(proxy_summary: dict, session_summary: dict) -> dict:
        """合并两个汇总 dict，各数值字段求和"""
```

合并规则：
- `merge_model_lists`：按 `_normalize_model_name(model)` 分组，数值字段求和
- `merge_trend_lists`：按 `date` 分组，input/output/cache_tokens 各自求和
- `merge_summary`：`request_count`、`input_tokens`、`output_tokens`、`cache_read_tokens`、`cache_write_tokens`、`total_tokens` 直接相加，`estimated_cost_usd` 重新计算

### API 端点映射

| API 端点 | StatsService 方法 | 合并策略 |
|----------|------------------|---------|
| `/api/token_stats` | `fetch_summary(period)` | Merger.merge_summary |
| `/api/token_stats/by_model` | `fetch_by_model(period)` | Merger.merge_model_lists |
| `/api/token_stats/trend` | `fetch_trend(period)` | Merger.merge_trend_lists |
| `/api/token_stats/summary` | 调 3 次 `fetch_summary` | server.py 组装 dict |
| `/api/token_stats/requests` | `fetch_requests(...)` | 已有，无需改 |
| `/api/token_stats/by_upstream` | `fetch_by_upstream(period)` | Merger.merge_model_lists |
| `/api/token_stats/by_model/{model}/requests` | `fetch_by_model_requests(...)` | 已有，无需改 |

**响应格式不变，前端零改动。**

### server.py 清理

**删除的函数**（约 400 行）：
- `get_time_range()` (L328)
- `_get_proxy_token_aggregate()` (L341)
- `_get_proxy_token_by_model()` (L371)
- `_get_proxy_token_trend()` (L407)
- `get_token_stats()` (L544)
- `_normalize_model_name()` (L612)
- `get_token_stats_by_model()` (L618)
- `get_daily_token_trend()` (L698)
- `calculate_cost()` (L221)
- `_get_stats_calculator()` (L175)
- `get_cc_switch_db()` (L193)
- `get_model_pricing()` (L211)

**保留**：
- `_get_stats_service()` — 路由转发用
- `get_state_db()` — StatsService 内部用
- `get_access_log_db()` — StatsService 内部用

**路由转发模板**：
```python
if path == "/api/token_stats":
    period = qs.get("period", ["week"])[0]
    if period not in ("day", "week", "month"): period = "week"
    stats = _get_stats_service().fetch_summary(period)
    return json_response(self, stats)
```

### StatsService 方法改造

现有 `fetch_by_model`、`fetch_by_upstream`、`fetch_trend`、`fetch_summary` 的合并策略从"proxy 优先 sessions 补缺"改为"求和"：

```python
def fetch_by_model(self, period: str) -> list:
    dao = self._get_dao()
    session_dao = self._get_session_dao()
    proxy_models = dao.aggregate_by_model(period)
    session_models = session_dao.aggregate_by_model(period)
    merged = _Merger.merge_model_lists(proxy_models, session_models)
    # 成本计算
    calculator = self._get_calculator()
    for m in merged:
        m["estimated_cost_usd"] = calculator.calculate(...)
    return merged
```

### 测试策略

- 新增 `_Merger` 类单元测试
- 现有 `test/test_stats_service.py` 合并逻辑测试改为验证"求和"
- 删除 server.py 中已迁移函数的测试
- 端到端冒烟 `test_e2e_smoke.py` 验证 API 响应格式不变
- 全量 406 tests 不能断

### 后续移除 sessions 源

当 Hermes 接入代理后：
1. `_Merger` 方法中删除 sessions 参数和合并逻辑，直接返回 proxy 数据
2. 删除 `_SessionDao` 类
3. StatsService 中删除 `self._session_dao` 和 `_get_session_dao()`
4. `fetch_*` 方法中删除 session DAO 查询步骤
