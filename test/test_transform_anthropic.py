"""transform_anthropic — Anthropic Messages ↔ Chat Completions 转换测试。"""
import json
import unittest


class TestAnthropicToChat(unittest.TestCase):
    """anthropic_to_chat — Anthropic Messages → Chat Completions 请求转换。"""

    def test_simple_text_message(self):
        """user 角色 + 字符串 content → Chat message。"""
        from transform_anthropic import anthropic_to_chat
        body = {
            "model": "claude-sonnet-4-6",
            "messages": [
                {"role": "user", "content": "Hello, how are you?"}
            ],
            "max_tokens": 4096,
        }
        result = anthropic_to_chat(body, {"target": "qwen3.6-plus", "multimodal": True})
        self.assertEqual(result["model"], "qwen3.6-plus")
        self.assertEqual(len(result["messages"]), 1)
        self.assertEqual(result["messages"][0]["role"], "user")
        self.assertEqual(result["messages"][0]["content"], "Hello, how are you?")
        self.assertEqual(result["max_tokens"], 4096)

    def test_system_string(self):
        """system: 'You are helpful' → messages[0] 为 {role:'system', content:'You are helpful'}"""
        from transform_anthropic import anthropic_to_chat
        body = {
            "model": "claude-sonnet-4-6",
            "system": "You are helpful",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 100,
        }
        result = anthropic_to_chat(body, {"target": "qwen3.6-plus"})
        self.assertEqual(result["messages"][0]["role"], "system")
        self.assertEqual(result["messages"][0]["content"], "You are helpful")
        self.assertEqual(result["messages"][1]["role"], "user")

    def test_system_array(self):
        """system: [{type:'text', text:'part1'}, ...] → \n 连接。"""
        from transform_anthropic import anthropic_to_chat
        body = {
            "model": "claude-sonnet-4-6",
            "system": [
                {"type": "text", "text": "part1"},
                {"type": "text", "text": "part2"},
            ],
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 100,
        }
        result = anthropic_to_chat(body, {"target": "qwen3.6-plus"})
        self.assertEqual(result["messages"][0]["content"], "part1\npart2")

    def test_system_array_filters_empty(self):
        """system block 无 text 字段 → 跳过，不产生空行。"""
        from transform_anthropic import anthropic_to_chat
        body = {
            "model": "claude-sonnet-4-6",
            "system": [
                {"type": "text", "text": "real"},
                {"type": "thinking", "text": "skip"},
            ],
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 100,
        }
        result = anthropic_to_chat(body, {"target": "qwen3.6-plus"})
        self.assertEqual(result["messages"][0]["content"], "real")

    def test_multimodal_image(self):
        """image source base64 → image_url。"""
        from transform_anthropic import anthropic_to_chat
        body = {
            "model": "claude-sonnet-4-6",
            "messages": [
                {"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "abc123"}},
                ]}
            ],
            "max_tokens": 100,
        }
        result = anthropic_to_chat(body, {"target": "qwen3.6-plus", "multimodal": True})
        self.assertEqual(result["messages"][0]["content"][0]["type"], "image_url")
        self.assertEqual(result["messages"][0]["content"][0]["image_url"]["url"], "data:image/png;base64,abc123")

    def test_tool_use_conversion(self):
        """tool_use block → tool_calls[] + arguments 序列化。"""
        from transform_anthropic import anthropic_to_chat
        body = {
            "model": "claude-sonnet-4-6",
            "messages": [
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "call_1", "name": "search", "input": {"query": "hello"}},
                ]}
            ],
            "max_tokens": 100,
        }
        result = anthropic_to_chat(body, {"target": "qwen3.6-plus"})
        tc = result["messages"][0]["tool_calls"][0]
        self.assertEqual(tc["id"], "call_1")
        self.assertEqual(tc["type"], "function")
        self.assertEqual(tc["function"]["name"], "search")
        self.assertEqual(tc["function"]["arguments"], json.dumps({"query": "hello"}))

    def test_tool_result_conversion(self):
        """tool_result → 独立 {role:'tool', tool_call_id, content}。"""
        from transform_anthropic import anthropic_to_chat
        body = {
            "model": "claude-sonnet-4-6",
            "messages": [
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "call_1", "content": "search results"},
                ]}
            ],
            "max_tokens": 100,
        }
        result = anthropic_to_chat(body, {"target": "qwen3.6-plus"})
        self.assertEqual(result["messages"][0]["role"], "tool")
        self.assertEqual(result["messages"][0]["tool_call_id"], "call_1")
        self.assertEqual(result["messages"][0]["content"], "search results")

    def test_tool_result_array_content(self):
        """tool_result content 为数组 → json.dumps。"""
        from transform_anthropic import anthropic_to_chat
        body = {
            "model": "claude-sonnet-4-6",
            "messages": [
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "call_1", "content": [{"key": "val"}]},
                ]}
            ],
            "max_tokens": 100,
        }
        result = anthropic_to_chat(body, {"target": "qwen3.6-plus"})
        self.assertEqual(result["messages"][0]["content"], '[{"key": "val"}]')

    def test_tool_result_null_content(self):
        """tool_result content 为 null → content: ''。"""
        from transform_anthropic import anthropic_to_chat
        body = {
            "model": "claude-sonnet-4-6",
            "messages": [
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "call_1", "content": None},
                ]}
            ],
            "max_tokens": 100,
        }
        result = anthropic_to_chat(body, {"target": "qwen3.6-plus"})
        self.assertEqual(result["messages"][0]["content"], "")

    def test_tool_result_complex_content(self):
        """content 含 image 块等非文本 → 取第一个 type:'text' 块的 text 字段。"""
        from transform_anthropic import anthropic_to_chat
        body = {
            "model": "claude-sonnet-4-6",
            "messages": [
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "call_1", "content": [
                        {"type": "image", "source": {"data": "abc"}},
                        {"type": "text", "text": "extracted text"},
                    ]},
                ]}
            ],
            "max_tokens": 100,
        }
        result = anthropic_to_chat(body, {"target": "qwen3.6-plus"})
        self.assertEqual(result["messages"][0]["content"], "extracted text")

    def test_thinking_discarded(self):
        """消息中的 thinking block → 不出现在 Chat messages 中。"""
        from transform_anthropic import anthropic_to_chat
        body = {
            "model": "claude-sonnet-4-6",
            "messages": [
                {"role": "assistant", "content": [
                    {"type": "thinking", "thinking": "Let me think..."},
                    {"type": "text", "text": "Hello"},
                ]}
            ],
            "max_tokens": 100,
        }
        result = anthropic_to_chat(body, {"target": "qwen3.6-plus"})
        self.assertEqual(len(result["messages"]), 1)
        self.assertEqual(result["messages"][0]["content"], [{"type": "text", "text": "Hello"}])

    def test_o_series_max_completion_tokens(self):
        """o3 模型 → max_completion_tokens，普通模型 → max_tokens。"""
        from transform_anthropic import anthropic_to_chat
        body = {
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 4096,
        }
        result_o = anthropic_to_chat(body, {"target": "o3"})
        self.assertIn("max_completion_tokens", result_o)
        self.assertEqual(result_o["max_completion_tokens"], 4096)
        self.assertNotIn("max_tokens", result_o)

        result_q = anthropic_to_chat(body, {"target": "qwen3.6-plus"})
        self.assertIn("max_tokens", result_q)
        self.assertNotIn("max_completion_tokens", result_q)

    def test_tool_definitions_conversion(self):
        """Anthropic tools → {type:'function', function:{name,description,parameters}}。"""
        from transform_anthropic import anthropic_to_chat
        body = {
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "Search for cats"}],
            "max_tokens": 100,
            "tools": [
                {"name": "search", "description": "Search the web", "input_schema": {"type": "object"}},
            ],
        }
        result = anthropic_to_chat(body, {"target": "qwen3.6-plus"})
        self.assertEqual(len(result["tools"]), 1)
        tool = result["tools"][0]
        self.assertEqual(tool["type"], "function")
        self.assertEqual(tool["function"]["name"], "search")
        self.assertEqual(tool["function"]["description"], "Search the web")
        self.assertEqual(tool["function"]["parameters"], {"type": "object"})

    def test_thinking_to_reasoning_effort_adaptive(self):
        """thinking: {type:'adaptive'} → reasoning_effort: 'xhigh'。"""
        from transform_anthropic import anthropic_to_chat
        body = {
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 100,
            "thinking": {"type": "adaptive"},
        }
        result = anthropic_to_chat(body, {"target": "gpt-5.1"})
        self.assertEqual(result["reasoning_effort"], "xhigh")

    def test_thinking_to_reasoning_effort_budget(self):
        """thinking: {type:'enabled', budget_tokens: 16000} → reasoning_effort: 'high'。"""
        from transform_anthropic import anthropic_to_chat
        body = {
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 100,
            "thinking": {"type": "enabled", "budget_tokens": 16000},
        }
        result = anthropic_to_chat(body, {"target": "gpt-5.1"})
        self.assertEqual(result["reasoning_effort"], "high")

        # 低 budget → low
        body["thinking"]["budget_tokens"] = 2000
        result = anthropic_to_chat(body, {"target": "gpt-5.1"})
        self.assertEqual(result["reasoning_effort"], "low")

        # 中 budget → medium
        body["thinking"]["budget_tokens"] = 8000
        result = anthropic_to_chat(body, {"target": "gpt-5.1"})
        self.assertEqual(result["reasoning_effort"], "medium")

    def test_reasoning_effort_on_gpt5_model(self):
        """目标模型 gpt-5.1 → 注入 reasoning_effort。"""
        from transform_anthropic import anthropic_to_chat
        body = {
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 100,
            "thinking": {"type": "enabled", "budget_tokens": 10000},
        }
        result = anthropic_to_chat(body, {"target": "gpt-5.1"})
        self.assertIn("reasoning_effort", result)

    def test_reasoning_effort_skipped_on_qwen(self):
        """目标模型 qwen3.6-plus → 不注入 reasoning_effort。"""
        from transform_anthropic import anthropic_to_chat
        body = {
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 100,
            "thinking": {"type": "enabled", "budget_tokens": 10000},
        }
        result = anthropic_to_chat(body, {"target": "qwen3.6-plus"})
        self.assertNotIn("reasoning_effort", result)

    def test_tool_choice_auto(self):
        """{type:'auto'} → 'auto'。"""
        from transform_anthropic import anthropic_to_chat
        body = {
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 100,
            "tool_choice": {"type": "auto"},
        }
        result = anthropic_to_chat(body, {"target": "qwen3.6-plus"})
        self.assertEqual(result["tool_choice"], "auto")

    def test_tool_choice_any(self):
        """{type:'any'} → 'required'。"""
        from transform_anthropic import anthropic_to_chat
        body = {
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 100,
            "tool_choice": {"type": "any"},
        }
        result = anthropic_to_chat(body, {"target": "qwen3.6-plus"})
        self.assertEqual(result["tool_choice"], "required")

    def test_tool_choice_tool(self):
        """{type:'tool', name:'x'} → {type:'function', function:{name:'x'}}。"""
        from transform_anthropic import anthropic_to_chat
        body = {
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 100,
            "tool_choice": {"type": "tool", "name": "search"},
        }
        result = anthropic_to_chat(body, {"target": "qwen3.6-plus"})
        self.assertEqual(result["tool_choice"]["type"], "function")
        self.assertEqual(result["tool_choice"]["function"]["name"], "search")

    def test_tool_choice_string_fallback(self):
        """'auto' → 'auto', 'any' → 'required'。"""
        from transform_anthropic import anthropic_to_chat
        body1 = {
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 100,
            "tool_choice": "auto",
        }
        result1 = anthropic_to_chat(body1, {"target": "qwen3.6-plus"})
        self.assertEqual(result1["tool_choice"], "auto")

        body2 = {**body1, "tool_choice": "any"}
        result2 = anthropic_to_chat(body2, {"target": "qwen3.6-plus"})
        self.assertEqual(result2["tool_choice"], "required")

    def test_unknown_fields_not_crash(self):
        """含 output_config.format、context_management、speed 等未知字段不抛异常。"""
        from transform_anthropic import anthropic_to_chat
        body = {
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 100,
            "output_config": {"format": {"type": "text"}},
            "context_management": "auto",
            "speed": "fast",
        }
        try:
            result = anthropic_to_chat(body, {"target": "qwen3.6-plus"})
            self.assertIn("messages", result)
        except Exception as e:
            self.fail(f"anthropic_to_chat 不应抛异常: {e}")

    def test_empty_messages(self):
        """空 messages → Chat messages 不含奇怪数据。"""
        from transform_anthropic import anthropic_to_chat
        body = {
            "model": "claude-sonnet-4-6",
            "messages": [],
            "max_tokens": 100,
        }
        result = anthropic_to_chat(body, {"target": "qwen3.6-plus"})
        self.assertEqual(result["messages"], [])

    def test_cache_control_preserved(self):
        """text block 上的 cache_control → 保留在 output 中。"""
        from transform_anthropic import anthropic_to_chat
        body = {
            "model": "claude-sonnet-4-6",
            "messages": [
                {"role": "user", "content": [
                    {"type": "text", "text": "Hello", "cache_control": {"type": "ephemeral"}},
                ]}
            ],
            "max_tokens": 100,
        }
        result = anthropic_to_chat(body, {"target": "qwen3.6-plus"})
        block = result["messages"][0]["content"][0]
        self.assertIn("cache_control", block)
        self.assertEqual(block["cache_control"], {"type": "ephemeral"})

    def test_stream_options_added(self):
        """stream: true → stream_options: {include_usage: true}。"""
        from transform_anthropic import anthropic_to_chat
        body = {
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 100,
            "stream": True,
        }
        result = anthropic_to_chat(body, {"target": "qwen3.6-plus"})
        self.assertTrue(result["stream"])
        self.assertEqual(result["stream_options"], {"include_usage": True})


