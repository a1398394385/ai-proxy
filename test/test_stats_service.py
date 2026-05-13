#!/usr/bin/env python3
"""StatsService 测试 — TokenStatsDao 功能测试。

验证 StatsService 类实例化、Provider 接口实现、以及 TokenStatsDao 查询正确性。
"""

import unittest
import sqlite3
import tempfile
import os
import time
from pathlib import Path
from datetime import datetime, timedelta
from tempfile import TemporaryDirectory


class TestStatsService(unittest.TestCase):
    """StatsService 基础测试。"""

    def setUp(self):
        """创建临时目录和空 token_stats 表。"""
        self.tmpdir = tempfile.mkdtemp()
        self.data_db = Path(self.tmpdir) / "access_log.db"
        self.config_db = self.data_db
        self.state_db = Path(self.tmpdir) / "state.db"
        # 创建空 token_stats 表（复用 token_stats.py 的建表 SQL）
        conn = sqlite3.connect(str(self.data_db))
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
                upstream_id INTEGER,
                status              TEXT DEFAULT 'completed',
                created_at          TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

        # 创建 sessions 表
        self.state_db = Path(self.tmpdir) / "state.db"
        state_conn = sqlite3.connect(str(self.state_db))
        state_conn.execute("""CREATE TABLE sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model TEXT,
            started_at REAL NOT NULL,
            input_tokens INTEGER,
            output_tokens INTEGER,
            cache_read_tokens INTEGER DEFAULT 0,
            cache_write_tokens INTEGER DEFAULT 0,
            message_count INTEGER DEFAULT 0
        )""")
        state_conn.commit()
        state_conn.close()

    def tearDown(self):
        """清理临时文件。"""
        for db_path in [self.data_db, self.config_db, self.state_db]:
            if db_path.exists():
                os.remove(str(db_path))
        # 清理可能存在的 -wal / -shm 文件
        for suffix in ["-wal", "-shm"]:
            wal_path = Path(str(self.data_db) + suffix)
            if wal_path.exists():
                os.remove(str(wal_path))
        os.rmdir(self.tmpdir)

    def _create_service(self):
        """创建 StatsService 实例。"""
        from stats_service import StatsService

        return StatsService(
            data_db_path=str(self.data_db),
            state_db_path=str(self.state_db),
            opencode_db_path=str(Path(self.tmpdir) / "nonexistent_opencode.db"),
        )

    def _insert_test_data(self, records: list):
        """插入测试数据。

        Args:
            records: list of dicts with token_stats fields
        """
        conn = sqlite3.connect(str(self.data_db))
        for r in records:
            conn.execute(
                "INSERT INTO token_stats "
                "(request_id, request_type, model, target_model, request_ts, duration_ms, "
                "input_tokens, output_tokens, cached_read_tokens, cached_write_tokens, "
                "upstream_id, "
                "status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    r.get("upstream_id"),
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
                name TEXT UNIQUE NOT NULL,
                base_url TEXT NOT NULL,
                api_key TEXT DEFAULT '',
                timeout INTEGER DEFAULT 30,
                connect_timeout INTEGER DEFAULT 10,
                ssl_verify INTEGER DEFAULT 1,
                retry INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS target_models (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                upstream_id INTEGER,
                multimodal INTEGER DEFAULT 0
            )
        """)
        conn.execute(
            "INSERT INTO upstreams (id, name, base_url) VALUES (?, ?, ?)",
            (1, "OpenAI", "https://api.openai.com"),
        )
        conn.execute(
            "INSERT INTO upstreams (id, name, base_url) VALUES (?, ?, ?)",
            (2, "Anthropic", "https://api.anthropic.com"),
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
        self.assertEqual(service.data_db_path, self.data_db)
        self.assertEqual(service.data_db_path, self.config_db)
        self.assertEqual(service.state_db_path, self.state_db)

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
        self.assertEqual(result["requests"], [])
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["limit"], 50)
        self.assertEqual(result["offset"], 0)

    def test_fetch_trend_empty_db(self):
        """空数据库返回空趋势列表。"""
        service = self._create_service()
        result = service.fetch_trend("day")
        self.assertEqual(result, [])

    def test_fetch_by_upstream_empty_map(self):
        """无 upstream_map 时返回空列表。"""
        service = self._create_service()
        result = service.fetch_by_upstream("day")
        self.assertEqual(result, {"upstreams": []})

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
        self.assertEqual(result["cache_read_tokens"], 30)
        self.assertEqual(result["cache_write_tokens"], 20)
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

        self.assertEqual(len(result["requests"]), 2)
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["limit"], 50)

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

        self.assertEqual(len(result["requests"]), 1)
        self.assertEqual(result["requests"][0]["request_id"], "req-1")

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

        self.assertEqual(len(result["requests"]), 1)
        self.assertEqual(result["requests"][0]["request_id"], "req-2")

    def test_fetch_requests_pagination(self):
        """分页参数正确。"""
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        for i in range(5):
            self._insert_test_data(
                [
                    {
                        "request_id": f"req-{i}",
                        "request_ts": ts,
                    }
                ]
            )

        service = self._create_service()
        result = service.fetch_requests("day", limit=2, offset=0)
        self.assertEqual(len(result["requests"]), 2)
        self.assertEqual(result["limit"], 2)
        self.assertEqual(result["offset"], 0)

        result = service.fetch_requests("day", limit=2, offset=2)
        self.assertEqual(len(result["requests"]), 2)
        self.assertEqual(result["offset"], 2)

        result = service.fetch_requests("day", limit=2, offset=4)
        self.assertEqual(len(result["requests"]), 1)

    # ─── fetch_by_model_requests 测试 ───

    def test_fetch_by_model_requests_basic(self):
        """按模型获取请求列表正确。"""
        now = datetime.now()
        jm = now.strftime("%Y-%m-%d %H:%M:%S")
        rm = [
            {
                "request_id": "req-1",
                "target_model": "qwen3.6-plus",
                "request_ts": jm,
            },
            {
                "request_id": "req-2",
                "target_model": "claude-sonnet-4",
                "request_ts": jm,
            },
            {
                "request_id": "req-3",
                "target_model": "qwen3.6-plus",
                "request_ts": jm,
            },
        ]
        self._insert_test_data(rm)

        service = self._create_service()
        result = service.fetch_by_model_requests("qwen3.6-plus", "day")

        # 返回 dict 结构
        self.assertEqual(result["model"], "qwen3.6-plus")
        self.assertEqual(len(result["requests"]), 2)
        for r in result["requests"]:
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

    def test_fetch_trend_includes_estimated_cost_cny(self):
        """趋势数据应包含 estimated_cost_cny 字段。"""
        from proxy.pricing_manager import PricingDB

        PricingDB(self.config_db)  # 建表 + 种子数据

        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        self._insert_test_data(
            [
                {
                    "request_id": "req-1",
                    "target_model": "claude-sonnet-4-6-20260217",
                    "request_ts": ts,
                    "input_tokens": 1000,
                    "output_tokens": 2000,
                },
            ]
        )

        service = self._create_service()
        result = service.fetch_trend("day")
        self.assertGreater(len(result), 0)
        for point in result:
            self.assertIn("estimated_cost_cny", point)
            self.assertIsInstance(point["estimated_cost_cny"], (int, float))
        # 有趋势数据时成本应为正值（claude-sonnet-4-6 有种子定价）
        if result:
            self.assertGreater(result[0]["estimated_cost_cny"], 0)

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
                "upstream_id": 1,
            },
            {
                "request_id": "req-2",
                "target_model": "claude-sonnet-4",
                "request_ts": ts,
                "input_tokens": 300,
                "output_tokens": 500,
                "upstream_id": 2,
            },
        ]
        self._insert_test_data(records)
        self._setup_config_db()

        service = self._create_service()
        result = service.fetch_by_upstream("day")

        self.assertEqual(len(result["upstreams"]), 2)
        upstreams = result["upstreams"]
        # 按 total_output DESC（Anthropic 500 > OpenAI 200）
        self.assertEqual(upstreams[0]["upstream_id"], 2)
        self.assertEqual(upstreams[0]["upstream_name"], "Anthropic")
        self.assertEqual(upstreams[0]["output_tokens"], 500)
        self.assertEqual(upstreams[1]["upstream_id"], 1)
        self.assertEqual(upstreams[1]["upstream_name"], "OpenAI")
        self.assertEqual(upstreams[1]["output_tokens"], 200)
        # 验证返回格式包含所有必需字段
        for up in upstreams:
            self.assertIn("upstream_id", up)
            self.assertIn("upstream_name", up)
            self.assertIn("base_url", up)
            self.assertIn("request_count", up)
            self.assertIn("input_tokens", up)
            self.assertIn("output_tokens", up)
            self.assertIn("cache_read_tokens", up)
            self.assertIn("cache_write_tokens", up)
            self.assertIn("total_tokens", up)
            self.assertIn("estimated_cost_cny", up)

    def test_fetch_by_upstream_no_config_db(self):
        """没有 config.db 时返回空列表。"""
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        self._insert_test_data(
            [
                {
                    "request_id": "req-1",
                    "target_model": "qwen3.6-plus",
                    "request_ts": ts,
                }
            ]
        )

        service = self._create_service()
        result = service.fetch_by_upstream("day")
        self.assertEqual(result, {"upstreams": []})

    # ─── Period 格式兼容测试 ───

    def test_period_day_formats(self):
        """day 和 24h 格式应等效。"""
        service = self._create_service()
        result_day = service.fetch_summary("day")
        result_24h = service.fetch_summary("24h")
        data_keys = [
            "request_count",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "avg_duration_ms",
        ]
        for key in data_keys:
            self.assertEqual(result_day[key], result_24h[key], f"Mismatch on {key}")

    def test_period_week_formats(self):
        """week 和 7d 格式应等效。"""
        service = self._create_service()
        result_week = service.fetch_summary("week")
        result_7d = service.fetch_summary("7d")
        data_keys = [
            "request_count",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "avg_duration_ms",
        ]
        for key in data_keys:
            self.assertEqual(result_week[key], result_7d[key], f"Mismatch on {key}")

    def test_period_month_formats(self):
        """month 和 30d 格式应等效。"""
        service = self._create_service()
        result_month = service.fetch_summary("month")
        result_30d = service.fetch_summary("30d")
        data_keys = [
            "request_count",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "avg_duration_ms",
        ]
        for key in data_keys:
            self.assertEqual(result_month[key], result_30d[key], f"Mismatch on {key}")


class TestCostCalculator(unittest.TestCase):
    """_CostCalculator 测试（通过 PricingDB + config.db）。"""

    def setUp(self):
        """创建临时目录及 PricingDB 实例。"""
        self.tmpdir = tempfile.mkdtemp()
        self.data_db_path = Path(self.tmpdir) / "config.db"
        from proxy.pricing_manager import PricingDB

        db = PricingDB(self.data_db_path)  # 建表 + 种子数据
        # 添加自定义测试模型（避免与种子数据冲突）
        db.add_pricing(
            {
                "model_id": "test-usd-model",
                "display_name": "Test USD",
                "input_cost_per_million": "2.5",
                "output_cost_per_million": "10.0",
                "cache_read_cost_per_million": "0.5",
                "cache_creation_cost_per_million": "2.0",
                "currency": "USD",
            }
        )

    def tearDown(self):
        """清理临时文件。"""
        import shutil

        if os.path.exists(self.tmpdir):
            shutil.rmtree(self.tmpdir)

    def _create_calculator(self):
        """创建 _CostCalculator 实例。"""
        from stats_service import _CostCalculator

        return _CostCalculator(self.data_db_path)

    def test_calculate_known_model(self):
        """已知模型返回正确成本（USD 自动 × 7 转为人民币）。"""
        calc = self._create_calculator()
        cost = calc.calculate("test-usd-model", 1000, 2000, 500, 300)
        # USD 定价 × 7：input=17.5, output=70.0, cache_read=3.5, cache_write=14.0 (RMB)
        expected = (
            1000 / 1_000_000 * 2.5 * 7
            + 2000 / 1_000_000 * 10.0 * 7
            + 500 / 1_000_000 * 0.5 * 7
            + 300 / 1_000_000 * 2.0 * 7
        )
        self.assertAlmostEqual(cost, expected, places=10)

    def test_calculate_unknown_model(self):
        """未知模型返回 0。"""
        calc = self._create_calculator()
        cost = calc.calculate("nonexistent-model", 1000, 2000, 0, 0)
        self.assertEqual(cost, 0)

    def test_calculate_rmb_model(self):
        """RMB 定价直接使用不换算。"""
        from proxy.pricing_manager import PricingDB

        db = PricingDB(self.data_db_path)
        db.add_pricing(
            {
                "model_id": "test-rmb-model",
                "display_name": "Test RMB",
                "input_cost_per_million": "10",
                "output_cost_per_million": "50",
                "currency": "RMB",
            }
        )
        calc = self._create_calculator()
        cost = calc.calculate("test-rmb-model", 1_000_000, 500_000, 0, 0)
        expected = 10 + 50 * 0.5  # 10 input + 25 output = 35 RMB
        self.assertAlmostEqual(cost, expected, places=6)

    def test_calculate_zero_tokens(self):
        """零 tokens 返回 0。"""
        calc = self._create_calculator()
        cost = calc.calculate("claude-sonnet-4-6-20260217", 0, 0, 0, 0)
        self.assertEqual(cost, 0)

    def test_calculate_none_tokens(self):
        """None tokens 返回 0。"""
        calc = self._create_calculator()
        cost = calc.calculate("claude-sonnet-4-6-20260217", None, None, None, None)
        self.assertEqual(cost, 0)

    def test_get_pricing(self):
        """get_pricing 返回正确的定价字典（人民币值）。"""
        calc = self._create_calculator()
        pricing = calc.get_pricing()
        self.assertIn("test-usd-model", pricing)
        # 2.5 USD × 7 = 17.5 RMB
        self.assertAlmostEqual(
            pricing["test-usd-model"]["input_cost"], 2.5 * 7, places=6
        )
        self.assertAlmostEqual(
            pricing["test-usd-model"]["output_cost"], 10.0 * 7, places=6
        )

    def test_get_pricing_cache_reused(self):
        """无失效时缓存在多次调用间复用（第二次调用不重新加载）。"""
        calc = self._create_calculator()
        pricing1 = calc.get_pricing()
        # 不清缓存，直接调用
        pricing2 = calc.get_pricing()
        self.assertIs(pricing1, pricing2)  # 同一对象（命中缓存）


class TestStatsServiceCostCalculation(unittest.TestCase):
    """StatsService.calculate_cost 和 get_pricing 委托测试（通过 PricingDB）。"""

    def setUp(self):
        """创建临时目录和空 access_log.db。"""
        self.tmpdir = tempfile.mkdtemp()
        self.data_db = Path(self.tmpdir) / "access_log.db"
        self.config_db = self.data_db
        self.state_db = Path(self.tmpdir) / "state.db"

        conn = sqlite3.connect(str(self.data_db))
        conn.execute("CREATE TABLE IF NOT EXISTS token_stats (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

    def tearDown(self):
        """清理临时文件。"""
        import shutil

        if os.path.exists(self.tmpdir):
            shutil.rmtree(self.tmpdir)

    def _create_service(self):
        """创建 StatsService 实例。"""
        from stats_service import StatsService

        return StatsService(
            data_db_path=str(self.data_db),
            state_db_path=str(self.state_db),
            opencode_db_path=str(Path(self.tmpdir) / "nonexistent_opencode.db"),
        )

    def _setup_pricing_db(self):
        """通过 PricingDB 填充测试定价数据（USD 自定义值）。"""
        from proxy.pricing_manager import PricingDB

        db = PricingDB(self.config_db)
        # 用自定义模型避免与种子数据冲突
        db.add_pricing(
            {
                "model_id": "test-calc-model",
                "display_name": "Test Calc",
                "input_cost_per_million": "2.5",
                "output_cost_per_million": "10.0",
                "cache_read_cost_per_million": "0.5",
                "cache_creation_cost_per_million": "2.0",
                "currency": "USD",
            }
        )

    def test_service_calculate_cost_known_model(self):
        """StatsService.calculate_cost 委托正确（USD × 7 = RMB）。"""
        self._setup_pricing_db()
        service = self._create_service()

        cost = service.calculate_cost("test-calc-model", 1000, 2000, 500, 300)
        # 2.5/10/0.5/2.0 USD → ×7 → 17.5/70/3.5/14.0 RMB
        expected = (
            1000 / 1_000_000 * 2.5 * 7
            + 2000 / 1_000_000 * 10.0 * 7
            + 500 / 1_000_000 * 0.5 * 7
            + 300 / 1_000_000 * 2.0 * 7
        )
        self.assertAlmostEqual(cost, expected, places=10)

    def test_service_calculate_cost_unknown_model(self):
        """未知模型返回 0。"""
        self._setup_pricing_db()
        service = self._create_service()
        cost = service.calculate_cost("nonexistent-model", 1000, 2000, 0, 0)
        self.assertEqual(cost, 0)

    def test_service_get_pricing(self):
        """StatsService.get_pricing 委托正确（返回人民币值）。"""
        self._setup_pricing_db()
        service = self._create_service()
        pricing = service.get_pricing()
        self.assertIn("test-calc-model", pricing)
        # 2.5 USD × 7 = 17.5 RMB
        self.assertAlmostEqual(
            pricing["test-calc-model"]["input_cost"], 2.5 * 7, places=6
        )


class TestCostCalculatorCNY(unittest.TestCase):
    """_CostCalculator 币种换算和缓存失效测试。"""

    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "config.db"
        from proxy.pricing_manager import PricingDB

        PricingDB(self.db_path)  # 建表 + 种子数据

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_usd_pricing_converted_to_cny(self):
        """USD 定价应自动 × 7 换算为人民币。"""
        from stats_service import _CostCalculator

        calc = _CostCalculator(self.db_path)
        pricing = calc.get_pricing()
        # claude-sonnet-4-6: input=3 USD → 应返回 3*7=21 RMB
        self.assertIn("claude-sonnet-4-6-20260217", pricing)
        self.assertAlmostEqual(
            pricing["claude-sonnet-4-6-20260217"]["input_cost"], 21.0
        )

    def test_calculate_returns_cny(self):
        """calculate() 返回人民币金额。"""
        from stats_service import _CostCalculator

        calc = _CostCalculator(self.db_path)
        # 1M input tokens × $3/1M = $3 → ¥21
        cost = calc.calculate("claude-sonnet-4-6-20260217", 1_000_000, 0, 0, 0)
        self.assertAlmostEqual(cost, 21.0)

    def test_unknown_model_returns_zero(self):
        """未知模型返回 0。"""
        from stats_service import _CostCalculator

        calc = _CostCalculator(self.db_path)
        cost = calc.calculate("nonexistent-model", 1000, 1000, 0, 0)
        self.assertEqual(cost, 0)

    def test_invalidate_cache(self):
        """invalidate_cache 后下次 get_pricing 重新加载。"""
        from stats_service import _CostCalculator

        calc = _CostCalculator(self.db_path)
        pricing1 = calc.get_pricing()
        calc.invalidate_cache()
        # 验证缓存已清空
        self.assertEqual(calc._pricing_cache, {})
        # 再次获取应重新加载
        pricing2 = calc.get_pricing()
        self.assertEqual(len(pricing1), len(pricing2))
        # 新新加载的对象不应是同一个引用
        self.assertIsNot(pricing1, pricing2)

    def test_invalidate_pricing_cache_on_stats_service(self):
        """StatsService.invalidate_pricing_cache() 委托到 _CostCalculator。"""
        from stats_service import StatsService

        service = StatsService(
            data_db_path=str(Path(self.tmpdir.name) / "access_log.db"),
            state_db_path=str(Path(self.tmpdir.name) / "state.db"),
            opencode_db_path=str(Path(self.tmpdir.name) / "nonexistent_opencode.db"),
        )
        # 先触发 calculator 懒加载
        calc = service._get_calculator()
        calc.get_pricing()
        self.assertGreater(len(calc._pricing_cache), 0)
        # 失效
        service.invalidate_pricing_cache()
        self.assertEqual(calc._pricing_cache, {})


class TestCostCalculatorBreakdown(unittest.TestCase):
    """_CostCalculator.calculate_breakdown 测试 — 4 项独立成本拆分。"""

    def setUp(self):
        import os
        from tempfile import TemporaryDirectory
        self.tmpdir = TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS model_pricing (
                model_id TEXT PRIMARY KEY,
                display_name TEXT,
                input_cost_per_million REAL DEFAULT 0,
                output_cost_per_million REAL DEFAULT 0,
                cache_read_cost_per_million REAL DEFAULT 0,
                cache_creation_cost_per_million REAL DEFAULT 0,
                currency TEXT DEFAULT 'RMB',
                multiplier REAL DEFAULT 1.0,
                created_at TEXT,
                updated_at TEXT
            )
        """)
        # 种子数据：RMB 定价
        conn.execute(
            "INSERT INTO model_pricing (model_id, display_name, input_cost_per_million, "
            "output_cost_per_million, cache_read_cost_per_million, cache_creation_cost_per_million, "
            "currency) VALUES (?, ?, ?, ?, ?, ?, 'RMB')",
            ("test-model", "Test Model", 1.0, 2.0, 0.5, 0.25),
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_breakdown_all_tokens_positive(self):
        """所有 token 维度均为正数时返回 4 项非零成本。"""
        from stats_service import _CostCalculator
        calc = _CostCalculator(str(self.db_path))

        result = calc.calculate_breakdown(
            model="test-model",
            input_tokens=1_000_000,
            output_tokens=500_000,
            cache_read_tokens=200_000,
            cache_write_tokens=100_000,
        )

        self.assertAlmostEqual(result["input_cost_cny"], 1.0, places=6)
        self.assertAlmostEqual(result["output_cost_cny"], 1.0, places=6)
        self.assertAlmostEqual(result["cache_read_cost_cny"], 0.1, places=6)
        self.assertAlmostEqual(result["cache_write_cost_cny"], 0.025, places=6)

    def test_breakdown_unknown_model_returns_zeros(self):
        """无定价模型返回 4 项 0.0。"""
        from stats_service import _CostCalculator
        calc = _CostCalculator(str(self.db_path))

        result = calc.calculate_breakdown(
            model="nonexistent-model",
            input_tokens=1_000_000,
            output_tokens=500_000,
            cache_read_tokens=200_000,
            cache_write_tokens=100_000,
        )

        self.assertEqual(result["input_cost_cny"], 0.0)
        self.assertEqual(result["output_cost_cny"], 0.0)
        self.assertEqual(result["cache_read_cost_cny"], 0.0)
        self.assertEqual(result["cache_write_cost_cny"], 0.0)

    def test_breakdown_zeros_when_all_tokens_zero(self):
        """所有 token 为 0 时返回 4 项 0.0。"""
        from stats_service import _CostCalculator
        calc = _CostCalculator(str(self.db_path))

        result = calc.calculate_breakdown(
            model="test-model",
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
        )

        self.assertEqual(result["input_cost_cny"], 0.0)
        self.assertEqual(result["output_cost_cny"], 0.0)
        self.assertEqual(result["cache_read_cost_cny"], 0.0)
        self.assertEqual(result["cache_write_cost_cny"], 0.0)

    def test_breakdown_consistent_with_calculate_total(self):
        """calculate_breakdown 的 4 项之和应等于 calculate() 的返回值。"""
        from stats_service import _CostCalculator
        calc = _CostCalculator(str(self.db_path))

        breakdown = calc.calculate_breakdown(
            model="test-model",
            input_tokens=1_500_000,
            output_tokens=800_000,
            cache_read_tokens=300_000,
            cache_write_tokens=150_000,
        )
        total_from_breakdown = (
            breakdown["input_cost_cny"] + breakdown["output_cost_cny"]
            + breakdown["cache_read_cost_cny"] + breakdown["cache_write_cost_cny"]
        )
        total = calc.calculate(
            model="test-model",
            input_tokens=1_500_000,
            output_tokens=800_000,
            cache_read_tokens=300_000,
            cache_write_tokens=150_000,
        )
        self.assertAlmostEqual(total_from_breakdown, total, places=6)

    def test_breakdown_case_insensitive_model(self):
        """模型名大小写不敏感匹配。"""
        from stats_service import _CostCalculator
        calc = _CostCalculator(str(self.db_path))

        result_lower = calc.calculate_breakdown(
            model="test-model",
            input_tokens=1_000_000, output_tokens=0,
            cache_read_tokens=0, cache_write_tokens=0,
        )
        result_upper = calc.calculate_breakdown(
            model="TEST-MODEL",
            input_tokens=1_000_000, output_tokens=0,
            cache_read_tokens=0, cache_write_tokens=0,
        )
        self.assertEqual(result_lower["input_cost_cny"], result_upper["input_cost_cny"])

    def test_breakdown_with_multiplier(self):
        """multiplier 非 1.0 时成本按比例缩放。"""
        from stats_service import _CostCalculator

        # 插入 multiplier=2.0 的定价
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("INSERT INTO model_pricing (model_id, display_name, input_cost_per_million, "
                     "output_cost_per_million, cache_read_cost_per_million, cache_creation_cost_per_million, "
                     "currency, multiplier) VALUES (?, ?, ?, ?, ?, ?, 'RMB', 2.0)",
                     ("mult-model", "Multi Model", 1.0, 2.0, 0.5, 0.25))
        conn.commit()
        conn.close()

        calc = _CostCalculator(str(self.db_path))
        result = calc.calculate_breakdown(
            model="mult-model",
            input_tokens=1_000_000,
            output_tokens=500_000,
            cache_read_tokens=200_000,
            cache_write_tokens=100_000,
        )
        # multiplier=2.0，成本应翻倍
        self.assertAlmostEqual(result["input_cost_cny"], 2.0, places=6)
        self.assertAlmostEqual(result["output_cost_cny"], 2.0, places=6)


class TestTokenStatsDao(unittest.TestCase):
    """_TokenStatsDao 测试。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.data_db = Path(self.tmpdir) / "access_log.db"
        conn = sqlite3.connect(str(self.data_db))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""CREATE TABLE IF NOT EXISTS token_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL,
            request_type TEXT NOT NULL, model TEXT NOT NULL, target_model TEXT NOT NULL,
            request_ts TEXT NOT NULL, duration_ms INTEGER, input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0, cached_read_tokens INTEGER DEFAULT 0,
            cached_write_tokens INTEGER DEFAULT 0, upstream_id INTEGER,
            status TEXT DEFAULT 'completed', created_at TEXT NOT NULL)""")
        conn.commit()
        conn.close()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _seed_token_stats_data(self):
        """插入测试用 token_stats 数据。"""
        conn = sqlite3.connect(str(self.data_db))
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO token_stats (request_id, request_type, model, target_model, "
            "request_ts, duration_ms, input_tokens, output_tokens, cached_read_tokens, "
            "cached_write_tokens, upstream_id, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("req-001", "responses", "deepseek-v4-flash", "deepseek-v4-flash",
             now, 500, 1000, 500, 100, 50, "up-deepseek", "completed", now),
        )
        conn.commit()
        conn.close()

    def test_query_raw_returns_unified_schema(self):
        """query_raw 返回统一格式记录，字段名符合 schema。"""
        from stats_service import _TokenStatsDao
        dao = _TokenStatsDao(self.data_db)
        self._seed_token_stats_data()

        records = dao.query_raw("week")

        self.assertIsInstance(records, list)
        self.assertGreater(len(records), 0)
        r = records[0]
        # 统一 schema 必须字段
        self.assertIn("request_id", r)
        self.assertIn("model", r)
        self.assertIn("request_type", r)
        self.assertIn("request_ts", r)
        self.assertIn("duration_ms", r)
        self.assertIn("status", r)
        self.assertIn("input_tokens", r)
        self.assertIn("output_tokens", r)
        self.assertIn("cache_read_tokens", r)     # 注意：不带 'd'
        self.assertIn("cache_write_tokens", r)    # 注意：不带 'd'
        self.assertIn("upstream_id", r)
        # 不应存在的旧字段
        self.assertNotIn("_source", r)
        self.assertNotIn("target_model", r)
        self.assertNotIn("cached_read_tokens", r)
        self.assertNotIn("cached_write_tokens", r)

    def test_query_raw_upstream_id_null_defaults_unknown(self):
        """upstream_id 为 NULL 时，query_raw 返回 '__unknown__'。"""
        from stats_service import _TokenStatsDao
        dao = _TokenStatsDao(self.data_db)

        conn = sqlite3.connect(str(self.data_db))
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO token_stats (request_id, request_type, model, target_model, "
            "request_ts, duration_ms, input_tokens, output_tokens, cached_read_tokens, "
            "cached_write_tokens, upstream_id, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'completed', ?)",
            ("req-1", "responses", "gpt-4", "gpt-4", now, 100, 50, 30, 0, 0, now),
        )
        conn.commit()
        conn.close()

        records = dao.query_raw("week")
        self.assertEqual(records[0]["upstream_id"], "__unknown__")


