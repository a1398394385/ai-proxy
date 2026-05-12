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
            self._insert_test_data([
                {
                    "request_id": f"req-{i}",
                    "request_ts": ts,
                }
            ])

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

        self.assertEqual(len(result["upstreams"]), 2)
        upstreams = result["upstreams"]
        # 按 estimated_cost_usd 降序
        self.assertEqual(upstreams[0]["upstream_id"], "https://api.anthropic.com")
        self.assertEqual(upstreams[0]["output_tokens"], 500)
        self.assertEqual(upstreams[1]["upstream_id"], "https://api.openai.com")
        self.assertEqual(upstreams[1]["output_tokens"], 200)
        # 验证返回格式包含所有必需字段
        for up in upstreams:
            self.assertIn("upstream_id", up)
            self.assertIn("base_url", up)
            self.assertIn("request_count", up)
            self.assertIn("input_tokens", up)
            self.assertIn("output_tokens", up)
            self.assertIn("cache_read_tokens", up)
            self.assertIn("cache_write_tokens", up)
            self.assertIn("total_tokens", up)
            self.assertIn("estimated_cost_usd", up)

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
        self.assertEqual(result, {"upstreams": []})

    # ─── Period 格式兼容测试 ───

    def test_period_day_formats(self):

        """day 和 24h 格式应等效。"""
        service = self._create_service()
        result_day = service.fetch_summary("day")
        result_24h = service.fetch_summary("24h")
        data_keys = ["request_count", "input_tokens", "output_tokens", "total_tokens",
                     "cache_read_tokens", "cache_write_tokens", "avg_duration_ms"]
        for key in data_keys:
            self.assertEqual(result_day[key], result_24h[key], f"Mismatch on {key}")

    def test_period_week_formats(self):
        """week 和 7d 格式应等效。"""
        service = self._create_service()
        result_week = service.fetch_summary("week")
        result_7d = service.fetch_summary("7d")
        data_keys = ["request_count", "input_tokens", "output_tokens", "total_tokens",
                     "cache_read_tokens", "cache_write_tokens", "avg_duration_ms"]
        for key in data_keys:
            self.assertEqual(result_week[key], result_7d[key], f"Mismatch on {key}")

    def test_period_month_formats(self):
        """month 和 30d 格式应等效。"""
        service = self._create_service()
        result_month = service.fetch_summary("month")
        result_30d = service.fetch_summary("30d")
        data_keys = ["request_count", "input_tokens", "output_tokens", "total_tokens",
                     "cache_read_tokens", "cache_write_tokens", "avg_duration_ms"]
        for key in data_keys:
            self.assertEqual(result_month[key], result_30d[key], f"Mismatch on {key}")

class TestCostCalculator(unittest.TestCase):

    """_CostCalculator 测试。"""



    def setUp(self):

        """创建临时目录。"""

        self.tmpdir = tempfile.mkdtemp()

        self.cc_switch_db_path = Path(self.tmpdir) / "cc-switch.db"



    def tearDown(self):

        """清理临时文件。"""

        import shutil



        if os.path.exists(self.tmpdir):

            shutil.rmtree(self.tmpdir)



    def _setup_pricing_db(self):

        """创建 cc-switch.db 并填充 model_pricing 数据。"""

        conn = sqlite3.connect(str(self.cc_switch_db_path))

        conn.execute(

            """

            CREATE TABLE IF NOT EXISTS model_pricing (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                model_id TEXT NOT NULL,

                input_cost_per_million REAL NOT NULL DEFAULT 0,

                output_cost_per_million REAL NOT NULL DEFAULT 0,

                cache_read_cost_per_million REAL NOT NULL DEFAULT 0,

                cache_creation_cost_per_million REAL NOT NULL DEFAULT 0

            )

            """

        )

        conn.execute(

            "INSERT INTO model_pricing (model_id, input_cost_per_million, "

            "output_cost_per_million, cache_read_cost_per_million, cache_creation_cost_per_million) "

            "VALUES (?, ?, ?, ?, ?)",

            ("qwen3.6-plus", 2.5, 10.0, 0.5, 2.0),

        )

        conn.execute(

            "INSERT INTO model_pricing (model_id, input_cost_per_million, "

            "output_cost_per_million, cache_read_cost_per_million, cache_creation_cost_per_million) "

            "VALUES (?, ?, ?, ?, ?)",

            ("claude-sonnet-4", 3.0, 15.0, 0.75, 3.5),

        )

        conn.commit()

        conn.close()



    def _create_calculator(self):

        """创建 _CostCalculator 实例。"""

        from stats_service import _CostCalculator



        return _CostCalculator(self.cc_switch_db_path)



    def test_calculate_known_model(self):

        """已知模型返回正确成本。"""

        self._setup_pricing_db()

        calc = self._create_calculator()



        cost = calc.calculate("qwen3.6-plus", 1000, 2000, 500, 300)



        expected = (

            1000 / 1_000_000 * 2.5

            + 2000 / 1_000_000 * 10.0

            + 500 / 1_000_000 * 0.5

            + 300 / 1_000_000 * 2.0

        )

        self.assertAlmostEqual(cost, expected, places=10)



    def test_calculate_unknown_model(self):

        """未知模型返回 0。"""

        self._setup_pricing_db()

        calc = self._create_calculator()



        cost = calc.calculate("unknown-model", 1000, 2000, 0, 0)

        self.assertEqual(cost, 0)



    def test_calculate_db_not_exists(self):

        """cc-switch.db 不存在返回 0。"""

        calc = self._create_calculator()



        cost = calc.calculate("qwen3.6-plus", 1000, 2000, 0, 0)

        self.assertEqual(cost, 0)



    def test_get_pricing(self):

        """get_pricing 返回正确的定价字典。"""

        self._setup_pricing_db()

        calc = self._create_calculator()



        pricing = calc.get_pricing()



        self.assertIn("qwen3.6-plus", pricing)

        self.assertEqual(pricing["qwen3.6-plus"]["input_cost"], 2.5)

        self.assertEqual(pricing["qwen3.6-plus"]["output_cost"], 10.0)

        self.assertEqual(pricing["qwen3.6-plus"]["cache_read_cost"], 0.5)

        self.assertEqual(pricing["qwen3.6-plus"]["cache_creation_cost"], 2.0)



    def test_get_pricing_cache_ttl(self):

        """定价缓存 TTL 生效。"""
        import time


        self._setup_pricing_db()

        calc = self._create_calculator()



        pricing1 = calc.get_pricing()



        calc._pricing_cache_time = time.time() - 301

        calc._pricing_cache = {}



        pricing2 = calc.get_pricing()

        self.assertEqual(pricing1, pricing2)



    def test_calculate_zero_tokens(self):

        """零 tokens 返回 0。"""

        self._setup_pricing_db()

        calc = self._create_calculator()



        cost = calc.calculate("qwen3.6-plus", 0, 0, 0, 0)

        self.assertEqual(cost, 0)



    def test_calculate_none_tokens(self):

        """None tokens 返回 0。"""

        self._setup_pricing_db()

        calc = self._create_calculator()



        cost = calc.calculate("qwen3.6-plus", None, None, None, None)

        self.assertEqual(cost, 0)





