"""统一路径管理 — 零内部依赖，供 proxy 和 server 共用。"""

from pathlib import Path

DATA_DIR = Path.home() / ".ai-agent-tools" / "data"
DATA_DB = DATA_DIR / "access_log.db"


def get_data_path(*parts: str) -> Path:
    """返回 ~/.ai-agent-tools/data/ 下的子路径。"""
    return DATA_DIR.joinpath(*parts)
