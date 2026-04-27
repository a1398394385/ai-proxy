# Token 统计抽取设计文稿

**日期**: 2026-04-26
**目标**: 将 token 统计逻辑从 proxy.py 抽取为独立的 `token_stats.py` 模块，统一处理 Anthropic / OpenAI Chat / OpenAI Responses 三种 usage 格式。

---

## 问题陈述

当前 token 统计的格式兼容逻辑散布在三处：

1. `proxy.py` 非流式路径 — 从 Chat Completions 响应的 `usage` 提取
2. `proxy.py` 流式路径 — 从 SSE `response.completed` 事件的 `response.usage` 提取
3. `transform.py` `_emit_completion` — 将上游 usage 归一化为 Responses API 格式，同时做 Anthropic cache 适配

每次新增一种格式（如 Anthropic cache 字段）需要改动多个文件。需要将格式检测和提取逻辑集中到一个地方。

---

## 数据库

写入目标：`data/access_log.db` 中的 `token_stats` 表（与 `debug_log` 同库不同表，已由 `request_logger.py` 初始化）：

```sql
CREATE TABLE token_stats (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id          TEXT NOT NULL,
    agent               TEXT NOT NULL,
    model               TEXT NOT NULL,
    target_model        TEXT NOT NULL,
    request_ts          TEXT NOT NULL,
    duration_ms         INTEGER,
    input_tokens        INTEGER DEFAULT 0,
    output_tokens       INTEGER DEFAULT 0,
    cached_read_tokens  INTEGER DEFAULT 0,
    cached_write_tokens INTEGER DEFAULT 0,
    status              TEXT DEFAULT 'completed',
    created_at          TEXT NOT NULL
);
```

与 `request_logger.py` 的关系：
- `request_logger.py` 负责 `debug_log` 表的记录（raw_request、converted_request、upstream_response、converted_response）
- `token_stats.py` 负责 `token_stats` 表的写入，替代 `RequestLogger.log_token_stats()` 方法
- 两者写入同一数据库，各自操作自己负责的表，无重复记录

---

## 架构

```
proxy.py                              token_stats.py
  ├─ 非流式 ── chat_response["usage"] ──→ record_token_stats(usage, context)
  └─ 流式   ── final_usage            ──→ record_token_stats(usage, context)
                                               │
transform.py                                   │
  └─ _emit_completion: state.usage  ──→ SSE──→─┘
     (usage 块完全透传原始字段，不做 Anthropic 适配)
```

**原则**:
- `transform.py` 负责 SSE 事件格式（Responses API 结构），不关心 usage 内部字段含义
- `token_stats.py` 统一负责：格式检测 → 字段提取 → DB 写入
- proxy.py 只需收集 usage dict 和 context，调用一个函数即可

---

## 接口设计

### 唯一公开函数

```python
def record_token_stats(usage: dict, context: dict) -> None:
```

参数说明：

| 参数 | 类型 | 说明 |
|------|------|------|
| `usage` | dict | 上游返回的原始 usage dict，null/空 dict 时直接 return |
| `context` | dict | 见下方 |

context 必需字段：

| 字段 | 类型 | 缺少时行为 |
|------|------|-----------|
| `request_id` | str | **无此字段则 warning + return**（无法写入有意义记录） |
| `agent` | str | 默认 `"unknown"` |
| `model` | str | 默认 `"unknown"` |
| `target_model` | str | 默认 `"unknown"` |
| `request_ts` | str | 默认空字符串 |
| `duration_ms` | int | 默认 0 |

### 格式检测策略 — 按优先级提取，非"非零优先"

每个提取项有固定的 key 优先级列表。**按顺序查找，取第一个存在且 > 0 的值**。如果都为 0 或不存在，返回 0。不使用 "非零优先" 是因为两个格式的字段可能同时非 0 但值不同。

| 提取项 | 优先级 1 | 优先级 2 | 优先级 3 |
|--------|----------|----------|----------|
| input_tokens | `prompt_tokens` | `input_tokens` | — |
| output_tokens | `completion_tokens` | `output_tokens` | — |
| cache_read | `cache_read_input_tokens` | `prompt_tokens_details.cached_tokens` | `input_tokens_details.cached_tokens` |
| cache_write | `cache_creation_input_tokens` | `input_tokens_details.cache_creation_input_tokens` | — |

