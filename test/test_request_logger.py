"""Request Logger 单元测试。"""

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from proxy.request_logger import RequestLogger, init_logger, get_logger, _generate_request_id


def _query_debug_log(db_path, request_id=None):
    """查询 debug_log 记录。"""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    if request_id:
        rows = conn.execute(
            "SELECT * FROM debug_log WHERE request_id = ? ORDER BY id", (request_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM debug_log ORDER BY id").fetchall()
    conn.close()
    return rows


def _query_token_stats(db_path, request_id=None):
    """查询 token_stats 记录。"""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    if request_id:
        rows = conn.execute(
            "SELECT * FROM token_stats WHERE request_id = ?", (request_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM token_stats ORDER BY id").fetchall()
    conn.close()
    return rows


class TestDBInitialization(unittest.TestCase):
    """DB 初始化 + data/ 目录自动创建 + 表结构验证。"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test_access_log.db"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_creates_db_file(self):
        """初始化后 DB 文件存在。"""
        logger = RequestLogger(self.db_path)
        self.assertTrue(self.db_path.exists())

    def test_creates_data_directory(self):
        """data/ 目录不存在时自动创建。"""
        nested_dir = Path(self.tmpdir.name) / "data" / "subdir"
        nested_path = nested_dir / "test.db"
        logger = RequestLogger(nested_path)
        self.assertTrue(nested_dir.exists())
        self.assertTrue(nested_path.exists())

    def test_debug_log_table_exists(self):
        """debug_log 表存在。"""
        logger = RequestLogger(self.db_path)
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='debug_log'")
        self.assertIsNotNone(cursor.fetchone())
        conn.close()

    def test_token_stats_table_exists(self):
        """token_stats 表存在。"""
        logger = RequestLogger(self.db_path)
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='token_stats'")
        self.assertIsNotNone(cursor.fetchone())
        conn.close()

    def test_debug_log_columns(self):
        """debug_log 表结构正确。"""
        logger = RequestLogger(self.db_path)
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.execute("PRAGMA table_info(debug_log)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        conn.close()

        expected = {
            "id": "INTEGER",
            "request_id": "TEXT",
            "stage": "TEXT",
            "model": "TEXT",
            "target_model": "TEXT",
            "status_code": "INTEGER",
            "request_type": "TEXT",
            "request_path": "TEXT",
            "data": "TEXT",
            "headers": "TEXT",
            "session_id": "TEXT",
            "created_at": "TEXT",
        }
        for col, dtype in expected.items():
            self.assertIn(col, columns, f"Missing column: {col}")

    def test_token_stats_columns(self):
        """token_stats 表结构正确。"""
        logger = RequestLogger(self.db_path)
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.execute("PRAGMA table_info(token_stats)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        conn.close()

        expected = {
            "id": "INTEGER",
            "request_id": "TEXT",
            "request_type": "TEXT",
            "model": "TEXT",
            "target_model": "TEXT",
            "request_ts": "TEXT",
            "duration_ms": "INTEGER",
            "input_tokens": "INTEGER",
            "output_tokens": "INTEGER",
            "cached_read_tokens": "INTEGER",
            "cached_write_tokens": "INTEGER",
            "status": "TEXT",
            "created_at": "TEXT",
        }
        for col, dtype in expected.items():
            self.assertIn(col, columns, f"Missing column: {col}")


class TestGenerateRequestId(unittest.TestCase):
    """request_id 生成验证。"""

    def test_returns_string(self):
        rid = _generate_request_id()
        self.assertIsInstance(rid, str)

    def test_length_16(self):
        rid = _generate_request_id()
        self.assertEqual(len(rid), 16)

    def test_hex_only(self):
        rid = _generate_request_id()
        self.assertTrue(all(c in "0123456789abcdef" for c in rid))

    def test_uniqueness(self):
        ids = {_generate_request_id() for _ in range(100)}
        self.assertEqual(len(ids), 100)



class TestGlobalLogger(unittest.TestCase):
    """全局单例 init_logger / get_logger。"""

    def setUp(self):
        from proxy import request_logger
        self._prev_logger = request_logger._logger
        request_logger._logger = None

    def tearDown(self):
        from proxy import request_logger
        request_logger._logger = self._prev_logger

    def test_init_logger_returns_instance(self):
        tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(tmpdir.name) / "test.db"
        logger = init_logger(db_path)
        self.assertIsInstance(logger, RequestLogger)
        tmpdir.cleanup()

    def test_get_logger_after_init(self):
        tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(tmpdir.name) / "test.db"
        init_logger(db_path)
        self.assertIsNotNone(get_logger())
        tmpdir.cleanup()

    def test_get_logger_before_init_returns_none(self):
        from proxy import request_logger
        request_logger._logger = None
        self.assertIsNone(get_logger())


class TestLogRawRequest(unittest.TestCase):
    """log_raw_request 写入验证。"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        self.logger = RequestLogger(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_log_dict_body(self):
        rid = _generate_request_id()
        self.logger.log_raw_request(rid, "gpt-4o", "qwen3.6-plus", {"model": "gpt-4o", "input": []})
        rows = _query_debug_log(self.db_path, rid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stage"], "raw_request")
        self.assertEqual(rows[0]["model"], "gpt-4o")
        self.assertEqual(rows[0]["target_model"], "qwen3.6-plus")
        data = json.loads(rows[0]["data"])
        self.assertEqual(data["model"], "gpt-4o")

    def test_log_string_body(self):
        rid = _generate_request_id()
        self.logger.log_raw_request(rid, "*", "unknown", "raw bytes")
        rows = _query_debug_log(self.db_path, rid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stage"], "raw_request")
        self.assertEqual(rows[0]["data"], "raw bytes")

    def test_log_raw_request_with_headers(self):
        """log_raw_request 带 headers 参数时正确写入。"""
        rid = _generate_request_id()
        headers = {"Content-Type": "application/json", "Authorization": "Bearer sk-test"}
        self.logger.log_raw_request(rid, "gpt-4o", "qwen3.6-plus", {"model": "gpt-4o"},
                                    headers=headers)
        rows = _query_debug_log(self.db_path, rid)
        self.assertEqual(len(rows), 1)
        saved_headers = json.loads(rows[0]["headers"])
        self.assertEqual(saved_headers["Content-Type"], "application/json")
        self.assertEqual(saved_headers["Authorization"], "Bearer sk-test")

    def test_log_raw_request_without_headers(self):
        """log_raw_request 不带 headers 时，headers 列为 NULL。"""
        rid = _generate_request_id()
        self.logger.log_raw_request(rid, "gpt-4o", "qwen3.6-plus", {"model": "gpt-4o"})
        rows = _query_debug_log(self.db_path, rid)
        self.assertIsNone(rows[0]["headers"])


class TestLogConvertedRequest(unittest.TestCase):
    """log_converted_request 写入验证。"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        self.logger = RequestLogger(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_log_converted_request(self):
        rid = _generate_request_id()
        body = {"model": "qwen3.6-plus", "messages": [{"role": "user", "content": "hi"}]}
        self.logger.log_converted_request(rid, "gpt-4o", "qwen3.6-plus", body)
        rows = _query_debug_log(self.db_path, rid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stage"], "converted_request")
        self.assertEqual(rows[0]["model"], "gpt-4o")
        self.assertEqual(rows[0]["target_model"], "qwen3.6-plus")
        data = json.loads(rows[0]["data"])
        self.assertEqual(data["model"], "qwen3.6-plus")

    def test_log_converted_request_with_headers(self):
        """log_converted_request 带 headers 参数时正确写入。"""
        rid = _generate_request_id()
        headers = {"Content-Type": "application/json", "Authorization": "Bearer sk-abc"}
        self.logger.log_converted_request(rid, "gpt-4o", "qwen3.6-plus",
                                          {"model": "qwen3.6-plus"}, headers=headers)
        rows = _query_debug_log(self.db_path, rid)
        saved_headers = json.loads(rows[0]["headers"])
        self.assertEqual(saved_headers["Authorization"], "Bearer sk-abc")

    def test_log_converted_request_without_headers(self):
        """log_converted_request 不带 headers 时，headers 列为 NULL。"""
        rid = _generate_request_id()
        self.logger.log_converted_request(rid, "gpt-4o", "qwen3.6-plus", {"model": "qwen3.6-plus"})
        rows = _query_debug_log(self.db_path, rid)
        self.assertIsNone(rows[0]["headers"])


class TestLogUpstreamResponse(unittest.TestCase):
    """log_upstream_response 写入验证。"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        self.logger = RequestLogger(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_log_dict_response(self):
        rid = _generate_request_id()
        body = {"choices": [{"message": {"content": "hello"}}]}
        self.logger.log_upstream_response(rid, 200, body, 350)
        rows = _query_debug_log(self.db_path, rid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stage"], "upstream_response")
        self.assertEqual(rows[0]["status_code"], 200)
        data = json.loads(rows[0]["data"])
        self.assertEqual(data["choices"][0]["message"]["content"], "hello")

    def test_log_string_response(self):
        rid = _generate_request_id()
        self.logger.log_upstream_response(rid, 500, "Internal Server Error", 120)
        rows = _query_debug_log(self.db_path, rid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stage"], "upstream_response")
        self.assertEqual(rows[0]["status_code"], 500)
        self.assertEqual(rows[0]["data"], "Internal Server Error")

    def test_log_upstream_response_with_model_target(self):
        """带 model/target_model 参数时，应正确写入。"""
        rid = _generate_request_id()
        self.logger.log_upstream_response(rid, 200, "ok", 100, "gpt-4", "up-a")
        rows = _query_debug_log(self.db_path, rid)
        self.assertEqual(rows[0]["model"], "gpt-4")
        self.assertEqual(rows[0]["target_model"], "up-a")

    def test_log_upstream_response_model_defaults_to_none(self):
        """不传 model/target 时，应为 None。"""
        rid = _generate_request_id()
        self.logger.log_upstream_response(rid, 200, "ok", 100)
        rows = _query_debug_log(self.db_path, rid)
        self.assertIsNone(rows[0]["model"])
        self.assertIsNone(rows[0]["target_model"])

    def test_log_upstream_response_with_headers(self):
        """log_upstream_response 带 headers 参数时正确写入。"""
        rid = _generate_request_id()
        headers = {"Content-Type": "application/json", "X-Request-Id": "req-123"}
        self.logger.log_upstream_response(rid, 200, "ok", 100, headers=headers)
        rows = _query_debug_log(self.db_path, rid)
        saved_headers = json.loads(rows[0]["headers"])
        self.assertEqual(saved_headers["X-Request-Id"], "req-123")

    def test_log_upstream_response_without_headers(self):
        """log_upstream_response 不带 headers 时，headers 列为 NULL。"""
        rid = _generate_request_id()
        self.logger.log_upstream_response(rid, 200, "ok", 100)
        rows = _query_debug_log(self.db_path, rid)
        self.assertIsNone(rows[0]["headers"])


class TestLogConvertedResponse(unittest.TestCase):
    """log_converted_response 写入验证。"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        self.logger = RequestLogger(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_log_converted_response(self):
        rid = _generate_request_id()
        body = {"id": "resp-abc123", "output": [{"type": "message"}]}
        self.logger.log_converted_response(rid, "gpt-4o", "qwen3.6-plus", body)
        rows = _query_debug_log(self.db_path, rid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stage"], "converted_response")
        self.assertEqual(rows[0]["model"], "gpt-4o")
        self.assertEqual(rows[0]["target_model"], "qwen3.6-plus")
        data = json.loads(rows[0]["data"])
        self.assertEqual(data["id"], "resp-abc123")

    def test_log_converted_response_with_headers(self):
        """log_converted_response 带 headers 参数时正确写入。"""
        rid = _generate_request_id()
        headers = {"Content-Type": "text/event-stream", "Cache-Control": "no-cache"}
        self.logger.log_converted_response(rid, "gpt-4o", "qwen3.6-plus",
                                           {"id": "resp-abc"}, headers=headers)
        rows = _query_debug_log(self.db_path, rid)
        saved_headers = json.loads(rows[0]["headers"])
        self.assertEqual(saved_headers["Content-Type"], "text/event-stream")

    def test_log_converted_response_without_headers(self):
        """log_converted_response 不带 headers 时，headers 列为 NULL。"""
        rid = _generate_request_id()
        self.logger.log_converted_response(rid, "gpt-4o", "qwen3.6-plus", {"id": "resp-abc"})
        rows = _query_debug_log(self.db_path, rid)
        self.assertIsNone(rows[0]["headers"])


class TestLogTokenStats(unittest.TestCase):
    """log_token_stats 写入验证。"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        self.logger = RequestLogger(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_log_token_stats(self):
        rid = _generate_request_id()
        self.logger.log_token_stats(
            request_id=rid,
            request_type="codex",
            model="gpt-4o",
            target_model="qwen3.6-plus",
            request_ts="2026-04-25 15:30:00",
            duration_ms=350,
            input_tokens=100,
            output_tokens=50,
            cached_read=20,
            cached_write=10,
            status="completed",
        )
        rows = _query_token_stats(self.db_path, rid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["request_type"], "codex")
        self.assertEqual(rows[0]["model"], "gpt-4o")
        self.assertEqual(rows[0]["target_model"], "qwen3.6-plus")
        self.assertEqual(rows[0]["request_ts"], "2026-04-25 15:30:00")
        self.assertEqual(rows[0]["duration_ms"], 350)
        self.assertEqual(rows[0]["input_tokens"], 100)
        self.assertEqual(rows[0]["output_tokens"], 50)
        self.assertEqual(rows[0]["cached_read_tokens"], 20)
        self.assertEqual(rows[0]["cached_write_tokens"], 10)
        self.assertEqual(rows[0]["status"], "completed")

    def test_default_token_values(self):
        """验证默认值为 0。"""
        rid = _generate_request_id()
        self.logger.log_token_stats(
            request_id=rid,
            request_type="unknown",
            model="*",
            target_model="qwen3.6-plus",
            request_ts="2026-04-25 15:30:00",
            duration_ms=0,
            input_tokens=0,
            output_tokens=0,
            cached_read=0,
            cached_write=0,
            status="incomplete",
        )
        rows = _query_token_stats(self.db_path, rid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["input_tokens"], 0)
        self.assertEqual(rows[0]["status"], "incomplete")


class TestLogWriteFailure(unittest.TestCase):
    """日志写入失败不抛异常验证。"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        self.logger = RequestLogger(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_log_raw_request_db_failure(self):
        """mock _get_conn 抛异常时，log_raw_request 不应向外传播异常。"""
        def failing_conn():
            raise sqlite3.OperationalError("simulated DB error")

        self.logger._get_conn = failing_conn
        # 不应抛出异常
        self.logger.log_raw_request("fake-id", "gpt-4o", "qwen", {"key": "value"})


class TestCleanupExpired(unittest.TestCase):
    """_cleanup_expired 清理策略验证。"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"

    def tearDown(self):
        self.tmpdir.cleanup()

    def _insert_old_debug_log(self, request_id: str, created_at: str):
        """手动插入旧记录。"""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO debug_log (request_id, stage, model, target_model, data, created_at) "
            "VALUES (?, 'raw_request', 'gpt-4o', 'qwen', '{\"test\":1}', ?)",
            (request_id, created_at),
        )
        conn.commit()
        conn.close()

    def _insert_old_token_stats(self, request_id: str, created_at: str):
        """手动插入旧 token_stats 记录。"""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO token_stats "
            "(request_id, request_type, model, target_model, request_ts, duration_ms, "
            "input_tokens, output_tokens, cached_read_tokens, cached_write_tokens, status, created_at) "
            "VALUES (?, 'codex', 'gpt-4o', 'qwen', ?, 0, 0, 0, 0, 0, 'completed', ?)",
            (request_id, created_at, created_at),
        )
        conn.commit()
        conn.close()

    def _days_ago(self, days: int) -> str:
        """返回 N 天前的日期字符串。"""
        from datetime import timedelta
        past = datetime.now() - timedelta(days=days)
        return past.strftime("%Y-%m-%d %H:%M:%S")

    def test_cleanup_removes_old_debug_log(self):
        """超过 retention_days 的 debug_log 记录应被清理。"""
        logger = RequestLogger(self.db_path, debug_retention_days=7)

        # 插入 10 天前的记录
        self._insert_old_debug_log("old-request", self._days_ago(10))

        logger._cleanup_expired()

        rows = _query_debug_log(self.db_path, "old-request")
        self.assertEqual(len(rows), 0)

    def test_cleanup_keeps_recent_debug_log(self):
        """retention_days 内的 debug_log 记录应保留。"""
        logger = RequestLogger(self.db_path, debug_retention_days=7)

        self._insert_old_debug_log("recent-request", self._days_ago(3))

        logger._cleanup_expired()

        rows = _query_debug_log(self.db_path, "recent-request")
        self.assertEqual(len(rows), 1)

    def test_cleanup_does_not_affect_token_stats(self):
        """token_stats 记录不受清理影响。"""
        logger = RequestLogger(self.db_path, debug_retention_days=7)

        old_date = self._days_ago(30)
        self._insert_old_token_stats("old-stats-request", old_date)

        stats_before = _query_token_stats(self.db_path)
        self.assertEqual(len(stats_before), 1)

        logger._cleanup_expired()

        stats_after = _query_token_stats(self.db_path)
        self.assertEqual(len(stats_after), 1)

    def test_cleanup_with_custom_retention_days(self):
        """自定义 retention_days 应生效。"""
        short_retention = RequestLogger(self.db_path, debug_retention_days=1)

        self._insert_old_debug_log("old-custom", self._days_ago(3))

        short_retention._cleanup_expired()

        rows = _query_debug_log(self.db_path, "old-custom")
        self.assertEqual(len(rows), 0)


if __name__ == "__main__":
    unittest.main()
