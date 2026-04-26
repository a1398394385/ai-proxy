# Codex SSE 响应格式对齐设计文稿

**日期**: 2026-04-26
**目标**: 修复 fact-store-browser proxy 的 SSE 流式响应格式，确保与 Codex CLI 的 Responses API 消费端完全兼容

---

## 问题陈述

当前 proxy 的 SSE 流式转发中，多个事件的 JSON 数据格式与 Codex 的 `ResponsesStreamEvent` 解析要求不匹配，导致 Codex 报 `stream closed before response.completed` 错误。

根因：Codex 要求每个 SSE 事件的 `data` JSON 中必须包含 `"type"` 字段，且响应级事件需用 `"response"` 键包裹，item 事件需用 `"item"` 键包裹。当前实现缺失这些字段。

---

## Codex 期望的 SSE 事件格式

Codex 使用 `ResponsesStreamEvent` 结构体（`codex-rs/codex-api/src/sse/responses.rs:166-178`）解析事件：

```rust
pub struct ResponsesStreamEvent {
    #[serde(rename = "type")]
    pub(crate) kind: String,      // 必须：事件类型名
    response: Option<Value>,       // 响应级数据：created/completed/failed/incomplete
    item: Option<Value>,           // Item 数据：output_item.added/output_item.done
    delta: Option<String>,         // 增量文本：output_text.delta 等
    summary_index: Option<i64>,    // 推理摘要索引
    content_index: Option<i64>,    // 内容索引
    metadata: Option<Value>,       // 元数据：openai_verification_recommendation
    headers: Option<Value>,        // HTTP 头：model 信息
    // ...
}
```

**核心规则**：
1. 所有事件的 data JSON 必须有 `"type"` 字段
2. 响应级事件（`response.created`, `response.completed`, `response.failed`, `response.incomplete`）的数据包裹在 `"response"` 键中
3. Item 事件（`response.output_item.added`, `response.output_item.done`）的数据包裹在 `"item"` 键中
4. Delta 事件的文本放在 `"delta"` 字段

**Fixture 示例**（`codex-rs/core/tests/cli_responses_fixture.sse`）：

```
event: response.created
data: {"type":"response.created","response":{"id":"resp1"}}

event: response.output_item.done
data: {"type":"response.output_item.done","item":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"fixture hello"}]}}

event: response.completed
data: {"type":"response.completed","response":{"id":"resp1","output":[]}}
```

---

## 当前格式差距分析

### CRITICAL：缺失 `"type"` 字段

| 事件 | 当前格式 | 缺少 |
|------|----------|------|
| `response.output_item.added` (reasoning) | `{"output_index":0,"item":{"type":"reasoning",...}}` | 无顶层 `"type"` |
| `response.output_item.added` (message) | `{"output_index":1,"item":{"type":"message",...}}` | 无顶层 `"type"` |
| `response.reasoning_summary_text.delta` | `{"output_index":0,"summary_index":0,"delta":"..."}` | 无 `"type"` |
| `response.output_text.delta` | `{"output_index":1,"content_index":0,"delta":"..."}` | 无 `"type"` |
| `response.reasoning_summary_text.done` | `{"output_index":0,"summary_index":0,"text":"..."}` | 无 `"type"` |
| `response.output_text.done` | `{"output_index":1,"content_index":0,"text":"..."}` | 无 `"type"` |
| `response.output_item.done` (reasoning) | `{"output_index":0,"item":{...}}` | 无 `"type"` |
| `response.output_item.done` (message) | `{"output_index":1,"item":{...}}` | 无 `"type"` |
| `response.output_item.done` (function_call) | `{"output_index":2,"item":{...}}` | 无 `"type"` |
| `response.incomplete` | `{"reason":"max_tokens"}` | 无 `"type"` + 无 `"response"` 包裹 |

### 已修复（之前提交中已修正）

| 事件 | 状态 | 备注 |
|------|------|------|
| `response.created` | PASS — 有 `"type"` 和 `"response"` 包裹 | `_emit_created` 返回的 edata 内部已有 `"type"` |
| `response.metadata` | PASS — 有 `"type"` | edata 内部已有 `"type":"response.metadata"`。Codex 的 `response_model()` 优先从 `event.response.headers` 读 model，回退到 `event.headers`。metadata 事件无 `response`/`headers` 包裹，但 `response.created` 先于 metadata 发送，其 `response.model` 已设置，所以缺失不影响功能。verification 信息从 `event.metadata.openai_verification_recommendation` 读取，我们的格式正确。 |
| `response.completed` | PASS — 有 `"type"` 和 `"response"` 包裹 | — |

