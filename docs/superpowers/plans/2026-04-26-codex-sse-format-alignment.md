# Codex SSE 格式对齐实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 fact-store-browser proxy 的 SSE 流式响应格式，使所有事件的 data JSON 都包含 `"type"` 字段，与 Codex CLI 的 `ResponsesStreamEvent` 解析完全兼容。

**Architecture:** 新增 `_format_sse_event(event_type, data)` 统一辅助函数，自动注入 `"type"` 字段。替换 `transform.py` 中所有硬编码的 f-string SSE 事件生成。`chat_to_responses`（非流式）无需修改。

**Tech Stack:** Python 3, pytest, `transform.py` 纯转换模块

**设计文稿:** `docs/superpowers/specs/2026-04-26-codex-sse-format-alignment-design.md`

---

### Task 1: 新增 `_format_sse_event` 辅助函数 + 单元测试

**Files:**
- Modify: `transform.py:17`（import 区域之后，`responses_to_chat` 之前）
- Test: `test/test_transform.py`（新增 `TestFormatSSEEvent` 类）

- [ ] **Step 0: 补充 `import json`**

`test/test_transform.py` 当前缺少 `import json`，新增的测试需要用到 `json.loads()`。

在 `test/test_transform.py` 第 1 行之前添加：

```python
import json
import unittest
from transform import generate_response_id
```

- [ ] **Step 1: 写 `_format_sse_event` 单元测试**

在 `test/test_transform.py` 末尾（`if __name__` 之前）新增测试类：

```python
class TestFormatSSEEvent(unittest.TestCase):
    def test_event_line_prefix(self):
        """输出以 event: 开头。"""
        from transform import _format_sse_event
        result = _format_sse_event("response.created", {"id": "resp-1"})
        self.assertTrue(result.startswith("event: response.created\n"))

    def test_data_line_prefix(self):
        """输出包含 data: 行。"""
        from transform import _format_sse_event
        result = _format_sse_event("response.created", {"id": "resp-1"})
        self.assertIn("\ndata: ", result)

    def test_type_field_injected(self):
        """data JSON 包含顶层 "type" 字段，值等于 event_type。"""
        from transform import _format_sse_event
        result = _format_sse_event("response.output_item.added", {"output_index": 0})
        data_part = result.split("data: ", 1)[1].split("\n", 1)[0]
        parsed = json.loads(data_part)
        self.assertEqual(parsed["type"], "response.output_item.added")

    def test_data_fields_preserved(self):
        """data 中的原始字段保留。"""
        from transform import _format_sse_event
        result = _format_sse_event("response.completed", {"response": {"id": "r1"}})
        data_part = result.split("data: ", 1)[1].split("\n", 1)[0]
        parsed = json.loads(data_part)
        self.assertEqual(parsed["response"]["id"], "r1")

    def test_compact_separators(self):
        """使用 separators=(',', ':') 紧凑格式，无多余空格。"""
        from transform import _format_sse_event
        result = _format_sse_event("response.created", {"id": "resp-1"})
        data_part = result.split("data: ", 1)[1].split("\n", 1)[0]
        # 紧凑格式不会有 ": " 或 ", " 在 JSON 内部
        self.assertNotIn(": ", data_part)
        self.assertNotIn(", ", data_part)

    def test_ends_with_double_newline(self):
        """输出以 \n\n 结尾。"""
        from transform import _format_sse_event
        result = _format_sse_event("response.created", {"id": "resp-1"})
        self.assertTrue(result.endswith("\n\n"))

    def test_type_overrides_existing(self):
        """如果 data 中已有 "type"，event_type 覆盖它。"""
        from transform import _format_sse_event
        result = _format_sse_event("response.completed", {"type": "wrong.type", "response": {}})
        data_part = result.split("data: ", 1)[1].split("\n", 1)[0]
        parsed = json.loads(data_part)
        self.assertEqual(parsed["type"], "response.completed")
```

