#!/usr/bin/env python3
"""Codex Responses API → Chat Completions 转换代理。"""

import os
import sys
import json
import time
import ssl
import gzip
import socket
import logging
import http.client
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from pathlib import Path

from config_manager import ConfigCache, _parse_yaml, _yaml_scalar

from transform import (
    generate_response_id,
    responses_to_chat,
    chat_to_responses,
    create_codex_sse_stream,
    anthropic_to_chat,
    chat_to_anthropic,
    create_anthropic_sse_stream,
    _format_sse_event,
)

from request_logger import (
    get_logger,
    init_logger as init_request_logger,
    _generate_request_id,
    _extract_agent,
)

from token_stats import record_token_stats

# ─── 配置加载 ───────────────────────────────────────────────────────

CONFIG = {}
CONFIG_PATH = Path(__file__).parent / "proxy_config.yaml"

# ─── 动态配置缓存（替代静态 model_map）───────────────────────────
CONFIG_DB_PATH = Path(__file__).resolve().parent / "data" / "access_log.db"
config_cache = ConfigCache(CONFIG_DB_PATH)


def load_config(config_path: Path = None):
    """加载 proxy_config.yaml 的 proxy 段（日志配置等），校验动态配置 * fallback。

    config_path: 可选，覆盖默认配置文件路径（用于测试）。

    model_map 校验已移除，改由 ConfigCache 从 config.db 动态加载。
    """
    global CONFIG
    path = config_path or CONFIG_PATH
    if not path.exists():
        print(f"FATAL: 配置文件不存在: {path}", file=sys.stderr)
        sys.exit(1)

    with open(path, "r") as f:
        CONFIG = _parse_yaml(f.read())

    # 设置日志：同时写 proxy.log 文件和 stdout，遵循 log_level 配置
    if not logging.root.handlers:
        log_level = CONFIG.get("proxy", {}).get("log_level", "INFO")
        numeric_level = getattr(logging, log_level.upper(), logging.INFO)

        log_file = Path(__file__).parent / "proxy.log"
        file_handler = logging.FileHandler(log_file)
        stream_handler = logging.StreamHandler(sys.stdout)

        logging.basicConfig(
            level=numeric_level,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[file_handler, stream_handler],
        )


def resolve_model(model_name: str) -> dict:
    """使用动态配置缓存查找模型路由。

    返回格式与旧版兼容：
    {"target": str, "multimodal": bool}
    -> 新增 {"target": str, "multimodal": bool, "upstream": dict}
    """
    cfg = config_cache.resolve(model_name)
    if cfg is None:
        return {"target": model_name, "multimodal": False}
    return {
        "target": cfg["target_name"],
        "multimodal": bool(cfg["multimodal"]),
        "upstream": cfg["upstream"],
    }


# ─── ThreadedHTTPServer ────────────────────────────────────────────

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


# ─── 日志轮转 ──────────────────────────────────────────────────────

def rotate_log_if_needed():
    """启动时检查 proxy.log 大小，超过 101 MB 则轮转。

    具体实现：
    1. 检查 proxy.log 是否存在，不存在直接返回
    2. 获取文件大小，<= 101 MB 直接返回
    3. 重命名当前文件为 proxy.log.YYYYMMDD.gz（gzip 压缩）
    4. 新建空日志文件（由后续 logging.FileHandler 创建）

    注意：仅在启动时触发一次，运行中不轮转。
    注意：此函数必须在 load_config() 之后调用，因为 logging 需要先配置好
    """
    log_file = Path(__file__).parent / "proxy.log"
    if not log_file.exists():
        return
    size = log_file.stat().st_size
    if size <= 101 * 1024 * 1024:  # 101 MB
        return

    timestamp = time.strftime("%Y%m%d")
    gz_path = Path(str(log_file) + f".{timestamp}.gz")
    logging.info(f"日志轮转: {log_file} → {gz_path}")
    with open(log_file, "rb") as f_in:
        with gzip.open(gz_path, "wb") as f_out:
            f_out.write(f_in.read())
    log_file.unlink()


# ─── 上游连接创建 ──────────────────────────────────────────────

