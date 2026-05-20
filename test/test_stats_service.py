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
        # 创建空 token_stats 表
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

    def tearDown(self):
        """清理临时文件。"""
        for db_path in [self.data_db, self.config_db]:
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
        """空数据库返回 24 个零值桶（补空桶逻辑）。"""
        service = self._create_service()
        result = service.fetch_trend("day")
        self.assertEqual(len(result), 24)
        for bucket in result:
            self.assertEqual(bucket["request_count"], 0)
            self.assertEqual(bucket["total_tokens"], 0)

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
        # 注意：补空桶后 result[0] 可能是零值桶，需要找到有数据的桶
        costs = [p["estimated_cost_cny"] for p in result if p["request_count"] > 0]
        if costs:
            self.assertGreater(max(costs), 0)

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
        # 按 estimated_cost_cny DESC（成本均为 0，保持原始插入顺序）
        self.assertEqual(upstreams[0]["upstream_id"], 1)
        self.assertEqual(upstreams[0]["upstream_name"], "OpenAI")
        self.assertEqual(upstreams[0]["output_tokens"], 200)
        self.assertEqual(upstreams[1]["upstream_id"], 2)
        self.assertEqual(upstreams[1]["upstream_name"], "Anthropic")
        self.assertEqual(upstreams[1]["output_tokens"], 500)
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
        # 新代码：即使无 config，也会聚合上游为 __unknown__
        self.assertEqual(len(result["upstreams"]), 1)
        self.assertEqual(result["upstreams"][0]["upstream_name"], "__unknown__")

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

    



    def test_db_not_exists(self):
        """opencode.db 不存在 → query_raw 返回空结果，不抛异常。"""
        from stats_service import _OpenCodeDao
        dao = _OpenCodeDao(Path("/nonexistent/opencode.db"))

        self.assertEqual(dao.query_raw("week"), [])

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

    

class TestCrossViewConsistency(unittest.TestCase):
    """所有视图的数据一致性验证 — 同源同结果。"""

    def setUp(self):
        import json, time
        self.tmpdir = tempfile.mkdtemp()
        self.data_db = Path(self.tmpdir) / "access_log.db"
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
        for p in [self.data_db, self.opencode_db]:
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
        """虚拟上游 [OpenCode] 展示名正确。"""
        svc = self._create_service()
        result = svc.fetch_by_upstream("week")
        name_map = {u["upstream_id"]: u["upstream_name"] for u in result["upstreams"]}

        self.assertEqual(name_map.get("opencode"), "[OpenCode]")


class TestFetchUnifiedRecords(unittest.TestCase):
    """_fetch_unified_records 集成测试 — 三源数据合并 + 成本计算 + 分页。"""

    def setUp(self):
        import json, time
        self.tmpdir = tempfile.mkdtemp()
        self.data_db = Path(self.tmpdir) / "access_log.db"
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
        for p in [self.data_db, self.opencode_db]:
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
            opencode_db_path=str(self.opencode_db),
        )

    def test_returns_all_records_from_two_sources(self):
        """两源数据合并后应包含全部记录。"""
        svc = self._create_service()
        records = svc._fetch_unified_records("week")

        # proxy: 1, opencode: 1 → 共 2 条
        self.assertEqual(len(records), 2)
        upstream_ids = {r["upstream_id"] for r in records}
        self.assertIn("up-ds", upstream_ids)
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
        self.assertEqual(total, 2)
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
