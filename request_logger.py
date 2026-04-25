"""Request Logger — 请求/响应日志记录和 Token 统计。"""

import json
import sqlite3
import uuid
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional


class RequestLogger:
    """请求日志记录器，短连接方案：每次写入时创建/关闭 SQLite 连接。"""

    def __init__(self, db_path: Path, debug_retention_days: int = 7):
        """初始化：创建目录、建表、WAL 模式、清理过期数据。"""
        self.db_path = db_path
        self.debug_retention_days = debug_retention_days

        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = self._get_conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS debug_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id  TEXT NOT NULL,
                    stage       TEXT NOT NULL,
                    model       TEXT,
                    target_model TEXT,
                    status_code INTEGER,
                    data        TEXT,
                    created_at  TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_debug_request_id ON debug_log(request_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_debug_created_at ON debug_log(created_at)")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS token_stats (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id          TEXT NOT NULL,
                    agent               TEXT NOT NULL,
                    model               TEXT NOT NULL,
                    target_model        TEXT NOT NULL,
                    request_ts          TEXT NOT NULL,
                    duration_ms         INTEGER,
                    input_tokens        INTEGER DEFAULT 0,
                    output_tokens       INTEGER DEFAULT 0,
                    cached_read_tokens  INTEGER DEFAULT 0,
                    cached_write_tokens INTEGER DEFAULT 0,
                    status              TEXT DEFAULT 'completed',
                    created_at          TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_token_request_ts ON token_stats(request_ts)")

            conn.commit()
        finally:
            conn.close()

        self._cleanup_expired()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def close(self):
        pass

    def log_raw_request(self, request_id: str, model: str, target: str, body: str | dict):
        """阶段 1：记录 agent 原始请求。"""
        try:
            conn = self._get_conn()
            try:
                data = body if isinstance(body, str) else json.dumps(body)
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                conn.execute(
                    "INSERT INTO debug_log (request_id, stage, model, target_model, data, created_at) "
                    "VALUES (?, 'raw_request', ?, ?, ?, ?)",
                    (request_id, model, target, data, now),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logging.warning(f"日志写入失败 (raw_request): {e}")

    def log_converted_request(self, request_id: str, model: str, target: str, body: dict):
        """阶段 2：记录 proxy 转换后的请求。"""
        try:
            conn = self._get_conn()
            try:
                data = json.dumps(body)
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                conn.execute(
                    "INSERT INTO debug_log (request_id, stage, model, target_model, data, created_at) "
                    "VALUES (?, 'converted_request', ?, ?, ?, ?)",
                    (request_id, model, target, data, now),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logging.warning(f"日志写入失败 (converted_request): {e}")

    def log_upstream_response(self, request_id: str, status_code: int, body: str | dict, duration_ms: int):
        """阶段 3：记录上游原始响应。"""
        try:
            conn = self._get_conn()
            try:
                data = body if isinstance(body, str) else json.dumps(body)
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                conn.execute(
                    "INSERT INTO debug_log (request_id, stage, status_code, data, created_at) "
                    "VALUES (?, 'upstream_response', ?, ?, ?)",
                    (request_id, status_code, data, now),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logging.warning(f"日志写入失败 (upstream_response): {e}")

    def log_converted_response(self, request_id: str, model: str, target: str, body: dict):
        """阶段 4：记录 proxy 转换后的响应。"""
        try:
            conn = self._get_conn()
            try:
                data = json.dumps(body)
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                conn.execute(
                    "INSERT INTO debug_log (request_id, stage, model, target_model, data, created_at) "
                    "VALUES (?, 'converted_response', ?, ?, ?, ?)",
                    (request_id, model, target, data, now),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logging.warning(f"日志写入失败 (converted_response): {e}")

    def log_token_stats(self, request_id: str, agent: str, model: str, target_model: str,
                        request_ts: str, duration_ms: int, input_tokens: int,
                        output_tokens: int, cached_read: int, cached_write: int,
                        status: str):
        """写入 Token 统计记录。"""
        try:
            conn = self._get_conn()
            try:
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                conn.execute(
                    "INSERT INTO token_stats "
                    "(request_id, agent, model, target_model, request_ts, duration_ms, "
                    "input_tokens, output_tokens, cached_read_tokens, cached_write_tokens, status, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (request_id, agent, model, target_model, request_ts, duration_ms,
                     input_tokens, output_tokens, cached_read, cached_write, status, now),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logging.warning(f"日志写入失败 (token_stats): {e}")

    def _cleanup_expired(self):
        """启动时清理超过 debug_retention_days 的 debug_log 记录。"""
        try:
            conn = self._get_conn()
            try:
                conn.execute(
                    "DELETE FROM debug_log WHERE created_at < datetime('now', 'localtime', ?)",
                    (f"-{self.debug_retention_days} days",),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logging.warning(f"日志清理失败: {e}")


def _generate_request_id() -> str:
    return uuid.uuid4().hex[:16]


def _extract_agent(user_agent: str) -> str:
    if "codex" in user_agent.lower():
        return "codex"
    return "unknown"


_logger: Optional[RequestLogger] = None


def init_logger(db_path: Path, retention_days: int = 7) -> RequestLogger:
    global _logger
    _logger = RequestLogger(db_path, retention_days)
    return _logger


def get_logger() -> Optional[RequestLogger]:
    return _logger
