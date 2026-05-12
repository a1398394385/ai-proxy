#!/usr/bin/env python3
"""Codex Responses API → Chat Completions 转换代理（瘦入口）。"""

import os
import time
import gzip
import logging
from http.server import HTTPServer
from socketserver import ThreadingMixIn
from pathlib import Path

from proxy.common import CONFIG, load_config
from proxy.request_logger import init_logger as init_request_logger
from proxy.handler import ProxyHandler

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


# ─── 主入口 ────────────────────────────────────────────────────────


def main():
    import argparse

    parser = argparse.ArgumentParser(description="AI Proxy")
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
    from proxy.response_store import ResponseStore as _ResponseStore

    _store_cfg = CONFIG.get("response_store", {})
    server.response_store = _ResponseStore(
        max_entries=_store_cfg.get("max_entries", 1000),
        ttl_seconds=_store_cfg.get("ttl_seconds", 3600),
    )
    logging.info(f"AI Proxy 启动: http://{host}:{port}")

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
