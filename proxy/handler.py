#!/usr/bin/env python3
"""统一请求 Handler — 智能透传/转换路由。

合并 proxy.py 的 ProxyHandler 和 pass_through.py 的 PassThroughHandler：
- 根据路径设置 request_type（responses / messages / chat_completions）
- 对比 request_type 与上游 format → 透传或转换
- 透传路径：chunked 原样转发 + 4 阶段日志 + token_stats
- 转换路径：Chat Completions 中间格式 + 完整转换链
"""

import json
import re
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
from .transform.router import TransformRouter  # noqa: E402
from .sse_utils import _parse_sse_event  # noqa: E402 — v2 流式使用
from .agent_detector import detect_subagent


def _extract_session_id(body: dict, request_type: str) -> str | None:
    """从客户端请求中提取 session_id。

    - Responses: body["prompt_cache_key"]
    - Anthropic: json.loads(body["metadata"]["user_id"])["session_id"]
    """
    try:
        if request_type == "responses":
            return body.get("prompt_cache_key") or None
        if request_type == "messages":
            user_id_raw = (body.get("metadata") or {}).get("user_id")
            if user_id_raw:
                user_id = json.loads(user_id_raw) if isinstance(user_id_raw, str) else user_id_raw
                return user_id.get("session_id") or None
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass
    return None


# ── 上游路径映射 ──
# 各 format 对应的 API 后缀（不含版本路径）
_SUFFIX_MAP = {
    "chat_completions": "/chat/completions",
    "responses": "/responses",
    "messages": "/messages",
}


def _build_upstream_path(parsed_base, upstream_format):
    """构建上游请求路径，自动检测 base_url 中的 /vX 版本后缀。

    若 base_url 路径末尾已包含 /vX（X 为数字），直接拼接 API 后缀；
    否则使用默认 /v1 作为版本前缀。
    例如:
      base_url=https://api.openai.com/v1  → /v1/chat/completions
      base_url=https://glm.api.com/v4    → /v4/chat/completions
      base_url=https://api.example.com   → /v1/chat/completions
    """
    base_path = parsed_base.path.rstrip("/")
    suffix = _SUFFIX_MAP.get(upstream_format, "/chat/completions")

    if re.search(r'/v\d+$', base_path):
        return base_path + suffix
    else:
        return base_path + "/v1" + suffix


