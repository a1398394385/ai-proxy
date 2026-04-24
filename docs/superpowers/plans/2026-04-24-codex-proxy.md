# Codex Proxy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 fact-store-browser 项目中新增一个本地 HTTP 代理，将 Codex CLI 的 Responses API 请求转换为 Chat Completions 格式，转发到 LiteLLM 网关，并将响应转回 Responses API SSE 格式。

**Architecture:** 纯 Python 标准库实现，零外部依赖。`transform.py` 存放纯转换逻辑（可独立测试），`proxy.py` 实现 ThreadedHTTPServer + 路由 + 配置 + 上游转发。`server.sh` 扩展为双进程管理。

**Tech Stack:** Python 3.x stdlib (http.server, http.client, socketserver, ssl, json, logging, dataclasses, gzip, uuid, time)

---

## File Map

```
fact-store-browser/
├── transform.py              # NEW: 纯转换逻辑，无 IO
├── test_transform.py         # NEW: transform.py 的单元测试
├── proxy.py                  # NEW: HTTP server + 路由 + 配置 + 上游转发 + 日志轮转
├── proxy_config.yaml         # NEW: 代理配置示例
├── server.sh                 # MODIFY: 扩展 start/stop/status 管理双进程
```

---

### Task 1: transform.py 骨架 + test_transform.py

**Files:**
- Create: `transform.py` — import + `generate_response_id()`
- Create: `test_transform.py` — 测试骨架
- Test: `python3 -m pytest test_transform.py -v`

- [ ] **Step 1: 创建 transform.py 骨架**

```python
"""纯转换逻辑模块 — 无 IO，可独立测试。

包含：
- responses_to_chat(): Responses API → Chat Completions
- chat_to_responses(): Chat Completions → Responses API
- StreamState + create_codex_sse_stream(): SSE 流转换
- SSE 解析器 iter_sse_events + _parse_sse_event
- generate_response_id(): 生成 resp-{timestamp_ms}-{random_hex8}
"""

import json
import uuid
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


def generate_response_id() -> str:
    """生成 OpenAI 规范 response ID: resp-{timestamp_ms}-{random_hex8}"""
    ts = int(time.time() * 1000)
    rand = uuid.uuid4().hex[:8]
    return f"resp-{ts}-{rand}"
```

- [ ] **Step 2: 创建 test_transform.py**

```python
import unittest
from transform import generate_response_id


class TestGenerateResponseId(unittest.TestCase):
    def test_format(self):
        rid = generate_response_id()
        self.assertTrue(rid.startswith("resp-"))
        parts = rid.split("-")
        self.assertEqual(len(parts), 3)  # resp, timestamp, hex
        self.assertTrue(parts[1].isdigit())  # timestamp_ms
        self.assertEqual(len(parts[2]), 8)  # random_hex8

    def test_uniqueness(self):
        ids = {generate_response_id() for _ in range(100)}
        self.assertEqual(len(ids), 100)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 运行测试验证骨架**

Run: `python3 -m pytest test_transform.py -v`
Expected: 2 tests PASS

- [ ] **Step 4: Commit**

```bash
git add transform.py test_transform.py
git commit -m "feat: add transform.py skeleton and response_id generator with tests"
```

---

### Task 2: responses_to_chat — 基础字段映射

**Files:**
- Modify: `transform.py` — 添加 `responses_to_chat()` 基础映射
- Modify: `test_transform.py` — 添加基础映射测试

- [ ] **Step 1: 写出测试**

```python
class TestResponsesToChatBasic(unittest.TestCase):
    def test_basic_field_mapping(self):
        from transform import responses_to_chat
        body = {
            "model": "codex-mini-latest",
            "instructions": "You are a helpful assistant.",
            "input": [
                {"type": "message", "role": "user", "content": "Hello"}
            ],
            "max_output_tokens": 1000,
            "stream": True,
        }
        model_cfg = {"target": "claude-sonnet-4-6", "multimodal": False}
        result = responses_to_chat(body, model_cfg)

        self.assertEqual(result["model"], "claude-sonnet-4-6")
        self.assertEqual(result["max_tokens"], 1000)
        self.assertEqual(result["stream"], True)
        # instructions → system message
        self.assertEqual(result["messages"][0]["role"], "system")
        self.assertEqual(result["messages"][0]["content"], "You are a helpful assistant.")
        # user message
        self.assertEqual(result["messages"][1]["role"], "user")
        self.assertEqual(result["messages"][1]["content"], "Hello")

    def test_empty_instructions_skipped(self):
        from transform import responses_to_chat
        body = {
            "model": "gpt-4o",
            "instructions": "",
            "input": [{"type": "message", "role": "user", "content": "Hi"}],
        }
        model_cfg = {"target": "claude-sonnet-4-6", "multimodal": False}
        result = responses_to_chat(body, model_cfg)
        # No system message when instructions is empty
        self.assertEqual(len(result["messages"]), 1)
        self.assertEqual(result["messages"][0]["role"], "user")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test_transform.py::TestResponsesToChatBasic -v`
Expected: FAIL — `responses_to_chat` not defined

- [ ] **Step 3: 实现 responses_to_chat 基础映射**

```python
def responses_to_chat(body: dict, model_cfg: dict) -> dict:
    """Responses API → Chat Completions 请求转换。

    model_cfg: model_map 中命中的条目，如 {"target": "claude-sonnet-4-6", "multimodal": False}
    """
    messages = []

    # instructions → system message
    instructions = body.get("instructions", "")
    if instructions:
        messages.append({"role": "system", "content": instructions})

    # input → messages
    for item in body.get("input", []):
        msg = _map_input_item(item, model_cfg)
        if msg is not None:
            messages.extend(msg)

    # 基础字段映射
    chat = {
        "model": model_cfg["target"],
        "messages": messages,
    }

    if "max_output_tokens" in body:
        chat["max_tokens"] = body["max_output_tokens"]

    # 透传字段
    for key in ("tools", "tool_choice", "parallel_tool_calls", "stream"):
        if key in body:
            chat[key] = body[key]

    # reasoning.effort 透传
    reasoning = body.get("reasoning", {})
    if reasoning and "effort" in reasoning:
        chat["reasoning"] = {"effort": reasoning["effort"]}

    # 结构化输出映射
    text_format = body.get("text", {}).get("format")
    if text_format:
        chat["response_format"] = _map_response_format(text_format)

    return chat


def _map_input_item(item: dict, model_cfg: dict) -> list:
    """将单个 input 条目映射为 Chat Completions messages。返回 list 因为某些类型可能展开为多条。"""
    item_type = item.get("type")

    if item_type == "message":
        return [_map_message(item, model_cfg)]
    elif item_type == "function_call":
        return [_map_function_call(item)]
    elif item_type == "function_call_output":
        return [_map_function_call_output(item)]
    elif item_type == "computer_call_output":
        return [_map_computer_call_output(item)]
    elif item_type == "reasoning":
        # 丢弃
        return []
    elif item_type in ("web_search_call", "code_interpreter_call", "mcp_call"):
        logger.warning(f"[transform] 丢弃不支持的 input 类型: {item_type}")
        return []
    else:
        logger.warning(f"[transform] 丢弃未知 input 类型: {item_type}")
        return []


def _map_message(item: dict, model_cfg: dict) -> dict:
    """映射 message 类型的 input 条目。"""
    role = item.get("role", "user")
    content = item.get("content")

    if isinstance(content, str):
        return {"role": role, "content": content}

    if isinstance(content, list):
        mapped = []
        for part in content:
            part_type = part.get("type")
            if part_type == "input_text":
                mapped.append({"type": "text", "text": part.get("text", "")})
            elif part_type == "input_image":
                mapped.append(_map_input_image(part, model_cfg))
            elif part_type == "input_file":
                mapped.append(_map_input_file(part))
            else:
                logger.warning(f"[transform] 丢弃不支持的 content 类型: {part_type}")
        return {"role": role, "content": mapped} if mapped else {"role": role, "content": ""}

    return {"role": role, "content": str(content) if content else ""}


def _map_input_image(part: dict, model_cfg: dict) -> dict:
    """映射 input_image，根据 multimodal 配置分支。"""
    if model_cfg.get("multimodal", False):
        image_url = part.get("image_url", "")
        detail = part.get("detail", "auto")
        return {
            "type": "image_url",
            "image_url": {"url": image_url, "detail": detail},
        }
    else:
        logger.warning("[transform] 模型不支持多模态，input_image 已替换为占位文本")
        return {"type": "text", "text": "[image: unsupported]"}


def _map_input_file(part: dict) -> dict:
    """映射 input_file 为占位文本。"""
    filename = part.get("filename", "unknown")
    logger.debug(f"[transform] 文件内容 {part.get('file_id', '?')} 无法转换，已替换为占位标记 [{filename}]")
    return {"type": "text", "text": f"[file: {filename}]"}


def _map_function_call(item: dict) -> dict:
    """映射 function_call → assistant + tool_calls。"""
    call_id = item.get("id", "")
    name = item.get("name", "")
    arguments = item.get("arguments", "")
    if isinstance(arguments, dict):
        arguments = json.dumps(arguments)
    return {
        "role": "assistant",
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": arguments},
        }],
    }