- [ ] **Step 2: 运行测试，验证全部 7 个测试失败**

Run: `cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_transform.py::TestFormatSSEEvent -v`
Expected: 全部 FAIL with "ImportError: cannot import name '_format_sse_event'"

- [ ] **Step 3: 实现 `_format_sse_event` 函数**

在 `transform.py` 的 `import` 块之后（line 17 之后），`responses_to_chat` 之前，添加：

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

- [ ] **Step 4: 运行测试，验证全部通过**

Run: `cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_transform.py::TestFormatSSEEvent -v`
Expected: 7 passed

- [ ] **Step 5: 运行全量测试，确认无回归**

Run: `cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/ -v --tb=short 2>&1 | tail -5`
Expected: 全部通过（现有测试 + 新增 7 个）

- [ ] **Step 6: Commit**

```bash
git add transform.py test/test_transform.py
git commit -m "feat(transform): 新增 _format_sse_event 辅助函数，统一 SSE 事件格式"
```

---

### Task 2: 修改所有 SSE 事件生成点，使用 `_format_sse_event`

**Files:**
- Modify: `transform.py:420-584`（`_process_delta` 和 `_emit_completion`）

此 Task 一次性替换所有 11 处事件生成点。每处改动独立但属于同一逻辑单元。

- [ ] **Step 1: 修改 `_process_delta` — `_emit_created` 元组拼接（line 427）**

当前代码（line 425-427）：
```python
    if not state.created_sent:
        for etype, edata in _emit_created(state):
            events.append(f"event: {etype}\ndata: {json.dumps(edata, ensure_ascii=False, separators=(',', ':'))}\n\n")
```

替换为：
```python
    if not state.created_sent:
        for etype, edata in _emit_created(state):
            events.append(_format_sse_event(etype, edata))
```

- [ ] **Step 2: 修改 `_process_delta` — `response.output_item.added`（reasoning，line 437-441）**

当前代码：
```python
                events.append(
                    f'event: response.output_item.added\n'
                    f'data: {{"output_index":0,"item":{{"type":"reasoning",'
                    f'"id":"{state.reasoning_id}","summary":[],"status":"in_progress"}}}}\n\n'
                )
```

替换为：
```python
                events.append(_format_sse_event("response.output_item.added", {
                    "output_index": 0,
                    "item": {"type": "reasoning", "id": state.reasoning_id, "summary": [], "status": "in_progress"},
                }))
```

- [ ] **Step 3: 修改 `_process_delta` — `response.reasoning_summary_text.delta`（line 445-448）**

当前代码：
```python
            events.append(
                f'event: response.reasoning_summary_text.delta\n'
                f'data: {{"output_index":0,"summary_index":0,"delta":{json.dumps(reasoning_text, ensure_ascii=False)}}}\n\n'
            )
```

替换为：
```python
            events.append(_format_sse_event("response.reasoning_summary_text.delta", {
                "output_index": 0, "summary_index": 0, "delta": reasoning_text,
            }))
```

- [ ] **Step 4: 修改 `_process_delta` — `response.output_item.added`（message，line 456-460）**

当前代码：
```python
            events.append(
                f'event: response.output_item.added\n'
                f'data: {{"output_index":{idx},"item":{{"type":"message","role":"assistant",'
                f'"content":[],"status":"in_progress"}}}}\n\n'
            )
```

替换为：
```python
            events.append(_format_sse_event("response.output_item.added", {
                "output_index": idx,
                "item": {"type": "message", "role": "assistant", "content": [], "status": "in_progress"},
            }))
```

- [ ] **Step 5: 修改 `_process_delta` — `response.output_text.delta`（line 465-468）**

当前代码：
```python
        events.append(
            f'event: response.output_text.delta\n'
            f'data: {{"output_index":{idx},"content_index":0,"delta":{json.dumps(content, ensure_ascii=False)}}}\n\n'
        )
```

