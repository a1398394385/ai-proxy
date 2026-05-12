#!/usr/bin/env python3
"""统一请求 Handler — 智能透传/转换路由。

合并 proxy.py 的 ProxyHandler 和 pass_through.py 的 PassThroughHandler：
- 根据路径设置 request_type（responses / messages / chat_completions）
- 对比 request_type 与上游 format → 透传或转换
- 透传路径：chunked 原样转发 + 4 阶段日志 + token_stats
- 转换路径：Chat Completions 中间格式 + 完整转换链
"""

import json
import time
import ssl
import logging
import http.client
import urllib.parse
import socket
from http.server import BaseHTTPRequestHandler

from .common import (
    CONFIG,
    config_cache,
    resolve_model,
    _create_upstream_conn,
    _normalize_forward_path,
)

from .transform import (
    generate_response_id,
    responses_to_chat,
    chat_to_responses,
    create_codex_sse_stream,
    anthropic_to_chat,
    chat_to_anthropic,
    create_anthropic_sse_stream,
    _format_sse_event,
)

from .request_logger import (
    get_logger,
    init_logger as init_request_logger,
    _generate_request_id,
    REQUEST_TYPE_RESPONSES,
    REQUEST_TYPE_MESSAGES,
    REQUEST_TYPE_CHAT_COMPLETIONS,
)

from .token_stats import record_token_stats

# ─── 统一 Handler ──────────────────────────────────────────────────