def _create_upstream_conn(upstream_cfg, parsed, port):
    """创建到上游的连接，支持 HTTP 代理（含 HTTPS tunneling）。

    使用 http.client 的 set_tunnel() 实现 HTTPS over HTTP 代理。
    """
    proxy = upstream_cfg.get("proxy")
    connect_timeout = upstream_cfg.get("connect_timeout", 10)

    if proxy:
        proxy_parsed = urllib.parse.urlparse(proxy)
        proxy_host = proxy_parsed.hostname
        proxy_port = proxy_parsed.port or 8080

        use_ssl = parsed.scheme == "https"
        ssl_ctx = ssl.create_default_context() if upstream_cfg.get("ssl_verify", True) else ssl._create_unverified_context()

        if use_ssl:
            conn = http.client.HTTPSConnection(
                proxy_host, proxy_port,
                timeout=connect_timeout, context=ssl_ctx,
            )
        else:
            conn = http.client.HTTPConnection(
                proxy_host, proxy_port,
                timeout=connect_timeout,
            )
        # 设置 tunnel：CONNECT 目标主机:端口
        conn.set_tunnel(parsed.hostname, port)
        return conn
    else:
        use_ssl = parsed.scheme == "https"
        ssl_ctx = ssl.create_default_context() if upstream_cfg.get("ssl_verify", True) else ssl._create_unverified_context()
        if use_ssl:
            return http.client.HTTPSConnection(
                parsed.hostname, port,
                timeout=connect_timeout, context=ssl_ctx,
            )
        else:
            return http.client.HTTPConnection(
                parsed.hostname, port,
                timeout=connect_timeout,
            )


# ─── 透传辅助函数 ───────────────────────────────────────────────────

def _normalize_forward_path(path: str):
    """归一化透传请求路径，返回安全路径或 None（含路径穿越时）。"""
    query = ""
    if "?" in path:
        path, query = path.split("?", 1)

    if path.startswith("/v1"):
        path = path[3:]

    if ".." in path:
        return None

    while "//" in path:
        path = path.replace("//", "/")

    if not path.startswith("/"):
        path = "/" + path

    if query:
        path = path + "?" + query

    return path


def _extract_model_for_pass_through(method: str, path: str, body_raw: bytes) -> str:
    """从请求中提取模型名称，用于透传路由。失败时返回 '*'。"""
    if method == "POST":
        try:
            body = json.loads(body_raw)
            return body.get("model", "*")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return "*"
    elif method == "GET":
        parsed = urllib.parse.urlparse(path)
        params = urllib.parse.parse_qs(parsed.query)
        models = params.get("model")
        if models and len(models) > 0:
            return models[0]
        return "*"
    return "*"


# ─── 请求 Handler ──────────────────────────────────────────────────

