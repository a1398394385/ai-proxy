import unittest
from unittest.mock import patch, MagicMock


class TestUpstreamDriver(unittest.TestCase):

    def setUp(self):
        self.cfg = {
            "base_url": "https://test.example.com/v1",
            "api_key": "test-key",
            "timeout": 30,
            "connect_timeout": 5,
            "retry": 1,
            "ssl_verify": True,
            "format": "chat_completions",
        }

    def test_constructor_passes_ssl_verify_to_httpx_client(self):
        """验证 UpstreamDriver 将 ssl_verify 传给 httpx.Client。"""
        import httpx
        from proxy.upstream_driver import UpstreamDriver

        with patch("httpx.Client") as mock_client_cls:
            with patch("proxy.upstream_driver.OpenAI.__init__", return_value=None):
                driver = UpstreamDriver(self.cfg)
                _ = driver.openai
        mock_client_cls.assert_called_once()
        call_kwargs = mock_client_cls.call_args.kwargs
        self.assertTrue(call_kwargs.get("verify"))

    def test_ssl_verify_false_passed_to_httpx_client(self):
        """ssl_verify=False 时 httpx.Client.verify=False。"""
        import httpx
        from proxy.upstream_driver import UpstreamDriver

        cfg = {**self.cfg, "ssl_verify": False}
        with patch("httpx.Client") as mock_client_cls:
            with patch("proxy.upstream_driver.OpenAI.__init__", return_value=None):
                driver = UpstreamDriver(cfg)
                _ = driver.openai
        call_kwargs = mock_client_cls.call_args.kwargs
        self.assertFalse(call_kwargs.get("verify"))

    def test_timeout_separates_connect_and_read(self):
        """httpx.Timeout 使用 connect_timeout 和 timeout 分别设置。"""
        import httpx
        from proxy.upstream_driver import UpstreamDriver

        with patch("httpx.Timeout") as mock_timeout:
            driver = UpstreamDriver(self.cfg)
            _ = driver.openai
        mock_timeout.assert_called_once()
        call_kwargs = mock_timeout.call_args.kwargs
        self.assertEqual(call_kwargs["connect"], 5.0)
        self.assertEqual(call_kwargs["read"], 30.0)

    def test_rejects_unsupported_format(self):
        """不支持的上游格式抛出 ValueError。"""
        from proxy.upstream_driver import UpstreamDriver
        driver = UpstreamDriver(self.cfg)
        with self.assertRaises(ValueError):
            driver.create("unsupported_format", {"model": "test"})

    def test_chat_create_unpacks_dict_to_sdk(self):
        """chat_create 将 dict 解包传给 openai SDK。"""
        from proxy.upstream_driver import UpstreamDriver

        driver = UpstreamDriver(self.cfg)
        mock_create = MagicMock(return_value=MagicMock(model_dump=lambda: {"id": "1"}))
        with patch.object(driver.openai.chat.completions, "create", mock_create):
            driver.chat_create(
                model="gpt-4",
                messages=[{"role": "user", "content": "hi"}],
                temperature=0.7,
            )
        mock_create.assert_called_once_with(
            model="gpt-4",
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.7,
        )

    def test_chat_stream_copies_kwargs_no_side_effect(self):
        """chat_stream 不修改调用者传入的 dict（无副作用）。"""
        from proxy.upstream_driver import UpstreamDriver

        driver = UpstreamDriver(self.cfg)
        original = {"model": "gpt-4", "stream": True, "messages": []}
        saved = dict(original)
        mock_create = MagicMock()
        with patch.object(driver.openai.chat.completions, "create", mock_create):
            driver.chat_stream(**original)
        # original dict 不应被修改
        self.assertEqual(original, saved)

    def test_chat_stream_removes_duplicate_stream_key(self):
        """chat_stream 移除可能重复的 stream key 但不抛 TypeError。"""
        from proxy.upstream_driver import UpstreamDriver

        driver = UpstreamDriver(self.cfg)
        mock_create = MagicMock()
        with patch.object(driver.openai.chat.completions, "create", mock_create):
            driver.chat_stream(
                model="gpt-4",
                messages=[{"role": "user", "content": "hi"}],
                stream=True,
            )
        call_kwargs = mock_create.call_args.kwargs
        self.assertTrue(call_kwargs["stream"])

    def test_close_cleans_up_client(self):
        """close() 关闭底层客户端。"""
        from proxy.upstream_driver import UpstreamDriver
        driver = UpstreamDriver(self.cfg)
        _ = driver.openai
        driver.close()
        self.assertIsNone(driver._openai_client)
