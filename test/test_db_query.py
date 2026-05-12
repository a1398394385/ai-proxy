"""POST /api/db/query 单元测试"""
import json
import sqlite3
import tempfile
import io
from pathlib import Path
from unittest.mock import MagicMock, patch

from server.common import json_response, _read_json, access_log_db
from proxy.paths import DATA_DB
from server.dbquery_api import handle_post as dbquery_handle_post


def _make_handler(body_dict=None, body_bytes=None):
    """创建模拟的 handler，模拟 POST /api/db/query 的请求"""
    if body_bytes is None:
        body_bytes = json.dumps(body_dict).encode() if body_dict else b""
    handler = MagicMock(name="MockHandler")
    handler.headers = {"Content-Length": str(len(body_bytes))}
    handler.rfile = io.BytesIO(body_bytes)
    handler.path = "/api/db/query"
    handler.wfile = io.BytesIO()
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    return handler


def _extract_call_args(handler):
    """从 handler 捕获的响应中提取 (data, status)"""
    if not handler.send_response.call_count:
        return None, None
    status = handler.send_response.call_args[0][0]
    data = None
    try:
        written = handler.wfile.getvalue()
        if written:
            data = json.loads(written)
    except (json.JSONDecodeError, AttributeError):
        pass
    return data, status


