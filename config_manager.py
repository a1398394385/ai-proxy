#!/usr/bin/env python3
"""动态模型配置管理 — ConfigDB（数据库 CRUD）+ ConfigCache（内存缓存）。"""

import sqlite3
import sys
import threading
import time
import logging
from pathlib import Path
from typing import Optional


class ConfigDB:
    """config.db 数据库操作。每次查询打开新连接（无连接池）。

    参数:
        db_path: config.db 路径
        yaml_seed_path: 可选，proxy_config.yaml 路径，仅在首次启动且数据库为空时导入
    """

    def __init__(self, db_path: Path, yaml_seed_path: Path = None):
        self.db_path = db_path
        self._ensure_db()
        if yaml_seed_path is not None:
            self._seed_from_yaml(yaml_seed_path)

    def _connect(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=3000")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _ensure_db(self):
        """创建数据库和表（幂等）。"""
        conn = self._connect()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS upstreams (
                    id              TEXT PRIMARY KEY,
                    base_url        TEXT NOT NULL,
                    api_key         TEXT NOT NULL DEFAULT '',
                    timeout         INTEGER NOT NULL DEFAULT 120  CHECK(timeout > 0),
                    connect_timeout INTEGER NOT NULL DEFAULT 10   CHECK(connect_timeout > 0),
                    ssl_verify      INTEGER NOT NULL DEFAULT 1    CHECK(ssl_verify IN (0, 1)),
                    retry           INTEGER NOT NULL DEFAULT 1    CHECK(retry >= 0),
                    is_active       INTEGER NOT NULL DEFAULT 1    CHECK(is_active IN (0, 1)),
                    is_default      INTEGER NOT NULL DEFAULT 0    CHECK(is_default IN (0, 1)),
                    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS target_models (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    name        TEXT NOT NULL CHECK(length(name) > 0),
                    upstream_id TEXT NOT NULL REFERENCES upstreams(id) ON DELETE RESTRICT,
                    multimodal  INTEGER NOT NULL DEFAULT 1    CHECK(multimodal IN (0, 1)),
                    format      TEXT NOT NULL DEFAULT 'openai_chat',
                    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                    UNIQUE(name, upstream_id)
                );

                CREATE TABLE IF NOT EXISTS model_routes (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    source          TEXT NOT NULL UNIQUE CHECK(length(source) > 0),
                    target_model_id INTEGER NOT NULL REFERENCES target_models(id) ON DELETE RESTRICT,
                    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
                );
            """)
        finally:
            conn.close()

    def _seed_from_yaml(self, yaml_path: Path):
        """首次启动从 proxy_config.yaml 导入种子数据。

        使用 IMMEDIATE 事务减少 SELECT → BEGIN 之间的竞态窗口。
        """
        conn = self._connect()
        try:
            # IMMEDIATE 事务获取数据库写锁，阻止其他写入者，消除竞态
            conn.execute("BEGIN IMMEDIATE")
            try:
                has_version = conn.execute(
                    "SELECT COUNT(*) FROM schema_version"
                ).fetchone()[0] > 0
                if has_version:
                    conn.execute("ROLLBACK")
                    return

                if not yaml_path.exists():
                    conn.execute("INSERT INTO schema_version (version) VALUES (1)")
                    conn.commit()
                    return

                with open(yaml_path) as f:
                    config = _parse_yaml(f.read())

                # BEGIN IMMEDIATE 已在上面执行，无需重复
                upstream_data = config.get("upstream", {})
                if upstream_data:
                    conn.execute(
                        """INSERT INTO upstreams (id, base_url, api_key, timeout,
                           connect_timeout, ssl_verify, retry, is_default, is_active)
                           VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1)""",
                        (
                            "default",
                            upstream_data.get("base_url", ""),
                            upstream_data.get("api_key", ""),
                            upstream_data.get("timeout", 120),
                            upstream_data.get("connect_timeout", 10),
                            1 if upstream_data.get("ssl_verify", True) else 0,
                            upstream_data.get("retry", 1),
                        ),
                    )

                model_map = config.get("model_map", {})
                default_upstream_id = "default" if upstream_data else None

                for source, cfg in model_map.items():
                    if cfg is None or not isinstance(cfg, dict):
                        continue
                    target_name = cfg.get("target", source)
                    multimodal = 1 if cfg.get("multimodal", False) else 0

                    conn.execute(
                        """INSERT OR IGNORE INTO target_models (name, upstream_id, multimodal, format)
                           VALUES (?, ?, ?, 'openai_chat')""",
                        (target_name, default_upstream_id, multimodal),
                    )
                    row = conn.execute(
                        """SELECT id FROM target_models
                           WHERE name=? AND upstream_id=?""",
                        (target_name, default_upstream_id),
                    ).fetchone()
                    if row is None:
                        continue
                    target_id = row["id"]

                    conn.execute(
                        """INSERT OR REPLACE INTO model_routes (source, target_model_id)
                           VALUES (?, ?)""",
                        (source, target_id),
                    )

                conn.execute("INSERT INTO schema_version (version) VALUES (1)")
                conn.commit()

                if conn.execute("SELECT COUNT(*) FROM model_routes WHERE source='*'").fetchone()[0] == 0:
                    print("FATAL: 种子导入完成后 * fallback 路由仍然缺失", file=sys.stderr)
                    sys.exit(1)
            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    def close(self):
        pass

    # ─── 上游 CRUD ────────────────────────────────────────────────

    def list_upstreams(self, active_only=False):
        conn = self._connect()
        try:
            sql = "SELECT * FROM upstreams"
            if active_only:
                sql += " WHERE is_active = 1"
            sql += " ORDER BY id"
            return [dict(r) for r in conn.execute(sql).fetchall()]
        finally:
            conn.close()

    def get_upstream(self, upstream_id: str) -> Optional[dict]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM upstreams WHERE id = ?", (upstream_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def add_upstream(self, data: dict) -> str:
        conn = self._connect()
        try:
            if data.get("is_default"):
                conn.execute("UPDATE upstreams SET is_default = 0")

            conn.execute(
                """INSERT INTO upstreams (id, base_url, api_key, timeout,
                   connect_timeout, ssl_verify, retry, is_default)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    data["id"],
                    data["base_url"],
                    data.get("api_key", ""),
                    data.get("timeout", 120),
                    data.get("connect_timeout", 10),
                    data.get("ssl_verify", 1),
                    data.get("retry", 1),
                    data.get("is_default", 0),
                ),
            )
            conn.commit()
            return data["id"]
        finally:
            conn.close()

    def update_upstream(self, upstream_id: str, data: dict):
        conn = self._connect()
        try:
            if data.get("is_default"):
                conn.execute("UPDATE upstreams SET is_default = 0")

            fields = []
            values = []
            for key in ("base_url", "api_key", "timeout", "connect_timeout",
                        "ssl_verify", "retry", "is_default"):
                if key in data:
                    fields.append(f"{key} = ?")
                    values.append(data[key])
            if not fields:
                return
            fields.append("updated_at = datetime('now')")
            values.append(upstream_id)

            conn.execute(
                f"UPDATE upstreams SET {', '.join(fields)} WHERE id = ?",
                values,
            )
            conn.commit()
        finally:
            conn.close()

    def disable_upstream(self, upstream_id: str):
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE upstreams SET is_active = 0, updated_at = datetime('now') WHERE id = ?",
                (upstream_id,),
            )
            conn.commit()
        finally:
            conn.close()

    def upstream_active_routes(self, upstream_id: str) -> list:
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT mr.source FROM model_routes mr
                   JOIN target_models tm ON mr.target_model_id = tm.id
                   WHERE tm.upstream_id = ?""",
                (upstream_id,),
            ).fetchall()
            return [r["source"] for r in rows]
        finally:
            conn.close()

    # ─── 目标模型 CRUD ────────────────────────────────────────────

    def list_models(self, upstream_id=None):
        conn = self._connect()
        try:
            sql = """SELECT tm.*, u.id as upstream_name, u.is_active as upstream_active
                     FROM target_models tm
                     JOIN upstreams u ON tm.upstream_id = u.id"""
            params = []
            if upstream_id:
                sql += " WHERE tm.upstream_id = ?"
                params.append(upstream_id)
            sql += " ORDER BY tm.upstream_id, tm.name"
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def get_model(self, model_id: int) -> Optional[dict]:
        conn = self._connect()
        try:
            row = conn.execute(
                """SELECT tm.*, u.id as upstream_name, u.is_active as upstream_active
                   FROM target_models tm
                   JOIN upstreams u ON tm.upstream_id = u.id
                   WHERE tm.id = ?""",
                (model_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def add_model(self, data: dict) -> int:
        conn = self._connect()
        try:
            cursor = conn.execute(
                """INSERT INTO target_models (name, upstream_id, multimodal, format)
                   VALUES (?, ?, ?, ?)""",
                (
                    data["name"],
                    data["upstream_id"],
                    data.get("multimodal", 1),
                    data.get("format", "openai_chat"),
                ),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def update_model(self, model_id: int, data: dict):
        conn = self._connect()
        try:
            fields = []
            values = []
            for key in ("name", "upstream_id", "multimodal", "format"):
                if key in data:
                    fields.append(f"{key} = ?")
                    values.append(data[key])
            if not fields:
                return
            values.append(model_id)
            conn.execute(
                f"UPDATE target_models SET {', '.join(fields)} WHERE id = ?",
                values,
            )
            conn.commit()
        finally:
            conn.close()

    def delete_model(self, model_id: int, check_refs: bool = True):
        """删除模型。check_refs=True 时先检查路由引用，有引用则返回引用列表（不抛异常）。"""
        conn = self._connect()
        try:
            if check_refs:
                refs = [r["source"] for r in conn.execute(
                    "SELECT source FROM model_routes WHERE target_model_id = ?",
                    (model_id,),
                ).fetchall()]
                if refs:
                    return {"error": "模型被路由引用，无法删除", "referenced_routes": refs}
            conn.execute("DELETE FROM target_models WHERE id = ?", (model_id,))
            conn.commit()
            return {"message": "Deleted"}
        finally:
            conn.close()

    def model_referenced_routes(self, model_id: int) -> list:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT source FROM model_routes WHERE target_model_id = ?",
                (model_id,),
            ).fetchall()
            return [r["source"] for r in rows]
        finally:
            conn.close()

    # ─── 路由映射 CRUD ────────────────────────────────────────────

    def list_routes(self):
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT mr.*, tm.name as target_name, tm.upstream_id,
                          u.is_active as upstream_active
                   FROM model_routes mr
                   JOIN target_models tm ON mr.target_model_id = tm.id
                   JOIN upstreams u ON tm.upstream_id = u.id
                   ORDER BY
                     CASE mr.source WHEN '*' THEN 0 ELSE 1 END,
                     mr.source"""
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_route(self, route_id: int) -> Optional[dict]:
        conn = self._connect()
        try:
            row = conn.execute(
                """SELECT mr.*, tm.name as target_name, tm.upstream_id
                   FROM model_routes mr
                   JOIN target_models tm ON mr.target_model_id = tm.id
                   WHERE mr.id = ?""",
                (route_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def add_route(self, data: dict) -> int:
        conn = self._connect()
        try:
            cursor = conn.execute(
                "INSERT INTO model_routes (source, target_model_id) VALUES (?, ?)",
                (data["source"], data["target_model_id"]),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def update_route(self, route_id: int, data: dict):
        conn = self._connect()
        try:
            fields = []
            values = []
            for key in ("source", "target_model_id"):
                if key in data:
                    fields.append(f"{key} = ?")
                    values.append(data[key])
            if not fields:
                return
            fields.append("updated_at = datetime('now')")
            values.append(route_id)
            conn.execute(
                f"UPDATE model_routes SET {', '.join(fields)} WHERE id = ?",
                values,
            )
            conn.commit()
        finally:
            conn.close()

    def delete_route(self, route_id: int):
        conn = self._connect()
        try:
            conn.execute("DELETE FROM model_routes WHERE id = ?", (route_id,))
            conn.commit()
        finally:
            conn.close()

    def get_route_by_source(self, source: str) -> Optional[dict]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM model_routes WHERE source = ?", (source,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    # ─── 配置查询（供 proxy 使用）────────────────────────────────

    def resolve_model(self, source_name: str) -> Optional[dict]:
        """返回值约定：
        - 找到可用匹配（路由存在 + 上游 is_active=1）→ 返回完整配置 dict
        - 匹配到但上游禁用 → 跳过，继续尝试 "*" fallback
        - "*" 也找不到或也禁用 → 返回 None
        """
        for name in (source_name, "*"):
            row = self._resolve_one(name)
            if row is not None:
                return row
        return None

    def _resolve_one(self, source_name: str) -> Optional[dict]:
        conn = self._connect()
        try:
            row = conn.execute(
                """SELECT tm.name as target_name, tm.multimodal, tm.format,
                          u.id as upstream_id, u.base_url, u.api_key,
                          u.timeout, u.connect_timeout, u.ssl_verify, u.retry
                   FROM model_routes mr
                   JOIN target_models tm ON mr.target_model_id = tm.id
                   JOIN upstreams u ON tm.upstream_id = u.id
                   WHERE mr.source = ? AND u.is_active = 1""",
                (source_name,),
            ).fetchone()
            if row is None:
                return None
            d = dict(row)
            return {
                "target_name": d["target_name"],
                "multimodal": d["multimodal"],
                "format": d["format"],
                "matched_source": source_name,
                "upstream": {
                    "id": d["upstream_id"],
                    "base_url": d["base_url"],
                    "api_key": d["api_key"],
                    "timeout": d["timeout"],
                    "connect_timeout": d["connect_timeout"],
                    "ssl_verify": bool(d["ssl_verify"]),
                    "retry": d["retry"],
                },
            }
        finally:
            conn.close()

    def get_all_routes(self) -> dict:
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT mr.source, tm.name as target_name, tm.multimodal,
                          tm.format, tm.upstream_id
                   FROM model_routes mr
                   JOIN target_models tm ON mr.target_model_id = tm.id
                   JOIN upstreams u ON tm.upstream_id = u.id
                   WHERE u.is_active = 1"""
            ).fetchall()
            result = {}
            for r in rows:
                result[r["source"]] = {
                    "target_name": r["target_name"],
                    "multimodal": r["multimodal"],
                    "format": r["format"],
                    "upstream_id": r["upstream_id"],
                }
            return result
        finally:
            conn.close()

    def validate_star_fallback(self) -> bool:
        return self.resolve_model("*") is not None

    def get_counts(self) -> dict:
        conn = self._connect()
        try:
            upstreams = conn.execute(
                "SELECT COUNT(*) FROM upstreams WHERE is_active = 1"
            ).fetchone()[0]
            models = conn.execute("SELECT COUNT(*) FROM target_models").fetchone()[0]
            routes = conn.execute("SELECT COUNT(*) FROM model_routes").fetchone()[0]
            return {"upstreams": upstreams, "models": models, "routes": routes}
        finally:
            conn.close()


class ConfigCache:
    """内存缓存，供 proxy.py 使用。"""

    def __init__(self, db_path: Path, ttl: float = 5, yaml_seed_path: Path = None):
        self._db_path = db_path
        self._ttl = ttl
        self._yaml_seed_path = yaml_seed_path
        self._lock = threading.Lock()
        self._routes: dict = {}
        self._loaded_at: float = 0

    def reload(self):
        with self._lock:
            self._loaded_at = 0

    def resolve(self, source_name: str) -> Optional[dict]:
        with self._lock:
            self._refresh_if_stale()
            if source_name in self._routes:
                return self._routes[source_name]
            return self._routes.get("*")

    def get_all(self) -> dict:
        with self._lock:
            self._refresh_if_stale()
            return {k: v for k, v in self._routes.items()}

    def _refresh_if_stale(self):
        now = time.time()
        if self._loaded_at > 0 and now - self._loaded_at < self._ttl:
            return
        try:
            db = ConfigDB(self._db_path, yaml_seed_path=self._yaml_seed_path)
            try:
                new_routes = {}
                all_routes = db.get_all_routes()
                for source in all_routes:
                    cfg = db._resolve_one(source)
                    if cfg:
                        new_routes[source] = cfg
                star_cfg = db.resolve_model("*")
                if star_cfg:
                    new_routes["*"] = star_cfg
                self._routes = new_routes
                self._loaded_at = time.time()
            finally:
                db.close()
        except Exception:
            # 数据库异常时保留旧缓存，不更新 _loaded_at
            # TTL 机制下次继续尝试，同时记录日志方便排查
            logging.warning("[ConfigCache] 配置缓存刷新失败，保留旧缓存", exc_info=True)


# ─── YAML 解析（内联，避免依赖 proxy.py）───────────────────────────

def _parse_yaml(text: str) -> dict:
    """极简 YAML 解析器。"""
    result = {}
    stack = [(result, -1)]

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())
        while len(stack) > 1 and stack[-1][1] >= indent:
            stack.pop()

        current_dict = stack[-1][0]
        if ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip().strip('"').strip("'")
            val = val.strip()

            if val == "" or val.startswith("#"):
                new_dict = {}
                current_dict[key] = new_dict
                stack.append((new_dict, indent))
            elif val.startswith("[") and val.endswith("]"):
                items = [
                    _yaml_scalar(item.strip())
                    for item in val[1:-1].split(",")
                    if item.strip()
                ]
                current_dict[key] = items
            else:
                current_dict[key] = _yaml_scalar(val)

    return result


def _yaml_scalar(val: str):
    if not val:
        return ""
    if " #" in val:
        val = val[: val.index(" #")].strip()
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        return val[1:-1]
    if val.lower() in ("true", "yes"):
        return True
    if val.lower() in ("false", "no"):
        return False
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    return val
