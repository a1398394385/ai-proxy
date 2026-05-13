import unittest
from proxy.transform_router import TransformRouter


class TestTransformRouter(unittest.TestCase):

    def test_known_request_converter(self):
        """已知转换对应返回正确的请求转换函数。"""
        from proxy.transform_responses import responses_to_chat
        router = TransformRouter
        result = router.convert_request(
            {"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
            source="responses",
            target="chat_completions",
            model_cfg={"target": "gpt-4", "multimodal": False, "upstream": {}},
        )
        self.assertIsInstance(result, dict)
        self.assertIn("messages", result)

    def test_unknown_pair_raises_keyerror(self):
        """未注册的转换对抛出 KeyError。"""
        router = TransformRouter
        with self.assertRaises(KeyError):
            router.convert_request(
                {"model": "gpt-4"},
                source="no_such_format",
                target="chat_completions",
                model_cfg={"target": "gpt-4", "multimodal": False, "upstream": {}},
            )

    def test_response_converter_known_pair(self):
        """已知响应转换对应返回正确的函数。"""
        from proxy.transform_responses import chat_to_responses
        router = TransformRouter
        result = router.convert_response(
            {"id": "1", "choices": [{"message": {"role": "assistant", "content": "ok"}}]},
            source="chat_completions",
            target="responses",
        )
        self.assertIsInstance(result, dict)

    def test_stream_converter_has_unified_signature(self):
        """流式转换器注册表中所有函数接受 (chunks, *, request_messages, response_store)。"""
        import inspect
        for (source, target), func in TransformRouter._stream_converters.items():
            sig = inspect.signature(func)
            self.assertIn("request_messages", sig.parameters)
            self.assertIn("response_store", sig.parameters)