def _build_passthrough_path(parsed_base, forward_path):
    """构建透传上游请求路径，避免 /vX 版本路径重复拼接。

    若 base_url 路径末尾的 /vX 与 forward_path 开头的 /vX/ 一致，
    则从 forward_path 中去掉该版本前缀。
    """
    base_path = parsed_base.path.rstrip("/")
    m = re.search(r'/(v\d+)$', base_path)
    if m:
        version = m.group(1)
        prefix = "/" + version + "/"
        if forward_path.startswith(prefix):
            forward_path = forward_path[len(prefix) - 1:]  # 保留开头的 /
    return base_path + forward_path

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
        elif self.path.startswith("/admin/key-status"):
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            upstream_id = qs.get("upstream_id", [None])[0]
            if not upstream_id:
                self._send_json(400, {"error": "缺少 upstream_id 参数"})
                return
            try:
                upstream_id = int(upstream_id)
            except (ValueError, TypeError):
                self._send_json(400, {"error": "upstream_id 必须为整数"})
                return
            status_data = config_cache.get_key_status(upstream_id)
            self._send_json(200, status_data)
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
        if path == "/v1/responses" or path.startswith("/v1/responses/"):
            request_type = REQUEST_TYPE_RESPONSES
        elif path == "/v1/messages" or path.startswith("/v1/messages/"):
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
        session_id = _extract_session_id(body, request_type)
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

        # 解析模型路由：直线路由 → agent 路由覆盖层 → 主路由
        raw_cfg = None
        # 1. 直线路由（最高优先级，匹配"上游名/模型名"前缀）
        raw_cfg = config_cache.resolve_direct(model_name)
        if raw_cfg:
            logging.info(f"直线路由: upstream_id={raw_cfg['upstream'].get('id','?')}, "
                         f"model={raw_cfg['target_name']}")
        # 2. 子代理路由
        if raw_cfg is None and is_agent:
            raw_cfg = config_cache.resolve_agent(model_name, request_type)
        # 3. 路由表（精确匹配 → * fallback）
        if raw_cfg is None:
            raw_cfg = config_cache.resolve(model_name, request_type)

        if raw_cfg is None:
            logging.debug(f"直线路由未命中: model={model_name}")
            err_msg = f"模型 {model_name} 不可用（无匹配路由）"
            logging.error(err_msg)
            self._send_json(500, {
                "error": {"type": "internal_error", "message": err_msg}
            })
            return

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
            client_headers = dict(self.headers)
            logger.log_raw_request(request_id, model_name, target, body,
                                   request_type=request_type, request_path=downstream_url,
                                   session_id=session_id, is_agent=is_agent,
                                   headers=client_headers)

        # 透传/转换判定
        if request_type == upstream_format and upstream_format:
            # 透传路径：request_type 与上游 format 匹配
            self._handle_passthrough(
                request_type, model_name, target, request_ts, request_id,
                upstream_cfg, body_raw, body, session_id
            )
        else:
            # 转换路径：走 Chat Completions 中间格式
            self._handle_convert(
                request_type, model_name, model_cfg, body, request_id, request_ts, target, session_id
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
                            request_id, upstream_cfg, body_raw, body, session_id=None):
        """透传路径：原样转发原始 body 到上游。

        与 PassThroughHandler._handle_pass_through() 对应，
        增加阶段 2/4 日志 + token_stats 调用。
        """
        # 构建上游完整请求 URL
        forward_path = _normalize_forward_path(self.path)
        if forward_path is None:
            self._send_json(400, {"error": {"type": "invalid_request_error", "message": "无效的请求路径"}})
            return

        _parsed = urllib.parse.urlparse(upstream_cfg["base_url"])
        upstream_url = f"{_parsed.scheme}://{_parsed.netloc}{_build_passthrough_path(_parsed, forward_path)}"

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
                upstream_cfg, forward_path, request_type, session_id,
                upstream_url=upstream_url,
            )
        else:
            self._forward_pass_through_non_streaming(
                body_raw, request_id, model_name, target, request_ts,
                upstream_cfg, forward_path, request_type, session_id,
                upstream_url=upstream_url,
            )

    def _forward_pass_through_non_streaming(self, body_raw, request_id, model_name,
                                             target, request_ts, upstream_cfg,
                                             forward_path, request_type, session_id=None,
                                             upstream_url=None):
        """非流式透传：原样转发请求到上游，原样返回响应。"""
        base_url = upstream_cfg["base_url"]
        api_key = config_cache.pick_key(upstream_cfg["id"])
        if not api_key and upstream_cfg.get("id") in config_cache._upstream_has_any_key:
            logging.warning(f"[proxy] 上游 {upstream_cfg.get('name', upstream_cfg['id'])} 有 key 记录但全部被禁用")
        timeout = upstream_cfg.get("timeout", 120)
        connect_timeout = upstream_cfg.get("connect_timeout", 10)
        retries = upstream_cfg.get("retry", 0) + 1

        parsed = urllib.parse.urlparse(base_url)
        path = _build_passthrough_path(parsed, forward_path)
        port = parsed.port or (80 if parsed.scheme == "http" else 443)
        content_type = self.headers.get("Content-Type", "application/json")

        for attempt in range(retries):
            conn = None
            try:
                conn = _create_upstream_conn(upstream_cfg, parsed, port)

                headers = {"Content-Type": content_type}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                logger = get_logger()
                if logger:
                    logger.log_converted_request(
                        request_id, model_name, target,
                        {"passthrough": True, "format_match": True,
                         "reason": f"request_type '{request_type}' 匹配上游 format"},
                        request_type=request_type,
                        request_path=upstream_url,
                        headers=headers,
                    )
                # 用 connect_timeout 建立连接后，切换到完整 timeout
                conn.connect()
                conn.sock.settimeout(timeout)

                conn.request(self.command, path, body=body_raw, headers=headers)

                start = time.time()
                resp = conn.getresponse()
                resp_body = resp.read()
                duration_ms = int((time.time() - start) * 1000)
                conn.close()
                conn = None

                error_body_str = resp_body.decode("utf-8", errors="replace")
                if resp.status >= 500 and attempt < retries - 1:
                    logging.warning(f"透传上游 {resp.status}，重试 {attempt + 1}/{retries}: {error_body_str[:500]}")
                    continue

                # 上游返回非 200 → 打印错误到 proxy.log
                if resp.status != 200:
                    up_name = upstream_cfg.get("name", upstream_cfg.get("id", "?"))
                    logging.error(f"上游返回错误: upstream={up_name}, model={model_name}, "
                                  f"target_model={target}, status={resp.status}, body={error_body_str[:2000]}")
                if resp.status == 429:
                    config_cache.mark_cooldown(
                        upstream_cfg["id"],
                        api_key,
                        upstream_cfg.get("key_cooldown_secs", 60)
                    )
                    logging.warning(f"[proxy] 上游 {upstream_cfg.get('name', upstream_cfg['id'])} key ****{api_key[-4:] if len(api_key)>4 else api_key} 触发 429，冷却 {upstream_cfg.get('key_cooldown_secs', 60)}s")

                # 阶段 3：记录上游响应
                logger = get_logger()
                if logger:
                    log_data = error_body_str[:5000]
                    logger.log_upstream_response(
                        request_id, resp.status, log_data, duration_ms,
                        model_name, target,
                        request_type=request_type,
                        headers=dict(resp.getheaders()),
                    )
                if logger:
                    logger.log_converted_response(
                        request_id, model_name, target,
                        {"passthrough": True},
                        request_type=request_type,
                        headers={
                            "Content-Type": resp.getheader("Content-Type", "application/json"),
                            "Content-Length": str(len(resp_body)),
                        },
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
                            if session_id:
                                ctx["session_id"] = session_id
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
                if attempt < retries - 1:
                    logging.warning(f"透传上游请求失败，重试 {attempt + 1}/{retries}: {e}")
                    continue
                logging.error(f"透传上游请求失败（重试耗尽）: model={model_name}, target={target}, err={e}")
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
                                         forward_path, request_type, session_id=None,
                                         upstream_url=None):
        """流式 SSE 透传：逐 chunk 原样中继，不注入代理事件。"""
        base_url = upstream_cfg["base_url"]
        api_key = config_cache.pick_key(upstream_cfg["id"])
        if not api_key and upstream_cfg.get("id") in config_cache._upstream_has_any_key:
            logging.warning(f"[proxy] 上游 {upstream_cfg.get('name', upstream_cfg['id'])} 有 key 记录但全部被禁用")
        timeout = upstream_cfg.get("timeout", 120)
        connect_timeout = upstream_cfg.get("connect_timeout", 10)
        retries = upstream_cfg.get("retry", 0) + 1

        parsed = urllib.parse.urlparse(base_url)
        path = _build_passthrough_path(parsed, forward_path)
        port = parsed.port or (80 if parsed.scheme == "http" else 443)
        content_type = self.headers.get("Content-Type", "application/json")

        logger = get_logger()
        start = 0
        upstream_status = None
        headers_sent = False

        for attempt in range(retries):
            conn = None
            try:
                conn = _create_upstream_conn(upstream_cfg, parsed, port)

                headers = {
                    "Content-Type": content_type,
                    "Accept": "text/event-stream",
                    "Connection": "close",
                }
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                logger = get_logger()
                if logger:
                    logger.log_converted_request(
                        request_id, model_name, target,
                        {"passthrough": True, "format_match": True,
                         "reason": f"request_type '{request_type}' 匹配上游 format"},
                        request_type=request_type,
                        request_path=upstream_url,
                        headers=headers,
                    )
                # 用 connect_timeout 建立连接后，切换到完整 timeout
                conn.connect()
                conn.sock.settimeout(timeout)

                conn.request(self.command, path, body=body_raw, headers=headers)

                # 先读取上游响应状态，确认成功后再发送头部给客户端
                resp = conn.getresponse()
                upstream_status = resp.status
                start = time.time()
                logger = get_logger()

                if upstream_status != 200:
                    # 上游返回错误 → 直接转发错误响应（非流式）
                    error_body = resp.read()
                    error_body_str = error_body.decode("utf-8", errors="replace")
                    up_name = upstream_cfg.get("name", upstream_cfg.get("id", "?"))
                    logging.error(f"流式透传上游返回错误: upstream={up_name}, "
                                  f"model={model_name}, target_model={target}, "
                                  f"status={upstream_status}, body={error_body_str[:2000]}")
                    if upstream_status == 429:
                        config_cache.mark_cooldown(
                            upstream_cfg["id"],
                            api_key,
                            upstream_cfg.get("key_cooldown_secs", 60)
                        )
                        logging.warning(f"[proxy] 上游 {upstream_cfg.get('name', upstream_cfg['id'])} key ****{api_key[-4:] if len(api_key)>4 else api_key} 触发 429，冷却 {upstream_cfg.get('key_cooldown_secs', 60)}s")
                    self.send_response(upstream_status)
                    self.send_header("Content-Type", resp.getheader("Content-Type", "application/json"))
                    self.send_header("Content-Length", str(len(error_body)))
                    self.end_headers()
                    self.wfile.write(error_body)
                    logger = get_logger()
                    if logger:
                        logger.log_upstream_response(
                            request_id, upstream_status,
                            error_body_str[:5000],
                            0, model_name, target,
                            request_type=request_type,
                            headers=dict(resp.getheaders()),
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
                        headers=dict(resp.getheaders()),
                    )

                # 阶段 4：记录"透传"标记
                if logger:
                    logger.log_converted_response(
                        request_id, model_name, target,
                        {"passthrough": True, "streaming": True},
                        request_type=request_type,
                        headers={
                            "Content-Type": "text/event-stream",
                            "Cache-Control": "no-cache",
                            "X-Accel-Buffering": "no",
                            "Transfer-Encoding": "chunked",
                        },
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
                    if session_id:
                        ctx["session_id"] = session_id
                    record_token_stats(final_usage, ctx)

                self.close_connection = True
                return

            except (socket.timeout, http.client.HTTPException, OSError) as e:
                if attempt < retries - 1:
                    logging.warning(f"透传流式上游请求失败，重试 {attempt + 1}/{retries}: {e}")
                    continue
                logging.error(f"透传流式上游请求失败（重试耗尽）: model={model_name}, target={target}, err={e}")
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
                        request_id, request_ts, target, session_id=None):
        """转换路径：TransformRouter 路由 → SDK 调上游 → 响应转换。

        client_format: 客户端协议 (responses / messages / chat_completions)
        upstream_format: 上游协议（取自 upstream_cfg.format）
        """
        is_stream = body.get("stream", False)
        upstream_cfg = model_cfg.get("upstream") or {}
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
            _parsed = urllib.parse.urlparse(upstream_cfg["base_url"])
            upstream_url = f"{_parsed.scheme}://{_parsed.netloc}{_build_upstream_path(_parsed, upstream_format)}"
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
                store_enabled=store_enabled, session_id=session_id,
                upstream_url=upstream_url,
            )
        else:
            self._forward_non_streaming(
                upstream_body, request_id, model_name, target, request_ts,
                upstream_cfg, client_format, upstream_format,
                store_enabled=store_enabled,
                is_responses_api=is_responses_api, session_id=session_id,
                upstream_url=upstream_url,
            )

    # ── 转换路径内部方法（v2 SDK 驱动） ──────────────────────────

    def _forward_non_streaming(self, upstream_body, request_id, model, target,
                                 request_ts, upstream_cfg, client_format,
                                 upstream_format, store_enabled=True,
                                 is_responses_api=False, session_id=None,
                                 upstream_url=None):
        """非流式：http.client 连上游 → 响应转换。"""
        base_url = upstream_cfg["base_url"]
        api_key = config_cache.pick_key(upstream_cfg["id"])
        if not api_key and upstream_cfg.get("id") in config_cache._upstream_has_any_key:
            logging.warning(f"[proxy] 上游 {upstream_cfg.get('name', upstream_cfg['id'])} 有 key 记录但全部被禁用")
        timeout = upstream_cfg.get("timeout", 120)
        retries = upstream_cfg.get("retry", 0) + 1
        logger = get_logger()

        parsed = urllib.parse.urlparse(base_url)
        path = _build_upstream_path(parsed, upstream_format)
        port = parsed.port or (80 if parsed.scheme == "http" else 443)

        for attempt in range(retries):
            conn = None
            try:
                conn = _create_upstream_conn(upstream_cfg, parsed, port)

                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                }
                logger = get_logger()
                if logger:
                    logger.log_converted_request(
                        request_id, model, target, upstream_body,
                        request_type=client_format,
                        request_path=upstream_url,
                        headers=headers,
                    )
                conn.connect()
                conn.sock.settimeout(timeout)

                conn.request("POST", path, body=json.dumps(upstream_body), headers=headers)

                start = time.time()
                resp = conn.getresponse()
                resp_body = resp.read()
                duration_ms = int((time.time() - start) * 1000)
                conn.close()
                conn = None

                if resp.status >= 500 and attempt < retries - 1:
                    resp_body_str = resp_body.decode("utf-8", errors="replace")
                    if logger:
                        logger.log_upstream_response(
                            request_id, resp.status,
                            resp_body_str,
                            duration_ms, model, target,
                            request_type=client_format,
                            headers=dict(resp.getheaders()),
                        )
                    logging.warning(f"上游 {resp.status}，重试 {attempt + 1}/{retries}: {resp_body_str[:500]}")
                    continue

                if resp.status != 200:
                    resp_body_str = resp_body.decode("utf-8", errors="replace")
                    logging.error(f"转换上游返回错误: model={model}, status={resp.status}, "
                                  f"body={resp_body_str[:2000]}")
                    if resp.status == 429:
                        config_cache.mark_cooldown(
                            upstream_cfg["id"],
                            api_key,
                            upstream_cfg.get("key_cooldown_secs", 60)
                        )
                        logging.warning(f"[proxy] 上游 {upstream_cfg.get('name', upstream_cfg['id'])} key ****{api_key[-4:] if len(api_key)>4 else api_key} 触发 429，冷却 {upstream_cfg.get('key_cooldown_secs', 60)}s")
                    if logger:
                        logger.log_upstream_response(
                            request_id, resp.status,
                            resp_body_str,
                            duration_ms, model, target,
                            request_type=client_format,
                            headers=dict(resp.getheaders()),
                        )
                    self.send_response(resp.status)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(resp_body)
                    return

                try:
                    chat_response = json.loads(resp_body)
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    chat_response = {"error": str(e), "raw": resp_body.decode("utf-8", errors="replace")[:5000]}

                if logger:
                    logger.log_upstream_response(
                        request_id, resp.status, chat_response, duration_ms,
                        model, target,
                        request_type=client_format,
                        headers=dict(resp.getheaders()),
                    )

                try:
                    from .transform.router import TransformRouter
                    output = TransformRouter.convert_response(
                        chat_response, upstream_format, client_format
                    )
                    if logger:
                        logger.log_converted_response(
                            request_id, model, target, output,
                            request_type=client_format,
                            headers={"Content-Type": "application/json"},
                        )

                    usage = chat_response.get("usage", {})
                    if usage:
                        ctx = {
                            "request_id": request_id,
                            "request_type": client_format,
                            "model": model,
                            "target_model": target,
                            "request_ts": request_ts,
                            "duration_ms": duration_ms,
                        }
                        if upstream_cfg.get("id") is not None:
                            ctx["upstream_id"] = upstream_cfg["id"]
                        if session_id:
                            ctx["session_id"] = session_id
                        record_token_stats(usage, ctx)
                except Exception as e:
                    logging.exception("响应转换失败")
                    if logger:
                        logger.log_converted_response(
                            request_id, model, target,
                            {"error": str(e)}, request_type=client_format,
                            headers={"Content-Type": "application/json"},
                        )
                    self._send_json(500, {"error": {"type": "internal_error", "message": str(e)}})
                    return

                if store_enabled and is_responses_api:
                    from .transform import output_items_to_messages as _oitm
                    assistant_msgs = _oitm(output.get("output", []))
                    messages_for_conv = [
                        m for m in upstream_body.get("messages", [])
                        if m.get("role") != "system"
                    ] + assistant_msgs
                    _store_response(self.server, output, messages_for_conv)

                self._send_json(200, output)
                return

            except (socket.timeout, http.client.HTTPException, OSError) as e:
                if attempt < retries - 1:
                    logging.warning(f"转换上游请求失败，重试 {attempt + 1}/{retries}: {e}")
                    continue
                logging.error(f"转换上游请求失败（重试耗尽）: model={model}, target={target}, err={e}")
                self._send_json(502, {"error": {"type": "server_error", "message": str(e)}})
                return
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

    def _forward_streaming(self, upstream_body, model_cfg, request_id, model_name,
                             target, request_ts, upstream_cfg, client_format,
                             upstream_format, store_enabled=True, session_id=None,
                             upstream_url=None):
        """流式：http.client 连上游 SSE → TransformRouter 逐事件转换。"""
        base_url = upstream_cfg["base_url"]
        api_key = config_cache.pick_key(upstream_cfg["id"])
        if not api_key and upstream_cfg.get("id") in config_cache._upstream_has_any_key:
            logging.warning(f"[proxy] 上游 {upstream_cfg.get('name', upstream_cfg['id'])} 有 key 记录但全部被禁用")
        timeout = upstream_cfg.get("timeout", 120)
        logger = get_logger()

        parsed = urllib.parse.urlparse(base_url)
        path = _build_upstream_path(parsed, upstream_format)
        port = parsed.port or (80 if parsed.scheme == "http" else 443)

        conn = _create_upstream_conn(upstream_cfg, parsed, port)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        logger = get_logger()
        if logger:
            logger.log_converted_request(
                request_id, model_name, target, upstream_body,
                request_type=client_format,
                request_path=upstream_url,
                headers=headers,
            )
        conn.connect()
        conn.sock.settimeout(timeout)

        conn.request("POST", path, body=json.dumps(upstream_body), headers=headers)

        start = time.time()
        sse_buffer = []
        sse_buffer_size = 0
        SSE_BUFFER_MAX = 200 * 1024
        final_usage = None
        upstream_status = None

        try:  # 外层 try: 包裹全部逻辑，finally 中关闭 conn
            try:
                resp = conn.getresponse()
                upstream_status = resp.status
            except Exception as e:
                logging.error(f"上游连接失败: model={model_name}, target={target}, err={e}")
                self._handle_upstream_error(e)
                return

            if resp.status != 200:
                error_body = resp.read().decode("utf-8", errors="replace")
                logging.error(f"流式转换上游返回错误: model={model_name}, "
                              f"status={resp.status}, body={error_body[:2000]}")
                if resp.status == 429:
                    config_cache.mark_cooldown(
                        upstream_cfg["id"],
                        api_key,
                        upstream_cfg.get("key_cooldown_secs", 60)
                    )
                    logging.warning(f"[proxy] 上游 {upstream_cfg.get('name', upstream_cfg['id'])} key ****{api_key[-4:] if len(api_key)>4 else api_key} 触发 429，冷却 {upstream_cfg.get('key_cooldown_secs', 60)}s")
                error_event = _format_sse_event("response.failed", {
                    "response": {
                        "id": generate_response_id(),
                        "status": "failed",
                        "output": [],
                        "status_details": {
                            "error": {"type": "server_error", "message": f"Upstream returned HTTP {resp.status}"},
                        },
                    },
                })
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                self.wfile.write(error_event.encode("utf-8"))
                self.wfile.flush()
                try:
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, OSError):
                    pass
                if logger:
                    logger.log_upstream_response(
                        request_id, resp.status, error_body, 0,
                        model_name, target,
                        request_type=client_format,
                        headers=dict(resp.getheaders()),
                    )
                return

            # 发送 SSE 响应头
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.close_connection = True

            # 核心：TransformRouter 逐事件转换
            _rstore = (
                getattr(self.server, "response_store", None)
                if store_enabled else None
            )
            from .transform.router import TransformRouter
            for sse_event in TransformRouter.stream_convert(
                resp, upstream_format, client_format,
                request_messages=upstream_body.get("messages") if _rstore else None,
                response_store=_rstore,
            ):
                self.wfile.write(sse_event.encode("utf-8"))
                self.wfile.flush()
                if sse_buffer_size < SSE_BUFFER_MAX:
                    sse_buffer.append(sse_event)
                    sse_buffer_size += len(sse_event)

                if "response.completed" in sse_event or "message_delta" in sse_event:
                    parsed_evt = _parse_sse_event(sse_event)
                    data = parsed_evt.get("data")
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
                            "error": {"type": "server_error", "message": str(e)},
                        },
                    },
                })
                self.wfile.write(error_event.encode("utf-8"))
                self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except Exception:
                pass

        duration_ms = int((time.time() - start) * 1000)
        full_sse = "".join(sse_buffer) if sse_buffer else "(buffer overflow)"

        if logger:
            logger.log_upstream_response(
                request_id, upstream_status, full_sse, duration_ms,
                model_name, target,
                request_type=client_format,
                headers=dict(resp.getheaders()),
            )
            logger.log_converted_response(
                request_id, model_name, target,
                {"streaming": True, "data": full_sse},
                request_type=client_format,
                headers={
                    "Content-Type": "text/event-stream",
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )

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
            if session_id:
                ctx["session_id"] = session_id
            record_token_stats(final_usage, ctx)
        else:
            logging.warning(
                f"流式路径未提取到 usage: request_id={request_id}, "
                f"model={model_name}, target={target}"
            )

        try:
            conn.close()
        except Exception:
            pass

    def _handle_upstream_error(self, e: Exception):
        """统一 http.client 异常 → HTTP 错误映射。"""
        logging.exception(f"上游请求异常: {type(e).__name__}: {e}")

        if isinstance(e, socket.timeout):
            self._send_json(504, {"error": {"type": "timeout_error", "message": str(e)}})
        elif isinstance(e, (socket.gaierror, ssl.SSLError)):
            self._send_json(502, {"error": {"type": "connection_error", "message": str(e)}})
        elif isinstance(e, (http.client.HTTPException, ConnectionError, OSError)):
            self._send_json(502, {"error": {"type": "connection_error", "message": str(e)}})
        else:
            self._send_json(500, {"error": {"type": "internal_error", "message": str(e)}})

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
