"""统一数据表定义 — 唯一建表语句来源。零内部依赖（第 0 层）。

所有 data db (access_log.db) 的表定义集中在此文件。
其他模块禁止手动编写 CREATE TABLE 语句，必须通过此模块获取。

用法:
    from proxy.schema import ensure_table, ensure_all_tables
    ensure_all_tables(conn)          # 创建所有表
    ensure_table(conn, 'token_stats')  # 创建单表
"""

# 表创建顺序（满足外键依赖）
_TABLE_ORDER = [
    "schema_version",
    "upstreams",
    "target_models", "route_templates",
    "model_routes",
    "agent_routes",
    "debug_log",
    "token_stats",
    "model_pricing",
]

# ─── 建表语句 ─────────────────────────────────────────────

TABLES = {
    "schema_version": """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER NOT NULL
        )
    """,

    "upstreams": """
        CREATE TABLE IF NOT EXISTS upstreams (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT UNIQUE NOT NULL,
            base_url        TEXT NOT NULL,
            api_key         TEXT NOT NULL DEFAULT '',
            timeout         INTEGER NOT NULL DEFAULT 600  CHECK(timeout > 0),
            connect_timeout INTEGER NOT NULL DEFAULT 10   CHECK(connect_timeout > 0),
            ssl_verify      INTEGER NOT NULL DEFAULT 1    CHECK(ssl_verify IN (0, 1)),
            retry           INTEGER NOT NULL DEFAULT 1    CHECK(retry >= 0),
            is_active       INTEGER NOT NULL DEFAULT 1    CHECK(is_active IN (0, 1)),
            format          TEXT NOT NULL DEFAULT 'chat_completions'
                            CHECK(format IN ('responses', 'messages', 'chat_completions')),
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """,

    "target_models": """
        CREATE TABLE IF NOT EXISTS target_models (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL CHECK(length(name) > 0),
            upstream_id INTEGER NOT NULL REFERENCES upstreams(id) ON DELETE RESTRICT,
            multimodal  INTEGER NOT NULL DEFAULT 1    CHECK(multimodal IN (0, 1)),
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(name, upstream_id)
        )
    """,

    "model_routes": """
        CREATE TABLE IF NOT EXISTS model_routes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            source          TEXT NOT NULL CHECK(length(source) > 0),
            target_model_id INTEGER REFERENCES target_models(id) ON DELETE SET NULL,
            request_type    TEXT NOT NULL DEFAULT 'responses'
                            CHECK(request_type IN ('responses', 'messages', 'chat_completions')),
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(source, request_type)
        )
    """,

    "agent_routes": """
        CREATE TABLE IF NOT EXISTS agent_routes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            source          TEXT NOT NULL CHECK(length(source) > 0 AND source != '*'),
            target_model_id INTEGER REFERENCES target_models(id) ON DELETE SET NULL,
            request_type    TEXT NOT NULL DEFAULT 'chat_completions'
                            CHECK(request_type IN ('responses', 'messages', 'chat_completions')),
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(source, request_type)
        )
    """,


    "route_templates": """
        CREATE TABLE IF NOT EXISTS route_templates (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL CHECK(length(name) > 0 AND length(name) <= 100),
            request_type    TEXT NOT NULL DEFAULT 'chat_completions'
                            CHECK(request_type IN ('responses', 'messages', 'chat_completions')),
            items           TEXT NOT NULL DEFAULT '[]',
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
            last_applied_at TEXT,
            UNIQUE(name, request_type)
        )
    """,

    "debug_log": """
        CREATE TABLE IF NOT EXISTS debug_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id   TEXT NOT NULL,
            stage        TEXT NOT NULL,
            model        TEXT,
            target_model TEXT,
            status_code  INTEGER,
            request_type TEXT,
            request_path TEXT,
            data         TEXT,
            session_id   TEXT,
            created_at   TEXT NOT NULL
        )
    """,

    "token_stats": """
        CREATE TABLE IF NOT EXISTS token_stats (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id          TEXT NOT NULL,
            model               TEXT NOT NULL,
            target_model        TEXT NOT NULL,
            input_tokens        INTEGER DEFAULT 0,
            output_tokens       INTEGER DEFAULT 0,
            cached_read_tokens  INTEGER DEFAULT 0,
            cached_write_tokens INTEGER DEFAULT 0,
            request_type        TEXT NOT NULL,
            response_type       TEXT,
            request_ts          TEXT NOT NULL,
            duration_ms         INTEGER,
            status              TEXT DEFAULT 'completed',
            upstream_id         INTEGER,
            session_id          TEXT,
            created_at          TEXT NOT NULL
        )
    """,

    "model_pricing": """
        CREATE TABLE IF NOT EXISTS model_pricing (
            model_id                        TEXT PRIMARY KEY,
            display_name                    TEXT NOT NULL,
            input_cost_per_million          TEXT NOT NULL,
            output_cost_per_million         TEXT NOT NULL,
            cache_read_cost_per_million     TEXT NOT NULL DEFAULT '0',
            cache_creation_cost_per_million TEXT NOT NULL DEFAULT '0',
            currency                        TEXT NOT NULL DEFAULT 'USD'
                                            CHECK(currency IN ('USD', 'RMB')),
            multiplier                      TEXT NOT NULL DEFAULT '1.0',
            input_includes_cache_read       INTEGER NOT NULL DEFAULT 0
                                            CHECK(input_includes_cache_read IN (0, 1)),
            created_at                      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """,
}

# ─── 索引语句 ─────────────────────────────────────────────

INDEXES = {
    "debug_log": [
        "CREATE INDEX IF NOT EXISTS idx_debug_request_id ON debug_log(request_id)",
        "CREATE INDEX IF NOT EXISTS idx_debug_created_at ON debug_log(created_at)",
    ],
    "token_stats": [
        "CREATE INDEX IF NOT EXISTS idx_token_stats_request_ts ON token_stats(request_ts)",
        "CREATE INDEX IF NOT EXISTS idx_token_stats_target_model ON token_stats(target_model)",
        "CREATE INDEX IF NOT EXISTS idx_token_stats_upstream_id ON token_stats(upstream_id)",
        "CREATE INDEX IF NOT EXISTS idx_token_stats_session_id ON token_stats(session_id)",
    ],
}

# ─── 公共 API ─────────────────────────────────────────────


def ensure_table(conn, table_name: str):
    """确保指定表及其索引存在（幂等）。"""
    sql = TABLES.get(table_name)
    if sql:
        conn.execute(sql)
    for idx_sql in INDEXES.get(table_name, []):
        conn.execute(idx_sql)



