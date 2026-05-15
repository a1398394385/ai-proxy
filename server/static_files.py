"""server 包 — 静态文件服务。"""

import mimetypes
import os

from .common import json_response

# 模块级缓存（启动时计算一次）
_STATIC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static"
)
_REAL_STATIC = os.path.realpath(_STATIC_DIR)

mimetypes.init()


def handle_get(path, qs, handler) -> bool:
    """处理静态文件 GET 请求。始终返回 True（作为兜底 Handler）。"""
    if path == "/":
        file_path = os.path.join(_STATIC_DIR, "index.html")
    else:
        file_path = os.path.join(_STATIC_DIR, path.lstrip("/"))

    # 路径遍历保护
    real_file = os.path.realpath(file_path)
    if not real_file.startswith(_REAL_STATIC + os.sep):
        handler.send_response(403)
        handler.end_headers()
        return True

    if not os.path.isfile(file_path):
        handler.send_response(404)
        handler.end_headers()
        return True

    # MIME 类型检测
    mime, _ = mimetypes.guess_type(file_path)
    if mime is None:
        mime = "application/octet-stream"

    with open(file_path, "rb") as f:
        body = f.read()

    handler.send_response(200)
    # 二进制文件不加 charset
    if mime.startswith("text/") or mime in ("application/javascript", "application/json"):
        handler.send_header("Content-Type", f"{mime}; charset=utf-8")
    else:
        handler.send_header("Content-Type", mime)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "public, max-age=60")
    handler.end_headers()
    handler.wfile.write(body)
    return True
