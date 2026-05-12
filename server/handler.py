"""server 包 — HermesDataHandler 请求分发。"""

from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote

from . import config_api, fact_api, token_api, pricing_api, dbquery_api, static_files
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
    dbquery_api.handle_post,
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

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        qs = parse_qs(parsed.query)
        for fn in _GET_HANDLERS:
            if fn(path, qs, self):
                return
        json_response(self, {"error": "Not found"}, 404)

    def do_POST(self):
        path = unquote(urlparse(self.path).path)
        for fn in _POST_HANDLERS:
            if fn(path, self):
                return
        json_response(self, {"error": "Not found"}, 404)

    def do_PUT(self):
        path = unquote(urlparse(self.path).path)
        for fn in _PUT_HANDLERS:
            if fn(path, self):
                return
        json_response(self, {"error": "Not found"}, 404)

    def do_DELETE(self):
        path = unquote(urlparse(self.path).path)
        for fn in _DELETE_HANDLERS:
            if fn(path, self):
                return
        json_response(self, {"error": "Not found"}, 404)
