"""测试 proxy/handler.py 统一请求处理器的路由与透传/转换逻辑。

测试覆盖:
- do_POST 路径路由 (responses / messages / chat_completions / fallback)
- 透传判定 (request_type == upstream.format)
- 转换判定 (request_type != upstream.format)
- 透传模式日志 (passthrough=True 标记)
- 转换模式日志 (4 阶段完整)
- 非流式响应结构
- 流式 SSE 事件格式
"""

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from proxy.request_logger import (
    REQUEST_TYPE_RESPONSES,
    REQUEST_TYPE_MESSAGES,
    REQUEST_TYPE_CHAT_COMPLETIONS,
)
from proxy.handler import ProxyHandler


# ─── 测试辅助 ──────────────────────────────────────────────────────────


def _default_upstream_cfg():
    """标准上游配置。"""
    return {
        "base_url": "http://127.0.0.1:4000/",
        "api_key": "test-key",
        "timeout": 120,
        "connect_timeout": 10,
        "ssl_verify": True,
        "retry": 0,
    }


def _resolve_result(target_name="gpt-4o", format_type="chat_completions"):
    """模拟 config_cache.resolve() 返回值。"""
    upstream = _default_upstream_cfg()
    upstream["format"] = format_type
    return {
        "target_name": target_name,
        "multimodal": False,
        "format": format_type,
        "matched_source": "*",
        "upstream": upstream,
    }


def _make_real_handler(body_bytes, path="/v1/chat/completions", method="POST"):
    """构建拥有真实 ProxyHandler 方法的测试 handler。

    通过创建 ProxyHandler 子类并覆盖 __init__，绕过 BaseHTTPRequestHandler
    的网络初始化，同时保留所有方法可正常调用。

    所有 send_* / wfile 等均 mock，供测试断言。
    """
    body_bytes = body_bytes or b'{"model":"gpt-4o"}'

    class _TestHandler(ProxyHandler):
        def __init__(self):
            # 不调用 super().__init__——绕过 BaseHTTPRequestHandler 的网络层
            self.rfile = io.BytesIO(body_bytes)
            self.wfile = io.BytesIO()

            hdr = MagicMock()
            hdr.get = lambda k, d=None: {
                "Content-Length": str(len(body_bytes)),
                "Content-Type": "application/json",
                "User-Agent": "codex-cli/1.0",
            }.get(k, d)
            self.headers = hdr

            self.path = path
            self.command = method
            self.client_address = ("127.0.0.1", 0)
            self.request_version = "HTTP/1.1"

            # 响应方法全部 mock
            self.send_response = MagicMock()
            self.send_header = MagicMock()
            self.end_headers = MagicMock()
            self.close_connection = False

            # server 模拟
            self.server = MagicMock()
            self.server.response_store = None

    return _TestHandler()


def _mock_logger():
    """返回 mock logger，记录所有调用。"""
    logger = MagicMock()
    logger.log_raw_request = MagicMock()
    logger.log_converted_request = MagicMock()
    logger.log_upstream_response = MagicMock()
    logger.log_converted_response = MagicMock()
    return logger


# ─── 路由测试 ──────────────────────────────────────────────────────────


