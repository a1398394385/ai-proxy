# SSE 状态机全面重构 Phase 1 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 `CodexStreamConverter` 类替换现有的 `StreamState` + 独立顶层函数，补齐 Codex CLI 期望的完整 SSE 事件序列（content_part 生命周期、function_call_arguments delta、response.in_progress、refusal、[DONE]），同时修复 `chat_to_responses` 的 text+refusal 合并问题和 proxy.py 的错误路径缺失 [DONE] 问题。

**Architecture:** 将 `transform_responses.py` 中的流转换逻辑从过程式函数（`_emit_created`/`_process_delta`/`_emit_completion`）改为封装到 `CodexStreamConverter` 数据类的方法中，`create_codex_sse_stream()` 保持对外接口不变仅内部改用新类，`transform.py` 的 re-export 列表同步移除已废弃的三个顶层函数。

**Tech Stack:** Python 3.10+, `dataclasses`, `uuid`, `time`, `pytest`（测试运行命令：`cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/ -v`）

---

## 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| Modify | `transform_responses.py` | 新增 `ToolBlockState`、`CodexStreamConverter`；移除旧 `StreamState` 及三个顶层函数 |
| Modify | `transform.py` | 更新 re-export 列表：移除 `_emit_created`/`_process_delta`/`_emit_completion`；添加 `CodexStreamConverter`/`ToolBlockState` |
| Modify | `proxy.py` | 三处错误路径各补一行 `data: [DONE]`；`iter_sse_events` 缓冲区 256→4096 |
| Modify | `test/test_transform.py` | 更新 5 个现有测试；新增 13 个测试用例 |

---

### Task 1：新增 `ToolBlockState` dataclass

**Files:**
- Modify: `transform_responses.py`（在 `StreamState` 定义之前插入）
- Test: `test/test_transform.py`

- [ ] **Step 1: 写失败测试**

```python
# 加到 test/test_transform.py 末尾 TestStreamState class 内
class TestToolBlockState(unittest.TestCase):
    def test_default_fields(self):
        from transform import ToolBlockState
        b = ToolBlockState()
        self.assertEqual(b.output_index, -1)
        self.assertEqual(b.call_id, "")
        self.assertEqual(b.name, "")
        self.assertEqual(b.accumulated_args, "")
        self.assertFalse(b.started)
        self.assertEqual(b.item_id, "")

    def test_mutation(self):
        from transform import ToolBlockState
        b = ToolBlockState()
        b.call_id = "call_abc"
        b.name = "bash"
        b.accumulated_args = '{"cmd":"ls"}'
        b.started = True
        b.item_id = "fc_00000001"
        b.output_index = 2
        self.assertEqual(b.call_id, "call_abc")
        self.assertEqual(b.output_index, 2)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_transform.py::TestToolBlockState -v
```
期望：`ImportError: cannot import name 'ToolBlockState'`

- [ ] **Step 3: 在 `transform_responses.py` 中插入 `ToolBlockState`**

在 `StreamState` 定义（第 361 行）之前插入：

```python
@dataclass
class ToolBlockState:
    """工具调用块的中间状态，每个 tool_calls index 对应一个实例。"""
    output_index: int = -1
    call_id: str = ""
    name: str = ""
    accumulated_args: str = ""
    started: bool = False
    item_id: str = ""          # added/done 必须复用同一 ID
```

- [ ] **Step 4: 在 `transform.py` 的 re-export 列表中加入 `ToolBlockState`**

找到 `transform.py` 中从 `transform_responses` 导入的块，添加 `ToolBlockState,`。

- [ ] **Step 5: 运行测试确认通过**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_transform.py::TestToolBlockState -v
```
期望：`2 passed`

- [ ] **Step 6: Commit**

```bash
cd /Users/xys/.hermes/fact-store-browser && git add transform_responses.py transform.py test/test_transform.py && git commit -m "feat: 新增 ToolBlockState dataclass（工具调用延迟启动状态）"
```

---

### Task 2：新增 `CodexStreamConverter` dataclass 字段

**Files:**
- Modify: `transform_responses.py`（在 `ToolBlockState` 之后、现有 `StreamState` 之前）
- Test: `test/test_transform.py`

- [ ] **Step 1: 写失败测试**

```python
class TestCodexStreamConverterFields(unittest.TestCase):
    def test_default_fields(self):
        from transform import CodexStreamConverter
        c = CodexStreamConverter()
        self.assertEqual(c.response_id, "")
        self.assertEqual(c.model, "")
        self.assertEqual(c.next_output_index, 0)
        self.assertEqual(c.text_output_index, -1)
        self.assertFalse(c.text_message_opened)
        self.assertFalse(c.text_content_part_opened)
        self.assertEqual(c.accumulated_text, "")
        self.assertEqual(c.reasoning_output_index, -1)
        self.assertFalse(c.reasoning_opened)
        self.assertFalse(c.refusal_opened)
        self.assertEqual(c.refusal_content_index, 0)
        self.assertEqual(c.tool_blocks, {})
        self.assertIsNone(c.final_usage)
        self.assertEqual(c.output_items, [])
        self.assertFalse(c.created_sent)

    def test_stream_state_alias(self):
        """向后兼容：StreamState 必须是 CodexStreamConverter 的别名。"""
        from transform import StreamState, CodexStreamConverter
        self.assertIs(StreamState, CodexStreamConverter)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_transform.py::TestCodexStreamConverterFields -v
```
期望：`ImportError: cannot import name 'CodexStreamConverter'`

- [ ] **Step 3: 在 `transform_responses.py` 中添加 `CodexStreamConverter` 类定义**

在 `ToolBlockState` 之后插入（完整 dataclass 字段，暂不含方法）：

```python
@dataclass
class CodexStreamConverter:
    """完整的 Codex SSE 流转换器，替代旧 StreamState + 三个顶层函数。"""

    response_id: str = ""
    model: str = ""
    next_output_index: int = 0

    # 文本消息状态
    text_message_id: str = ""
    text_output_index: int = -1
    text_message_opened: bool = False
    text_content_part_opened: bool = False
    accumulated_text: str = ""

    # 推理状态
    reasoning_id: str = ""
    reasoning_output_index: int = -1
    reasoning_opened: bool = False
    accumulated_reasoning: str = ""

    # 拒绝状态
    refusal_opened: bool = False
    refusal_content_index: int = 0   # 在 _handle_refusal_delta 首次打开时保存，避免时序竞态
    accumulated_refusal: str = ""

    # 工具调用状态（key: tool_calls index → ToolBlockState）
    tool_blocks: dict = field(default_factory=dict)

    # 完成状态
    finish_reason: str = ""
    final_usage: Optional[dict] = None   # None = 未收到 usage chunk

    # output_items 存 (output_index, item) 元组，finish() 中按 output_index 排序
    output_items: list = field(default_factory=list)
    created_sent: bool = False


# 向后兼容别名
StreamState = CodexStreamConverter
```

- [ ] **Step 4: 更新 `transform.py` re-export**

在 `transform_responses` 导入块添加 `CodexStreamConverter,`，并将 `StreamState` 改为从此别名导入（或保留直接导入 `StreamState`，两种均可）。

- [ ] **Step 5: 运行测试确认通过**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_transform.py::TestCodexStreamConverterFields -v
```
期望：`2 passed`

- [ ] **Step 6: Commit**

```bash
cd /Users/xys/.hermes/fact-store-browser && git add transform_responses.py transform.py test/test_transform.py && git commit -m "feat: 新增 CodexStreamConverter dataclass 字段定义，StreamState 设为别名"
```

---

### Task 3：实现 `_build_response_obj`、`_format_sse`、`_emit_created`

**Files:**
- Modify: `transform_responses.py`（在 `CodexStreamConverter` 类体内添加方法）
- Test: `test/test_transform.py`

- [ ] **Step 1: 写失败测试**

```python
class TestCodexStreamConverterCreated(unittest.TestCase):
    def _make_converter(self):
        from transform import CodexStreamConverter
        c = CodexStreamConverter()
        c.response_id = "resp-test-00000001"
        c.model = "test-model"
        return c

    def test_build_response_obj_required_fields(self):
        c = self._make_converter()
        obj = c._build_response_obj("in_progress")
        for key in ("id", "object", "created_at", "status", "model", "output", "usage"):
            self.assertIn(key, obj, f"缺少必需字段: {key}")
        self.assertEqual(obj["id"], "resp-test-00000001")
        self.assertEqual(obj["object"], "response")
        self.assertEqual(obj["status"], "in_progress")
        self.assertEqual(obj["model"], "test-model")
        self.assertEqual(obj["output"], [])

    def test_build_response_obj_usage_none_fallback(self):
        c = self._make_converter()
        obj = c._build_response_obj("completed", usage=None)
        self.assertEqual(obj["usage"], {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})

    def test_build_response_obj_incomplete_details(self):
        c = self._make_converter()
        obj = c._build_response_obj("incomplete", incomplete_details={"reason": "max_tokens"})
        self.assertIn("incomplete_details", obj)
        self.assertEqual(obj["incomplete_details"]["reason"], "max_tokens")

    def test_build_response_obj_no_incomplete_details_when_none(self):
        c = self._make_converter()
        obj = c._build_response_obj("completed")
        self.assertNotIn("incomplete_details", obj)

    def test_emit_created_returns_three_events(self):
        c = self._make_converter()
        events = c._emit_created()
        self.assertEqual(len(events), 3)

    def test_emit_created_sets_created_sent(self):
        c = self._make_converter()
        self.assertFalse(c.created_sent)
        c._emit_created()
        self.assertTrue(c.created_sent)

    def test_emit_created_event_types(self):
        import json
        c = self._make_converter()
        events = c._emit_created()
        types = []
        for e in events:
            for line in e.split("\n"):
                if line.startswith("data: "):
                    types.append(json.loads(line[6:])["type"])
        self.assertIn("response.created", types)
        self.assertIn("response.in_progress", types)
        self.assertIn("response.metadata", types)

    def test_emit_created_response_has_model(self):
        import json
        c = self._make_converter()
        events = c._emit_created()
        # First event (response.created) response object must have correct model
        for line in events[0].split("\n"):
            if line.startswith("data: "):
                data = json.loads(line[6:])
                self.assertEqual(data["response"]["model"], "test-model")
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_transform.py::TestCodexStreamConverterCreated -v
```
期望：`AttributeError: 'CodexStreamConverter' object has no attribute '_build_response_obj'`

