"""最小 Chat Completions SSE mock server，供 e2e 测试使用。"""

import json
import time
import socket
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler


class MockChatHandler(BaseHTTPRequestHandler):
    """模拟 Chat Completions API — 支持非流式 JSON 和流式 SSE。"""

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body_raw = self.rfile.read(content_length)
        body = json.loads(body_raw)
        is_stream = body.get("stream", False)

        if is_stream:
            self._handle_stream(body)
        else:
            self._handle_non_stream(body)

    def _handle_non_stream(self, body):
        model = body.get("model", "mock-model")
        messages = body.get("messages", [])
        content = f"mock response to: {messages[-1].get('content', '')[:100] if messages else 'empty'}"

        response = {
            "id": f"chatcmpl-mock-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "prompt_tokens_details": {"cached_tokens": 20},
            },
        }
        self._send_json(200, response)

    def _handle_stream(self, body):
        model = body.get("model", "mock-model")

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        chat_id = f"chatcmpl-mock-{int(time.time())}"
        words = ["Hello", ", ", "this", " is", " a", " mock", " streaming", " response", "!"]

        for i, word in enumerate(words):
            chunk = {
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {"content": word},
                    "finish_reason": None,
                }],
            }
            if i == len(words) - 1:
                chunk["choices"][0]["finish_reason"] = "stop"
                chunk["usage"] = {
                    "prompt_tokens": 100,
                    "completion_tokens": len(words),
                    "total_tokens": 100 + len(words),
                }

            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
            self.wfile.flush()
            time.sleep(0.01)

        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _send_json(self, status, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_mock_server(port=0):
    """启动 mock server，返回 (server, port)。port=0 自动分配。"""
    server = HTTPServer(("127.0.0.1", port), MockChatHandler)
    actual_port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, actual_port