---

## 修复方案

### 统一 SSE 事件格式化函数

新增单一 `_format_sse_event(event_type: str, data: dict) -> str` 辅助函数，**所有事件统一使用此函数**：

```python
def _format_sse_event(event_type: str, data: dict) -> str:
    """生成标准 SSE 事件字符串，确保包含 "type" 字段。

    event_type 会作为 data JSON 的顶层 "type" 字段注入。
    如果 data 中已有 "type" 键，event_type 会覆盖它。
    统一使用 separators=(',', ':') 紧凑格式。
    """
    payload = {"type": event_type, **data}
    return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
```

**设计决策**：不使用两个函数，统一为单一函数。`_emit_created` 当前返回 `(etype, edata)` 元组列表，调用方在 `_process_delta` 中手动拼接。改造方式：

**方案一**：保持 `_emit_created` 返回元组，调用方改用 `_format_sse_event`：

```python
# _process_delta 中：
if not state.created_sent:
    for etype, edata in _emit_created(state):
        events.append(_format_sse_event(etype, edata))
```

`_emit_created` 返回的 edata 内部已有 `"type"` 字段（如 `"type": "response.created"`），但 `_format_sse_event` 的 `"type"` 注入会覆盖它，结果一致。**这是推荐的方案**，因为改动最小且统一了所有事件的格式化逻辑。

### 逐事件修复

#### 1. `response.output_item.added`（所有类型）

修复前：
```python
events.append(
    f'event: response.output_item.added\n'
    f'data: {{"output_index":0,"item":{{"type":"reasoning",'
    f'"id":"{state.reasoning_id}","summary":[],"status":"in_progress"}}}}\n\n'
)
```

修复后：
```python
events.append(_format_sse_event("response.output_item.added", {
    "output_index": 0,
    "item": {"type": "reasoning", "id": state.reasoning_id, "summary": [], "status": "in_progress"},
}))
```

#### 2. `response.reasoning_summary_text.delta`

修复前：
```python
f'data: {{"output_index":0,"summary_index":0,"delta":{json.dumps(reasoning_text, ensure_ascii=False)}}}\n\n'
```

修复后：
```python
events.append(_format_sse_event("response.reasoning_summary_text.delta", {
    "output_index": 0, "summary_index": 0, "delta": reasoning_text,
}))
```

#### 3. `response.output_text.delta`

同上模式。

#### 4. `response.reasoning_summary_text.done`

修复前：
```python
f'data: {{"output_index":0,"summary_index":0,"text":{json.dumps(state.reasoning_buffer, ensure_ascii=False)}}}\n\n'
```

修复后：
```python
events.append(_format_sse_event("response.reasoning_summary_text.done", {
    "output_index": 0, "summary_index": 0, "text": state.reasoning_buffer,
}))
```

#### 5. `response.output_text.done`

同上模式。

#### 6. `response.output_item.done`（所有类型：reasoning、message、function_call）

同上模式，将 hard-coded f-string 替换为 `_format_sse_event` 调用。

#### 7. `response.incomplete`

修复前：
```python
f'data: {{"reason":{json.dumps(INCOMPLETE_REASON_MAP[state.finish_reason])}}}\n\n'
```

修复后：
```python
events.append(_format_sse_event("response.incomplete", {
    "response": {"incomplete_details": {"reason": INCOMPLETE_REASON_MAP[state.finish_reason]}},
}))
```

Codex 从 `event.response.incomplete_details.reason` 读取原因，新格式匹配。

---

## 非流式 `chat_to_responses` 检查

`chat_to_responses` 函数返回的是非流式响应对象，不是 SSE 事件流。Codex 在非流式模式下直接解析 JSON 响应体，不要求 `"type"` 字段。当前格式已正确：

```json
{
  "id": "resp-...",
  "model": "...",
  "status": "completed",
  "output": [...],
  "usage": {...}
}
```

**无需修改**。

---

## 实施计划

### Phase 1: 新增 `_format_sse_event` 辅助函数

- 在 `transform.py` 中添加 `_format_sse_event(event_type, data)` 函数
- 自动注入 `"type"` 字段，统一使用 `separators=(',', ':')`