- [ ] **Step 3: 在 `CodexStreamConverter` 类体末尾添加三个方法**

```python
    def _format_sse(self, event_type: str, data: dict) -> str:
        return _format_sse_event(event_type, data)

    def _build_response_obj(
        self,
        status: str,
        usage: dict = None,
        output: list = None,
        incomplete_details: dict = None,
    ) -> dict:
        obj = {
            "id": self.response_id,
            "object": "response",
            "created_at": int(time.time()),
            "status": status,
            "model": self.model,
            "output": output if output is not None else [],
            "usage": usage or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        }
        if incomplete_details is not None:
            obj["incomplete_details"] = incomplete_details
        return obj

    def _emit_created(self) -> list:
        resp = self._build_response_obj("in_progress")
        events = [
            self._format_sse("response.created",     {"response": resp}),
            self._format_sse("response.in_progress", {"response": resp}),
            self._format_sse("response.metadata",    {"headers": {"model": self.model}}),
        ]
        self.created_sent = True
        return events
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_transform.py::TestCodexStreamConverterCreated -v
```
期望：`8 passed`

- [ ] **Step 5: Commit**

```bash
cd /Users/xys/.hermes/fact-store-browser && git add transform_responses.py test/test_transform.py && git commit -m "feat: 实现 _build_response_obj / _format_sse / _emit_created 方法"
```

---

### Task 4：实现 `_handle_text_delta` + `_close_text_block`

**Files:**
- Modify: `transform_responses.py`
- Test: `test/test_transform.py`

- [ ] **Step 1: 写失败测试**

```python
class _SSETestBase(unittest.TestCase):
    """公共基类：为所有 SSE 事件测试提供 _parse_events 辅助方法，消除重复代码。"""
    def _parse_events(self, sse_strings):
        import json
        events = []
        for s in sse_strings:
            for block in s.strip().split("\n\n"):
                if not block.strip():
                    continue
                etype, data = None, None
                for line in block.split("\n"):
                    if line.startswith("event: "):
                        etype = line[7:]
                    elif line.startswith("data: "):
                        data = json.loads(line[6:])
                if etype and data:
                    events.append((etype, data))
        return events


class TestHandleTextDelta(_SSETestBase):
    def _make_converter(self):
        from transform import CodexStreamConverter
        c = CodexStreamConverter()
        c.response_id = "resp-test"
        c.model = "test"
        return c

    def test_first_delta_emits_output_item_added_and_content_part_added(self):
        c = self._make_converter()
        result = c._handle_text_delta("Hello")
        events = self._parse_events(result)
        types = [e[0] for e in events]
        self.assertIn("response.output_item.added", types)
        self.assertIn("response.content_part.added", types)
        self.assertIn("response.output_text.delta", types)

    def test_output_item_added_has_correct_item_structure(self):
        c = self._make_converter()
        result = c._handle_text_delta("Hello")
        events = self._parse_events(result)
        added = next(e for e in events if e[0] == "response.output_item.added")
        item = added[1]["item"]
        self.assertEqual(item["type"], "message")
        self.assertEqual(item["status"], "in_progress")
        self.assertEqual(item["role"], "assistant")
        self.assertEqual(item["content"], [])
        self.assertTrue(item["id"].startswith("msg_"))

    def test_content_part_added_has_correct_part_structure(self):
        c = self._make_converter()
        result = c._handle_text_delta("Hello")
        events = self._parse_events(result)
        part_added = next(e for e in events if e[0] == "response.content_part.added")
        part = part_added[1]["part"]
        self.assertEqual(part["type"], "output_text")
        self.assertEqual(part["text"], "")
        self.assertEqual(part["annotations"], [])
        self.assertEqual(part_added[1]["content_index"], 0)

    def test_second_delta_no_added_events(self):
        c = self._make_converter()
        c._handle_text_delta("Hello")   # first
        result = c._handle_text_delta(" World")   # second
        events = self._parse_events(result)
        types = [e[0] for e in events]
        self.assertNotIn("response.output_item.added", types)
        self.assertNotIn("response.content_part.added", types)
        self.assertIn("response.output_text.delta", types)

    def test_accumulated_text(self):
        c = self._make_converter()
        c._handle_text_delta("Hello")
        c._handle_text_delta(" World")
        self.assertEqual(c.accumulated_text, "Hello World")

    def test_output_index_increments(self):
        c = self._make_converter()
        c._handle_text_delta("Hello")
        self.assertEqual(c.next_output_index, 1)
        self.assertEqual(c.text_output_index, 0)

    def test_close_text_block_emits_done_events(self):
        c = self._make_converter()
        c._handle_text_delta("Hello World")
        result = c._close_text_block()
        events = self._parse_events(result)
        types = [e[0] for e in events]
        self.assertIn("response.output_text.done", types)
        self.assertIn("response.content_part.done", types)

    def test_close_text_block_done_has_full_text(self):
        c = self._make_converter()
        c._handle_text_delta("Hello ")
        c._handle_text_delta("World")
        result = c._close_text_block()
        events = self._parse_events(result)
        text_done = next(e for e in events if e[0] == "response.output_text.done")
        self.assertEqual(text_done[1]["text"], "Hello World")

    def test_close_text_block_sets_opened_false(self):
        c = self._make_converter()
        c._handle_text_delta("Hi")
        self.assertTrue(c.text_content_part_opened)
        c._close_text_block()
        self.assertFalse(c.text_content_part_opened)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_transform.py::TestHandleTextDelta -v
```
期望：`AttributeError: 'CodexStreamConverter' object has no attribute '_handle_text_delta'`

- [ ] **Step 3: 在 `CodexStreamConverter` 类体中添加两个方法**

```python
    def _handle_text_delta(self, text: str) -> list:
        events = []
        if not self.text_message_opened:
            self.text_output_index = self.next_output_index
            self.next_output_index += 1
            self.text_message_id = f"msg_{uuid.uuid4().hex[:8]}"
            self.text_message_opened = True
            events.append(self._format_sse("response.output_item.added", {
                "output_index": self.text_output_index,
                "item": {
                    "type": "message",
                    "id": self.text_message_id,
                    "status": "in_progress",
                    "role": "assistant",
                    "content": [],
                },
            }))
        if not self.text_content_part_opened:
            self.text_content_part_opened = True
            events.append(self._format_sse("response.content_part.added", {
                "output_index": self.text_output_index,
                "content_index": 0,
                "part": {"type": "output_text", "text": "", "annotations": []},
            }))
        self.accumulated_text += text
        events.append(self._format_sse("response.output_text.delta", {
            "output_index": self.text_output_index,
            "content_index": 0,
            "delta": text,
        }))
        return events

    def _close_text_block(self) -> list:
        if not self.text_content_part_opened:
            return []
        events = [
            self._format_sse("response.output_text.done", {
                "output_index": self.text_output_index,
                "content_index": 0,
                "text": self.accumulated_text,
            }),
            self._format_sse("response.content_part.done", {
                "output_index": self.text_output_index,
                "content_index": 0,
                "part": {"type": "output_text", "text": self.accumulated_text, "annotations": []},
            }),
        ]
        self.text_content_part_opened = False
        return events
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_transform.py::TestHandleTextDelta -v
```
期望：`9 passed`

- [ ] **Step 5: Commit**

```bash
cd /Users/xys/.hermes/fact-store-browser && git add transform_responses.py test/test_transform.py && git commit -m "feat: 实现 _handle_text_delta 和 _close_text_block（content_part 生命周期）"
```

---

### Task 5：实现 `_handle_reasoning_delta` + `_close_reasoning_block`

**Files:**
- Modify: `transform_responses.py`
- Test: `test/test_transform.py`

- [ ] **Step 1: 写失败测试**