class TestUpstreamResolver(unittest.TestCase):
    """_UpstreamResolver 测试。"""

    def setUp(self):
        """创建临时目录和 config.db。"""
        self.tmpdir = tempfile.mkdtemp()
        self.config_db = Path(self.tmpdir) / "data.db"
        self.data_db = self.config_db

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
                name TEXT UNIQUE NOT NULL,
                base_url TEXT NOT NULL,
                api_key TEXT DEFAULT '',
                timeout INTEGER DEFAULT 30,
                connect_timeout INTEGER DEFAULT 10,
                ssl_verify INTEGER DEFAULT 1,
                retry INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS target_models (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                upstream_id INTEGER,
                multimodal INTEGER DEFAULT 0
            )
        """)
        conn.execute(
            "INSERT INTO upstreams (id, name, base_url) VALUES (?, ?, ?)",
            (1, "OpenAI", "https://api.openai.com"),
        )
        conn.execute(
            "INSERT INTO upstreams (id, name, base_url) VALUES (?, ?, ?)",
            (2, "Anthropic", "https://api.anthropic.com"),
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
        self.assertEqual(result["upstream_name"], "OpenAI")
        self.assertEqual(result["base_url"], "https://api.openai.com")

        result2 = resolver.resolve("claude-sonnet-4")
        self.assertEqual(result2["upstream_name"], "Anthropic")
        self.assertEqual(result2["base_url"], "https://api.anthropic.com")

    def test_resolve_orphan_model(self):
        """orphan model → __unknown__。"""
        from stats_service import _UpstreamResolver

        self._setup_config_db()
        resolver = _UpstreamResolver(self.config_db)

        result = resolver.resolve("nonexistent-model")
        self.assertEqual(result["upstream_name"], "__unknown__")
        self.assertIsNone(result["base_url"])

    def test_resolve_config_db_not_exists(self):
        """config.db 不存在 → 不抛异常，返回 __unknown__。"""
        from stats_service import _UpstreamResolver

        # 不创建 config.db
        resolver = _UpstreamResolver(self.config_db)

        # 不应抛异常
        result = resolver.resolve("any-model")
        self.assertEqual(result["upstream_name"], "__unknown__")
        self.assertIsNone(result["base_url"])

    def test_get_all_upstreams(self):
        """get_all_upstreams 返回所有 upstream。"""
        from stats_service import _UpstreamResolver

        self._setup_config_db()
        resolver = _UpstreamResolver(self.config_db)

        upstreams = resolver.get_all_upstreams()
        self.assertEqual(len(upstreams), 2)

        urls = {up["upstream_name"] for up in upstreams}
        self.assertIn("OpenAI", urls)
        self.assertIn("Anthropic", urls)

    def test_cache_ttl_refresh(self):
        """缓存过期后自动刷新。"""
        import time
        from stats_service import _UpstreamResolver

        self._setup_config_db()
        resolver = _UpstreamResolver(self.config_db)

        # 首次解析
        result1 = resolver.resolve("qwen3.6-plus")
        self.assertEqual(result1["upstream_name"], "OpenAI")

        # 修改缓存时间为负值，强制过期
        resolver._loaded_at = time.time() - 61

        # 再次解析应触发刷新
        result2 = resolver.resolve("qwen3.6-plus")
        self.assertEqual(result2["upstream_name"], "OpenAI")

    def test_stats_service_uses_resolver(self):
        """StatsService 使用 _UpstreamResolver。"""
        from stats_service import StatsService

        state_db = Path(self.tmpdir) / "state.db"

        # 在同一个数据库中创建 token_stats 表和 config 表
        self._setup_config_db()
        conn = sqlite3.connect(str(self.data_db))
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
                upstream_id INTEGER,
                status TEXT DEFAULT 'completed',
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

        service = StatsService(
            data_db_path=str(self.data_db),
            state_db_path=str(state_db),
            opencode_db_path=str(Path(self.tmpdir) / "nonexistent_opencode.db"),
        )

        # 验证 resolver 已初始化
        self.assertIsNotNone(service._upstream_resolver)

        # 验证 resolve 方法可用
        result = service._resolve_upstream("qwen3.6-plus")
        self.assertEqual(result["upstream_name"], "OpenAI")


class TestSessionDao(unittest.TestCase):
    """_SessionDao 测试。"""

    def setUp(self):
        """创建临时目录和 sessions 表。"""
        self.tmpdir = tempfile.mkdtemp()
        self.state_db = Path(self.tmpdir) / "state.db"

        # 创建 sessions 表（started_at 为 Unix 时间戳 REAL）
        conn = sqlite3.connect(str(self.state_db))
        conn.execute("""
            CREATE TABLE sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model TEXT,
                started_at REAL NOT NULL,
                input_tokens INTEGER,
                output_tokens INTEGER,
                cache_read_tokens INTEGER DEFAULT 0,
                cache_write_tokens INTEGER DEFAULT 0,
                message_count INTEGER DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()

    def tearDown(self):
        """清理临时文件。"""
        import shutil

        if os.path.exists(self.tmpdir):
            shutil.rmtree(self.tmpdir)

    def _create_dao(self):
        """创建 _SessionDao 实例。"""
        from stats_service import _SessionDao

        return _SessionDao(self.state_db)

    def _insert_session(self, **kwargs):
        """插入一条 session 测试数据。"""
        import time as _time

        defaults = {
            "model": "qwen3.6-plus",
            "started_at": _time.time(),
            "input_tokens": 100,
            "output_tokens": 200,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "message_count": 1,
        }
        defaults.update(kwargs)
        d = defaults
        conn = sqlite3.connect(str(self.state_db))
        conn.execute(
            "INSERT INTO sessions "
            "(model, started_at, input_tokens, output_tokens, "
            "cache_read_tokens, cache_write_tokens, message_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                d["model"],
                d["started_at"],
                d["input_tokens"],
                d["output_tokens"],
                d["cache_read_tokens"],
                d["cache_write_tokens"],
                d["message_count"],
            ),
        )
        conn.commit()
        conn.close()

    # ─── query_sessions 测试 ───

    def test_query_sessions_basic(self):
        """有数据时返回正确记录。"""
        self._insert_session(model="qwen3.6-plus", input_tokens=100, output_tokens=200)
        dao = self._create_dao()
        result = dao.query_sessions("day")

        self.assertEqual(len(result), 1)
        rec = result[0]
        self.assertTrue(rec["request_id"].startswith("sess-"))
        self.assertEqual(rec["request_type"], "session")
        self.assertEqual(rec["target_model"], "qwen3.6-plus")
        self.assertEqual(rec["input_tokens"], 100)
        self.assertEqual(rec["output_tokens"], 200)
        self.assertEqual(rec["status"], "completed")
        self.assertEqual(rec["_source"], "session")
        self.assertIsNone(rec["duration_ms"])

    def test_query_sessions_db_not_exists(self):
        """state.db 不存在时返回空列表，不抛异常。"""
        from stats_service import _SessionDao

        dao = _SessionDao(Path("/nonexistent/path/state.db"))
        result = dao.query_sessions("day")
        self.assertEqual(result, [])

    def test_query_sessions_model_filter(self):
        """按模型过滤正确（支持 [ctx] 后缀匹配）。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._insert_session(model="qwen3.6-plus", started_at=now)
        self._insert_session(model="claude-sonnet-4", started_at=now)
        dao = self._create_dao()
        result = dao.query_sessions("day", model="qwen3.6-plus")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["model"], "qwen3.6-plus")

    def test_query_sessions_period_filter(self):
        """Period 过滤只返回时间范围内数据。"""
        import time as _time

        now_ts = _time.time()
        self._insert_session(model="qwen3.6-plus", started_at=now_ts)
        self._insert_session(
            model="claude-sonnet-4", started_at=now_ts - 10 * 86400, input_tokens=999
        )
        dao = self._create_dao()
        result = dao.query_sessions("day")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["input_tokens"], 100)

    def test_query_sessions_null_input_tokens_excluded(self):
        """input_tokens IS NULL 的 session 被排除。"""
        self._insert_session(model="qwen3.6-plus", input_tokens=None, output_tokens=0)
        dao = self._create_dao()
        result = dao.query_sessions("day")
        self.assertEqual(len(result), 0)

    def test_period_filter_works_with_unix_timestamp(self):
        """验证 _period_to_condition 对 Unix 时间戳的 started_at 正确过滤。"""
        import time as _time

        now_ts = _time.time()
        self._insert_session(
            model="recent",
            started_at=now_ts - 3600,
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=0,
            cache_write_tokens=0,
        )
        self._insert_session(
            model="old",
            started_at=now_ts - 8 * 86400,
            input_tokens=200,
            output_tokens=100,
            cache_read_tokens=0,
            cache_write_tokens=0,
        )
        dao = self._create_dao()
        week_sessions = dao.query_sessions("week")
        week_models = [s["model"] for s in week_sessions]
        self.assertIn("recent", week_models)
        self.assertNotIn("old", week_models)
        month_sessions = dao.query_sessions("month")
        month_models = [s["model"] for s in month_sessions]
        self.assertIn("recent", month_models)
        self.assertIn("old", month_models)

    # ─── normalize_model_name 测试 ───

    def test_normalize_model_name_with_bracket(self):
        """去除 [xxx] 上下文后缀。"""
        from stats_service import _SessionDao

        self.assertEqual(
            _SessionDao._normalize_model_name("qwen3.6-plus [hermes]"), "qwen3.6-plus"
        )

    def test_normalize_model_name_no_bracket(self):
        """无括号的模型名保持不变。"""
        from stats_service import _SessionDao

        self.assertEqual(
            _SessionDao._normalize_model_name("qwen3.6-plus"), "qwen3.6-plus"
        )

    def test_normalize_model_name_none(self):
        """None 输入返回 None。"""
        from stats_service import _SessionDao

        self.assertIsNone(_SessionDao._normalize_model_name(None))

    def test_session_record_has_normalized_target_model(self):
        """session 记录的 target_model 已去除 [xxx] 后缀。"""
        self._insert_session(
            model="qwen3.6-plus [hermes]", input_tokens=50, output_tokens=100
        )
        dao = self._create_dao()
        result = dao.query_sessions("day")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["model"], "qwen3.6-plus [hermes]")
        self.assertEqual(result[0]["target_model"], "qwen3.6-plus")

    # ─── aggregate_by_model 测试 ───

    def test_aggregate_by_model_basic(self):
        """按模型聚合正确。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._insert_session(
            model="qwen3.6-plus", input_tokens=100, output_tokens=200, started_at=now
        )
        self._insert_session(
            model="qwen3.6-plus", input_tokens=50, output_tokens=100, started_at=now
        )
        self._insert_session(
            model="claude-sonnet-4", input_tokens=300, output_tokens=500, started_at=now
        )
        dao = self._create_dao()
        result = dao.aggregate_by_model("day")

        self.assertEqual(len(result), 2)

        # qwen3.6-plus: 100+50=150 input, 200+100=300 output, 2 sessions
        qwen = next(m for m in result if m["model"] == "qwen3.6-plus")
        self.assertEqual(qwen["input_tokens"], 150)
        self.assertEqual(qwen["output_tokens"], 300)
        self.assertEqual(qwen["request_count"], 2)

        claude = next(m for m in result if m["model"] == "claude-sonnet-4")
        self.assertEqual(claude["input_tokens"], 300)
        self.assertEqual(claude["output_tokens"], 500)
        self.assertEqual(claude["request_count"], 1)

    def test_aggregate_by_model_db_not_exists(self):
        """state.db 不存在时返回空列表。"""
        from stats_service import _SessionDao

        dao = _SessionDao(Path("/nonexistent/state.db"))
        result = dao.aggregate_by_model("day")
        self.assertEqual(result, [])

    def test_aggregate_by_model_merges_context_suffix(self):
        """同名带 [ctx] 后缀的模型聚合到同一 base model。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._insert_session(
            model="qwen3.6-plus", input_tokens=100, output_tokens=200, started_at=now
        )
        self._insert_session(
            model="qwen3.6-plus [coder]",
            input_tokens=50,
            output_tokens=100,
            started_at=now,
        )
        dao = self._create_dao()
        result = dao.aggregate_by_model("day")

        # 应合并为一条 qwen3.6-plus
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["model"], "qwen3.6-plus")
        self.assertEqual(result[0]["input_tokens"], 150)
        self.assertEqual(result[0]["output_tokens"], 300)
        self.assertEqual(result[0]["request_count"], 2)

    def test_aggregate_by_model_sorted_by_total_tokens(self):
        """聚合结果按 total_tokens 降序排列。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._insert_session(
            model="small-model", input_tokens=10, output_tokens=20, started_at=now
        )
        self._insert_session(
            model="big-model", input_tokens=1000, output_tokens=2000, started_at=now
        )
        dao = self._create_dao()
        result = dao.aggregate_by_model("day")

        self.assertEqual(result[0]["model"], "big-model")
        self.assertEqual(result[1]["model"], "small-model")

    # ─── aggregate_summary 测试 ───

    def test_aggregate_summary_basic(self):
        import time as _time

        now_ts = _time.time()
        self._insert_session(
            model="model-a",
            started_at=now_ts - 3600,
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=20,
            cache_write_tokens=10,
        )
        self._insert_session(
            model="model-b",
            started_at=now_ts - 1800,
            input_tokens=200,
            output_tokens=100,
            cache_read_tokens=40,
            cache_write_tokens=20,
        )
        dao = self._create_dao()
        result = dao.aggregate_summary("week")
        self.assertEqual(result["request_count"], 2)
        self.assertEqual(result["input_tokens"], 300)
        self.assertEqual(result["output_tokens"], 150)
        self.assertEqual(result["cached_read_tokens"], 60)
        self.assertEqual(result["cached_write_tokens"], 30)

    def test_aggregate_summary_db_not_exists(self):
        from stats_service import _SessionDao

        dao = _SessionDao(Path("/nonexistent/state.db"))
        result = dao.aggregate_summary("week")
        self.assertEqual(result["request_count"], 0)
        self.assertEqual(result["input_tokens"], 0)

    # ─── aggregate_trend 测试 ───

    def test_aggregate_trend_basic(self):
        import time as _time

        now_ts = _time.time()
        self._insert_session(
            model="model-a",
            started_at=now_ts - 3600,
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=20,
            cache_write_tokens=10,
        )
        dao = self._create_dao()
        result = dao.aggregate_trend("week")
        self.assertIsInstance(result, list)
        self.assertGreaterEqual(len(result), 1)
        point = result[0]
        self.assertIn("time", point)
        self.assertIn("input_tokens", point)
        self.assertIn("cached_read_tokens", point)

    def test_aggregate_trend_db_not_exists(self):
        from stats_service import _SessionDao

        dao = _SessionDao(Path("/nonexistent/state.db"))
        result = dao.aggregate_trend("week")
        self.assertEqual(result, [])

    def test_query_raw_returns_unified_schema(self):
        """query_raw 返回统一格式，upstream_id 固定为 'hermes'。"""
        import time as _time
        from stats_service import _SessionDao

        conn = sqlite3.connect(str(self.state_db))
        now_ts = _time.time()
        conn.execute(
            "INSERT INTO sessions (model, started_at, input_tokens, output_tokens, "
            "cache_read_tokens, cache_write_tokens) VALUES (?, ?, ?, ?, ?, ?)",
            ("claude-sonnet-4-6[1m]", now_ts, 1000, 500, 100, 50),
        )
        conn.commit()
        conn.close()

        dao = _SessionDao(self.state_db)
        records = dao.query_raw("week")

        self.assertEqual(len(records), 1)
        r = records[0]
        self.assertEqual(r["upstream_id"], "hermes")
        self.assertEqual(r["model"], "claude-sonnet-4-6")  # 去掉了 [1m] 后缀
        self.assertEqual(r["request_type"], "session")
        self.assertEqual(r["input_tokens"], 1000)
        self.assertEqual(r["output_tokens"], 500)
        self.assertEqual(r["cache_read_tokens"], 100)
        self.assertEqual(r["cache_write_tokens"], 50)
        self.assertEqual(r["status"], "completed")
        self.assertNotIn("_source", r)
        self.assertNotIn("target_model", r)