def _map_function_call_output(item: dict) -> dict:
    """映射 function_call_output → tool message。"""
    return {
        "role": "tool",
        "tool_call_id": item.get("tool_call_id", ""),
        "content": item.get("output", ""),
    }


def _map_computer_call_output(item: dict) -> dict:
    """映射 computer_call_output → tool message。"""
    return {
        "role": "tool",
        "tool_call_id": item.get("tool_call_id", ""),
        "content": item.get("output", ""),
    }


def _map_response_format(text_format: dict) -> dict:
    """映射 text.format → response_format。"""
    fmt_type = text_format.get("type", "text")

    if fmt_type == "json_schema":
        return {
            "type": "json_schema",
            "json_schema": {
                "name": text_format.get("name", ""),
                "schema": text_format.get("schema", {}),
                "strict": text_format.get("strict", False),
            },
        }
    else:
        # text / json_object 直接映射
        return {"type": fmt_type}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test_transform.py::TestResponsesToChatBasic -v`
Expected: 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add transform.py test_transform.py
git commit -m "feat: implement responses_to_chat basic field mapping"
```

---

### Task 3: chat_to_responses — 非流式响应转换

**Files:**
- Modify: `transform.py` — 添加 `chat_to_responses()`
- Modify: `test_transform.py` — 添加响应转换测试

- [ ] **Step 1: 写出测试**

```python
class TestChatToResponses(unittest.TestCase):
    def test_basic_response(self):
        from transform import chat_to_responses
        chat_resp = {
            "id": "chatcmpl-abc123",
            "model": "claude-sonnet-4-6",
            "choices": [{
                "message": {"content": "Hello world"},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
        }
        result = chat_to_responses(chat_resp)

        self.assertTrue(result["id"].startswith("resp-"))
        self.assertEqual(result["model"], "claude-sonnet-4-6")
        self.assertEqual(result["status"], "completed")
        # output 数组
        self.assertEqual(len(result["output"]), 1)
        msg = result["output"][0]
        self.assertEqual(msg["type"], "message")
        self.assertEqual(msg["role"], "assistant")
        self.assertEqual(msg["content"][0]["type"], "output_text")
        self.assertEqual(msg["content"][0]["text"], "Hello world")
        self.assertEqual(msg["status"], "completed")
        # usage
        self.assertEqual(result["usage"]["input_tokens"], 100)
        self.assertEqual(result["usage"]["output_tokens"], 50)
        self.assertEqual(result["usage"]["total_tokens"], 150)

    def test_id_prefix_replacement(self):
        from transform import chat_to_responses
        chat_resp = {
            "id": "chatcmpl-xyz",
            "model": "test",
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        result = chat_to_responses(chat_resp)
        self.assertTrue(result["id"].startswith("resp-"))
        self.assertNotIn("chatcmpl", result["id"])

    def test_non_chatcmpl_id(self):
        from transform import chat_to_responses
        chat_resp = {
            "id": "some-other-id",
            "model": "test",
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        result = chat_to_responses(chat_resp)
        self.assertTrue(result["id"].startswith("resp-"))

    def test_incomplete_length(self):
        from transform import chat_to_responses
        chat_resp = {
            "id": "chatcmpl-trunc",
            "model": "test",
            "choices": [{"message": {"content": "hel"}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        result = chat_to_responses(chat_resp)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["incomplete_details"]["reason"], "max_tokens")

    def test_content_filter(self):
        from transform import chat_to_responses
        chat_resp = {
            "id": "chatcmpl-filter",
            "model": "test",
            "choices": [{"message": {"content": ""}, "finish_reason": "content_filter"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
        }
        result = chat_to_responses(chat_resp)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["incomplete_details"]["reason"], "content_filter")

    def test_tool_calls_in_response(self):
        from transform import chat_to_responses
        chat_resp = {
            "id": "chatcmpl-tools",
            "model": "test",
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{
                        "id": "call_abc",
                        "type": "function",
                        "function": {"name": "bash", "arguments": '{"cmd":"ls"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        result = chat_to_responses(chat_resp)
        self.assertEqual(result["status"], "completed")
        # 找到 function_call 类型的 output
        fc = [o for o in result["output"] if o["type"] == "function_call"]
        self.assertEqual(len(fc), 1)
        self.assertEqual(fc[0]["name"], "bash")
        self.assertEqual(fc[0]["arguments"], '{"cmd":"ls"}')

    def test_refusal(self):
        from transform import chat_to_responses
        chat_resp = {
            "id": "chatcmpl-refuse",
            "model": "test",
            "choices": [{
                "message": {"content": None, "refusal": "I cannot help with that."},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        result = chat_to_responses(chat_resp)
        refusal_items = [o for o in result["output"] if o.get("content") and o["content"][0].get("type") == "refusal"]
        self.assertEqual(len(refusal_items), 1)
        self.assertEqual(refusal_items[0]["content"][0]["refusal"], "I cannot help with that.")

    def test_usage_details_defaults(self):
        from transform import chat_to_responses
        chat_resp = {
            "id": "chatcmpl-abc",
            "model": "test",
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            # 没有 prompt_tokens_details / completion_tokens_details
        }
        result = chat_to_responses(chat_resp)
        self.assertEqual(result["usage"]["input_tokens_details"]["cached_tokens"], 0)
        self.assertEqual(result["usage"]["output_tokens_details"]["reasoning_tokens"], 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test_transform.py::TestChatToResponses -v`
Expected: FAIL — `chat_to_responses` not defined

- [ ] **Step 3: 实现 chat_to_responses**

```python
FINISH_REASON_MAP = {
    "stop": "completed",
    "length": "incomplete",
    "tool_calls": "completed",
    "content_filter": "incomplete",
}

INCOMPLETE_REASON_MAP = {
    "length": "max_tokens",
    "content_filter": "content_filter",
}


def chat_to_responses(response: dict) -> dict:
    """Chat Completions → Responses API 非流式响应转换。"""
    chat_id = response.get("id", "")
    if chat_id.startswith("chatcmpl-"):
        resp_id = "resp-" + chat_id[len("chatcmpl-"):]
    else:
        resp_id = f"resp-{uuid.uuid4().hex[:8]}"

    choice = response.get("choices", [{}])[0]
    message = choice.get("message", {})
    finish_reason = choice.get("finish_reason", "stop")

    output = []

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

    # 工具调用
    tool_calls = message.get("tool_calls", [])
    for tc in tool_calls:
        func = tc.get("function", {})
        output.append({
            "type": "function_call",
            "id": tc.get("id", ""),
            "call_id": tc.get("id", ""),
            "name": func.get("name", ""),
            "arguments": func.get("arguments", ""),
        })

    result = {
        "id": resp_id,
        "model": response.get("model", ""),
        "status": FINISH_REASON_MAP.get(finish_reason, "completed"),
        "output": output,
    }

    # incomplete_details
    if finish_reason in INCOMPLETE_REASON_MAP:
        result["incomplete_details"] = {
            "reason": INCOMPLETE_REASON_MAP[finish_reason],
        }

    # usage 映射
    usage = response.get("usage", {})
    result["usage"] = {
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
        "input_tokens_details": {
            "cached_tokens": usage.get("prompt_tokens_details", {}).get("cached_tokens", 0),
        },
        "output_tokens_details": {
            "reasoning_tokens": usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0),
        },
    }

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test_transform.py::TestChatToResponses -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add transform.py test_transform.py
git commit -m "feat: implement chat_to_responses non-streaming response conversion"
```

---

### Task 4: SSE 解析器 — iter_sse_events + _parse_sse_event

**Files:**
- Modify: `transform.py` — 添加 `_parse_sse_event` + `iter_sse_events`
- Modify: `test_transform.py` — 添加 SSE 解析测试

- [ ] **Step 1: 写出测试**

```python
class TestSSEParser(unittest.TestCase):
    def test_parse_simple_event(self):
        from transform import _parse_sse_event
        result = _parse_sse_event("event: response.created\ndata: {\"id\":\"resp-1\"}")
        self.assertEqual(result["event"], "response.created")
        self.assertEqual(result["data"]["id"], "resp-1")

    def test_parse_default_event(self):
        from transform import _parse_sse_event
        # 没有 event: 行，默认 message
        result = _parse_sse_event("data: {\"key\":\"value\"}")
        self.assertEqual(result["event"], "message")
        self.assertEqual(result["data"]["key"], "value")

    def test_parse_done(self):
        from transform import _parse_sse_event
        result = _parse_sse_event("data: [DONE]")
        self.assertEqual(result["event"], "[DONE]")
        self.assertIsNone(result["data"])

    def test_parse_empty_returns_none(self):
        from transform import _parse_sse_event
        self.assertIsNone(_parse_sse_event(""))
        self.assertIsNone(_parse_sse_event(": keepalive"))

    def test_parse_invalid_json_returns_none(self):
        from transform import _parse_sse_event
        self.assertIsNone(_parse_sse_event("data: not-json"))

    def test_parse_multiple_data_lines(self):
        from transform import _parse_sse_event
        # 多行 data: 应该用 \n 拼接
        result = _parse_sse_event("data: {\"a\":1,\n data: \"b\":2}")
        self.assertEqual(result["data"]["a"], 1)
        self.assertEqual(result["data"]["b"], 2)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test_transform.py::TestSSEParser -v`