```python
class TestHandleReasoningDelta(_SSETestBase):
    def _make_converter(self):
        from transform import CodexStreamConverter
        c = CodexStreamConverter()
        c.response_id = "resp-test"
        c.model = "test"
        return c

    def test_first_delta_emits_output_item_added(self):
        c = self._make_converter()
        result = c._handle_reasoning_delta("Let me think")
        events = self._parse_events(result)
        types = [e[0] for e in events]
        self.assertIn("response.output_item.added", types)
        self.assertIn("response.reasoning.delta", types)

    def test_output_item_added_reasoning_structure(self):
        c = self._make_converter()
        result = c._handle_reasoning_delta("Think...")
        events = self._parse_events(result)
        added = next(e for e in events if e[0] == "response.output_item.added")
        self.assertEqual(added[1]["item"]["type"], "reasoning")
        self.assertEqual(added[1]["item"]["summary"], [])
        self.assertTrue(added[1]["item"]["id"].startswith("rs_"))

    def test_second_delta_no_added(self):
        c = self._make_converter()
        c._handle_reasoning_delta("Think...")
        result = c._handle_reasoning_delta("more...")
        events = self._parse_events(result)
        types = [e[0] for e in events]
        self.assertNotIn("response.output_item.added", types)

    def test_reasoning_delta_event_name_is_correct(self):
        """事件名必须是 response.reasoning.delta，不是 response.reasoning_summary_text.delta。"""
        c = self._make_converter()
        result = c._handle_reasoning_delta("thinking")
        events = self._parse_events(result)
        delta_event = next(e for e in events if "delta" in e[0] and "reasoning" in e[0])
        self.assertEqual(delta_event[0], "response.reasoning.delta")

    def test_reasoning_delta_has_no_summary_index(self):
        """response.reasoning.delta 不含 summary_index 字段。"""
        c = self._make_converter()
        result = c._handle_reasoning_delta("thinking")
        events = self._parse_events(result)
        delta_event = next(e for e in events if e[0] == "response.reasoning.delta")
        self.assertNotIn("summary_index", delta_event[1])

    def test_accumulated_reasoning(self):
        c = self._make_converter()
        c._handle_reasoning_delta("Think ")
        c._handle_reasoning_delta("harder")
        self.assertEqual(c.accumulated_reasoning, "Think harder")

    def test_close_reasoning_block_item_structure(self):
        c = self._make_converter()
        c._handle_reasoning_delta("deep thought")
        result = c._close_reasoning_block()
        events = self._parse_events(result)
        item_done = next(e for e in events if e[0] == "response.output_item.done")
        item = item_done[1]["item"]
        self.assertEqual(item["type"], "reasoning")
        self.assertEqual(len(item["summary"]), 1)
        self.assertEqual(item["summary"][0]["type"], "summary_text")
        self.assertEqual(item["summary"][0]["text"], "deep thought")
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_transform.py::TestHandleReasoningDelta -v
```
期望：`AttributeError: 'CodexStreamConverter' object has no attribute '_handle_reasoning_delta'`

- [ ] **Step 3: 添加两个方法到 `CodexStreamConverter`**

```python
    def _handle_reasoning_delta(self, reasoning: str) -> list:
        events = []
        if not self.reasoning_opened:
            self.reasoning_output_index = self.next_output_index
            self.next_output_index += 1
            self.reasoning_id = f"rs_{uuid.uuid4().hex[:8]}"
            self.reasoning_opened = True
            events.append(self._format_sse("response.output_item.added", {
                "output_index": self.reasoning_output_index,
                "item": {"type": "reasoning", "id": self.reasoning_id, "summary": []},
            }))
        self.accumulated_reasoning += reasoning
        events.append(self._format_sse("response.reasoning.delta", {
            "output_index": self.reasoning_output_index,
            "delta": reasoning,
        }))
        return events

    def _close_reasoning_block(self) -> list:
        if not self.reasoning_opened:
            return []
        item = {
            "type": "reasoning",
            "id": self.reasoning_id,
            "summary": [{"type": "summary_text", "text": self.accumulated_reasoning}],
        }
        self.output_items.append((self.reasoning_output_index, item))
        return [
            self._format_sse("response.reasoning.done", {
                "output_index": self.reasoning_output_index,
                "text": self.accumulated_reasoning,
            }),
            self._format_sse("response.output_item.done", {
                "output_index": self.reasoning_output_index,
                "item": item,
            }),
        ]
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_transform.py::TestHandleReasoningDelta -v
```
期望：`7 passed`

- [ ] **Step 5: Commit**

```bash
cd /Users/xys/.hermes/fact-store-browser && git add transform_responses.py test/test_transform.py && git commit -m "feat: 实现 _handle_reasoning_delta 和 _close_reasoning_block（修正事件名为 response.reasoning.delta）"
```

---

### Task 6：实现 `_handle_refusal_delta`、`_close_refusal_block`、`_emit_message_item_done`

**Files:**
- Modify: `transform_responses.py`
- Test: `test/test_transform.py`

- [ ] **Step 1: 写失败测试**

```python
class TestHandleRefusalDelta(_SSETestBase):
    def _make_converter(self):
        from transform import CodexStreamConverter
        c = CodexStreamConverter()
        c.response_id = "resp-test"
        c.model = "test"
        return c

    def test_pure_refusal_opens_message_item(self):
        c = self._make_converter()
        result = c._handle_refusal_delta("I cannot help")
        events = self._parse_events(result)
        types = [e[0] for e in events]
        self.assertIn("response.output_item.added", types)
        self.assertIn("response.content_part.added", types)
        self.assertIn("response.refusal.delta", types)

    def test_refusal_content_part_added_structure(self):
        c = self._make_converter()
        result = c._handle_refusal_delta("No")
        events = self._parse_events(result)
        part_added = next(e for e in events if e[0] == "response.content_part.added")
        self.assertEqual(part_added[1]["part"]["type"], "refusal")
        self.assertEqual(part_added[1]["part"]["refusal"], "")

    def test_refusal_content_index_zero_when_no_text(self):
        c = self._make_converter()
        c._handle_refusal_delta("No")
        self.assertEqual(c.refusal_content_index, 0)

    def test_refusal_content_index_one_when_text_opened(self):
        c = self._make_converter()
        c._handle_text_delta("Some text")   # opens text content part
        c._handle_refusal_delta("No")
        self.assertEqual(c.refusal_content_index, 1)

    def test_refusal_content_index_stored_not_recomputed(self):
        """关键：_close_refusal_block 用存储的 index，即使 text_content_part_opened 已被置 False。"""
        c = self._make_converter()
        c._handle_text_delta("Some text")
        c._handle_refusal_delta("No")
        c._close_text_block()   # 置 text_content_part_opened = False
        # refusal_content_index 应仍为 1
        result = c._close_refusal_block()
        events = self._parse_events(result)
        done = next(e for e in events if e[0] == "response.refusal.done")
        self.assertEqual(done[1]["content_index"], 1)   # 不能变成 0

    def test_emit_message_item_done_status_completed(self):
        c = self._make_converter()
        c._handle_text_delta("Hello")
        result = c._emit_message_item_done()
        events = self._parse_events(result)
        item_done = next(e for e in events if e[0] == "response.output_item.done")
        self.assertEqual(item_done[1]["item"]["status"], "completed")

    def test_emit_message_item_done_empty_content_fallback(self):
        """边界：message 被打开但无文本也无拒绝时，兜底补空 output_text 块。"""
        c = self._make_converter()
        # Manually open message without any content
        c.text_output_index = c.next_output_index
        c.next_output_index += 1
        c.text_message_id = "msg_test"
        c.text_message_opened = True
        result = c._emit_message_item_done()
        events = self._parse_events(result)
        item_done = next(e for e in events if e[0] == "response.output_item.done")
        content = item_done[1]["item"]["content"]
        self.assertEqual(len(content), 1)
        self.assertEqual(content[0]["type"], "output_text")
        self.assertEqual(content[0]["text"], "")

    def test_emit_message_item_done_text_and_refusal_merged(self):
        c = self._make_converter()
        c._handle_text_delta("Some text")
        c._handle_refusal_delta("No")
        result = c._emit_message_item_done()
        events = self._parse_events(result)
        item_done = next(e for e in events if e[0] == "response.output_item.done")
        content = item_done[1]["item"]["content"]
        types = [b["type"] for b in content]
        self.assertIn("output_text", types)
        self.assertIn("refusal", types)

    def test_emit_message_item_done_appended_to_output_items(self):
        c = self._make_converter()
        c._handle_text_delta("Hello")
        c._emit_message_item_done()
        self.assertEqual(len(c.output_items), 1)
        idx, item = c.output_items[0]
        self.assertEqual(item["type"], "message")
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_transform.py::TestHandleRefusalDelta -v
```
期望：`AttributeError: 'CodexStreamConverter' object has no attribute '_handle_refusal_delta'`

- [ ] **Step 3: 添加三个方法到 `CodexStreamConverter`**