### Phase 2: 修改所有 SSE 事件生成点

逐一修改以下事件（约 10 处），全部替换为 `_format_sse_event` 调用：

1. `_process_delta` 中 `_emit_created` 的元组拼接（transform.py ~427）
2. `response.output_item.added` — reasoning（transform.py ~437）
3. `response.output_item.added` — message（transform.py ~456）
4. `response.reasoning_summary_text.delta`（transform.py ~445）
5. `response.output_text.delta`（transform.py ~465）
6. `response.reasoning_summary_text.done`（transform.py ~494）
7. `response.output_text.done`（transform.py ~513）
8. `response.output_item.done` — reasoning（transform.py ~504）
9. `response.output_item.done` — message（transform.py ~523）
10. `response.output_item.done` — function_call（transform.py ~544）
11. `response.incomplete`（transform.py ~552）

### Phase 3: 新增事件格式测试

在 `test/test_transform.py` 中：

**单元测试 — `_format_sse_event`**：
- 验证输出以 `event:` 开头，`data:` 后面的 JSON 可被 `json.loads` 解析
- 验证解析后的 JSON 包含 `"type"` 字段且值等于 event_type
- 验证 `separators=(',', ':')` 紧凑格式（无空格）

**集成测试 — `create_codex_sse_stream` 完整流程**：
- 传入一个模拟的 upstream response（包含 reasoning + text + tool call）
- 收集所有 yield 的 SSE 事件字符串
- 逐事件解析 `data:` 后的 JSON，验证每个都有 `"type"` 字段
- 验证 `response.incomplete` 有 `"response"` 包裹

**快照测试 — 完整 SSE 格式对照**：
- 参照 Codex 的 `cli_responses_fixture.sse` 格式
- 写一个完整的流式流程测试，输出与 Codex fixture 格式一致的 SSE 事件
- 验证第一个事件的 `"type"` 是 `"response.created"`，最后一个是 `"response.completed"`

### Phase 4: Codex CLI 端对端验证

**配置备份与切换**：

1. 备份当前 codex 配置文件：`cp ~/.codex/config.toml ~/.codex/config.toml.backup.$(date +%Y%m%d_%H%M%S)`
2. 修改 `~/.codex/config.toml`，将 `model_provider` 切换为 `proxy`，使 codex 连接到我们的代理：
   ```toml
   model_provider = "proxy"
   model = "gpt-4o"
   model_reasoning_effort = "xhigh"

   [model_providers.proxy]
   name = "proxy"
   base_url = "http://127.0.0.1:48743/v1"
   env_key = "OPENAI_API_KEY"
   wire_api = "responses"
   ```
   当前配置中 `proxy` provider 已存在，只需确保 `model_provider = "proxy"` 即可。`wire_api = "responses"` 表明 codex 使用 Responses API 格式通信，这正是我们需要对齐的格式。

**测试流程**：

1. 确保 proxy 运行在 48743 端口（`./server.sh restart`）
2. 启动 codex CLI，连接到 proxy，发送一个简单的文本请求
3. 观察 codex 是否正常接收流式响应并完成交互
4. 验证不再出现 `stream closed before response.completed` 错误
5. 测试完成后恢复原始配置：`cp ~/.codex/config.toml.backup.* ~/.codex/config.toml`

---

## 风险评估

| 风险 | 影响 | 缓解 |
|------|------|------|
| 其他客户端依赖旧格式 | 可能破坏非 Codex 客户端 | 新增 `"type"` 是增量字段，不会破坏已有解析，只是增加了一个之前不存在的键 |
| 遗漏某个事件 | Codex 仍然报错 | Phase 3 的集成测试逐事件扫描，不会遗漏 |
| `response.incomplete` 格式变更 | 可能影响错误处理 | codex 从 `response.incomplete_details.reason` 读取，我们的新格式完全匹配 |

---

## 成功标准

1. Codex 不再报 `stream closed before response.completed`
2. 所有 SSE 事件 data JSON 都有顶层 `"type"` 字段
3. `response.incomplete` 有 `"response"` 包裹
4. 所有现有测试通过
5. `_format_sse_event` 有直接单元测试
6. `create_codex_sse_stream` 有集成测试，逐事件验证 `"type"` 字段
7. 完整 SSE 快照测试，格式与 Codex fixture 一致