Expected: FAIL — `_parse_sse_event` not defined

- [ ] **Step 3: 实现 _parse_sse_event**

```python
def _parse_sse_event(text: str) -> Optional[dict]:
    """解析单个 SSE 事件文本，返回 {event, data} 或 None。"""
    event_type = "message"
    data_lines = []
    for line in text.splitlines():
        if line.startswith("event: "):
            event_type = line[7:]
        elif line.startswith("data: "):
            data_lines.append(line[6:])
        # ": " 开头为 keepalive，跳过
    raw = "\n".join(data_lines)
    if not raw:
        return None
    if raw == "[DONE]":
        return {"event": "[DONE]", "data": None}
    try:
        return {"event": event_type, "data": json.loads(raw)}
    except json.JSONDecodeError:
        return None
```

- [ ] **Step 4: 测试 iter_sse_events（需要 mock 流）**

在 test_transform.py 中添加：

```python
class TestIterSSEEvents(unittest.TestCase):
    def test_iter_multiple_events(self):
        from transform import iter_sse_events

        class MockStream:
            def __init__(self, data):
                self.data = data
                self.pos = 0

            def read(self, size):
                chunk = self.data[self.pos:self.pos + size]
                self.pos += size
                return chunk

        raw = b'event: response.created\ndata: {"id":"resp-1"}\n\nevent: response.completed\ndata: {"id":"resp-1"}\n\n'
        stream = MockStream(raw)
        events = list(iter_sse_events(stream))
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event"], "response.created")
        self.assertEqual(events[1]["event"], "response.completed")

    def test_iter_with_done(self):
        from transform import iter_sse_events

        class MockStream:
            def __init__(self, data):
                self.data = data
                self.pos = 0

            def read(self, size):
                chunk = self.data[self.pos:self.pos + size]
                self.pos += size
                return chunk

        raw = b'event: response.created\ndata: {"id":"resp-1"}\n\ndata: [DONE]\n\n'
        stream = MockStream(raw)
        events = list(iter_sse_events(stream))
        self.assertEqual(events[-1]["event"], "[DONE]")
```

- [ ] **Step 5: 实现 iter_sse_events**

```python
def iter_sse_events(upstream_response):
    """逐 chunk 读取 HTTP 响应流，yield 解析后的 SSE 事件。

    upstream_response: 有 read(size) 方法的对象（http.client.HTTPResponse）
    """
    buf = b""
    while True:
        # SSE buffer size: 256 is tunable. Risk #2: smaller = less latency but more read calls. Start at 256; if latency observed, try 128 or 64.
        chunk = upstream_response.read(256)
        if not chunk:
            break
        buf += chunk
        while b"\n\n" in buf:
            raw, buf = buf.split(b"\n\n", 1)
            event = _parse_sse_event(raw.decode("utf-8", errors="replace"))
            if event:
                yield event
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m pytest test_transform.py -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add transform.py test_transform.py
git commit -m "feat: add SSE parser (iter_sse_events + _parse_sse_event) with tests"
```

---

### Task 5: StreamState + SSE 流转换 create_codex_sse_stream

**Files:**
- Modify: `transform.py` — 添加 `StreamState` + `create_codex_sse_stream()` + `_emit_completion_events()` + `_process_delta()` + `_emit_created()`
- Modify: `test_transform.py` — 添加 SSE 流转换测试

- [ ] **Step 1: 写出测试**

```python
class TestStreamState(unittest.TestCase):
    def test_message_output_index_no_reasoning(self):
        from transform import StreamState
        state = StreamState()
        state.has_text = True
        self.assertEqual(state.message_output_index, 0)

    def test_message_output_index_with_reasoning(self):
        from transform import StreamState
        state = StreamState()
        state.has_reasoning = True
        state.has_text = True
        self.assertEqual(state.message_output_index, 1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test_transform.py::TestStreamState -v`
Expected: FAIL — `StreamState` not defined

- [ ] **Step 3: 实现 StreamState**

```python
@dataclass
class StreamState:
    response_id: str = ""
    model: str = ""
    reasoning_id: str = ""
    # 推理 item
    reasoning_buffer: str = ""
    has_reasoning: bool = False
    reasoning_item_announced: bool = False
    # 文本 message item
    text_buffer: str = ""
    has_text: bool = False
    message_item_announced: bool = False
    # 工具调用积累
    tool_calls: dict = field(default_factory=dict)
    # 完成状态
    finish_reason: str = ""
    usage: dict = field(default_factory=dict)
    # 完整 output 数组
    output_items: list = field(default_factory=list)
    created_sent: bool = False

    @property
    def message_output_index(self) -> int:
        return 1 if self.has_reasoning else 0
```

- [ ] **Step 4: 实现 create_codex_sse_stream + _emit_created + _process_delta + _emit_completion**

```python
def create_codex_sse_stream(upstream_response):
    """读取上游 SSE 流，逐事件 yield Responses API 格式的 SSE 字符串。

    upstream_response: http.client.HTTPResponse
    """
    state = StreamState()
    state.response_id = generate_response_id()

    for event in iter_sse_events(upstream_response):
        if event["event"] == "[DONE]":
            break

        data = event.get("data", {})
        if not data:
            continue

        # 捕获 model
        if not state.model:
            state.model = data.get("model", "")

        # 捕获 usage
        if "usage" in data and data["usage"]:
            state.usage = data["usage"]

        # 捕获 finish_reason
        choices = data.get("choices", [])
        if choices:
            choice = choices[0]
            if choice.get("finish_reason"):
                state.finish_reason = choice["finish_reason"]

            delta = choice.get("delta", {})
            if delta:
                for event_str in _process_delta(delta, state):
                    yield event_str

    # 所有 chunk 读完，发送 completion
    if state.created_sent:
        for event_str in _emit_completion(state):
            yield event_str


def _emit_created(state: StreamState) -> list:
    """阶段 1: response.created + response.metadata。

    设计意图：暂不发送 output_item.added，因为不知道第一个内容是推理还是文本。
    """
    events = []
    created = {
        "id": state.response_id,
        "object": "response",
        "model": state.model,
        "status": "in_progress",
        "output": [],
    }
    events.append(("response.created", created))
    metadata = {
        "model": state.model,
        "previous_response_id": None,
    }
    events.append(("response.metadata", metadata))
    state.created_sent = True
    return events


def _process_delta(delta: dict, state: StreamState) -> list:
    """处理单个 Chat Completions delta，返回 SSE 事件字符串列表。"""
    events = []

    # 首次：发送 created + metadata
    if not state.created_sent:
        for etype, edata in _emit_created(state):
            events.append(f"event: {etype}\ndata: {json.dumps(edata, ensure_ascii=False)}\n\n")

    # 推理 delta（检测顺序：reasoning_content → thinking → reasoning）
    for key in ("reasoning_content", "thinking", "reasoning"):
        if delta.get(key):
            reasoning_text = delta[key]
            if not state.has_reasoning:
                state.has_reasoning = True
                state.reasoning_id = f"rs_{uuid.uuid4().hex[:8]}"
                # output_item.added for reasoning
                events.append(
                    f'event: response.output_item.added\n'
                    f'data: {{"output_index":0,"item":{{"type":"reasoning",'
                    f'"id":"{state.reasoning_id}","summary":[],"status":"in_progress"}}}}\n\n'
                )
                state.reasoning_item_announced = True
            # reasoning delta
            state.reasoning_buffer += reasoning_text
            events.append(
                f'event: response.reasoning_summary_text.delta\n'
                f'data: {{"output_index":0,"summary_index":0,"delta":{json.dumps(reasoning_text, ensure_ascii=False)}}}\n\n'
            )
            break  # 只处理第一个命中的推理字段

    # 文本 delta
    content = delta.get("content", "")
    if content:
        if not state.message_item_announced:
            idx = state.message_output_index
            events.append(
                f'event: response.output_item.added\n'
                f'data: {{"output_index":{idx},"item":{{"type":"message","role":"assistant",'
                f'"content":[],"status":"in_progress"}}}}\n\n'
            )
            state.message_item_announced = True
        state.text_buffer += content
        idx = state.message_output_index
        events.append(
            f'event: response.output_text.delta\n'
            f'data: {{"output_index":{idx},"content_index":0,"delta":{json.dumps(content, ensure_ascii=False)}}}\n\n'
        )

    # 工具调用 delta（积累，不发事件）
    tool_calls = delta.get("tool_calls")
    if tool_calls:
        for tc in tool_calls:
            idx = tc.get("index", 0)
            if idx not in state.tool_calls:
                state.tool_calls[idx] = {
                    "id": tc.get("id", ""),
                    "name": tc.get("function", {}).get("name", ""),
                    "arguments_buffer": "",
                }
            func_args = tc.get("function", {}).get("arguments", "")
            if func_args:
                state.tool_calls[idx]["arguments_buffer"] += func_args

    return events


def _emit_completion(state: StreamState) -> list:
    """阶段 5-6: 完成时发送 reasoning done, text done, tool calls, incomplete, completed。"""
    events = []

    # 推理完成
    if state.has_reasoning:
        events.append(
            f'event: response.reasoning_summary_text.done\n'
            f'data: {{"output_index":0,"summary_index":0,"text":{json.dumps(state.reasoning_buffer, ensure_ascii=False)}}}\n\n'
        )
        reasoning_item = {
            "type": "reasoning",
            "id": state.reasoning_id,
            "summary": [{"type": "summary_text", "text": state.reasoning_buffer}],
            "status": "completed",
        }
        events.append(
            f'event: response.output_item.done\n'
            f'data: {{"output_index":0,"item":{json.dumps(reasoning_item, ensure_ascii=False)}}}\n\n'
        )
        state.output_items.append(reasoning_item)

    # 文本完成
    if state.has_text:
        idx = state.message_output_index
        events.append(
            f'event: response.output_text.done\n'
            f'data: {{"output_index":{idx},"content_index":0,"text":{json.dumps(state.text_buffer, ensure_ascii=False)}}}\n\n'
        )
        message_item = {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": state.text_buffer}],
            "status": "completed",
        }
        events.append(
            f'event: response.output_item.done\n'
            f'data: {{"output_index":{idx},"item":{json.dumps(message_item, ensure_ascii=False)}}}\n\n'
        )
        state.output_items.append(message_item)

    # 工具调用完成（按 index 排序）
    sorted_tc = sorted(state.tool_calls.items(), key=lambda x: x[0])
    for i, (idx, tc) in enumerate(sorted_tc):
        tc_item = {
            "type": "function_call",
            "id": tc["id"] or f"call_{uuid.uuid4().hex[:8]}",
            "call_id": tc["id"] or f"call_{uuid.uuid4().hex[:8]}",
            "name": tc["name"],
            "arguments": tc["arguments_buffer"],
        }
        output_idx = state.message_output_index + i + 1 if state.has_text else i + (1 if state.has_reasoning else 0)
        events.append(
            f'event: response.output_item.done\n'
            f'data: {{"output_index":{output_idx},"item":{json.dumps(tc_item, ensure_ascii=False)}}}\n\n'
        )
        state.output_items.append(tc_item)

    # incomplete
    if state.finish_reason in INCOMPLETE_REASON_MAP:
        events.append(
            f'event: response.incomplete\n'
            f'data: {{"reason":{json.dumps(INCOMPLETE_REASON_MAP[state.finish_reason])}}}\n\n'
        )

    # completed
    usage = state.usage
    completed = {
        "id": state.response_id,
        "status": FINISH_REASON_MAP.get(state.finish_reason, "completed"),
        "output": state.output_items,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "input_tokens_details": {
                "cached_tokens": usage.get("prompt_tokens_details", {}).get("cached_tokens", 0),
            },
            "output_tokens_details": {
                "reasoning_tokens": usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0),
            },
        },
    }
    if state.finish_reason in INCOMPLETE_REASON_MAP:
        completed["incomplete_details"] = {"reason": INCOMPLETE_REASON_MAP[state.finish_reason]}
    events.append(
        f'event: response.completed\ndata: {json.dumps(completed, ensure_ascii=False)}\n\n'
    )

    return events
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest test_transform.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add transform.py test_transform.py
git commit -m "feat: implement StreamState + SSE stream conversion with tests"
```