```python
    def _handle_refusal_delta(self, refusal: str) -> list:
        events = []
        if not self.text_message_opened:
            self.text_output_index = self.next_output_index
            self.next_output_index += 1
            self.text_message_id = f"msg_{uuid.uuid4().hex[:8]}"
            self.text_message_opened = True       # 必须在 added 后置 True，纯 refusal 场景靠此使 finish() 步骤 4 命中
            events.append(self._format_sse("response.output_item.added", {
                "output_index": self.text_output_index,
                "item": {
                    "type": "message",
                    "id": self.text_message_id,
                    "status": "in_progress",
                    "role": "assistant",
                    "content": [],
                },
            }))
        if not self.refusal_opened:
            self.refusal_content_index = 1 if self.text_content_part_opened else 0
            self.refusal_opened = True
            events.append(self._format_sse("response.content_part.added", {
                "output_index": self.text_output_index,
                "content_index": self.refusal_content_index,
                "part": {"type": "refusal", "refusal": ""},
            }))
        self.accumulated_refusal += refusal
        events.append(self._format_sse("response.refusal.delta", {
            "output_index": self.text_output_index,
            "content_index": self.refusal_content_index,
            "delta": refusal,
        }))
        return events

    def _close_refusal_block(self) -> list:
        if not self.refusal_opened:
            return []
        return [
            self._format_sse("response.refusal.done", {
                "output_index": self.text_output_index,
                "content_index": self.refusal_content_index,
                "refusal": self.accumulated_refusal,
            }),
            self._format_sse("response.content_part.done", {
                "output_index": self.text_output_index,
                "content_index": self.refusal_content_index,
                "part": {"type": "refusal", "refusal": self.accumulated_refusal},
            }),
        ]

    def _emit_message_item_done(self) -> list:
        content = []
        if self.accumulated_text:
            content.append({
                "type": "output_text",
                "text": self.accumulated_text,
                "annotations": [],
            })
        if self.accumulated_refusal:
            content.append({"type": "refusal", "refusal": self.accumulated_refusal})
        if not content:
            content.append({"type": "output_text", "text": "", "annotations": []})
        item = {
            "type": "message",
            "id": self.text_message_id,
            "status": "completed",
            "role": "assistant",
            "content": content,
        }
        self.output_items.append((self.text_output_index, item))
        return [self._format_sse("response.output_item.done", {
            "output_index": self.text_output_index,
            "item": item,
        })]
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_transform.py::TestHandleRefusalDelta -v
```
期望：`9 passed`

- [ ] **Step 5: Commit**

```bash
cd /Users/xys/.hermes/fact-store-browser && git add transform_responses.py test/test_transform.py && git commit -m "feat: 实现 refusal 处理三件套（_handle_refusal_delta、_close_refusal_block、_emit_message_item_done）"
```

---

### Task 7：实现 `_handle_tool_call_delta` + `_close_tool_blocks`

**Files:**
- Modify: `transform_responses.py`
- Test: `test/test_transform.py`

- [ ] **Step 1: 写失败测试**

```python
class TestHandleToolCallDelta(_SSETestBase):
    def _make_converter(self):
        from transform import CodexStreamConverter
        c = CodexStreamConverter()
        c.response_id = "resp-test"
        c.model = "test"
        return c

    def test_delayed_start_fires_after_id_and_name_ready(self):
        c = self._make_converter()
        # First delta: has id but no name yet
        result1 = c._handle_tool_call_delta({
            "index": 0, "id": "call_abc", "type": "function",
            "function": {"name": "", "arguments": ""},
        })
        events1 = self._parse_events(result1)
        self.assertFalse(any(e[0] == "response.output_item.added" for e in events1),
                         "id 没有 name 时不应触发 output_item.added")
        # Second delta: name arrives
        result2 = c._handle_tool_call_delta({
            "index": 0,
            "function": {"name": "bash", "arguments": '{"cmd":'},
        })
        events2 = self._parse_events(result2)
        self.assertTrue(any(e[0] == "response.output_item.added" for e in events2),
                        "id+name 都就绪时应触发 output_item.added")

    def test_output_item_added_structure_for_tool(self):
        c = self._make_converter()
        result = c._handle_tool_call_delta({
            "index": 0, "id": "call_abc", "type": "function",
            "function": {"name": "bash", "arguments": ""},
        })
        events = self._parse_events(result)
        added = next(e for e in events if e[0] == "response.output_item.added")
        item = added[1]["item"]
        self.assertEqual(item["type"], "function_call")
        self.assertEqual(item["call_id"], "call_abc")
        self.assertEqual(item["name"], "bash")
        self.assertEqual(item["arguments"], "")
        self.assertEqual(item["status"], "in_progress")

    def test_accumulated_args_flushed_on_start(self):
        """延迟启动：启动前积累的 args 在 output_item.added 后一次性通过 delta 发出。"""
        c = self._make_converter()
        # args arrive before name
        c._handle_tool_call_delta({"index": 0, "id": "call_abc", "function": {"name": "", "arguments": '{"a":'}})
        c._handle_tool_call_delta({"index": 0, "function": {"name": "", "arguments": '"b"}'}})
        # now name arrives
        result = c._handle_tool_call_delta({"index": 0, "function": {"name": "bash", "arguments": ""}})
        events = self._parse_events(result)
        # Should have output_item.added and then function_call_arguments.delta with accumulated args
        types = [e[0] for e in events]
        self.assertIn("response.output_item.added", types)
        self.assertIn("response.function_call_arguments.delta", types)
        delta_event = next(e for e in events if e[0] == "response.function_call_arguments.delta")
        self.assertEqual(delta_event[1]["delta"], '{"a":"b"}')

    def test_close_tool_blocks_emits_done_events(self):
        c = self._make_converter()
        c._handle_tool_call_delta({
            "index": 0, "id": "call_abc", "type": "function",
            "function": {"name": "bash", "arguments": '{"cmd":"ls"}'},
        })
        result = c._close_tool_blocks()
        events = self._parse_events(result)
        types = [e[0] for e in events]
        self.assertIn("response.function_call_arguments.done", types)
        self.assertIn("response.output_item.done", types)

    def test_close_tool_blocks_item_done_status_completed(self):
        c = self._make_converter()
        c._handle_tool_call_delta({
            "index": 0, "id": "call_abc", "type": "function",
            "function": {"name": "bash", "arguments": '{}'},
        })
        result = c._close_tool_blocks()
        events = self._parse_events(result)
        item_done = next(e for e in events if e[0] == "response.output_item.done")
        self.assertEqual(item_done[1]["item"]["status"], "completed")

    def test_close_tool_blocks_fallback_for_unstarted_block(self):
        """强制启动：call_id/name 从未就绪时使用 fallback。"""
        c = self._make_converter()
        c._handle_tool_call_delta({"index": 0, "function": {"name": "", "arguments": '{}'}})
        result = c._close_tool_blocks()
        events = self._parse_events(result)
        added = next(e for e in events if e[0] == "response.output_item.added")
        item = added[1]["item"]
        self.assertEqual(item["call_id"], "tool_call_0")
        self.assertEqual(item["name"], "unknown_tool")

    def test_multiple_tools_ordered_by_index(self):
        c = self._make_converter()
        # index=1 arrives first
        c._handle_tool_call_delta({
            "index": 1, "id": "call_2", "type": "function",
            "function": {"name": "read_file", "arguments": "{}"},
        })
        # index=0 arrives second
        c._handle_tool_call_delta({
            "index": 0, "id": "call_1", "type": "function",
            "function": {"name": "bash", "arguments": '{"cmd":"ls"}'},
        })
        result = c._close_tool_blocks()
        events = self._parse_events(result)
        item_dones = [e for e in events if e[0] == "response.output_item.done"]
        names = [e[1]["item"]["name"] for e in item_dones]
        # bash (index=0) should come before read_file (index=1)
        self.assertEqual(names.index("bash"), 0)
        self.assertEqual(names.index("read_file"), 1)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_transform.py::TestHandleToolCallDelta -v
```
期望：`AttributeError: 'CodexStreamConverter' object has no attribute '_handle_tool_call_delta'`

- [ ] **Step 3: 添加两个方法到 `CodexStreamConverter`**

```python
    def _handle_tool_call_delta(self, tc_delta: dict) -> list:
        events = []
        tc_index = tc_delta.get("index", 0)
        if tc_index not in self.tool_blocks:
            self.tool_blocks[tc_index] = ToolBlockState()
        block = self.tool_blocks[tc_index]

        tc_id = tc_delta.get("id", "")
        if tc_id:
            block.call_id = tc_id

        func = tc_delta.get("function", {})
        func_name = func.get("name", "")
        if func_name:
            block.name = func_name

        func_args = func.get("arguments", "")
        if func_args:
            block.accumulated_args += func_args

        # 延迟启动：call_id 和 name 都就绪时才发 output_item.added
        if not block.started and block.call_id and block.name:
            block.output_index = self.next_output_index
            self.next_output_index += 1
            block.item_id = f"fc_{uuid.uuid4().hex[:8]}"
            block.started = True
            events.append(self._format_sse("response.output_item.added", {
                "output_index": block.output_index,
                "item": {
                    "type": "function_call",
                    "id": block.item_id,
                    "call_id": block.call_id,
                    "name": block.name,
                    "arguments": "",
                    "status": "in_progress",
                },
            }))
            # 一次性发出之前积累的 args
            if block.accumulated_args:
                events.append(self._format_sse("response.function_call_arguments.delta", {
                    "output_index": block.output_index,
                    "call_id": block.call_id,
                    "delta": block.accumulated_args,
                }))
        elif block.started and func_args:
            events.append(self._format_sse("response.function_call_arguments.delta", {
                "output_index": block.output_index,
                "call_id": block.call_id,
                "delta": func_args,
            }))
        return events

    def _close_tool_blocks(self) -> list:
        events = []
        # 已就绪的块按 output_index 排序；未就绪的按 tc_index 追加到末尾
        started = sorted(
            [(idx, b) for idx, b in self.tool_blocks.items() if b.started],
            key=lambda x: x[1].output_index,
        )
        unstarted = sorted(
            [(idx, b) for idx, b in self.tool_blocks.items() if not b.started],
            key=lambda x: x[0],
        )

        for tc_index, block in started:
            events.extend(self._emit_tool_block_done(block))

        for tc_index, block in unstarted:
            # Fallback
            block.call_id = block.call_id or f"tool_call_{tc_index}"
            block.name = block.name or "unknown_tool"
            block.output_index = self.next_output_index
            self.next_output_index += 1
            block.item_id = f"fc_{uuid.uuid4().hex[:8]}"
            block.started = True
            events.append(self._format_sse("response.output_item.added", {
                "output_index": block.output_index,
                "item": {
                    "type": "function_call",
                    "id": block.item_id,
                    "call_id": block.call_id,
                    "name": block.name,
                    "arguments": "",
                    "status": "in_progress",
                },
            }))
            if block.accumulated_args:
                events.append(self._format_sse("response.function_call_arguments.delta", {
                    "output_index": block.output_index,
                    "call_id": block.call_id,
                    "delta": block.accumulated_args,
                }))
            events.extend(self._emit_tool_block_done(block))

        return events

    def _emit_tool_block_done(self, block: "ToolBlockState") -> list:
        item = {
            "type": "function_call",
            "id": block.item_id,
            "call_id": block.call_id,
            "name": block.name,
            "arguments": block.accumulated_args,
            "status": "completed",
        }
        self.output_items.append((block.output_index, item))
        return [
            self._format_sse("response.function_call_arguments.done", {
                "output_index": block.output_index,
                "call_id": block.call_id,
                "arguments": block.accumulated_args,
            }),
            self._format_sse("response.output_item.done", {
                "output_index": block.output_index,
                "item": item,
            }),
        ]
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_transform.py::TestHandleToolCallDelta -v
```
期望：`7 passed`

