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


if __name__ == "__main__":
    unittest.main()