---

### Task 6: proxy.py — 配置加载 + YAML 解析 + 日志系统 + ThreadedHTTPServer + 路由骨架

**Files:**
- Create: `proxy.py` — 完整 HTTP server
- Create: `proxy_config.yaml` — 配置示例

- [ ] **Step 1: 创建 proxy_config.yaml**

```yaml
proxy:
  host: "127.0.0.1"
  port: 48743
  log_level: "INFO"

upstream:
  base_url: "https://llm-open-api.cargoware.com/v1"
  api_key: "sk-xxx"
  timeout: 120
  connect_timeout: 10
  ssl_verify: true
  retry: 1

model_map:
  "codex-mini-latest":
    target: "claude-sonnet-4-6"
    multimodal: false
  "o4-mini":
    target: "claude-sonnet-4-6"
    multimodal: false
  "gpt-4o":
    target: "claude-sonnet-4-6"
    multimodal: false
  "*":
    target: "claude-sonnet-4-6"
    multimodal: false
```

- [ ] **Step 2: 创建 proxy.py — 全部代码**

```python
#!/usr/bin/env python3
"""Codex Responses API → Chat Completions 转换代理。

纯 Python 标准库，零外部依赖。
"""

import os
import sys
import json
import time
import ssl
import gzip
import uuid
import socket
import logging
import http.client
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from pathlib import Path

from transform import (
    generate_response_id,
    responses_to_chat,
    chat_to_responses,
    create_codex_sse_stream,
)

# ─── 最小 YAML 解析器（仅支持 3 层嵌套，标量值）───────────────────────────

def _parse_yaml(text: str) -> dict:
    """极简 YAML 解析器，仅支持本项目 proxy_config.yaml 的结构。
    嵌套 dict 最多 3 层，值为 str/int/float/bool。
    """
    result = {}
    stack = [(result, -1)]  # (current_dict, indent_level)

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())

        # 弹出比当前 indent 深的栈帧
        while len(stack) > 1 and stack[-1][1] >= indent:
            stack.pop()

        current_dict = stack[-1][0]

        if ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip().strip('"').strip("'")
            val = val.strip()

            if val == "" or val.startswith("#"):
                # 嵌套 dict
                new_dict = {}
                current_dict[key] = new_dict
                stack.append((new_dict, indent))
            elif val.startswith("[") and val.endswith("]"):
                # 内联列表
                items = [
                    _yaml_scalar(item.strip())
                    for item in val[1:-1].split(",")
                    if item.strip()
                ]
                current_dict[key] = items
            else:
                current_dict[key] = _yaml_scalar(val)

    return result


def _yaml_scalar(val: str):
    """将 YAML 标量值转为 Python 类型。"""
    if not val:
        return ""
    # 去除注释
    if " #" in val:
        val = val[: val.index(" #")].strip()
    # 去除引号
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        return val[1:-1]
    # bool
    if val.lower() in ("true", "yes"):
        return True
    if val.lower() in ("false", "no"):
        return False
    # int
    try:
        return int(val)
    except ValueError:
        pass
    # float
    try:
        return float(val)
    except ValueError:
        pass
    return val


# ─── 配置加载 ───────────────────────────────────────────────────────

CONFIG = {}
CONFIG_PATH = Path(__file__).parent / "proxy_config.yaml"


def load_config(config_path: Path = None):
    """加载 proxy_config.yaml，校验后写入全局 CONFIG。

    config_path: 可选，覆盖默认配置文件路径（用于测试）。

    校验规则：
    - 必须包含 model_map
    - model_map 必须包含 "*" fallback 键，否则 sys.exit(1) 打印明确错误
    """
    global CONFIG
    path = config_path or CONFIG_PATH
    if not path.exists():
        print(f"FATAL: 配置文件不存在: {path}", file=sys.stderr)
        sys.exit(1)

    with open(path, "r") as f:
        CONFIG = _parse_yaml(f.read())

    # 校验 model_map 存在
    if "model_map" not in CONFIG:
        print("FATAL: 配置文件缺少 model_map", file=sys.stderr)
        sys.exit(1)

    # 校验 "*" fallback 键 — 启动时必须存在
    if "*" not in CONFIG["model_map"]:
        print('FATAL: model_map 必须包含 "*" fallback 键', file=sys.stderr)
        sys.exit(1)

    # 设置日志：同时写 proxy.log 文件和 stdout，遵循 log_level 配置
    # Fix #5: guard around basicConfig to prevent side effects on repeated calls
    if not logging.root.handlers:
        log_level = CONFIG.get("proxy", {}).get("log_level", "INFO")
        numeric_level = getattr(logging, log_level.upper(), logging.INFO)

        log_file = Path(__file__).parent / "proxy.log"
        file_handler = logging.FileHandler(log_file)
        stream_handler = logging.StreamHandler(sys.stdout)

        logging.basicConfig(
            level=numeric_level,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[file_handler, stream_handler],
        )


def resolve_model(model_name: str) -> dict:
    """根据 model_name 从 model_map 查找配置，支持 * fallback。"""
    model_map = CONFIG.get("model_map", {})
    if model_name in model_map:
        return model_map[model_name]
    return model_map.get("*", {"target": model_name, "multimodal": False})


# ─── ThreadedHTTPServer ────────────────────────────────────────────

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


# ─── 日志轮转 ──────────────────────────────────────────────────────

def rotate_log_if_needed():
    """启动时检查 proxy.log 大小，超过 101 MB 则轮转。

    具体实现：
    1. 检查 proxy.log 是否存在，不存在直接返回
    2. 获取文件大小，<= 101 MB 直接返回
    3. 重命名当前文件为 proxy.log.YYYYMMDD.gz（gzip 压缩）
    4. 新建空日志文件（由后续 logging.FileHandler 创建）

    注意：仅在启动时触发一次，运行中不轮转。
    注意：此函数必须在 load_config() 之后调用，因为 logging 需要先配置好
    （否则 log_file 不存在时 print 不会输出到文件）。
    """
    log_file = Path(__file__).parent / "proxy.log"
    if not log_file.exists():
        return
    size = log_file.stat().st_size
    if size <= 101 * 1024 * 1024:  # 101 MB
        return

    timestamp = time.strftime("%Y%m%d")
    gz_path = Path(str(log_file) + f".{timestamp}.gz")
    # 此时 logging 已初始化
    logging.info(f"日志轮转: {log_file} → {gz_path}")
    with open(log_file, "rb") as f_in:
        with gzip.open(gz_path, "wb") as f_out:
            f_out.write(f_in.read())
    log_file.unlink()


# ─── 请求 Handler ──────────────────────────────────────────────────

class ProxyHandler(BaseHTTPRequestHandler):
    """处理所有 HTTP 请求。"""

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, {"status": "ok", "pid": os.getpid()})
        elif self.path == "/v1/models":
            self._handle_models()
        elif self.path == "/v1/responses":
            # GET /v1/responses → 426 Upgrade Required
            # 设计意图：触发 Codex 回退到 HTTP POST + SSE 模式
            # （Codex 默认尝试 WebSocket，426 告知其回退到纯 HTTP SSE）
            self.send_response(426)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Upgrade Required: Use HTTP POST with SSE")
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path in ("/v1/responses", "/v1/responses/compact"):
            self._handle_responses()
        else:
            self._send_json(404, {"error": "not found"})

    def _handle_models(self):
        """返回 model_map 中所有非 * 的 key。"""
        model_map = CONFIG.get("model_map", {})
        models = [k for k in model_map if k != "*"]
        self._send_json(200, {"data": [{"id": m, "object": "model"} for m in models]})

    def _handle_responses(self):
        """核心：Responses → Chat → Responses 转换。"""
        # 读取请求体
        content_length = int(self.headers.get("Content-Length", 0))
        body_raw = self.rfile.read(content_length)
        try:
            body = json.loads(body_raw)
        except json.JSONDecodeError as e:
            logging.error(f"JSON 解析失败: {e}")
            self._send_json(400, {"error": {"type": "invalid_request_error", "message": str(e)}})
            return

        model_name = body.get("model", "*")
        model_cfg = resolve_model(model_name)
        is_stream = body.get("stream", False)

        logging.info(f"请求: model={model_name}, stream={is_stream}, target={model_cfg['target']}")

        # 转换请求体
        try:
            chat_body = responses_to_chat(body, model_cfg)
        except Exception as e:
            logging.exception("responses_to_chat 转换失败")
            self._send_json(500, {"error": {"type": "internal_error", "message": str(e)}})
            return

        # 转发到上游
        if is_stream:
            self._forward_streaming(chat_body, model_cfg)
        else:
            self._forward_non_streaming(chat_body)

    def _forward_non_streaming(self, chat_body: dict):
        """非流式：转发到上游，转换响应，返回。

        超时处理：
        - connect_timeout: 连接超时（独立设置）
        - timeout: 读超时（总超时，包含 connect + read）
        实现方式：先用 connect_timeout 建立连接，再设 socket timeout 为总超时
        """
        upstream_cfg = CONFIG.get("upstream", {})
        base_url = upstream_cfg["base_url"]
        api_key = upstream_cfg["api_key"]
        timeout = upstream_cfg.get("timeout", 120)
        connect_timeout = upstream_cfg.get("connect_timeout", 10)
        retries = upstream_cfg.get("retry", 1) + 1

        parsed = urllib.parse.urlparse(base_url)
        path = parsed.path.rstrip("/") + "/chat/completions"
        ssl_ctx = ssl.create_default_context() if upstream_cfg.get("ssl_verify", True) else ssl._create_unverified_context()

        for attempt in range(retries):
            conn = None
            try:
                # 先用 connect_timeout 建立连接
                conn = http.client.HTTPSConnection(
                    parsed.hostname,
                    parsed.port or 443,
                    timeout=connect_timeout,
                    context=ssl_ctx,
                )
                conn.request("POST", path, body=json.dumps(chat_body), headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                })

                # 连接成功后设 read timeout（总超时 - 已用时）
                resp = conn.getresponse()
                # 设置 socket read timeout
                if conn.sock:
                    conn.sock.settimeout(timeout)
                resp_body = resp.read()
                conn.close()
                conn = None

                if resp.status >= 500 and attempt < retries - 1:
                    logging.warning(f"上游 {resp.status}，重试 {attempt + 1}/{retries}")
                    continue

                if resp.status != 200:
                    self.send_response(resp.status)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(resp_body)
                    return

                # 转换响应
                chat_response = json.loads(resp_body)
                responses_response = chat_to_responses(chat_response)
                self._send_json(200, responses_response)
                return

            except (socket.timeout, http.client.HTTPException, OSError) as e:
                logging.warning(f"上游请求失败 (attempt {attempt + 1}): {e}")
                if attempt < retries - 1:
                    continue
                self._send_json(500, {"error": {"type": "server_error", "message": str(e)}})
                return
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

    def _forward_streaming(self, chat_body: dict, model_cfg: dict):
        """流式：直连上游 SSE，通过 create_codex_sse_stream 转换后逐事件返回。

        整合验证点：
        - create_codex_sse_stream 返回字符串 generator，每个字符串是完整的 SSE 事件
        - 必须逐事件 write + flush，不能 accumulate 后一次性发送
        - 异常时发送 response.failed SSE 事件
        """
        upstream_cfg = CONFIG.get("upstream", {})
        base_url = upstream_cfg["base_url"]
        api_key = upstream_cfg["api_key"]
        timeout = upstream_cfg.get("timeout", 120)
        connect_timeout = upstream_cfg.get("connect_timeout", 10)
        ssl_verify = upstream_cfg.get("ssl_verify", True)

        parsed = urllib.parse.urlparse(base_url)
        path = parsed.path.rstrip("/") + "/chat/completions"
        ssl_ctx = ssl.create_default_context() if ssl_verify else ssl._create_unverified_context()

        conn = http.client.HTTPSConnection(
            parsed.hostname, parsed.port or 443, timeout=connect_timeout, context=ssl_ctx,
        )
        conn.request("POST", path, body=json.dumps(chat_body), headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        })

        # 设置 Codex 响应头
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        try:
            resp = conn.getresponse()
            # 设置 read timeout
            if conn.sock:
                conn.sock.settimeout(timeout)

            # 验证上游 Content-Type，非 SSE 则包装为 response.failed
            ct = resp.getheader("Content-Type", "")
            if resp.status != 200:
                # 上游返回非 200（如 502），发 response.failed
                error_event = (
                    f'event: response.failed\n'
                    f'data: {{"type":"error","error":{{"type":"server_error",'
                    f'"message":"Upstream returned HTTP {resp.status}"}}}}\n\n'
                )
                self.wfile.write(error_event.encode("utf-8"))
                self.wfile.flush()
                return

            if "text/event-stream" not in ct:
                logging.warning(f"上游返回非 SSE Content-Type: {ct}")
                error_event = (
                    f'event: response.failed\n'
                    f'data: {{"type":"error","error":{{"type":"server_error",'
                    f'"message":"Upstream returned non-SSE Content-Type: {ct}"}}}}\n\n'
                )
                self.wfile.write(error_event.encode("utf-8"))
                self.wfile.flush()
                return

            # 核心：通过 create_codex_sse_stream 逐事件转换并发送
            for sse_event in create_codex_sse_stream(resp):
                self.wfile.write(sse_event.encode("utf-8"))
                self.wfile.flush()

        except Exception as e:
            logging.exception("流式转发异常")
            try:
                error_event = (
                    f'event: response.failed\n'
                    f'data: {{"type":"error","error":{{"type":"server_error",'
                    f'"message":{json.dumps(str(e))}}}}}\n\n'
                )
                self.wfile.write(error_event.encode("utf-8"))
                self.wfile.flush()
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _send_json(self, status_code: int, data: dict):
        """发送 JSON 响应。"""
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        """重定向到 logger。"""
        logging.info("%s - - [%s] %s" % (self.client_address[0], self.log_date_time_string(), format % args))


# ─── 主入口 ────────────────────────────────────────────────────────

def main():
    load_config()
    rotate_log_if_needed()

    proxy_cfg = CONFIG.get("proxy", {})
    host = proxy_cfg.get("host", "127.0.0.1")
    port = proxy_cfg.get("port", 48743)

    server = ThreadedHTTPServer((host, port), ProxyHandler)
    logging.info(f"Codex Proxy 启动: http://{host}:{port}")

    # PID 文件
    pid_file = Path(__file__).parent / ".proxy.pid"
    pid_file.write_text(str(os.getpid()))

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("收到中断信号，关闭服务")
        server.shutdown()
        pid_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 冒烟测试 proxy.py**

Run: `python3 proxy.py &` (后台启动)
Then: `curl http://127.0.0.1:48743/health`
Expected: `{"status":"ok","pid":...}`
Then: `kill $(cat .proxy.pid)`