替换为：
```python
        events.append(_format_sse_event("response.output_text.delta", {
            "output_index": idx, "content_index": 0, "delta": content,
        }))
```

- [ ] **Step 6: 修改 `_emit_completion` — `response.reasoning_summary_text.done`（line 494-497）**

当前代码：
```python
        events.append(
            f'event: response.reasoning_summary_text.done\n'
            f'data: {{"output_index":0,"summary_index":0,"text":{json.dumps(state.reasoning_buffer, ensure_ascii=False)}}}\n\n'
        )
```

替换为：
```python
        events.append(_format_sse_event("response.reasoning_summary_text.done", {
            "output_index": 0, "summary_index": 0, "text": state.reasoning_buffer,
        }))
```

- [ ] **Step 7: 修改 `_emit_completion` — `response.output_item.done`（reasoning，line 504-507）**

当前代码：
```python
        events.append(
            f'event: response.output_item.done\n'
            f'data: {{"output_index":0,"item":{json.dumps(reasoning_item, ensure_ascii=False, separators=(",", ":"))}}}\n\n'
        )
```

替换为：
```python
        events.append(_format_sse_event("response.output_item.done", {
            "output_index": 0, "item": reasoning_item,
        }))
```

- [ ] **Step 8: 修改 `_emit_completion` — `response.output_text.done`（line 513-516）**

当前代码：
```python
        events.append(
            f'event: response.output_text.done\n'
            f'data: {{"output_index":{idx},"content_index":0,"text":{json.dumps(state.text_buffer, ensure_ascii=False)}}}\n\n'
        )
```

替换为：
```python
        events.append(_format_sse_event("response.output_text.done", {
            "output_index": idx, "content_index": 0, "text": state.text_buffer,
        }))
```

- [ ] **Step 9: 修改 `_emit_completion` — `response.output_item.done`（message，line 523-526）**

当前代码：
```python
        events.append(
            f'event: response.output_item.done\n'
            f'data: {{"output_index":{idx},"item":{json.dumps(message_item, ensure_ascii=False, separators=(",", ":"))}}}\n\n'
        )
```

替换为：
```python
        events.append(_format_sse_event("response.output_item.done", {
            "output_index": idx, "item": message_item,
        }))
```

- [ ] **Step 10: 修改 `_emit_completion` — `response.output_item.done`（function_call，line 544-547）**

当前代码：
```python
        events.append(
            f'event: response.output_item.done\n'
            f'data: {{"output_index":{output_idx},"item":{json.dumps(tc_item, ensure_ascii=False, separators=(",", ":"))}}}\n\n'
        )
```

替换为：
```python
        events.append(_format_sse_event("response.output_item.done", {
            "output_index": output_idx, "item": tc_item,
        }))
```

- [ ] **Step 11: 修改 `_emit_completion` — `response.incomplete`（line 552-555）**

当前代码：
```python
        events.append(
            f'event: response.incomplete\n'
            f'data: {{"reason":{json.dumps(INCOMPLETE_REASON_MAP[state.finish_reason])}}}\n\n'
        )
```

替换为：
```python
        events.append(_format_sse_event("response.incomplete", {
            "response": {"incomplete_details": {"reason": INCOMPLETE_REASON_MAP[state.finish_reason]}},
        }))
```

- [ ] **Step 12: 运行全量测试，验证通过**

Run: `cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_transform.py -v --tb=short 2>&1 | tail -10`
Expected: 全部通过（原有测试 + Task 1 的 7 个新测试）

- [ ] **Step 13: Commit**

```bash
git add transform.py
git commit -m "feat(transform): 所有 SSE 事件改用 _format_sse_event，注入 type 字段"
```

---

### Task 3: 新增事件格式集成测试 + 快照测试

**Files:**
- Test: `test/test_transform.py`（新增 `TestSSEEventTypeFormat` 和 `TestSSESnapshot` 类）

- [ ] **Step 1: 新增集成测试 — 逐事件验证 `"type"` 字段**

