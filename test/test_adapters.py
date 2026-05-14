"""测试 ProtocolAdapter — ResponsesAdapter 和 MessagesAdapter 的双向转换。

迁移自 test_transform.py 和 test_transform_anthropic.py。
每个测试方法使用 adapter.request_to() / .response_from() / .stream_from() 接口。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from proxy.adapters import get_adapter, UnsupportedFormat


# ═══════════════════════════════════════════════════════════════════
# ResponsesAdapter 测试
# ═══════════════════════════════════════════════════════════════════

class TestResponsesAdapterProtocol(unittest.TestCase):
    """ProtocolAdapter 基本接口。"""

    def test_protocol_is_responses(self):
        adapter = get_adapter("responses")
        self.assertEqual(adapter.protocol, "responses")

    def test_request_to_unsupported_format(self):
        adapter = get_adapter("responses")
        with self.assertRaises(UnsupportedFormat):
            adapter.request_to("messages", {"model": "x", "input": []}, {})

    def test_response_from_unsupported_format(self):
        adapter = get_adapter("responses")
        with self.assertRaises(UnsupportedFormat):
            adapter.response_from("messages", {})

    def test_stream_from_unsupported_format(self):
        adapter = get_adapter("responses")
        with self.assertRaises(UnsupportedFormat):
            list(adapter.stream_from("messages", iter([])))


class TestResponsesAdapterRequestTo(unittest.TestCase):
    """ResponsesAdapter.request_to("chat_completions") 等价于旧 responses_to_chat。"""

    @classmethod
    def setUpClass(cls):
        cls.adapter = get_adapter("responses")

    def test_instructions_to_system(self):
        """instructions → system message。"""
        result = self.adapter.request_to("chat_completions", {
            "model": "gpt-4o",
            "instructions": "You are helpful.",
            "input": [{"type": "message", "role": "user", "content": "Hello"}],
        }, {"target": "gpt-4o", "multimodal": False})
        self.assertEqual(result["model"], "gpt-4o")
        self.assertIn("messages", result)
        self.assertEqual(result["messages"][0]["role"], "system")
        self.assertEqual(result["messages"][0]["content"], "You are helpful.")

    def test_no_instructions_no_system(self):
        """无 instructions → 无 system 消息。"""
        result = self.adapter.request_to("chat_completions", {
            "model": "gpt-4o",
            "input": [{"type": "message", "role": "user", "content": "Hello"}],
        }, {"target": "gpt-4o", "multimodal": False})
        roles = [m["role"] for m in result["messages"]]
        self.assertNotIn("system", roles)

    def test_target_model_mapped(self):
        """model name mapped to target via model_cfg。"""
        result = self.adapter.request_to("chat_completions", {
            "model": "codex-mini",
            "instructions": "Be helpful.",
            "input": [{"type": "message", "role": "user", "content": "Hi"}],
        }, {"target": "claude-sonnet-4-6", "multimodal": False})
        self.assertEqual(result["model"], "claude-sonnet-4-6")

    def test_max_output_tokens_mapped(self):
        """max_output_tokens → max_tokens。"""
        result = self.adapter.request_to("chat_completions", {
            "model": "gpt-4o",
            "input": [{"type": "message", "role": "user", "content": "Hi"}],
            "max_output_tokens": 2000,
        }, {"target": "gpt-4o", "multimodal": False})
        self.assertEqual(result["max_tokens"], 2000)

    def test_tools_conversion(self):
        """工具定义转换。"""
        result = self.adapter.request_to("chat_completions", {
            "model": "gpt-4o",
            "tools": [{
                "type": "function",
                "name": "get_weather",
                "description": "Get weather for a city",
                "input_schema": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                },
            }],
            "input": [{"type": "message", "role": "user", "content": "Weather?"}],
        }, {"target": "gpt-4o", "multimodal": False})
        self.assertIn("tools", result)
        self.assertEqual(len(result["tools"]), 1)
        self.assertEqual(result["tools"][0]["type"], "function")
        self.assertEqual(result["tools"][0]["function"]["name"], "get_weather")

    def test_image_input_multimodal_true(self):
        """图片输入 + multimodal=True → content array。"""
        result = self.adapter.request_to("chat_completions", {
            "model": "gpt-4o",
            "input": [{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "What is this?"},
                {"type": "input_image", "image_url": "http://example.com/img.jpg"},
            ]}],
        }, {"target": "gpt-4o", "multimodal": True})
        msgs = result["messages"]
        self.assertEqual(len(msgs), 1)
        content = msgs[0]["content"]
        self.assertIsInstance(content, list)
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(content[1]["type"], "image_url")

    def test_previous_response_id_discarded(self):
        """previous_response_id 不在请求体中透传。"""
        result = self.adapter.request_to("chat_completions", {
            "model": "gpt-4o",
            "previous_response_id": "resp-123",
            "input": [{"type": "message", "role": "user", "content": "Hi"}],
        }, {"target": "gpt-4o", "multimodal": False})
        self.assertNotIn("previous_response_id", result)

    def test_instructions_empty_skipped(self):
        """空 instructions 不生成 system 消息。"""
        result = self.adapter.request_to("chat_completions", {
            "model": "gpt-4o",
            "instructions": "",
            "input": [{"type": "message", "role": "user", "content": "Hi"}],
        }, {"target": "gpt-4o", "multimodal": False})
        self.assertEqual(len(result["messages"]), 1)
        self.assertEqual(result["messages"][0]["role"], "user")


class TestResponsesAdapterResponseFrom(unittest.TestCase):
    """ResponsesAdapter.response_from("chat_completions") 等价于旧 chat_to_responses。"""

    @classmethod
    def setUpClass(cls):
        cls.adapter = get_adapter("responses")

    def test_basic_response(self):
        result = self.adapter.response_from("chat_completions", {
            "id": "chatcmpl-abc123",
            "model": "claude-sonnet-4-6",
            "choices": [{"message": {"content": "Hello world"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        })
        self.assertTrue(result["id"].startswith("resp-"))
        self.assertEqual(result["model"], "claude-sonnet-4-6")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["output"]), 1)
        msg = result["output"][0]
        self.assertEqual(msg["type"], "message")
        self.assertEqual(msg["role"], "assistant")
        self.assertEqual(msg["content"][0]["type"], "output_text")
        self.assertEqual(msg["content"][0]["text"], "Hello world")
        self.assertEqual(result["usage"]["input_tokens"], 100)
        self.assertEqual(result["usage"]["output_tokens"], 50)

    def test_id_prefix_replacement(self):
        result = self.adapter.response_from("chat_completions", {
            "id": "chatcmpl-xyz",
            "model": "test",
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })
        self.assertTrue(result["id"].startswith("resp-"))

    def test_non_chatcmpl_id(self):
        result = self.adapter.response_from("chat_completions", {
            "id": "some-other-id",
            "model": "test",
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })
        self.assertTrue(result["id"].startswith("resp-"))

    def test_content_filter(self):
        result = self.adapter.response_from("chat_completions", {
            "id": "chatcmpl-filtered",
            "model": "test",
            "choices": [{"message": {"content": "I cannot answer"}, "finish_reason": "content_filter"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })
        self.assertEqual(result["status"], "incomplete")

    def test_tool_calls_in_response(self):
        result = self.adapter.response_from("chat_completions", {
            "id": "chatcmpl-tool",
            "model": "test",
            "choices": [{"message": {
                "content": None,
                "tool_calls": [{
                    "id": "call_123",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city":"London"}'},
                }],
            }, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })
        self.assertEqual(len(result["output"]), 1)
        item = result["output"][0]
        self.assertEqual(item["type"], "function_call")
        self.assertEqual(item["name"], "get_weather")
        self.assertEqual(item["arguments"], '{"city":"London"}')

    def test_refusal(self):
        result = self.adapter.response_from("chat_completions", {
            "id": "chatcmpl-refusal",
            "model": "test",
            "choices": [{"message": {"content": None, "refusal": "I cannot answer that"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })
        self.assertEqual(result["output"][0]["type"], "message")
        self.assertIn(result["output"][0]["content"][0]["type"], ("output_text", "refusal"))

    def test_usage_details_defaults(self):
        result = self.adapter.response_from("chat_completions", {
            "id": "chatcmpl-u",
            "model": "test",
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })
        self.assertEqual(result["usage"]["input_tokens"], 10)
        self.assertEqual(result["usage"]["output_tokens"], 5)
        self.assertEqual(result["usage"]["total_tokens"], 15)
        self.assertEqual(result["usage"].get("cached_read_tokens", 0), 0)
        self.assertEqual(result["usage"].get("cached_write_tokens", 0), 0)


class TestResponsesAdapterStreamFrom(unittest.TestCase):
    """ResponsesAdapter.stream_from("chat_completions") 等价于旧 create_codex_sse_stream。"""

    @classmethod
    def setUpClass(cls):
        cls.adapter = get_adapter("responses")

    def test_stream_produces_sse_format(self):
        """stream_from 至少产生 SSE 格式输出。"""
        # 使用 dict 模拟 OpenAI SDK chunk（stream 转换通过 chunk.get() 访问）
        chunk = {
            "id": "chatcmpl-1", "object": "chat.completion.chunk",
            "created": 1234567890, "model": "gpt-4o",
            "choices": [{
                "delta": {"content": "hello", "role": None, "tool_calls": None},
                "finish_reason": None, "index": 0,
            }],
            "usage": None,
        }
        events = list(self.adapter.stream_from("chat_completions", iter([chunk])))
        self.assertGreater(len(events), 0)


# ═══════════════════════════════════════════════════════════════════
# MessagesAdapter 测试
# ═══════════════════════════════════════════════════════════════════

class TestMessagesAdapterProtocol(unittest.TestCase):
    """MessagesAdapter 基本接口。"""

    def test_protocol_is_messages(self):
        adapter = get_adapter("messages")
        self.assertEqual(adapter.protocol, "messages")

    def test_request_to_unsupported_format(self):
        adapter = get_adapter("messages")
        with self.assertRaises(UnsupportedFormat):
            adapter.request_to("responses",
                {"model": "x", "max_tokens": 1, "messages": []}, {})

    def test_response_from_unsupported_format(self):
        adapter = get_adapter("messages")
        with self.assertRaises(UnsupportedFormat):
            adapter.response_from("responses", {})

    def test_stream_from_unsupported_format(self):
        adapter = get_adapter("messages")
        with self.assertRaises(UnsupportedFormat):
            list(adapter.stream_from("responses", iter([])))


class TestMessagesAdapterRequestTo(unittest.TestCase):
    """MessagesAdapter.request_to("chat_completions") 等价于旧 anthropic_to_chat。"""

    @classmethod
    def setUpClass(cls):
        cls.adapter = get_adapter("messages")
        cls.model_cfg = {"target": "qwen3.6-plus", "multimodal": True}

    def test_simple_text_message(self):
        result = self.adapter.request_to("chat_completions", {
            "model": "claude-sonnet",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1000,
        }, self.model_cfg)
        self.assertEqual(result["model"], "qwen3.6-plus")
        self.assertEqual(len(result["messages"]), 1)
        self.assertEqual(result["messages"][0]["role"], "user")
        self.assertEqual(result["messages"][0]["content"], "Hello")
        self.assertEqual(result["max_tokens"], 1000)

    def test_system_string(self):
        result = self.adapter.request_to("chat_completions", {
            "model": "claude-sonnet",
            "system": "You are helpful.",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 1000,
        }, self.model_cfg)
        self.assertEqual(result["messages"][0]["role"], "system")
        self.assertEqual(result["messages"][0]["content"], "You are helpful.")
        self.assertEqual(result["messages"][1]["role"], "user")

    def test_system_array(self):
        result = self.adapter.request_to("chat_completions", {
            "model": "claude-sonnet",
            "system": [{"type": "text", "text": "You are helpful."}],
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 100,
        }, self.model_cfg)
        self.assertEqual(result["messages"][0]["role"], "system")

    def test_multimodal_image(self):
        result = self.adapter.request_to("chat_completions", {
            "model": "claude-sonnet",
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "What?"},
                {"type": "image", "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": "AAAA",
                }},
            ]}],
            "max_tokens": 100,
        }, {"target": "qwen3.6-plus", "multimodal": True})
        self.assertIn("messages", result)

    def test_tool_definitions_conversion(self):
        result = self.adapter.request_to("chat_completions", {
            "model": "claude-sonnet",
            "messages": [{"role": "user", "content": "Weather?"}],
            "max_tokens": 100,
            "tools": [{
                "name": "get_weather",
                "description": "Get weather",
                "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}},
            }],
        }, {"target": "qwen3.6-plus", "multimodal": True})
        self.assertIn("tools", result)
        self.assertEqual(result["tools"][0]["function"]["name"], "get_weather")

    def test_tool_choice_auto(self):
        result = self.adapter.request_to("chat_completions", {
            "model": "claude-sonnet",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 100,
            "tool_choice": {"type": "auto"},
        }, self.model_cfg)
        self.assertEqual(result.get("tool_choice"), "auto")

    def test_empty_messages(self):
        result = self.adapter.request_to("chat_completions", {
            "model": "claude-sonnet",
            "messages": [],
            "max_tokens": 100,
        }, self.model_cfg)
        self.assertEqual(result["messages"], [])

    def test_thinking_discarded(self):
        """thinking 配置被丢弃（chat_completions 不支持）。"""
        result = self.adapter.request_to("chat_completions", {
            "model": "claude-sonnet",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 100,
            "thinking": {"type": "enabled", "budget_tokens": 1000},
        }, self.model_cfg)
        self.assertNotIn("thinking", result)


class TestMessagesAdapterResponseFrom(unittest.TestCase):
    """MessagesAdapter.response_from("chat_completions") 等价于旧 chat_to_anthropic。"""

    @classmethod
    def setUpClass(cls):
        cls.adapter = get_adapter("messages")

    def test_basic_text_response(self):
        result = self.adapter.response_from("chat_completions", {
            "id": "chatcmpl-1",
            "model": "qwen3.6-plus",
            "choices": [{"message": {"content": "Hello"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })
        self.assertEqual(result["type"], "message")
        self.assertEqual(result["role"], "assistant")
        self.assertEqual(result["content"][0]["type"], "text")
        self.assertEqual(result["content"][0]["text"], "Hello")
        self.assertEqual(result["stop_reason"], "end_turn")

    def test_finish_reason_stop(self):
        result = self.adapter.response_from("chat_completions", {
            "id": "chatcmpl-1", "model": "test",
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })
        self.assertEqual(result["stop_reason"], "end_turn")

    def test_finish_reason_length(self):
        result = self.adapter.response_from("chat_completions", {
            "id": "chatcmpl-1", "model": "test",
            "choices": [{"message": {"content": "hi"}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })
        self.assertEqual(result["stop_reason"], "max_tokens")

    def test_finish_reason_tool_calls(self):
        result = self.adapter.response_from("chat_completions", {
            "id": "chatcmpl-1", "model": "test",
            "choices": [{"message": {
                "content": None,
                "tool_calls": [{"id": "call_1", "type": "function",
                                "function": {"name": "get_weather", "arguments": "{}"}}],
            }, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })
        self.assertEqual(result["stop_reason"], "tool_use")
        self.assertIn("content", result)

    def test_finish_reason_content_filter(self):
        result = self.adapter.response_from("chat_completions", {
            "id": "chatcmpl-1", "model": "test",
            "choices": [{"message": {"content": "I cannot"}, "finish_reason": "content_filter"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })
        self.assertEqual(result["stop_reason"], "end_turn")

    def test_tool_calls_response(self):
        result = self.adapter.response_from("chat_completions", {
            "id": "chatcmpl-tc", "model": "test",
            "choices": [{"message": {
                "content": None,
                "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city":"London"}'},
                }],
            }, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })
        self.assertEqual(result["stop_reason"], "tool_use")
        content = result["content"]
        self.assertTrue(any(b["type"] == "tool_use" for b in content))
        tool_block = next(b for b in content if b["type"] == "tool_use")
        self.assertEqual(tool_block["name"], "get_weather")
        self.assertEqual(tool_block["input"], {"city": "London"})

    def test_usage_mapping(self):
        result = self.adapter.response_from("chat_completions", {
            "id": "chatcmpl-u", "model": "test",
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })
        usage = result["usage"]
        self.assertEqual(usage["input_tokens"], 10)
        self.assertEqual(usage["output_tokens"], 5)

    def test_hardcoded_fields(self):
        result = self.adapter.response_from("chat_completions", {
            "id": "chatcmpl-h", "model": "test",
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })
        self.assertEqual(result["type"], "message")
        self.assertEqual(result["role"], "assistant")


class TestMessagesAdapterStreamFrom(unittest.TestCase):
    """MessagesAdapter.stream_from("chat_completions") 等价于旧 create_anthropic_sse_stream。"""

    @classmethod
    def setUpClass(cls):
        cls.adapter = get_adapter("messages")

    def test_stream_produces_sse_format(self):
        """stream_from 产生 SSE 格式输出。"""
        chunk = {
            "id": "chatcmpl-1", "object": "chat.completion.chunk",
            "created": 1234567890, "model": "gpt-4o",
            "choices": [{
                "delta": {"content": "hello", "role": None, "tool_calls": None},
                "finish_reason": None, "index": 0,
            }],
            "usage": None,
        }
        events = list(self.adapter.stream_from("chat_completions", iter([chunk])))
        self.assertGreater(len(events), 0)


if __name__ == "__main__":
    unittest.main()
