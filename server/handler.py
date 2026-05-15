"""server 包 — HermesDataHandler 请求分发。"""

from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote

from . import config_api, fact_api, token_api, pricing_api, static_files
from .common import json_response


# ─── 分发表 ───
# 注意：static_files.handle_get 必须在 _GET_HANDLERS 最后（它始终返回 True，是兜底）

_GET_HANDLERS = [
    config_api.handle_get,
    fact_api.handle_get,
    token_api.handle_get,
    pricing_api.handle_get,
    static_files.handle_get,  # 兜底，必须最后
]

_POST_HANDLERS = [
    config_api.handle_post,
    pricing_api.handle_post,
    fact_api.handle_post,
]

_PUT_HANDLERS = [
    config_api.handle_put,
    pricing_api.handle_put,
    fact_api.handle_put,
]

_DELETE_HANDLERS = [
    config_api.handle_delete,
    pricing_api.handle_delete,
    fact_api.handle_delete,
]


# ─── Handler ───


class HermesDataHandler(SimpleHTTPRequestHandler):
    stats_service = None

    def log_message(self, format, *args):
        print(f"[HermesData] {args[0]}")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS"
        )
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _dispatch(self, handlers, path, qs=None):
        for fn in handlers:
            if qs is not None:
                if fn(path, qs, self):
                    return
            else:
                if fn(path, self):
                    return
        json_response(self, {"error": "Not found"}, 404)

    def do_GET(self):
        parsed = urlparse(self.path)
        self._dispatch(_GET_HANDLERS, unquote(parsed.path), parse_qs(parsed.query))

    def do_POST(self):
        self._dispatch(_POST_HANDLERS, unquote(urlparse(self.path).path))

    def do_PUT(self):
        self._dispatch(_PUT_HANDLERS, unquote(urlparse(self.path).path))

    def do_DELETE(self):
        self._dispatch(_DELETE_HANDLERS, unquote(urlparse(self.path).path))