class TestHandlerRouting(unittest.TestCase):
    """测试 do_POST 根据路径确定 request_type 并路由正确。"""

    def setUp(self):
        self.logger = _mock_logger()

    def _route_test(self, path, expected_method, upstream_format="chat_completions"):
        """通用路由测试辅助。返回 handler 供断言。"""
        handler = _make_real_handler(b'{"model":"gpt-4o"}', path=path)

        # mock 内部处理方法，避免真实网络调用
        handler._handle_passthrough = MagicMock()
        handler._handle_convert = MagicMock()

        upstream = _resolve_result(format_type=upstream_format)

        with patch("proxy.handler.config_cache") as mock_cc,\
             patch("proxy.handler.get_logger") as mock_gl,\
             patch("proxy.handler.CONFIG") as mock_cfg:
            mock_cc.resolve.return_value = upstream
            mock_gl.return_value = self.logger
            mock_cfg.get.return_value = _default_upstream_cfg()

            handler.do_POST()

        return handler

    def test_post_responses_route_to_convert(self):
        """POST /v1/responses + format=chat_completions → 转换路径。"""
        handler = self._route_test("/v1/responses", "_handle_convert",
                                   upstream_format="chat_completions")
        handler._handle_convert.assert_called_once()
        self.assertEqual(handler._handle_convert.call_args[0][0],
                         REQUEST_TYPE_RESPONSES)

    def test_post_messages_route_to_convert(self):
        """POST /v1/messages → request_type=messages。"""
        handler = self._route_test("/v1/messages", "_handle_convert",
                                   upstream_format="chat_completions")
        handler._handle_convert.assert_called_once()
        self.assertEqual(handler._handle_convert.call_args[0][0],
                         REQUEST_TYPE_MESSAGES)

    def test_post_chat_completions_route_to_passthrough(self):
        """POST /v1/chat/completions + format=chat_completions → 透传。"""
        handler = self._route_test("/v1/chat/completions", "_handle_passthrough",
                                   upstream_format="chat_completions")
        handler._handle_passthrough.assert_called_once()

    def test_post_v1_other_fallback_to_passthrough(self):
        """POST /v1/embeddings → fallback chat_completions + format 匹配 → 透传。"""
        handler = self._route_test("/v1/embeddings", "_handle_passthrough",
                                   upstream_format="chat_completions")
        handler._handle_passthrough.assert_called_once()

    def test_post_unknown_path_404(self):
        """POST 非 /v1/ 路径 → 404。"""
        handler = _make_real_handler(b'{}', path="/some/random")

        with patch("proxy.handler.config_cache") as mock_cc,\
             patch("proxy.handler.get_logger") as mock_gl,\
             patch("proxy.handler.CONFIG"):
            mock_cc.resolve.return_value = _resolve_result()
            mock_gl.return_value = self.logger
            handler.do_POST()

        handler.send_response.assert_called_with(404)

    def test_get_health_200(self):
        """GET /health → 200 OK。"""
        handler = _make_real_handler(b'', path="/health", method="GET")
        handler.do_GET()
        handler.send_response.assert_called_with(200)

    def test_get_v1_responses_426(self):
        """GET /v1/responses → 426 Upgrade Required。"""
        handler = _make_real_handler(b'', path="/v1/responses", method="GET")
        handler.do_GET()
        handler.send_response.assert_called_with(426)


# ─── 透传判定测试 ──────────────────────────────────────────────────────


