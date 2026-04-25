"""Request Logger 单元测试。"""

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from request_logger import RequestLogger, init_logger, get_logger, _generate_request_id


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
            "data": "TEXT",
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
            "agent": "TEXT",
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


class TestExtractAgent(unittest.TestCase):
    """User-Agent 提取验证。"""

    def test_codex_detected(self):
        from request_logger import _extract_agent
        self.assertEqual(_extract_agent("codex-cli/1.0"), "codex")

    def test_codex_case_insensitive(self):
        from request_logger import _extract_agent
        self.assertEqual(_extract_agent("CODEX/2.0"), "codex")

    def test_unknown_agent(self):
        from request_logger import _extract_agent
        self.assertEqual(_extract_agent("curl/7.0"), "unknown")

    def test_empty_user_agent(self):
        from request_logger import _extract_agent
        self.assertEqual(_extract_agent(""), "unknown")


class TestGlobalLogger(unittest.TestCase):
    """全局单例 init_logger / get_logger。"""

    def setUp(self):
        import request_logger
        self._prev_logger = request_logger._logger
        request_logger._logger = None

    def tearDown(self):
        import request_logger
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
        import request_logger
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
            agent="codex",
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
        self.assertEqual(rows[0]["agent"], "codex")
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
            agent="unknown",
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

    def test_log_raw_request_no_exception(self):
        """即使写入函数内部出错也不应抛出异常。"""
        rid = _generate_request_id()
        # 正常写入后验证记录存在
        self.logger.log_raw_request(rid, "gpt-4o", "qwen", {"key": "value"})
        rows = _query_debug_log(self.db_path, rid)
        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