class ProxyHandler(BaseHTTPRequestHandler):
    """合并 ProxyHandler + PassThroughHandler 的统一请求处理器。

    路由逻辑：
    - POST /v1/responses → request_type = 'responses'
    - POST /v1/messages   → request_type = 'messages'
    - POST /v1/chat/completions → request_type = 'chat_completions'
    - POST /v1/* 其他    → request_type = 'chat_completions'（透传 fallback）
    - GET  /health        → 健康检查
    - GET  /v1/models     → 返回模型列表
    - GET  /v1/responses  → 426 Upgrade Required
    - POST /admin/reload  → 强制刷新配置缓存

    透传判定：
    - request_type == upstream_cfg.format → 直接转发（_handle_passthrough）
    - request_type != upstream_cfg.format → Chat Completions 中间转换（_handle_convert）
    """

    protocol_version = "HTTP/1.1"

    # ── GET ─────────────────────────────────────────────────────

    def do_GET(self):
        if self.path == "/health":
            import os
            self._send_json(200, {"status": "ok", "pid": os.getpid()})
        elif self.path == "/v1/models":
            self._handle_models()
        elif self.path == "/v1/responses":
            # GET /v1/responses → 426 Upgrade Required
            # 设计意图：触发 Codex 回退到 HTTP POST + SSE 模式
            self.send_response(426)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Upgrade Required: Use HTTP POST with SSE")
        else:
            self._send_json(404, {"error": "not found"})

    # ── POST ────────────────────────────────────────────────────

    def do_POST(self):
        """统一路由入口：根据路径确定 request_type，解析模型，判定透传/转换。"""
        # 解析路径
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        # /admin/reload 独立处理
        if path == "/admin/reload":
            self._handle_admin_reload()
            return

        # 路由检测：根据路径设置 request_type
        if path == "/v1/responses" or path == "/v1/responses/compact":
            request_type = REQUEST_TYPE_RESPONSES
        elif path == "/v1/messages":
            request_type = REQUEST_TYPE_MESSAGES
        elif path == "/v1/chat/completions":
            request_type = REQUEST_TYPE_CHAT_COMPLETIONS
        elif path.startswith("/v1/"):
            # /v1/* 其他路径 → 透传 fallback
            request_type = REQUEST_TYPE_CHAT_COMPLETIONS
        else:
            self._send_json(404, {"error": "not found"})
            return

        # 读取请求体
        content_length = int(self.headers.get("Content-Length", 0))
        body_raw = self.rfile.read(content_length)

        # 生成 request_id
        request_id = _generate_request_id()
        request_ts = time.strftime("%Y-%m-%d %H:%M:%S")

        # 解析 JSON body
        try:
            body = json.loads(body_raw)
        except json.JSONDecodeError as e:
            logging.error(f"JSON 解析失败: {e}")
            logger = get_logger()
            if logger:
                logger.log_raw_request(
                    request_id,
                    body_raw.decode("utf-8", errors="replace")[:50],
                    "?",
                    {"raw_error": str(e), "raw_body": body_raw.decode("utf-8", errors="replace")[:5000]},
                    request_type=request_type,
                )
            self._send_json(400, {"error": {"type": "invalid_request_error", "message": str(e)}})
            return

        model_name = body.get("model", "*")

        # 解析模型路由：先查 config_cache.resolve 获取完整信息（含 format）
        raw_cfg = config_cache.resolve(model_name, request_type)
        if raw_cfg is None:
            model_cfg = {"target": model_name, "multimodal": False}
            upstream_cfg = CONFIG.get("upstream", {})
            upstream_format = ""
        else:
            model_cfg = {
                "target": raw_cfg["target_name"],
                "multimodal": bool(raw_cfg["multimodal"]),
                "upstream": raw_cfg["upstream"],
            }
            upstream_cfg = model_cfg["upstream"]
            upstream_format = raw_cfg.get("format", "")

        target = model_cfg["target"]

        if not upstream_cfg:
            logging.error(f"模型 {model_name} 无法解析上游配置")
            self._send_json(500, {"error": {"type": "internal_error", "message": "模型路由不可用"}})
            return

        # 阶段 1：记录原始请求
        logger = get_logger()
        if logger:
            logger.log_raw_request(request_id, model_name, target, body, request_type=request_type)

        # 透传/转换判定
        if request_type == upstream_format and upstream_format:
            # 透传路径：request_type 与上游 format 匹配
            self._handle_passthrough(
                request_type, model_name, target, request_ts, request_id,
                upstream_cfg, body_raw, body
            )
        else:
            # 转换路径：走 Chat Completions 中间格式
            self._handle_convert(
                request_type, model_name, model_cfg, body, request_id, request_ts, target
            )

    # ── 辅助路由 ─────────────────────────────────────────────────

    def _handle_models(self):
        """返回动态配置中所有非 * 的源模型列表。"""
        routes = config_cache.get_all()
        models = [k for k in routes if k != "*"]
        self._send_json(200, {"data": [{"id": m, "object": "model"} for m in models]})

    def _handle_admin_reload(self):
        """重新加载动态配置。仅允许本地请求。"""
        client_ip = self.client_address[0]
        if client_ip not in ("127.0.0.1", "::1"):
            self._send_json(403, {"error": "forbidden", "message": "仅允许本地请求"})
            return

        try:
            config_cache.reload()
            logging.info(f"配置已重载 (来自 {client_ip})")
            self._send_json(200, {
                "status": "ok",
                "reloaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
        except Exception as e:
            logging.exception("配置重载失败")
            self._send_json(500, {"status": "error", "message": str(e)})

    # ── 透传路径 (_handle_passthrough) ──────────────────────────

    def _handle_passthrough(self, request_type, model_name, target, request_ts,
                            request_id, upstream_cfg, body_raw, body):
        """透传路径：原样转发原始 body 到上游。

        与 PassThroughHandler._handle_pass_through() 对应，
        增加阶段 2/4 日志 + token_stats 调用。
        """
        # 阶段 2：记录"透传"标记
        logger = get_logger()
        if logger:
            logger.log_converted_request(
                request_id, model_name, target,
                {"passthrough": True, "format_match": True,
                 "reason": f"request_type '{request_type}' 匹配上游 format"},
                request_type=request_type,
            )

        # 替换 body 中的 model 名称为 target（如路由有映射）
        if target != model_name and body_raw:
            try:
                new_body = json.loads(body_raw)
                if new_body.get("model") == model_name:
                    new_body["model"] = target
                    body_raw = json.dumps(new_body).encode("utf-8")
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

        # 归一化转发路径
        forward_path = _normalize_forward_path(self.path)
        if forward_path is None:
            self._send_json(400, {"error": {"type": "invalid_request_error", "message": "无效的请求路径"}})
            return

        # 检测 stream 模式
        is_stream = False
        try:
            req_body = json.loads(body_raw)
            is_stream = req_body.get("stream", False)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

        logging.info(f"透传: model={model_name}, stream={is_stream}, target={target}, path={forward_path}")

        if is_stream:
            self._forward_pass_through_streaming(
                body_raw, request_id, model_name, target, request_ts,
                upstream_cfg, forward_path, request_type
            )
        else:
            self._forward_pass_through_non_streaming(
                body_raw, request_id, model_name, target, request_ts,
                upstream_cfg, forward_path, request_type
            )

    def _forward_pass_through_non_streaming(self, body_raw, request_id, model_name,
                                             target, request_ts, upstream_cfg,
                                             forward_path, request_type):
        """非流式透传：原样转发请求到上游，原样返回响应。"""
        base_url = upstream_cfg["base_url"]
        api_key = upstream_cfg["api_key"]
        timeout = upstream_cfg.get("timeout", 120)
        connect_timeout = upstream_cfg.get("connect_timeout", 10)
        retries = upstream_cfg.get("retry", 0) + 1

        parsed = urllib.parse.urlparse(base_url)
        path = parsed.path.rstrip("/") + forward_path
        port = parsed.port or (80 if parsed.scheme == "http" else 443)
        content_type = self.headers.get("Content-Type", "application/json")

        for attempt in range(retries):
            conn = None
            try:
                conn = _create_upstream_conn(upstream_cfg, parsed, port)
                # 用 connect_timeout 建立连接后，切换到完整 timeout
                conn.connect()
                conn.sock.settimeout(timeout)

                headers = {"Content-Type": content_type}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                conn.request(self.command, path, body=body_raw, headers=headers)

                start = time.time()
                resp = conn.getresponse()
                resp_body = resp.read()
                duration_ms = int((time.time() - start) * 1000)
                conn.close()
                conn = None

                if resp.status >= 500 and attempt < retries - 1:
                    logging.warning(f"透传上游 {resp.status}，重试 {attempt + 1}/{retries}")
                    continue

                # 阶段 3：记录上游响应
                logger = get_logger()
                if logger:
                    log_data = resp_body.decode("utf-8", errors="replace")[:5000]
                    logger.log_upstream_response(
                        request_id, resp.status, log_data, duration_ms,
                        model_name, target,
                        request_type=request_type,
                    )
                if logger:
                    logger.log_converted_response(
                        request_id, model_name, target,
                        {"passthrough": True},
                        request_type=request_type,
                    )

                # Token 统计（透传路径）
                if resp.status == 200:
                    try:
                        chat_response = json.loads(resp_body)
                        usage = chat_response.get("usage", {})
                        if usage:
                            record_token_stats(usage, {
                                "request_id": request_id,
                                "request_type": request_type,
                                "model": model_name,
                                "target_model": target,
                                "request_ts": request_ts,
                                "duration_ms": duration_ms,
                            })
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        logging.warning("透传: 响应非 JSON，无法提取 usage")

                self.send_response(resp.status)
                self.send_header("Content-Type", resp.getheader("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(resp_body)))
                self.end_headers()
                self.wfile.write(resp_body)
                return

            except (socket.timeout, http.client.HTTPException, OSError) as e:
                logging.warning(f"透传上游请求失败 (attempt {attempt + 1}): {e}")
                if attempt < retries - 1:
                    continue
                self._send_json(502, {"error": {"type": "server_error", "message": str(e)}})
                return
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

    def _forward_pass_through_streaming(self, body_raw, request_id, model_name,
                                         target, request_ts, upstream_cfg,
                                         forward_path, request_type):
        """流式 SSE 透传：逐 chunk 原样中继，不注入代理事件。"""
        base_url = upstream_cfg["base_url"]
        api_key = upstream_cfg["api_key"]
        timeout = upstream_cfg.get("timeout", 120)
        connect_timeout = upstream_cfg.get("connect_timeout", 10)
        retries = upstream_cfg.get("retry", 0) + 1

        parsed = urllib.parse.urlparse(base_url)
        path = parsed.path.rstrip("/") + forward_path
        port = parsed.port or (80 if parsed.scheme == "http" else 443)
        content_type = self.headers.get("Content-Type", "application/json")

        start = 0
        upstream_status = None
        headers_sent = False

        for attempt in range(retries):
            conn = None
            try:
                conn = _create_upstream_conn(upstream_cfg, parsed, port)
                # 用 connect_timeout 建立连接后，切换到完整 timeout
                conn.connect()
                conn.sock.settimeout(timeout)

                headers = {
                    "Content-Type": content_type,
                    "Accept": "text/event-stream",
                    "Connection": "close",
                }
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                conn.request(self.command, path, body=body_raw, headers=headers)

                # 先读取上游响应状态，确认成功后再发送头部给客户端
                resp = conn.getresponse()
                upstream_status = resp.status
                start = time.time()

                if upstream_status != 200:
                    # 上游返回错误 → 直接转发错误响应（非流式）
                    error_body = resp.read()
                    self.send_response(upstream_status)
                    self.send_header("Content-Type", resp.getheader("Content-Type", "application/json"))
                    self.send_header("Content-Length", str(len(error_body)))
                    self.end_headers()
                    self.wfile.write(error_body)
                    if logger:
                        logger.log_upstream_response(
                            request_id, upstream_status,
                            error_body.decode("utf-8", errors="replace")[:5000],
                            0, model_name, target,
                            request_type=request_type,
                        )
                    return

                # 上游返回 200 → 确认发送流式头部给客户端
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Accel-Buffering", "no")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                headers_sent = True
                sse_buffer = []
                final_usage = None

                buf = b""
                final_usage = None
                done_received = False
                while True:
                    chunk = resp.read(4096)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n\n" in buf:
                        event_raw, buf = buf.split(b"\n\n", 1)
                        event_bytes = event_raw + b"\n\n"
                        self._write_chunk(event_bytes)
                        self.wfile.flush()
                        sse_buffer.append(event_bytes)

                        # OpenAI [DONE] 或 Anthropic message_stop 均视为流结束
                        if b"data: [DONE]" in event_raw or b"message_stop" in event_raw:
                            done_received = True
                            break
                        if b'"usage"' in event_raw:
                            try:
                                for line in event_raw.split(b"\n"):
                                    if line.startswith(b"data:"):
                                        json_str = line[5:]  # 去掉 "data:" 前缀
                                        if json_str.startswith(b" "):
                                            json_str = json_str[1:]  # 去掉可选的前导空格
                                        data_json = json.loads(json_str)
                                        usage = data_json.get("usage") or data_json.get("message", {}).get("usage")
                                        if usage:
                                            if final_usage:
                                                final_usage.update(usage)
                                            else:
                                                final_usage = dict(usage)
                            except (json.JSONDecodeError, UnicodeDecodeError):
                                pass
                    else:
                        continue
                    break

                if buf and not done_received:
                    self._write_chunk(buf)
                    self.wfile.flush()
                    sse_buffer.append(buf)

                # chunked 终止块
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()

                duration_ms = int((time.time() - start) * 1000)
                full_sse = b"".join(sse_buffer).decode("utf-8", errors="replace")[:5000]

                # 阶段 3：记录上游响应
                logger = get_logger()
                if logger:
                    logger.log_upstream_response(
                        request_id, upstream_status, full_sse, duration_ms,
                        model_name, target,
                        request_type=request_type,
                    )

                # 阶段 4：记录"透传"标记
                if logger:
                    logger.log_converted_response(
                        request_id, model_name, target,
                        {"passthrough": True, "streaming": True},
                        request_type=request_type,
                    )

                # Token 统计（透传流式路径）
                if final_usage:
                    record_token_stats(final_usage, {
                        "request_id": request_id,
                        "request_type": request_type,
                        "model": model_name,
                        "target_model": target,
                        "request_ts": request_ts,
                        "duration_ms": duration_ms,
                    })

                self.close_connection = True
                return

            except (socket.timeout, http.client.HTTPException, OSError) as e:
                logging.warning(f"透传流式上游请求失败 (attempt {attempt + 1}): {e}")
                if attempt < retries - 1:
                    continue
                logger = get_logger()
                if logger:
                    logger.log_upstream_response(
                        request_id, 0,
                        json.dumps({"error": str(e)}),
                        int((time.time() - start) * 1000),
                        model_name, target,
                        request_type=request_type,
                    )
                if not headers_sent:
                    self._send_json(502, {"error": {"type": "server_error", "message": str(e)}})
                else:
                    try:
                        error_event = f"data: {{\"type\":\"error\",\"error\":{{\"type\":\"server_error\",\"message\":\"{str(e)}\"}}}}\n\n".encode("utf-8")
                        self._write_chunk(error_event)
                        self.wfile.write(b"0\r\n\r\n")
                        self.wfile.flush()
                    except (BrokenPipeError, OSError):
                        pass
                return
            except Exception as e:
                logging.exception(f"透传流式失败: {e}")
                logger = get_logger()
                if logger:
                    logger.log_upstream_response(
                        request_id, upstream_status or 0,
                        json.dumps({"error": str(e)}),
                        int((time.time() - start) * 1000),
                        model_name, target,
                        request_type=request_type,
                    )
                if not headers_sent:
                    self._send_json(500, {"error": {"type": "server_error", "message": str(e)}})
                else:
                    try:
                        error_event = f"data: {{\"type\":\"error\",\"error\":{{\"type\":\"server_error\",\"message\":\"{str(e)}\"}}}}\n\n".encode("utf-8")
                        self._write_chunk(error_event)
                        self.wfile.write(b"0\r\n\r\n")
                        self.wfile.flush()
                    except (BrokenPipeError, OSError):
                        pass
                return
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

    # ── 转换路径 (_handle_convert) ───────────────────────────────

    def _handle_convert(self, request_type, model_name, model_cfg, body,
                        request_id, request_ts, target):
        """转换路径：请求 → Chat Completions 中间格式 → 上游 → 响应格式转换。

        合并 proxy.py 的 _handle_responses 和 _handle_messages 逻辑。
        """
        is_stream = body.get("stream", False)
        upstream_cfg = model_cfg.get("upstream") or CONFIG.get("upstream", {})
        logger = get_logger()

        # 请求格式转换
        try:
            if request_type == REQUEST_TYPE_RESPONSES:
                chat_body = responses_to_chat(body, model_cfg)
            elif request_type == REQUEST_TYPE_MESSAGES:
                chat_body = anthropic_to_chat(body, model_cfg)
            else:
                # chat_completions — 无需转换
                chat_body = body
        except Exception as e:
            logging.exception(f"请求转换失败 ({request_type})")
            if logger:
                logger.log_converted_request(
                    request_id, model_name, target,
                    {"error": str(e)}, request_type=request_type,
                )
            self._send_json(500, {"error": {"type": "internal_error", "message": str(e)}})
            return

        # 阶段 2：记录转换后的请求
        if logger:
            logger.log_converted_request(
                request_id, model_name, target, chat_body, request_type=request_type,
            )

        # previous_response_id：仅 responses 路径支持多轮对话
        if request_type == REQUEST_TYPE_RESPONSES:
            prev_id = body.get("previous_response_id")
            if prev_id:
                response_store = getattr(self.server, "response_store", None)
                if response_store is not None:
                    record = response_store.get(prev_id)
                    if record:
                        system_msgs = [m for m in chat_body["messages"] if m.get("role") == "system"]
                        non_system_msgs = [m for m in chat_body["messages"] if m.get("role") != "system"]
                        chat_body["messages"] = system_msgs + record.conversation + non_system_msgs
                    else:
                        logging.warning(f"previous_response_id={prev_id!r} 不存在或已过期，忽略历史")

        # 设置转换回调
        if request_type == REQUEST_TYPE_RESPONSES:
            response_converter = chat_to_responses
            sse_stream_factory = create_codex_sse_stream
            store_enabled = body.get("store", True)
            is_responses_api = True
        elif request_type == REQUEST_TYPE_MESSAGES:
            response_converter = chat_to_anthropic
            sse_stream_factory = create_anthropic_sse_stream
            store_enabled = False
            is_responses_api = False
        else:
            # chat_completions — 无需响应转换
            response_converter = None
            sse_stream_factory = None
            store_enabled = False
            is_responses_api = False

        logging.info(f"转换: model={model_name}, stream={is_stream}, target={target}, "
                     f"request_type={request_type}")

        if is_stream:
            self._forward_streaming(
                chat_body, model_cfg, request_id, model_name, target, request_ts,
                response_converter=response_converter,
                sse_stream_factory=sse_stream_factory,
                store_enabled=store_enabled,
                upstream_cfg=upstream_cfg,
                request_type=request_type,
            )
        else:
            self._forward_non_streaming(
                chat_body, request_id, model_name, target, request_ts,
                response_converter=response_converter,
                store_enabled=store_enabled,
                is_responses_api=is_responses_api,
                upstream_cfg=upstream_cfg,
                request_type=request_type,
            )

    # ── 转换路径内部方法 ─────────────────────────────────────────

    def _forward_non_streaming(self, chat_body, request_id, model, target, request_ts,
                                response_converter=None, store_enabled=True,
                                is_responses_api=False, upstream_cfg=None,
                                request_type=None):
        """非流式：转发到上游，转换响应，返回。

        response_converter: callable, chat_response -> format_response
        is_responses_api: True 时存入 ResponseStore（防止误触发）
        """
        if upstream_cfg is None:
            upstream_cfg = CONFIG.get("upstream", {})
        if response_converter is None:
            from .transform_responses import chat_to_responses as response_converter
        base_url = upstream_cfg["base_url"]
        api_key = upstream_cfg["api_key"]
        timeout = upstream_cfg.get("timeout", 120)
        connect_timeout = upstream_cfg.get("connect_timeout", 10)
        retries = upstream_cfg.get("retry", 0) + 1

        parsed = urllib.parse.urlparse(base_url)
        path = parsed.path.rstrip("/") + "/chat/completions"
        port = parsed.port or (80 if parsed.scheme == "http" else 443)
        use_ssl = parsed.scheme == "https"
        ssl_ctx = ssl.create_default_context() if upstream_cfg.get("ssl_verify", True) else ssl._create_unverified_context()

        for attempt in range(retries):
            conn = None
            try:
                conn = _create_upstream_conn(upstream_cfg, parsed, port)
                # 用 connect_timeout 建立连接后，切换到完整 timeout
                conn.connect()
                conn.sock.settimeout(timeout)

                conn.request("POST", path, body=json.dumps(chat_body), headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                })

                start = time.time()
                resp = conn.getresponse()
                resp_body = resp.read()
                duration_ms = int((time.time() - start) * 1000)
                conn.close()
                conn = None

                if resp.status >= 500 and attempt < retries - 1:
                    logging.warning(f"上游 {resp.status}，重试 {attempt + 1}/{retries}")
                    continue

                if resp.status != 200:
                    logger = get_logger()
                    if logger:
                        logger.log_upstream_response(
                            request_id, resp.status,
                            resp_body.decode("utf-8", errors="replace"),
                            duration_ms, model, target,
                            request_type=request_type,
                        )
                    self.send_response(resp.status)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(resp_body)
                    return

                # 阶段 3：记录上游响应
                try:
                    chat_response = json.loads(resp_body)
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    chat_response = {"error": str(e), "raw": resp_body.decode("utf-8", errors="replace")[:5000]}

                logger = get_logger()
                if logger:
                    logger.log_upstream_response(
                        request_id, resp.status, chat_response, duration_ms,
                        model, target,
                        request_type=request_type,
                    )

                # 阶段 4：转换响应 + Token 统计
                try:
                    responses_response = response_converter(chat_response)
                    if logger:
                        logger.log_converted_response(
                            request_id, model, target, responses_response,
                            request_type=request_type,
                        )

                    usage = chat_response.get("usage", {})
                    if usage:
                        record_token_stats(usage, {
                            "request_id": request_id,
                            "request_type": request_type,
                            "model": model,
                            "target_model": target,
                            "request_ts": request_ts,
                            "duration_ms": duration_ms,
                        })
                except Exception as e:
                    logging.exception("响应转换失败")
                    if logger:
                        logger.log_converted_response(
                            request_id, model, target,
                            {"error": str(e)}, request_type=request_type,
                        )
                    self._send_json(500, {"error": {"type": "internal_error", "message": str(e)}})
                    return

                # 存储 response（仅 responses 路径）
                if store_enabled and is_responses_api:
                    from .transform_responses import output_items_to_messages as _oitm
                    assistant_msgs = _oitm(responses_response.get("output", []))
                    messages_for_conv = [
                        m for m in chat_body.get("messages", []) if m.get("role") != "system"
                    ] + assistant_msgs
                    _store_response(self.server, responses_response, messages_for_conv)

                self._send_json(200, responses_response)
                return

            except (socket.timeout, http.client.HTTPException, OSError) as e:
                logging.warning(f"上游请求失败 (attempt {attempt + 1}): {e}")
                if attempt < retries - 1:
                    continue
                self._send_json(500, {"error": {"type": "server_error", "message": str(e)}})
                return
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

    def _forward_streaming(self, chat_body, model_cfg, request_id, model, target, request_ts,
                            response_converter=None, sse_stream_factory=None,
                            store_enabled=True, upstream_cfg=None, request_type=None):
        """流式：直连上游 SSE，通过 sse_stream_factory 转换后逐事件返回。"""
        if sse_stream_factory is None:
            from .transform_responses import create_codex_sse_stream as sse_stream_factory
        if upstream_cfg is None:
            upstream_cfg = CONFIG.get("upstream", {})
        base_url = upstream_cfg["base_url"]
        api_key = upstream_cfg["api_key"]
        timeout = upstream_cfg.get("timeout", 120)
        connect_timeout = upstream_cfg.get("connect_timeout", 10)
        ssl_verify = upstream_cfg.get("ssl_verify", True)

        parsed = urllib.parse.urlparse(base_url)
        path = parsed.path.rstrip("/") + "/chat/completions"
        port = parsed.port or (80 if parsed.scheme == "http" else 443)
        use_ssl = parsed.scheme == "https"
        ssl_ctx = ssl.create_default_context() if ssl_verify else ssl._create_unverified_context()

        conn = _create_upstream_conn(upstream_cfg, parsed, port)
        # 用 connect_timeout 建立连接后，切换到完整 timeout
        conn.connect()
        conn.sock.settimeout(timeout)

        conn.request("POST", path, body=json.dumps(chat_body), headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        })

        # 设置 SSE 响应头
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()

        start = time.time()
        sse_buffer = []
        final_usage = None
        upstream_status = None

        try:
            try:
                resp = conn.getresponse()
                upstream_status = resp.status

                ct = resp.getheader("Content-Type", "")
                if resp.status != 200:
                    error_event = _format_sse_event("response.failed", {
                        "response": {
                            "id": generate_response_id(),
                            "status": "failed",
                            "output": [],
                            "status_details": {
                                "error": {
                                    "type": "server_error",
                                    "message": f"Upstream returned HTTP {resp.status}",
                                },
                            },
                        },
                    })
                    self.wfile.write(error_event.encode("utf-8"))
                    self.wfile.flush()
                    completed_event = _format_sse_event("response.completed", {
                        "response": {
                            "id": generate_response_id(),
                            "status": "failed",
                            "output": [],
                            "usage": {
                                "input_tokens": 0,
                                "output_tokens": 0,
                                "total_tokens": 0,
                                "input_tokens_details": {"cached_tokens": 0},
                                "output_tokens_details": {},
                            },
                        },
                    })
                    self.wfile.write(completed_event.encode("utf-8"))
                    self.wfile.flush()
                    try:
                        self.wfile.write(b"data: [DONE]\n\n")
                        self.wfile.flush()
                    except (BrokenPipeError, OSError):
                        pass
                    logger = get_logger()
                    if logger:
                        logger.log_upstream_response(
                            request_id, resp.status,
                            resp.read().decode("utf-8", errors="replace"),
                            0, model, target,
                            request_type=request_type,
                        )
                    return

                if "text/event-stream" not in ct:
                    logging.warning(f"上游返回非 SSE Content-Type: {ct}")
                    error_event = _format_sse_event("response.failed", {
                        "response": {
                            "id": generate_response_id(),
                            "status": "failed",
                            "output": [],
                            "status_details": {
                                "error": {
                                    "type": "server_error",
                                    "message": f"Upstream returned non-SSE Content-Type: {ct}",
                                },
                            },
                        },
                    })
                    self.wfile.write(error_event.encode("utf-8"))
                    self.wfile.flush()
                    completed_event = _format_sse_event("response.completed", {
                        "response": {
                            "id": generate_response_id(),
                            "status": "failed",
                            "output": [],
                            "usage": {
                                "input_tokens": 0,
                                "output_tokens": 0,
                                "total_tokens": 0,
                                "input_tokens_details": {"cached_tokens": 0},
                                "output_tokens_details": {},
                            },
                        },
                    })
                    self.wfile.write(completed_event.encode("utf-8"))
                    self.wfile.flush()
                    try:
                        self.wfile.write(b"data: [DONE]\n\n")
                        self.wfile.flush()
                    except (BrokenPipeError, OSError):
                        pass
                    logger = get_logger()
                    if logger:
                        logger.log_upstream_response(
                            request_id, upstream_status,
                            resp.read().decode("utf-8", errors="replace"),
                            0, model, target,
                            request_type=request_type,
                        )
                    return

                # 核心：通过 sse_stream_factory 逐事件转换并发送
                _rstore = getattr(self.server, "response_store", None) if store_enabled else None
                if _rstore is not None:
                    stream_gen = sse_stream_factory(
                        resp,
                        request_messages=chat_body.get("messages"),
                        response_store=_rstore,
                    )
                else:
                    stream_gen = sse_stream_factory(resp)
                for sse_event in stream_gen:
                    self.wfile.write(sse_event.encode("utf-8"))
                    self.wfile.flush()
                    sse_buffer.append(sse_event)
                    if "response.completed" in sse_event:
                        try:
                            data = json.loads(sse_event.split("data: ", 1)[1])
                            final_usage = data.get("response", {}).get("usage")
                        except (json.JSONDecodeError, IndexError):
                            pass
            except Exception as e:
                logging.exception("流式转发异常")
                upstream_status = getattr(e, 'code', None) or getattr(e, 'status', None) or 502
                try:
                    error_event = _format_sse_event("response.failed", {
                        "response": {
                            "id": generate_response_id(),
                            "status": "failed",
                            "output": [],
                            "status_details": {
                                "error": {
                                    "type": "server_error",
                                    "message": str(e),
                                },
                            },
                        },
                    })
                    self.wfile.write(error_event.encode("utf-8"))
                    self.wfile.flush()
                    completed_event = _format_sse_event("response.completed", {
                        "response": {
                            "id": generate_response_id(),
                            "status": "failed",
                            "output": [],
                            "usage": {
                                "input_tokens": 0,
                                "output_tokens": 0,
                                "total_tokens": 0,
                                "input_tokens_details": {"cached_tokens": 0},
                                "output_tokens_details": {},
                            },
                        },
                    })
                    self.wfile.write(completed_event.encode("utf-8"))
                    self.wfile.flush()
                    try:
                        self.wfile.write(b"data: [DONE]\n\n")
                        self.wfile.flush()
                    except (BrokenPipeError, OSError):
                        pass
                except Exception:
                    pass
                logger = get_logger()
                if logger:
                    logger.log_upstream_response(
                        request_id, upstream_status,
                        json.dumps({"error": {"type": "server_error", "message": str(e)}}),
                        int((time.time() - start) * 1000),
                        model, target,
                        request_type=request_type,
                    )

            duration_ms = int((time.time() - start) * 1000)
            full_sse = "".join(sse_buffer)

            try:
                self.wfile.close()
            except Exception:
                pass

            logger = get_logger()
            if logger:
                logger.log_upstream_response(
                    request_id, upstream_status, full_sse, duration_ms,
                    model, target,
                    request_type=request_type,
                )
                logger.log_converted_response(
                    request_id, model, target,
                    {"streaming": True, "note": "SSE 流式响应，无 converted_response"},
                    request_type=request_type,
                )
                if final_usage:
                    record_token_stats(final_usage, {
                        "request_id": request_id,
                        "request_type": request_type,
                        "model": model,
                        "target_model": target,
                        "request_ts": request_ts,
                        "duration_ms": duration_ms,
                    })
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # ── 工具方法 ─────────────────────────────────────────────────

    def _write_chunk(self, data: bytes) -> None:
        """写一个 chunked 编码块。"""
        if data:
            self.wfile.write(f"{len(data):X}\r\n".encode())
            self.wfile.write(data)
            self.wfile.write(b"\r\n")

    def _send_json(self, status_code: int, data: dict):
        """发送 JSON 响应。"""
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        """重定向到 logger。"""
        logging.info("%s - - [%s] %s" % (
            self.client_address[0],
            self.log_date_time_string(),
            format % args,
        ))


# ─── 存储辅助 ──────────────────────────────────────────────────────


def _store_response(server, responses_response: dict, messages_for_conv: list):
    """将 responses_response 存入 server.response_store（如已挂载）。

    messages_for_conv: 已包含完整对话历史的消息列表（调用方负责构建，含 assistant 输出；
                       不包含 system，避免多轮时重复叠加）。
    """
    response_store = getattr(server, "response_store", None)
    if response_store is None:
        return
    from .response_store import ResponseRecord as _RR
    output = responses_response.get("output", [])
    record = _RR(
        response_id=responses_response.get("id", ""),
        model=responses_response.get("model", ""),
        output=output,
        conversation=messages_for_conv,
        usage=responses_response.get("usage", {}),
        status=responses_response.get("status", "completed"),
        created_at=time.time(),
        expires_at=time.time() + response_store.ttl_seconds,
    )
    response_store.put(record.response_id, record)