- [ ] **Step 5: Commit**

```bash
cd /Users/xys/.hermes/fact-store-browser && git add transform_responses.py test/test_transform.py && git commit -m "feat: 实现工具调用延迟启动、_handle_tool_call_delta 和 _close_tool_blocks"
```

---

### Task 8：实现 `_convert_usage`、`process_chunk`、`finish`

**Files:**
- Modify: `transform_responses.py`
- Test: `test/test_transform.py`

- [ ] **Step 1: 写失败测试**

```python
class TestProcessChunkAndFinish(_SSETestBase):
    def _make_converter(self, response_id="resp-test"):
        from transform import CodexStreamConverter
        c = CodexStreamConverter()
        c.response_id = response_id
        return c

    def test_first_chunk_triggers_emit_created_and_updates_model(self):
        c = self._make_converter()
        result = c.process_chunk({"model": "gpt-4o", "choices": [{"delta": {"content": "Hi"}}]})
        self.assertTrue(c.created_sent)
        self.assertEqual(c.model, "gpt-4o")
        events = self._parse_events(result)
        types = [e[0] for e in events]
        self.assertIn("response.created", types)
        self.assertIn("response.in_progress", types)

    def test_second_chunk_no_duplicate_created(self):
        c = self._make_converter()
        c.process_chunk({"model": "test", "choices": [{"delta": {"content": "Hi"}}]})
        result = c.process_chunk({"choices": [{"delta": {"content": " there"}}]})
        events = self._parse_events(result)
        types = [e[0] for e in events]
        self.assertNotIn("response.created", types)

    def test_process_chunk_captures_finish_reason(self):
        c = self._make_converter()
        c.process_chunk({"model": "test", "choices": [{"delta": {}, "finish_reason": "stop"}]})
        self.assertEqual(c.finish_reason, "stop")

    def test_process_chunk_captures_usage(self):
        c = self._make_converter()
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        c.process_chunk({"model": "test", "choices": [{"delta": {}}], "usage": usage})
        self.assertEqual(c.final_usage["prompt_tokens"], 10)

    def test_finish_emits_completed_and_done(self):
        c = self._make_converter()
        c.process_chunk({"model": "test", "choices": [{"delta": {"content": "Hello"}}]})
        c.process_chunk({"choices": [{"delta": {}, "finish_reason": "stop"}],
                         "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}})
        result = c.finish()
        all_text = "".join(result)
        self.assertIn("response.completed", all_text)
        self.assertIn("data: [DONE]", all_text)

    def test_finish_on_empty_stream_still_sends_created(self):
        c = self._make_converter()
        c.response_id = "resp-empty"
        c.model = "test"
        result = c.finish()
        all_text = "".join(result)
        self.assertIn("response.created", all_text)
        self.assertIn("response.in_progress", all_text)
        self.assertIn("response.completed", all_text)
        self.assertIn("data: [DONE]", all_text)

    def test_finish_incomplete_sends_response_incomplete_and_completed(self):
        """finish_reason=length 时同时发送 response.incomplete 和 response.completed（behavior preserved from old code）。"""
        import json
        c = self._make_converter()
        c.model = "test"
        c.process_chunk({"model": "test", "choices": [{"delta": {"content": "partial"}, "finish_reason": "length"}],
                         "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
        result = c.finish()
        all_text = "".join(result)
        self.assertIn("response.incomplete", all_text)
        self.assertIn("response.completed", all_text)
        # 确认 completed 中 response.status 为 "incomplete"
        data = json.loads([line for s in result for line in s.split("\n")
                          if line.startswith("data: ") and "response.completed" in line][0][6:])
        self.assertEqual(data["response"]["status"], "incomplete")

    def test_finish_output_sorted_by_index(self):
        """reasoning（index=0）先于 text（index=1）出现在 response.completed output 中。"""
        import json
        c = self._make_converter()
        c.model = "test"
        c.process_chunk({"model": "test", "choices": [{"delta": {"reasoning_content": "Think"}}]})
        c.process_chunk({"choices": [{"delta": {"content": "Answer"}}]})
        c.process_chunk({"choices": [{"delta": {}, "finish_reason": "stop"}],
                         "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}})
        result = c.finish()
        all_text = "".join(result)
        completed_line = next(
            line for s in result for line in s.split("\n")
            if line.startswith("data: ") and "response.completed" in line
        )
        completed = json.loads(completed_line[6:])
        output = completed["response"]["output"]
        self.assertEqual(output[0]["type"], "reasoning")
        self.assertEqual(output[1]["type"], "message")
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_transform.py::TestProcessChunkAndFinish -v
```
期望：`AttributeError: 'CodexStreamConverter' object has no attribute 'process_chunk'`

- [ ] **Step 3: 添加 `_convert_usage`、`process_chunk`、`finish` 到 `CodexStreamConverter`**

```python
    def _convert_usage(self, raw: dict) -> dict:
        usage = {
            "input_tokens": _find_first(raw, ["prompt_tokens", "input_tokens"]),
            "output_tokens": _find_first(raw, ["completion_tokens", "output_tokens"]),
            "total_tokens": raw.get("total_tokens", 0),
        }
        details = {"cached_tokens": 0}
        for k in ("prompt_tokens_details", "input_tokens_details"):
            if raw.get(k):
                details.update(raw[k])
        usage["input_tokens_details"] = details
        out_det = raw.get("completion_tokens_details") or raw.get("output_tokens_details")
        usage["output_tokens_details"] = out_det or {"reasoning_tokens": 0}
        for k in ("cache_read_input_tokens", "cache_creation_input_tokens"):
            if k in raw and raw[k] is not None:
                usage[k] = raw[k]
        return usage

    def process_chunk(self, chunk: dict) -> list:
        events = []
        # 首个 chunk：更新 model，发 created 三件套
        if not self.created_sent:
            model = chunk.get("model", "")
            if model:
                self.model = model
            events.extend(self._emit_created())
        # 捕获 usage
        if chunk.get("usage"):
            self.final_usage = chunk["usage"]
        # 处理 choices
        for choice in chunk.get("choices", []):
            if choice.get("finish_reason"):
                self.finish_reason = choice["finish_reason"]
            delta = choice.get("delta", {})
            if not delta:
                continue
            # 顺序：content → refusal → reasoning → tool_calls
            content = delta.get("content")
            if content:
                events.extend(self._handle_text_delta(content))
            refusal = delta.get("refusal")
            if refusal:
                events.extend(self._handle_refusal_delta(refusal))
            for key in ("reasoning_content", "thinking", "reasoning"):
                reasoning = delta.get(key)
                if reasoning:
                    events.extend(self._handle_reasoning_delta(reasoning))
                    break
            tool_calls = delta.get("tool_calls")
            if tool_calls:
                for tc in tool_calls:
                    events.extend(self._handle_tool_call_delta(tc))
        return events

    def finish(self) -> list:
        events = []
        if not self.created_sent:
            self.model = self.model or ""
            events.extend(self._emit_created())
        if self.text_content_part_opened:
            events.extend(self._close_text_block())
        if self.refusal_opened:
            events.extend(self._close_refusal_block())
        if self.text_message_opened:
            events.extend(self._emit_message_item_done())
        if self.reasoning_opened:
            events.extend(self._close_reasoning_block())
        events.extend(self._close_tool_blocks())
        # 按 output_index 排序
        self.output_items.sort(key=lambda x: x[0])
        output_list = [item for _, item in self.output_items]
        # 构建 usage
        usage = self._convert_usage(self.final_usage) if self.final_usage else None
        # 构建 response
        if self.finish_reason in INCOMPLETE_REASON_MAP:
            incomplete_details = {"reason": INCOMPLETE_REASON_MAP[self.finish_reason]}
            status = "incomplete"
            resp = self._build_response_obj(status, usage=usage, output=output_list,
                                            incomplete_details=incomplete_details)
            events.append(self._format_sse("response.incomplete", {"response": resp}))
        else:
            status = "completed"
            resp = self._build_response_obj(status, usage=usage, output=output_list)
        events.append(self._format_sse("response.completed", {"response": resp}))
        events.append("data: [DONE]\n\n")
        return events
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_transform.py::TestProcessChunkAndFinish -v
```
期望：`8 passed`