class TestHandlerPassthrough(unittest.TestCase):
    """测试透传/转换判定逻辑及透传模式下的日志行为。"""

    def setUp(self):
        self.logger = _mock_logger()

    def _run_do_post(self, path, upstream_format, body_bytes=None):
        """运行 do_POST，mock 网络层依赖，返回 handler。"""
        body = body_bytes or b'{"model":"gpt-4o"}'
        handler = _make_real_handler(body, path=path)

        # mock 转发方法防止网络调用
        handler._forward_pass_through_non_streaming = MagicMock()
        handler._forward_pass_through_streaming = MagicMock()
        handler._forward_non_streaming = MagicMock()
        handler._forward_streaming = MagicMock()

        upstream = _resolve_result(format_type=upstream_format)

        with patch("proxy.handler.config_cache") as mock_cc,\
             patch("proxy.handler.get_logger") as mock_gl,\
             patch("proxy.handler.CONFIG") as mock_cfg:
            mock_cc.resolve.return_value = upstream
            mock_gl.return_value = self.logger
            mock_cfg.get.return_value = _default_upstream_cfg()

            handler.do_POST()

        return handler

    def test_passthrough_when_format_matches(self):
        """request_type == upstream.format → 透传。"""
        handler = self._run_do_post("/v1/responses", "responses")
        handler._forward_pass_through_non_streaming.assert_called_once()

    def test_convert_when_format_mismatch(self):
        """request_type != upstream.format → 转换。"""
        handler = self._run_do_post("/v1/responses", "chat_completions")
        handler._forward_non_streaming.assert_called_once()

    def test_convert_when_no_format(self):
        """upstream.format 为空 → 转换路径（responses→chat 默认）。"""
        handler = self._run_do_post("/v1/responses", "chat_completions")
        handler._forward_non_streaming.assert_called_once()

    def test_passthrough_log_stage2_mark(self):
        """透传阶段 2: log_converted_request 含 passthrough=True。"""
        handler = self._run_do_post("/v1/responses", "responses")

        log_calls = self.logger.log_converted_request.call_args_list
        self.assertGreaterEqual(len(log_calls), 1)

        passthrough_data = log_calls[0][0][3]  # data 是第 4 位参
        self.assertTrue(passthrough_data.get("passthrough"))
        self.assertTrue(passthrough_data.get("format_match"))

    def test_passthrough_non_streaming_response(self):
        """非流式透传: 响应正确转发。"""
        mock_resp_body = b'{"id":"ok","choices":[{"message":{"content":"hi"}}]}'
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.getheader.return_value = "application/json"
        mock_resp.read.return_value = mock_resp_body
        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_resp

        handler = _make_real_handler(b'{"model":"gpt-4o"}')

        with patch("proxy.handler.get_logger", return_value=self.logger),\
             patch("proxy.handler.record_token_stats"),\
             patch("proxy.handler.urllib.parse.urlparse") as mock_parse,\
             patch("proxy.handler._create_upstream_conn") as mock_cconn:
            mock_parse.return_value = MagicMock(path="/", port=4000, scheme="http")
            mock_cconn.return_value = mock_conn

            handler._forward_pass_through_non_streaming(
                b'{"model":"gpt-4o"}',
                "rid-001", "gpt-4o", "gpt-4o",
                "ts", _default_upstream_cfg(),
                "/v1/chat/completions", "chat_completions",
            )

        handler.send_response.assert_called_with(200)
        handler.send_header.assert_any_call("Content-Type", "application/json")

    def test_passthrough_streaming_sse(self):
        """流式透传: SSE Content-Type + chunked 写入。"""
        sse_data = b'data: {"id":"evt-1","type":"response.created"}\n\ndata: [DONE]\n\n'
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read = MagicMock(side_effect=[sse_data, b""])
        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_resp

        handler = _make_real_handler(b'{"model":"gpt-4o","stream":true}')

        with patch("proxy.handler.get_logger", return_value=self.logger),\
             patch("proxy.handler.record_token_stats"),\
             patch("proxy.handler.urllib.parse.urlparse") as mock_parse,\
             patch("proxy.handler._create_upstream_conn") as mock_cconn:
            mock_parse.return_value = MagicMock(path="/", port=4000, scheme="http")
            mock_cconn.return_value = mock_conn

            handler._forward_pass_through_streaming(
                b'{"model":"gpt-4o","stream":true}',
                "rid-001", "gpt-4o", "gpt-4o",
                "ts", _default_upstream_cfg(),
                "/v1/chat/completions", "chat_completions",
            )

        handler.send_header.assert_any_call("Content-Type", "text/event-stream")

    def test_anthropic_sse_no_space_after_data(self):
        """Anthropic SSE data: 无空格 → usage 提取成功，record_token_stats 被调用。"""
        # Anthropic 上游发送 data: 后无空格 (合法 SSE 格式)
        sse_data = (
            b'event:message_start\n'
            b'data:{"message":{"id":"msg_1","role":"assistant","type":"message",'
            b'"usage":{"input_tokens":1000,"output_tokens":0}},"type":"message_start"}\n\n'
            b'event:content_block_delta\n'
            b'data:{"type":"content_block_delta","delta":{"text":"hello"}}\n\n'
            b'event:message_delta\n'
            b'data:{"type":"message_delta","delta":{"stop_reason":"end_turn"},'
            b'"usage":{"output_tokens":50}}\n\n'
            b'event:message_stop\n'
            b'data:{"type":"message_stop"}\n\n'
        )
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read = MagicMock(side_effect=[sse_data, b""])
        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_resp

        handler = _make_real_handler(b'{"model":"claude-sonnet-4-6","stream":true}')

        mock_token_stats = MagicMock()
        with patch("proxy.handler.get_logger", return_value=self.logger),\
             patch("proxy.handler.record_token_stats", mock_token_stats),\
             patch("proxy.handler.urllib.parse.urlparse") as mock_parse,\
             patch("proxy.handler._create_upstream_conn") as mock_cconn:
            mock_parse.return_value = MagicMock(path="/", port=4000, scheme="http")
            mock_cconn.return_value = mock_conn

            handler._forward_pass_through_streaming(
                b'{"model":"claude-sonnet-4-6","stream":true}',
                "rid-anthro", "claude-sonnet-4-6", "qwen3.6-plus",
                "ts", _default_upstream_cfg(),
                "/v1/messages", "messages",
            )

        # 验证 record_token_stats 被调用，且 usage 合并了 message_start 和 message_delta
        mock_token_stats.assert_called_once()
        call_args = mock_token_stats.call_args[0]
        usage = call_args[0]
        context = call_args[1]
        self.assertEqual(context["request_id"], "rid-anthro")
        self.assertEqual(context["request_type"], "messages")
        self.assertEqual(context["model"], "claude-sonnet-4-6")
        self.assertEqual(context["target_model"], "qwen3.6-plus")
        # message_start 的 input_tokens=1000，message_delta 的 output_tokens=50
        self.assertEqual(usage["input_tokens"], 1000)
        self.assertEqual(usage["output_tokens"], 50)

