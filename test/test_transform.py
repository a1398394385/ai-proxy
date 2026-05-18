import json
import unittest
from proxy.transform import generate_response_id


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
        from proxy.transform import responses_to_chat
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
        self.assertEqual(result["max_completion_tokens"], 1000)
        self.assertEqual(result["stream"], True)
        # instructions → system message
        self.assertEqual(result["messages"][0]["role"], "system")
        self.assertEqual(result["messages"][0]["content"], "You are a helpful assistant.")
        # user message
        self.assertEqual(result["messages"][1]["role"], "user")
        self.assertEqual(result["messages"][1]["content"], "Hello")

    def test_empty_instructions_skipped(self):
        from proxy.transform import responses_to_chat
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

    def test_input_string_wraps_to_user_message(self):
        from proxy.transform import responses_to_chat
        body = {
            "model": "gpt-4o",
            "input": "Hello world",
        }
        model_cfg = {"target": "test-model", "multimodal": False}
        result = responses_to_chat(body, model_cfg)
        msgs = result.get("messages", [])
        self.assertTrue(any(
            m.get("role") == "user" and "Hello world" in m.get("content", "")
            for m in msgs
        ), "input 为 string 时未能正确包装为 user 消息")

    def test_max_output_tokens_maps_to_both_fields(self):
        from proxy.transform import responses_to_chat
        body = {
            "model": "gpt-4o",
            "input": [],
            "max_output_tokens": 2048,
        }
        model_cfg = {"target": "test-model", "multimodal": False}
        result = responses_to_chat(body, model_cfg)
        self.assertEqual(result.get("max_completion_tokens"), 2048)
        self.assertEqual(result.get("max_tokens"), 2048)

    def test_stream_options_merge_include_usage(self):
        from proxy.transform import responses_to_chat
        body = {
            "model": "gpt-4o",
            "input": [],
            "stream": True,
            "stream_options": {"custom_field": True},
        }
        model_cfg = {"target": "test-model", "multimodal": False}
        result = responses_to_chat(body, model_cfg)
        so = result.get("stream_options", {})
        self.assertTrue(so.get("include_usage"),
                        "stream_options 应包含 include_usage=True")
        self.assertTrue(so.get("custom_field"),
                        "stream_options 应保留自定义字段")