if __name__ == "__main__":
    unittest.main()


class TestChatToAnthropic(unittest.TestCase):
    """chat_to_anthropic — Chat Completions → Anthropic Messages 响应转换。"""

    def test_basic_text_response(self):
        """content: 'hello', finish_reason: 'stop' → content: [{type:'text', text:'hello'}], stop_reason: 'end_turn'。"""
        from transform_anthropic import chat_to_anthropic
        response = {
            "id": "chatcmpl-123",
            "model": "qwen3.6-plus",
            "choices": [{
                "message": {"content": "hello"},
                "finish_reason": "stop",
            }],
        }
        result = chat_to_anthropic(response)
        self.assertEqual(result["id"], "chatcmpl-123")
        self.assertEqual(result["type"], "message")
        self.assertEqual(result["role"], "assistant")
        self.assertEqual(result["content"], [{"type": "text", "text": "hello"}])
        self.assertEqual(result["stop_reason"], "end_turn")
        self.assertIsNone(result["stop_sequence"])

    def test_tool_calls_response(self):
        """tool_calls → content: [{type:'tool_use', id, name, input:{...}}]。"""
        from transform_anthropic import chat_to_anthropic
        response = {
            "id": "chatcmpl-456",
            "model": "qwen3.6-plus",
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{
                        "id": "call_abc",
                        "function": {"name": "search", "arguments": '{"query":"cats"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
        }
        result = chat_to_anthropic(response)
        self.assertEqual(len(result["content"]), 1)
        tc = result["content"][0]
        self.assertEqual(tc["type"], "tool_use")
        self.assertEqual(tc["id"], "call_abc")
        self.assertEqual(tc["name"], "search")
        self.assertEqual(tc["input"], {"query": "cats"})
        self.assertEqual(result["stop_reason"], "tool_use")

    def test_refusal_response(self):
        """refusal → content: [{type:'text', text: refusal}]。"""
        from transform_anthropic import chat_to_anthropic
        response = {
            "id": "chatcmpl-789",
            "model": "qwen3.6-plus",
            "choices": [{
                "message": {"content": None, "refusal": "I cannot help with that"},
                "finish_reason": "stop",
            }],
        }
        result = chat_to_anthropic(response)
        texts = [b for b in result["content"] if b["type"] == "text"]
        self.assertEqual(len(texts), 1)
        self.assertEqual(texts[0]["text"], "I cannot help with that")

    def test_finish_reason_stop(self):
        """finish_reason: 'stop' → stop_reason: 'end_turn'。"""
        from transform_anthropic import chat_to_anthropic
        response = {
            "id": "c1", "model": "m",
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
        }
        self.assertEqual(chat_to_anthropic(response)["stop_reason"], "end_turn")

    def test_finish_reason_length(self):
        """finish_reason: 'length' → stop_reason: 'max_tokens'。"""
        from transform_anthropic import chat_to_anthropic
        response = {
            "id": "c1", "model": "m",
            "choices": [{"message": {"content": "hi"}, "finish_reason": "length"}],
        }
        self.assertEqual(chat_to_anthropic(response)["stop_reason"], "max_tokens")

    def test_finish_reason_tool_calls(self):
        """finish_reason: 'tool_calls' → stop_reason: 'tool_use'。"""
        from transform_anthropic import chat_to_anthropic
        response = {
            "id": "c1", "model": "m",
            "choices": [{"message": {"content": None}, "finish_reason": "tool_calls"}],
        }
        self.assertEqual(chat_to_anthropic(response)["stop_reason"], "tool_use")

    def test_finish_reason_content_filter(self):
        """finish_reason: 'content_filter' → stop_reason: 'end_turn'。"""
        from transform_anthropic import chat_to_anthropic
        response = {
            "id": "c1", "model": "m",
            "choices": [{"message": {"content": "hi"}, "finish_reason": "content_filter"}],
        }
        self.assertEqual(chat_to_anthropic(response)["stop_reason"], "end_turn")

    def test_usage_mapping(self):
        """usage: {prompt_tokens, completion_tokens, prompt_tokens_details.cached_tokens} → Anthropic usage。"""
        from transform_anthropic import chat_to_anthropic
        response = {
            "id": "c1", "model": "m",
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "prompt_tokens_details": {"cached_tokens": 20},
            },
        }
        result = chat_to_anthropic(response)
        self.assertEqual(result["usage"]["input_tokens"], 100)
        self.assertEqual(result["usage"]["output_tokens"], 50)
        self.assertEqual(result["usage"]["cache_read_input_tokens"], 20)

    def test_hardcoded_fields(self):
        """type: 'message', role: 'assistant', stop_sequence: null 始终注入。"""
        from transform_anthropic import chat_to_anthropic
        response = {
            "id": "c1", "model": "m",
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
        }
        result = chat_to_anthropic(response)
        self.assertEqual(result["type"], "message")
        self.assertEqual(result["role"], "assistant")
        self.assertIsNone(result["stop_sequence"])

    def test_tool_calls_empty_arguments(self):
        """function.arguments: '' → input: {}（空字符串降级为空 dict）。"""
        from transform_anthropic import chat_to_anthropic
        response = {
            "id": "c1", "model": "m",
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{"id": "call_1", "function": {"name": "fn", "arguments": ""}}],
                },
                "finish_reason": "tool_calls",
            }],
        }
        result = chat_to_anthropic(response)
        self.assertEqual(result["content"][0]["input"], {})

    def test_tool_calls_invalid_arguments_json(self):
        """function.arguments: 'not valid json' → input: {}。"""
        from transform_anthropic import chat_to_anthropic
        response = {
            "id": "c1", "model": "m",
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{"id": "call_1", "function": {"name": "fn", "arguments": "not valid json"}}],
                },
                "finish_reason": "tool_calls",
            }],
        }
        result = chat_to_anthropic(response)
        self.assertEqual(result["content"][0]["input"], {})