- [ ] **Step 4: Commit**

```bash
git add proxy.py proxy_config.yaml
git commit -m "feat: add proxy.py HTTP server with config, routing, and upstream forwarding"
```

---

### Task 7: 扩展 server.sh 管理双进程

**Files:**
- Modify: `server.sh` — 添加 proxy start/stop/status

- [ ] **Step 1: 完整重写 server.sh**

```bash
#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PIDFILE="$SCRIPT_DIR/.server.pid"
PROXY_PIDFILE="$SCRIPT_DIR/.proxy.pid"

start_data_browser() {
  if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
      echo "Hermes Data Browser 已经在运行 (PID $PID)，访问 http://127.0.0.1:18742"
      return
    fi
    rm -f "$PIDFILE"
  fi
  cd "$SCRIPT_DIR"
  nohup python server.py > /dev/null 2>&1 &
  echo $! > "$PIDFILE"
  echo "Hermes Data Browser 已启动 (PID $!)，访问 http://127.0.0.1:18742"
}

stop_data_browser() {
  if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if kill "$PID" 2>/dev/null; then
      rm -f "$PIDFILE"
      echo "Hermes Data Browser 已停止 (PID $PID)"
      return
    fi
    rm -f "$PIDFILE"
  fi
  PID2=$(lsof -ti:18742 2>/dev/null)
  if [ -n "$PID2" ]; then
    kill "$PID2" && echo "Hermes Data Browser 已停止 (端口 18742)" || echo "停止失败"
    return
  fi
  echo "Hermes Data Browser 未运行"
}

status_data_browser() {
  if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
      echo "Hermes Data Browser 运行中 PID=$PID"
      return
    fi
  fi
  PID2=$(lsof -ti:18742 2>/dev/null)
  if [ -n "$PID2" ]; then
    echo "Hermes Data Browser 运行中 PID=$PID2 (孤儿进程)"
    return
  fi
  echo "Hermes Data Browser 未运行"
}

start_proxy() {
  if [ -f "$PROXY_PIDFILE" ]; then
    PID=$(cat "$PROXY_PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
      echo "Codex Proxy 已经在运行 (PID $PID)，访问 http://127.0.0.1:48743"
      return
    fi
    rm -f "$PROXY_PIDFILE"
  fi
  cd "$SCRIPT_DIR"
  nohup python3 proxy.py > /dev/null 2>&1 &
  echo $! > "$PROXY_PIDFILE"
  echo "Codex Proxy 已启动 (PID $!)，访问 http://127.0.0.1:48743"
}

stop_proxy() {
  if [ -f "$PROXY_PIDFILE" ]; then
    PID=$(cat "$PROXY_PIDFILE")
    if kill "$PID" 2>/dev/null; then
      rm -f "$PROXY_PIDFILE"
      echo "Codex Proxy 已停止 (PID $PID)"
      return
    fi
    rm -f "$PROXY_PIDFILE"
  fi
  PID2=$(lsof -ti:48743 2>/dev/null)
  if [ -n "$PID2" ]; then
    kill "$PID2" && echo "Codex Proxy 已停止 (端口 48743)" || echo "停止失败"
    return
  fi
  echo "Codex Proxy 未运行"
}

status_proxy() {
  if [ -f "$PROXY_PIDFILE" ]; then
    PID=$(cat "$PROXY_PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
      echo "Codex Proxy 运行中 PID=$PID"
      return
    fi
  fi
  PID2=$(lsof -ti:48743 2>/dev/null)
  if [ -n "$PID2" ]; then
    echo "Codex Proxy 运行中 PID=$PID2 (孤儿进程)"
    return
  fi
  echo "Codex Proxy 未运行"
}

start() {
  start_data_browser
  start_proxy
}

stop() {
  stop_data_browser
  stop_proxy
}

status() {
  status_data_browser
  status_proxy
}

case "${1:-start}" in
  start)  start ;;
  stop)   stop ;;
  status) status ;;
  *)      echo "用法: $0 {start|stop|status}" ;;
esac
```