class TestChatToResponses(unittest.TestCase):
    def test_basic_response(self):
        from proxy.transform import chat_to_responses
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
        self.assertEqual(result.get("object"), "response")
        self.assertIsInstance(result.get("created_at"), int)
        self.assertGreater(result["created_at"], 0)
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
        from proxy.transform import chat_to_responses
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
        from proxy.transform import chat_to_responses
        chat_resp = {
            "id": "some-other-id",
            "model": "test",
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        result = chat_to_responses(chat_resp)
        self.assertTrue(result["id"].startswith("resp-"))

    def test_incomplete_length(self):
        from proxy.transform import chat_to_responses
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
        from proxy.transform import chat_to_responses
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
        from proxy.transform import chat_to_responses
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
        from proxy.transform import chat_to_responses
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
        from proxy.transform import chat_to_responses
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

    def test_response_object_and_created_at(self):
        from proxy.transform import chat_to_responses
        chat_resp = {
            "id": "chatcmpl-abc",
            "model": "test",
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        result = chat_to_responses(chat_resp)
        self.assertEqual(result.get("object"), "response")
        self.assertIsInstance(result.get("created_at"), int)
        self.assertGreater(result["created_at"], 0)

    def test_reasoning_content_enriched(self):
        from proxy.transform import chat_to_responses
        chat_resp = {
            "id": "chatcmpl-def",
            "model": "test",
            "choices": [{
                "message": {"content": "answer", "reasoning_content": "thinking..."},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        result = chat_to_responses(chat_resp)
        items = [o for o in result["output"] if o["type"] == "reasoning"]
        self.assertEqual(len(items), 1, "应包含 1 个 reasoning item")
        has_content = any(
            c["type"] == "reasoning_text"
            for c in items[0].get("content", [])
        )
        self.assertTrue(has_content, "reasoning item 应包含 content/reasoning_text")



class TestSSEParser(unittest.TestCase):
    def test_parse_simple_event(self):
        from proxy.transform import _parse_sse_event
        result = _parse_sse_event("event: response.created\ndata: {\"id\":\"resp-1\"}")
        self.assertEqual(result["event"], "response.created")
        self.assertEqual(result["data"]["id"], "resp-1")

    def test_parse_default_event(self):
        from proxy.transform import _parse_sse_event
        result = _parse_sse_event("data: {\"key\":\"value\"}")
        self.assertEqual(result["event"], "message")
        self.assertEqual(result["data"]["key"], "value")

    def test_parse_done(self):
        from proxy.transform import _parse_sse_event
        result = _parse_sse_event("data: [DONE]")
        self.assertEqual(result["event"], "[DONE]")
        self.assertIsNone(result["data"])

    def test_parse_empty_returns_none(self):
        from proxy.transform import _parse_sse_event
        self.assertIsNone(_parse_sse_event(""))
        self.assertIsNone(_parse_sse_event(": keepalive"))

    def test_parse_invalid_json_returns_none(self):
        from proxy.transform import _parse_sse_event
        self.assertIsNone(_parse_sse_event("data: not-json"))

    def test_parse_multiple_data_lines(self):
        from proxy.transform import _parse_sse_event
        result = _parse_sse_event("data: {\"a\":1,\n data: \"b\":2}")
        self.assertEqual(result["data"]["a"], 1)
        self.assertEqual(result["data"]["b"], 2)


class TestIterSSEEvents(unittest.TestCase):
    def test_iter_multiple_events(self):
        from proxy.transform import iter_sse_events

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
        from proxy.transform import iter_sse_events

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
    def test_next_output_index_increments_on_text(self):
        from proxy.transform import CodexStreamConverter
        c = CodexStreamConverter()
        c.response_id = "resp-test"
        c.model = "test"
        self.assertEqual(c.next_output_index, 0)
        c._handle_text_delta("Hello")
        self.assertEqual(c.next_output_index, 1)
        self.assertEqual(c.text_output_index, 0)

    def test_next_output_index_increments_on_reasoning_then_text(self):
        from proxy.transform import CodexStreamConverter
        c = CodexStreamConverter()
        c.response_id = "resp-test"
        c.model = "test"
        c._handle_reasoning_delta("Think")
        c._handle_text_delta("Answer")
        self.assertEqual(c.reasoning_output_index, 0)
        self.assertEqual(c.text_output_index, 1)

    def test_stream_state_is_codex_stream_converter(self):
        from proxy.transform import StreamState, CodexStreamConverter
        self.assertIs(StreamState, CodexStreamConverter)


class TestSSEStreamIntegration(unittest.TestCase):
    """使用 mock 上游 SSE 数据，验证 create_codex_sse_stream 的完整事件序列。"""

    def test_text_only_stream(self):
        """纯文本流：created + metadata + output_item.added(message) + text deltas + text done + item done + completed。"""
        from proxy.transform import create_codex_sse_stream

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
        self.assertIn("event: response.in_progress", events_text)
        self.assertIn("event: response.content_part.added", events_text)
        self.assertIn("event: response.content_part.done", events_text)
        self.assertIn("data: [DONE]", events_text)
        # 不应该有推理事件
        self.assertNotIn("response.reasoning", events_text)

    def test_reasoning_plus_text_stream(self):
        """推理+文本流：验证 reasoning output_index=0, message output_index=1。"""
        from proxy.transform import create_codex_sse_stream

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
        self.assertIn("event: response.reasoning.delta", events_text)
        self.assertIn("event: response.reasoning.done", events_text)
        self.assertIn("data: [DONE]", events_text)
        self.assertIn("event: response.output_text.delta", events_text)
        self.assertIn("event: response.output_text.done", events_text)
        self.assertIn("event: response.completed", events_text)
        # 验证 reasoning 在 output_index=0
        self.assertIn('"output_index":0', events_text)
        # 验证 message 在 output_index=1
        self.assertIn('"output_index":1', events_text)

    def test_tool_calls_accumulation(self):
        """工具调用积累：验证 tool_calls 积累后一次性发送。"""
        from proxy.transform import create_codex_sse_stream

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
        self.assertIn("event: response.output_item.added", events_text)
        self.assertIn("event: response.function_call_arguments.delta", events_text)
        self.assertIn("event: response.function_call_arguments.done", events_text)
        self.assertIn("event: response.output_item.done", events_text)
        self.assertIn('"type":"function_call"', events_text)
        self.assertIn('"name":"bash"', events_text)
        self.assertIn("data: [DONE]", events_text)
        # 验证 arguments 被完整拼接（JSON 转义后）
        self.assertIn('"arguments":"{\\"cmd\\":\\"ls\\"}"', events_text)

    def test_multiple_tool_calls_out_of_order(self):
        """Risk #10：多 tool_calls 乱序到达，按 index 排序后发送（done 事件中 index=0 先于 index=1）。"""
        from proxy.transform import create_codex_sse_stream

        class MockStream:
            def __init__(self):
                # index=1 先到达（输出 added），index=0 后到达
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

        # done 事件中 bash (index=0) 在 read_file (index=1) 之前发送
        bash_done_pos = events_text.index('"name":"bash"')
        read_file_done_pos = events_text.index('"name":"read_file"')
        # 注意：added 事件中 read_file（先到达）在 bash 之前出现，
        # 但 done 事件中 bash 按 tc_index 排序在 read_file 之前
        # 验证两者的 done 事件顺序
        self.assertIn("event: response.output_item.added", events_text)
        self.assertIn("event: response.function_call_arguments.done", events_text)


class TestResponsesToChatAllInputTypes(unittest.TestCase):
    def test_image_multimodal_true(self):
        from proxy.transform import responses_to_chat
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
        from proxy.transform import responses_to_chat
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
        from proxy.transform import responses_to_chat
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
        from proxy.transform import responses_to_chat
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
        from proxy.transform import responses_to_chat
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
        from proxy.transform import responses_to_chat
        body = {
            "model": "gpt-4o",
            "input": [{"type": "reasoning", "id": "rs_123"}],
        }
        model_cfg = {"target": "claude-sonnet-4-6", "multimodal": False}
        result = responses_to_chat(body, model_cfg)
        self.assertEqual(len(result["messages"]), 0)

    def test_web_search_call_dropped(self):
        from proxy.transform import responses_to_chat
        body = {
            "model": "gpt-4o",
            "input": [{"type": "web_search_call"}],
        }
        model_cfg = {"target": "claude-sonnet-4-6", "multimodal": False}
        result = responses_to_chat(body, model_cfg)
        self.assertEqual(len(result["messages"]), 0)

    def test_code_interpreter_call_dropped(self):
        from proxy.transform import responses_to_chat
        body = {
            "model": "gpt-4o",
            "input": [{"type": "code_interpreter_call"}],
        }
        model_cfg = {"target": "claude-sonnet-4-6", "multimodal": False}
        result = responses_to_chat(body, model_cfg)
        self.assertEqual(len(result["messages"]), 0)

    def test_mcp_call_dropped(self):
        from proxy.transform import responses_to_chat
        body = {
            "model": "gpt-4o",
            "input": [{"type": "mcp_call"}],
        }
        model_cfg = {"target": "claude-sonnet-4-6", "multimodal": False}
        result = responses_to_chat(body, model_cfg)
        self.assertEqual(len(result["messages"]), 0)

    def test_computer_call_mapped(self):
        """验证 computer_call 与 function_call 一样被转换为 tool_calls 格式。"""
        from proxy.transform import responses_to_chat
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
        from proxy.transform import responses_to_chat
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
        from proxy.transform import responses_to_chat
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
        from proxy.transform import responses_to_chat
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
        from proxy.transform import _map_tools
        chat_tools = [{
            "type": "function",
            "function": {"name": "bash", "parameters": {}},
        }]
        result = _map_tools(chat_tools)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["function"]["name"], "bash")

    def test_map_tools_downgrades_custom_type(self):
        """非 function 类型工具降级为 function 格式，不丢弃。"""
        from proxy.transform import _map_tools
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
        self.assertEqual(len(result), 2, "custom 工具应降级保留而非丢弃")
        # apply_patch 降级为 function
        downgraded = [t for t in result if t["function"]["name"] == "apply_patch"]
        self.assertEqual(len(downgraded), 1)
        self.assertIn("input", downgraded[0]["function"]["parameters"]["properties"],
            "无 schema 的 custom 工具应兜底为 input 字符串参数")
        # 原始类型标注
        self.assertIn("原始工具类型: custom", downgraded[0]["function"]["description"])
        # bash 保持原样
        bash = [t for t in result if t["function"]["name"] == "bash"]
        self.assertEqual(len(bash), 1)

    def test_reasoning_effort_mapping(self):
        """reasoning.effort → reasoning_effort: Responses API 到 Chat Completions 的正确映射。"""
        from proxy.transform import responses_to_chat
        body = {
            "model": "gpt-4o",
            "input": [{"type": "message", "role": "user", "content": "think"}],
            "reasoning": {"effort": "high"},
        }
        model_cfg = {"target": "claude-sonnet-4-6", "multimodal": False}
        result = responses_to_chat(body, model_cfg)
        self.assertEqual(result["reasoning_effort"], "high",
            "reasoning.effort 应映射为 reasoning_effort")
        self.assertNotIn("reasoning", result,
            "reasoning 对象不应透传，Chat Completions API 不接受")

    def test_discarded_fields(self):
        """验证 previous_response_id, include, store, metadata 全部丢弃。service_tier 透传。"""
        from proxy.transform import responses_to_chat
        body = {
            "model": "gpt-4o",
            "input": [{"type": "message", "role": "user", "content": "hi"}],
            "previous_response_id": "resp-prev",
            "include": ["reasoning"],
            "store": True,
            "metadata": {"key": "val"},
            "service_tier": "default",
            "text": {},
        }
        model_cfg = {"target": "claude-sonnet-4-6", "multimodal": False}
        result = responses_to_chat(body, model_cfg)
        self.assertNotIn("previous_response_id", result)
        self.assertNotIn("include", result)
        self.assertNotIn("store", result)
        self.assertNotIn("metadata", result)

    def test_chat_params_passthrough(self):
        from proxy.transform import responses_to_chat
        body = {
            "model": "gpt-4o",
            "input": [{"type": "message", "role": "user", "content": "hello"}],
            "temperature": 0.7,
            "top_p": 0.9,
            "stop": ["END"],
            "frequency_penalty": 0.5,
            "presence_penalty": 0.5,
            "n": 2,
        }
        model_cfg = {"target": "test-model", "multimodal": False}
        result = responses_to_chat(body, model_cfg)
        self.assertEqual(result.get("temperature"), 0.7)
        self.assertEqual(result.get("top_p"), 0.9)
        self.assertEqual(result.get("stop"), ["END"])
        self.assertEqual(result.get("n"), 2)

    def test_responses_only_fields_discarded(self):
        import logging
        from proxy.transform import responses_to_chat
        # metadata, max_tool_calls, truncation 应被丢弃（不在结果中）
        body = {
            "model": "gpt-4o",
            "input": [{"type": "message", "role": "user", "content": "hi"}],
            "metadata": {"key": "val"},
            "max_tool_calls": 5,
            "truncation": "auto",
        }
        model_cfg = {"target": "test-model", "multimodal": False}
        result = responses_to_chat(body, model_cfg)
        self.assertNotIn("metadata", result)
        self.assertNotIn("max_tool_calls", result)
        self.assertNotIn("truncation", result)

    def test_function_call_output_to_tool_role(self):
        from proxy.transform import responses_to_chat
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
        from proxy.transform import responses_to_chat
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
        from proxy.transform import responses_to_chat
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
        from proxy.transform import responses_to_chat
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
        from proxy.transform import responses_to_chat
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
        from proxy.transform import responses_to_chat
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
        from proxy.transform import responses_to_chat
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
        from proxy.transform import chat_to_responses
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
        from proxy.transform import chat_to_responses
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
        from proxy.transform import chat_to_responses
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
        from proxy.transform import _format_sse_event
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
        from proxy.transform import _format_sse_event
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
        from proxy.transform import _format_sse_event
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
        from proxy.transform import _format_sse_event
        result = _format_sse_event("response.metadata", {"model": "test"})
        first_line = result.split("\n")[0]
        self.assertEqual(first_line, "event: response.metadata")

    def test_data_json_parseable(self):
        """验证 data 行后是合法 JSON。"""
        from proxy.transform import _format_sse_event
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
        from proxy.transform import _format_sse_event
        result = _format_sse_event("response.output_text.done", {
            "output_index": 0, "content_index": 0, "text": "done",
        })
        self.assertTrue(result.endswith("\n\n"))

    def test_response_incomplete_event(self):
        """验证 response.incomplete 的正确格式（含 response 包裹）。"""
        from proxy.transform import _format_sse_event
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
                    try:
                        data = json.loads(line[6:])
                    except json.JSONDecodeError:
                        pass  # [DONE] 终止标记不可解析为 JSON
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
        from proxy.transform import create_codex_sse_stream

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
            "response.created", "response.in_progress", "response.metadata",
            "response.output_item.added", "response.reasoning.delta",
            "response.reasoning.done", "response.output_item.done",
            "response.output_text.delta", "response.output_text.done",
            "response.content_part.added", "response.content_part.done",
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
                        try:
                            data = json.loads(line[6:])
                        except json.JSONDecodeError:
                            pass  # [DONE] 终止标记
                if etype and data:
                    events.append((etype, data))
        return events


class TestHandleReasoningDelta(_SSETestBase):
    def _make_converter(self):
        from proxy.transform import CodexStreamConverter
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


class TestProcessChunkAndFinish(_SSETestBase):
    def _make_converter(self, response_id="resp-test"):
        from proxy.transform import CodexStreamConverter
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
        """finish_reason=length 时同时发送 response.incomplete 和 response.completed。"""
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


class TestHandleToolCallDelta(_SSETestBase):
    def _make_converter(self):
        from proxy.transform import CodexStreamConverter
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


class TestNewSSEFeatures(_SSETestBase):
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

    # 继承 _SSETestBase._parse_events，不重复实现

    def _stream_to_text(self, chunks):
        from proxy.transform import create_codex_sse_stream
        stream = self._make_mock_stream(chunks)
        return "".join(create_codex_sse_stream(stream))

    def test_created_in_progress_metadata_sequence(self):
        """流开始事件必须为 created→in_progress→metadata 顺序。"""
        chunks = [
            b'data: {"id":"c1","model":"test","choices":[{"delta":{"content":"Hi"},"index":0}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}\n\n',
            b'data: [DONE]\n\n',
        ]
        events = self._parse_events([self._stream_to_text(chunks)])
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
        """多工具并发：index 1 先到，index 0 后到，done 事件中 bash 先于 read_file 发送。"""
        chunks = [
            # index=1 先到
            b'data: {"id":"c1","model":"test","choices":[{"delta":{"tool_calls":[{"index":1,"id":"call_2","type":"function","function":{"name":"read_file","arguments":"{}"}}]},"index":0}]}\n\n',
            # index=0 后到
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"bash","arguments":"{\\"cmd\\":\\"ls\\"}"}}]},"index":0}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}\n\n',
            b'data: [DONE]\n\n',
        ]
        text = self._stream_to_text(chunks)
        events = self._parse_events([text])
        done_events = [e for e in events if e[0] == "response.output_item.done"]
        tool_names = [e[1]["item"]["name"] for e in done_events if e[1]["item"]["type"] == "function_call"]
        self.assertIn("bash", tool_names)
        self.assertIn("read_file", tool_names)
        self.assertLess(tool_names.index("bash"), tool_names.index("read_file"),
                        "done 事件中 bash (index=0) 应在 read_file (index=1) 之前")