在 `test/test_transform.py` 新增：

```python
class TestSSEEventTypeFormat(unittest.TestCase):
    """集成测试：逐事件解析 data JSON，验证每个都有 "type" 字段。"""

    def _collect_events(self, mock_stream):
        """从 mock stream 收集所有 SSE 事件，返回 [(event_type, data_dict), ...]。"""
        from transform import create_codex_sse_stream
        events_text = ""
        for event in create_codex_sse_stream(mock_stream):
            events_text += event
        parsed = []
        for block in events_text.strip().split("\n\n"):
            if not block:
                continue
            lines = block.split("\n")
            etype = None
            data_str = None
            for line in lines:
                if line.startswith("event: "):
                    etype = line[len("event: "):]
                elif line.startswith("data: "):
                    data_str = line[len("data: "):]
            if etype and data_str:
                parsed.append((etype, json.loads(data_str)))
        return parsed

    def test_all_events_have_type_field(self):
        """所有 SSE 事件的 data JSON 都有顶层 "type" 字段。"""
        class MockStream:
            def __init__(self):
                chunks = [
                    b'event: message\ndata: {"id":"chatcmpl-1","model":"test","choices":[{"delta":{"reasoning_content":"thinking..."},"index":0}]}\n\n',
                    b'event: message\ndata: {"id":"chatcmpl-1","choices":[{"delta":{"content":"answer"},"index":0}]}\n\n',
                    b'event: message\ndata: {"id":"chatcmpl-1","choices":[{"delta":{},"index":0,"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}}\n\n',
                    b'data: [DONE]\n\n',
                ]
                self.data = b"".join(chunks)
                self.pos = 0

            def read(self, size):
                chunk = self.data[self.pos:self.pos + size]
                self.pos += size
                return chunk

        events = self._collect_events(MockStream())
        self.assertGreater(len(events), 0, "应该有至少一个事件")
        for etype, data in events:
            self.assertIn("type", data, f"事件 {etype} 缺少 type 字段")
            self.assertEqual(data["type"], etype, f"事件 {etype} 的 type 值不匹配")

    def test_incomplete_has_response_wrapper(self):
        """response.incomplete 有 "response" 包裹。"""
        class MockStream:
            def __init__(self):
                chunks = [
                    b'event: message\ndata: {"id":"chatcmpl-1","model":"test","choices":[{"delta":{"content":"partial"},"index":0}]}\n\n',
                    b'event: message\ndata: {"id":"chatcmpl-1","choices":[{"delta":{},"index":0,"finish_reason":"length"}],"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}}\n\n',
                    b'data: [DONE]\n\n',
                ]
                self.data = b"".join(chunks)
                self.pos = 0

            def read(self, size):
                chunk = self.data[self.pos:self.pos + size]
                self.pos += size
                return chunk

        events = self._collect_events(MockStream())
        incomplete = [(e, d) for e, d in events if e == "response.incomplete"]
        self.assertEqual(len(incomplete), 1, "应该有一个 response.incomplete 事件")
        etype, data = incomplete[0]
        self.assertIn("response", data, "incomplete 事件应该有 response 包裹")
        self.assertIn("incomplete_details", data["response"])
        self.assertEqual(data["response"]["incomplete_details"]["reason"], "max_tokens")

    def test_created_has_response_wrapper(self):
        """response.created 有 "response" 包裹。"""
        class MockStream:
            def __init__(self):
                chunks = [
                    b'event: message\ndata: {"id":"chatcmpl-1","model":"test","choices":[{"delta":{"content":"hi"},"index":0}]}\n\n',
                    b'event: message\ndata: {"id":"chatcmpl-1","choices":[{"delta":{},"index":0,"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}\n\n',
                    b'data: [DONE]\n\n',
                ]
                self.data = b"".join(chunks)
                self.pos = 0

            def read(self, size):
                chunk = self.data[self.pos:self.pos + size]
                self.pos += size
                return chunk

        events = self._collect_events(MockStream())
        created = [(e, d) for e, d in events if e == "response.created"]
        self.assertEqual(len(created), 1)
        etype, data = created[0]
        self.assertIn("response", data)
        self.assertIn("output", data["response"])
```