- [ ] **Step 5: Commit**

```bash
cd /Users/xys/.hermes/fact-store-browser && git add transform_responses.py test/test_transform.py && git commit -m "feat: 实现 process_chunk / finish / _convert_usage——CodexStreamConverter 核心循环完成"
```

---

### Task 9：接入 `create_codex_sse_stream` + 更新 `transform.py`

**Files:**
- Modify: `transform_responses.py`（修改 `create_codex_sse_stream` 函数体）
- Modify: `transform.py`（更新 re-export：移除旧函数，添加新符号）

- [ ] **Step 1: 修改 `create_codex_sse_stream()`**

将 `transform_responses.py` 中 `create_codex_sse_stream` 函数体完整替换为：

```python
def create_codex_sse_stream(upstream_response):
    """读取上游 SSE 流，逐事件 yield Responses API 格式的 SSE 字符串。"""
    converter = CodexStreamConverter()
    converter.response_id = generate_response_id()

    for event in iter_sse_events(upstream_response):
        if event["event"] == "[DONE]":
            break
        data = event.get("data")
        if not data:
            continue
        for sse_str in converter.process_chunk(data):
            yield sse_str

    for sse_str in converter.finish():
        yield sse_str
```

- [ ] **Step 2: 更新 `transform.py`**

在 `transform_responses` 导入块中：
- **移除**：`StreamState`（改从别名获得）、`_process_delta`、`_emit_completion`（旧顶层函数）
- **添加**：`CodexStreamConverter`、`ToolBlockState`

完整更新后的 `transform_responses` 导入块：

```python
from transform_responses import (  # noqa: F401 — re-export
    generate_response_id,
    responses_to_chat,
    chat_to_responses,
    create_codex_sse_stream,
    _parse_sse_event,
    iter_sse_events,
    StreamState,
    CodexStreamConverter,
    ToolBlockState,
    _map_tools,
    _map_response_format,
)
```

- [ ] **Step 3: 删除 `transform_responses.py` 中已废弃的三个顶层函数**

删除以下三个函数（它们已成为 `CodexStreamConverter` 的方法）：
- `def _emit_created(state: StreamState) -> list:` （第 430-453 行附近）
- `def _process_delta(delta: dict, state: StreamState) -> list:` （第 456-517 行附近）
- `def _emit_completion(state: StreamState) -> list:` （第 520-612 行附近）

同时删除旧的 `StreamState` dataclass 定义（第 361-385 行附近），因为现在 `StreamState = CodexStreamConverter` 别名已在 `CodexStreamConverter` 定义后设置。

- [ ] **Step 4: 运行测试确认部分失败（不提交，立即进入 Task 10 修复）**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_transform.py -v 2>&1 | head -60
```
期望：大部分通过，以下测试失败：
- `TestStreamState::test_message_output_index_no_reasoning`（引用已删除的 `message_output_index` property）
- `TestStreamState::test_message_output_index_with_reasoning`（同上）
- `test_reasoning_plus_text_stream`（引用旧事件名 `response.reasoning_summary_text.delta`）
- `test_reasoning_plus_text_events_have_type_field`（同上）
- `test_text_only_stream`（缺少 `response.in_progress` 等新事件）
- `test_tool_calls_accumulation`（缺少 `output_item.added` 等新事件）

**注意：此步骤不提交**（CLAUDE.md 硬规定：每个 commit 必须全量测试通过），立即进入 Task 10 修复失败测试。Task 9 和 Task 10 的全部文件变更合并为一次 commit（在 Task 10 Step 7 提交）。

---

### Task 10：更新现有失败测试

**Files:**
- Modify: `test/test_transform.py`

- [ ] **Step 1: 更新 `TestStreamState` 类**

用以下新版本替换整个 `TestStreamState` 类：

```python
class TestStreamState(unittest.TestCase):
    def test_next_output_index_increments_on_text(self):
        from transform import CodexStreamConverter
        c = CodexStreamConverter()
        c.response_id = "resp-test"
        c.model = "test"
        self.assertEqual(c.next_output_index, 0)
        c._handle_text_delta("Hello")
        self.assertEqual(c.next_output_index, 1)
        self.assertEqual(c.text_output_index, 0)

    def test_next_output_index_increments_on_reasoning_then_text(self):
        from transform import CodexStreamConverter
        c = CodexStreamConverter()
        c.response_id = "resp-test"
        c.model = "test"
        c._handle_reasoning_delta("Think")
        c._handle_text_delta("Answer")
        self.assertEqual(c.reasoning_output_index, 0)
        self.assertEqual(c.text_output_index, 1)

    def test_stream_state_is_codex_stream_converter(self):
        from transform import StreamState, CodexStreamConverter
        self.assertIs(StreamState, CodexStreamConverter)
```

- [ ] **Step 2: 更新 `test_reasoning_plus_text_stream`**

将以下两行：
```python
self.assertIn("event: response.reasoning_summary_text.delta", events_text)
self.assertIn("event: response.reasoning_summary_text.done", events_text)
```
改为：
```python
self.assertIn("event: response.reasoning.delta", events_text)
self.assertIn("event: response.reasoning.done", events_text)
```
同时补充：
```python
self.assertIn("data: [DONE]", events_text)
```

- [ ] **Step 3: 更新 `test_text_only_stream`**

在 `self.assertIn("event: response.completed", events_text)` 之后添加：
```python
self.assertIn("event: response.in_progress", events_text)
self.assertIn("event: response.content_part.added", events_text)
self.assertIn("event: response.content_part.done", events_text)
self.assertIn("data: [DONE]", events_text)
```
同时将原有：
```python
self.assertNotIn("response.reasoning_summary_text", events_text)
```
改为：
```python
self.assertNotIn("response.reasoning", events_text)
```

- [ ] **Step 4: 更新 `test_tool_calls_accumulation`**

在现有断言之后添加：
```python
self.assertIn("event: response.output_item.added", events_text)
self.assertIn("event: response.function_call_arguments.delta", events_text)
self.assertIn("event: response.function_call_arguments.done", events_text)
self.assertIn("data: [DONE]", events_text)
```

- [ ] **Step 5: 更新 `test_reasoning_plus_text_events_have_type_field`（`TestSSEEventFormatIntegration`）**

将 `expected` 集合中的旧事件名替换：
```python
# 原来：
"response.reasoning_summary_text.delta",
"response.reasoning_summary_text.done",
# 改为：
"response.reasoning.delta",
"response.reasoning.done",
```
同时添加新事件名到 expected：
```python
"response.in_progress",
"response.content_part.added",
"response.content_part.done",
```

- [ ] **Step 6: 运行全量测试确认通过**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_transform.py -v
```
期望：全部通过（含原有的 40+ 测试）

- [ ] **Step 7: Commit**

```bash
cd /Users/xys/.hermes/fact-store-browser && git add transform_responses.py transform.py test/test_transform.py && git commit -m "test: 切换 create_codex_sse_stream 为 CodexStreamConverter，移除旧顶层函数，更新 5 个现有测试以匹配新事件名称（含 Task 9 全部变更，合并 commit 确保测试全量通过）"
```

---

### Task 11：修复 `chat_to_responses` text+refusal 合并

**Files:**
- Modify: `transform_responses.py`（`chat_to_responses` 函数）
- Test: `test/test_transform.py`

- [ ] **Step 1: 写失败测试**

```python
class TestChatToResponsesRefusalMerge(unittest.TestCase):
    def test_text_and_refusal_merged_into_single_message_item(self):
        """text + refusal 应合并进同一个 message output item 的 content 数组。"""
        from transform import chat_to_responses
        chat_resp = {
            "id": "chatcmpl-merge",
            "model": "test",
            "choices": [{
                "message": {
                    "content": "I can help with some things.",
                    "refusal": "But not with this.",
                },
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        result = chat_to_responses(chat_resp)
        msg_items = [o for o in result["output"] if o["type"] == "message"]
        self.assertEqual(len(msg_items), 1, "text+refusal 必须合并为单个 message item")
        content = msg_items[0]["content"]
        types = [b["type"] for b in content]
        self.assertIn("output_text", types)
        self.assertIn("refusal", types)

    def test_refusal_only_creates_single_message_item(self):
        from transform import chat_to_responses
        chat_resp = {
            "id": "chatcmpl-refonly",
            "model": "test",
            "choices": [{
                "message": {"content": None, "refusal": "I cannot help with that."},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        result = chat_to_responses(chat_resp)
        msg_items = [o for o in result["output"] if o["type"] == "message"]
        self.assertEqual(len(msg_items), 1)
        self.assertEqual(msg_items[0]["content"][0]["type"], "refusal")

    def test_text_only_still_works(self):
        from transform import chat_to_responses
        chat_resp = {
            "id": "chatcmpl-txtonly",
            "model": "test",
            "choices": [{"message": {"content": "Hello"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        result = chat_to_responses(chat_resp)
        msg_items = [o for o in result["output"] if o["type"] == "message"]
        self.assertEqual(len(msg_items), 1)
        self.assertEqual(msg_items[0]["content"][0]["text"], "Hello")
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_transform.py::TestChatToResponsesRefusalMerge -v
```
期望：`test_text_and_refusal_merged_into_single_message_item` 失败（当前生成 2 个 message item）