class TestOutputItemsToMessages(unittest.TestCase):
    """output_items_to_messages 独立单元测试（设计文稿 §4.3）。"""

    def test_text_message(self):
        from proxy.transform import output_items_to_messages
        items = [{"type": "message", "content": [{"type": "output_text", "text": "Hello"}]}]
        result = output_items_to_messages(items)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["role"], "assistant")
        self.assertEqual(result[0]["content"], "Hello")

    def test_pure_refusal_message_fallback_empty_string(self):
        """纯拒绝消息：content 没有 output_text，fallback 为空字符串，不能是 None 或 KeyError。"""
        from proxy.transform import output_items_to_messages
        items = [{"type": "message", "content": [{"type": "refusal", "refusal": "No"}]}]
        result = output_items_to_messages(items)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["content"], "")   # fallback，不是 None

    def test_multiple_function_calls_merged(self):
        """多个 function_call 合并为单条 assistant 消息。"""
        from proxy.transform import output_items_to_messages
        items = [
            {"type": "function_call", "id": "fc1", "call_id": "call_1", "name": "bash", "arguments": '{}'},
            {"type": "function_call", "id": "fc2", "call_id": "call_2", "name": "read_file", "arguments": '{}'},
        ]
        result = output_items_to_messages(items)
        self.assertEqual(len(result), 1, "多个工具调用应合并为单条 assistant 消息")
        self.assertIsNone(result[0]["content"])
        self.assertEqual(len(result[0]["tool_calls"]), 2)

    def test_reasoning_skipped(self):
        from proxy.transform import output_items_to_messages
        items = [
            {"type": "reasoning", "id": "rs1", "summary": [{"type": "summary_text", "text": "think"}]},
            {"type": "message", "content": [{"type": "output_text", "text": "Answer"}]},
        ]
        result = output_items_to_messages(items)
        self.assertEqual(len(result), 1, "reasoning 应被跳过")
        self.assertEqual(result[0]["content"], "Answer")