**设计理由**: Anthropic 的 cache 字段（`cache_read_input_tokens`）在 usage 顶层，与 OpenAI Chat 的 `prompt_tokens` 同级。qwen 通过 LiteLLM 返回时，usage 中同时存在 `prompt_tokens`（Chat 格式）和 `cache_read_input_tokens`（Anthropic 格式）。按优先级查找确保正确命中。

### 核心辅助函数

```python
def _find_first(usage: dict, keys: list, default=0) -> int:
    """按 keys 顺序查找 usage，返回第一个值 > 0 的 key 的值。"""
    for k in keys:
        v = usage.get(k)
        if v is not None and v > 0:
            return v
    return default
```

每个提取项调用 `_find_first(usage, [p1, p2, p3])`，逻辑简洁统一。

---

## proxy.py 变更

两处调用点替换为 `record_token_stats(usage, context)`：

**非流式路径**：`chat_response["usage"]` 直接传入
**流式路径**：从 `response.completed` SSE 事件解析出的 `response.usage` 传入（已经是 `_emit_completion` 透传的原始字段集合）

`request_logger.log_token_stats()` 方法保留但从 proxy.py 中不再调用。

---

## transform.py 变更

`_emit_completion` 中的 `usage` 构建简化为：**透传 `state.usage` 的所有原始字段，仅补充 Responses API 规范字段名**。

`state.usage` 的来源：在 `create_codex_sse_stream` 中，从上游 Chat Completions SSE 流的最终 chunk（含 `finish_reason`）中捕获。**它是一次性设置的，不是逐步累积的。** 格式取决于上游 LiteLLM 返回的实际字段（可能是纯 Chat 格式，也可能是 Chat + Anthropic cache 混合）。

```python
# 后：透传原始字段 + Responses API 重命名
raw = state.usage
usage = {
    "input_tokens": raw.get("prompt_tokens") or raw.get("input_tokens", 0),
    "output_tokens": raw.get("completion_tokens") or raw.get("output_tokens", 0),
    "total_tokens": raw.get("total_tokens", 0),
}
# 透传原始 details + Anthropic cache 字段，不做格式适配
input_details = raw.get("prompt_tokens_details") or raw.get("input_tokens_details")
if input_details:
    usage["input_tokens_details"] = input_details
output_details = raw.get("completion_tokens_details") or raw.get("output_tokens_details")
if output_details:
    usage["output_tokens_details"] = output_details
for k in ("cache_read_input_tokens", "cache_creation_input_tokens"):
    if k in raw:
        usage[k] = raw[k]
```

这样 `token_stats.py` 拿到的 `final_usage` 会包含所有原始字段（Anthropic cache 字段被保留在顶层），格式检测逻辑能正常工作。

---

## 错误处理

- `record_token_stats` 整体包裹在 try/except 中
- 异常只 `logging.warning`，不抛出
- usage 为 `None` 或空 dict → 直接 return
- context 缺少 `request_id` → `logging.warning` + return
- DB 写入失败 → warning 日志，不重试
- 所有路径均不阻断 proxy 正常请求处理

---

## 测试计划

1. **单元测试** — `test/test_token_stats.py`
   - Anthropic 格式：input/output + cache_read/cache_write 正确提取
   - OpenAI Chat 格式：prompt/completion + cached_tokens 正确提取
   - OpenAI Responses 格式：input/output + input_tokens_details 正确提取
   - 混合格式（qwen）：Chat 的 prompt_tokens + Anthropic 的 cache 字段共存，各自命中
   - 空 usage → 不写 DB
   - context 缺 request_id → 不写 DB + warning
   - usage 所有值为 0 → 写 DB（0 值也是有效记录）
   - DB 写入异常 → 不抛出

2. **集成测试** — 更新 `test/test_proxy_logger_integration.py`
   - 流式/非流式路径 token_stats 仍正确写入

---

## 性能

- 纯 dict key 查找，`_find_first` 每个提取项最多 3 次 `dict.get()`
- DB 写入是唯一阻塞操作，与当前逻辑一致
- 在整个函数错误处理包裹下，任何异常 < 1ms 返回
- 调用点在 SSE 流结束后（非关键路径），不影响用户响应延迟

---

## 成功标准

1. proxy.py 中不再有 format-specific 的 usage 字段提取逻辑
2. `token_stats.py` 正确处理 3 种格式 + 混合格式（qwen）
3. 异常不影响 proxy 正常请求处理
4. 所有现有测试通过（111 tests）
5. 新增 `test/test_token_stats.py` 覆盖上述测试计划中的场景