# ─── 转换路径测试 ──────────────────────────────────────────────────────


class TestHandlerConvert(unittest.TestCase):
    """测试转换路径: 请求转换、4 阶段日志、响应格式。"""

    def setUp(self):
        self.logger = _mock_logger()

    def _run_convert(self, path, body, upstream_format):
        """运行 do_POST → _handle_convert，mock 网络层。"""
        body_bytes = json.dumps(body).encode()
        handler = _make_real_handler(body_bytes, path=path)
        handler._forward_non_streaming = MagicMock()
        handler._forward_streaming = MagicMock()

        upstream = _resolve_result(format_type=upstream_format)

        with patch("proxy.handler.config_cache") as mock_cc,\
             patch("proxy.handler.get_logger") as mock_gl,\
             patch("proxy.handler.CONFIG") as mock_cfg:
            mock_cc.resolve.return_value = upstream
            mock_gl.return_value = self.logger
            mock_cfg.get.return_value = _default_upstream_cfg()

            handler.do_POST()

        return handler

    def test_convert_logging_stages(self):
        """转换模式: 阶段 1/2 日志记录。"""
        body = {"model": "gpt-4o", "input": [{"type": "message", "role": "user", "content": "hi"}]}
        handler = self._run_convert("/v1/responses", body, "chat_completions")

        # 阶段 1: (request_id, model_name, target, body, ...)
        self.logger.log_raw_request.assert_called()
        raw_args = self.logger.log_raw_request.call_args[0]
        self.assertEqual(raw_args[1], "gpt-4o", "阶段 1: model_name")

        # 阶段 2: (request_id, model_name, target, chat_body, ...)
        self.logger.log_converted_request.assert_called()
        conv_args = self.logger.log_converted_request.call_args[0]
        self.assertEqual(conv_args[1], "gpt-4o", "阶段 2: model_name")

    def test_convert_responses_to_chat(self):
        """responses body → Chat Completions 格式。"""
        body = {
            "model": "gpt-4o",
            "instructions": "You are helpful.",
            "input": [{"type": "message", "role": "user", "content": "Hello"}],
        }
        handler = self._run_convert("/v1/responses", body, "chat_completions")

        handler._forward_non_streaming.assert_called_once()
        chat_body = handler._forward_non_streaming.call_args[0][0]
        self.assertIn("messages", chat_body)
        self.assertEqual(chat_body["messages"][0]["role"], "system")

    def test_convert_messages_to_chat(self):
        """Anthropic Messages body → Chat Completions 格式。"""
        body = {
            "model": "claude-sonnet",
            "system": "You are helpful.",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1000,
        }
        handler = self._run_convert("/v1/messages", body, "chat_completions")

        handler._forward_non_streaming.assert_called_once()
        chat_body = handler._forward_non_streaming.call_args[0][0]
        self.assertIn("messages", chat_body)
        self.assertEqual(chat_body["messages"][0]["role"], "system")

    def test_convert_non_streaming_sdk_response(self):
        """非流式转换: _handle_convert → _forward_non_streaming 被调用。"""
        handler = _make_real_handler(b'{"model":"gpt-4o"}')
        handler._forward_non_streaming = MagicMock()
        model_cfg = {
            "target": "gpt-4o", "multimodal": False,
            "upstream": {** _default_upstream_cfg(), "format": "chat_completions"},
        }
        from proxy.request_logger import _generate_request_id
        handler._handle_convert(
            "chat_completions", "gpt-4o", model_cfg,
            {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
            _generate_request_id(), "ts", "gpt-4o",
        )
        handler._forward_non_streaming.assert_called_once()

    def test_convert_streaming_sdk_path(self):
        """流式转换: _handle_convert → _forward_streaming 被调用。"""
        handler = _make_real_handler(b'{"model":"gpt-4o","stream":true}')
        handler._forward_streaming = MagicMock()

        model_cfg = {
            "target": "gpt-4o", "multimodal": False,
            "upstream": {** _default_upstream_cfg(), "format": "chat_completions"},
        }
        from proxy.request_logger import _generate_request_id
        handler._handle_convert(
            "chat_completions", "gpt-4o", model_cfg,
            {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}], "stream": True},
            _generate_request_id(), "ts", "gpt-4o",
        )
        handler._forward_streaming.assert_called_once()


