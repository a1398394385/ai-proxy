"""server 包 — Hermes Data Browser 后端。

提供 Fact Store 浏览、Token 使用统计、模型路由管理等 API。
"""

import os
from http.server import HTTPServer

from .common import HOST, PORT, DB_PATH, STATE_DB_PATH  # noqa: F401
from proxy.paths import DATA_DB
from .common import json_response, _read_json, config_db, pricing_db, fact_db, state_db, access_log_db  # noqa: F401
from .common import _reload_proxies, row_to_dict, MAX_BODY_SIZE  # noqa: F401
from .handler import HermesDataHandler  # noqa: F401


def main():
    if not os.path.exists(DB_PATH):
        print(f"Database not found: {DB_PATH}")
        return
    if not os.path.exists(STATE_DB_PATH):
        print(f"Warning: State database not found: {STATE_DB_PATH}")
        print("Token statistics will not be available.")

    # 初始化 StatsService
    try:
        from stats_service import StatsService

        stats = StatsService(
            access_log_db_path=str(DATA_DB),
            data_db_path=str(DATA_DB),
            state_db_path=STATE_DB_PATH,
        )
        HermesDataHandler.stats_service = stats
    except Exception as e:
        print(f"Warning: StatsService 初始化失败: {e}")
        print("Token 统计和计费 API 将不可用")

    server = HTTPServer((HOST, PORT), HermesDataHandler)
    print("=" * 50)
    print("Hermes Data Browser")
    print("=" * 50)
    print(f"访问地址: http://{HOST}:{PORT}")
    print("功能: Fact Store + Token 使用统计")
    print("按 Ctrl+C 停止")
    print("=" * 50)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭...")
        server.server_close()
