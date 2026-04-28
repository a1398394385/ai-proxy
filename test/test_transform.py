import json
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


class TestSSEParser(unittest.TestCase):
    def test_parse_simple_event(self):
        from transform import _parse_sse_event
        result = _parse_sse_event("event: response.created\ndata: {\"id\":\"resp-1\"}")
        self.assertEqual(result["event"], "response.created")
        self.assertEqual(result["data"]["id"], "resp-1")

    def test_parse_default_event(self):
        from transform import _parse_sse_event
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
        result = _parse_sse_event("data: {\"a\":1,\n data: \"b\":2}")
        self.assertEqual(result["data"]["a"], 1)
        self.assertEqual(result["data"]["b"], 2)


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
        # 验证 arguments 被完整拼接（JSON 转义后）
        self.assertIn('"arguments":"{\\"cmd\\":\\"ls\\"}"', events_text)

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

    def test_output_text_content(self):
        """验证 assistant 消息的 output_text content 类型被正确映射为文本。"""
        from transform import responses_to_chat
        body = {
            "model": "gpt-4o",
            "input": [{
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Hello from assistant"}],
            }],
        }
        model_cfg = {"target": "claude-sonnet-4-6", "multimodal": False}
        result = responses_to_chat(body, model_cfg)
        content = result["messages"][0]["content"]
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(content[0]["text"], "Hello from assistant")

    def test_mixed_input_and_output_text(self):
        """验证 user 的 input_text 和 assistant 的 output_text 混用场景。"""
        from transform import responses_to_chat
        body = {
            "model": "gpt-4o",
            "input": [
                {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Hi"}]},
                {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Hello!"}]},
                {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "How are you?"}]},
            ],
        }
        model_cfg = {"target": "claude-sonnet-4-6", "multimodal": False}
        result = responses_to_chat(body, model_cfg)
        self.assertEqual(len(result["messages"]), 3)
        self.assertEqual(result["messages"][0]["content"][0]["text"], "Hi")
        self.assertEqual(result["messages"][1]["content"][0]["text"], "Hello!")
        self.assertEqual(result["messages"][2]["content"][0]["text"], "How are you?")

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

    def test_computer_call_mapped(self):
        """验证 computer_call 与 function_call 一样被转换为 tool_calls 格式。"""
        from transform import responses_to_chat
        body = {
            "model": "gpt-4o",
            "input": [{
                "type": "computer_call",
                "id": "call_comp",
                "name": "screenshot",
                "arguments": '{"action":"screenshot"}',
            }],
        }
        model_cfg = {"target": "claude-sonnet-4-6", "multimodal": False}
        result = responses_to_chat(body, model_cfg)
        self.assertEqual(result["messages"][0]["role"], "assistant")
        self.assertEqual(result["messages"][0]["tool_calls"][0]["function"]["name"], "screenshot")


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

    def test_tools_responses_api_to_chat_format(self):
        """验证 Responses API 工具格式转换为 Chat Completions 格式（"function" 包裹）。"""
        from transform import responses_to_chat
        # Codex 实际发送的 Responses API 工具格式（无 "function" 包裹）
        body = {
            "model": "gpt-5.1-codex-max",
            "input": [{"type": "message", "role": "user", "content": "run ls"}],
            "tools": [{
                "type": "function",
                "name": "shell_command",
                "description": "Runs a shell command.",
                "strict": False,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                    },
                    "required": ["command"],
                    "additionalProperties": False,
                },
            }],
        }
        model_cfg = {"target": "qwen3.6-plus", "multimodal": False}
        result = responses_to_chat(body, model_cfg)

        self.assertEqual(len(result["tools"]), 1)
        tool = result["tools"][0]
        self.assertEqual(tool["type"], "function")
        self.assertIn("function", tool, "Chat Completions 工具必须有 'function' 键")
        func = tool["function"]
        self.assertEqual(func["name"], "shell_command")
        self.assertEqual(func["description"], "Runs a shell command.")
        self.assertFalse(func.get("strict"))
        self.assertIn("parameters", func)
        self.assertEqual(func["parameters"]["type"], "object")

    def test_map_tools_idempotent_on_chat_format(self):
        """验证 _map_tools 对已有 'function' 包裹的工具不会双重包裹。"""
        from transform import _map_tools
        chat_tools = [{
            "type": "function",
            "function": {"name": "bash", "parameters": {}},
        }]
        result = _map_tools(chat_tools)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["function"]["name"], "bash")

    def test_map_tools_drops_custom_type(self):
        """验证 _map_tools 丢弃非 function 类型的工具（如 Codex 的 custom apply_patch）。"""
        from transform import _map_tools
        tools = [
            {
                "type": "custom",
                "name": "apply_patch",
                "description": "Apply a patch to a file.",
                "format": {"type": "grammar", "syntax": "lark"},
            },
            {
                "type": "function",
                "function": {"name": "bash", "parameters": {}},
            },
        ]
        result = _map_tools(tools)
        self.assertEqual(len(result), 1, "应该只保留 function 类型工具")
        self.assertEqual(result[0]["function"]["name"], "bash")

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

    def test_function_call_uses_call_id(self):
        """验证 function_call 使用 call_id 字段（Codex 实际发送格式）。"""
        from transform import responses_to_chat
        body = {
            "model": "gpt-4o",
            "input": [{
                "type": "function_call",
                "call_id": "toolu_abc123",
                "name": "exec_command",
                "arguments": '{"cmd": "ls"}',
            }],
        }
        model_cfg = {"target": "claude-sonnet-4-6", "multimodal": False}
        result = responses_to_chat(body, model_cfg)
        msg = result["messages"][0]
        self.assertEqual(msg["role"], "assistant")
        self.assertEqual(msg["tool_calls"][0]["id"], "toolu_abc123")
        self.assertEqual(msg["tool_calls"][0]["function"]["name"], "exec_command")

    def test_function_call_output_uses_call_id(self):
        """验证 function_call_output 使用 call_id 字段（Codex 实际发送格式）。"""
        from transform import responses_to_chat
        body = {
            "model": "gpt-4o",
            "input": [{
                "type": "function_call_output",
                "call_id": "toolu_abc123",
                "output": "result",
            }],
        }
        model_cfg = {"target": "claude-sonnet-4-6", "multimodal": False}
        result = responses_to_chat(body, model_cfg)
        msg = result["messages"][0]
        self.assertEqual(msg["role"], "tool")
        self.assertEqual(msg["tool_call_id"], "toolu_abc123")
        self.assertEqual(msg["content"], "result")

    def test_computer_call_output_uses_call_id(self):
        """验证 computer_call_output 也支持 call_id 字段。"""
        from transform import responses_to_chat
        body = {
            "model": "gpt-4o",
            "input": [{
                "type": "computer_call_output",
                "call_id": "call_comp",
                "output": '{"action": "done"}',
            }],
        }
        model_cfg = {"target": "claude-sonnet-4-6", "multimodal": False}
        result = responses_to_chat(body, model_cfg)
        msg = result["messages"][0]
        self.assertEqual(msg["role"], "tool")
        self.assertEqual(msg["tool_call_id"], "call_comp")

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