class TestStatsServiceCostCalculation(unittest.TestCase):

    """StatsService.calculate_cost 和 get_pricing 委托测试。"""



    def setUp(self):

        """创建临时目录和空 access_log.db。"""

        self.tmpdir = tempfile.mkdtemp()

        self.access_log_db = Path(self.tmpdir) / "access_log.db"

        self.config_db = Path(self.tmpdir) / "config.db"

        self.state_db = Path(self.tmpdir) / "state.db"

        self.cc_switch_db = Path(self.tmpdir) / "cc-switch.db"



        conn = sqlite3.connect(str(self.access_log_db))

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

            access_log_db_path=str(self.access_log_db),

            config_db_path=str(self.config_db),

            state_db_path=str(self.state_db),

            cc_switch_db_path=str(self.cc_switch_db),

        )



    def _setup_pricing_db(self):

        """创建 cc-switch.db 并填充定价数据。"""

        conn = sqlite3.connect(str(self.cc_switch_db))

        conn.execute(

            "CREATE TABLE IF NOT EXISTS model_pricing ("

            "id INTEGER PRIMARY KEY, model_id TEXT, "

            "input_cost_per_million REAL, output_cost_per_million REAL, "

            "cache_read_cost_per_million REAL, cache_creation_cost_per_million REAL)"

        )

        conn.execute(

            "INSERT INTO model_pricing (model_id, input_cost_per_million, "

            "output_cost_per_million, cache_read_cost_per_million, cache_creation_cost_per_million) "

            "VALUES (?, ?, ?, ?, ?)",

            ("qwen3.6-plus", 2.5, 10.0, 0.5, 2.0),

        )

        conn.commit()

        conn.close()



    def test_service_calculate_cost_known_model(self):

        """StatsService.calculate_cost 委托正确。"""

        self._setup_pricing_db()

        service = self._create_service()



        cost = service.calculate_cost("qwen3.6-plus", 1000, 2000, 500, 300)

        expected = (

            1000 / 1_000_000 * 2.5

            + 2000 / 1_000_000 * 10.0

            + 500 / 1_000_000 * 0.5

            + 300 / 1_000_000 * 2.0

        )

        self.assertAlmostEqual(cost, expected, places=10)



    def test_service_calculate_cost_unknown_model(self):

        """未知模型返回 0。"""

        self._setup_pricing_db()

        service = self._create_service()



        cost = service.calculate_cost("unknown-model", 1000, 2000, 0, 0)

        self.assertEqual(cost, 0)



    def test_service_get_pricing(self):

        """StatsService.get_pricing 委托正确。"""

        self._setup_pricing_db()

        service = self._create_service()



        pricing = service.get_pricing()

        self.assertIn("qwen3.6-plus", pricing)

        self.assertEqual(pricing["qwen3.6-plus"]["input_cost"], 2.5)



    def test_service_calculate_cost_no_db(self):

        """cc-switch.db 不存在时返回 0。"""

        service = self._create_service()



        cost = service.calculate_cost("qwen3.6-plus", 1000, 2000, 0, 0)

        self.assertEqual(cost, 0)


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
            (d["model"], d["started_at"], d["input_tokens"], d["output_tokens"],
             d["cache_read_tokens"], d["cache_write_tokens"], d["message_count"]),
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
        self._insert_session(model="claude-sonnet-4", started_at=now_ts - 10 * 86400, input_tokens=999)
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
        self._insert_session(model="recent", started_at=now_ts - 3600,
                             input_tokens=100, output_tokens=50,
                             cache_read_tokens=0, cache_write_tokens=0)
        self._insert_session(model="old", started_at=now_ts - 8 * 86400,
                             input_tokens=200, output_tokens=100,
                             cache_read_tokens=0, cache_write_tokens=0)
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
            _SessionDao._normalize_model_name("qwen3.6-plus [hermes]"),
            "qwen3.6-plus"
        )

    def test_normalize_model_name_no_bracket(self):
        """无括号的模型名保持不变。"""
        from stats_service import _SessionDao
        self.assertEqual(
            _SessionDao._normalize_model_name("qwen3.6-plus"),
            "qwen3.6-plus"
        )

    def test_normalize_model_name_none(self):
        """None 输入返回 None。"""
        from stats_service import _SessionDao
        self.assertIsNone(_SessionDao._normalize_model_name(None))

    def test_session_record_has_normalized_target_model(self):
        """session 记录的 target_model 已去除 [xxx] 后缀。"""
        self._insert_session(model="qwen3.6-plus [hermes]", input_tokens=50, output_tokens=100)
        dao = self._create_dao()
        result = dao.query_sessions("day")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["model"], "qwen3.6-plus [hermes]")
        self.assertEqual(result[0]["target_model"], "qwen3.6-plus")

    # ─── aggregate_by_model 测试 ───

    def test_aggregate_by_model_basic(self):
        """按模型聚合正确。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._insert_session(model="qwen3.6-plus", input_tokens=100, output_tokens=200,
                             started_at=now)
        self._insert_session(model="qwen3.6-plus", input_tokens=50, output_tokens=100,
                             started_at=now)
        self._insert_session(model="claude-sonnet-4", input_tokens=300, output_tokens=500,
                             started_at=now)
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
        self._insert_session(model="qwen3.6-plus", input_tokens=100, output_tokens=200,
                             started_at=now)
        self._insert_session(model="qwen3.6-plus [coder]", input_tokens=50, output_tokens=100,
                             started_at=now)
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
        self._insert_session(model="small-model", input_tokens=10, output_tokens=20,
                             started_at=now)
        self._insert_session(model="big-model", input_tokens=1000, output_tokens=2000,
                             started_at=now)
        dao = self._create_dao()
        result = dao.aggregate_by_model("day")

        self.assertEqual(result[0]["model"], "big-model")
        self.assertEqual(result[1]["model"], "small-model")

    # ─── aggregate_summary 测试 ───

    def test_aggregate_summary_basic(self):
        import time as _time
        now_ts = _time.time()
        self._insert_session(model="model-a", started_at=now_ts - 3600,
                             input_tokens=100, output_tokens=50,
                             cache_read_tokens=20, cache_write_tokens=10)
        self._insert_session(model="model-b", started_at=now_ts - 1800,
                             input_tokens=200, output_tokens=100,
                             cache_read_tokens=40, cache_write_tokens=20)
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
        self._insert_session(model="model-a", started_at=now_ts - 3600,
                             input_tokens=100, output_tokens=50,
                             cache_read_tokens=20, cache_write_tokens=10)
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




class TestFetchRequestsMerged(unittest.TestCase):
    """fetch_requests 合并 token_stats + sessions 测试。"""

    def setUp(self):
        """创建临时目录和两个数据库。"""
        self.tmpdir = tempfile.mkdtemp()
        self.access_log_db = Path(self.tmpdir) / "access_log.db"
        self.config_db = Path(self.tmpdir) / "config.db"
        self.state_db = Path(self.tmpdir) / "state.db"
        self.cc_switch_db = Path(self.tmpdir) / "cc-switch.db"

        # 创建 token_stats 表
        conn = sqlite3.connect(str(self.access_log_db))
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
            access_log_db_path=str(self.access_log_db),
            config_db_path=str(self.config_db),
            state_db_path=str(self.state_db),
            cc_switch_db_path=str(self.cc_switch_db),
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
            "status": "completed",
            "created_at": now,
        }
        defaults.update(kwargs)
        d = defaults
        conn = sqlite3.connect(str(self.access_log_db))
        conn.execute(
            "INSERT INTO token_stats (request_id, request_type, model, target_model, "
            "request_ts, duration_ms, input_tokens, output_tokens, cached_read_tokens, "
            "cached_write_tokens, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (d["request_id"], d["request_type"], d["model"], d["target_model"],
             d["request_ts"], d["duration_ms"], d["input_tokens"], d["output_tokens"],
             d["cached_read_tokens"], d["cached_write_tokens"], d["status"], d["created_at"]),
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
            (d["model"], d["started_at"], d["input_tokens"], d["output_tokens"],
             d["cache_read_tokens"], d["cache_write_tokens"]),
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
        self._insert_token_stat(request_id="proxy-1", target_model="qwen3.6-plus", request_ts=ts)
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
        self._insert_token_stat(request_id="proxy-qwen", target_model="qwen3.6-plus", request_ts=now)
        self._insert_token_stat(request_id="proxy-claude", target_model="claude-sonnet-4", request_ts=now)
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
        self._insert_token_stat(request_id="proxy-1", request_type="chat", request_ts=now)
        self._insert_session(model="qwen3.6-plus", started_at=now)

        service = self._create_service()
        result = service.fetch_requests("day", request_type="session")

        # 只应返回 session 记录
        self.assertEqual(len(result["requests"]), 1)
        self.assertEqual(result["requests"][0]["_source"], "session")

    def test_merged_filter_by_request_type_chat(self):
        """按 request_type='chat' 筛选只返回 proxy。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._insert_token_stat(request_id="proxy-1", request_type="chat", request_ts=now)
        self._insert_session(model="qwen3.6-plus", started_at=now)

        service = self._create_service()
        result = service.fetch_requests("day", request_type="chat")

        self.assertEqual(len(result["requests"]), 1)
        self.assertEqual(result["requests"][0]["_source"], "proxy")

    def test_merged_cost_calculation(self):
        """每条记录都有 estimated_cost_usd。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._insert_token_stat(request_id="proxy-1", input_tokens=100, output_tokens=200)
        self._insert_session(model="qwen3.6-plus", input_tokens=100, output_tokens=200)

        service = self._create_service()
        result = service.fetch_requests("day")

        for r in result["requests"]:
            self.assertIn("estimated_cost_usd", r)
            self.assertIsInstance(r["estimated_cost_usd"], (int, float))



if __name__ == "__main__":
    unittest.main()



class TestFetchByModelRequestsMerged(unittest.TestCase):
    """fetch_by_model_requests 合并 token_stats + sessions 测试。"""

    def setUp(self):
        """创建临时目录和两个数据库。"""
        self.tmpdir = tempfile.mkdtemp()
        self.access_log_db = Path(self.tmpdir) / "access_log.db"
        self.config_db = Path(self.tmpdir) / "config.db"
        self.state_db = Path(self.tmpdir) / "state.db"
        self.cc_switch_db = Path(self.tmpdir) / "cc-switch.db"

        # 创建 token_stats 表
        conn = sqlite3.connect(str(self.access_log_db))
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
            access_log_db_path=str(self.access_log_db),
            config_db_path=str(self.config_db),
            state_db_path=str(self.state_db),
            cc_switch_db_path=str(self.cc_switch_db),
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
            "status": "completed",
            "created_at": now,
        }
        defaults.update(kwargs)
        d = defaults
        conn = sqlite3.connect(str(self.access_log_db))
        conn.execute(
            "INSERT INTO token_stats (request_id, request_type, model, target_model, "
            "request_ts, duration_ms, input_tokens, output_tokens, cached_read_tokens, "
            "cached_write_tokens, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (d["request_id"], d["request_type"], d["model"], d["target_model"],
             d["request_ts"], d["duration_ms"], d["input_tokens"], d["output_tokens"],
             d["cached_read_tokens"], d["cached_write_tokens"], d["status"], d["created_at"]),
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
            (d["model"], d["started_at"], d["input_tokens"], d["output_tokens"],
             d["cache_read_tokens"], d["cache_write_tokens"]),
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
        self._insert_token_stat(request_id="proxy-1", target_model="qwen3.6-plus", request_ts=ts)
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
        self._insert_token_stat(request_id="proxy-old", target_model="qwen3.6-plus", request_ts=old_ts)
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
            self._insert_token_stat(request_id=f"req-{i}", target_model="qwen3.6-plus", request_ts=now)

        service = self._create_service()
        result = service.fetch_by_model_requests("qwen3.6-plus", "day", limit=10, offset=0)
        self.assertEqual(len(result["requests"]), 10)
        self.assertEqual(result["total"], 15)
        self.assertEqual(result["limit"], 10)

    def test_pagination_offset(self):
        """分页 offset 正确跳过前面的记录。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for i in range(10):
            self._insert_token_stat(request_id=f"req-{i}", target_model="qwen3.6-plus", request_ts=now)

        service = self._create_service()
        result = service.fetch_by_model_requests("qwen3.6-plus", "day", limit=5, offset=5)
        self.assertEqual(len(result["requests"]), 5)
        self.assertEqual(result["offset"], 5)

    def test_model_filter_exact_match(self):
        """按 model 精确筛选。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._insert_token_stat(request_id="proxy-qwen", target_model="qwen3.6-plus", request_ts=now)
        self._insert_token_stat(request_id="proxy-claude", target_model="claude-sonnet-4", request_ts=now)
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
        """每条记录都有 estimated_cost_usd。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._insert_token_stat(request_id="proxy-1", target_model="qwen3.6-plus",
                                input_tokens=100, output_tokens=200)
        self._insert_session(model="qwen3.6-plus", input_tokens=100, output_tokens=200)

        service = self._create_service()
        result = service.fetch_by_model_requests("qwen3.6-plus", "day")

        for r in result["requests"]:
            self.assertIn("estimated_cost_usd", r)
            self.assertIsInstance(r["estimated_cost_usd"], (int, float))