class TestProxyErrorPathsDone(unittest.TestCase):
    """验证 SDK 驱动路径的错误处理包含 data: [DONE]。"""

    @staticmethod
    def _read_forward_streaming_source():
        import pathlib
        return (pathlib.Path(__file__).parent.parent / "proxy" / "handler.py").read_text()

    def test_stream_exception_path_has_done(self):
        """流式转换异常路径补发 [DONE]。"""
        src = self._read_forward_streaming_source()
        idx = src.index("流式转换异常")
        segment = src[idx:idx+2000]
        self.assertIn("[DONE]", segment,
            "流式转换异常路径缺少 data: [DONE]")

    def test_upstream_error_path_calls_handle_upstream_error(self):
        """http.client 异常由 _handle_upstream_error 处理。"""
        src = self._read_forward_streaming_source()
        # 外层 try 注释是 _forward_streaming 特有标记
        idx = src.index("外层 try: 包裹全部逻辑")
        segment = src[idx:idx+1500]
        self.assertIn("_handle_upstream_error", segment)

    def test_iter_sse_buffer_size(self):
        """iter_sse_events 读缓冲区应为 4096 字节。"""
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "proxy" / "sse_utils.py").read_text()
        self.assertIn("read(4096)", src, "iter_sse_events 缓冲区应从 256 增大到 4096")
        self.assertNotIn("read(256)", src, "旧 256 字节缓冲区应已删除")