- [ ] **Step 3: 修改 `chat_to_responses` 函数中 content/refusal 部分**

将 `transform_responses.py` 中 `chat_to_responses` 函数内的以下代码块：

```python
    # 文本内容
    content = message.get("content")
    if content:
        output.append({
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": content}],
            "status": FINISH_REASON_MAP.get(finish_reason, "completed"),
        })

    # 拒绝内容
    refusal = message.get("refusal")
    if refusal:
        output.append({
            "type": "message",
            "role": "assistant",
            "content": [{"type": "refusal", "refusal": refusal}],
            "status": FINISH_REASON_MAP.get(finish_reason, "completed"),
        })
```

替换为：

```python
    # 文本和拒绝内容合并到同一 message item
    content = message.get("content")
    refusal = message.get("refusal")
    if content or refusal:
        msg_content = []
        if content:
            msg_content.append({"type": "output_text", "text": content, "annotations": []})
        if refusal:
            msg_content.append({"type": "refusal", "refusal": refusal})
        output.append({
            "type": "message",
            "id": f"msg_{uuid.uuid4().hex[:8]}",
            "role": "assistant",
            "content": msg_content,
            "status": FINISH_REASON_MAP.get(finish_reason, "completed"),
        })
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_transform.py::TestChatToResponsesRefusalMerge test/test_transform.py::TestChatToResponses -v
```
期望：全部通过（含 `TestChatToResponses` 现有测试，确认新增 `id` 字段无回归——现有测试若依赖 output item 无 `id` 字段则会在此时暴露）

- [ ] **Step 5: Commit**

```bash
cd /Users/xys/.hermes/fact-store-browser && git add transform_responses.py test/test_transform.py && git commit -m "fix: chat_to_responses 将 text+refusal 合并为单个 message item，添加 id 字段"
```

---

### Task 12：修复 `proxy.py` 错误路径补发 `[DONE]` + 缓冲区扩容

**Files:**
- Modify: `proxy.py`（`_forward_streaming` 内三处错误路径 + `iter_sse_events`）
- Modify: `transform_responses.py`（`iter_sse_events` 缓冲区 256→4096）

- [ ] **Step 1: 写失败测试（proxy 错误路径）**

```python
class TestProxyErrorPathsDone(unittest.TestCase):
    """验证三处错误路径各包含 data: [DONE]。"""

    def _read_forward_streaming_source(self):
        import pathlib
        return pathlib.Path("/Users/xys/.hermes/fact-store-browser/proxy.py").read_text()

    def test_non_200_error_path_has_done(self):
        """上游非 200 错误路径在 completed 后补发 [DONE]。"""
        src = self._read_forward_streaming_source()
        # 找到非 200 分支代码段（"Upstream returned HTTP {resp.status}"附近）
        idx = src.index("Upstream returned HTTP")
        segment = src[idx:idx+800]
        self.assertIn("[DONE]", segment,
            "非 200 错误路径缺少 data: [DONE] 终止标记")

    def test_non_sse_content_type_error_path_has_done(self):
        src = self._read_forward_streaming_source()
        idx = src.index("non-SSE Content-Type")
        segment = src[idx:idx+800]
        self.assertIn("[DONE]", segment,
            "非 SSE Content-Type 错误路径缺少 data: [DONE]")

    def test_exception_error_path_has_done(self):
        src = self._read_forward_streaming_source()
        idx = src.index("流式转发异常")
        segment = src[idx:idx+1000]
        self.assertIn("[DONE]", segment,
            "流式转发异常路径缺少 data: [DONE]")

    def test_iter_sse_buffer_size(self):
        """iter_sse_events 读缓冲区应为 4096 字节。"""
        import pathlib
        src = pathlib.Path("/Users/xys/.hermes/fact-store-browser/transform_responses.py").read_text()
        self.assertIn("read(4096)", src, "iter_sse_events 缓冲区应从 256 增大到 4096")
        self.assertNotIn("read(256)", src, "旧 256 字节缓冲区应已删除")
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_transform.py::TestProxyErrorPathsDone -v
```
期望：4 个测试全部失败

- [ ] **Step 3: 在 `proxy.py` 中三处错误路径各补一行 `[DONE]`**

在 **非 200 路径**（`return` 语句前，紧接 `completed_event` 的 `flush` 后）插入：
```python
self.wfile.write(b"data: [DONE]\n\n")
self.wfile.flush()
```

在 **非 SSE Content-Type 路径**（同样位置）插入：
```python
self.wfile.write(b"data: [DONE]\n\n")
self.wfile.flush()
```

在 **流异常路径**（`except Exception as e:` 块中，第二个 `flush` 后）插入：
```python
self.wfile.write(b"data: [DONE]\n\n")
self.wfile.flush()
```

- [ ] **Step 4: 在 `transform_responses.py` 中将 `iter_sse_events` 缓冲区从 256 改为 4096**

找到第 350 行附近的 `chunk = upstream_response.read(256)` 改为 `read(4096)`。