class TestFetchByUpstreamMerged(unittest.TestCase):
    """fetch_by_upstream 合并 token_stats + sessions 测试。"""

    def setUp(self):
        """创建临时目录和两个数据库。"""
        self.tmpdir = tempfile.mkdtemp()
        self.access_log_db = Path(self.tmpdir) / "access_log.db"
        self.config_db = Path(self.tmpdir) / "config.db"
        self.state_db = Path(self.tmpdir) / "state.db"
        self.cc_switch_db = Path(self.tmpdir) / "cc-switch.db"

        # 创建 token_stats 表
        conn = sqlite3.connect(str(self.access_log_db))
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
            access_log_db_path=str(self.access_log_db),
            config_db_path=str(self.config_db),
            state_db_path=str(self.state_db),
            cc_switch_db_path=str(self.cc_switch_db),
        )

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
            "status": "completed",
            "created_at": now,
        }
        defaults.update(kwargs)
        d = defaults
        conn = sqlite3.connect(str(self.access_log_db))
        conn.execute(
            "INSERT INTO token_stats (request_id, request_type, model, target_model, "
            "request_ts, duration_ms, input_tokens, output_tokens, cached_read_tokens, "
            "cached_write_tokens, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (d["request_id"], d["request_type"], d["model"], d["target_model"],
             d["request_ts"], d["duration_ms"], d["input_tokens"], d["output_tokens"],
             d["cached_read_tokens"], d["cached_write_tokens"], d["status"], d["created_at"]),
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
            (d["model"], d["started_at"], d["input_tokens"], d["output_tokens"],
             d["cache_read_tokens"], d["cache_write_tokens"]),
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
            request_id="req-1", target_model="qwen3.6-plus",
            request_ts=ts, input_tokens=100, output_tokens=200
        )
        self._insert_token_stat(
            request_id="req-2", target_model="qwen3.6-plus",
            request_ts=ts, input_tokens=50, output_tokens=100
        )
        self._setup_config_db()

        service = self._create_service()
        result = service.fetch_by_upstream("day")

        self.assertEqual(len(result["upstreams"]), 1)
        up = result["upstreams"][0]
        self.assertEqual(up["upstream_id"], "https://api.openai.com")
        self.assertEqual(up["request_count"], 2)
        self.assertEqual(up["input_tokens"], 150)
        self.assertEqual(up["output_tokens"], 300)
        self.assertEqual(up["total_tokens"], 450)

    def test_sessions_only(self):
        """仅有 sessions 数据时正确聚合。"""
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        self._insert_session(model="qwen3.6-plus", started_at=ts, input_tokens=100, output_tokens=200)
        self._insert_session(model="qwen3.6-plus", started_at=ts, input_tokens=50, output_tokens=100)
        self._setup_config_db()

        service = self._create_service()
        result = service.fetch_by_upstream("day")

        self.assertEqual(len(result["upstreams"]), 1)
        up = result["upstreams"][0]
        self.assertEqual(up["upstream_id"], "https://api.openai.com")
        self.assertEqual(up["request_count"], 2)
        self.assertEqual(up["input_tokens"], 150)
        self.assertEqual(up["output_tokens"], 300)

    def test_merged_both_sources_same_upstream(self):
        """token_stats 和 sessions 都指向同 upstream 时累加。"""
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        # token_stats: 100 input, 200 output
        self._insert_token_stat(
            request_id="proxy-1", target_model="qwen3.6-plus",
            request_ts=ts, input_tokens=100, output_tokens=200
        )
        # sessions: 50 input, 100 output
        self._insert_session(
            model="qwen3.6-plus", started_at=ts, input_tokens=50, output_tokens=100
        )
        self._setup_config_db()

        service = self._create_service()
        result = service.fetch_by_upstream("day")

        self.assertEqual(len(result["upstreams"]), 1)
        up = result["upstreams"][0]
        self.assertEqual(up["upstream_id"], "https://api.openai.com")
        self.assertEqual(up["request_count"], 2)  # 1 + 1
        self.assertEqual(up["input_tokens"], 150)  # 100 + 50
        self.assertEqual(up["output_tokens"], 300)  # 200 + 100

    def test_merged_different_upstreams(self):
        """不同 upstream 正确分组。"""
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        # token_stats → openai
        self._insert_token_stat(
            request_id="proxy-1", target_model="qwen3.6-plus",
            request_ts=ts, input_tokens=100, output_tokens=200
        )
        # sessions → anthropic
        self._insert_session(
            model="claude-sonnet-4", started_at=ts, input_tokens=300, output_tokens=500
        )
        self._setup_config_db()

        service = self._create_service()
        result = service.fetch_by_upstream("day")

        self.assertEqual(len(result["upstreams"]), 2)
        upstream_ids = {up["upstream_id"] for up in result["upstreams"]}
        self.assertIn("https://api.openai.com", upstream_ids)
        self.assertIn("https://api.anthropic.com", upstream_ids)

    def test_orphan_sessions_go_to_unknown(self):
        """无法解析到 upstream 的 sessions 归入 __unknown__。"""
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        # orphan session
        self._insert_session(
            model="unknown-orphan-model", started_at=ts, input_tokens=100, output_tokens=200
        )
        self._setup_config_db()

        service = self._create_service()
        result = service.fetch_by_upstream("day")

        unknown = [up for up in result["upstreams"] if up["upstream_id"] == "__unknown__"]
        self.assertEqual(len(unknown), 1)
        self.assertEqual(unknown[0]["request_count"], 1)
        self.assertEqual(unknown[0]["input_tokens"], 100)
        self.assertEqual(unknown[0]["output_tokens"], 200)
        self.assertIsNone(unknown[0]["base_url"])

    def test_orphan_token_stats_go_to_unknown(self):
        """无法解析到 upstream 的 token_stats 也归入 __unknown__。"""
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        self._insert_token_stat(
            request_id="orphan-1", target_model="orphan-model",
            request_ts=ts, input_tokens=50, output_tokens=100
        )
        self._setup_config_db()

        service = self._create_service()
        result = service.fetch_by_upstream("day")

        unknown = [up for up in result["upstreams"] if up["upstream_id"] == "__unknown__"]
        self.assertEqual(len(unknown), 1)
        self.assertEqual(unknown[0]["request_count"], 1)
        self.assertIsNone(unknown[0]["base_url"])

    def test_sorted_by_estimated_cost_usd_desc(self):
        """结果按 estimated_cost_usd 降序排列。"""
        # 创建定价数据，使不同 upstream 有不同的 cost
        conn = sqlite3.connect(str(self.cc_switch_db))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS model_pricing (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id TEXT NOT NULL,
                input_cost_per_million REAL NOT NULL DEFAULT 0,
                output_cost_per_million REAL NOT NULL DEFAULT 0,
                cache_read_cost_per_million REAL NOT NULL DEFAULT 0,
                cache_creation_cost_per_million REAL NOT NULL DEFAULT 0
            )
        """)
        conn.execute(
            "INSERT INTO model_pricing (model_id, input_cost_per_million, "
            "output_cost_per_million, cache_read_cost_per_million, cache_creation_cost_per_million) "
            "VALUES (?, ?, ?, ?, ?)",
            ("qwen3.6-plus", 1.0, 2.0, 0.0, 0.0),
        )
        conn.execute(
            "INSERT INTO model_pricing (model_id, input_cost_per_million, "
            "output_cost_per_million, cache_read_cost_per_million, cache_creation_cost_per_million) "
            "VALUES (?, ?, ?, ?, ?)",
            ("claude-sonnet-4", 10.0, 20.0, 0.0, 0.0),
        )
        conn.commit()
        conn.close()

        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        # openai: small tokens
        self._insert_token_stat(
            request_id="proxy-1", target_model="qwen3.6-plus",
            request_ts=ts, input_tokens=1000, output_tokens=2000
        )
        # anthropic: large tokens
        self._insert_session(
            model="claude-sonnet-4", started_at=ts, input_tokens=1000, output_tokens=2000
        )
        self._setup_config_db()

        service = self._create_service()
        result = service.fetch_by_upstream("day")

        self.assertEqual(len(result["upstreams"]), 2)
        # anthropic should be first (higher cost per token)
        self.assertEqual(result["upstreams"][0]["upstream_id"], "https://api.anthropic.com")
        self.assertGreater(result["upstreams"][0]["estimated_cost_usd"], result["upstreams"][1]["estimated_cost_usd"])
        self.assertEqual(result["upstreams"][1]["upstream_id"], "https://api.openai.com")

    def test_base_url_correct(self):
        """upstream 的 base_url 正确。"""
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        self._insert_token_stat(
            request_id="proxy-1", target_model="qwen3.6-plus",
            request_ts=ts, input_tokens=100, output_tokens=200
        )
        self._setup_config_db()

        service = self._create_service()
        result = service.fetch_by_upstream("day")

        openai = [up for up in result["upstreams"] if up["upstream_id"] == "https://api.openai.com"]
        self.assertEqual(len(openai), 1)
        self.assertEqual(openai[0]["base_url"], "https://api.openai.com")

    def test_unknown_base_url_is_none(self):
        """__unknown__ 的 base_url 为 None。"""
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        self._insert_session(
            model="orphan-model", started_at=ts, input_tokens=100, output_tokens=200
        )
        self._setup_config_db()

        service = self._create_service()
        result = service.fetch_by_upstream("day")

        unknown = [up for up in result["upstreams"] if up["upstream_id"] == "__unknown__"]
        self.assertEqual(len(unknown), 1)
        self.assertIsNone(unknown[0]["base_url"])

    def test_config_db_not_exists_returns_empty(self):
        """config.db 不存在时返回空 upstreams。"""
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        self._insert_token_stat(
            request_id="req-1", target_model="qwen3.6-plus",
            request_ts=ts, input_tokens=100, output_tokens=200
        )
        # 不创建 config.db

        service = self._create_service()
        result = service.fetch_by_upstream("day")

        self.assertEqual(result, {"upstreams": []})

    def test_cost_calculation_non_zero(self):
        """有定价数据时成本计算非零。"""
        # 创建定价数据库
        conn = sqlite3.connect(str(self.cc_switch_db))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS model_pricing (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id TEXT NOT NULL,
                input_cost_per_million REAL NOT NULL DEFAULT 0,
                output_cost_per_million REAL NOT NULL DEFAULT 0,
                cache_read_cost_per_million REAL NOT NULL DEFAULT 0,
                cache_creation_cost_per_million REAL NOT NULL DEFAULT 0
            )
        """)
        conn.execute(
            "INSERT INTO model_pricing (model_id, input_cost_per_million, "
            "output_cost_per_million, cache_read_cost_per_million, cache_creation_cost_per_million) "
            "VALUES (?, ?, ?, ?, ?)",
            ("qwen3.6-plus", 2.5, 10.0, 0.5, 2.0),
        )
        conn.commit()
        conn.close()

        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        self._insert_token_stat(
            request_id="req-1", target_model="qwen3.6-plus",
            request_ts=ts, input_tokens=1000, output_tokens=2000
        )
        self._setup_config_db()

        service = self._create_service()
        result = service.fetch_by_upstream("day")

        self.assertEqual(len(result["upstreams"]), 1)
        up = result["upstreams"][0]
        self.assertGreater(up["estimated_cost_usd"], 0)
        # 验证计算: (1000/1M * 2.5) + (2000/1M * 10.0) = 0.0025 + 0.02 = 0.0225
        expected = 1000 / 1_000_000 * 2.5 + 2000 / 1_000_000 * 10.0
        self.assertAlmostEqual(up["estimated_cost_usd"], expected, places=6)

    def test_output_uses_cache_not_cached_prefix(self):
        """fetch_by_upstream 输出字段名统一为 cache_*。"""
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        self._insert_token_stat(
            request_id="req-1", target_model="qwen3.6-plus",
            request_ts=ts, input_tokens=100, output_tokens=200
        )
        self._setup_config_db()
        result = self._create_service().fetch_by_upstream("week")
        for u in result["upstreams"]:
            self.assertIn("cache_read_tokens", u)
            self.assertNotIn("cached_read_tokens", u)
            self.assertIn("cache_write_tokens", u)
            self.assertNotIn("cached_write_tokens", u)

    def test_session_and_token_orphan_merged_to_unknown(self):
        """orphan sessions + orphan token_stats 都归入 __unknown__ 并合并。"""
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        # orphan token_stats
        self._insert_token_stat(
            request_id="orphan-proxy", target_model="orphan-model",
            request_ts=ts, input_tokens=50, output_tokens=100
        )
        # orphan session
        self._insert_session(
            model="orphan-model", started_at=ts, input_tokens=30, output_tokens=60
        )
        self._setup_config_db()

        service = self._create_service()
        result = service.fetch_by_upstream("day")

        unknown = [up for up in result["upstreams"] if up["upstream_id"] == "__unknown__"]
        self.assertEqual(len(unknown), 1)
        self.assertEqual(unknown[0]["request_count"], 2)  # 1 + 1
        self.assertEqual(unknown[0]["input_tokens"], 80)  # 50 + 30
        self.assertEqual(unknown[0]["output_tokens"], 160)  # 100 + 60


