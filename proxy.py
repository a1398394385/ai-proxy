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

from transform import (
    generate_response_id,
    responses_to_chat,
    chat_to_responses,
    create_codex_sse_stream,
)

# ─── 最小 YAML 解析器（仅支持 3 层嵌套，标量值）───────────────────────────

def _parse_yaml(text: str) -> dict:
    """极简 YAML 解析器，仅支持本项目 proxy_config.yaml 的结构。
    嵌套 dict 最多 3 层，值为 str/int/float/bool。
    """
    result = {}
    stack = [(result, -1)]  # (current_dict, indent_level)

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())

        # 弹出比当前 indent 深的栈帧
        while len(stack) > 1 and stack[-1][1] >= indent:
            stack.pop()

        current_dict = stack[-1][0]

        if ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip().strip('"').strip("'")
            val = val.strip()

            if val == "" or val.startswith("#"):
                # 嵌套 dict
                new_dict = {}
                current_dict[key] = new_dict
                stack.append((new_dict, indent))
            elif val.startswith("[") and val.endswith("]"):
                # 内联列表
                items = [
                    _yaml_scalar(item.strip())
                    for item in val[1:-1].split(",")
                    if item.strip()
                ]
                current_dict[key] = items
            else:
                current_dict[key] = _yaml_scalar(val)

    return result


def _yaml_scalar(val: str):
    """将 YAML 标量值转为 Python 类型。"""
    if not val:
        return ""
    # 去除注释
    if " #" in val:
        val = val[: val.index(" #")].strip()
    # 去除引号
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        return val[1:-1]
    # bool
    if val.lower() in ("true", "yes"):
        return True
    if val.lower() in ("false", "no"):
        return False
    # int
    try:
        return int(val)
    except ValueError:
        pass
    # float
    try:
        return float(val)
    except ValueError:
        pass
    return val


# ─── 配置加载 ───────────────────────────────────────────────────────

CONFIG = {}
CONFIG_PATH = Path(__file__).parent / "proxy_config.yaml"