- [ ] **Step 2: 新增快照测试 — 完整 SSE 格式对照 Codex fixture**

```python
class TestSSESnapshot(unittest.TestCase):
    """快照测试：完整 SSE 输出格式与 Codex fixture 一致。"""

    def test_snapshot_text_only_stream(self):
        """纯文本流快照：验证事件顺序和关键字段。"""
        from transform import create_codex_sse_stream

        class MockStream:
            def __init__(self):
                chunks = [
                    b'event: message\ndata: {"id":"chatcmpl-1","model":"test-model","choices":[{"delta":{"content":"Hello"},"index":0}]}\n\n',
                    b'event: message\ndata: {"id":"chatcmpl-1","choices":[{"delta":{"content":" World"},"index":0}]}\n\n',
                    b'event: message\ndata: {"id":"chatcmpl-1","choices":[{"delta":{},"index":0,"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}}\n\n',
                    b'data: [DONE]\n\n',
                ]
                self.data = b"".join(chunks)
                self.pos = 0

            def read(self, size):
                chunk = self.data[self.pos:self.pos + size]
                self.pos += size
                return chunk

        events_text = ""
        for event in create_codex_sse_stream(MockStream()):
            events_text += event

        # 验证事件顺序
        event_types = []
        for block in events_text.strip().split("\n\n"):
            for line in block.split("\n"):
                if line.startswith("event: "):
                    event_types.append(line[len("event: "):])
                    break

        self.assertEqual(event_types[0], "response.created")
        self.assertEqual(event_types[1], "response.metadata")
        self.assertEqual(event_types[-1], "response.completed")
        # created 在前，completed 在后
        self.assertIn("response.output_item.added", event_types)
        self.assertIn("response.output_text.delta", event_types)
        self.assertIn("response.output_text.done", event_types)
        self.assertIn("response.output_item.done", event_types)

    def test_snapshot_reasoning_plus_text_stream(self):
        """推理+文本流快照：验证 reasoning output_index=0, message output_index=1。"""
        from transform import create_codex_sse_stream

        class MockStream:
            def __init__(self):
                chunks = [
                    b'event: message\ndata: {"id":"chatcmpl-1","model":"test","choices":[{"delta":{"reasoning_content":"Let me think..."},"index":0}]}\n\n',
                    b'event: message\ndata: {"id":"chatcmpl-1","choices":[{"delta":{"content":"Answer"},"index":0}]}\n\n',
                    b'event: message\ndata: {"id":"chatcmpl-1","choices":[{"delta":{},"index":0,"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}\n\n',
                    b'data: [DONE]\n\n',
                ]
                self.data = b"".join(chunks)
                self.pos = 0

            def read(self, size):
                chunk = self.data[self.pos:self.pos + size]
                self.pos += size
                return chunk

        events_text = ""
        for event in create_codex_sse_stream(MockStream()):
            events_text += event

        # 验证 reasoning 在 message 之前
        reasoning_added_pos = events_text.index('"type":"reasoning"')
        message_added_pos = events_text.index('"type":"message"')
        self.assertLess(reasoning_added_pos, message_added_pos)

        # 验证 reasoning output_index=0
        self.assertIn('"output_index":0', events_text)
        # 验证 message output_index=1
        self.assertIn('"output_index":1', events_text)
```

- [ ] **Step 3: 运行所有新增测试**

Run: `cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_transform.py::TestSSEEventTypeFormat test/test_transform.py::TestSSESnapshot -v --tb=short`
Expected: 全部通过（5 个新增测试）

- [ ] **Step 4: 运行全量测试**

Run: `cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/ -v --tb=short 2>&1 | tail -5`
Expected: 全部通过

- [ ] **Step 5: Commit**

