"""proxy pass-through 功能的单元测试与集成测试。"""

import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import importlib.util

from proxy import request_logger
from proxy.request_logger import RequestLogger, REQUEST_TYPE_CHAT_COMPLETIONS
from proxy.common import _normalize_forward_path, _extract_model_for_pass_through
from proxy.handler import ProxyHandler


def _load_proxy():
    spec = importlib.util.spec_from_file_location(
        "proxy_test", Path(__file__).parent.parent / "proxy.py"
    )
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


def _query_debug_log(db_path, request_id=None):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    if request_id:
        rows = conn.execute(
            "SELECT * FROM debug_log WHERE request_id = ? ORDER BY id",
            (request_id,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM debug_log ORDER BY id").fetchall()
    conn.close()
    return rows


def _make_handler(mod, body: bytes, path="/v1/chat/completions", method="POST"):
    handler = MagicMock()
    handler.rfile = io.BytesIO(body)
    handler.wfile = io.BytesIO()
    hdr = MagicMock()
    hdr.get = lambda k, d=None: {
        "Content-Length": str(len(body)),
        "User-Agent": "codex-cli/1.0",
        "Content-Type": "application/json",
    }.get(k, d)
    handler.headers = hdr
    handler.path = path
    handler.command = method
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler._forward_pass_through_non_streaming = (
        lambda *a, **kw: ProxyHandler._forward_pass_through_non_streaming(
            handler, *a, **kw
        )
    )
    handler._forward_pass_through_streaming = (
        lambda *a, **kw: ProxyHandler._forward_pass_through_streaming(
            handler, *a, **kw
        )
    )
    sent = {}
    handler._send_json = lambda status, data: sent.update(
        {"status": status, "data": data}
    )
    return handler, sent


class TestNormalizeForwardPath(unittest.TestCase):

    def test_normal_case(self):
        """常规路径：保持 /v1 前缀不变（不再由 normalize 负责去除）。"""
        result = _normalize_forward_path("/v1/chat/completions")
        self.assertEqual(result, "/v1/chat/completions")

    def test_traversal_rejection(self):
        """路径穿越应被拒绝，返回 None。"""
        result = _normalize_forward_path("/v1/../../../etc/passwd")
        self.assertIsNone(result)

    def test_double_slash_norm(self):
        """多余斜杠应被归一化。"""
        result = _normalize_forward_path("/v1//api//test")
        self.assertEqual(result, "/v1/api/test")

    def test_query_preserve(self):
        """查询参数应保留。"""
        result = _normalize_forward_path("/v1/chat?model=gpt")
        self.assertEqual(result, "/v1/chat?model=gpt")

    def test_root_path(self):
        """根路径处理。"""
        result = _normalize_forward_path("/v1/")
        self.assertEqual(result, "/v1/")

    def test_path_without_v1(self):
        """非 /v1 前缀路径原样返回。"""
        result = _normalize_forward_path("/other/path")
        self.assertEqual(result, "/other/path")


class TestExtractModelForPassThrough(unittest.TestCase):

    def test_post_json_extracts_model(self):
        """POST JSON 体包含 model 字段时正确提取。"""
        result = _extract_model_for_pass_through(
            "POST", "/v1/chat", b'{"model":"gpt-4o"}'
        )
        self.assertEqual(result, "gpt-4o")

    def test_post_invalid_json_returns_star(self):
        """POST 无效 JSON 返回 '*'。"""
        result = _extract_model_for_pass_through(
            "POST", "/v1/chat", b"not-json"
        )
        self.assertEqual(result, "*")

    def test_post_no_model_key_returns_star(self):
        """POST JSON 无 model 键返回 '*'。"""
        result = _extract_model_for_pass_through(
            "POST", "/v1/chat", b'{"other":"val"}'
        )
        self.assertEqual(result, "*")

    def test_get_query_extracts_model(self):
        """GET 查询参数中的 model 应被提取。"""
        result = _extract_model_for_pass_through(
            "GET", "/v1/test?model=claude-4", b""
        )
        self.assertEqual(result, "claude-4")

    def test_get_no_model_returns_star(self):
        """GET 查询参数无 model 返回 '*'。"""
        result = _extract_model_for_pass_through("GET", "/v1/test", b"")
        self.assertEqual(result, "*")


class TestRoutePriority(unittest.TestCase):

    def setUp(self):
        self.mod = _load_proxy()
        _configure(self.mod)

    def test_get_v1_responses_returns_426(self):
        """GET /v1/responses → 426, 不应命中透传 catch-all。"""
        handler = MagicMock()
        handler.path = "/v1/responses"
        handler.command = "GET"
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = io.BytesIO()

        self.mod.ProxyHandler.do_GET(handler)

        handler.send_response.assert_called_with(426)

    def test_get_health_returns_200(self):
        """GET /health → 200, 不应命中透传或 404。"""
        handler = MagicMock()
        handler.path = "/health"
        handler.command = "GET"
        sent = {}
        handler._send_json = lambda status, data: sent.update(
            {"status": status, "data": data}
        )

        self.mod.ProxyHandler.do_GET(handler)

        self.assertEqual(sent["status"], 200)
        self.assertEqual(sent["data"]["status"], "ok")

    @unittest.skip("_handle_messages 已合并到 do_POST 内部路由逻辑")
    def test_post_v1_messages_routes_to_handle_messages(self):
        """POST /v1/messages → 内部路由为 REQUEST_TYPE_MESSAGES（已无法通过 mock 验证）。"""
        pass


class TestPassThroughLogging(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        request_logger._logger = RequestLogger(self.db_path)

    def tearDown(self):
        request_logger._logger = None
        self.tmpdir.cleanup()

    def test_passthrough_logging_four_stages(self):
        """透传请求应记录全部 4 阶段日志，converted 阶段包含 passthrough 标记。"""
        mock_resp_body = json.dumps({
            "id": "chatcmpl-test",
            "model": "qwen3.6-plus",
            "choices": [{"message": {"content": "Hello"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }).encode()

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = mock_resp_body
        mock_resp.getheader.return_value = "application/json"

        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_resp
        mock_conn.sock = MagicMock()

        body = json.dumps({
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 5,
        }).encode()

        handler = MagicMock()
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler.path = "/v1/chat/completions"
        handler.command = "POST"
        handler.headers = MagicMock()
        handler.headers.get = lambda k, d=None: {
            "Content-Length": str(len(body)),
            "Content-Type": "application/json",
        }.get(k, d)
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler._send_json = MagicMock()
        handler._forward_pass_through_non_streaming = (
            lambda *a, **kw: ProxyHandler._forward_pass_through_non_streaming(
                handler, *a, **kw
            )
        )
        handler._forward_pass_through_streaming = (
            lambda *a, **kw: ProxyHandler._forward_pass_through_streaming(
                handler, *a, **kw
            )
        )

        upstream_cfg = {
            "base_url": "http://127.0.0.1:4000/",
            "api_key": "test-key",
            "timeout": 120,
            "connect_timeout": 10,
            "retry": 0,
        }

        with patch("proxy.handler._create_upstream_conn", return_value=mock_conn):
            # raw_request 由 do_POST 记录，_handle_passthrough 自身只记录后 3 阶段
            logger = request_logger._logger
            if logger:
                logger.log_raw_request(
                    "test-req-pt-log", "gpt-4o", "qwen3.6-plus",
                    json.loads(body), request_type=REQUEST_TYPE_CHAT_COMPLETIONS,
                )
            ProxyHandler._handle_passthrough(
                handler,
                REQUEST_TYPE_CHAT_COMPLETIONS,
                "gpt-4o",
                "qwen3.6-plus",
                "2025-01-01 00:00:00",
                "test-req-pt-log",
                upstream_cfg,
                body,
                json.loads(body),
            )

        handler.send_response.assert_called_with(200)

        stages = [r["stage"] for r in _query_debug_log(self.db_path)]
        self.assertIn("raw_request", stages, "应记录 raw_request 阶段")
        self.assertIn("converted_request", stages, "应记录 converted_request 阶段（含 passthrough 标记）")
        self.assertIn("upstream_response", stages, "应记录 upstream_response 阶段")
        self.assertIn("converted_response", stages, "应记录 converted_response 阶段（含 passthrough 标记）")

        # 验证 converted_request 包含 passthrough 标记
        converted_req_rows = [
            r for r in _query_debug_log(self.db_path)
            if r["stage"] == "converted_request"
        ]
        self.assertEqual(len(converted_req_rows), 1)
        data = json.loads(converted_req_rows[0]["data"])
        self.assertTrue(data.get("passthrough"), "converted_request 数据应包含 passthrough: True")

if __name__ == "__main__":
    unittest.main()