class ProxyHandler(BaseHTTPRequestHandler):
    """处理所有 HTTP 请求。"""

    def do_GET(self):
        if self.path == "/health":
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
        elif self.path.startswith("/v1/"):
            self._handle_pass_through()
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path in ("/v1/responses", "/v1/responses/compact"):
            self._handle_responses()
        elif self.path == "/v1/messages":
            self._handle_messages()
        elif self.path == "/admin/reload":
            self._handle_admin_reload()
        elif self.path.startswith("/v1/"):
            self._handle_pass_through()
        else:
            self._send_json(404, {"error": "not found"})

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

    def _handle_responses(self):
        """核心：Responses → Chat → Responses 转换。"""
        # 读取请求体
        content_length = int(self.headers.get("Content-Length", 0))
        body_raw = self.rfile.read(content_length)

        # 生成 request_id
        request_id = _generate_request_id()
        request_ts = time.strftime("%Y-%m-%d %H:%M:%S")

        try:
            body = json.loads(body_raw)
        except json.JSONDecodeError as e:
            logging.error(f"JSON 解析失败: {e}")
            model_name = body_raw.decode("utf-8", errors="replace")[:50]
            logger = get_logger()
            if logger:
                logger.log_raw_request(request_id, model_name, "?", {"raw_error": str(e), "raw_body": body_raw.decode("utf-8", errors="replace")[:5000]})
            self._send_json(400, {"error": {"type": "invalid_request_error", "message": str(e)}})
            return

        model_name = body.get("model", "*")
        model_cfg = resolve_model(model_name)
        target = model_cfg["target"]
        upstream_cfg = model_cfg.get("upstream")
        if upstream_cfg is None:
            logging.error(f"模型 {model_name} 无法解析上游配置")
            self._send_json(500, {"error": {"type": "internal_error", "message": "模型路由不可用"}})
            return
        is_stream = body.get("stream", False)

        logging.info(f"请求: model={model_name}, stream={is_stream}, target={target}")

        # 阶段 1：记录原始请求
        logger = get_logger()
        if logger:
            logger.log_raw_request(request_id, model_name, target, body)

        # 转换请求体
        try:
            chat_body = responses_to_chat(body, model_cfg)
        except Exception as e:
            logging.exception("responses_to_chat 转换失败")
            if logger:
                logger.log_converted_request(request_id, model_name, target, {"error": str(e)})
            self._send_json(500, {"error": {"type": "internal_error", "message": str(e)}})
            return

        # 阶段 2：记录转换后的请求
        if logger:
            logger.log_converted_request(request_id, model_name, target, chat_body)

        # previous_response_id：从 store 读取历史 conversation 并注入到本轮 messages
        prev_id = body.get("previous_response_id")
        if prev_id:
            response_store = getattr(self.server, "response_store", None)
            if response_store is not None:
                record = response_store.get(prev_id)
                if record:
                    # system 消息始终保持首位，历史插入 system 与 user 之间
                    system_msgs = [m for m in chat_body["messages"] if m.get("role") == "system"]
                    non_system_msgs = [m for m in chat_body["messages"] if m.get("role") != "system"]
                    chat_body["messages"] = system_msgs + record.conversation + non_system_msgs
                else:
                    logging.warning(f"previous_response_id={prev_id!r} 不存在或已过期，忽略历史")

        # 转发到上游（传入 request_id, model_name, target, request_ts）
        # upstream_cfg 优先从 model_cfg 获取（动态配置），fallback 到 CONFIG（静态配置）
        store_enabled = body.get("store", True)
        if is_stream:
            self._forward_streaming(chat_body, model_cfg, request_id, model_name, target, request_ts,
                                    store_enabled=store_enabled, upstream_cfg=upstream_cfg)
        else:
            self._forward_non_streaming(chat_body, request_id, model_name, target, request_ts,
                                        store_enabled=store_enabled, is_responses_api=True,
                                        upstream_cfg=upstream_cfg)

    def _handle_messages(self):
        """核心：Anthropic Messages → Chat → Anthropic Messages 转换。"""
        content_length = int(self.headers.get("Content-Length", 0))
        body_raw = self.rfile.read(content_length)
        request_id = _generate_request_id()
        request_ts = time.strftime("%Y-%m-%d %H:%M:%S")

        try:
            body = json.loads(body_raw)
        except json.JSONDecodeError as e:
            logging.error(f"JSON 解析失败: {e}")
            self._send_json(400, {"error": {"type": "invalid_request_error", "message": str(e)}})
            return

        model_name = body.get("model", "*")
        model_cfg = resolve_model(model_name)
        target = model_cfg["target"]
        is_stream = body.get("stream", False)

        logging.info(f"请求[/v1/messages]: model={model_name}, stream={is_stream}, target={target}")

        logger = get_logger()
        if logger:
            logger.log_raw_request(request_id, model_name, target, body)

        # 请求转换
        try:
            chat_body = anthropic_to_chat(body, model_cfg)
        except Exception as e:
            logging.exception("anthropic_to_chat 转换失败")
            if logger:
                logger.log_converted_request(request_id, model_name, target, {"error": str(e)})
            self._send_json(500, {"error": {"type": "internal_error", "message": str(e)}})
            return

        if logger:
            logger.log_converted_request(request_id, model_name, target, chat_body)

        # 转发（response_converter 始终为 chat_to_anthropic，store_enabled=False 防止 kwargs 注入给 create_anthropic_sse_stream）
        upstream_cfg = model_cfg.get("upstream") or CONFIG.get("upstream", {})
        if is_stream:
            self._forward_streaming(chat_body, model_cfg, request_id, model_name, target, request_ts,
                                    response_converter=chat_to_anthropic,
                                    sse_stream_factory=create_anthropic_sse_stream,
                                    store_enabled=False, upstream_cfg=upstream_cfg)
        else:
            self._forward_non_streaming(chat_body, request_id, model_name, target, request_ts,
                                        response_converter=chat_to_anthropic,
                                        upstream_cfg=upstream_cfg)

    def _forward_non_streaming(self, chat_body: dict, request_id: str, model: str, target: str, request_ts: str, response_converter=None, store_enabled: bool = True, is_responses_api: bool = False, upstream_cfg: dict = None):
        """非流式：转发到上游，转换响应，返回。

        response_converter: callable, chat_response -> format_response
        is_responses_api: True 时在 response_converter() 后存入 store（防止 _handle_messages 误触发）
        upstream_cfg: 上游配置 dict；None 时从 CONFIG fallback（向后兼容静态配置）

        超时处理：
        - connect_timeout: 连接超时（独立设置）
        - timeout: 读超时（总超时，包含 connect + read）
        实现方式：先用 connect_timeout 建立连接，再设 socket timeout 为总超时
        """
        if upstream_cfg is None:
            upstream_cfg = CONFIG.get("upstream", {})
        if response_converter is None:
            from transform_responses import chat_to_responses as response_converter
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
                conn.request("POST", path, body=json.dumps(chat_body), headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                })

                # 连接成功后设 read timeout（总超时 - 已用时）
                start = time.time()
                resp = conn.getresponse()
                if conn.sock:
                    conn.sock.settimeout(timeout)
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
                        logger.log_upstream_response(request_id, resp.status, resp_body.decode("utf-8", errors="replace"), duration_ms)
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
                    logger.log_upstream_response(request_id, resp.status, chat_response, duration_ms)

                # 阶段 4 + 5：转换响应 + Token 统计
                try:
                    responses_response = response_converter(chat_response)
                    if logger:
                        logger.log_converted_response(request_id, model, target, responses_response)

                        usage = chat_response.get("usage", {})
                        if usage:
                            record_token_stats(usage, {
                                "request_id": request_id,
                                "agent": _extract_agent(self.headers.get("User-Agent", "")),
                                "model": model,
                                "target_model": target,
                                "request_ts": request_ts,
                                "duration_ms": duration_ms,
                            })
                except Exception as e:
                    logging.exception("chat_to_responses 转换失败")
                    if logger:
                        logger.log_converted_response(request_id, model, target, {"error": str(e)})
                    self._send_json(500, {"error": {"type": "internal_error", "message": str(e)}})
                    return

                # 存储 response（仅当 store_enabled=True 且 is_responses_api=True 时）
                # 使用 is_responses_api 显式标记（而非根据 response_converter 类型推断），
                # 防止 _handle_messages（Anthropic 路径）不传参数时误触发存储
                if store_enabled and is_responses_api:
                    from transform_responses import output_items_to_messages as _oitm
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

    def _forward_streaming(self, chat_body: dict, model_cfg: dict, request_id: str, model: str, target: str, request_ts: str, response_converter=None, sse_stream_factory=None, store_enabled: bool = True, upstream_cfg: dict = None):
        """流式：直连上游 SSE，通过 sse_stream_factory 转换后逐事件返回。

        response_converter: callable（本函数内用于 token_stats 的 context，不直接调用）
        sse_stream_factory: callable, upstream_response -> Generator[str]
                           默认 create_codex_sse_stream（与当前行为一致）
        upstream_cfg: 上游配置 dict；None 时从 CONFIG fallback（向后兼容静态配置）
        """
        if sse_stream_factory is None:
            from transform_responses import create_codex_sse_stream as sse_stream_factory
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
        conn.request("POST", path, body=json.dumps(chat_body), headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        })

        # 设置 Codex 响应头
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        start = time.time()
        sse_buffer = []
        final_usage = None
        upstream_status = None

        try:
            try:
                resp = conn.getresponse()
                if conn.sock:
                    conn.sock.settimeout(timeout)
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
                        logger.log_upstream_response(request_id, resp.status, resp.read().decode("utf-8", errors="replace"), 0)
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
                        logger.log_upstream_response(request_id, upstream_status, resp.read().decode("utf-8", errors="replace"), 0)
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
                    logger.log_upstream_response(request_id, upstream_status,
                        json.dumps({"error": {"type": "server_error", "message": str(e)}}),
                        int((time.time() - start) * 1000))

            duration_ms = int((time.time() - start) * 1000)
            full_sse = "".join(sse_buffer)

            try:
                self.wfile.close()
            except Exception:
                pass

            logger = get_logger()
            if logger:
                logger.log_upstream_response(request_id, upstream_status, full_sse, duration_ms)
                logger.log_converted_response(request_id, model, target, {"streaming": True, "note": "SSE 流式响应，无 converted_response"})
                agent = _extract_agent(self.headers.get("User-Agent", ""))
                if final_usage:
                    record_token_stats(final_usage, {
                        "request_id": request_id,
                        "agent": agent,
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
        logging.info("%s - - [%s] %s" % (self.client_address[0], self.log_date_time_string(), format % args))

    def _handle_pass_through(self):
        """透传端点：原样转发 /v1/* 请求到上游，不做协议转换。"""
        content_length = int(self.headers.get("Content-Length", 0))
        body_raw = self.rfile.read(content_length)

        request_id = _generate_request_id()
        request_ts = time.strftime("%Y-%m-%d %H:%M:%S")

        model_name = _extract_model_for_pass_through(self.command, self.path, body_raw)

        model_cfg = resolve_model(model_name)
        target = model_cfg["target"]
        upstream_cfg = model_cfg.get("upstream")
        if upstream_cfg is None:
            logging.error(f"透传: 模型 {model_name} 无法解析上游配置")
            self._send_json(500, {"error": {"type": "internal_error", "message": "模型路由不可用"}})
            return

        forward_path = _normalize_forward_path(self.path)
        if forward_path is None:
            self._send_json(400, {"error": {"type": "invalid_request_error", "message": "无效的请求路径"}})
            return

        is_stream = False
        try:
            body = json.loads(body_raw)
            is_stream = body.get("stream", False)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

        logging.info(f"透传: model={model_name}, stream={is_stream}, target={target}, forward_path={forward_path}")

        logger = get_logger()
        if logger:
            log_body = body_raw.decode("utf-8", errors="replace")[:5000] if body_raw else "(empty)"
            logger.log_raw_request(request_id, model_name, target,
                {"method": self.command, "path": self.path, "forward_path": forward_path, "body": log_body})

        if is_stream:
            self._forward_pass_through_streaming(body_raw, request_id, model_name, target, request_ts, upstream_cfg, forward_path)
        else:
            self._forward_pass_through_non_streaming(body_raw, request_id, model_name, target, request_ts, upstream_cfg, forward_path)

    def _forward_pass_through_non_streaming(self, body_raw, request_id, model_name, target, request_ts, upstream_cfg, forward_path):
        """非流式透传转发 — 将在 Task 5 实现完整逻辑。"""
        self._send_json(501, {"error": {"type": "not_implemented", "message": "非流式透传尚未实现"}})

    def _forward_pass_through_streaming(self, body_raw, request_id, model_name, target, request_ts, upstream_cfg, forward_path):
        """流式 SSE 透传转发 — 将在 Task 6 实现完整逻辑。"""
        self._send_json(501, {"error": {"type": "not_implemented", "message": "流式透传尚未实现"}})


# ─── 存储辅助 ──────────────────────────────────────────────────────

def _store_response(server, responses_response: dict, messages_for_conv: list):
    """将 responses_response 存入 server.response_store（如已挂载）。

    messages_for_conv: 已包含完整对话历史的消息列表（调用方负责构建，含 assistant 输出；
                       不包含 system，避免多轮时重复叠加）。
    """
    # 懒导入 response_store 类（避免 proxy.py 模块级导入时的循环依赖，proxy.py
    # 导入 transform，transform 导入 response_store，response_store 不导入 proxy.py）
    response_store = getattr(server, "response_store", None)
    if response_store is None:
        return
    from response_store import ResponseRecord as _RR
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


# ─── 主入口 ────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Codex Proxy")
    parser.add_argument("-c", "--config", default=None, help="配置文件路径")
    args = parser.parse_args()

    load_config(Path(args.config) if args.config else None)
    rotate_log_if_needed()

    proxy_cfg = CONFIG.get("proxy", {})
    host = proxy_cfg.get("host", "127.0.0.1")
    port = proxy_cfg.get("port", 48743)

    logging_cfg = CONFIG.get("logging", {})
    retention_days = logging_cfg.get("debug_retention_days", 7)
    log_dir = logging_cfg.get("log_dir", "data")
    db_file = logging_cfg.get("log_file", "access_log.db")
    init_request_logger(Path(__file__).parent / log_dir / db_file, retention_days)

    server = ThreadedHTTPServer((host, port), ProxyHandler)
    from response_store import ResponseStore as _ResponseStore
    _store_cfg = CONFIG.get("response_store", {})
    server.response_store = _ResponseStore(
        max_entries=_store_cfg.get("max_entries", 1000),
        ttl_seconds=_store_cfg.get("ttl_seconds", 3600),
    )
    logging.info(f"Codex Proxy 启动: http://{host}:{port}")

    # PID 文件
    pid_file = Path(__file__).parent / ".proxy.pid"
    pid_file.write_text(str(os.getpid()))

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("收到中断信号，关闭服务")
        server.shutdown()
        pid_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
