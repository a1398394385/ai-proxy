"""server 包 — 静态文件服务。"""

import os

from .common import json_response


def handle_get(path, qs, handler) -> bool:
    """处理静态文件 GET 请求。始终返回 True（作为兜底 Handler）。"""
    static_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static"
    )
    file_path = os.path.join(static_dir, path.lstrip("/"))
    if path == "/":
        file_path = os.path.join(static_dir, "index.html")
    real_file = os.path.realpath(file_path)
    real_static = os.path.realpath(static_dir)
    if not real_file.startswith(real_static + os.sep):
        handler.send_response(403)
        handler.end_headers()
        return True
    if os.path.isfile(file_path):
        ext_map = {
            ".html": "text/html",
            ".css": "text/css",
            ".js": "application/javascript",
        }
        ext = os.path.splitext(file_path)[1]
        mime = ext_map.get(ext, "application/octet-stream")
        with open(file_path, "rb") as f:
            body = f.read()
        handler.send_response(200)
        handler.send_header("Content-Type", f"{mime}; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
    else:
        handler.send_response(404)
        handler.end_headers()
    return True
