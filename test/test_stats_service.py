#!/usr/bin/env python3
"""StatsService 测试 — 骨架验证。

验证 StatsService 类可以正确实例化，Provider 接口方法签名存在。
"""

import unittest
import sqlite3
import tempfile
import os
from pathlib import Path


class TestStatsService(unittest.TestCase):
    """StatsService 基础测试。"""

    def setUp(self):
        """创建临时目录和空 token_stats 表。"""
        self.tmpdir = tempfile.mkdtemp()
        self.access_log_db = Path(self.tmpdir) / "access_log.db"
        self.config_db = Path(self.tmpdir) / "config.db"
        self.state_db = Path(self.tmpdir) / "state.db"
        self.cc_switch_db = Path(self.tmpdir) / "cc-switch.db"

        # 创建空 token_stats 表（复用 token_stats.py 的建表 SQL）
        conn = sqlite3.connect(str(self.access_log_db))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS token_stats (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id          TEXT NOT NULL,
                request_type        TEXT NOT NULL,
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
        conn.commit()
        conn.close()

    def tearDown(self):
        """清理临时文件。"""
        for db_path in [self.access_log_db, self.config_db, self.state_db, self.cc_switch_db]:
            if db_path.exists():
                os.remove(str(db_path))
        # 清理可能存在的 -wal / -shm 文件
        for suffix in ["-wal", "-shm"]:
            wal_path = Path(str(self.access_log_db) + suffix)
            if wal_path.exists():
                os.remove(str(wal_path))
        os.rmdir(self.tmpdir)

    def _create_service(self):
        """创建 StatsService 实例。"""
        from stats_service import StatsService
        return StatsService(
            access_log_db_path=str(self.access_log_db),
            config_db_path=str(self.config_db),
            state_db_path=str(self.state_db),
            cc_switch_db_path=str(self.cc_switch_db),
        )

    # ─── 实例化测试 ───

    def test_instantiation(self):
        """验证 StatsService 可以正确实例化。"""
        service = self._create_service()
        self.assertIsNotNone(service)
        self.assertEqual(service.access_log_db_path, self.access_log_db)
        self.assertEqual(service.config_db_path, self.config_db)
        self.assertEqual(service.state_db_path, self.state_db)
        self.assertEqual(service.cc_switch_db_path, self.cc_switch_db)

    # ─── Provider 接口签名存在测试 ───

    def test_fetch_by_model_exists(self):
        """fetch_by_model 方法签名存在。"""
        service = self._create_service()
        self.assertTrue(hasattr(service, "fetch_by_model"))
        self.assertTrue(callable(getattr(service, "fetch_by_model")))

    def test_fetch_requests_exists(self):
        """fetch_requests 方法签名存在。"""
        service = self._create_service()
        self.assertTrue(hasattr(service, "fetch_requests"))
        self.assertTrue(callable(getattr(service, "fetch_requests")))

    def test_fetch_by_upstream_exists(self):
        """fetch_by_upstream 方法签名存在。"""
        service = self._create_service()
        self.assertTrue(hasattr(service, "fetch_by_upstream"))
        self.assertTrue(callable(getattr(service, "fetch_by_upstream")))

    def test_fetch_trend_exists(self):
        """fetch_trend 方法签名存在。"""
        service = self._create_service()
        self.assertTrue(hasattr(service, "fetch_trend"))
        self.assertTrue(callable(getattr(service, "fetch_trend")))

    def test_fetch_summary_exists(self):
        """fetch_summary 方法签名存在。"""
        service = self._create_service()
        self.assertTrue(hasattr(service, "fetch_summary"))
        self.assertTrue(callable(getattr(service, "fetch_summary")))

    def test_fetch_by_model_requests_exists(self):
        """fetch_by_model_requests 方法签名存在。"""
        service = self._create_service()
        self.assertTrue(hasattr(service, "fetch_by_model_requests"))
        self.assertTrue(callable(getattr(service, "fetch_by_model_requests")))

    # ─── NotImplementedError 测试 ───

    def test_fetch_by_model_raises_not_implemented(self):
        """fetch_by_model 尚未实现，应抛出 NotImplementedError。"""
        service = self._create_service()
        with self.assertRaises(NotImplementedError):
            service.fetch_by_model("24h")

    def test_fetch_requests_raises_not_implemented(self):
        """fetch_requests 尚未实现，应抛出 NotImplementedError。"""
        service = self._create_service()
        with self.assertRaises(NotImplementedError):
            service.fetch_requests("24h")

    def test_fetch_by_upstream_raises_not_implemented(self):
        """fetch_by_upstream 尚未实现，应抛出 NotImplementedError。"""
        service = self._create_service()
        with self.assertRaises(NotImplementedError):
            service.fetch_by_upstream("24h")

    def test_fetch_trend_raises_not_implemented(self):
        """fetch_trend 尚未实现，应抛出 NotImplementedError。"""
        service = self._create_service()
        with self.assertRaises(NotImplementedError):
            service.fetch_trend("24h")

    def test_fetch_summary_raises_not_implemented(self):
        """fetch_summary 尚未实现，应抛出 NotImplementedError。"""
        service = self._create_service()
        with self.assertRaises(NotImplementedError):
            service.fetch_summary("24h")

    def test_fetch_by_model_requests_raises_not_implemented(self):
        """fetch_by_model_requests 尚未实现，应抛出 NotImplementedError。"""
        service = self._create_service()
        with self.assertRaises(NotImplementedError):
            service.fetch_by_model_requests("qwen3.6-plus", "24h")


if __name__ == "__main__":
    unittest.main()