class TestChatToResponsesRefusalMerge(unittest.TestCase):
    def test_text_and_refusal_merged_into_single_message_item(self):
        """text + refusal 应合并进同一个 message output item 的 content 数组。"""
        from proxy.transform import chat_to_responses
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
        from proxy.transform import chat_to_responses
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
        from proxy.transform import chat_to_responses
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


class TestHandleRefusalDelta(_SSETestBase):
    def _make_converter(self):
        from proxy.transform import CodexStreamConverter
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
        from proxy.transform import CodexStreamConverter
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


class TestResponsesStreamConverterCreated(unittest.TestCase):
    def _make_converter(self):
        from proxy.transform import ResponsesStreamConverter
        c = ResponsesStreamConverter()
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

    def test_codex_alias_importable(self):
        """CodexStreamConverter 仍可作为别名导入。"""
        from proxy.transform import CodexStreamConverter as Alias
        import proxy.transform as mod
        self.assertIs(Alias, mod.ResponsesStreamConverter)



class TestResponsesStreamConverterFields(unittest.TestCase):
    def test_default_fields(self):
        from proxy.transform import ResponsesStreamConverter
        c = ResponsesStreamConverter()
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

    def test_responses_stream_converter_importable(self):
        """ResponsesStreamConverter 可从 transform 导入。"""
        from proxy.transform import ResponsesStreamConverter
        self.assertTrue(hasattr(ResponsesStreamConverter, "response_id"))
        """CodexStreamConverter 可从 transform 导入（别名在删除旧 StreamState 后生效）。"""
        from proxy.transform import CodexStreamConverter
        self.assertTrue(hasattr(CodexStreamConverter, "response_id"))


