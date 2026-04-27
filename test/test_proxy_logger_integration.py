"""proxy + logger 集成冒烟测试。

使用 mock 方式模拟 proxy handler，注入临时 DB logger，
验证 proxy 转发流程中正确记录了日志。
"""

import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import importlib.util

import request_logger
import token_stats
from request_logger import RequestLogger


def _query_debug_log(db_path, request_id=None):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    if request_id:
        rows = conn.execute(
            "SELECT * FROM debug_log WHERE request_id = ? ORDER BY id", (request_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM debug_log ORDER BY id").fetchall()
    conn.close()
    return rows


def _query_token_stats(db_path, request_id=None):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    if request_id:
        rows = conn.execute(
            "SELECT * FROM token_stats WHERE request_id = ?", (request_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM token_stats ORDER BY id").fetchall()
    conn.close()
    return rows


def _load_proxy():
    spec = importlib.util.spec_from_file_location("proxy_test", Path(__file__).parent.parent / "proxy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _configure(mod):
    mod.CONFIG = {
        "proxy": {"host": "127.0.0.1", "port": 48743},
        "upstream": {
            "base_url": "http://127.0.0.1:4000/",
            "api_key": "test-key",
            "timeout": 120,
            "connect_timeout": 10,
            "ssl_verify": True,
            "retry": 0,
        },
        "model_map": {
            "gpt-4o": {"target": "qwen3.6-plus", "multimodal": True},
            "*": {"target": "qwen3.6-plus", "multimodal": True},
        },
    }


def _make_handler(mod, body: bytes):
    handler = MagicMock()
    handler.rfile = io.BytesIO(body)
    handler.wfile = io.BytesIO()
    hdr = MagicMock()
    hdr.get = lambda k, d=None: {"Content-Length": str(len(body)), "User-Agent": "codex-cli/1.0"}.get(k, d)
    handler.headers = hdr
    # Mock HTTP server response methods (send_response, end_headers etc.)
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    # 绑定真实方法，让代理流程继续执行
    handler._forward_non_streaming = lambda *a, **kw: mod.ProxyHandler._forward_non_streaming(handler, *a, **kw)
    handler._forward_streaming = lambda *a, **kw: mod.ProxyHandler._forward_streaming(handler, *a, **kw)
    sent = {}
    handler._send_json = lambda status, data: sent.update({"status": status, "data": data})
    return handler, sent


class TestJsonParseFailure(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        request_logger._logger = RequestLogger(self.db_path)
        self.mod = _load_proxy()
        _configure(self.mod)

    def tearDown(self):
        request_logger._logger = None
        self.tmpdir.cleanup()

    def test_json_parse_failure_records_raw_request(self):
        body = b"not valid json"
        handler, sent = _make_handler(self.mod, body)

        self.mod.ProxyHandler._handle_responses(handler)

        self.assertEqual(sent["status"], 400)
        logs = _query_debug_log(self.db_path)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["stage"], "raw_request")
        data = json.loads(logs[0]["data"])
        self.assertIn("raw_error", data)


class TestUpstream500Error(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        request_logger._logger = RequestLogger(self.db_path)
        self.mod = _load_proxy()
        _configure(self.mod)

    def tearDown(self):
        request_logger._logger = None
        self.tmpdir.cleanup()

    def test_upstream_500_error(self):
        mock_resp = MagicMock()
        mock_resp.status = 500
        mock_resp.read.return_value = b'{"error": "server error"}'
        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_resp
        mock_conn.sock = MagicMock()

        with patch("http.client.HTTPConnection", return_value=mock_conn):
            body = json.dumps({
                "model": "gpt-4o",
                "input": [{"type": "message", "role": "user", "content": "Hi"}],
            }).encode()
            handler, _ = _make_handler(self.mod, body)
            self.mod.ProxyHandler._handle_responses(handler)

        stages = [r["stage"] for r in _query_debug_log(self.db_path)]
        self.assertIn("raw_request", stages)
        self.assertIn("converted_request", stages)
        self.assertIn("upstream_response", stages)
        self.assertNotIn("converted_response", stages)


class TestFullRequestFlow(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        request_logger._logger = RequestLogger(self.db_path)
        self._orig_token_db = token_stats.DB_PATH
        token_stats.DB_PATH = self.db_path
        self.mod = _load_proxy()
        _configure(self.mod)

    def tearDown(self):
        token_stats.DB_PATH = self._orig_token_db
        request_logger._logger = None
        self.tmpdir.cleanup()

    def test_non_streaming_full_flow(self):
        chat_resp = {
            "id": "chatcmpl-test123",
            "model": "qwen3.6-plus",
            "choices": [{"message": {"content": "Hello"}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150,
                "prompt_tokens_details": {"cached_tokens": 20},
            },
        }
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(chat_resp).encode()
        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_resp
        mock_conn.sock = MagicMock()

        with patch("http.client.HTTPConnection", return_value=mock_conn):
            body = json.dumps({
                "model": "gpt-4o",
                "input": [{"type": "message", "role": "user", "content": "Hi"}],
            }).encode()
            handler, _ = _make_handler(self.mod, body)
            self.mod.ProxyHandler._handle_responses(handler)

        stages = [r["stage"] for r in _query_debug_log(self.db_path)]
        self.assertIn("raw_request", stages)
        self.assertIn("converted_request", stages)
        self.assertIn("upstream_response", stages)
        self.assertIn("converted_response", stages)

        stats = _query_token_stats(self.db_path)
        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0]["input_tokens"], 100)
        self.assertEqual(stats[0]["output_tokens"], 50)
        self.assertEqual(stats[0]["cached_read_tokens"], 20)
        self.assertEqual(stats[0]["status"], "completed")


class TestConversionException(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        request_logger._logger = RequestLogger(self.db_path)
        self.mod = _load_proxy()
        _configure(self.mod)

    def tearDown(self):
        request_logger._logger = None
        self.tmpdir.cleanup()

    def test_converted_response_error_recorded(self):
        """chat_to_responses 抛异常时，有 converted_response 错误补录。"""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({
            "id": "chatcmpl-ok", "model": "qwen3.6-plus",
            "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }).encode()
        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_resp
        mock_conn.sock = MagicMock()

        with patch("http.client.HTTPConnection", return_value=mock_conn):
            with patch.object(self.mod, "chat_to_responses", side_effect=RuntimeError("conversion failed")):
                body = json.dumps({
                    "model": "gpt-4o",
                    "input": [{"type": "message", "role": "user", "content": "Hi"}],
                }).encode()
                handler, _ = _make_handler(self.mod, body)
                self.mod.ProxyHandler._handle_responses(handler)

        stages = [r["stage"] for r in _query_debug_log(self.db_path)]
        self.assertIn("raw_request", stages)
        self.assertIn("converted_request", stages)
        self.assertIn("upstream_response", stages)
        self.assertIn("converted_response", stages)
        cr = [r for r in _query_debug_log(self.db_path) if r["stage"] == "converted_response"][0]
        self.assertIn("error", json.loads(cr["data"]))


class TestStreamingFlow(unittest.TestCase):
    """流式 SSE 场景：验证 _forward_streaming 路径的日志记录。"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        request_logger._logger = RequestLogger(self.db_path)
        self._orig_token_db = token_stats.DB_PATH
        token_stats.DB_PATH = self.db_path
        self.mod = _load_proxy()
        _configure(self.mod)

    def tearDown(self):
        token_stats.DB_PATH = self._orig_token_db
        request_logger._logger = None
        self.tmpdir.cleanup()

    def test_streaming_flow_logs_upstream_and_token_stats(self):
        """流式请求：有 upstream_response + converted_response + token_stats。"""
        sse_events = [
            "event: response.output_item.added\ndata: {\"type\":\"response.output_item.added\"}\n\n",
            "event: response.completed\ndata: {\"response\":{\"output\":[],\"usage\":{\"input_tokens\":100,\"output_tokens\":50,\"input_tokens_details\":{\"cached_tokens\":20}}},\"type\":\"response.completed\"}\n\n",
        ]
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.getheader.return_value = "text/event-stream"
        mock_resp.read.return_value = b""

        with patch("http.client.HTTPConnection") as mock_conn_cls:
            mock_conn = MagicMock()
            mock_conn.getresponse.return_value = mock_resp
            mock_conn.sock = MagicMock()
            mock_conn_cls.return_value = mock_conn

            with patch.object(self.mod, "create_codex_sse_stream", return_value=sse_events):
                body = json.dumps({
                    "model": "gpt-4o",
                    "input": [{"type": "message", "role": "user", "content": "Hi"}],
                    "stream": True,
                }).encode()
                handler, _ = _make_handler(self.mod, body)
                self.mod.ProxyHandler._handle_responses(handler)

        stages = [r["stage"] for r in _query_debug_log(self.db_path)]
        self.assertIn("raw_request", stages)
        self.assertIn("converted_request", stages)
        self.assertIn("upstream_response", stages)
        self.assertIn("converted_response", stages)

        # converted_response 应标记 streaming
        cr = [r for r in _query_debug_log(self.db_path) if r["stage"] == "converted_response"][0]
        data = json.loads(cr["data"])
        self.assertTrue(data["streaming"])

        # token_stats 应有正确的值
        stats = _query_token_stats(self.db_path)
        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0]["input_tokens"], 100)
        self.assertEqual(stats[0]["output_tokens"], 50)
        self.assertEqual(stats[0]["cached_read_tokens"], 20)
        self.assertEqual(stats[0]["status"], "completed")

    def test_streaming_sse_interrupt_no_final_usage(self):
        """SSE 中断场景：无 final_usage → 不记录 token_stats。"""
        sse_events = [
            "data: {\"type\":\"response.output_item.added\"}\n\n",
        ]
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.getheader.return_value = "text/event-stream"
        mock_resp.read.return_value = b""

        with patch("http.client.HTTPConnection") as mock_conn_cls:
            mock_conn = MagicMock()
            mock_conn.getresponse.return_value = mock_resp
            mock_conn.sock = MagicMock()
            mock_conn_cls.return_value = mock_conn

            with patch.object(self.mod, "create_codex_sse_stream", return_value=sse_events):
                body = json.dumps({
                    "model": "gpt-4o",
                    "input": [{"type": "message", "role": "user", "content": "Hi"}],
                    "stream": True,
                }).encode()
                handler, _ = _make_handler(self.mod, body)
                self.mod.ProxyHandler._handle_responses(handler)

        stages = [r["stage"] for r in _query_debug_log(self.db_path)]
        self.assertIn("upstream_response", stages)
        self.assertIn("converted_response", stages)

        # 无 final_usage 时不写 token_stats（之前会写 incomplete，现在只记录成功请求）
        stats = _query_token_stats(self.db_path)
        self.assertEqual(len(stats), 0)


class TestLogWriteFailure(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        logger = RequestLogger(self.db_path)
        request_logger._logger = logger
        self.mod = _load_proxy()
        _configure(self.mod)

    def tearDown(self):
        request_logger._logger = None
        self.tmpdir.cleanup()

    def test_proxy_does_not_crash_on_log_failure(self):
        request_logger._logger._get_conn = lambda: (_ for _ in ()).throw(
            sqlite3.OperationalError("DB locked"))

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({
            "id": "chatcmpl-ok", "model": "qwen3.6-plus",
            "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }).encode()
        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_resp
        mock_conn.sock = MagicMock()

        with patch("http.client.HTTPConnection", return_value=mock_conn):
            body = json.dumps({
                "model": "gpt-4o",
                "input": [{"type": "message", "role": "user", "content": "Hi"}],
            }).encode()
            handler, _ = _make_handler(self.mod, body)
            self.mod.ProxyHandler._handle_responses(handler)


if __name__ == "__main__":
    unittest.main()
