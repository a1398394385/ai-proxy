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


if __name__ == "__main__":
    unittest.main()