def load_config(config_path: Path = None):
    """加载 proxy_config.yaml，校验后写入全局 CONFIG。

    config_path: 可选，覆盖默认配置文件路径（用于测试）。

    校验规则：
    - 必须包含 model_map
    - model_map 必须包含 "*" fallback 键，否则 sys.exit(1) 打印明确错误
    """
    global CONFIG
    path = config_path or CONFIG_PATH
    if not path.exists():
        print(f"FATAL: 配置文件不存在: {path}", file=sys.stderr)
        sys.exit(1)

    with open(path, "r") as f:
        CONFIG = _parse_yaml(f.read())

    # 校验 model_map 存在
    if "model_map" not in CONFIG:
        print("FATAL: 配置文件缺少 model_map", file=sys.stderr)
        sys.exit(1)

    # 校验 "*" fallback 键 — 启动时必须存在
    if "*" not in CONFIG["model_map"]:
        print('FATAL: model_map 必须包含 "*" fallback 键', file=sys.stderr)
        sys.exit(1)

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
    """根据 model_name 从 model_map 查找配置，支持 * fallback。"""
    model_map = CONFIG.get("model_map", {})
    if model_name in model_map:
        return model_map[model_name]
    return model_map.get("*", {"target": model_name, "multimodal": False})


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
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path in ("/v1/responses", "/v1/responses/compact"):
            self._handle_responses()
        else:
            self._send_json(404, {"error": "not found"})

    def _handle_models(self):
        """返回 model_map 中所有非 * 的 key。"""
        model_map = CONFIG.get("model_map", {})
        models = [k for k in model_map if k != "*"]
        self._send_json(200, {"data": [{"id": m, "object": "model"} for m in models]})

    def _handle_responses(self):
        """核心：Responses → Chat → Responses 转换。"""
        # 读取请求体
        content_length = int(self.headers.get("Content-Length", 0))
        body_raw = self.rfile.read(content_length)
        try:
            body = json.loads(body_raw)
        except json.JSONDecodeError as e:
            logging.error(f"JSON 解析失败: {e}")
            self._send_json(400, {"error": {"type": "invalid_request_error", "message": str(e)}})
            return

        model_name = body.get("model", "*")
        model_cfg = resolve_model(model_name)
        is_stream = body.get("stream", False)

        logging.info(f"请求: model={model_name}, stream={is_stream}, target={model_cfg['target']}")

        # 转换请求体
        try:
            chat_body = responses_to_chat(body, model_cfg)
        except Exception as e:
            logging.exception("responses_to_chat 转换失败")
            self._send_json(500, {"error": {"type": "internal_error", "message": str(e)}})
            return

        # 转发到上游
        if is_stream:
            self._forward_streaming(chat_body, model_cfg)
        else:
            self._forward_non_streaming(chat_body)

    def _forward_non_streaming(self, chat_body: dict):
        """非流式：转发到上游，转换响应，返回。

        超时处理：
        - connect_timeout: 连接超时（独立设置）
        - timeout: 读超时（总超时，包含 connect + read）
        实现方式：先用 connect_timeout 建立连接，再设 socket timeout 为总超时
        """
        upstream_cfg = CONFIG.get("upstream", {})
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
                # 先用 connect_timeout 建立连接
                if use_ssl:
                    conn = http.client.HTTPSConnection(
                        parsed.hostname,
                        port,
                        timeout=connect_timeout,
                        context=ssl_ctx,
                    )
                else:
                    conn = http.client.HTTPConnection(
                        parsed.hostname,
                        port,
                        timeout=connect_timeout,
                    )
                conn.request("POST", path, body=json.dumps(chat_body), headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                })

                # 连接成功后设 read timeout（总超时 - 已用时）
                resp = conn.getresponse()
                if conn.sock:
                    conn.sock.settimeout(timeout)
                resp_body = resp.read()
                conn.close()
                conn = None

                if resp.status >= 500 and attempt < retries - 1:
                    logging.warning(f"上游 {resp.status}，重试 {attempt + 1}/{retries}")
                    continue

                if resp.status != 200:
                    self.send_response(resp.status)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(resp_body)
                    return

                # 转换响应
                chat_response = json.loads(resp_body)
                responses_response = chat_to_responses(chat_response)
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

    def _forward_streaming(self, chat_body: dict, model_cfg: dict):
        """流式：直连上游 SSE，通过 create_codex_sse_stream 转换后逐事件返回。"""
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

        if use_ssl:
            conn = http.client.HTTPSConnection(
                parsed.hostname, port, timeout=connect_timeout, context=ssl_ctx,
            )
        else:
            conn = http.client.HTTPConnection(
                parsed.hostname, port, timeout=connect_timeout,
            )
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

        try:
            resp = conn.getresponse()
            if conn.sock:
                conn.sock.settimeout(timeout)

            # 验证上游 Content-Type，非 SSE 则包装为 response.failed
            ct = resp.getheader("Content-Type", "")
            if resp.status != 200:
                error_event = (
                    f'event: response.failed\n'
                    f'data: {{"type":"error","error":{{"type":"server_error",'
                    f'"message":"Upstream returned HTTP {resp.status}"}}}}\n\n'
                )
                self.wfile.write(error_event.encode("utf-8"))
                self.wfile.flush()
                return

            if "text/event-stream" not in ct:
                logging.warning(f"上游返回非 SSE Content-Type: {ct}")
                error_event = (
                    f'event: response.failed\n'
                    f'data: {{"type":"error","error":{{"type":"server_error",'
                    f'"message":"Upstream returned non-SSE Content-Type: {ct}"}}}}\n\n'
                )
                self.wfile.write(error_event.encode("utf-8"))
                self.wfile.flush()
                return

            # 核心：通过 create_codex_sse_stream 逐事件转换并发送
            for sse_event in create_codex_sse_stream(resp):
                self.wfile.write(sse_event.encode("utf-8"))
                self.wfile.flush()

        except Exception as e:
            logging.exception("流式转发异常")
            try:
                error_event = (
                    f'event: response.failed\n'
                    f'data: {{"type":"error","error":{{"type":"server_error",'
                    f'"message":{json.dumps(str(e))}}}}}\n\n'
                )
                self.wfile.write(error_event.encode("utf-8"))
                self.wfile.flush()
            except Exception:
                pass
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


# ─── 主入口 ────────────────────────────────────────────────────────

def main():
    load_config()
    rotate_log_if_needed()

    proxy_cfg = CONFIG.get("proxy", {})
    host = proxy_cfg.get("host", "127.0.0.1")
    port = proxy_cfg.get("port", 48743)

    server = ThreadedHTTPServer((host, port), ProxyHandler)
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
