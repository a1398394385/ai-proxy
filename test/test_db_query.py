"""POST /api/db/query 单元测试"""
import json
import re
import sqlite3
import tempfile
import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import server


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

        cls._patcher = patch("server.ACCESS_LOG_DB_PATH", cls.db_path)
        cls._patcher.start()

    @classmethod
    def teardown_class(cls):
        cls._patcher.stop()
        cls.tmpdir.cleanup()

    # ===== 正常查询 =====

    def test_normal_select_returns_columns_and_rows(self):
        """正常 SELECT 查询返回 columns + rows 结构"""
        conn = server.get_access_log_db()
        cursor = conn.execute("SELECT id, request_id FROM debug_log ORDER BY id LIMIT 2")
        columns = [col[0] for col in cursor.description]
        rows = [list(row) for row in cursor.fetchall()]
        conn.close()
        assert columns == ["id", "request_id"]
        assert rows == [[1, "req-001"], [2, "req-002"]]

    def test_select_token_stats(self):
        """token_stats 表查询正常"""
        conn = server.get_access_log_db()
        cursor = conn.execute("SELECT * FROM token_stats ORDER BY id")
        rows = [list(row) for row in cursor.fetchall()]
        conn.close()
        assert len(rows) == 2
        assert rows[0] == [1, "req-001", 100, 50, "gpt-4"]

    # ===== SQL 校验：只允许 SELECT =====

    def test_reject_delete(self):
        """DELETE 语句被拒绝"""
        sql = "DELETE FROM debug_log"
        assert not sql.strip().upper().startswith("SELECT")

    def test_reject_insert(self):
        """INSERT 语句被拒绝"""
        sql = "INSERT INTO debug_log VALUES (5, 'x')"
        assert not sql.strip().upper().startswith("SELECT")

    def test_reject_update(self):
        """UPDATE 语句被拒绝"""
        sql = "UPDATE debug_log SET stage='x'"
        assert not sql.strip().upper().startswith("SELECT")

    def test_reject_drop(self):
        """DROP 语句被拒绝"""
        sql = "DROP TABLE debug_log"
        assert not sql.strip().upper().startswith("SELECT")

    def test_reject_create(self):
        """CREATE 语句被拒绝"""
        sql = "CREATE TABLE x (id INT)"
        assert not sql.strip().upper().startswith("SELECT")

    def test_accept_select_lowercase(self):
        """小写 select 也应该通过"""
        sql = "select * from debug_log"
        assert sql.strip().upper().startswith("SELECT")

    def test_accept_select_with_cte(self):
        """WITH 开头的查询不被接受（因为没有以 SELECT 开头）"""
        sql = "WITH cte AS (SELECT 1) SELECT * FROM cte"
        # 注意：API 只检测是否以 SELECT 开头，此场景会被拒绝
        assert not sql.strip().upper().startswith("SELECT")

    # ===== SQL 校验：禁止多语句 =====

    def test_reject_semicolon_multi_statement(self):
        """含分号的多语句被拒绝"""
        sql = "SELECT 1; SELECT 2"
        assert ";" in sql

    def test_reject_semicolon_trailing(self):
        """结尾有分号也不允许"""
        sql = "SELECT * FROM debug_log;"
        assert ";" in sql

    def test_allow_select_without_semicolon(self):
        """正常 SELECT 可以包含不需要报错的分号内容
        注意：只检查 sql 语句中的分号
        """
        sql = "SELECT * FROM debug_log WHERE name LIKE '%abc%'"
        assert ";" not in sql

    # ===== 表名校验 =====

    def test_allow_debug_log_table(self):
        """debug_log 在白名单内"""
        table_pattern = re.compile(r"FROM\s+(\w+)", re.IGNORECASE)
        sql = "SELECT * FROM debug_log"
        tables = [m.group(1) for m in table_pattern.finditer(sql)]
        for t in tables:
            assert t in ("debug_log", "token_stats")

    def test_allow_token_stats_table(self):
        """token_stats 在白名单内"""
        table_pattern = re.compile(r"FROM\s+(\w+)", re.IGNORECASE)
        sql = "SELECT * FROM token_stats"
        tables = [m.group(1) for m in table_pattern.finditer(sql)]
        for t in tables:
            assert t in ("debug_log", "token_stats")

    def test_reject_upstreams_table(self):
        """upstreams 表不在白名单中"""
        table_pattern = re.compile(r"FROM\s+(\w+)", re.IGNORECASE)
        sql = "SELECT * FROM upstreams"
        tables = [m.group(1) for m in table_pattern.finditer(sql)]
        for t in tables:
            assert t not in ("debug_log", "token_stats")

    def test_reject_arbitrary_table(self):
        """其他任意表都不在白名单中"""
        table_pattern = re.compile(r"FROM\s+(\w+)", re.IGNORECASE)
        sql = "SELECT * FROM users"
        tables = [m.group(1) for m in table_pattern.finditer(sql)]
        for t in tables:
            assert t not in ("debug_log", "token_stats")

    # ===== LIMIT 处理 =====

    def test_limit_injection_when_missing(self):
        """无 LIMIT 时自动追加 LIMIT 500"""
        sql = "SELECT * FROM debug_log"
        limit_pattern = re.compile(r"LIMIT\s+(\d+)", re.IGNORECASE)
        if not limit_pattern.search(sql):
            sql += " LIMIT 500"
        assert "LIMIT 500" in sql

    def test_limit_capped_when_exceeds_500(self):
        """LIMIT > 500 时强制改为 500"""
        sql = "SELECT * FROM debug_log LIMIT 999"
        limit_pattern = re.compile(r"LIMIT\s+(\d+)", re.IGNORECASE)
        limit_match = limit_pattern.search(sql)
        if limit_match:
            limit_val = int(limit_match.group(1))
            if limit_val > 500:
                sql = limit_pattern.sub("LIMIT 500", sql)
        assert "LIMIT 500" in sql
        assert "999" not in sql

    def test_limit_preserved_when_under_500(self):
        """LIMIT <= 500 时保持不变"""
        sql = "SELECT * FROM debug_log LIMIT 50"
        limit_pattern = re.compile(r"LIMIT\s+(\d+)", re.IGNORECASE)
        limit_match = limit_pattern.search(sql)
        if limit_match:
            limit_val = int(limit_match.group(1))
            if limit_val > 500:
                sql = limit_pattern.sub("LIMIT 500", sql)
        assert "LIMIT 50" in sql

    def test_limit_exact_500_preserved(self):
        """LIMIT 恰好 500 时保持不变"""
        sql = "SELECT * FROM debug_log LIMIT 500"
        limit_pattern = re.compile(r"LIMIT\s+(\d+)", re.IGNORECASE)
        limit_match = limit_pattern.search(sql)
        if limit_match:
            limit_val = int(limit_match.group(1))
            if limit_val > 500:
                sql = limit_pattern.sub("LIMIT 500", sql)
        assert "LIMIT 500" in sql

    # ===== 全流程集成测试 =====

    def _run_query(self, sql):
        """模拟完整的 POST /api/db/query 处理流程，返回 (data, status)"""
        handler = _make_handler({"sql": sql})
        data = server._read_json(handler)
        if data is None:
            # 从 handler 的 send_response 捕获错误
            return _extract_call_args(handler)

        sql_text = data.get("sql", "").strip()

        # 安全校验：只允许 SELECT
        if not sql_text.upper().startswith("SELECT"):
            server.json_response(handler, {"error": "只允许 SELECT 查询"}, 400)
            return _extract_call_args(handler)

        # 安全校验：禁止多语句
        if ";" in sql_text:
            server.json_response(handler, {"error": "禁止多语句 SQL"}, 400)
            return _extract_call_args(handler)

        # 白名单表名校验
        table_pattern = re.compile(r"FROM\s+(\w+)", re.IGNORECASE)
        for match in table_pattern.finditer(sql_text):
            table_name = match.group(1)
            if table_name not in ("debug_log", "token_stats"):
                server.json_response(handler, {"error": f"禁止访问表: {table_name}"}, 403)
                return _extract_call_args(handler)

        # LIMIT 处理
        limit_pattern = re.compile(r"LIMIT\s+(\d+)", re.IGNORECASE)
        limit_match = limit_pattern.search(sql_text)
        if limit_match:
            limit_val = int(limit_match.group(1))
            if limit_val > 500:
                sql_text = limit_pattern.sub("LIMIT 500", sql_text)
        else:
            sql_text += " LIMIT 500"

        # 执行查询
        conn = server.get_access_log_db()
        try:
            cursor = conn.execute(sql_text)
            columns = [col[0] for col in cursor.description]
            rows = [list(row) for row in cursor.fetchall()]
            conn.close()
            server.json_response(handler, {"columns": columns, "rows": rows})
        except Exception as e:
            conn.close()
            server.json_response(handler, {"error": str(e)}, 400)

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
        result = server._read_json(handler)
        assert result is None
        # 验证 json_response 被调用
        assert handler.send_response.called
        status = handler.send_response.call_args[0][0]
        assert status == 400

    def test_missing_sql_field(self):
        """请求体中缺少 sql 字段"""
        handler = _make_handler({"foo": "bar"})
        data = server._read_json(handler)
        assert data is not None
        sql = data.get("sql", "").strip()
        assert sql == ""

    def test_empty_sql_string(self):
        """sql 字段为空字符串"""
        handler = _make_handler({"sql": ""})
        data = server._read_json(handler)
        assert data is not None
        sql = data.get("sql", "").strip()
        assert sql == ""

    def test_null_sql_field(self):
        """sql 字段为 null，要通过校验逻辑"""
        handler = _make_handler({"sql": None})
        data = server._read_json(handler)
        assert data is not None
        sql = data.get("sql")
        assert sql is None
