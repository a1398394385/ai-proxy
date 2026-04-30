#!/usr/bin/env python3
"""Pass-Through Proxy — 纯透传代理，不做任何协议转换。"""

import os, sys, json, time, ssl, logging, http.client, urllib.parse, socket
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from pathlib import Path

from common import (
    CONFIG, CONFIG_PATH, load_config,
    config_cache, resolve_model, _create_upstream_conn,
    _normalize_forward_path, _extract_model_for_pass_through,
    get_port, get_host,
)
from request_logger import (
    get_logger, init_logger as init_request_logger,
    _generate_request_id, _extract_agent,
)
from token_stats import record_token_stats

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

class PassThroughHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._handle_pass_through()
    
    def do_POST(self):
        self._handle_pass_through()
    
    def _handle_pass_through(self):
        """透传请求：原样转发到上游。"""
        content_length = int(self.headers.get("Content-Length", 0))
        body_raw = self.rfile.read(content_length)
        
        request_id = _generate_request_id()
        request_ts = time.strftime("%Y-%m-%d %H:%M:%S")
        
        model_name = _extract_model_for_pass_through(self.command, self.path, body_raw)
        model_cfg = resolve_model(model_name, proxy_type='pass_through')
        target = model_cfg["target"]
        upstream_cfg = model_cfg.get("upstream")
        if upstream_cfg is None:
            upstream_cfg = CONFIG.get("upstream", {})
        
        forward_path = _normalize_forward_path(self.path)
        if forward_path is None:
            self._send_json(400, {"error": {"type": "invalid_request_error", "message": "无效的请求路径"}})
            return
        
        # Detect stream mode
        is_stream = False
        try:
            body = json.loads(body_raw)
            is_stream = body.get("stream", False)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        
        logging.info(f"透传: model={model_name}, stream={is_stream}, target={target}, path={forward_path}")
        
        # Phase 1: log raw request
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
                conn.request(self.command, path, body=body_raw, headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": content_type,
                })
                
                start = time.time()
                resp = conn.getresponse()
                if conn.sock:
                    conn.sock.settimeout(timeout)
                resp_body = resp.read()
                duration_ms = int((time.time() - start) * 1000)
                conn.close()
                conn = None
                
                if resp.status >= 500 and attempt < retries - 1:
                    logging.warning(f"透传上游 {resp.status}，重试 {attempt + 1}/{retries}")
                    continue
                
                logger = get_logger()
                if logger:
                    log_data = resp_body.decode("utf-8", errors="replace")[:5000]
                    logger.log_upstream_response(request_id, resp.status, log_data, duration_ms)
                
                if resp.status == 200:
                    try:
                        chat_response = json.loads(resp_body)
                        usage = chat_response.get("usage", {})
                        if usage:
                            record_token_stats(usage, {
                                "request_id": request_id,
                                "agent": _extract_agent(self.headers.get("User-Agent", "")),
                                "model": model_name,
                                "target_model": target,
                                "request_ts": request_ts,
                                "duration_ms": duration_ms,
                            })
                        else:
                            logging.warning("透传: 无法从响应提取 usage，跳过 token_stats")
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        logging.warning("透传: 响应非 JSON，无法提取 usage")
                
                self.send_response(resp.status)
                self.send_header("Content-Type", resp.getheader("Content-Type", "application/json"))
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
                    try: conn.close()
                    except Exception: pass
    
    def _forward_pass_through_streaming(self, body_raw, request_id, model_name, target, request_ts, upstream_cfg, forward_path):
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
        
        for attempt in range(retries):
            conn = None
            try:
                conn = _create_upstream_conn(upstream_cfg, parsed, port)
                conn.request(self.command, path, body=body_raw, headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": content_type,
                    "Accept": "text/event-stream",
                })
                
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                
                start = time.time()
                sse_buffer = []
                final_usage = None
                upstream_status = None
                
                resp = conn.getresponse()
                if conn.sock:
                    conn.sock.settimeout(timeout)
                upstream_status = resp.status
                
                if resp.status != 200:
                    error_body = resp.read()
                    self.wfile.write(error_body)
                    self.wfile.flush()
                    logger = get_logger()
                    if logger:
                        logger.log_upstream_response(request_id, upstream_status,
                            error_body.decode("utf-8", errors="replace")[:5000], 0)
                    return
                
                while True:
                    chunk = resp.read(4096)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    sse_buffer.append(chunk)
                
                duration_ms = int((time.time() - start) * 1000)
                full_sse = b"".join(sse_buffer).decode("utf-8", errors="replace")[:5000]
                logger = get_logger()
                if logger:
                    logger.log_upstream_response(request_id, upstream_status, full_sse, duration_ms)
                
                if sse_buffer:
                    last_chunk = sse_buffer[-1].decode("utf-8", errors="replace")
                    import re
                    # Brace-balanced usage extraction (handles nested JSON)
                    usage_start = last_chunk.find('"usage"')
                    if usage_start >= 0:
                        colon_pos = last_chunk.find(':', usage_start)
                        if colon_pos >= 0:
                            brace_pos = last_chunk.find('{', colon_pos)
                            if brace_pos >= 0:
                                depth = 0
                                end_pos = brace_pos
                                for i in range(brace_pos, len(last_chunk)):
                                    if last_chunk[i] == '{':
                                        depth += 1
                                    elif last_chunk[i] == '}':
                                        depth -= 1
                                        if depth == 0:
                                            end_pos = i + 1
                                            break
                                if end_pos > brace_pos:
                                    try:
                                        final_usage = json.loads(last_chunk[brace_pos:end_pos])
                                    except json.JSONDecodeError:
                                        pass
                    if final_usage:
                        record_token_stats(final_usage, {
                            "request_id": request_id,
                            "agent": _extract_agent(self.headers.get("User-Agent", "")),
                            "model": model_name,
                            "target_model": target,
                            "request_ts": request_ts,
                            "duration_ms": duration_ms,
                        })
                    else:
                        logging.warning("透传流式: 无法从 SSE 提取 usage，跳过 token_stats")
                return
                
            except (socket.timeout, http.client.HTTPException, OSError) as e:
                logging.warning(f"透传流式上游请求失败 (attempt {attempt + 1}): {e}")
                if attempt < retries - 1:
                    continue
                logger = get_logger()
                if logger:
                    logger.log_upstream_response(request_id, 0,
                        json.dumps({"error": str(e)}), int((time.time() - start) * 1000))
                try:
                    self.wfile.write(f"data: {{\"error\":\"{str(e)}\"}}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, OSError):
                    pass
                return
            except Exception as e:
                logging.exception(f"透传流式失败: {e}")
                logger = get_logger()
                if logger:
                    logger.log_upstream_response(request_id, upstream_status or 0,
                        json.dumps({"error": str(e)}), int((time.time() - start) * 1000))
                try:
                    self.wfile.write(f"data: {{\"error\":\"{str(e)}\"}}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, OSError):
                    pass
                return
            finally:
                if conn:
                    try: conn.close()
                    except Exception: pass
    
    def _send_json(self, status, data):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

def main():
    load_config()
    
    # Setup logging
    if not logging.root.handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    
    # Setup request logger
    logging_cfg = CONFIG.get("logging", {})
    retention_days = logging_cfg.get("debug_retention_days", 7)
    log_dir = logging_cfg.get("log_dir", "data")
    db_file = logging_cfg.get("log_file", "access_log.db")
    try:
        init_request_logger(Path(__file__).parent / log_dir / db_file, retention_days)
    except Exception as e:
        logging.warning(f"请求日志初始化失败: {e}")
    
    host = get_host("pass_through", "127.0.0.1")
    port = get_port("pass_through", 48744)
    
    server = ThreadedHTTPServer((host, port), PassThroughHandler)
    logging.info(f"Pass-Through Proxy 启动: http://{host}:{port}")
    
    pid_file = Path(__file__).parent / ".pass_through.pid"
    pid_file.write_text(str(os.getpid()))
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("收到中断信号，关闭服务")
        server.shutdown()
        pid_file.unlink(missing_ok=True)

if __name__ == "__main__":
    main()