class TestFetchRequestsMerged(unittest.TestCase):
    """fetch_requests 合并 token_stats + sessions 测试。"""

    def setUp(self):
        """创建临时目录和两个数据库。"""
        self.tmpdir = tempfile.mkdtemp()
        self.data_db = Path(self.tmpdir) / "access_log.db"
        self.config_db = self.data_db
        self.state_db = Path(self.tmpdir) / "state.db"

        # 创建 token_stats 表
        conn = sqlite3.connect(str(self.data_db))
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
                upstream_id INTEGER,
                status TEXT DEFAULT 'completed',
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

        # 创建 sessions 表
        conn = sqlite3.connect(str(self.state_db))
        conn.execute("""
            CREATE TABLE sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model TEXT,
                started_at TEXT NOT NULL,
                input_tokens INTEGER,
                output_tokens INTEGER,
                cache_read_tokens INTEGER DEFAULT 0,
                cache_write_tokens INTEGER DEFAULT 0,
                message_count INTEGER DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()

    def tearDown(self):
        import shutil

        if os.path.exists(self.tmpdir):
            shutil.rmtree(self.tmpdir)

    def _create_service(self):
        from stats_service import StatsService

        return StatsService(
            data_db_path=str(self.data_db),
            state_db_path=str(self.state_db),
            opencode_db_path=str(Path(self.tmpdir) / "nonexistent_opencode.db"),
        )

    def _insert_token_stat(self, **kwargs):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        defaults = {
            "request_id": "req-1",
            "request_type": "chat",
            "model": "gpt-4",
            "target_model": "qwen3.6-plus",
            "request_ts": now,
            "duration_ms": 100,
            "input_tokens": 100,
            "output_tokens": 200,
            "cached_read_tokens": 0,
            "cached_write_tokens": 0,
            "upstream_id": None,
            "status": "completed",
            "created_at": now,
        }
        defaults.update(kwargs)
        d = defaults
        conn = sqlite3.connect(str(self.data_db))
        conn.execute(
            "INSERT INTO token_stats (request_id, request_type, model, target_model, "
            "request_ts, duration_ms, input_tokens, output_tokens, cached_read_tokens, "
            "cached_write_tokens, upstream_id, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                d["request_id"],
                d["request_type"],
                d["model"],
                d["target_model"],
                d["request_ts"],
                d["duration_ms"],
                d["input_tokens"],
                d["output_tokens"],
                d["cached_read_tokens"],
                d["cached_write_tokens"],
                d.get("upstream_id"),
                d["status"],
                d["created_at"],
            ),
        )
        conn.commit()
        conn.close()

    def _insert_session(self, **kwargs):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        defaults = {
            "model": "qwen3.6-plus",
            "started_at": now,
            "input_tokens": 100,
            "output_tokens": 200,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }
        defaults.update(kwargs)
        d = defaults
        conn = sqlite3.connect(str(self.state_db))
        conn.execute(
            "INSERT INTO sessions (model, started_at, input_tokens, output_tokens, "
            "cache_read_tokens, cache_write_tokens) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                d["model"],
                d["started_at"],
                d["input_tokens"],
                d["output_tokens"],
                d["cache_read_tokens"],
                d["cache_write_tokens"],
            ),
        )
        conn.commit()
        conn.close()

    def test_merged_returns_dict_structure(self):
        """返回正确的 dict 结构。"""
        service = self._create_service()
        result = service.fetch_requests("day")
        self.assertIn("requests", result)
        self.assertIn("total", result)
        self.assertIn("limit", result)
        self.assertIn("offset", result)
        self.assertIsInstance(result["requests"], list)

    def test_merged_empty_db(self):
        """空数据库返回空列表和 zero total。"""
        service = self._create_service()
        result = service.fetch_requests("day")
        self.assertEqual(result["requests"], [])
        self.assertEqual(result["total"], 0)

    def test_merged_both_sources(self):
        """token_stats 和 sessions 都有数据时正确合并。"""
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        self._insert_token_stat(
            request_id="proxy-1", target_model="qwen3.6-plus", request_ts=ts
        )
        self._insert_session(model="qwen3.6-plus", started_at=ts)

        service = self._create_service()
        result = service.fetch_requests("day")

        self.assertEqual(len(result["requests"]), 2)
        self.assertEqual(result["total"], 2)
        sources = {r["_source"] for r in result["requests"]}
        self.assertIn("proxy", sources)
        self.assertIn("session", sources)

    def test_merged_proxy_has_source_field(self):
        """proxy 记录 _source = 'proxy'。"""
        self._insert_token_stat(request_id="proxy-1")
        service = self._create_service()
        result = service.fetch_requests("day")
        self.assertEqual(len(result["requests"]), 1)
        self.assertEqual(result["requests"][0]["_source"], "proxy")

    def test_merged_session_has_source_field(self):
        """session 记录 _source = 'session'。"""
        self._insert_session(model="qwen3.6-plus")
        service = self._create_service()
        result = service.fetch_requests("day")
        self.assertEqual(len(result["requests"]), 1)
        self.assertEqual(result["requests"][0]["_source"], "session")

    def test_merged_session_fields(self):
        """session 记录字段正确。"""
        self._insert_session(model="qwen3.6-plus", input_tokens=100, output_tokens=200)
        service = self._create_service()
        result = service.fetch_requests("day")
        rec = result["requests"][0]
        self.assertTrue(rec["request_id"].startswith("sess-"))
        self.assertEqual(rec["request_type"], "session")
        self.assertEqual(rec["status"], "completed")
        self.assertIsNone(rec["duration_ms"])
        self.assertEqual(rec["input_tokens"], 100)
        self.assertEqual(rec["output_tokens"], 200)

    def test_merged_sort_by_request_ts_desc(self):
        """混合记录按 request_ts DESC 排序。"""
        now = datetime.now()
        old_ts = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        recent_ts = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

        # proxy: old, session: recent
        self._insert_token_stat(request_id="proxy-old", request_ts=old_ts)
        self._insert_session(model="qwen3.6-plus", started_at=recent_ts)

        service = self._create_service()
        result = service.fetch_requests("day")

        self.assertEqual(len(result["requests"]), 2)
        # 最近的应该排第一
        self.assertEqual(result["requests"][0]["request_id"].replace("sess-", ""), "1")
        self.assertEqual(result["requests"][1]["request_id"], "proxy-old")

    def test_merged_pagination_limit(self):
        """分页 limit 正确限制返回数量。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for i in range(15):
            self._insert_token_stat(request_id=f"req-{i}", request_ts=now)

        service = self._create_service()
        result = service.fetch_requests("day", limit=10, offset=0)
        self.assertEqual(len(result["requests"]), 10)
        self.assertEqual(result["total"], 15)
        self.assertEqual(result["limit"], 10)

    def test_merged_pagination_offset(self):
        """分页 offset 正确跳过前面的记录。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for i in range(10):
            self._insert_token_stat(request_id=f"req-{i}", request_ts=now)

        service = self._create_service()
        result = service.fetch_requests("day", limit=5, offset=5)
        self.assertEqual(len(result["requests"]), 5)
        self.assertEqual(result["offset"], 5)

    def test_merged_filter_by_model(self):
        """按 model 筛选只返回匹配的记录。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._insert_token_stat(
            request_id="proxy-qwen", target_model="qwen3.6-plus", request_ts=now
        )
        self._insert_token_stat(
            request_id="proxy-claude", target_model="claude-sonnet-4", request_ts=now
        )
        self._insert_session(model="qwen3.6-plus", started_at=now)
        self._insert_session(model="claude-sonnet-4", started_at=now)

        service = self._create_service()
        result = service.fetch_requests("day", model="qwen3.6-plus")

        self.assertEqual(result["total"], 2)
        for r in result["requests"]:
            self.assertEqual(r["target_model"], "qwen3.6-plus")

    def test_merged_filter_by_request_type_session(self):
        """按 request_type='session' 筛选只返回 sessions。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._insert_token_stat(
            request_id="proxy-1", request_type="chat", request_ts=now
        )
        self._insert_session(model="qwen3.6-plus", started_at=now)

        service = self._create_service()
        result = service.fetch_requests("day", request_type="session")

        # 只应返回 session 记录
        self.assertEqual(len(result["requests"]), 1)
        self.assertEqual(result["requests"][0]["_source"], "session")

    def test_merged_filter_by_request_type_chat(self):
        """按 request_type='chat' 筛选只返回 proxy。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._insert_token_stat(
            request_id="proxy-1", request_type="chat", request_ts=now
        )
        self._insert_session(model="qwen3.6-plus", started_at=now)

        service = self._create_service()
        result = service.fetch_requests("day", request_type="chat")

        self.assertEqual(len(result["requests"]), 1)
        self.assertEqual(result["requests"][0]["_source"], "proxy")

    def test_merged_cost_calculation(self):
        """每条记录都有 estimated_cost_cny。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._insert_token_stat(
            request_id="proxy-1", input_tokens=100, output_tokens=200
        )
        self._insert_session(model="qwen3.6-plus", input_tokens=100, output_tokens=200)

        service = self._create_service()
        result = service.fetch_requests("day")

        for r in result["requests"]:
            self.assertIn("estimated_cost_cny", r)
            self.assertIsInstance(r["estimated_cost_cny"], (int, float))

    def test_request_record_uses_cache_prefix(self):
        """proxy 记录字段名统一为 cache_*。"""
        self._insert_token_stat(
            input_tokens=100,
            output_tokens=200,
            cached_read_tokens=20,
            cached_write_tokens=10,
        )
        result = self._create_service().fetch_requests("day")
        for req in result["requests"]:
            if req.get("_source") == "proxy":
                self.assertIn("cache_read_tokens", req)
                self.assertNotIn("cached_read_tokens", req)


if __name__ == "__main__":
    unittest.main()


class TestFetchByModelRequestsMerged(unittest.TestCase):
    """fetch_by_model_requests 合并 token_stats + sessions 测试。"""

    def setUp(self):
        """创建临时目录和两个数据库。"""
        self.tmpdir = tempfile.mkdtemp()
        self.data_db = Path(self.tmpdir) / "access_log.db"
        self.config_db = self.data_db
        self.state_db = Path(self.tmpdir) / "state.db"

        # 创建 token_stats 表
        conn = sqlite3.connect(str(self.data_db))
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
                upstream_id INTEGER,
                status TEXT DEFAULT 'completed',
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

        # 创建 sessions 表
        conn = sqlite3.connect(str(self.state_db))
        conn.execute("""
            CREATE TABLE sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model TEXT,
                started_at TEXT NOT NULL,
                input_tokens INTEGER,
                output_tokens INTEGER,
                cache_read_tokens INTEGER DEFAULT 0,
                cache_write_tokens INTEGER DEFAULT 0,
                message_count INTEGER DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()

    def tearDown(self):
        """清理临时文件。"""
        import shutil

        if os.path.exists(self.tmpdir):
            shutil.rmtree(self.tmpdir)

    def _create_service(self):
        """创建 StatsService 实例。"""
        from stats_service import StatsService

        return StatsService(
            data_db_path=str(self.data_db),
            state_db_path=str(self.state_db),
            opencode_db_path=str(Path(self.tmpdir) / "nonexistent_opencode.db"),
        )

    def _insert_token_stat(self, **kwargs):
        """插入 token_stats 测试数据。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        defaults = {
            "request_id": "req-1",
            "request_type": "chat",
            "model": "gpt-4",
            "target_model": "qwen3.6-plus",
            "request_ts": now,
            "duration_ms": 100,
            "input_tokens": 100,
            "output_tokens": 200,
            "cached_read_tokens": 0,
            "cached_write_tokens": 0,
            "upstream_id": None,
            "status": "completed",
            "created_at": now,
        }
        defaults.update(kwargs)
        d = defaults
        conn = sqlite3.connect(str(self.data_db))
        conn.execute(
            "INSERT INTO token_stats (request_id, request_type, model, target_model, "
            "request_ts, duration_ms, input_tokens, output_tokens, cached_read_tokens, "
            "cached_write_tokens, upstream_id, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                d["request_id"],
                d["request_type"],
                d["model"],
                d["target_model"],
                d["request_ts"],
                d["duration_ms"],
                d["input_tokens"],
                d["output_tokens"],
                d["cached_read_tokens"],
                d["cached_write_tokens"],
                d.get("upstream_id"),
                d["status"],
                d["created_at"],
            ),
        )
        conn.commit()
        conn.close()

    def _insert_session(self, **kwargs):
        """插入 session 测试数据。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        defaults = {
            "model": "qwen3.6-plus",
            "started_at": now,
            "input_tokens": 100,
            "output_tokens": 200,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }
        defaults.update(kwargs)
        d = defaults
        conn = sqlite3.connect(str(self.state_db))
        conn.execute(
            "INSERT INTO sessions (model, started_at, input_tokens, output_tokens, "
            "cache_read_tokens, cache_write_tokens) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                d["model"],
                d["started_at"],
                d["input_tokens"],
                d["output_tokens"],
                d["cache_read_tokens"],
                d["cache_write_tokens"],
            ),
        )
        conn.commit()
        conn.close()

    def test_returns_dict_structure(self):
        """返回正确的 dict 结构。"""
        service = self._create_service()
        result = service.fetch_by_model_requests("qwen3.6-plus", "day")
        self.assertIn("model", result)
        self.assertIn("requests", result)
        self.assertIn("total", result)
        self.assertIn("limit", result)
        self.assertIn("offset", result)
        self.assertEqual(result["model"], "qwen3.6-plus")

    def test_empty_db_returns_empty(self):
        """空数据库返回空列表和 zero total。"""
        service = self._create_service()
        result = service.fetch_by_model_requests("qwen3.6-plus", "day")
        self.assertEqual(result["requests"], [])
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["limit"], 50)
        self.assertEqual(result["offset"], 0)

    def test_both_sources_merged(self):
        """token_stats 和 sessions 都有数据时正确合并。"""
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        self._insert_token_stat(
            request_id="proxy-1", target_model="qwen3.6-plus", request_ts=ts
        )
        self._insert_session(model="qwen3.6-plus", started_at=ts)

        service = self._create_service()
        result = service.fetch_by_model_requests("qwen3.6-plus", "day")

        self.assertEqual(len(result["requests"]), 2)
        self.assertEqual(result["total"], 2)
        sources = {r["_source"] for r in result["requests"]}
        self.assertIn("proxy", sources)
        self.assertIn("session", sources)

    def test_proxy_has_source_field(self):
        """proxy 记录 _source = 'proxy'。"""
        self._insert_token_stat(request_id="proxy-1", target_model="qwen3.6-plus")
        service = self._create_service()
        result = service.fetch_by_model_requests("qwen3.6-plus", "day")
        self.assertEqual(len(result["requests"]), 1)
        self.assertEqual(result["requests"][0]["_source"], "proxy")

    def test_session_has_source_field(self):
        """session 记录 _source = 'session'。"""
        self._insert_session(model="qwen3.6-plus")
        service = self._create_service()
        result = service.fetch_by_model_requests("qwen3.6-plus", "day")
        self.assertEqual(len(result["requests"]), 1)
        self.assertEqual(result["requests"][0]["_source"], "session")

    def test_sorted_by_request_ts_desc(self):
        """混合记录按 request_ts DESC 排序。"""
        now = datetime.now()
        old_ts = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        recent_ts = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

        # proxy: old, session: recent
        self._insert_token_stat(
            request_id="proxy-old", target_model="qwen3.6-plus", request_ts=old_ts
        )
        self._insert_session(model="qwen3.6-plus", started_at=recent_ts)

        service = self._create_service()
        result = service.fetch_by_model_requests("qwen3.6-plus", "day")

        self.assertEqual(len(result["requests"]), 2)
        # 最近的应该排第一
        self.assertEqual(result["requests"][0]["_source"], "session")
        self.assertEqual(result["requests"][1]["_source"], "proxy")

    def test_pagination_limit(self):
        """分页 limit 正确限制返回数量。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for i in range(15):
            self._insert_token_stat(
                request_id=f"req-{i}", target_model="qwen3.6-plus", request_ts=now
            )

        service = self._create_service()
        result = service.fetch_by_model_requests(
            "qwen3.6-plus", "day", limit=10, offset=0
        )
        self.assertEqual(len(result["requests"]), 10)
        self.assertEqual(result["total"], 15)
        self.assertEqual(result["limit"], 10)

    def test_pagination_offset(self):
        """分页 offset 正确跳过前面的记录。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for i in range(10):
            self._insert_token_stat(
                request_id=f"req-{i}", target_model="qwen3.6-plus", request_ts=now
            )

        service = self._create_service()
        result = service.fetch_by_model_requests(
            "qwen3.6-plus", "day", limit=5, offset=5
        )
        self.assertEqual(len(result["requests"]), 5)
        self.assertEqual(result["offset"], 5)

    def test_model_filter_exact_match(self):
        """按 model 精确筛选。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._insert_token_stat(
            request_id="proxy-qwen", target_model="qwen3.6-plus", request_ts=now
        )
        self._insert_token_stat(
            request_id="proxy-claude", target_model="claude-sonnet-4", request_ts=now
        )
        self._insert_session(model="qwen3.6-plus", started_at=now)
        self._insert_session(model="claude-sonnet-4", started_at=now)

        service = self._create_service()
        result = service.fetch_by_model_requests("qwen3.6-plus", "day")

        self.assertEqual(result["total"], 2)
        for r in result["requests"]:
            self.assertEqual(r["target_model"], "qwen3.6-plus")

    def test_model_filter_with_context_suffix(self):
        """model 过滤支持 [ctx] 后缀匹配。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._insert_session(model="qwen3.6-plus [hermes]", started_at=now)
        self._insert_session(model="qwen3.6-plus [coder]", started_at=now)
        self._insert_session(model="claude-sonnet-4", started_at=now)

        service = self._create_service()
        result = service.fetch_by_model_requests("qwen3.6-plus", "day")

        # 应匹配到两个 qwen3.6-plus 带后缀的 session
        self.assertEqual(result["total"], 2)

    def test_cost_calculation(self):
        """每条记录都有 estimated_cost_cny。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._insert_token_stat(
            request_id="proxy-1",
            target_model="qwen3.6-plus",
            input_tokens=100,
            output_tokens=200,
        )
        self._insert_session(model="qwen3.6-plus", input_tokens=100, output_tokens=200)

        service = self._create_service()
        result = service.fetch_by_model_requests("qwen3.6-plus", "day")

        for r in result["requests"]:
            self.assertIn("estimated_cost_cny", r)
            self.assertIsInstance(r["estimated_cost_cny"], (int, float))


class TestFetchByUpstreamMerged(unittest.TestCase):
    """fetch_by_upstream 合并 token_stats + sessions 测试。"""

    def setUp(self):
        """创建临时目录和两个数据库。"""
        self.tmpdir = tempfile.mkdtemp()
        self.data_db = Path(self.tmpdir) / "access_log.db"
        self.config_db = self.data_db
        self.state_db = Path(self.tmpdir) / "state.db"

        # 创建 token_stats 表
        conn = sqlite3.connect(str(self.data_db))
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
                upstream_id INTEGER,
                status TEXT DEFAULT 'completed',
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

        # 创建 sessions 表
        conn = sqlite3.connect(str(self.state_db))
        conn.execute("""
            CREATE TABLE sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model TEXT,
                started_at TEXT NOT NULL,
                input_tokens INTEGER,
                output_tokens INTEGER,
                cache_read_tokens INTEGER DEFAULT 0,
                cache_write_tokens INTEGER DEFAULT 0,
                message_count INTEGER DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()

    def tearDown(self):
        """清理临时文件。"""
        import shutil

        if os.path.exists(self.tmpdir):
            shutil.rmtree(self.tmpdir)

    def _create_service(self):
        """创建 StatsService 实例。"""
        from stats_service import StatsService

        return StatsService(
            data_db_path=str(self.data_db),
            state_db_path=str(self.state_db),
        )

    def _setup_config_db(self):
        """创建 config.db 并填充 upstream/target_models 数据。"""
        conn = sqlite3.connect(str(self.config_db))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS upstreams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                base_url TEXT NOT NULL,
                api_key TEXT DEFAULT '',
                timeout INTEGER DEFAULT 30,
                connect_timeout INTEGER DEFAULT 10,
                ssl_verify INTEGER DEFAULT 1,
                retry INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS target_models (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                upstream_id INTEGER,
                multimodal INTEGER DEFAULT 0
            )
        """)
        conn.execute(
            "INSERT INTO upstreams (id, name, base_url) VALUES (?, ?, ?)",
            (1, "OpenAI", "https://api.openai.com"),
        )
        conn.execute(
            "INSERT INTO upstreams (id, name, base_url) VALUES (?, ?, ?)",
            (2, "Anthropic", "https://api.anthropic.com"),
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

    def _insert_token_stat(self, **kwargs):
        """插入 token_stats 测试数据。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        defaults = {
            "request_id": "req-1",
            "request_type": "chat",
            "model": "gpt-4",
            "target_model": "qwen3.6-plus",
            "request_ts": now,
            "duration_ms": 100,
            "input_tokens": 100,
            "output_tokens": 200,
            "cached_read_tokens": 0,
            "cached_write_tokens": 0,
            "upstream_id": None,
            "status": "completed",
            "created_at": now,
        }
        defaults.update(kwargs)
        d = defaults
        conn = sqlite3.connect(str(self.data_db))
        conn.execute(
            "INSERT INTO token_stats (request_id, request_type, model, target_model, "
            "request_ts, duration_ms, input_tokens, output_tokens, cached_read_tokens, "
            "cached_write_tokens, upstream_id, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                d["request_id"],
                d["request_type"],
                d["model"],
                d["target_model"],
                d["request_ts"],
                d["duration_ms"],
                d["input_tokens"],
                d["output_tokens"],
                d["cached_read_tokens"],
                d["cached_write_tokens"],
                d.get("upstream_id"),
                d["status"],
                d["created_at"],
            ),
        )
        conn.commit()
        conn.close()

    def _insert_session(self, **kwargs):
        """插入 session 测试数据。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        defaults = {
            "model": "qwen3.6-plus",
            "started_at": now,
            "input_tokens": 100,
            "output_tokens": 200,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }
        defaults.update(kwargs)
        d = defaults
        conn = sqlite3.connect(str(self.state_db))
        conn.execute(
            "INSERT INTO sessions (model, started_at, input_tokens, output_tokens, "
            "cache_read_tokens, cache_write_tokens) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                d["model"],
                d["started_at"],
                d["input_tokens"],
                d["output_tokens"],
                d["cache_read_tokens"],
                d["cache_write_tokens"],
            ),
        )
        conn.commit()
        conn.close()

    def test_returns_dict_structure(self):
        """返回正确的 dict 结构。"""
        service = self._create_service()
        result = service.fetch_by_upstream("day")
        self.assertIn("upstreams", result)
        self.assertIsInstance(result["upstreams"], list)

    def test_empty_db_returns_empty_upstreams(self):
        """空数据库返回空 upstreams 列表。"""
        service = self._create_service()
        result = service.fetch_by_upstream("day")
        self.assertEqual(result, {"upstreams": []})

    def test_token_stats_only(self):
        """仅有 token_stats 数据时正确聚合。"""
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        self._insert_token_stat(
            request_id="req-1",
            target_model="qwen3.6-plus",
            request_ts=ts,
            input_tokens=100,
            output_tokens=200,
            upstream_id=1,
        )
        self._insert_token_stat(
            request_id="req-2",
            target_model="qwen3.6-plus",
            request_ts=ts,
            input_tokens=50,
            output_tokens=100,
            upstream_id=1,
        )
        self._setup_config_db()

        service = self._create_service()
        result = service.fetch_by_upstream("day")

        self.assertEqual(len(result["upstreams"]), 1)
        up = result["upstreams"][0]
        self.assertEqual(up["upstream_id"], 1)
        self.assertEqual(up["upstream_name"], "OpenAI")
        self.assertEqual(up["request_count"], 2)
        self.assertEqual(up["input_tokens"], 150)
        self.assertEqual(up["output_tokens"], 300)
        self.assertEqual(up["total_tokens"], 450)

    def test_sessions_only(self):
        """仅有 sessions 数据时正确聚合。"""
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        self._insert_session(
            model="qwen3.6-plus", started_at=ts, input_tokens=100, output_tokens=200
        )
        self._insert_session(
            model="qwen3.6-plus", started_at=ts, input_tokens=50, output_tokens=100
        )
        self._setup_config_db()

        service = self._create_service()
        result = service.fetch_by_upstream("day")

        self.assertEqual(len(result["upstreams"]), 1)
        up = result["upstreams"][0]
        self.assertEqual(up["upstream_id"], "[Hermes]")
        self.assertEqual(up["upstream_name"], "[Hermes]")
        self.assertEqual(up["request_count"], 2)
        self.assertEqual(up["input_tokens"], 150)
        self.assertEqual(up["output_tokens"], 300)

    def test_merged_both_sources_same_upstream(self):
        """token_stats 和 sessions 三源独立归桶，proxy→OpenAI，sessions→[Hermes]。"""
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        # token_stats: 100 input, 200 output → upstream_id=1
        self._insert_token_stat(
            request_id="proxy-1",
            target_model="qwen3.6-plus",
            request_ts=ts,
            input_tokens=100,
            output_tokens=200,
            upstream_id=1,
        )
        # sessions: 50 input, 100 output → [Hermes]
        self._insert_session(
            model="qwen3.6-plus", started_at=ts, input_tokens=50, output_tokens=100
        )
        self._setup_config_db()

        service = self._create_service()
        result = service.fetch_by_upstream("day")

        self.assertEqual(len(result["upstreams"]), 2)
        openai = [up for up in result["upstreams"] if up["upstream_id"] == 1]
        self.assertEqual(len(openai), 1)
        self.assertEqual(openai[0]["upstream_name"], "OpenAI")
        self.assertEqual(openai[0]["request_count"], 1)
        self.assertEqual(openai[0]["input_tokens"], 100)
        self.assertEqual(openai[0]["output_tokens"], 200)

        hermes = [up for up in result["upstreams"] if up["upstream_id"] == "[Hermes]"]
        self.assertEqual(len(hermes), 1)
        self.assertEqual(hermes[0]["upstream_name"], "[Hermes]")
        self.assertEqual(hermes[0]["request_count"], 1)
        self.assertEqual(hermes[0]["input_tokens"], 50)
        self.assertEqual(hermes[0]["output_tokens"], 100)

    def test_merged_different_upstreams(self):
        """不同 upstream 正确分组。"""
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        # token_stats → openai (upstream_id=1)
        self._insert_token_stat(
            request_id="proxy-1",
            target_model="qwen3.6-plus",
            request_ts=ts,
            input_tokens=100,
            output_tokens=200,
            upstream_id=1,
        )
        # sessions → [Hermes]
        self._insert_session(
            model="claude-sonnet-4", started_at=ts, input_tokens=300, output_tokens=500
        )
        self._setup_config_db()

        service = self._create_service()
        result = service.fetch_by_upstream("day")

        self.assertEqual(len(result["upstreams"]), 2)
        upstream_ids = {up["upstream_id"] for up in result["upstreams"]}
        self.assertIn(1, upstream_ids)
        self.assertIn("[Hermes]", upstream_ids)

    def test_orphan_sessions_go_to_unknown(self):
        """无法解析到 upstream 的 sessions 归入 [Hermes]。"""
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        # orphan session
        self._insert_session(
            model="unknown-orphan-model",
            started_at=ts,
            input_tokens=100,
            output_tokens=200,
        )
        self._setup_config_db()

        service = self._create_service()
        result = service.fetch_by_upstream("day")

        hermes = [
            up for up in result["upstreams"] if up["upstream_id"] == "[Hermes]"
        ]
        self.assertEqual(len(hermes), 1)
        self.assertEqual(hermes[0]["request_count"], 1)
        self.assertEqual(hermes[0]["input_tokens"], 100)
        self.assertEqual(hermes[0]["output_tokens"], 200)
        self.assertIsNone(hermes[0]["base_url"])

    def test_orphan_token_stats_excluded(self):
        """无 upstream_id 的 token_stats 不进入上游统计（避免 __unknown__ 干扰）。"""
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        self._insert_token_stat(
            request_id="orphan-1",
            target_model="orphan-model",
            request_ts=ts,
            input_tokens=50,
            output_tokens=100,
        )
        self._setup_config_db()

        service = self._create_service()
        result = service.fetch_by_upstream("day")

        unknown = [
            up for up in result["upstreams"] if up["upstream_name"] == "__unknown__"
        ]
        self.assertEqual(len(unknown), 0)

    def _setup_pricing_db(self):
        """通过 PricingDB 创建定价数据。"""
        from proxy.pricing_manager import PricingDB

        db = PricingDB(self.config_db)
        db.add_pricing(
            {
                "model_id": "claude-sonnet-4",
                "display_name": "Claude Sonnet 4",
                "input_cost_per_million": "10.0",
                "output_cost_per_million": "20.0",
                "currency": "USD",
            }
        )

    def test_sorted_by_estimated_cost_cny_desc(self):
        """结果按 estimated_cost_cny 降序排列。"""
        from proxy.pricing_manager import PricingDB

        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        # openai: cheaper pricing (0.325/1.95 USD)
        self._insert_token_stat(
            request_id="proxy-1",
            target_model="qwen3.6-plus",
            request_ts=ts,
            input_tokens=1000,
            output_tokens=2000,
            upstream_id=1,
        )
        # anthropic: expensive pricing (10.0/20.0 USD)
        self._insert_token_stat(
            request_id="proxy-2",
            model="claude-sonnet-4",
            target_model="claude-sonnet-4",
            request_ts=ts,
            input_tokens=1000,
            output_tokens=2000,
            upstream_id=2,
        )
        self._setup_config_db()
        # Add pricing for models (cost calculator receives model name)
        PricingDB(self.config_db).add_pricing({
            "model_id": "gpt-4",
            "display_name": "GPT-4 Summary",
            "input_cost_per_million": "0.325",
            "output_cost_per_million": "1.95",
            "currency": "USD",
        })
        PricingDB(self.config_db).add_pricing({
            "model_id": "claude-sonnet-4",
            "display_name": "Claude Summary",
            "input_cost_per_million": "10.0",
            "output_cost_per_million": "20.0",
            "currency": "USD",
        })

        service = self._create_service()
        result = service.fetch_by_upstream("day")

        self.assertEqual(len(result["upstreams"]), 2)
        # anthropic should be first (higher cost per token)
        self.assertEqual(
            result["upstreams"][0]["upstream_id"], 2
        )
        self.assertGreater(
            result["upstreams"][0]["estimated_cost_cny"],
            result["upstreams"][1]["estimated_cost_cny"],
        )
        self.assertEqual(
            result["upstreams"][1]["upstream_id"], 1
        )

    def test_base_url_correct(self):
        """upstream 的 base_url 正确。"""
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        self._insert_token_stat(
            request_id="proxy-1",
            target_model="qwen3.6-plus",
            request_ts=ts,
            input_tokens=100,
            output_tokens=200,
            upstream_id=1,
        )
        self._setup_config_db()

        service = self._create_service()
        result = service.fetch_by_upstream("day")

        openai = [
            up
            for up in result["upstreams"]
            if up["upstream_id"] == 1
        ]
        self.assertEqual(len(openai), 1)
        self.assertEqual(openai[0]["base_url"], "https://api.openai.com")

    def test_unknown_base_url_is_none(self):
        """[Hermes] 的 base_url 为 None。"""
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        self._insert_session(
            model="orphan-model", started_at=ts, input_tokens=100, output_tokens=200
        )
        self._setup_config_db()

        service = self._create_service()
        result = service.fetch_by_upstream("day")

        hermes = [
            up for up in result["upstreams"] if up["upstream_id"] == "[Hermes]"
        ]
        self.assertEqual(len(hermes), 1)
        self.assertIsNone(hermes[0]["base_url"])

    def test_config_db_not_exists_returns_empty(self):
        """config.db 不存在时返回空 upstreams。"""
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        self._insert_token_stat(
            request_id="req-1",
            target_model="qwen3.6-plus",
            request_ts=ts,
            input_tokens=100,
            output_tokens=200,
        )
        # 不创建 config.db

        service = self._create_service()
        result = service.fetch_by_upstream("day")

        self.assertEqual(result, {"upstreams": []})

    def test_cost_calculation_non_zero(self):
        """有定价数据时成本计算非零（USD × 7 = RMB）。"""
        from proxy.pricing_manager import PricingDB

        PricingDB(self.config_db)  # 建表 + 种子数据
        PricingDB(self.config_db).add_pricing({
            "model_id": "gpt-4",
            "display_name": "GPT-4 Summary",
            "input_cost_per_million": "0.325",
            "output_cost_per_million": "1.95",
            "currency": "USD",
        })

        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        self._insert_token_stat(
            request_id="req-1",
            target_model="qwen3.6-plus",
            request_ts=ts,
            input_tokens=1000,
            output_tokens=2000,
            upstream_id=1,
        )
        self._setup_config_db()

        service = self._create_service()
        result = service.fetch_by_upstream("day")

        self.assertEqual(len(result["upstreams"]), 1)
        up = result["upstreams"][0]
        self.assertGreater(up["estimated_cost_cny"], 0)
        # 验证计算（RMB）：(1000/1M * 0.325*7) + (2000/1M * 1.95*7) = 0.002275 + 0.0273 = 0.029575
        expected = (1000 / 1_000_000 * 0.325 * 7) + (2000 / 1_000_000 * 1.95 * 7)
        self.assertAlmostEqual(up["estimated_cost_cny"], expected, places=6)

    def test_output_uses_cache_not_cached_prefix(self):
        """fetch_by_upstream 输出字段名统一为 cache_*。"""
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        self._insert_token_stat(
            request_id="req-1",
            target_model="qwen3.6-plus",
            request_ts=ts,
            input_tokens=100,
            output_tokens=200,
        )
        self._setup_config_db()
        result = self._create_service().fetch_by_upstream("week")
        for u in result["upstreams"]:
            self.assertIn("cache_read_tokens", u)
            self.assertNotIn("cached_read_tokens", u)
            self.assertIn("cache_write_tokens", u)
            self.assertNotIn("cached_write_tokens", u)

    def test_session_and_token_orphan_merged_to_unknown(self):
        """orphan token_stats → __unknown__，orphan sessions → [Hermes]，独立归桶。"""
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        # orphan token_stats
        self._insert_token_stat(
            request_id="orphan-proxy",
            target_model="orphan-model",
            request_ts=ts,
            input_tokens=50,
            output_tokens=100,
        )
        # orphan session → [Hermes]
        self._insert_session(
            model="orphan-model", started_at=ts, input_tokens=30, output_tokens=60
        )
        self._setup_config_db()

        service = self._create_service()
        result = service.fetch_by_upstream("day")

        unknown = [
            up for up in result["upstreams"] if up["upstream_name"] == "__unknown__"
        ]
        self.assertEqual(len(unknown), 0)

        hermes = [
            up for up in result["upstreams"] if up["upstream_id"] == "[Hermes]"
        ]
        self.assertEqual(len(hermes), 1)
        self.assertEqual(hermes[0]["request_count"], 1)
        self.assertEqual(hermes[0]["input_tokens"], 30)
        self.assertEqual(hermes[0]["output_tokens"], 60)


class TestMerger(unittest.TestCase):
    """_Merger 双源合并测试。"""

    def test_merge_summary_sums_fields(self):
        proxy = {
            "period": "week",
            "request_count": 10,
            "input_tokens": 100,
            "output_tokens": 50,
            "cached_read_tokens": 200,
            "cached_write_tokens": 30,
            "total_tokens": 380,
            "avg_duration_ms": 150.0,
        }
        session = {
            "period": "week",
            "request_count": 5,
            "input_tokens": 80,
            "output_tokens": 40,
            "cached_read_tokens": 160,
            "cached_write_tokens": 20,
            "total_tokens": 300,
            "avg_duration_ms": 0,
        }
        from stats_service import _Merger

        result = _Merger.merge_summary(proxy, session)
        self.assertEqual(result["request_count"], 15)
        self.assertEqual(result["input_tokens"], 180)
        self.assertEqual(result["output_tokens"], 90)
        self.assertEqual(result["cache_read_tokens"], 360)
        self.assertEqual(result["cache_write_tokens"], 50)
        self.assertEqual(result["total_tokens"], 680)
        self.assertEqual(result["avg_duration_ms"], 150.0)

    def test_merge_summary_renames_cached_to_cache(self):
        from stats_service import _Merger

        proxy = {
            "period": "day",
            "request_count": 1,
            "input_tokens": 10,
            "output_tokens": 5,
            "cached_read_tokens": 20,
            "cached_write_tokens": 3,
            "total_tokens": 38,
            "avg_duration_ms": 0,
        }
        session = {
            "period": "day",
            "request_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_read_tokens": 0,
            "cached_write_tokens": 0,
            "total_tokens": 0,
            "avg_duration_ms": 0,
        }
        result = _Merger.merge_summary(proxy, session)
        self.assertIn("cache_read_tokens", result)
        self.assertNotIn("cached_read_tokens", result)
        self.assertIn("cache_write_tokens", result)
        self.assertNotIn("cached_write_tokens", result)

    def test_merge_summary_empty_session(self):
        from stats_service import _Merger

        proxy = {
            "period": "week",
            "request_count": 10,
            "input_tokens": 100,
            "output_tokens": 50,
            "cached_read_tokens": 200,
            "cached_write_tokens": 30,
            "total_tokens": 380,
            "avg_duration_ms": 100.0,
        }
        result = _Merger.merge_summary(proxy, {})
        self.assertEqual(result["request_count"], 10)
        self.assertEqual(result["cache_read_tokens"], 200)

    def test_merge_summary_no_estimated_cost(self):
        from stats_service import _Merger

        proxy = {
            "period": "week",
            "request_count": 1,
            "input_tokens": 100,
            "output_tokens": 50,
            "cached_read_tokens": 0,
            "cached_write_tokens": 0,
            "total_tokens": 150,
            "avg_duration_ms": 0,
        }
        result = _Merger.merge_summary(proxy, {})
        self.assertNotIn("estimated_cost_cny", result)

    def test_merge_model_lists_sums_same_model(self):
        from stats_service import _Merger

        proxy = [
            {
                "model": "claude-3.5-sonnet",
                "request_count": 5,
                "input_tokens": 100,
                "output_tokens": 50,
                "cached_read_tokens": 20,
                "cached_write_tokens": 10,
                "total_tokens": 180,
                "avg_duration_ms": 200.0,
            },
        ]
        session = [
            {
                "model": "claude-3.5-sonnet",
                "request_count": 3,
                "input_tokens": 60,
                "output_tokens": 30,
                "cached_read_tokens": 15,
                "cached_write_tokens": 5,
                "total_tokens": 110,
            },
        ]
        result = _Merger.merge_model_lists(proxy, session)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["model"], "claude-3.5-sonnet")
        self.assertEqual(result[0]["request_count"], 8)
        self.assertEqual(result[0]["input_tokens"], 160)
        self.assertEqual(result[0]["cache_read_tokens"], 35)
        self.assertEqual(result[0]["avg_duration_ms"], 200.0)

    def test_merge_model_lists_different_models(self):
        from stats_service import _Merger

        proxy = [
            {
                "model": "model-a",
                "request_count": 2,
                "input_tokens": 10,
                "output_tokens": 5,
                "cached_read_tokens": 0,
                "cached_write_tokens": 0,
                "total_tokens": 15,
                "avg_duration_ms": 100.0,
            },
        ]
        session = [
            {
                "model": "model-b",
                "request_count": 3,
                "input_tokens": 20,
                "output_tokens": 10,
                "cached_read_tokens": 0,
                "cached_write_tokens": 0,
                "total_tokens": 30,
            },
        ]
        result = _Merger.merge_model_lists(proxy, session)
        self.assertEqual(len(result), 2)

    def test_merge_model_lists_normalizes_names(self):
        from stats_service import _Merger

        proxy = [
            {
                "model": "claude-3.5-sonnet",
                "request_count": 5,
                "input_tokens": 100,
                "output_tokens": 50,
                "cached_read_tokens": 20,
                "cached_write_tokens": 10,
                "total_tokens": 180,
                "avg_duration_ms": 0,
            },
        ]
        session = [
            {
                "model": "claude-3.5-sonnet[1m]",
                "request_count": 3,
                "input_tokens": 60,
                "output_tokens": 30,
                "cached_read_tokens": 15,
                "cached_write_tokens": 5,
                "total_tokens": 110,
            },
        ]
        result = _Merger.merge_model_lists(proxy, session)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["request_count"], 8)

    def test_merge_trend_lists_sums_same_time(self):
        from stats_service import _Merger

        proxy = [
            {
                "time": "2026-05-11",
                "request_count": 5,
                "input_tokens": 100,
                "output_tokens": 50,
                "cached_read_tokens": 20,
                "cached_write_tokens": 10,
                "total_tokens": 180,
            },
        ]
        session = [
            {
                "time": "2026-05-11",
                "request_count": 3,
                "input_tokens": 60,
                "output_tokens": 30,
                "cached_read_tokens": 15,
                "cached_write_tokens": 5,
                "total_tokens": 110,
            },
        ]
        result = _Merger.merge_trend_lists(proxy, session)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["request_count"], 8)
        self.assertEqual(result[0]["input_tokens"], 160)
        self.assertEqual(result[0]["cache_read_tokens"], 35)

    def test_merge_trend_lists_output_key_is_date(self):
        from stats_service import _Merger

        proxy = [
            {
                "time": "2026-05-11",
                "request_count": 5,
                "input_tokens": 100,
                "output_tokens": 50,
                "cached_read_tokens": 20,
                "cached_write_tokens": 10,
                "total_tokens": 180,
            },
        ]
        result = _Merger.merge_trend_lists(proxy, [])
        self.assertIn("date", result[0])
        self.assertNotIn("time", result[0])
        self.assertEqual(result[0]["date"], "2026-05-11")

    def test_merge_trend_lists_empty_session(self):
        from stats_service import _Merger

        proxy = [
            {
                "time": "2026-05-11",
                "request_count": 5,
                "input_tokens": 100,
                "output_tokens": 50,
                "cached_read_tokens": 20,
                "cached_write_tokens": 10,
                "total_tokens": 180,
            },
        ]
        result = _Merger.merge_trend_lists(proxy, [])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["cache_read_tokens"], 20)


class TestFetchSummaryMerged(unittest.TestCase):
    """fetch_summary 双源合并测试。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.data_db = Path(self.tmpdir) / "access_log.db"
        self.state_db = Path(self.tmpdir) / "state.db"
        self.config_db = self.data_db
        self._create_data_db()
        self._create_state_db()

    def _create_data_db(self):
        conn = sqlite3.connect(str(self.data_db))
        conn.execute("""CREATE TABLE IF NOT EXISTS token_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT NOT NULL, request_type TEXT NOT NULL,
            model TEXT NOT NULL, target_model TEXT NOT NULL,
            request_ts TEXT NOT NULL, duration_ms INTEGER,
            input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
            cached_read_tokens INTEGER DEFAULT 0, cached_write_tokens INTEGER DEFAULT 0,
            upstream_id INTEGER,
            status TEXT DEFAULT 'completed', created_at TEXT NOT NULL)""")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO token_stats VALUES (1,'r1','chat','m1','m1',?,100,100,50,20,10,NULL,'completed',?)",
            (now, now),
        )
        conn.commit()
        conn.close()

    def _create_state_db(self):
        import time as _time

        conn = sqlite3.connect(str(self.state_db))
        conn.execute("""CREATE TABLE sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, model TEXT,
            started_at REAL NOT NULL, input_tokens INTEGER,
            output_tokens INTEGER,
            cache_read_tokens INTEGER DEFAULT 0,
            cache_write_tokens INTEGER DEFAULT 0)""")
        conn.execute(
            "INSERT INTO sessions VALUES (1,'m1',?,80,40,15,5)", (_time.time(),)
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        import shutil

        if os.path.exists(self.tmpdir):
            shutil.rmtree(self.tmpdir)

    def _create_service(self):
        from stats_service import StatsService

        return StatsService(
            data_db_path=str(self.data_db),
            state_db_path=str(self.state_db),
            opencode_db_path=str(Path(self.tmpdir) / "nonexistent_opencode.db"),
        )

    def test_fetch_summary_merges_both_sources(self):
        svc = self._create_service()
        result = svc.fetch_summary("week")
        self.assertEqual(result["request_count"], 2)  # 1 proxy + 1 session
        self.assertEqual(result["input_tokens"], 180)  # 100 + 80
        self.assertEqual(result["output_tokens"], 90)  # 50 + 40
        self.assertIn("cache_read_tokens", result)
        self.assertNotIn("cached_read_tokens", result)
        self.assertEqual(result["cache_read_tokens"], 35)  # 20 + 15
        self.assertEqual(result["cache_write_tokens"], 15)  # 10 + 5
        self.assertIn("estimated_cost_cny", result)

    def test_fetch_summary_proxy_only(self):
        conn = sqlite3.connect(str(self.state_db))
        conn.execute("DELETE FROM sessions")
        conn.commit()
        conn.close()
        svc = self._create_service()
        result = svc.fetch_summary("week")
        self.assertEqual(result["request_count"], 1)
        self.assertEqual(result["input_tokens"], 100)

    def test_fetch_all_summaries(self):
        svc = self._create_service()
        result = svc.fetch_all_summaries()
        self.assertIn("day", result)
        self.assertIn("week", result)
        self.assertIn("month", result)
        self.assertIn("cache_read_tokens", result["week"])
        self.assertIn("estimated_cost_cny", result["week"])


class TestOpenCodeDao(unittest.TestCase):
    """_OpenCodeDao 测试 — 用临时 SQLite 文件模拟 opencode.db 结构。"""

    def setUp(self):
        """创建临时目录和模拟 opencode.db。"""
        import json
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "opencode.db"

        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            CREATE TABLE session (
                id TEXT PRIMARY KEY,
                model TEXT,
                time_created INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE message (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                time_created INTEGER NOT NULL,
                data TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES session(id)
            )
        """)

        # 插入测试数据：2 个 session，3 条 message
        # 时间戳设计：msg-001/msg-002 在 2-4 天前（week 范围，不在 day 范围），
        # msg-003 在 30 天前（超出 week 范围，自动被排除）
        now_ms = int(time.time() * 1000)
        # session 1: model = mimo-v2.5-pro, 2 messages (2 days and 4 days ago)
        conn.execute(
            "INSERT INTO session (id, model, time_created) VALUES (?, ?, ?)",
            ("ses-001", '{"id":"mimo-v2.5-pro","providerID":"XiaoMi"}', now_ms - 172800000),
        )
        msg1_data = json.dumps({
            "role": "assistant",
            "modelID": "mimo-v2.5-pro",
            "tokens": {"input": 100, "output": 50, "reasoning": 10, "cache": {"read": 20, "write": 0}},
            "time": {"created": now_ms - 172800000, "completed": now_ms - 171800000},
        })
        conn.execute(
            "INSERT INTO message (id, session_id, time_created, data) VALUES (?, ?, ?, ?)",
            ("msg-001", "ses-001", now_ms - 172800000, msg1_data),
        )
        msg2_data = json.dumps({
            "role": "assistant",
            "modelID": "mimo-v2.5-pro",
            "tokens": {"input": 200, "output": 80, "reasoning": 0, "cache": {"read": 0, "write": 10}},
            "time": {"created": now_ms - 345600000, "completed": now_ms - 344600000},
        })
        conn.execute(
            "INSERT INTO message (id, session_id, time_created, data) VALUES (?, ?, ?, ?)",
            ("msg-002", "ses-001", now_ms - 345600000, msg2_data),
        )
        # session 2: model = glm-4.7, 1 message (30 days ago → outside week range)
        conn.execute(
            "INSERT INTO session (id, model, time_created) VALUES (?, ?, ?)",
            ("ses-002", '{"id":"glm-4.7","providerID":"ZhiPu"}', now_ms - 2592000000),
        )
        msg3_data = json.dumps({
            "role": "user",
            "modelID": "glm-4.7",
            "tokens": {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
            "time": {"created": now_ms - 2592000000},
        })
        conn.execute(
            "INSERT INTO message (id, session_id, time_created, data) VALUES (?, ?, ?, ?)",
            ("msg-003", "ses-002", now_ms - 2592000000, msg3_data),
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        """清理临时文件。"""
        import shutil
        if os.path.exists(self.tmpdir):
            shutil.rmtree(self.tmpdir)

    def _create_dao(self):
        """创建 _OpenCodeDao 实例。"""
        from stats_service import _OpenCodeDao
        return _OpenCodeDao(self.db_path)

    # ─── aggregate_by_model 测试 ───

    def test_aggregate_by_model_week(self):
        """按模型聚合：week 周期返回 mimo-v2.5-pro 的 2 条记录（input=300, output=60+10）。"""
        dao = self._create_dao()
        result = dao.aggregate_by_model("week")
        self.assertEqual(len(result), 1)  # glm-4.7 无 tokens 被过滤
        self.assertEqual(result[0]["model"], "mimo-v2.5-pro")
        self.assertEqual(result[0]["request_count"], 2)
        self.assertEqual(result[0]["input_tokens"], 300)   # 100 + 200
        self.assertEqual(result[0]["output_tokens"], 140)   # (50+10) + (80+0)
        self.assertEqual(result[0]["cached_read_tokens"], 20)  # 20 + 0
        self.assertEqual(result[0]["cached_write_tokens"], 10) # 0 + 10

    def test_aggregate_by_model_day_no_data(self):
        """day 周期无数据 → 返回空列表。"""
        dao = self._create_dao()
        result = dao.aggregate_by_model("day")
        self.assertEqual(result, [])

    # ─── aggregate_summary 测试 ───

    def test_aggregate_summary_week(self):
        """汇总统计：request_count=2, tokens 求和正确。"""
        dao = self._create_dao()
        result = dao.aggregate_summary("week")
        self.assertEqual(result["request_count"], 2)
        self.assertEqual(result["input_tokens"], 300)
        self.assertEqual(result["output_tokens"], 140)
        self.assertEqual(result["total_tokens"], 470)  # 300 + 140 + 20 + 10
        self.assertGreater(result["avg_duration_ms"], 0)  # duration 有值

    def test_aggregate_summary_day_empty(self):
        """day 周期无数据 → 返回零值 dict。"""
        dao = self._create_dao()
        result = dao.aggregate_summary("day")
        self.assertEqual(result["request_count"], 0)
        self.assertEqual(result["input_tokens"], 0)

    # ─── aggregate_trend 测试 ───

    def test_aggregate_trend_week(self):
        """趋势数据：返回 time key，按天分组。"""
        dao = self._create_dao()
        result = dao.aggregate_trend("week")
        self.assertGreater(len(result), 0)
        for point in result:
            self.assertIn("time", point)
            self.assertIn("input_tokens", point)
            self.assertIn("output_tokens", point)

    def test_aggregate_trend_day_period(self):
        """day 周期按小时分组，返回 time key。"""
        dao = self._create_dao()
        result = dao.aggregate_trend("day")
        self.assertEqual(result, [])

    # ─── query_messages_paged 测试 ───

    def test_query_messages_paged_basic(self):
        """分页查询：返回 2 条有效 message。"""
        dao = self._create_dao()
        records, total = dao.query_messages_paged("week")
        self.assertEqual(total, 2)
        self.assertEqual(len(records), 2)

    def test_query_messages_paged_model_filter(self):
        """按模型过滤：mimo-v2.5-pro → 2 条，glm-4.7 → 0 条。"""
        dao = self._create_dao()
        _, total = dao.query_messages_paged("week", model="mimo-v2.5-pro")
        self.assertEqual(total, 2)
        _, total_none = dao.query_messages_paged("week", model="glm-4.7")
        self.assertEqual(total_none, 0)

    def test_query_messages_paged_request_type_filter(self):
        """request_type 过滤：session → 2 条，proxy → 0 条。"""
        dao = self._create_dao()
        _, total_session = dao.query_messages_paged("week", request_type="session")
        self.assertEqual(total_session, 2)
        _, total_proxy = dao.query_messages_paged("week", request_type="proxy")
        self.assertEqual(total_proxy, 0)

    def test_query_messages_paged_record_format(self):
        """记录格式：request_id 以 oc-msg- 开头，request_type 为 session，_source 为 opencode。"""
        dao = self._create_dao()
        records, _ = dao.query_messages_paged("week")
        self.assertEqual(len(records), 2)
        for r in records:
            self.assertTrue(r["request_id"].startswith("oc-msg-"))
            self.assertEqual(r["request_type"], "session")
            self.assertEqual(r["_source"], "opencode")
            self.assertEqual(r["status"], "completed")

    def test_query_messages_paged_pagination(self):
        """分页：limit=1, offset=0 → 1 条，total=2。"""
        dao = self._create_dao()
        records, total = dao.query_messages_paged("week", limit=1, offset=0)
        self.assertEqual(total, 2)
        self.assertEqual(len(records), 1)

    # ─── db 不存在测试 ───

    def test_db_not_exists(self):
        """opencode.db 不存在 → 各方法返回空结果，不抛异常。"""
        from stats_service import _OpenCodeDao
        dao = _OpenCodeDao(Path("/nonexistent/opencode.db"))

        self.assertEqual(dao.aggregate_by_model("week"), [])
        summary = dao.aggregate_summary("week")
        self.assertEqual(summary["request_count"], 0)
        self.assertEqual(dao.aggregate_trend("week"), [])
        self.assertEqual(dao.query_messages_paged("week"), ([], 0))

    def test_query_raw_returns_unified_schema(self):
        """query_raw 返回统一格式，upstream_id 固定为 'opencode'。"""
        from stats_service import _OpenCodeDao
        dao = _OpenCodeDao(self.db_path)
        records = dao.query_raw("week")

        self.assertGreater(len(records), 0)
        r = records[0]
        self.assertEqual(r["upstream_id"], "opencode")
        self.assertEqual(r["request_type"], "session")
        self.assertIn("input_tokens", r)
        self.assertIn("output_tokens", r)
        self.assertIn("cache_read_tokens", r)
        self.assertIn("cache_write_tokens", r)
        self.assertNotIn("_source", r)
        self.assertNotIn("target_model", r)

    # ─── _Merger 三源合并测试 ───

    def test_merger_three_source_summary(self):
        """merge_summary 三源求和正确：proxy + session + opencode 数值字段累加。"""
        from stats_service import _Merger

        p = {"period": "week", "request_count": 10, "input_tokens": 100,
             "output_tokens": 50, "cached_read_tokens": 20, "cached_write_tokens": 5,
             "total_tokens": 175, "avg_duration_ms": 300}
        s = {"period": "week", "request_count": 3, "input_tokens": 30,
             "output_tokens": 15, "cached_read_tokens": 10, "cached_write_tokens": 2,
             "total_tokens": 57, "avg_duration_ms": 0}
        o = {"period": "week", "request_count": 5, "input_tokens": 200,
             "output_tokens": 100, "cached_read_tokens": 50, "cached_write_tokens": 10,
             "total_tokens": 360, "avg_duration_ms": 250}

        result = _Merger.merge_summary(p, s, o)
        self.assertEqual(result["request_count"], 18)  # 10+3+5
        self.assertEqual(result["input_tokens"], 330)  # 100+30+200
        self.assertEqual(result["output_tokens"], 165)  # 50+15+100
        # avg_duration_ms: proxy(300) > opencode(250) > session(0), 取 proxy
        self.assertEqual(result["avg_duration_ms"], 300)

    def test_merger_three_source_summary_empty_sources(self):
        """merge_summary 部分源为空：健壮处理。"""
        from stats_service import _Merger

        p = {"period": "week", "request_count": 10, "input_tokens": 100,
             "output_tokens": 50, "cached_read_tokens": 20, "cached_write_tokens": 5,
             "total_tokens": 175, "avg_duration_ms": 300}
        empty = {"period": "week", "request_count": 0, "input_tokens": 0,
                 "output_tokens": 0, "cached_read_tokens": 0, "cached_write_tokens": 0,
                 "total_tokens": 0, "avg_duration_ms": 0}

        result = _Merger.merge_summary(p, empty, empty)
        self.assertEqual(result["request_count"], 10)

    def test_merger_three_source_model_lists(self):
        """merge_model_lists 三源同名模型求和，不同模型独立保留。"""
        from stats_service import _Merger

        p = [{"model": "gpt-4", "request_count": 5, "input_tokens": 100,
              "output_tokens": 50, "cached_read_tokens": 10, "cached_write_tokens": 5,
              "avg_duration_ms": 200}]
        s = [{"model": "gpt-4", "request_count": 3, "input_tokens": 60,
              "output_tokens": 30, "cached_read_tokens": 5, "cached_write_tokens": 2,
              "avg_duration_ms": 0}]
        o = [{"model": "claude-sonnet", "request_count": 2, "input_tokens": 200,
              "output_tokens": 100, "cached_read_tokens": 20, "cached_write_tokens": 10,
              "avg_duration_ms": 0}]

        result = _Merger.merge_model_lists(p, s, o)
        self.assertEqual(len(result), 2)  # gpt-4 + claude-sonnet
        models = {m["model"] for m in result}
        self.assertIn("gpt-4", models)
        self.assertIn("claude-sonnet", models)

        gpt4 = next(m for m in result if m["model"] == "gpt-4")
        self.assertEqual(gpt4["input_tokens"], 160)  # 100+60
        self.assertEqual(gpt4["request_count"], 8)  # 5+3

    def test_merger_three_source_trend_lists(self):
        """merge_trend_lists 三源同时间桶求和。"""
        from stats_service import _Merger

        p = [{"time": "2026-05-10", "request_count": 2, "input_tokens": 100,
              "output_tokens": 50, "cached_read_tokens": 10, "cached_write_tokens": 5}]
        s = [{"time": "2026-05-10", "request_count": 1, "input_tokens": 30,
              "output_tokens": 15, "cached_read_tokens": 5, "cached_write_tokens": 2}]
        o = [{"time": "2026-05-12", "request_count": 3, "input_tokens": 200,
              "output_tokens": 100, "cached_read_tokens": 20, "cached_write_tokens": 10}]

        result = _Merger.merge_trend_lists(p, s, o)
        self.assertEqual(len(result), 2)  # 05-10 + 05-12

        day10 = next(r for r in result if r["date"] == "2026-05-10")
        self.assertEqual(day10["input_tokens"], 130)  # 100+30
        self.assertEqual(day10["request_count"], 3)  # 2+1

        day12 = next(r for r in result if r["date"] == "2026-05-12")
        self.assertEqual(day12["input_tokens"], 200)


class TestCrossViewConsistency(unittest.TestCase):
    """所有视图的数据一致性验证 — 同源同结果。"""

    def setUp(self):
        import json, time
        self.tmpdir = tempfile.mkdtemp()
        self.data_db = Path(self.tmpdir) / "access_log.db"
        self.state_db = Path(self.tmpdir) / "state.db"
        self.opencode_db = Path(self.tmpdir) / "opencode.db"

        conn = sqlite3.connect(str(self.data_db))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""CREATE TABLE token_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT, request_type TEXT,
            model TEXT, target_model TEXT, request_ts TEXT, duration_ms INTEGER,
            input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
            cached_read_tokens INTEGER DEFAULT 0, cached_write_tokens INTEGER DEFAULT 0,
            upstream_id INTEGER, status TEXT DEFAULT 'completed', created_at TEXT)""")
        conn.execute("""CREATE TABLE upstreams (
            id TEXT PRIMARY KEY, base_url TEXT, name TEXT, is_active INTEGER, format TEXT)""")
        conn.execute("""CREATE TABLE target_models (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, upstream_id TEXT)""")
        conn.execute("""CREATE TABLE model_pricing (
            model_id TEXT PRIMARY KEY, display_name TEXT,
            input_cost_per_million REAL DEFAULT 0, output_cost_per_million REAL DEFAULT 0,
            cache_read_cost_per_million REAL DEFAULT 0, cache_creation_cost_per_million REAL DEFAULT 0,
            currency TEXT DEFAULT 'RMB', multiplier REAL DEFAULT 1.0,
            created_at TEXT, updated_at TEXT)""")

        conn.execute("INSERT INTO upstreams (id, name, is_active, format) "
                     "VALUES ('up-a', 'Upstream A', 1, 'chat_completions')")
        conn.execute("INSERT INTO target_models (name, upstream_id) VALUES ('model-a', 'up-a')")
        conn.execute("INSERT INTO target_models (name, upstream_id) VALUES ('model-b', 'up-a')")
        conn.execute("INSERT INTO model_pricing (model_id, input_cost_per_million, "
                     "output_cost_per_million, cache_read_cost_per_million, cache_creation_cost_per_million, "
                     "currency) VALUES ('model-a', 1.0, 2.0, 0.5, 0.25, 'RMB')")
        conn.execute("INSERT INTO model_pricing (model_id, input_cost_per_million, "
                     "output_cost_per_million, cache_read_cost_per_million, cache_creation_cost_per_million, "
                     "currency) VALUES ('model-b', 3.0, 4.0, 1.0, 0.5, 'RMB')")

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("INSERT INTO token_stats (request_id, request_type, model, target_model, "
                     "request_ts, input_tokens, output_tokens, cached_read_tokens, cached_write_tokens, "
                     "upstream_id, status, created_at) VALUES "
                     "('req-1', 'responses', 'model-a', 'model-a', ?, 100, 50, 10, 5, 'up-a', 'completed', ?)",
                     (now, now))
        conn.execute("INSERT INTO token_stats (request_id, request_type, model, target_model, "
                     "request_ts, input_tokens, output_tokens, cached_read_tokens, cached_write_tokens, "
                     "upstream_id, status, created_at) VALUES "
                     "('req-2', 'responses', 'model-b', 'model-b', ?, 200, 100, 20, 10, 'up-a', 'completed', ?)",
                     (now, now))
        conn.commit()
        conn.close()

        sconn = sqlite3.connect(str(self.state_db))
        sconn.execute("""CREATE TABLE sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, model TEXT,
            started_at REAL, input_tokens INTEGER, output_tokens INTEGER,
            cache_read_tokens INTEGER DEFAULT 0, cache_write_tokens INTEGER DEFAULT 0,
            message_count INTEGER DEFAULT 0)""")
        sconn.execute("INSERT INTO sessions (model, started_at, input_tokens, output_tokens, "
                      "cache_read_tokens, cache_write_tokens) VALUES ('model-a', ?, 300, 150, 30, 15)",
                      (time.time(),))
        sconn.commit()
        sconn.close()

        oconn = sqlite3.connect(str(self.opencode_db))
        oconn.execute("CREATE TABLE session (id TEXT PRIMARY KEY, model TEXT, time_created INTEGER)")
        oconn.execute("CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, "
                      "time_created INTEGER, data TEXT)")
        now_ms = int(time.time() * 1000)
        msg_data = json.dumps({
            "role": "assistant", "modelID": "model-a",
            "tokens": {"input": 50, "output": 25, "reasoning": 0, "cache": {"read": 5, "write": 2}},
            "time": {"created": now_ms - 3600000, "completed": now_ms - 3500000},
        })
        oconn.execute("INSERT INTO message (id, session_id, time_created, data) VALUES (?, ?, ?, ?)",
                      ("msg-1", "s-1", now_ms - 3600000, msg_data))
        oconn.commit()
        oconn.close()

    def tearDown(self):
        import os
        for p in [self.data_db, self.state_db, self.opencode_db]:
            if p.exists():
                os.remove(str(p))
                for s in ["-wal", "-shm"]:
                    wp = Path(str(p) + s)
                    if wp.exists():
                        os.remove(str(wp))
        os.rmdir(self.tmpdir)

    def _create_service(self):
        from stats_service import StatsService
        return StatsService(
            data_db_path=str(self.data_db),
            state_db_path=str(self.state_db),
            opencode_db_path=str(self.opencode_db),
        )

    def test_summary_matches_by_model_sum(self):
        """fetch_summary 的 token 总数等于 fetch_by_model 各行求和。"""
        svc = self._create_service()
        summary = svc.fetch_summary("week")
        by_model = svc.fetch_by_model("week")

        model_input_sum = sum(m["input_tokens"] for m in by_model)
        model_output_sum = sum(m["output_tokens"] for m in by_model)
        model_cache_read_sum = sum(m["cache_read_tokens"] for m in by_model)
        model_cache_write_sum = sum(m["cache_write_tokens"] for m in by_model)
        model_total_sum = sum(m["total_tokens"] for m in by_model)
        model_cost_sum = sum(m["estimated_cost_cny"] for m in by_model)

        self.assertEqual(summary["input_tokens"], model_input_sum)
        self.assertEqual(summary["output_tokens"], model_output_sum)
        self.assertEqual(summary["cache_read_tokens"], model_cache_read_sum)
        self.assertEqual(summary["cache_write_tokens"], model_cache_write_sum)
        self.assertAlmostEqual(summary["estimated_cost_cny"], model_cost_sum, places=6)

    def test_summary_matches_by_upstream_sum(self):
        """fetch_summary 的 token 总数等于 fetch_by_upstream 各行求和。"""
        svc = self._create_service()
        summary = svc.fetch_summary("week")
        by_up = svc.fetch_by_upstream("week")["upstreams"]

        up_input_sum = sum(u["input_tokens"] for u in by_up)
        up_output_sum = sum(u["output_tokens"] for u in by_up)
        up_cost_sum = sum(u["estimated_cost_cny"] for u in by_up)

        self.assertEqual(summary["input_tokens"], up_input_sum)
        self.assertEqual(summary["output_tokens"], up_output_sum)
        self.assertAlmostEqual(summary["estimated_cost_cny"], up_cost_sum, places=6)

    def test_fetch_requests_total_matches_all_records(self):
        """fetch_requests 的 total 等于全量记录数。"""
        svc = self._create_service()
        result = svc.fetch_requests("week", limit=10)
        all_records = svc._fetch_unified_records("week")
        self.assertEqual(result["total"], len(all_records))

    def test_virtual_upstream_names(self):
        """虚拟上游 [Hermes]/[OpenCode] 展示名正确。"""
        svc = self._create_service()
        result = svc.fetch_by_upstream("week")
        name_map = {u["upstream_id"]: u["upstream_name"] for u in result["upstreams"]}

        self.assertEqual(name_map.get("hermes"), "[Hermes]")
        self.assertEqual(name_map.get("opencode"), "[OpenCode]")


class TestFetchUnifiedRecords(unittest.TestCase):
    """_fetch_unified_records 集成测试 — 三源数据合并 + 成本计算 + 分页。"""

    def setUp(self):
        import json, time
        self.tmpdir = tempfile.mkdtemp()
        self.data_db = Path(self.tmpdir) / "access_log.db"
        self.state_db = Path(self.tmpdir) / "state.db"
        self.opencode_db = Path(self.tmpdir) / "opencode.db"

        # ── data db: token_stats + upstreams + target_models + model_pricing ──
        conn = sqlite3.connect(str(self.data_db))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""CREATE TABLE token_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL,
            request_type TEXT NOT NULL, model TEXT NOT NULL, target_model TEXT NOT NULL,
            request_ts TEXT NOT NULL, duration_ms INTEGER, input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0, cached_read_tokens INTEGER DEFAULT 0,
            cached_write_tokens INTEGER DEFAULT 0, upstream_id INTEGER,
            status TEXT DEFAULT 'completed', created_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE upstreams (
            id TEXT PRIMARY KEY, base_url TEXT, api_key TEXT, name TEXT,
            timeout INTEGER, connect_timeout INTEGER, ssl_verify INTEGER,
            retry INTEGER, is_active INTEGER, format TEXT)""")
        conn.execute("""CREATE TABLE target_models (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, upstream_id TEXT,
            multimodal INTEGER)""")
        conn.execute("""CREATE TABLE model_pricing (
            model_id TEXT PRIMARY KEY, display_name TEXT,
            input_cost_per_million REAL DEFAULT 0, output_cost_per_million REAL DEFAULT 0,
            cache_read_cost_per_million REAL DEFAULT 0, cache_creation_cost_per_million REAL DEFAULT 0,
            currency TEXT DEFAULT 'RMB', multiplier REAL DEFAULT 1.0,
            created_at TEXT, updated_at TEXT)""")

        # 上游 + 模型映射
        conn.execute("INSERT INTO upstreams (id, base_url, name, is_active, format) "
                     "VALUES ('up-ds', 'https://api.deepseek.com', 'DeepSeek', 1, 'chat_completions')")
        conn.execute("INSERT INTO target_models (name, upstream_id) VALUES ('deepseek-v4-flash', 'up-ds')")
        # 定价
        conn.execute("INSERT INTO model_pricing (model_id, display_name, input_cost_per_million, "
                     "output_cost_per_million, cache_read_cost_per_million, cache_creation_cost_per_million, "
                     "currency) VALUES ('deepseek-v4-flash', 'DeepSeek V4 Flash', 1.0, 2.0, 0.5, 0.25, 'RMB')")
        # token_stats 数据
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("INSERT INTO token_stats (request_id, request_type, model, target_model, "
                     "request_ts, duration_ms, input_tokens, output_tokens, cached_read_tokens, "
                     "cached_write_tokens, upstream_id, status, created_at) "
                     "VALUES ('req-001', 'responses', 'deepseek-v4-flash', 'deepseek-v4-flash', "
                     "?, 500, 1000, 500, 100, 50, 'up-ds', 'completed', ?)", (now, now))
        conn.commit()
        conn.close()

        # ── state db: sessions ──
        sconn = sqlite3.connect(str(self.state_db))
        sconn.execute("""CREATE TABLE sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, model TEXT,
            started_at REAL NOT NULL, input_tokens INTEGER, output_tokens INTEGER,
            cache_read_tokens INTEGER DEFAULT 0, cache_write_tokens INTEGER DEFAULT 0,
            message_count INTEGER DEFAULT 0)""")
        sconn.execute("INSERT INTO sessions (model, started_at, input_tokens, output_tokens, "
                      "cache_read_tokens, cache_write_tokens) VALUES (?, ?, ?, ?, ?, ?)",
                      ("claude-sonnet-4-6[1m]", time.time(), 2000, 1000, 200, 100))
        sconn.commit()
        sconn.close()

        # ── opencode db: message ──
        oconn = sqlite3.connect(str(self.opencode_db))
        oconn.execute("""CREATE TABLE session (id TEXT PRIMARY KEY, model TEXT, time_created INTEGER)""")
        oconn.execute("""CREATE TABLE message (
            id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
            time_created INTEGER NOT NULL, data TEXT NOT NULL)""")
        now_ms = int(time.time() * 1000)
        msg_data = json.dumps({
            "role": "assistant", "modelID": "mimo-v2.5-pro",
            "tokens": {"input": 300, "output": 150, "reasoning": 0, "cache": {"read": 50, "write": 10}},
            "time": {"created": now_ms - 86400000, "completed": now_ms - 86300000},
        })
        oconn.execute("INSERT INTO message (id, session_id, time_created, data) VALUES (?, ?, ?, ?)",
                      ("msg-001", "ses-001", now_ms - 86400000, msg_data))
        oconn.commit()
        oconn.close()

    def tearDown(self):
        import os
        for p in [self.data_db, self.state_db, self.opencode_db]:
            if p.exists():
                os.remove(str(p))
                for s in ["-wal", "-shm"]:
                    wp = Path(str(p) + s)
                    if wp.exists():
                        os.remove(str(wp))
        os.rmdir(self.tmpdir)

    def _create_service(self):
        from stats_service import StatsService
        return StatsService(
            data_db_path=str(self.data_db),
            state_db_path=str(self.state_db),
            opencode_db_path=str(self.opencode_db),
        )

    def test_returns_all_records_from_three_sources(self):
        """三源数据合并后应包含全部记录。"""
        svc = self._create_service()
        records = svc._fetch_unified_records("week")

        # proxy: 1, session: 1, opencode: 1 → 共 3 条
        self.assertEqual(len(records), 3)
        upstream_ids = {r["upstream_id"] for r in records}
        self.assertIn("up-ds", upstream_ids)
        self.assertIn("hermes", upstream_ids)
        self.assertIn("opencode", upstream_ids)

    def test_cost_breakdown_fields_present(self):
        """每条记录包含 4 项独立成本。"""
        svc = self._create_service()
        records = svc._fetch_unified_records("week")

        for r in records:
            self.assertIn("input_cost_cny", r)
            self.assertIn("output_cost_cny", r)
            self.assertIn("cache_read_cost_cny", r)
            self.assertIn("cache_write_cost_cny", r)
            self.assertGreaterEqual(r["input_cost_cny"], 0)
            self.assertGreaterEqual(r["output_cost_cny"], 0)

    def test_model_normalization_for_sessions(self):
        """sessions 来源的 model 已去掉 [ctx] 后缀。"""
        svc = self._create_service()
        records = svc._fetch_unified_records("week")

        session_recs = [r for r in records if r["upstream_id"] == "hermes"]
        self.assertEqual(len(session_recs), 1)
        self.assertEqual(session_recs[0]["model"], "claude-sonnet-4-6")

    def test_field_rename_cached_to_cache(self):
        """token_stats 来源的字段名为 cache_* 而非 cached_*。"""
        svc = self._create_service()
        records = svc._fetch_unified_records("week")

        proxy_recs = [r for r in records if r["upstream_id"] == "up-ds"]
        self.assertEqual(len(proxy_recs), 1)
        r = proxy_recs[0]
        self.assertNotIn("cached_read_tokens", r)
        self.assertNotIn("cached_write_tokens", r)
        self.assertIn("cache_read_tokens", r)
        self.assertIn("cache_write_tokens", r)

    def test_pagination_returns_slice_and_total(self):
        """带分页参数时返回 (records, total) 元组。"""
        svc = self._create_service()
        result = svc._fetch_unified_records("week", limit=2, offset=0)

        self.assertIsInstance(result, tuple)
        records, total = result
        self.assertEqual(total, 3)
        self.assertEqual(len(records), 2)

    def test_model_filter_across_sources(self):
        """模型筛选跨三源生效。"""
        svc = self._create_service()
        records = svc._fetch_unified_records("week", model="deepseek-v4-flash")

        # 应只匹配 token_stats 中那条 deepseek-v4-flash 记录
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["model"], "deepseek-v4-flash")

    def test_no_source_field(self):
        """不包含 _source 字段。"""
        svc = self._create_service()
        records = svc._fetch_unified_records("week")
        for r in records:
            self.assertNotIn("_source", r)