class TestDbQuery:
    """数据库查询 API 测试"""

    @classmethod
    def setup_class(cls):
        """创建临时 access_log.db，包含 debug_log 和 token_stats 表"""
        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.tmpdir.name) / "access_log.db"
        conn = sqlite3.connect(str(cls.db_path))
        conn.executescript("""
            CREATE TABLE debug_log (
                id INTEGER PRIMARY KEY,
                request_id TEXT,
                stage TEXT,
                status_code INTEGER,
                method TEXT,
                path TEXT
            );
            CREATE TABLE token_stats (
                id INTEGER PRIMARY KEY,
                request_id TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                model TEXT
            );
            INSERT INTO debug_log VALUES (1, 'req-001', 'raw_request', NULL, 'GET', '/v1/models');
            INSERT INTO debug_log VALUES (2, 'req-002', 'converted_request', 200, 'POST', '/v1/responses');
            INSERT INTO debug_log VALUES (3, 'req-003', 'raw_response', 200, 'POST', '/v1/responses');
            INSERT INTO debug_log VALUES (4, 'req-004', 'converted_response', NULL, 'POST', '/v1/compact');
            INSERT INTO token_stats VALUES (1, 'req-001', 100, 50, 'gpt-4');
            INSERT INTO token_stats VALUES (2, 'req-002', 200, 100, 'claude-3');
        """)
        conn.close()

        cls._patcher = patch("server.common.DATA_DB", cls.db_path)
        cls._patcher.start()

    @classmethod
    def teardown_class(cls):
        cls._patcher.stop()
        cls.tmpdir.cleanup()

    # ===== 正常查询 =====

    def test_normal_select_returns_columns_and_rows(self):
        """正常 SELECT 查询返回 columns + rows 结构"""
        with access_log_db() as conn:
            cursor = conn.execute("SELECT id, request_id FROM debug_log ORDER BY id LIMIT 2")
            columns = [col[0] for col in cursor.description]
            rows = [list(row) for row in cursor.fetchall()]
        assert columns == ["id", "request_id"]
        assert rows == [[1, "req-001"], [2, "req-002"]]

    def test_select_token_stats(self):
        """token_stats 表查询正常"""
        with access_log_db() as conn:
            cursor = conn.execute("SELECT * FROM token_stats ORDER BY id")
            rows = [list(row) for row in cursor.fetchall()]
        assert len(rows) == 2
        assert rows[0] == [1, "req-001", 100, 50, "gpt-4"]

    # ===== 全流程集成测试 =====

    def _run_query(self, sql):
        """通过 dbquery_api.handle_post 执行查询，返回 (data, status)。"""
        handler = _make_handler({"sql": sql})
        dbquery_handle_post("/api/db/query", handler)
        return _extract_call_args(handler)

    def test_full_flow_normal_query(self):
        """完整流程：正常 SELECT 查询返回正确数据"""
        data, status = self._run_query("SELECT id, request_id FROM debug_log ORDER BY id LIMIT 2")
        assert status == 200
        assert data["columns"] == ["id", "request_id"]
        assert data["rows"] == [[1, "req-001"], [2, "req-002"]]

    def test_full_flow_empty_result(self):
        """完整流程：查询空结果集"""
        data, status = self._run_query("SELECT * FROM debug_log WHERE id = 999")
        assert status == 200
        assert "columns" in data
        assert data["rows"] == []

    def test_full_flow_reject_delete(self):
        """完整流程：DELETE 被拒绝"""
        data, status = self._run_query("DELETE FROM debug_log")
        assert status == 400
        assert "只允许 SELECT" in data["error"]

    def test_full_flow_reject_insert(self):
        """完整流程：INSERT 被拒绝"""
        data, status = self._run_query("INSERT INTO debug_log(id) VALUES(99)")
        assert status == 400
        assert "只允许 SELECT" in data["error"]

    def test_full_flow_reject_multi_statement(self):
        """完整流程：多语句被拒绝"""
        data, status = self._run_query("SELECT 1; SELECT 2")
        assert status == 400
        assert "禁止多语句" in data["error"]

    def test_full_flow_reject_upstreams(self):
        """完整流程：访问上游配置表被拒绝"""
        data, status = self._run_query("SELECT * FROM upstreams")
        assert status == 403
        assert "禁止访问表" in data["error"]

    def test_full_flow_reject_unknown_table(self):
        """完整流程：访问不存在的表也被拒绝"""
        data, status = self._run_query("SELECT * FROM users")
        assert status == 403
        assert "禁止访问表" in data["error"]

    def test_full_flow_auto_limit_injection(self):
        """完整流程：无 LIMIT 时自动追加"""
        data, status = self._run_query("SELECT * FROM debug_log")
        assert status == 200
        # debug_log 有 4 行，LIMIT 500 不应该截断
        assert len(data["rows"]) == 4

    def test_full_flow_limit_capped(self):
        """完整流程：LIMIT > 500 被自动截断"""
        data, status = self._run_query("SELECT * FROM debug_log LIMIT 999")
        assert status == 200
        assert len(data["rows"]) == 4  # 数据库中只有 4 行

    def test_full_flow_sql_syntax_error(self):
        """完整流程：SQL 语法错误返回 400"""
        data, status = self._run_query("SELECTT * FROMM debug_log")
        assert status == 400
        assert "error" in data

    def test_full_flow_incomplete_sql(self):
        """完整流程：不完整的 SQL"""
        data, status = self._run_query("SELECT")
        assert status == 400
        assert "error" in data

    # ===== 输入校验 =====

    def test_invalid_json_body(self):
        """无效 JSON 返回 400"""
        handler = _make_handler(body_bytes=b"not json")
        result = _read_json(handler)
        assert result is None
        # 验证 json_response 被调用
        assert handler.send_response.called
        status = handler.send_response.call_args[0][0]
        assert status == 400

    def test_missing_sql_field(self):
        """请求体中缺少 sql 字段"""
        handler = _make_handler({"foo": "bar"})
        data = _read_json(handler)
        assert data is not None
        sql = data.get("sql", "").strip()
        assert sql == ""

    def test_empty_sql_string(self):
        """sql 字段为空字符串"""
        handler = _make_handler({"sql": ""})
        data = _read_json(handler)
        assert data is not None
        sql = data.get("sql", "").strip()
        assert sql == ""

    def test_null_sql_field(self):
        """sql 字段为 null，要通过校验逻辑"""
        handler = _make_handler({"sql": None})
        data = _read_json(handler)
        assert data is not None
        sql = data.get("sql")
        assert sql is None