class TestMerger(unittest.TestCase):
    """_Merger 双源合并测试。"""

    def test_merge_summary_sums_fields(self):
        proxy = {
            "period": "week", "request_count": 10,
            "input_tokens": 100, "output_tokens": 50,
            "cached_read_tokens": 200, "cached_write_tokens": 30,
            "total_tokens": 380, "avg_duration_ms": 150.0,
        }
        session = {
            "period": "week", "request_count": 5,
            "input_tokens": 80, "output_tokens": 40,
            "cached_read_tokens": 160, "cached_write_tokens": 20,
            "total_tokens": 300, "avg_duration_ms": 0,
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
        proxy = {"period": "day", "request_count": 1, "input_tokens": 10,
                 "output_tokens": 5, "cached_read_tokens": 20,
                 "cached_write_tokens": 3, "total_tokens": 38,
                 "avg_duration_ms": 0}
        session = {"period": "day", "request_count": 0, "input_tokens": 0,
                   "output_tokens": 0, "cached_read_tokens": 0,
                   "cached_write_tokens": 0, "total_tokens": 0,
                   "avg_duration_ms": 0}
        result = _Merger.merge_summary(proxy, session)
        self.assertIn("cache_read_tokens", result)
        self.assertNotIn("cached_read_tokens", result)
        self.assertIn("cache_write_tokens", result)
        self.assertNotIn("cached_write_tokens", result)

    def test_merge_summary_empty_session(self):
        from stats_service import _Merger
        proxy = {"period": "week", "request_count": 10, "input_tokens": 100,
                 "output_tokens": 50, "cached_read_tokens": 200,
                 "cached_write_tokens": 30, "total_tokens": 380,
                 "avg_duration_ms": 100.0}
        result = _Merger.merge_summary(proxy, {})
        self.assertEqual(result["request_count"], 10)
        self.assertEqual(result["cache_read_tokens"], 200)

    def test_merge_summary_no_estimated_cost(self):
        from stats_service import _Merger
        proxy = {"period": "week", "request_count": 1, "input_tokens": 100,
                 "output_tokens": 50, "cached_read_tokens": 0,
                 "cached_write_tokens": 0, "total_tokens": 150,
                 "avg_duration_ms": 0}
        result = _Merger.merge_summary(proxy, {})
        self.assertNotIn("estimated_cost_usd", result)

    def test_merge_model_lists_sums_same_model(self):
        from stats_service import _Merger
        proxy = [
            {"model": "claude-3.5-sonnet", "request_count": 5,
             "input_tokens": 100, "output_tokens": 50,
             "cached_read_tokens": 20, "cached_write_tokens": 10,
             "total_tokens": 180, "avg_duration_ms": 200.0},
        ]
        session = [
            {"model": "claude-3.5-sonnet", "request_count": 3,
             "input_tokens": 60, "output_tokens": 30,
             "cached_read_tokens": 15, "cached_write_tokens": 5,
             "total_tokens": 110},
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
            {"model": "model-a", "request_count": 2, "input_tokens": 10,
             "output_tokens": 5, "cached_read_tokens": 0,
             "cached_write_tokens": 0, "total_tokens": 15,
             "avg_duration_ms": 100.0},
        ]
        session = [
            {"model": "model-b", "request_count": 3, "input_tokens": 20,
             "output_tokens": 10, "cached_read_tokens": 0,
             "cached_write_tokens": 0, "total_tokens": 30},
        ]
        result = _Merger.merge_model_lists(proxy, session)
        self.assertEqual(len(result), 2)

    def test_merge_model_lists_normalizes_names(self):
        from stats_service import _Merger
        proxy = [
            {"model": "claude-3.5-sonnet", "request_count": 5,
             "input_tokens": 100, "output_tokens": 50,
             "cached_read_tokens": 20, "cached_write_tokens": 10,
             "total_tokens": 180, "avg_duration_ms": 0},
        ]
        session = [
            {"model": "claude-3.5-sonnet[1m]", "request_count": 3,
             "input_tokens": 60, "output_tokens": 30,
             "cached_read_tokens": 15, "cached_write_tokens": 5,
             "total_tokens": 110},
        ]
        result = _Merger.merge_model_lists(proxy, session)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["request_count"], 8)

    def test_merge_trend_lists_sums_same_time(self):
        from stats_service import _Merger
        proxy = [
            {"time": "2026-05-11", "request_count": 5,
             "input_tokens": 100, "output_tokens": 50,
             "cached_read_tokens": 20, "cached_write_tokens": 10,
             "total_tokens": 180},
        ]
        session = [
            {"time": "2026-05-11", "request_count": 3,
             "input_tokens": 60, "output_tokens": 30,
             "cached_read_tokens": 15, "cached_write_tokens": 5,
             "total_tokens": 110},
        ]
        result = _Merger.merge_trend_lists(proxy, session)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["request_count"], 8)
        self.assertEqual(result[0]["input_tokens"], 160)
        self.assertEqual(result[0]["cache_read_tokens"], 35)

    def test_merge_trend_lists_output_key_is_date(self):
        from stats_service import _Merger
        proxy = [
            {"time": "2026-05-11", "request_count": 5,
             "input_tokens": 100, "output_tokens": 50,
             "cached_read_tokens": 20, "cached_write_tokens": 10,
             "total_tokens": 180},
        ]
        result = _Merger.merge_trend_lists(proxy, [])
        self.assertIn("date", result[0])
        self.assertNotIn("time", result[0])
        self.assertEqual(result[0]["date"], "2026-05-11")

    def test_merge_trend_lists_empty_session(self):
        from stats_service import _Merger
        proxy = [
            {"time": "2026-05-11", "request_count": 5,
             "input_tokens": 100, "output_tokens": 50,
             "cached_read_tokens": 20, "cached_write_tokens": 10,
             "total_tokens": 180},
        ]
        result = _Merger.merge_trend_lists(proxy, [])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["cache_read_tokens"], 20)