```bash
git add test/test_transform.py
git commit -m "test(transform): 新增 SSE 事件格式集成测试和快照测试"
```

---

### Task 4: Codex CLI 端对端验证

**Files:**
- 无代码修改，仅操作配置和执行验证

此 Task 在实现完成后执行。备份配置 → 切换 proxy → 测试 → 恢复。

- [ ] **Step 1: 备份当前 codex 配置**

```bash
cp ~/.codex/config.toml ~/.codex/config.toml.backup.$(date +%Y%m%d_%H%M%S)
```

- [ ] **Step 2: 切换 codex 配置到 proxy**

将 `~/.codex/config.toml` 的 `model_provider` 改为 `"proxy"`：

```bash
sed -i '' 's/model_provider = "custom"/model_provider = "proxy"/' ~/.codex/config.toml
```

确认配置文件中已有 `[model_providers.proxy]` 块（当前已有）：
```toml
[model_providers.proxy]
name = "proxy"
base_url = "http://127.0.0.1:48743/v1"
env_key = "OPENAI_API_KEY"
wire_api = "responses"
```

- [ ] **Step 3: 启动 proxy**

```bash
cd /Users/xys/.hermes/fact-store-browser && ./server.sh restart
```

确认 proxy 在 48743 端口运行。

- [ ] **Step 4: 启动 codex CLI 发送简单请求**

启动 codex CLI，发送 `echo hello` 或类似的简单请求。观察是否正常接收流式响应并完成交互。

- [ ] **Step 5: 验证不再出现 `stream closed before response.completed`**

确认 codex 正常完成交互，无错误。

- [ ] **Step 6: 恢复原始配置**

```bash
cp ~/.codex/config.toml.backup.* ~/.codex/config.toml
```

验证恢复后的配置中 `model_provider = "custom"`。

- [ ] **Step 7: Commit（如需）**

此 Task 无代码变更，不产生 commit。

---

## 计划自审

### 1. 设计文稿覆盖率检查

| 设计要求 | 对应 Task | 状态 |
|----------|-----------|------|
| 新增 `_format_sse_event` 函数 | Task 1 | 覆盖 |
| 修改 11 处 SSE 事件生成点 | Task 2 (Steps 1-11) | 覆盖 |
| `response.incomplete` 有 `"response"` 包裹 | Task 2 Step 11 | 覆盖 |
| 统一 `separators=(',', ':')` | Task 1 实现 + Step 5 测试 | 覆盖 |
| `_format_sse_event` 单元测试 | Task 1 Step 1 | 7 个测试 |
| 集成测试逐事件验证 `"type"` | Task 3 Step 1 | 3 个测试 |
| 完整 SSE 快照测试 | Task 3 Step 2 | 2 个测试 |
| Codex CLI 端对端验证 | Task 4 | 覆盖 |
| `chat_to_responses` 无需修改 | 计划中已说明 | N/A |

### 2. Placeholder 扫描

无 "TBD"、"TODO"、"implement later" 等占位符。所有步骤包含完整代码。

### 3. 类型一致性检查

- `_format_sse_event(event_type: str, data: dict) -> str` — 签名在 Task 1 定义，Task 2 所有调用方一致使用
- `StreamState` dataclass 未被修改，所有字段引用与现有代码一致
- `INCOMPLETE_REASON_MAP` 和 `FINISH_REASON_MAP` 常量直接使用，已在 transform.py 中定义

### 4. 风险评估

- **原有测试兼容性**：Task 2 修改事件格式后，现有 `TestSSEStreamIntegration` 测试仍使用 `assertIn("event: ...", events_text)` 匹配事件类型名，不依赖 data JSON 内容，应不受影响。
- **`response.incomplete` 格式变更**：从 `{"reason":"max_tokens"}` 改为 `{"response":{"incomplete_details":{"reason":"max_tokens"}}}`，Codex 从 `response.incomplete_details.reason` 读取，新格式完全匹配。