- [ ] **Step 2: Commit**

```bash
git add server.sh
git commit -m "feat: extend server.sh to manage both data browser and codex proxy"
```

---

### Task 8: 单元边界测试 — 扩展 input 类型 + 错误处理 + 重试

**Files:**
- Modify: `test_transform.py` — 添加所有边界场景测试

- [ ] **Step 1: 添加完整 input 类型测试**

```python
class TestResponsesToChatAllInputTypes(unittest.TestCase):
    def test_image_multimodal_true(self):
        from transform import responses_to_chat
        body = {
            "model": "gpt-4o",
            "input": [{
                "type": "message",
                "role": "user",
                "content": [{
                    "type": "input_image",
                    "image_url": "https://example.com/img.png",
                    "detail": "high",
                }],
            }],
        }
        model_cfg = {"target": "claude-sonnet-4-6", "multimodal": True}
        result = responses_to_chat(body, model_cfg)
        content = result["messages"][0]["content"]
        self.assertEqual(content[0]["type"], "image_url")
        self.assertEqual(content[0]["image_url"]["url"], "https://example.com/img.png")
        self.assertEqual(content[0]["image_url"]["detail"], "high")

    def test_image_multimodal_false(self):
        from transform import responses_to_chat
        body = {
            "model": "gpt-4o",
            "input": [{
                "type": "message",
                "role": "user",
                "content": [{"type": "input_image", "image_url": "https://example.com/img.png"}],
            }],
        }
        model_cfg = {"target": "claude-sonnet-4-6", "multimodal": False}
        result = responses_to_chat(body, model_cfg)
        content = result["messages"][0]["content"]
        self.assertEqual(content[0]["text"], "[image: unsupported]")

    def test_file_placeholder(self):
        from transform import responses_to_chat
        body = {
            "model": "gpt-4o",
            "input": [{
                "type": "message",
                "role": "user",
                "content": [{"type": "input_file", "file_id": "file-123", "filename": "doc.pdf"}],
            }],
        }
        model_cfg = {"target": "claude-sonnet-4-6", "multimodal": False}
        result = responses_to_chat(body, model_cfg)
        content = result["messages"][0]["content"]
        self.assertEqual(content[0]["text"], "[file: doc.pdf]")

    def test_reasoning_dropped(self):
        from transform import responses_to_chat
        body = {
            "model": "gpt-4o",
            "input": [{"type": "reasoning", "id": "rs_123"}],
        }
        model_cfg = {"target": "claude-sonnet-4-6", "multimodal": False}
        result = responses_to_chat(body, model_cfg)
        self.assertEqual(len(result["messages"]), 0)

    def test_web_search_call_dropped(self):
        from transform import responses_to_chat
        body = {
            "model": "gpt-4o",
            "input": [{"type": "web_search_call"}],
        }
        model_cfg = {"target": "claude-sonnet-4-6", "multimodal": False}
        result = responses_to_chat(body, model_cfg)
        self.assertEqual(len(result["messages"]), 0)

    def test_code_interpreter_call_dropped(self):
        from transform import responses_to_chat
        body = {
            "model": "gpt-4o",
            "input": [{"type": "code_interpreter_call"}],
        }
        model_cfg = {"target": "claude-sonnet-4-6", "multimodal": False}
        result = responses_to_chat(body, model_cfg)
        self.assertEqual(len(result["messages"]), 0)

    def test_mcp_call_dropped(self):
        from transform import responses_to_chat
        body = {
            "model": "gpt-4o",
            "input": [{"type": "mcp_call"}],
        }
        model_cfg = {"target": "claude-sonnet-4-6", "multimodal": False}
        result = responses_to_chat(body, model_cfg)
        self.assertEqual(len(result["messages"]), 0)
```

- [ ] **Step 2: 添加高级特性测试**