class TestFetchSummaryMerged(unittest.TestCase):
    """fetch_summary 双源合并测试。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.access_log_db = Path(self.tmpdir) / "access_log.db"
        self.state_db = Path(self.tmpdir) / "state.db"
        self.config_db = Path(self.tmpdir) / "config.db"
        self.cc_switch_db = Path(self.tmpdir) / "cc-switch.db"
        self._create_access_log_db()
        self._create_state_db()

    def _create_access_log_db(self):
        conn = sqlite3.connect(str(self.access_log_db))
        conn.execute("""CREATE TABLE IF NOT EXISTS token_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT NOT NULL, request_type TEXT NOT NULL,
            model TEXT NOT NULL, target_model TEXT NOT NULL,
            request_ts TEXT NOT NULL, duration_ms INTEGER,
            input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
            cached_read_tokens INTEGER DEFAULT 0, cached_write_tokens INTEGER DEFAULT 0,
            status TEXT DEFAULT 'completed', created_at TEXT NOT NULL)""")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("INSERT INTO token_stats VALUES (1,'r1','chat','m1','m1',?,100,100,50,20,10,'completed',?)",
                     (now, now))
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
        conn.execute("INSERT INTO sessions VALUES (1,'m1',?,80,40,15,5)", (_time.time(),))
        conn.commit()
        conn.close()

    def tearDown(self):
        import shutil
        if os.path.exists(self.tmpdir):
            shutil.rmtree(self.tmpdir)

    def _create_service(self):
        from stats_service import StatsService
        return StatsService(
            access_log_db_path=str(self.access_log_db),
            config_db_path=str(self.config_db),
            state_db_path=str(self.state_db),
            cc_switch_db_path=str(self.cc_switch_db),
        )

    def test_fetch_summary_merges_both_sources(self):
        svc = self._create_service()
        result = svc.fetch_summary("week")
        self.assertEqual(result["request_count"], 2)  # 1 proxy + 1 session
        self.assertEqual(result["input_tokens"], 180)  # 100 + 80
        self.assertEqual(result["output_tokens"], 90)   # 50 + 40
        self.assertIn("cache_read_tokens", result)
        self.assertNotIn("cached_read_tokens", result)
        self.assertEqual(result["cache_read_tokens"], 35)  # 20 + 15
        self.assertEqual(result["cache_write_tokens"], 15)  # 10 + 5
        self.assertIn("estimated_cost_usd", result)

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
        self.assertIn("estimated_cost_usd", result["week"])