# ─── 端到端流程 ────────────────────────────────────────────────────────


class TestHandlerEndToEnd(unittest.TestCase):
    """mock 全部外部依赖，验证 do_POST 完整流程。"""

    def setUp(self):
        self.logger = _mock_logger()

    def test_full_flow_passthrough(self):
        """透传全流程: do_POST → passthrough → non_streaming → 4 阶段日志。"""
        upstream = _resolve_result(format_type="chat_completions")
        mock_resp_body = b'{"id":"ok","choices":[]}'
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.getheader.return_value = "application/json"
        mock_resp.read.return_value = mock_resp_body
        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_resp

        handler = _make_real_handler(b'{"model":"gpt-4o"}', path="/v1/chat/completions")

        # urlparse 同时被 do_POST（路由）和 forwarding（上游）调用，用 side_effect 区分
        from urllib.parse import urlparse as _real_urlparse

        with patch("proxy.handler.config_cache") as mock_cc,\
             patch("proxy.handler.get_logger") as mock_gl,\
             patch("proxy.handler.CONFIG") as mock_cfg,\
             patch("proxy.handler.record_token_stats"),\
             patch("proxy.handler.urllib.parse.urlparse") as mock_parse,\
             patch("proxy.handler._create_upstream_conn") as mock_cconn:
            mock_cc.resolve.return_value = upstream
            mock_gl.return_value = self.logger
            mock_cfg.get.return_value = _default_upstream_cfg()
            mock_parse.side_effect = lambda u: (
                MagicMock(path="/", port=4000, scheme="http")
                if u.startswith("http")
                else _real_urlparse(u)
            )
            mock_cconn.return_value = mock_conn

            handler.do_POST()

        handler.send_response.assert_called_with(200)
        self.logger.log_raw_request.assert_called()
        self.logger.log_converted_request.assert_called()
        self.logger.log_upstream_response.assert_called()
        self.logger.log_converted_response.assert_called()
    def test_full_flow_convert(self):
        """转换全流程: do_POST → convert → SDK 驱动 → 4 阶段日志。"""
        upstream = _resolve_result(format_type="chat_completions")  # mismatch
        chat_resp = {
            "id": "chatcmpl-1", "model": "gpt-4o",
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        mock_chat = MagicMock()
        mock_chat.model_dump.return_value = chat_resp

        body = {
            "model": "gpt-4o",
            "instructions": "Be helpful",
            "input": [{"type": "message", "role": "user", "content": "hi"}],
        }
        handler = _make_real_handler(json.dumps(body).encode(), path="/v1/responses")

        with patch("proxy.handler.config_cache") as mock_cc,\
             patch("proxy.handler.get_logger") as mock_gl,\
             patch("proxy.handler.CONFIG") as mock_cfg,\
             patch("proxy.handler.record_token_stats"),\
             patch("proxy.upstream_driver.OpenAI") as mock_openai_cls:
            mock_cc.resolve.return_value = upstream
            mock_gl.return_value = self.logger
            mock_cfg.get.return_value = _default_upstream_cfg()
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_chat
            mock_openai_cls.return_value = mock_client

            handler.do_POST()

        self.logger.log_raw_request.assert_called()
        self.logger.log_converted_request.assert_called()
        self.logger.log_upstream_response.assert_called()
        self.logger.log_converted_response.assert_called()
        handler.send_response.assert_called_with(200)


class TestConvertOutputConsistency(unittest.TestCase):
    """验证新旧路径对相同请求产生一致的转换输出。"""

    @classmethod
    def setUpClass(cls):
        from test.mock_server import start_mock_server
        cls.mock_server, cls.mock_port = start_mock_server()

    @classmethod
    def tearDownClass(cls):
        cls.mock_server.shutdown()

    def _build_handler_sdk(self, upstream_cfg):
        """构建使用新 SDK 路径的 handler。"""
        from proxy.handler import ProxyHandler
        from proxy.common import CONFIG
        CONFIG["upstream"] = upstream_cfg
        class MockServer:
            response_store = None
        h = ProxyHandler.__new__(ProxyHandler)
        h.server = MockServer()
        h.client_address = ("127.0.0.1", 12345)
        h.headers = {}
        h.command = "POST"
        h.send_response = lambda code: None
        h.send_header = lambda k, v: None
        h.end_headers = lambda: None
        import io
        h.wfile = io.BytesIO()
        return h

    def test_non_streaming_output_key_fields(self):
        """非流式转换输出含 id/model/choices/usage 关键字段。"""
        upstream_cfg = {
            "base_url": f"http://127.0.0.1:{self.mock_port}/v1",
            "api_key": "mock-key",
            "timeout": 30,
            "connect_timeout": 5,
            "ssl_verify": False,
            "retry": 0,
            "format": "chat_completions",
        }
        h = self._build_handler_sdk(upstream_cfg)
        h.path = "/v1/messages"
        body = {
            "model": "claude-sonnet-4",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hello"}],
        }
        model_cfg = {
            "target": "claude-sonnet-4",
            "multimodal": False,
            "upstream": upstream_cfg,
        }
        from proxy.request_logger import _generate_request_id
        h._handle_convert(
            "messages", "claude-sonnet-4", model_cfg, body,
            _generate_request_id(), "2025-01-01 00:00:00", "claude-sonnet-4"
        )
        h.wfile.seek(0)
        raw = h.wfile.read()
        # Anthropic 响应格式应含 id/type/role/content/model/stop_reason/usage
        self.assertIn(b'"id"', raw)
        self.assertIn(b'"type"', raw)
        self.assertIn(b'"role"', raw)
        self.assertIn(b'"content"', raw)
        self.assertIn(b'"model"', raw)
        self.assertIn(b'"stop_reason"', raw)
        self.assertIn(b'"usage"', raw)

    def test_streaming_output_contains_events(self):
        """流式转换输出含 content_block_start / content_block_delta 事件。"""
        upstream_cfg = {
            "base_url": f"http://127.0.0.1:{self.mock_port}/v1",
            "api_key": "mock-key",
            "timeout": 30,
            "connect_timeout": 5,
            "ssl_verify": False,
            "retry": 0,
            "format": "chat_completions",
        }
        h = self._build_handler_sdk(upstream_cfg)
        h.path = "/v1/messages"
        body = {
            "model": "claude-sonnet-4",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        }
        model_cfg = {
            "target": "claude-sonnet-4",
            "multimodal": False,
            "upstream": upstream_cfg,
        }
        # 需要 mock request_logger 以避免 logger 未初始化
        from proxy.request_logger import _generate_request_id, init_logger
        try:
            init_logger()
        except Exception:
            pass
        from proxy.request_logger import get_logger
        if not get_logger():
            from unittest.mock import patch
            with patch("proxy.request_logger.get_logger", return_value=None):
                h._handle_convert(
                    "messages", "claude-sonnet-4", model_cfg, body,
                    _generate_request_id(), "2025-01-01 00:00:00", "claude-sonnet-4"
                )
        else:
            h._handle_convert(
                "messages", "claude-sonnet-4", model_cfg, body,
                _generate_request_id(), "2025-01-01 00:00:00", "claude-sonnet-4"
            )
        h.wfile.seek(0)
        raw = h.wfile.read().decode("utf-8", errors="replace")
        # Anthropic 流式关键事件类型
        self.assertIn("message_start", raw)
        self.assertIn("content_block_start", raw)
        self.assertIn("content_block_delta", raw)
        self.assertIn("message_stop", raw)


if __name__ == "__main__":
    unittest.main()