- [ ] **Step 5: 运行测试确认通过**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_transform.py::TestProxyErrorPathsDone -v
```
期望：`4 passed`

- [ ] **Step 6: Commit**

```bash
cd /Users/xys/.hermes/fact-store-browser && git add proxy.py transform_responses.py test/test_transform.py && git commit -m "fix: proxy.py 三处错误路径补发 [DONE] 终止标记，iter_sse_events 缓冲区 256→4096"
```

---

### Task 13：新增测试（设计文稿 §7.2 新增覆盖）

**Files:**
- Modify: `test/test_transform.py`

- [ ] **Step 1: 新增 9 个测试用例（写在文件末尾）**

```python
class TestNewSSEFeatures(unittest.TestCase):
    """Phase 1 新增事件序列的集成测试。"""

    @staticmethod
    def _make_mock_stream(chunks):
        class MockStream:
            def __init__(self):
                self.data = b"".join(chunks)
                self.pos = 0
            def read(self, size):
                chunk = self.data[self.pos:self.pos + size]
                self.pos += size
                return chunk
        return MockStream()

    @staticmethod
    def _parse_events(text):
        import json
        events = []
        for block in text.strip().split("\n\n"):
            lines = block.split("\n")
            etype, data = None, None
            for line in lines:
                if line.startswith("event: "): etype = line[7:]
                elif line.startswith("data: "):
                    try: data = json.loads(line[6:])
                    except json.JSONDecodeError: pass
            if etype and data:
                events.append((etype, data))
        return events

    def _stream_to_text(self, chunks):
        from transform import create_codex_sse_stream
        stream = self._make_mock_stream(chunks)
        return "".join(create_codex_sse_stream(stream))

    def test_created_in_progress_metadata_sequence(self):
        """流开始事件必须为 created→in_progress→metadata 顺序。"""
        chunks = [
            b'data: {"id":"c1","model":"test","choices":[{"delta":{"content":"Hi"},"index":0}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}\n\n',
            b'data: [DONE]\n\n',
        ]
        events = self._parse_events(self._stream_to_text(chunks))
        types = [e[0] for e in events[:3]]
        self.assertEqual(types[0], "response.created")
        self.assertEqual(types[1], "response.in_progress")
        self.assertEqual(types[2], "response.metadata")

    def test_content_part_lifecycle(self):
        """text 的 content_part 生命周期：added → delta×N → done。"""
        chunks = [
            b'data: {"id":"c1","model":"test","choices":[{"delta":{"content":"Hello"},"index":0}]}\n\n',
            b'data: {"choices":[{"delta":{"content":" World"},"index":0}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":2,"total_tokens":3}}\n\n',
            b'data: [DONE]\n\n',
        ]
        text = self._stream_to_text(chunks)
        self.assertIn("response.content_part.added", text)
        self.assertIn("response.content_part.done", text)
        # 验证 added 在 delta 之前
        self.assertLess(text.index("content_part.added"), text.index("output_text.delta"))

    def test_function_call_arguments_delta_and_done(self):
        """工具调用：output_item.added → arguments.delta → arguments.done → output_item.done。"""
        chunks = [
            b'data: {"id":"c1","model":"test","choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"bash","arguments":"{\\"cmd\\":"}}]},"index":0}]}\n\n',
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"ls\\"}"}}]},"index":0}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":5,"completion_tokens":3,"total_tokens":8}}\n\n',
            b'data: [DONE]\n\n',
        ]
        text = self._stream_to_text(chunks)
        self.assertIn("response.output_item.added", text)
        self.assertIn("response.function_call_arguments.delta", text)
        self.assertIn("response.function_call_arguments.done", text)
        # output_item.added 必须在 arguments.delta 之前
        self.assertLess(text.index("output_item.added"), text.index("function_call_arguments.delta"))

    def test_stream_ends_with_done(self):
        """流末尾必须有 data: [DONE]。"""
        chunks = [
            b'data: {"id":"c1","model":"test","choices":[{"delta":{"content":"Hi"},"index":0}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}\n\n',
            b'data: [DONE]\n\n',
        ]
        text = self._stream_to_text(chunks)
        self.assertTrue(text.rstrip().endswith("[DONE]"), "流末尾应有 data: [DONE]")

    def test_empty_stream_finish_still_valid(self):
        """无 delta 的空流：finish() 必须发送 created+in_progress+completed+[DONE]。"""
        chunks = [b'data: [DONE]\n\n']
        text = self._stream_to_text(chunks)
        self.assertIn("response.created", text)
        self.assertIn("response.in_progress", text)
        self.assertIn("response.completed", text)
        self.assertTrue(text.rstrip().endswith("[DONE]"))

    def test_reasoning_event_name_correct(self):
        """推理事件名为 response.reasoning.delta/done，不是 response.reasoning_summary_text.*。"""
        chunks = [
            b'data: {"id":"c1","model":"test","choices":[{"delta":{"reasoning_content":"think"},"index":0}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":1,"total_tokens":6}}\n\n',
            b'data: [DONE]\n\n',
        ]
        text = self._stream_to_text(chunks)
        self.assertIn("response.reasoning.delta", text)
        self.assertIn("response.reasoning.done", text)
        self.assertNotIn("reasoning_summary_text", text)

    def test_refusal_stream_sequence(self):
        """拒绝流：output_item.added → content_part.added → refusal.delta → refusal.done → content_part.done → output_item.done。"""
        chunks = [
            b'data: {"id":"c1","model":"test","choices":[{"delta":{"refusal":"I cannot"},"index":0}]}\n\n',
            b'data: {"choices":[{"delta":{"refusal":" help"},"index":0}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":2,"total_tokens":3}}\n\n',
            b'data: [DONE]\n\n',
        ]
        text = self._stream_to_text(chunks)
        for expected_event in ["response.output_item.added", "response.content_part.added",
                               "response.refusal.delta", "response.refusal.done",
                               "response.content_part.done", "response.output_item.done"]:
            self.assertIn(expected_event, text, f"缺少事件: {expected_event}")
        # 顺序验证
        positions = [text.index(e) for e in [
            "output_item.added", "content_part.added", "refusal.delta",
            "refusal.done", "content_part.done", "output_item.done"
        ]]
        self.assertEqual(positions, sorted(positions), "拒绝事件顺序不正确")

    def test_text_plus_refusal_share_same_message_item(self):
        """text + refusal 同时出现时，共用同一个 message output item（output_index 相同）。"""
        import json
        chunks = [
            b'data: {"id":"c1","model":"test","choices":[{"delta":{"content":"Some text","refusal":"No"},"index":0}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}\n\n',
            b'data: [DONE]\n\n',
        ]
        text = self._stream_to_text(chunks)
        # output_item.added 应只出现一次（共用同一个 message item）
        self.assertEqual(text.count('"response.output_item.added"'), 1,
                         "text+refusal 应共用同一个 message output item")

    def test_multi_tool_concurrent_ordered_by_index(self):
        """多工具并发：index 1 先到，index 0 后到，关闭时按 index 0 < 1 顺序。"""
        chunks = [
            # index=1 先到
            b'data: {"id":"c1","model":"test","choices":[{"delta":{"tool_calls":[{"index":1,"id":"call_2","type":"function","function":{"name":"read_file","arguments":"{}"}}]},"index":0}]}\n\n',
            # index=0 后到
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"bash","arguments":"{\\"cmd\\":\\"ls\\"}"}}]},"index":0}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}\n\n',
            b'data: [DONE]\n\n',
        ]
        text = self._stream_to_text(chunks)
        bash_pos = text.index('"name":"bash"')
        read_pos = text.index('"name":"read_file"')
        self.assertLess(bash_pos, read_pos, "bash (index=0) 应在 read_file (index=1) 之前发送")


class TestOutputItemsToMessages(unittest.TestCase):
    """_output_items_to_messages 独立单元测试（设计文稿 §4.3）。"""

    def test_text_message(self):
        from transform import _output_items_to_messages
        items = [{"type": "message", "content": [{"type": "output_text", "text": "Hello"}]}]
        result = _output_items_to_messages(items)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["role"], "assistant")
        self.assertEqual(result[0]["content"], "Hello")

    def test_pure_refusal_message_fallback_empty_string(self):
        """纯拒绝消息：content 没有 output_text，fallback 为空字符串，不能是 None 或 KeyError。"""
        from transform import _output_items_to_messages
        items = [{"type": "message", "content": [{"type": "refusal", "refusal": "No"}]}]
        result = _output_items_to_messages(items)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["content"], "")   # fallback，不是 None

    def test_multiple_function_calls_merged(self):
        """多个 function_call 合并为单条 assistant 消息。"""
        from transform import _output_items_to_messages
        items = [
            {"type": "function_call", "id": "fc1", "call_id": "call_1", "name": "bash", "arguments": '{}'},
            {"type": "function_call", "id": "fc2", "call_id": "call_2", "name": "read_file", "arguments": '{}'},
        ]
        result = _output_items_to_messages(items)
        self.assertEqual(len(result), 1, "多个工具调用应合并为单条 assistant 消息")
        self.assertIsNone(result[0]["content"])
        self.assertEqual(len(result[0]["tool_calls"]), 2)

    def test_reasoning_skipped(self):
        from transform import _output_items_to_messages
        items = [
            {"type": "reasoning", "id": "rs1", "summary": [{"type": "summary_text", "text": "think"}]},
            {"type": "message", "content": [{"type": "output_text", "text": "Answer"}]},
        ]
        result = _output_items_to_messages(items)
        self.assertEqual(len(result), 1, "reasoning 应被跳过")
        self.assertEqual(result[0]["content"], "Answer")
```

- [ ] **Step 2: 运行新增测试确认部分失败**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_transform.py::TestNewSSEFeatures test/test_transform.py::TestOutputItemsToMessages -v
```
期望：
- `TestNewSSEFeatures` 的 9 个集成测试 — **预期全部通过**（Task 8 已完成 `process_chunk` + `finish()` 实现后即应通过）
- `TestOutputItemsToMessages` — 因 `_output_items_to_messages` 未实现而失败（正常，Step 3 实现该函数即可）

注意：若 `TestNewSSEFeatures` 中有测试失败，则说明 Task 8 的实现有遗漏，需回退修复后再继续。

- [ ] **Step 3: 实现 `_output_items_to_messages` 函数（为 Phase 2 预置）**

在 `transform_responses.py` 末尾添加：

```python
def _output_items_to_messages(output_items: list) -> list:
    """将 Responses API output items 反转为 Chat Messages 格式（用于 conversation 历史）。

    - type=message: 取第一个 output_text block 的 text；纯拒绝时 fallback ""
    - type=function_call: 全部收集后合并为单条 tool_calls 消息
    - type=reasoning: 跳过
    """
    result = []
    tool_calls = []

    for item in output_items:
        itype = item.get("type")
        if itype == "message":
            # 先 flush 积累的 tool_calls
            if tool_calls:
                result.append({"role": "assistant", "content": None, "tool_calls": tool_calls})
                tool_calls = []
            text = next(
                (b["text"] for b in item.get("content", []) if b.get("type") == "output_text"),
                "",
            )
            result.append({"role": "assistant", "content": text})
        elif itype == "function_call":
            tool_calls.append({
                "id": item.get("call_id", item.get("id", "")),
                "type": "function",
                "function": {
                    "name": item.get("name", ""),
                    "arguments": item.get("arguments", ""),
                },
            })
        # reasoning: 跳过

    if tool_calls:
        result.append({"role": "assistant", "content": None, "tool_calls": tool_calls})

    return result
```

在 `transform.py` 的 re-export 列表中添加 `_output_items_to_messages,`。

- [ ] **Step 4: 运行所有测试确认全部通过**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/ -v
```
期望：全部通过（60+ 测试）

- [ ] **Step 5: Commit**

```bash
cd /Users/xys/.hermes/fact-store-browser && git add transform_responses.py transform.py test/test_transform.py && git commit -m "test: 新增 Phase 1 全覆盖测试（SSE 事件序列、refusal、工具并发、_output_items_to_messages）"
```

---

### Task 14：全量测试 + 验收

- [ ] **Step 1: 运行完整测试套件**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/ -v --tb=short
```
期望：全部通过，0 个失败

- [ ] **Step 2: 验证 transform.py 不再导出废弃符号**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -c "
from transform import (
    generate_response_id, responses_to_chat, chat_to_responses,
    create_codex_sse_stream, CodexStreamConverter, ToolBlockState,
    StreamState, _output_items_to_messages
)
print('所有符号导入成功')
try:
    from transform import _emit_created
    print('ERROR: _emit_created 不应再导出')
except ImportError:
    print('正确：_emit_created 已从导出列表移除')
"
```

- [ ] **Step 3: 最终 Commit**

```bash
cd /Users/xys/.hermes/fact-store-browser && git add -A && git commit -m "feat: Phase 1 SSE 状态机重构完成——全量测试通过"
```

---

*Phase 2（Response Store）和 Phase 3（MCP 支持）见独立计划文件。*
