"""server 包 — DB Query API。"""

import re

from .common import json_response, _read_json, data_db


def handle_post(path, handler) -> bool:
    if path != "/api/db/query":
        return False

    data = _read_json(handler)
    if not data:
        return True
    sql = data.get("sql", "").strip()

    # 安全校验：只允许 SELECT
    if not sql.upper().startswith("SELECT"):
        json_response(handler, {"error": "只允许 SELECT 查询"}, 400)
        return True

    # 安全校验：禁止多语句
    if ";" in sql:
        json_response(handler, {"error": "禁止多语句 SQL"}, 400)
        return True

    # 白名单表名校验
    table_pattern = re.compile(r"FROM\s+(\w+)", re.IGNORECASE)
    for match in table_pattern.finditer(sql):
        table_name = match.group(1)
        if table_name not in ("debug_log", "token_stats"):
            json_response(handler, {"error": f"禁止访问表: {table_name}"}, 403)
            return True

    # LIMIT 处理
    limit_pattern = re.compile(r"LIMIT\s+(\d+)", re.IGNORECASE)
    limit_match = limit_pattern.search(sql)
    if limit_match:
        limit_val = int(limit_match.group(1))
        if limit_val > 500:
            sql = limit_pattern.sub("LIMIT 500", sql)
    else:
        sql += " LIMIT 500"

    # 执行查询
    with data_db() as conn:
        try:
            cursor = conn.execute(sql)
            columns = [col[0] for col in cursor.description]
            rows = [list(row) for row in cursor.fetchall()]
        except Exception as e:
            json_response(handler, {"error": str(e)}, 400)
            return True
    json_response(handler, {"columns": columns, "rows": rows})
    return True
