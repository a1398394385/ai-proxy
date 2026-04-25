"""Request Logger 单元测试。"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from request_logger import RequestLogger, init_logger, get_logger, _generate_request_id


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


if __name__ == "__main__":
    unittest.main()