```python
class TestAdvancedFeatures(unittest.TestCase):
    def test_json_schema_format(self):
        from transform import responses_to_chat
        body = {
            "model": "gpt-4o",
            "input": [{"type": "message", "role": "user", "content": "extract"}],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "Person",
                    "schema": {"type": "object", "properties": {"name": {"type": "string"}}},
                    "strict": True,
                }
            },
        }
        model_cfg = {"target": "claude-sonnet-4-6", "multimodal": False}
        result = responses_to_chat(body, model_cfg)
        fmt = result["response_format"]
        self.assertEqual(fmt["type"], "json_schema")
        self.assertEqual(fmt["json_schema"]["name"], "Person")
        self.assertTrue(fmt["json_schema"]["strict"])

    def test_tools_passthrough(self):
        from transform import responses_to_chat
        body = {
            "model": "gpt-4o",
            "input": [{"type": "message", "role": "user", "content": "search"}],
            "tools": [{"type": "function", "function": {"name": "bash", "parameters": {}}}],
            "tool_choice": "required",
            "parallel_tool_calls": True,
        }
        model_cfg = {"target": "claude-sonnet-4-6", "multimodal": False}
        result = responses_to_chat(body, model_cfg)
        self.assertEqual(len(result["tools"]), 1)
        self.assertEqual(result["tool_choice"], "required")
        self.assertTrue(result["parallel_tool_calls"])

    def test_reasoning_effort_passthrough(self):
        from transform import responses_to_chat
        body = {
            "model": "gpt-4o",
            "input": [{"type": "message", "role": "user", "content": "think"}],
            "reasoning": {"effort": "high"},
        }
        model_cfg = {"target": "claude-sonnet-4-6", "multimodal": False}
        result = responses_to_chat(body, model_cfg)
        self.assertEqual(result["reasoning"]["effort"], "high")

    def test_discarded_fields(self):
        """验证 previous_response_id, include, store, client_metadata, service_tier 全部丢弃。"""
        from transform import responses_to_chat
        body = {
            "model": "gpt-4o",
            "input": [{"type": "message", "role": "user", "content": "hi"}],
            "previous_response_id": "resp-prev",
            "include": ["reasoning"],
            "store": True,
            "client_metadata": {"key": "val"},
            "service_tier": "default",
            "text": {},
        }
        model_cfg = {"target": "claude-sonnet-4-6", "multimodal": False}
        result = responses_to_chat(body, model_cfg)
        self.assertNotIn("previous_response_id", result)
        self.assertNotIn("include", result)
        self.assertNotIn("store", result)
        self.assertNotIn("client_metadata", result)
        self.assertNotIn("service_tier", result)

    def test_function_call_output_to_tool_role(self):
        """验证 function_call_output 正确转换为 role: "tool" + tool_call_id。"""
        from transform import responses_to_chat
        body = {
            "model": "gpt-4o",
            "input": [{
                "type": "function_call_output",
                "tool_call_id": "call_abc",
                "output": "result data",
            }],
        }
        model_cfg = {"target": "claude-sonnet-4-6", "multimodal": False}
        result = responses_to_chat(body, model_cfg)
        self.assertEqual(len(result["messages"]), 1)
        self.assertEqual(result["messages"][0]["role"], "tool")
        self.assertEqual(result["messages"][0]["tool_call_id"], "call_abc")
        self.assertEqual(result["messages"][0]["content"], "result data")

    def test_computer_call_output_to_tool_role(self):
        """验证 computer_call_output 正确转换为 role: "tool"。"""
        from transform import responses_to_chat
        body = {
            "model": "gpt-4o",
            "input": [{
                "type": "computer_call_output",
                "tool_call_id": "call_xyz",
                "output": '{"screenshot": "base64..."}',
            }],
        }
        model_cfg = {"target": "claude-sonnet-4-6", "multimodal": False}
        result = responses_to_chat(body, model_cfg)
        self.assertEqual(len(result["messages"]), 1)
        self.assertEqual(result["messages"][0]["role"], "tool")
        self.assertEqual(result["messages"][0]["content"], '{"screenshot": "base64..."}')

    def test_tool_name_namespace_preserved(self):
        """验证 tool name 中的 . 命名空间保留。"""
        from transform import responses_to_chat
        body = {
            "model": "gpt-4o",
            "input": [{
                "type": "function_call",
                "id": "call_mcp",
                "name": "mcp.server__fetch",
                "arguments": '{"url": "https://example.com"}',
            }],
        }
        model_cfg = {"target": "claude-sonnet-4-6", "multimodal": False}
        result = responses_to_chat(body, model_cfg)
        self.assertEqual(result["messages"][0]["tool_calls"][0]["function"]["name"], "mcp.server__fetch")
```

- [ ] **Step 3: 添加 SSE 流转换集成测试（验证 StreamState 积累→批量发送机制）**

```python
class TestSSEStreamIntegration(unittest.TestCase):
    """使用 mock 上游 SSE 数据，验证 create_codex_sse_stream 的完整事件序列。"""

    def test_text_only_stream(self):
        """纯文本流：created + metadata + output_item.added(message) + text deltas + text done + item done + completed。"""
        from transform import create_codex_sse_stream

        class MockStream:
            def __init__(self):
                chunks = [
                    b'event: message\ndata: {"id":"chatcmpl-1","model":"test","choices":[{"delta":{"content":"Hello"},"index":0}]}\n\n',
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

        stream = MockStream()
        events_text = ""
        for event in create_codex_sse_stream(stream):
            events_text += event

        # 验证关键事件存在
        self.assertIn("event: response.created", events_text)
        self.assertIn("event: response.metadata", events_text)
        self.assertIn("event: response.output_item.added", events_text)
        self.assertIn("event: response.output_text.delta", events_text)
        self.assertIn("event: response.output_text.done", events_text)
        self.assertIn("event: response.output_item.done", events_text)
        self.assertIn("event: response.completed", events_text)
        # 不应该有推理事件
        self.assertNotIn("response.reasoning_summary_text", events_text)

    def test_reasoning_plus_text_stream(self):
        """推理+文本流：验证 reasoning output_index=0, message output_index=1。"""
        from transform import create_codex_sse_stream

        class MockStream:
            def __init__(self):
                chunks = [
                    # 推理 delta
                    b'event: message\ndata: {"id":"chatcmpl-1","model":"test","choices":[{"delta":{"reasoning_content":"Let me think..."},"index":0}]}\n\n',
                    # 文本 delta
                    b'event: message\ndata: {"id":"chatcmpl-1","choices":[{"delta":{"content":"Answer"},"index":0}]}\n\n',
                    # 完成
                    b'event: message\ndata: {"id":"chatcmpl-1","choices":[{"delta":{},"index":0,"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}\n\n',
                    b'data: [DONE]\n\n',
                ]
                self.data = b"".join(chunks)
                self.pos = 0

            def read(self, size):
                chunk = self.data[self.pos:self.pos + size]
                self.pos += size
                return chunk

        stream = MockStream()
        events_text = ""
        for event in create_codex_sse_stream(stream):
            events_text += event

        self.assertIn("event: response.created", events_text)
        self.assertIn("event: response.metadata", events_text)
        self.assertIn("event: response.output_item.added", events_text)
        self.assertIn("event: response.reasoning_summary_text.delta", events_text)
        self.assertIn("event: response.reasoning_summary_text.done", events_text)
        self.assertIn("event: response.output_text.delta", events_text)
        self.assertIn("event: response.output_text.done", events_text)
        self.assertIn("event: response.completed", events_text)
        # 验证 reasoning 在 output_index=0
        self.assertIn('"output_index":0', events_text)
        # 验证 message 在 output_index=1
        self.assertIn('"output_index":1', events_text)

    def test_tool_calls_accumulation(self):
        """工具调用积累：验证 tool_calls 积累后一次性发送。"""
        from transform import create_codex_sse_stream

        class MockStream:
            def __init__(self):
                # 工具调用分多个 delta 到达
                chunks = [
                    b'event: message\ndata: {"id":"chatcmpl-1","model":"test","choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"bash","arguments":"{\\"cmd\\":"}}]},"index":0}]}\n\n',
                    b'event: message\ndata: {"id":"chatcmpl-1","choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"ls\\"}"}}]},"index":0}]}\n\n',
                    b'event: message\ndata: {"id":"chatcmpl-1","choices":[{"delta":{},"index":0,"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":5,"completion_tokens":3,"total_tokens":8}}\n\n',
                    b'data: [DONE]\n\n',
                ]
                self.data = b"".join(chunks)
                self.pos = 0

            def read(self, size):
                chunk = self.data[self.pos:self.pos + size]
                self.pos += size
                return chunk

        stream = MockStream()
        events_text = ""
        for event in create_codex_sse_stream(stream):
            events_text += event

        # 工具调用在完成时发送
        self.assertIn("event: response.output_item.done", events_text)
        self.assertIn('"type":"function_call"', events_text)
        self.assertIn('"name":"bash"', events_text)
        # 验证 arguments 被完整拼接
        self.assertIn('{"cmd":"ls"}', events_text)

    def test_multiple_tool_calls_out_of_order(self):
        """Risk #10：多 tool_calls 乱序到达，按 index 排序后发送。"""
        from transform import create_codex_sse_stream

        class MockStream:
            def __init__(self):
                # tool_calls 分两个 delta 到达，index 0 和 index 1
                chunks = [
                    b'event: message\ndata: {"id":"chatcmpl-1","model":"test","choices":[{"delta":{"tool_calls":[{"index":1,"id":"call_2","type":"function","function":{"name":"read_file","arguments":"{}"}}]},"index":0}]}\n\n',
                    b'event: message\ndata: {"id":"chatcmpl-1","choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"bash","arguments":"{\\"cmd\\":\\"ls\\"}"}}]},"index":0}]}\n\n',
                    b'event: message\ndata: {"id":"chatcmpl-1","choices":[{"delta":{},"index":0,"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}\n\n',
                    b'data: [DONE]\n\n',
                ]
                self.data = b"".join(chunks)
                self.pos = 0

            def read(self, size):
                chunk = self.data[self.pos:self.pos + size]
                self.pos += size
                return chunk

        stream = MockStream()
        events_text = ""
        for event in create_codex_sse_stream(stream):
            events_text += event

        # 验证 bash (index=0) 在 read_file (index=1) 之前发送
        bash_pos = events_text.index('"name":"bash"')
        read_file_pos = events_text.index('"name":"read_file"')
        self.assertLess(bash_pos, read_file_pos)
```

- [ ] **Step 4: 添加 chat_to_responses 直接单元测试

> 修复 #4：Task 6 之前只测试了 create_codex_sse_stream，缺少 chat_to_responses()
> 针对真实 Chat Completions 响应结构的直接测试。

