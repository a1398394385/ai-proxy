import unittest
from proxy.transform_router import TransformRouter


class TestTransformRouter(unittest.TestCase):

    def test_known_request_converter(self):
        """已知转换对应返回正确的请求转换。"""
        router = TransformRouter
        result = router.convert_request(
            {"model": "gpt-4", "input": [{"type": "message", "role": "user", "content": "hi"}]},
            client_format="responses",
            upstream_format="chat_completions",
            model_cfg={"target": "gpt-4", "multimodal": False, "upstream": {}},
        )
        self.assertIsInstance(result, dict)
        self.assertIn("messages", result)

    def test_unknown_pair_raises_keyerror(self):
        """未注册的客户端协议抛出 KeyError。"""
        router = TransformRouter
        with self.assertRaises(KeyError):
            router.convert_request(
                {"model": "gpt-4"},
                client_format="no_such_format",
                upstream_format="chat_completions",
                model_cfg={"target": "gpt-4", "multimodal": False, "upstream": {}},
            )

    def test_response_converter_known_pair(self):
        """已知响应转换返回正确结果。"""
        router = TransformRouter
        result = router.convert_response(
            {"id": "1", "choices": [{"message": {"role": "assistant", "content": "ok"}}]},
            upstream_format="chat_completions",
            client_format="responses",
        )
        self.assertIsInstance(result, dict)

    def test_passthrough_no_conversion(self):
        """相同格式直接透传。"""
        body = {"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]}
        result = TransformRouter.convert_request(
            body, "chat_completions", "chat_completions", {}
        )
        self.assertIs(result, body)

        resp = {"id": "1"}
        result2 = TransformRouter.convert_response(resp, "chat_completions", "chat_completions")
        self.assertIs(result2, resp)

    def test_stream_unknown_format_raises_keyerror(self):
        """未知客户端格式的流转换抛出 KeyError。"""
        router = TransformRouter
        with self.assertRaises(KeyError):
            list(router.stream_convert(
                iter([]), upstream_format="chat_completions", client_format="no_such_format",
            ))
