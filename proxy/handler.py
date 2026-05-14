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
from .transform_router import TransformRouter  # noqa: E402
from .transform_responses import _parse_sse_event  # noqa: E402 — v2 流式使用
from .agent_detector import detect_subagent

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

        # 构建完整下游请求 URL
        scheme = self.headers.get("X-Forwarded-Proto", "http")
        host = self.headers.get("Host", "localhost")
        downstream_url = f"{scheme}://{host}{self.path}"

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
                    request_path=downstream_url,
                )
            self._send_json(400, {"error": {"type": "invalid_request_error", "message": str(e)}})
            return

        model_name = body.get("model", "*")
        # Agent 检测：子 agent 请求走 agent_routes 覆盖层
        is_agent = detect_subagent(body)
        if is_agent:
            logging.info(f"检测到子 agent 请求: model={model_name}")
        client_ip = self.client_address[0]
        user_agent = self.headers.get("User-Agent", "")[:120]

        # 日志：记录客户端信息，便于排查来源
        logging.info(
            f"请求: client={client_ip}, ua={user_agent}, "
            f"model={model_name}, type={request_type}, path={self.path}"
        )

        # 解析模型路由：先查 agent 路由覆盖层，未命中或上游禁用则回退主路由
        raw_cfg = None
        if is_agent:
            raw_cfg = config_cache.resolve_agent(model_name, request_type)
        if raw_cfg is None:
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

        # 阶段 1：记录原始请求（完整下游 URL）
        logger = get_logger()
        if logger:
            logger.log_raw_request(request_id, model_name, target, body,
                                   request_type=request_type, request_path=downstream_url, is_agent=is_agent)

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
        # 构建上游完整请求 URL
        forward_path = _normalize_forward_path(self.path)
        if forward_path is None:
            self._send_json(400, {"error": {"type": "invalid_request_error", "message": "无效的请求路径"}})
            return

        upstream_url = upstream_cfg["base_url"].rstrip("/") + forward_path

        logger = get_logger()
        if logger:
            logger.log_converted_request(
                request_id, model_name, target,
                {"passthrough": True, "format_match": True,
                 "reason": f"request_type '{request_type}' 匹配上游 format"},
                request_type=request_type,
                request_path=upstream_url,
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
                            ctx = {
                                "request_id": request_id,
                                "request_type": request_type,
                                "model": model_name,
                                "target_model": target,
                                "request_ts": request_ts,
                                "duration_ms": duration_ms,
                                "response_type": upstream_cfg.get("format", "chat_completions"),
                            }
                            if upstream_cfg.get("id") is not None:
                                ctx["upstream_id"] = upstream_cfg["id"]
                            record_token_stats(usage, ctx)
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

                if final_usage:
                    ctx = {
                        "request_id": request_id,
                        "request_type": request_type,
                        "model": model_name,
                        "target_model": target,
                        "request_ts": request_ts,
                        "duration_ms": duration_ms,
                        "response_type": upstream_cfg.get("format", "chat_completions"),
                    }
                    if upstream_cfg.get("id") is not None:
                        ctx["upstream_id"] = upstream_cfg["id"]
                    record_token_stats(final_usage, ctx)

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

    def _handle_convert(self, client_format, model_name, model_cfg, body,
                        request_id, request_ts, target):
        """转换路径：TransformRouter 路由 → SDK 调上游 → 响应转换。

        client_format: 客户端协议 (responses / messages / chat_completions)
        upstream_format: 上游协议（取自 upstream_cfg.format）
        """
        is_stream = body.get("stream", False)
        upstream_cfg = model_cfg.get("upstream") or CONFIG.get("upstream", {})
        upstream_format = upstream_cfg.get("format", "chat_completions")
        logger = get_logger()

        # 请求格式转换
        try:
            upstream_body = TransformRouter.convert_request(
                body, client_format, upstream_format, model_cfg
            )
        except KeyError:
            logging.error(
                f"不支持的转换对: {client_format} → {upstream_format}"
            )
            self._send_json(400, {
                "error": {
                    "type": "invalid_request_error",
                    "message": f"不支持的格式转换: {client_format} → {upstream_format}"
                }
            })
            return
        except Exception as e:
            logging.exception(f"请求转换失败 ({client_format})")
            if logger:
                logger.log_converted_request(
                    request_id, model_name, target,
                    {"error": str(e)}, request_type=client_format,
                )
            self._send_json(500, {
                "error": {"type": "internal_error", "message": str(e)}
            })
            return

        # 阶段 2：记录转换后的请求
        upstream_url = None
        if upstream_cfg.get("base_url"):
            upstream_url = upstream_cfg["base_url"].rstrip("/") + "/v1/chat/completions"
        if logger:
            logger.log_converted_request(
                request_id, model_name, target, upstream_body,
                request_type=client_format,
                request_path=upstream_url,
            )

        # previous_response_id：仅 responses 路径支持多轮对话
        if client_format == REQUEST_TYPE_RESPONSES:
            prev_id = body.get("previous_response_id")
            if prev_id:
                response_store = getattr(self.server, "response_store", None)
                if response_store is not None:
                    record = response_store.get(prev_id)
                    if record:
                        system_msgs = [
                            m for m in upstream_body["messages"]
                            if m.get("role") == "system"
                        ]
                        non_system_msgs = [
                            m for m in upstream_body["messages"]
                            if m.get("role") != "system"
                        ]
                        upstream_body["messages"] = (
                            system_msgs + record.conversation + non_system_msgs
                        )
                    else:
                        logging.warning(
                            f"previous_response_id={prev_id!r} 不存在或已过期"
                        )

        # 设置回调
        if client_format == REQUEST_TYPE_RESPONSES:
            store_enabled = body.get("store", True)
            is_responses_api = True
        elif client_format == REQUEST_TYPE_MESSAGES:
            store_enabled = False
            is_responses_api = False
        else:
            store_enabled = False
            is_responses_api = False

        logging.info(
            f"转换: model={model_name}, stream={is_stream}, target={target}, "
            f"client={client_format}, upstream={upstream_format}"
        )

        if is_stream:
            self._forward_streaming(
                upstream_body, model_cfg, request_id, model_name, target, request_ts,
                upstream_cfg, client_format, upstream_format,
                store_enabled=store_enabled,
            )
        else:
            self._forward_non_streaming(
                upstream_body, request_id, model_name, target, request_ts,
                upstream_cfg, client_format, upstream_format,
                store_enabled=store_enabled,
                is_responses_api=is_responses_api,
            )

    # ── 转换路径内部方法（v2 SDK 驱动） ──────────────────────────

    def _forward_non_streaming(self, upstream_body, request_id, model, target,
                                 request_ts, upstream_cfg, client_format,
                                 upstream_format, store_enabled=True,
                                 is_responses_api=False):
        """非流式转换请求：SDK 调用 + 响应转换 + Token 统计。"""
        from .upstream_driver import UpstreamDriver

        driver = UpstreamDriver(upstream_cfg)
        logger = get_logger()

        import time as _time
        _request_start = _time.time()
        try:
            raw_response = driver.create(upstream_format, upstream_body)
            chat_response = raw_response.model_dump()
        except Exception as e:
            logging.exception(f"SDK 非流式调用失败: model={model}, target={target}, "
                              f"upstream_format={upstream_format}")
            if logger:
                logger.log_upstream_response(
                    request_id, 0,
                    json.dumps({"error": str(e)}),
                    int((_time.time() - _request_start) * 1000),
                    model, target,
                    request_type=client_format,
                )
            self._handle_sdk_error(e)
            driver.close()
            return
        duration_ms = int((_time.time() - _request_start) * 1000)
        request_ts_for_stats = request_ts

        # 阶段 3：记录上游响应
        if logger:
            logger.log_upstream_response(
                request_id, 200, chat_response, 0,
                model, target,
                request_type=client_format,
            )

        # 阶段 4：转换响应 + Token 统计
        try:
            output = TransformRouter.convert_response(
                chat_response, upstream_format, client_format
            )
            if logger:
                logger.log_converted_response(
                    request_id, model, target, output,
                    request_type=client_format,
                )

            usage = chat_response.get("usage", {})
            if usage:
                ctx = {
                    "request_id": request_id,
                    "request_type": client_format,
                    "model": model,
                    "target_model": target,
                    "request_ts": request_ts_for_stats,
                    "duration_ms": duration_ms,
                }
                if upstream_cfg.get("id") is not None:
                    ctx["upstream_id"] = upstream_cfg["id"]
                record_token_stats(usage, ctx)

        except Exception as e:
            logging.exception("响应转换失败")
            if logger:
                logger.log_converted_response(
                    request_id, model, target,
                    {"error": str(e)}, request_type=client_format,
                )
            self._send_json(500, {
                "error": {"type": "internal_error", "message": str(e)}
            })
            driver.close()
            return

        # 存储 response（仅 responses 路径）
        if store_enabled and is_responses_api:
            from .transform_responses import output_items_to_messages as _oitm
            assistant_msgs = _oitm(output.get("output", []))
            messages_for_conv = [
                m for m in upstream_body.get("messages", [])
                if m.get("role") != "system"
            ] + assistant_msgs
            _store_response(self.server, output, messages_for_conv)

        self._send_json(200, output)
        driver.close()

    def _forward_streaming(self, upstream_body, model_cfg, request_id, model_name,
                             target, request_ts, upstream_cfg, client_format,
                             upstream_format, store_enabled=True):
        """流式转换请求：SDK 流式调用 + TransformRouter 逐事件转换。"""
        from .upstream_driver import UpstreamDriver

        driver = UpstreamDriver(upstream_cfg)
        logger = get_logger()

        import time as _time
        _stream_start = _time.time()
        try:
            stream = driver.create_stream(upstream_format, upstream_body)
        except Exception as e:
            logging.exception(f"SDK 流式调用失败: model={model_name}, target={target}, "
                              f"upstream_format={upstream_format}")
            if logger:
                logger.log_upstream_response(
                    request_id, 0,
                    json.dumps({"error": str(e)}),
                    int((_time.time() - _stream_start) * 1000),
                    model_name, target,
                    request_type=client_format,
                )
            self._handle_sdk_error(e)
            driver.close()
            return

        # 发送 SSE 响应头
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        import time as _time
        start = _time.time()
        SSE_BUFFER_MAX = 200 * 1024
        sse_buffer = []
        sse_buffer_size = 0
        final_usage = None

        try:
            _rstore = (
                getattr(self.server, "response_store", None)
                if store_enabled else None
            )
            for sse_event in TransformRouter.stream_convert(
                stream, upstream_format, client_format,
                request_messages=upstream_body.get("messages") if _rstore else None,
                response_store=_rstore,
            ):
                self.wfile.write(sse_event.encode("utf-8"))
                self.wfile.flush()
                if sse_buffer_size < SSE_BUFFER_MAX:
                    sse_buffer.append(sse_event)
                    sse_buffer_size += len(sse_event)

                # 用 _parse_sse_event 做结构化解析
                if "response.completed" in sse_event or "message_delta" in sse_event:
                    parsed = _parse_sse_event(sse_event)
                    data = parsed.get("data")
                    if data:
                        usage = (
                            data.get("response", {}).get("usage")
                            or data.get("usage")
                        )
                        if usage:
                            final_usage = usage
        except (BrokenPipeError, ConnectionResetError):
            logging.warning("客户端断开连接")
        except Exception as e:
            logging.exception("流式转换异常")
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
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except Exception:
                pass

        duration_ms = int((_time.time() - start) * 1000)
        full_sse = "".join(sse_buffer) if sse_buffer else "(buffer overflow)"

        # 日志
        if logger:
            logger.log_upstream_response(
                request_id, 200, full_sse, duration_ms,
                model_name, target,
                request_type=client_format,
            )
            logger.log_converted_response(
                request_id, model_name, target,
                {"streaming": True, "note": "SDK 流式响应"},
                request_type=client_format,
            )

        # Token 统计
        if final_usage:
            ctx = {
                "request_id": request_id,
                "request_type": client_format,
                "model": model_name,
                "target_model": target,
                "request_ts": request_ts,
                "duration_ms": duration_ms,
            }
            if upstream_cfg.get("id") is not None:
                ctx["upstream_id"] = upstream_cfg["id"]
            record_token_stats(final_usage, ctx)
        else:
            logging.warning(
                f"流式路径未提取到 usage: request_id={request_id}, "
                f"model={model_name}, target={target}"
            )

        driver.close()
        self.close_connection = True

    def _handle_sdk_error(self, e: Exception):
        """统一 SDK 异常 → HTTP 错误映射。

        使用 isinstance 按继承链从具体到通用依次检查，避免遗漏子类。
        """
        import httpx

        logging.exception(f"SDK 调用异常: {type(e).__name__}: {e}")

        try:
            from openai import (
                APIError, APIConnectionError, APITimeoutError,
                RateLimitError, BadRequestError,
            )
            if isinstance(e, APITimeoutError):
                self._send_json(504, {"error": {"type": "timeout_error", "message": str(e)}})
                return
            if isinstance(e, RateLimitError):
                self._send_json(429, {"error": {"type": "rate_limit_error", "message": str(e)}})
                return
            if isinstance(e, BadRequestError):
                self._send_json(400, {"error": {"type": "invalid_request_error", "message": str(e)}})
                return
            if isinstance(e, APIConnectionError):
                self._send_json(502, {"error": {"type": "connection_error", "message": str(e)}})
                return
            if isinstance(e, APIError):
                self._send_json(502, {"error": {"type": "upstream_error", "message": str(e)}})
                return
        except ImportError:
            pass

        if isinstance(e, httpx.HTTPError):
            self._send_json(502, {"error": {"type": "connection_error", "message": str(e)}})
        else:
            self._send_json(502, {"error": {"type": "upstream_error", "message": str(e)}})

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