```python
class TestChatToResponsesDirect(unittest.TestCase):
    """chat_to_responses() 针对真实供应商响应结构的直接测试。"""

    def test_gpt4_style_response(self):
        """模拟 GPT-4 风格响应：有 content + usage 完整。"""
        from transform import chat_to_responses
        resp = {
            "id": "chatcmpl-9XyZ",
            "object": "chat.completion",
            "created": 1714089600,
            "model": "gpt-4o",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Hello! How can I help you today?",
                    "refusal": None,
                },
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 10,
                "total_tokens": 30,
                "prompt_tokens_details": {"cached_tokens": 10},
                "completion_tokens_details": {"reasoning_tokens": 5},
            },
        }
        result = chat_to_responses(resp)
        self.assertTrue(result["id"].startswith("resp-"))
        self.assertIn("9XyZ", result["id"])
        self.assertEqual(result["model"], "gpt-4o")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["output"]), 1)
        self.assertEqual(result["output"][0]["content"][0]["text"], "Hello! How can I help you today?")
        self.assertEqual(result["usage"]["input_tokens"], 20)
        self.assertEqual(result["usage"]["output_tokens"], 10)
        self.assertEqual(result["usage"]["input_tokens_details"]["cached_tokens"], 10)
        self.assertEqual(result["usage"]["output_tokens_details"]["reasoning_tokens"], 5)

    def test_claude_style_tool_calls_response(self):
        """模拟 Claude via LiteLLM 返回的 tool_calls 响应。"""
        from transform import chat_to_responses
        resp = {
            "id": "chatcmpl-tool123",
            "model": "claude-sonnet-4-6",
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_abc1",
                            "type": "function",
                            "function": {"name": "bash", "arguments": '{"cmd":"pwd"}'},
                        },
                        {
                            "id": "call_abc2",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": '{"path":"/etc/hosts"}'},
                        },
                    ],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
        }
        result = chat_to_responses(resp)
        self.assertEqual(result["status"], "completed")
        fc_items = [o for o in result["output"] if o["type"] == "function_call"]
        self.assertEqual(len(fc_items), 2)
        self.assertEqual(fc_items[0]["name"], "bash")
        self.assertEqual(fc_items[1]["name"], "read_file")

    def test_empty_content_with_usage(self):
        """供应商可能返回空 content + usage，无 finish_reason。"""
        from transform import chat_to_responses
        resp = {
            "id": "chatcmpl-empty",
            "model": "test",
            "choices": [{
                "message": {"content": "", "refusal": None},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
        }
        result = chat_to_responses(resp)
        self.assertEqual(result["status"], "completed")
        # 空 content 不应出现在 output 中
        msg_items = [o for o in result["output"] if o["type"] == "message"]
        self.assertEqual(len(msg_items), 0)
```

- [ ] **Step 4: 添加 previous_response_id 丢弃验证**

```python
class TestPreviousResponseIdDiscarded(unittest.TestCase):
    """验证 previous_response_id 被丢弃，且 input 数组完整承载对话上下文。

    设计假设：HTTP SSE 模式不依赖 previous_response_id，
    Codex 每次请求会发送完整 input 数组（包含历史消息）。
    """

    def test_previous_response_id_not_in_output(self):
        from transform import responses_to_chat
        body = {
            "model": "gpt-4o",
            "input": [
                {"type": "message", "role": "user", "content": "Hi"},
                {"type": "message", "role": "assistant", "content": "Hello!"},
                {"type": "message", "role": "user", "content": "How are you?"},
            ],
            "previous_response_id": "resp-prev-123",
        }
        model_cfg = {"target": "claude-sonnet-4-6", "multimodal": False}
        result = responses_to_chat(body, model_cfg)
        # previous_response_id 不在输出中
        self.assertNotIn("previous_response_id", result)
        # 但 input 中的完整消息历史都被转换
        self.assertEqual(len(result["messages"]), 3)
        self.assertEqual(result["messages"][0]["content"], "Hi")
        self.assertEqual(result["messages"][1]["content"], "Hello!")
        self.assertEqual(result["messages"][2]["content"], "How are you?")
```

- [ ] **Step 5: Run all tests**

Run: `python3 -m pytest test_transform.py -v`
Expected: All tests PASS (should be ~30+ tests at this point)

- [ ] **Step 6: Commit**

```bash
git add test_transform.py
git commit -m "test: add comprehensive unit tests for all input types, edge cases, and SSE stream"
```

---

### Task 9: 端对端冒烟测试 + 配置校验测试

**Files:**
- Modify: `quick_test.py` — 添加 proxy 冒烟测试
- Create: `test_proxy_config.py` — 配置校验测试

- [ ] **Step 1: 创建 test_proxy_config.py — 配置校验测试（使用 importlib 动态加载）**

> Fix #2：避免 `from proxy import CONFIG_PATH, load_config` 在 import 时触发 `load_config()` 导致 mock 失效。
> 改用 `importlib.util.spec_from_file_location` 动态加载模块，避免模块级执行。
> Fix #5：`load_config()` 已有 `if not logging.root.handlers:` 守卫，重复调用安全。

```python
"""proxy.py 配置加载校验测试。"""
import os
import sys
import unittest
import tempfile
import importlib.util
from pathlib import Path


def load_proxy_module(config_path_override: Path = None):
    """使用 importlib 动态加载 proxy.py，避免模块级 load_config() 执行。

    config_path_override: 可选，传入自定义 CONFIG_PATH。
    """
    proxy_py = Path(__file__).parent / "proxy.py"
    spec = importlib.util.spec_from_file_location("proxy_test", str(proxy_py))
    mod = importlib.util.module_from_spec(spec)
    if config_path_override:
        mod.CONFIG_PATH = config_path_override
    spec.loader.exec_module(mod)
    return mod


class TestConfigValidation(unittest.TestCase):
    def test_missing_star_fallback(self):
        """model_map 缺少 * 键应导致 sys.exit(1)。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("""
proxy:
  host: "127.0.0.1"
  port: 48743
  log_level: "INFO"

upstream:
  base_url: "https://example.com/v1"
  api_key: "sk-test"
  timeout: 120
  connect_timeout: 10
  ssl_verify: true
  retry: 0

model_map:
  "gpt-4o":
    target: "claude-sonnet-4-6"
    multimodal: false
""")
            tmp_path = f.name

        mod = load_proxy_module(Path(tmp_path))
        with self.assertRaises(SystemExit):
            mod.load_config()

        os.unlink(tmp_path)

    def test_valid_config_loads(self):
        """有效配置应正常加载，不触发 sys.exit。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("""
proxy:
  host: "127.0.0.1"
  port: 48743
  log_level: "WARNING"

upstream:
  base_url: "https://example.com/v1"
  api_key: "sk-test"
  timeout: 120
  connect_timeout: 10
  ssl_verify: true
  retry: 1

model_map:
  "gpt-4o":
    target: "claude-sonnet-4-6"
    multimodal: false
  "*":
    target: "claude-sonnet-4-6"
    multimodal: false
""")
            tmp_path = f.name

        mod = load_proxy_module(Path(tmp_path))
        # load_config() 已有 logging.root.handlers 守卫，重复调用安全
        mod.load_config()
        cfg = mod.resolve_model("gpt-4o")
        self.assertEqual(cfg["target"], "claude-sonnet-4-6")
        self.assertFalse(cfg["multimodal"])
        # fallback
        cfg2 = mod.resolve_model("unknown-model")
        self.assertEqual(cfg2["target"], "claude-sonnet-4-6")

        os.unlink(tmp_path)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 端对端冒烟测试**

```bash
# 1. 启动 proxy
./server.sh start

# 2. 健康检查
curl -s http://127.0.0.1:48743/health
# 期望: {"status":"ok","pid":...}

# 3. 模型列表
curl -s http://127.0.0.1:48743/v1/models
# 期望: {"data":[{"id":"codex-mini-latest","object":"model"},...]}

# 4. GET /v1/responses → 426 (设计意图: 触发 Codex WebSocket 回退到 HTTP SSE)
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:48743/v1/responses
# 期望: 426

# 5. 404 处理
curl -s http://127.0.0.1:48743/unknown
# 期望: {"error": "not found"}

# 6. POST /v1/responses with invalid JSON → 400
curl -s -X POST http://127.0.0.1:48743/v1/responses -H "Content-Type: application/json" -d "not-json"
# 期望: {"error":{"type":"invalid_request_error","message":"..."}}

# 7. 停止
./server.sh stop

# 8. 验证 server.sh status 两个服务都显示"未运行"
./server.sh status
```

- [ ] **Step 3: 运行所有测试**

Run: `python3 -m pytest test_transform.py test_proxy_config.py -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add test_proxy_config.py quick_test.py
git commit -m "test: add config validation tests and E2E smoke test script"
```

---

## 风险与注意事项

| # | 风险 | 应对 |
|---|------|------|
| 1 | 上游 LiteLLM 返回的 reasoning 字段名不确定 | 已按 `reasoning_content` → `thinking` → `reasoning` 顺序检测 |
| 2 | `http.client` 的 `read(256)` 可能在某些环境下仍产生延迟 | 如果实际延迟明显，可调小 read size 到 128 或 64 |
| 3 | 上游 SSE 格式可能与标准 SSE 略有差异（如多行 data:） | `_parse_sse_event` 已支持多行 data: 拼接 |
| 4 | Codex 版本差异可能导致 request/response 字段不同 | 日志记录完整请求体，遇到未知字段时扩展 input 类型映射 |
| 5 | 日志轮转仅在启动时触发，运行中不会轮转 | 设计如此，每次启动最多一次轮转 |
| 6 | `previous_response_id` 丢弃后 Codex 是否发送完整 input | Task 8 Step 4 测试验证 input 数组完整承载上下文；端对端测试时观察 Codex 行为 |
| 7 | LiteLLM usage 字段出现时机不确定 | 实现上读完所有 chunk 后再发 `response.completed`，此时 usage 已捕获 |