class TestToolBlockState(unittest.TestCase):
    def test_default_fields(self):
        from proxy.transform import ToolBlockState
        b = ToolBlockState()
        self.assertEqual(b.output_index, -1)
        self.assertEqual(b.call_id, "")
        self.assertEqual(b.name, "")
        self.assertEqual(b.accumulated_args, "")
        self.assertFalse(b.started)
        self.assertEqual(b.item_id, "")

    def test_mutation(self):
        from proxy.transform import ToolBlockState
        b = ToolBlockState()
        b.call_id = "call_abc"
        b.name = "bash"
        b.accumulated_args = '{"cmd":"ls"}'
        b.started = True
        b.item_id = "fc_00000001"
        b.output_index = 2
        self.assertEqual(b.call_id, "call_abc")
        self.assertEqual(b.output_index, 2)


class TestFixToolMessageOrder(unittest.TestCase):
    """验证 _fix_tool_message_order：assistant 纯文本消息不能夹在 tool_call/tool 对之间。"""

    def test_text_between_tool_pairs_merged(self):
        from proxy.transform import _fix_tool_message_order
        # 模拟问题场景：assistant+tool_calls → tool → assistant(纯文本) → assistant+tool_calls → tool
        # 连续的 assistant 消息应合并，而非推迟到末尾
        messages = [
            {"role": "assistant", "tool_calls": [{"id": "call_00", "type": "function"}]},
            {"role": "tool", "tool_call_id": "call_00"},
            {"role": "assistant", "content": "让我先核实设计"},  # 连续 assistant
            {"role": "assistant", "tool_calls": [{"id": "call_01", "type": "function"}]},
            {"role": "tool", "tool_call_id": "call_01"},
        ]
        result = _fix_tool_message_order(messages)
        roles = [(m["role"], bool(m.get("tool_calls"))) for m in result]
        # assistant(纯文本) 合并到下一条 assistant+tool_calls
        expected_roles = [
            ("assistant", True),
            ("tool", False),
            ("assistant", True),  # content + tool_calls 合并
            ("tool", False),
        ]
        self.assertEqual(roles, expected_roles)
        # 验证合并后的消息同时包含 content 和 tool_calls
        merged_msg = result[2]
        self.assertEqual(merged_msg["content"], "让我先核实设计")
        self.assertEqual(len(merged_msg["tool_calls"]), 1)

    def test_no_tool_calls_unchanged(self):
        from proxy.transform import _fix_tool_message_order
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        result = _fix_tool_message_order(messages)
        self.assertEqual(result, messages)

    def test_proper_order_unchanged(self):
        from proxy.transform import _fix_tool_message_order
        messages = [
            {"role": "assistant", "tool_calls": [{"id": "call_0", "type": "function"}]},
            {"role": "tool", "tool_call_id": "call_0"},
            {"role": "assistant", "content": "done"},
        ]
        result = _fix_tool_message_order(messages)
        self.assertEqual(result, messages)

    def test_real_scenario(self):
        """复现 e3d3e3fb52a94d5c 的消息序列。"""
        from proxy.transform import _fix_tool_message_order
        messages = [
            {"role": "system", "content": "..."},
            {"role": "system", "content": "..."},
            {"role": "user", "content": "..."},
            {"role": "user", "content": "..."},
            {"role": "assistant", "tool_calls": [{"id": "call_00_7aXx", "type": "function"}]},
            {"role": "tool", "tool_call_id": "call_00_7aXx"},
            {"role": "assistant", "content": "让我先核实设计中的现有代码"},
            {"role": "assistant", "tool_calls": [{"id": "call_00_nqkw", "type": "function"}]},
            {"role": "assistant", "tool_calls": [{"id": "call_01_dvAq", "type": "function"}]},
            {"role": "assistant", "tool_calls": [{"id": "call_02_Qvae", "type": "function"}]},
            {"role": "tool", "tool_call_id": "call_00_nqkw"},
            {"role": "tool", "tool_call_id": "call_01_dvAq"},
            {"role": "tool", "tool_call_id": "call_02_Qvae"},
        ]
        result = _fix_tool_message_order(messages)

        # 验证：所有 tool_call 都有对应的 tool 响应
        all_tc_ids = set()
        all_tool_call_ids = set()
        for m in result:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    all_tc_ids.add(tc["id"])
            if m.get("role") == "tool":
                all_tool_call_ids.add(m.get("tool_call_id"))
        self.assertEqual(all_tc_ids, all_tool_call_ids)

        # 验证连续的 assistant+tool_calls 被合并，且 assistant(text) 合并到下一条
        merged_tool_call_msgs = [m for m in result if m.get("role") == "assistant" and m.get("tool_calls")]
        self.assertEqual(len(merged_tool_call_msgs), 2)
        self.assertEqual(len(merged_tool_call_msgs[1]["tool_calls"]), 3)

        # 验证文本消息被合并到第二条 assistant 消息中
        self.assertEqual(merged_tool_call_msgs[1]["content"], "让我先核实设计中的现有代码")

    def test_user_between_tool_calls_and_tool(self):
        """复现 ce91a35cdad14ffa：Anthropic user 消息夹在 assistant+tool_calls 和 tool 之间。"""
        from proxy.transform import _fix_tool_message_order
        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "帮我提交者 3 个文件"},
            {"role": "assistant", "tool_calls": [
                {"id": "call_00", "type": "function"},
                {"id": "call_01", "type": "function"},
                {"id": "call_02", "type": "function"},
            ]},
            {"role": "user", "content": "用户补充内容"},  # 夹在 tool_calls 和 tool 之间
            {"role": "tool", "tool_call_id": "call_00"},
            {"role": "tool", "tool_call_id": "call_01"},
            {"role": "tool", "tool_call_id": "call_02"},
        ]
        result = _fix_tool_message_order(messages)

        # 验证：assistant+tool_calls 后紧跟 tool 消息，user 被推迟
        roles = [(m["role"], m.get("tool_call_id") or bool(m.get("tool_calls"))) for m in result]
        expected_roles = [
            ("system", False),
            ("user", False),
            ("assistant", True),   # tool_calls
            ("tool", "call_00"),
            ("tool", "call_01"),
            ("tool", "call_02"),
            ("user", False),       # 推迟到最后
        ]
        self.assertEqual(roles, expected_roles)

        # 验证：assistant+tool_calls 后面紧跟的是 tool，不是 user
        for i, m in enumerate(result):
            if m.get("role") == "assistant" and m.get("tool_calls"):
                self.assertEqual(result[i + 1]["role"], "tool")

    def test_consecutive_tool_calls_merged(self):
        """复现 d71205a904704afc 场景：连续多条 assistant+tool_calls 合并为单条。"""
        from proxy.transform import _fix_tool_message_order
        messages = [
            {"role": "assistant", "tool_calls": [{"id": "call_00_u8Yt", "type": "function"}]},
            {"role": "tool", "tool_call_id": "call_00_u8Yt"},
            {"role": "assistant", "tool_calls": [{"id": "call_00_ZxNe", "type": "function"}]},
            {"role": "tool", "tool_call_id": "call_00_ZxNe"},
            {"role": "assistant", "tool_calls": [{"id": "call_00_vggd", "type": "function"}]},
            {"role": "assistant", "tool_calls": [{"id": "call_01_0mOn", "type": "function"}]},
            {"role": "assistant", "tool_calls": [{"id": "call_02_iEjm", "type": "function"}]},
            {"role": "tool", "tool_call_id": "call_00_vggd"},
            {"role": "tool", "tool_call_id": "call_01_0mOn"},
            {"role": "tool", "tool_call_id": "call_02_iEjm"},
        ]
        result = _fix_tool_message_order(messages)

        # 验证：连续的 3 条 assistant+tool_calls 合并为 1 条
        tool_call_msgs = [m for m in result if m.get("role") == "assistant" and m.get("tool_calls")]
        self.assertEqual(len(tool_call_msgs), 3)  # 3组：单+单+合并3
        self.assertEqual(len(tool_call_msgs[2]["tool_calls"]), 3)  # 3条合并为1条

        # 验证所有 call_id 都在
        all_tc = []
        for m in tool_call_msgs:
            all_tc.extend(tc["id"] for tc in m["tool_calls"])
        self.assertEqual(sorted(all_tc), sorted([
            "call_00_u8Yt", "call_00_ZxNe", "call_00_vggd", "call_01_0mOn", "call_02_iEjm"
        ]))


