#!/usr/bin/env python3
"""StatsService 测试 — TokenStatsDao 功能测试。

验证 StatsService 类实例化、Provider 接口实现、以及 TokenStatsDao 查询正确性。
"""

import unittest
import sqlite3
import tempfile
import os
from pathlib import Path
from datetime import datetime, timedelta


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

    def _insert_test_data(self, records: list):
        """插入测试数据。

        Args:
            records: list of dicts with token_stats fields
        """
        conn = sqlite3.connect(str(self.access_log_db))
        for r in records:
            conn.execute(
                "INSERT INTO token_stats "
                "(request_id, request_type, model, target_model, request_ts, duration_ms, "
                "input_tokens, output_tokens, cached_read_tokens, cached_write_tokens, "
                "status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    r.get("request_id", "test-1"),
                    r.get("request_type", "chat"),
                    r.get("model", "gpt-4"),
                    r.get("target_model", "qwen3.6-plus"),
                    r.get("request_ts", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                    r.get("duration_ms", 100),
                    r.get("input_tokens", 100),
                    r.get("output_tokens", 200),
                    r.get("cached_read_tokens", 0),
                    r.get("cached_write_tokens", 0),
                    r.get("status", "completed"),
                    r.get("created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                ),
            )
        conn.commit()
        conn.close()

    def _setup_config_db(self):
        """创建 config.db 并填充 upstream/target_models 数据。"""
        conn = sqlite3.connect(str(self.config_db))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS upstreams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                base_url TEXT NOT NULL,
                api_key TEXT DEFAULT '',
                timeout INTEGER DEFAULT 30,
                connect_timeout INTEGER DEFAULT 10,
                ssl_verify INTEGER DEFAULT 1,
                retry INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                is_default INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS target_models (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                upstream_id INTEGER,
                multimodal INTEGER DEFAULT 0,
                format TEXT DEFAULT 'chat'
            )
        """)
        conn.execute(
            "INSERT INTO upstreams (id, base_url) VALUES (?, ?)",
            (1, "https://api.openai.com"),
        )
        conn.execute(
            "INSERT INTO upstreams (id, base_url) VALUES (?, ?)",
            (2, "https://api.anthropic.com"),
        )
        conn.execute(
            "INSERT INTO target_models (id, name, upstream_id) VALUES (?, ?, ?)",
            (1, "qwen3.6-plus", 1),
        )
        conn.execute(
            "INSERT INTO target_models (id, name, upstream_id) VALUES (?, ?, ?)",
            (2, "claude-sonnet-4", 2),
        )
        conn.commit()
        conn.close()

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

    # ─── 空数据库测试 ───

    def test_fetch_summary_empty_db(self):
        """空数据库返回零值汇总。"""
        service = self._create_service()
        result = service.fetch_summary("day")
        self.assertEqual(result["request_count"], 0)
        self.assertEqual(result["input_tokens"], 0)
        self.assertEqual(result["output_tokens"], 0)
        self.assertEqual(result["total_tokens"], 0)

    def test_fetch_by_model_empty_db(self):
        """空数据库返回空模型列表。"""
        service = self._create_service()
        result = service.fetch_by_model("day")
        self.assertEqual(result, [])

    def test_fetch_requests_empty_db(self):
        """空数据库返回空请求列表。"""
        service = self._create_service()
        result = service.fetch_requests("day")
        self.assertEqual(result, [])

    def test_fetch_trend_empty_db(self):
        """空数据库返回空趋势列表。"""
        service = self._create_service()
        result = service.fetch_trend("day")
        self.assertEqual(result, [])

    def test_fetch_by_upstream_empty_map(self):
        """无 upstream_map 时返回空列表。"""
        service = self._create_service()
        result = service.fetch_by_upstream("day")
        self.assertEqual(result, [])

    # ─── fetch_summary 测试 ───

    def test_fetch_summary_basic(self):
        """基本汇总统计正确。"""
        now = datetime.now()
        records = [
            {
                "request_id": "req-1",
                "request_ts": now.strftime("%Y-%m-%d %H:%M:%S"),
                "input_tokens": 100,
                "output_tokens": 200,
                "cached_read_tokens": 10,
                "cached_write_tokens": 5,
                "duration_ms": 150,
            },
            {
                "request_id": "req-2",
                "request_ts": now.strftime("%Y-%m-%d %H:%M:%S"),
                "input_tokens": 300,
                "output_tokens": 400,
                "cached_read_tokens": 20,
                "cached_write_tokens": 15,
                "duration_ms": 250,
            },
        ]
        self._insert_test_data(records)

        service = self._create_service()
        result = service.fetch_summary("day")

        self.assertEqual(result["request_count"], 2)
        self.assertEqual(result["input_tokens"], 400)
        self.assertEqual(result["output_tokens"], 600)
        self.assertEqual(result["cached_read_tokens"], 30)
        self.assertEqual(result["cached_write_tokens"], 20)
        self.assertEqual(result["total_tokens"], 1050)

    def test_fetch_summary_period_filter(self):
        """Period 过滤只统计时间范围内的数据。"""
        now = datetime.now()
        old_ts = (now - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")

        records = [
            {
                "request_id": "req-recent",
                "request_ts": now.strftime("%Y-%m-%d %H:%M:%S"),
                "input_tokens": 100,
                "output_tokens": 100,
            },
            {
                "request_id": "req-old",
                "request_ts": old_ts,
                "input_tokens": 999,
                "output_tokens": 999,
            },
        ]
        self._insert_test_data(records)

        service = self._create_service()
        result = service.fetch_summary("day")

        # 只应统计最近的数据
        self.assertEqual(result["request_count"], 1)
        self.assertEqual(result["input_tokens"], 100)

    # ─── fetch_by_model 测试 ───

    def test_fetch_by_model_basic(self):
        """按模型聚合正确。"""
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        records = [
            {
                "request_id": "req-1",
                "target_model": "qwen3.6-plus",
                "request_ts": ts,
                "input_tokens": 100,
                "output_tokens": 200,
            },
            {
                "request_id": "req-2",
                "target_model": "qwen3.6-plus",
                "request_ts": ts,
                "input_tokens": 50,
                "output_tokens": 100,
            },
            {
                "request_id": "req-3",
                "target_model": "claude-sonnet-4",
                "request_ts": ts,
                "input_tokens": 300,
                "output_tokens": 500,
            },
        ]
        self._insert_test_data(records)

        service = self._create_service()
        result = service.fetch_by_model("day")

        self.assertEqual(len(result), 2)

        # 按 output_tokens 降序排列，claude 应该排在第一位
        self.assertEqual(result[0]["model"], "claude-sonnet-4")
        self.assertEqual(result[0]["output_tokens"], 500)
        self.assertEqual(result[0]["request_count"], 1)

        self.assertEqual(result[1]["model"], "qwen3.6-plus")
        self.assertEqual(result[1]["output_tokens"], 300)
        self.assertEqual(result[1]["request_count"], 2)

    # ─── fetch_requests 测试 ───

    def test_fetch_requests_basic(self):
        """基本查询返回请求列表。"""
        now = datetime.now()
        records = [
            {
                "request_id": "req-1",
                "target_model": "qwen3.6-plus",
                "request_type": "chat",
                "request_ts": now.strftime("%Y-%m-%d %H:%M:%S"),
                "input_tokens": 100,
                "output_tokens": 200,
            },
            {
                "request_id": "req-2",
                "target_model": "claude-sonnet-4",
                "request_type": "messages",
                "request_ts": now.strftime("%Y-%m-%d %H:%M:%S"),
                "input_tokens": 300,
                "output_tokens": 400,
            },
        ]
        self._insert_test_data(records)

        service = self._create_service()
        result = service.fetch_requests("day")

        self.assertEqual(len(result), 2)

    def test_fetch_requests_model_filter(self):
        """按模型过滤正确。"""
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        records = [
            {
                "request_id": "req-1",
                "target_model": "qwen3.6-plus",
                "request_ts": ts,
            },
            {
                "request_id": "req-2",
                "target_model": "claude-sonnet-4",
                "request_ts": ts,
            },
        ]
        self._insert_test_data(records)

        service = self._create_service()
        result = service.fetch_requests("day", model="qwen3.6-plus")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["request_id"], "req-1")

    def test_fetch_requests_type_filter(self):
        """按 request_type 过滤正确。"""
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        records = [
            {
                "request_id": "req-1",
                "request_type": "chat",
                "request_ts": ts,
            },
            {
                "request_id": "req-2",
                "request_type": "messages",
                "request_ts": ts,
            },
        ]
        self._insert_test_data(records)

        service = self._create_service()
        result = service.fetch_requests("day", request_type="messages")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["request_id"], "req-2")

    def test_fetch_requests_pagination(self):
        """分页参数正确。"""
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        # 插入 5 条数据
        for i in range(5):
            self._insert_test_data([
                {
                    "request_id": f"req-{i}",
                    "request_ts": ts,
                }
            ])

        service = self._create_service()
        result = service.fetch_requests("day", limit=2, offset=0)
        self.assertEqual(len(result), 2)

        result = service.fetch_requests("day", limit=2, offset=2)
        self.assertEqual(len(result), 2)

        result = service.fetch_requests("day", limit=2, offset=4)
        self.assertEqual(len(result), 1)

    # ─── fetch_by_model_requests 测试 ───

    def test_fetch_by_model_requests_basic(self):
        """按模型获取请求列表正确。"""
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        records = [
            {
                "request_id": "req-1",
                "target_model": "qwen3.6-plus",
                "request_ts": ts,
            },
            {
                "request_id": "req-2",
                "target_model": "claude-sonnet-4",
                "request_ts": ts,
            },
            {
                "request_id": "req-3",
                "target_model": "qwen3.6-plus",
                "request_ts": ts,
            },
        ]
        self._insert_test_data(records)

        service = self._create_service()
        result = service.fetch_by_model_requests("qwen3.6-plus", "day")

        self.assertEqual(len(result), 2)
        for r in result:
            self.assertEqual(r["target_model"], "qwen3.6-plus")

    # ─── fetch_trend 测试 ───

    def test_fetch_trend_basic(self):
        """基本趋势数据正确。"""
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        records = [
            {
                "request_id": "req-1",
                "request_ts": ts,
                "input_tokens": 100,
                "output_tokens": 200,
            },
            {
                "request_id": "req-2",
                "request_ts": ts,
                "input_tokens": 300,
                "output_tokens": 400,
            },
        ]
        self._insert_test_data(records)

        service = self._create_service()
        result = service.fetch_trend("day")

        # 同一小时内聚合为一组
        self.assertGreaterEqual(len(result), 1)

    # ─── fetch_by_upstream 测试 ───

    def test_fetch_by_upstream_with_config(self):
        """有 config.db 时按上游聚合。"""
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        records = [
            {
                "request_id": "req-1",
                "target_model": "qwen3.6-plus",
                "request_ts": ts,
                "input_tokens": 100,
                "output_tokens": 200,
            },
            {
                "request_id": "req-2",
                "target_model": "claude-sonnet-4",
                "request_ts": ts,
                "input_tokens": 300,
                "output_tokens": 500,
            },
        ]
        self._insert_test_data(records)
        self._setup_config_db()

        service = self._create_service()
        result = service.fetch_by_upstream("day")

        self.assertEqual(len(result), 2)
        # 按 output_tokens 降序
        self.assertEqual(result[0]["upstream"], "https://api.anthropic.com")
        self.assertEqual(result[0]["output_tokens"], 500)
        self.assertEqual(result[1]["upstream"], "https://api.openai.com")
        self.assertEqual(result[1]["output_tokens"], 200)

    def test_fetch_by_upstream_no_config_db(self):
        """没有 config.db 时返回空列表。"""
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        self._insert_test_data([
            {
                "request_id": "req-1",
                "target_model": "qwen3.6-plus",
                "request_ts": ts,
            }
        ])

        service = self._create_service()
        result = service.fetch_by_upstream("day")
        self.assertEqual(result, [])

    # ─── Period 格式兼容测试 ───

    def test_period_day_formats(self):

        """day 和 24h 格式应等效。"""
        service = self._create_service()
        result_day = service.fetch_summary("day")
        result_24h = service.fetch_summary("24h")
        data_keys = ["request_count", "input_tokens", "output_tokens", "total_tokens",
                     "cached_read_tokens", "cached_write_tokens", "avg_duration_ms"]
        for key in data_keys:
            self.assertEqual(result_day[key], result_24h[key], f"Mismatch on {key}")

    def test_period_week_formats(self):
        """week 和 7d 格式应等效。"""
        service = self._create_service()
        result_week = service.fetch_summary("week")
        result_7d = service.fetch_summary("7d")
        data_keys = ["request_count", "input_tokens", "output_tokens", "total_tokens",
                     "cached_read_tokens", "cached_write_tokens", "avg_duration_ms"]
        for key in data_keys:
            self.assertEqual(result_week[key], result_7d[key], f"Mismatch on {key}")

    def test_period_month_formats(self):
        """month 和 30d 格式应等效。"""
        service = self._create_service()
        result_month = service.fetch_summary("month")
        result_30d = service.fetch_summary("30d")
        data_keys = ["request_count", "input_tokens", "output_tokens", "total_tokens",
                     "cached_read_tokens", "cached_write_tokens", "avg_duration_ms"]
        for key in data_keys:
            self.assertEqual(result_month[key], result_30d[key], f"Mismatch on {key}")



class TestUpstreamResolver(unittest.TestCase):
    """_UpstreamResolver 测试。"""

    def setUp(self):
        """创建临时目录和 config.db。"""
        self.tmpdir = tempfile.mkdtemp()
        self.config_db = Path(self.tmpdir) / "config.db"

    def tearDown(self):
        """清理临时文件。"""
        import shutil
        if os.path.exists(self.tmpdir):
            shutil.rmtree(self.tmpdir)

    def _setup_config_db(self):
        """创建 config.db 并填充 upstream/target_models 数据。"""
        conn = sqlite3.connect(str(self.config_db))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS upstreams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                base_url TEXT NOT NULL,
                api_key TEXT DEFAULT '',
                timeout INTEGER DEFAULT 30,
                connect_timeout INTEGER DEFAULT 10,
                ssl_verify INTEGER DEFAULT 1,
                retry INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                is_default INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS target_models (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                upstream_id INTEGER,
                multimodal INTEGER DEFAULT 0,
                format TEXT DEFAULT 'chat'
            )
        """)
        conn.execute(
            "INSERT INTO upstreams (id, base_url) VALUES (?, ?)",
            (1, "https://api.openai.com"),
        )
        conn.execute(
            "INSERT INTO upstreams (id, base_url) VALUES (?, ?)",
            (2, "https://api.anthropic.com"),
        )
        conn.execute(
            "INSERT INTO target_models (id, name, upstream_id) VALUES (?, ?, ?)",
            (1, "qwen3.6-plus", 1),
        )
        conn.execute(
            "INSERT INTO target_models (id, name, upstream_id) VALUES (?, ?, ?)",
            (2, "claude-sonnet-4", 2),
        )
        conn.commit()
        conn.close()

    def test_resolve_existing_model(self):
        """config.db 中存在的 model → 正确返回 upstream。"""
        from stats_service import _UpstreamResolver

        self._setup_config_db()
        resolver = _UpstreamResolver(self.config_db)

        result = resolver.resolve("qwen3.6-plus")
        self.assertEqual(result["upstream_name"], "https://api.openai.com")
        self.assertEqual(result["upstream_url"], "https://api.openai.com")

        result2 = resolver.resolve("claude-sonnet-4")
        self.assertEqual(result2["upstream_name"], "https://api.anthropic.com")
        self.assertEqual(result2["upstream_url"], "https://api.anthropic.com")

    def test_resolve_orphan_model(self):
        """orphan model → __unknown__。"""
        from stats_service import _UpstreamResolver

        self._setup_config_db()
        resolver = _UpstreamResolver(self.config_db)

        result = resolver.resolve("nonexistent-model")
        self.assertEqual(result["upstream_name"], "__unknown__")
        self.assertIsNone(result["upstream_url"])

    def test_resolve_config_db_not_exists(self):
        """config.db 不存在 → 不抛异常，返回 __unknown__。"""
        from stats_service import _UpstreamResolver

        # 不创建 config.db
        resolver = _UpstreamResolver(self.config_db)

        # 不应抛异常
        result = resolver.resolve("any-model")
        self.assertEqual(result["upstream_name"], "__unknown__")
        self.assertIsNone(result["upstream_url"])

    def test_get_all_upstreams(self):
        """get_all_upstreams 返回所有 upstream。"""
        from stats_service import _UpstreamResolver

        self._setup_config_db()
        resolver = _UpstreamResolver(self.config_db)

        upstreams = resolver.get_all_upstreams()
        self.assertEqual(len(upstreams), 2)

        urls = {up["upstream_url"] for up in upstreams}
        self.assertIn("https://api.openai.com", urls)
        self.assertIn("https://api.anthropic.com", urls)

    def test_cache_ttl_refresh(self):
        """缓存过期后自动刷新。"""
        import time
        from stats_service import _UpstreamResolver

        self._setup_config_db()
        resolver = _UpstreamResolver(self.config_db)

        # 首次解析
        result1 = resolver.resolve("qwen3.6-plus")
        self.assertEqual(result1["upstream_name"], "https://api.openai.com")

        # 修改缓存时间为负值，强制过期
        resolver._loaded_at = time.time() - 61

        # 再次解析应触发刷新
        result2 = resolver.resolve("qwen3.6-plus")
        self.assertEqual(result2["upstream_name"], "https://api.openai.com")

    def test_stats_service_uses_resolver(self):
        """StatsService 使用 _UpstreamResolver。"""
        from stats_service import StatsService

        access_log_db = Path(self.tmpdir) / "access_log.db"
        state_db = Path(self.tmpdir) / "state.db"
        cc_switch_db = Path(self.tmpdir) / "cc-switch.db"

        # 创建空 access_log.db
        conn = sqlite3.connect(str(access_log_db))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS token_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                request_type TEXT NOT NULL,
                model TEXT NOT NULL,
                target_model TEXT NOT NULL,
                request_ts TEXT NOT NULL,
                duration_ms INTEGER,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                cached_read_tokens INTEGER DEFAULT 0,
                cached_write_tokens INTEGER DEFAULT 0,
                status TEXT DEFAULT 'completed',
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

        self._setup_config_db()

        service = StatsService(
            access_log_db_path=str(access_log_db),
            config_db_path=str(self.config_db),
            state_db_path=str(state_db),
            cc_switch_db_path=str(cc_switch_db),
        )

        # 验证 resolver 已初始化
        self.assertIsNotNone(service._upstream_resolver)

        # 验证 resolve 方法可用
        result = service._resolve_upstream("qwen3.6-plus")
        self.assertEqual(result["upstream_name"], "https://api.openai.com")


if __name__ == "__main__":
    unittest.main()
