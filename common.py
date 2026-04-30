#!/usr/bin/env python3
"""共享代码模块：配置、模型解析、上游连接、路径/模型工具函数。

所有函数和变量从 proxy.py 迁移而来，供 proxy.py 和未来新服务公用。
"""

import os
import sys
import json
import time
import ssl
import logging
import http.client
import urllib.parse
from pathlib import Path

from config_manager import ConfigCache, _parse_yaml, _yaml_scalar

from request_logger import (
    get_logger,
    _generate_request_id,
    _extract_agent,
)

from token_stats import record_token_stats

# ─── 配置变量 ──────────────────────────────────────────────────────────

CONFIG = {}
CONFIG_PATH = Path(__file__).parent / "proxy_config.yaml"

# ─── 动态配置缓存（替代静态 model_map）─────────────────────────────
CONFIG_DB_PATH = Path(os.path.expanduser("~/.hermes/config.db"))
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

        log_file = Path(CONFIG_PATH).parent / "proxy.log"
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


# ─── 上游连接创建 ─────────────────────────────────────────────────────

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


# ─── 透传辅助函数 ───────────────────────────────────────────────────────

def _normalize_forward_path(path: str):
    """归一化透传请求路径，返回安全路径或 None（含路径穿越时）。"""
    query = ""
    if "?" in path:
        path, query = path.split("?", 1)

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


# ─── 服务端口/主机工具 ─────────────────────────────────────────────────

def get_port(service_name: str, default_port: int = None) -> int:
    """Read port from CONFIG['ports'][service_name], with fallback to old proxy.port."""
    ports_cfg = CONFIG.get("ports", {})
    svc = ports_cfg.get(service_name, {})
    if "port" in svc:
        return svc["port"]
    # Fallback: old proxy.port for codex_proxy
    if service_name == "codex_proxy":
        return CONFIG.get("proxy", {}).get("port", default_port or 48743)
    return default_port


def get_host(service_name: str, default_host: str = "127.0.0.1") -> str:
    ports_cfg = CONFIG.get("ports", {})
    svc = ports_cfg.get(service_name, {})
    return svc.get("host", default_host)
