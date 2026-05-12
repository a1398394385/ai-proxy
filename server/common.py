"""server 包 — 共享工具模块。

提供 JSON 响应、请求体读取、DB 连接上下文管理器等公共接口。
"""

import json
import os
import http.client
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from proxy.config_manager import ConfigDB, Migrations
from proxy.pricing_manager import PricingDB
from proxy.common import get_port, get_host, load_config, CONFIG_PATH


# ─── 路径常量 ───

DB_PATH = os.path.expanduser("~/.hermes/memory_store.db")
CONFIG_DB_PATH = Path(os.path.expanduser("~/.hermes/config.db"))
ACCESS_LOG_DB_PATH = Path("data/access_log.db")
STATE_DB_PATH = os.path.expanduser("~/.hermes/state.db")
MAX_BODY_SIZE = 10 * 1024 * 1024  # 10MB

# ─── 配置加载 ───

load_config(CONFIG_PATH)
HOST = get_host("data_browser", "127.0.0.1")
PORT = get_port("data_browser", 18742)


# ─── 响应工具 ───


def json_response(handler, data, status=200):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


def row_to_dict(row):
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, bytes):
            d[k] = None
    return d


def _read_json(handler):
    length = int(handler.headers.get("Content-Length", 0))
    if length > MAX_BODY_SIZE:
        json_response(handler, {"error": "Request body too large"}, 413)
        return None
    body = handler.rfile.read(length)
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        json_response(handler, {"error": "Invalid JSON"}, 400)
        return None


# ─── DB 上下文管理器 ───


@contextmanager
def config_db():
    db = ConfigDB(CONFIG_DB_PATH)
    try:
        yield db
    finally:
        db.close()


@contextmanager
def pricing_db():
    db = PricingDB(CONFIG_DB_PATH)
    try:
        yield db
    finally:
        db.close()


@contextmanager
def fact_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def state_db():
    conn = sqlite3.connect(STATE_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def access_log_db():
    conn = sqlite3.connect(str(ACCESS_LOG_DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# ─── 代理通知 ───


def _reload_proxies():
    try:
        proxy_port = get_port("codex_proxy", 48743)
        conn = http.client.HTTPConnection("127.0.0.1", proxy_port, timeout=2)
        conn.request("POST", "/admin/reload")
        conn.getresponse().read()
        conn.close()
    except Exception:
        pass
