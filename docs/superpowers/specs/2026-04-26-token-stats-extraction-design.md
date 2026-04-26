# Token 统计抽取设计文稿

**日期**: 2026-04-26
**目标**: 将 token 统计逻辑从 proxy.py 抽取为独立的 `token_stats.py` 模块，统一处理 Anthropic / OpenAI Chat / OpenAI Responses 三种 usage 格式。

---

## 问题陈述

当前 token 统计的格式兼容逻辑散布在三处：

1. `proxy.py` 非流式路径 — 从 Chat Completions 响应的 `usage` 提取
2. `proxy.py` 流式路径 — 从 SSE `response.completed` 事件的 `response.usage` 提取
3. `transform.py` `_emit_completion` — 将上游 usage 归一化为 Responses API 格式

每次新增一种格式（如 Anthropic cache 字段）需要改动多个文件。需要将格式检测和提取逻辑集中到一个地方。

---

## 架构

```
proxy.py                              token_stats.py
  ├─ 非流式 ── chat_response["usage"] ──→ record_token_stats(usage, context)
  └─ 流式   ── final_usage            ──→ record_token_stats(usage, context)
                                               │
transform.py                                   │
  └─ _emit_completion: state.usage  ──→ SSE──→─┘
     (简化为直通，不再做 Anthropic 适配)
```

**原则**:
- `transform.py` 负责 SSE 格式转换（Responses API 事件结构），不再做 Anthropic 字段适配
- `token_stats.py` 统一负责：格式检测 → 字段提取 → DB 写入
- proxy.py 只需收集 usage dict 和 context，调用一个函数即可

---

## 接口设计

### 唯一公开函数

```python
def record_token_stats(usage: dict, context: dict) -> None:
    """解析 usage 并写入 token_stats 表。失败静默，不抛异常。

    usage:  上游返回的原始 usage dict，支持 3 种格式自动检测
    context: {
        "request_id": str,
        "agent": str,
        "model": str,
        "target_model": str,
        "request_ts": str,
        "duration_ms": int,
    }
    """
```

### 格式检测策略

每个提取项按优先级尝试 Anthropic → OpenAI Chat → OpenAI Responses，取到为止：

| 提取项 | Anthropic (优先) | OpenAI Chat (回退) | OpenAI Responses (回退) |
|--------|------------------|--------------------|-----------------------|
| input_tokens | — | `prompt_tokens` | `input_tokens` |
| output_tokens | — | `completion_tokens` | `output_tokens` |
| cache_read | `cache_read_input_tokens` | `prompt_tokens_details.cached_tokens` | `input_tokens_details.cached_tokens` |
| cache_write | `cache_creation_input_tokens` | — | `input_tokens_details.cache_creation_input_tokens` |

**关键场景**: qwen 请求格式是 OpenAI Chat（`prompt_tokens`），但 LiteLLM 以 Anthropic 格式返回 cache 字段（`cache_read_input_tokens`）。优先级设计确保格式混合时自动命中正确的字段。

### 检测逻辑

```python
def _extract(usage, key, default=0):
    """从 usage 中按优先级查找 key，未找到返回 default。"""
    # 内联查找，避免多次 dict.get() 调用
```

每个提取项独立检测，不依赖"格式判定"。
- input_tokens: 取 `prompt_tokens` 或 `input_tokens` 中非 0 的那个
- output_tokens: 同理
- cache_read: 取 `cache_read_input_tokens` 或 `details.cached_tokens` 中非 0 的那个
- cache_write: 取 `cache_creation_input_tokens` 或 `details.cache_creation_input_tokens` 中非 0 的那个

这样即使 usage 同时包含多种格式的字段（LiteLLM 偶尔会这样），也能正确提取。

---

## proxy.py 变更

### 非流式路径

```python
# 前: 15 行内联提取 + log_token_stats
# 后:
from token_stats import record_token_stats

usage = chat_response.get("usage", {})
if usage:
    record_token_stats(usage, {
        "request_id": request_id,
        "agent": _extract_agent(self.headers.get("User-Agent", "")),
        "model": model,
        "target_model": target,
        "request_ts": request_ts,
        "duration_ms": duration_ms,
    })
```

### 流式路径

```python
# 前: 10 行从 final_usage 提取字段 + log_token_stats
# 后:
if final_usage:
    record_token_stats(final_usage, {...})
```

---

## transform.py 变更

`_emit_completion` 中的 usage 构建简化为：不再做 cache 字段兼容，直接把 `state.usage` 的各字段映射到 Responses API 格式即可。Anthropic 格式的 cache 字段会在 `token_stats.py` 侧统一处理。

```python
# 前：显式提取 cached_read / cache_write 并适配格式
# 后：只做 Chat → Responses 字段重命名，原始字段透传
"usage": {
    "input_tokens": usage.get("prompt_tokens", 0),
    "output_tokens": usage.get("completion_tokens", 0),
    "total_tokens": usage.get("total_tokens", 0),
    "input_tokens_details": usage.get("prompt_tokens_details", {}),
    "output_tokens_details": usage.get("completion_tokens_details", {}),
    # 保留 Anthropic 格式原始字段以备 token_stats 侧提取
    **{k: v for k, v in usage.items() if k.startswith("cache_")},
},
```

---

## 错误处理

- 整个 `record_token_stats` 包裹在 try/except 中
- 异常只 `logging.warning`，不抛出
- usage 为 `None` 或空 dict 时：直接 return
- DB 写入失败：warning 日志，不重试

---

## 测试计划

1. **单元测试** — `test/test_token_stats.py`
   - 4 种格式各 1 个测试：Anthropic / OpenAI Chat / OpenAI Responses / 混合格式（qwen）
   - cache_read/cache_write 正确提取
   - 空 usage → 不写 DB
   - DB 写入异常 → 不抛出

2. **集成测试** — 更新 `test/test_proxy_logger_integration.py`
   - 确保流式/非流式路径 token_stats 仍正确写入

---

## 性能

- 纯 dict key 查找（`O(k)` where k ≈ 10），无网络 IO
- DB 写入是唯一的阻塞操作，与当前逻辑一致
- 调用点在 SSE 流结束后（非关键路径），不影响用户响应延迟

---

## 成功标准

1. proxy.py 中不再有 format-specific 的 usage 字段提取逻辑
2. `token_stats.py` 正确处理 3 种格式 + 混合格式
3. 异常不影响 proxy 正常请求处理
4. 所有现有测试通过
5. 新增 `test/test_token_stats.py` 覆盖 4 种格式场景