class TestPreviousResponseIdDiscarded(unittest.TestCase):
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
        self.assertNotIn("previous_response_id", result)
        self.assertEqual(len(result["messages"]), 3)
        self.assertEqual(result["messages"][0]["content"], "Hi")
        self.assertEqual(result["messages"][1]["content"], "Hello!")
        self.assertEqual(result["messages"][2]["content"], "How are you?")


class TestChatToResponsesDirect(unittest.TestCase):
    def test_gpt4_style_response(self):
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
        from transform import chat_to_responses
        resp = {
            "id": "chatcmpl-tool123",
            "model": "claude-sonnet-4-6",
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": "call_abc1", "type": "function", "function": {"name": "bash", "arguments": '{"cmd":"pwd"}'}},
                        {"id": "call_abc2", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"/etc/hosts"}'}},
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
        msg_items = [o for o in result["output"] if o["type"] == "message"]
        self.assertEqual(len(msg_items), 0)


class TestFormatSSEEvent(unittest.TestCase):
    """测试 _format_sse_event 辅助函数 — 所有事件的 data JSON 必须包含 "type" 字段。"""

    def test_injects_type_field(self):
        """验证 data JSON 中包含 event_type 作为 "type" 字段。"""
        from transform import _format_sse_event
        result = _format_sse_event("response.output_text.delta", {
            "output_index": 0, "content_index": 0, "delta": "hello",
        })
        data_line = result.split("\n")[1]
        self.assertTrue(data_line.startswith("data: "))
        payload = json.loads(data_line[6:])
        self.assertEqual(payload["type"], "response.output_text.delta")
        self.assertEqual(payload["output_index"], 0)
        self.assertEqual(payload["content_index"], 0)
        self.assertEqual(payload["delta"], "hello")

    def test_type_overrides_existing(self):
        """验证 event_type 覆盖 data 中已有的 "type" 键。"""
        from transform import _format_sse_event
        result = _format_sse_event("response.completed", {
            "type": "wrong_type",
            "response": {"id": "resp-1"},
        })
        data_line = result.split("\n")[1]
        payload = json.loads(data_line[6:])
        self.assertEqual(payload["type"], "response.completed")
        self.assertNotEqual(payload["type"], "wrong_type")

    def test_compact_separators(self):
        """验证紧凑格式 — data JSON 的冒号和逗号后没有空格。"""
        from transform import _format_sse_event
        result = _format_sse_event("response.created", {
            "response": {"id": "resp-1", "status": "in_progress"},
        })
        # 只检查 data 行中 JSON 部分（去除 "data: " 前缀）
        data_line = result.split("\n")[1]
        json_str = data_line[6:]  # 去掉 "data: "
        self.assertNotIn(": ", json_str)
        self.assertNotIn(", ", json_str)

    def test_event_line_format(self):
        """验证事件行格式为 'event: <type>'。"""
        from transform import _format_sse_event
        result = _format_sse_event("response.metadata", {"model": "test"})
        first_line = result.split("\n")[0]
        self.assertEqual(first_line, "event: response.metadata")

    def test_data_json_parseable(self):
        """验证 data 行后是合法 JSON。"""
        from transform import _format_sse_event
        result = _format_sse_event("response.output_item.added", {
            "output_index": 1,
            "item": {"type": "message", "role": "assistant", "content": [], "status": "in_progress"},
        })
        data_line = result.split("\n")[1]
        self.assertTrue(data_line.startswith("data: "))
        payload = json.loads(data_line[6:])
        self.assertIsInstance(payload, dict)

    def test_ends_with_double_newline(self):
        """验证 SSE 事件以 \\n\\n 结尾。"""
        from transform import _format_sse_event
        result = _format_sse_event("response.output_text.done", {
            "output_index": 0, "content_index": 0, "text": "done",
        })
        self.assertTrue(result.endswith("\n\n"))

    def test_response_incomplete_event(self):
        """验证 response.incomplete 的正确格式（含 response 包裹）。"""
        from transform import _format_sse_event
        result = _format_sse_event("response.incomplete", {
            "response": {"incomplete_details": {"reason": "max_tokens"}},
        })
        data_line = result.split("\n")[1]
        payload = json.loads(data_line[6:])
        self.assertEqual(payload["type"], "response.incomplete")
        self.assertEqual(payload["response"]["incomplete_details"]["reason"], "max_tokens")


class TestSSEEventFormatIntegration(unittest.TestCase):
    """集成测试 — 逐事件验证 create_codex_sse_stream 输出的 data JSON 包含 "type" 字段。"""

    @staticmethod
    def _parse_events(text):
        """解析 SSE 文本，返回 [(event_type, data_dict), ...] 列表。"""
        events = []
        for block in text.strip().split("\n\n"):
            lines = block.split("\n")
            etype = None
            data = None
            for line in lines:
                if line.startswith("event: "):
                    etype = line[7:]
                elif line.startswith("data: "):
                    data = json.loads(line[6:])
            if etype and data:
                events.append((etype, data))
        return events

    @staticmethod
    def _make_mock_stream(chunks):
        """工厂：用 chunks 列表创建可读的 MockStream。"""
        class MockStream:
            def __init__(self):
                self.data = b"".join(chunks)
                self.pos = 0
            def read(self, size):
                chunk = self.data[self.pos:self.pos + size]
                self.pos += size
                return chunk
        return MockStream()

    def _stream_to_events(self, chunks):
        """辅助：chunks → create_codex_sse_stream → _parse_events。"""
        from transform import create_codex_sse_stream

        stream = self._make_mock_stream(chunks)
        text = ""
        for event in create_codex_sse_stream(stream):
            text += event
        return self._parse_events(text)

    # -- 预定义 chunk 数据 --

    TEXT_ONLY_CHUNKS = [
        b'event: message\ndata: {"id":"chatcmpl-1","model":"test","choices":[{"delta":{"content":"Hello"},"index":0}]}\n\n',
        b'event: message\ndata: {"id":"chatcmpl-1","choices":[{"delta":{"content":" World"},"index":0}]}\n\n',
        b'event: message\ndata: {"id":"chatcmpl-1","choices":[{"delta":{},"index":0,"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}}\n\n',
        b'data: [DONE]\n\n',
    ]

    TRUNCATED_CHUNKS = [
        b'event: message\ndata: {"id":"chatcmpl-1","model":"test","choices":[{"delta":{"content":"trunc"},"index":0,"finish_reason":"length"}],"usage":{"prompt_tokens":5,"completion_tokens":1,"total_tokens":6}}\n\n',
        b'data: [DONE]\n\n',
    ]

    REASONING_CHUNKS = [
        b'event: message\ndata: {"id":"chatcmpl-1","model":"test","choices":[{"delta":{"reasoning_content":"Think..."},"index":0}]}\n\n',
        b'event: message\ndata: {"id":"chatcmpl-1","choices":[{"delta":{"content":"Answer"},"index":0}]}\n\n',
        b'event: message\ndata: {"id":"chatcmpl-1","choices":[{"delta":{},"index":0,"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}\n\n',
        b'data: [DONE]\n\n',
    ]

    BASIC_CHUNKS = [
        b'event: message\ndata: {"id":"chatcmpl-1","model":"test","choices":[{"delta":{"content":"Hi"},"index":0}]}\n\n',
        b'event: message\ndata: {"id":"chatcmpl-1","choices":[{"delta":{},"index":0,"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}\n\n',
        b'data: [DONE]\n\n',
    ]

    def test_all_events_have_type_field(self):
        """纯文本流中每个事件的 data JSON 都包含 "type" 字段且与 event 行一致。"""
        events = self._stream_to_events(self.TEXT_ONLY_CHUNKS)
        self.assertGreater(len(events), 0, "至少应有一个 SSE 事件")
        for etype, data in events:
            self.assertIn("type", data, f"事件 '{etype}' 的 data JSON 缺少 'type' 字段")
            self.assertEqual(data["type"], etype,
                             f"data.type='{data.get('type')}' 与 event 行 '{etype}' 不一致")

    def test_response_incomplete_has_response_wrapping(self):
        """truncated 流中 response.incomplete 事件有 "response" 包裹。"""
        events = self._stream_to_events(self.TRUNCATED_CHUNKS)
        incomplete = [e for e in events if e[0] == "response.incomplete"]
        self.assertEqual(len(incomplete), 1, "应有一个 response.incomplete 事件")
        _, data = incomplete[0]
        self.assertIn("response", data, "response.incomplete 的 data 缺少 'response' 键")
        self.assertIn("incomplete_details", data["response"])
        self.assertEqual(data["response"]["incomplete_details"]["reason"], "max_tokens")

    def test_reasoning_plus_text_events_have_type_field(self):
        """推理+文本流中所有事件类型都出现且都有 "type" 字段。"""
        events = self._stream_to_events(self.REASONING_CHUNKS)
        expected = {
            "response.created", "response.metadata",
            "response.output_item.added", "response.reasoning_summary_text.delta",
            "response.reasoning_summary_text.done", "response.output_item.done",
            "response.output_text.delta", "response.output_text.done",
            "response.completed",
        }
        found = {e[0] for e in events}
        missing = expected - found
        self.assertFalse(missing, f"缺少事件类型: {missing}")
        for etype, data in events:
            self.assertIn("type", data, f"事件 '{etype}' 的 data JSON 缺少 'type' 字段")

    def test_first_event_matches_codex_fixture(self):
        """快照：第一个事件 response.created 与 Codex fixture 格式一致。"""
        events = self._stream_to_events(self.BASIC_CHUNKS)
        etype, data = events[0]
        self.assertEqual(etype, "response.created")
        self.assertEqual(data["type"], "response.created")
        self.assertIn("response", data)
        self.assertTrue(data["response"]["id"].startswith("resp-"))

    def test_last_event_matches_codex_fixture(self):
        """快照：最后一个事件 response.completed 与 Codex fixture 格式一致。"""
        events = self._stream_to_events(self.BASIC_CHUNKS)
        etype, data = events[-1]
        self.assertEqual(etype, "response.completed")
        self.assertEqual(data["type"], "response.completed")
        self.assertIn("response", data)
        self.assertIn("output", data["response"])
        self.assertIn("usage", data["response"])


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

    def test_codex_stream_converter_importable(self):
        """CodexStreamConverter 可从 transform 导入（别名在删除旧 StreamState 后生效）。"""
        from transform import CodexStreamConverter
        self.assertTrue(hasattr(CodexStreamConverter, "response_id"))


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


if __name__ == "__main__":
    unittest.main()