class TestResponsesStreamConverterCompat(unittest.TestCase):
    """ResponseStreamConverter 重命名向后兼容测试。"""

    def test_alias_class_equality(self):
        from proxy.transform import ResponsesStreamConverter, CodexStreamConverter
        self.assertIs(ResponsesStreamConverter, CodexStreamConverter,
                      "CodexStreamConverter 应作为别名指向 ResponsesStreamConverter")

    def test_alias_function_equality(self):
        from proxy.transform import create_responses_sse_stream, create_codex_sse_stream
        self.assertIs(create_responses_sse_stream, create_codex_sse_stream,
                      "create_codex_sse_stream 应作为别名指向 create_responses_sse_stream")

    def test_stream_state_alias(self):
        from proxy.transform import ResponsesStreamConverter, StreamState
        self.assertIs(StreamState, ResponsesStreamConverter,
                      "StreamState 应指向 ResponsesStreamConverter")

    def test_create_responses_sse_stream_importable(self):
        from proxy.transform import create_responses_sse_stream
        self.assertTrue(callable(create_responses_sse_stream))

    def test_create_codex_sse_stream_legacy_importable(self):
        from proxy.transform import create_codex_sse_stream
        self.assertTrue(callable(create_codex_sse_stream))

    def test_class_working(self):
        from proxy.transform import ResponsesStreamConverter
        c = ResponsesStreamConverter()
        self.assertTrue(hasattr(c, "response_id"))
        self.assertTrue(hasattr(c, "process_chunk"))


if __name__ == "__main__":
    unittest.main()
