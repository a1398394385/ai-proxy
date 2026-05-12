#!/usr/bin/env python3
"""动态模型配置管理 — ConfigDB（数据库 CRUD）+ ConfigCache（内存缓存）。"""

import sqlite3
import shutil
import threading
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional


class ConfigDB:
    """config.db 数据库操作。每次查询打开新连接（无连接池）。

    参数:
        db_path: config.db 路径
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._ensure_db()

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
                    timeout         INTEGER NOT NULL DEFAULT 600  CHECK(timeout > 0),
                    connect_timeout INTEGER NOT NULL DEFAULT 10   CHECK(connect_timeout > 0),
                    ssl_verify      INTEGER NOT NULL DEFAULT 1    CHECK(ssl_verify IN (0, 1)),
                    retry           INTEGER NOT NULL DEFAULT 1    CHECK(retry >= 0),
                    is_active       INTEGER NOT NULL DEFAULT 1    CHECK(is_active IN (0, 1)),
                    format          TEXT NOT NULL DEFAULT 'openai_chat'
                                    CHECK(format IN ('responses', 'messages', 'chat_completions')),
                    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS target_models (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    name        TEXT NOT NULL CHECK(length(name) > 0),
                    upstream_id TEXT NOT NULL REFERENCES upstreams(id) ON DELETE RESTRICT,
                    multimodal  INTEGER NOT NULL DEFAULT 1    CHECK(multimodal IN (0, 1)),
                    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                    UNIQUE(name, upstream_id)
                );

                CREATE TABLE IF NOT EXISTS model_routes (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    source          TEXT NOT NULL CHECK(length(source) > 0),
                    target_model_id INTEGER NOT NULL REFERENCES target_models(id) ON DELETE RESTRICT,
                    request_type    TEXT NOT NULL DEFAULT 'responses'
                                    CHECK(request_type IN ('responses', 'messages', 'chat_completions')),
                    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
                    UNIQUE(source, request_type)
                );
            """)
        finally:
            conn.close()

        # 检查迁移状态（不阻塞启动）
        try:
            mg = Migrations(self.db_path)
            s = mg.status()
            if not s["migrated"]:
                logging.warning(
                    f"[ConfigDB] 数据库需要迁移，"
                    f"请调用 Migrations.migrate() 或 POST /api/migrate. "
                    f"当前状态: {s['details']}"
                )
        except Exception:
            pass  # 静默 — 不阻塞启动

    def close(self):
        """No-op placeholder for API compatibility."""
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
            conn.execute(
                """INSERT INTO upstreams (id, base_url, api_key, timeout,
                   connect_timeout, ssl_verify, retry, format)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    data["id"],
                    data["base_url"],
                    data.get("api_key", ""),
                    data.get("timeout", 600),
                    data.get("connect_timeout", 10),
                    data.get("ssl_verify", 1),
                    data.get("retry", 1),
                    data.get("format", "chat_completions"),
                ),
            )
            conn.commit()
            return data["id"]
        finally:
            conn.close()

    def update_upstream(self, upstream_id: str, data: dict):
        conn = self._connect()
        try:
            fields = []
            values = []
            for key in ("base_url", "api_key", "timeout", "connect_timeout",
                        "ssl_verify", "retry", "format"):
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

    def delete_upstream_with_models(self, upstream_id: str):
        """删除上游及其关联模型（前置：已确认无路由引用）。
        使用显式事务保证原子性。"""
        conn = self._connect()
        try:
            conn.execute("BEGIN TRANSACTION")
            model_ids = [r["id"] for r in conn.execute(
                "SELECT id FROM target_models WHERE upstream_id = ?", (upstream_id,)
            ).fetchall()]
            for mid in model_ids:
                conn.execute("DELETE FROM target_models WHERE id = ?", (mid,))
            conn.execute("DELETE FROM upstreams WHERE id = ?", (upstream_id,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ─── 目标模型 CRUD ────────────────────────────────────────────

    def list_models(self, upstream_id=None):
        conn = self._connect()
        try:
            sql = """SELECT tm.id, tm.name, tm.upstream_id, tm.multimodal, tm.created_at,
                            u.format, u.id as upstream_name, u.is_active as upstream_active
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
                """SELECT tm.id, tm.name, tm.upstream_id, tm.multimodal, tm.created_at,
                            u.format, u.id as upstream_name, u.is_active as upstream_active
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
                """INSERT INTO target_models (name, upstream_id, multimodal)
                   VALUES (?, ?, ?)""",
                (
                    data["name"],
                    data["upstream_id"],
                    data.get("multimodal", 1),
                ),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def add_models_bulk(self, upstream_id: str, models: list) -> dict:
        """批量新增模型到指定上游（INSERT OR IGNORE 幂等）。

        返回:
            {"added": int, "skipped": int}
        """
        conn = self._connect()
        try:
            if not models:
                return {"added": 0, "skipped": 0}
            added = 0
            skipped = 0
            for m in models:
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO target_models (name, upstream_id, multimodal) VALUES (?, ?, ?)",
                    (m["name"], upstream_id, m.get("multimodal", 1)),
                )
                if cursor.rowcount == 1:
                    added += 1
                else:
                    skipped += 1
            conn.commit()
            return {"added": added, "skipped": skipped}
        finally:
            conn.close()
    def update_model(self, model_id: int, data: dict):
        conn = self._connect()
        try:
            fields = []
            values = []
            for key in ("name", "upstream_id", "multimodal"):
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

    def list_routes(self, request_type: Optional[str] = None):
        conn = self._connect()
        try:
            params = []
            where = ""
            if request_type is not None:
                where = "WHERE mr.request_type = ?"
                params.append(request_type)
            rows = conn.execute(
                f"""SELECT mr.*, tm.name as target_name, tm.upstream_id,
                          u.is_active as upstream_active
                   FROM model_routes mr
                   JOIN target_models tm ON mr.target_model_id = tm.id
                   JOIN upstreams u ON tm.upstream_id = u.id
                   {where}
                   ORDER BY
                     CASE mr.source WHEN '*' THEN 0 ELSE 1 END,
                     mr.source""",
                params,
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
        request_type = data.get("request_type", "responses")
        if request_type not in ("responses", "messages", "chat_completions"):
            raise ValueError("request_type must be one of: responses, messages, chat_completions")
        conn = self._connect()
        try:
            # 校验目标模型所属上游是否活跃
            active = conn.execute(
                """SELECT 1 FROM target_models tm
                   JOIN upstreams u ON tm.upstream_id = u.id
                   WHERE tm.id = ? AND u.is_active = 1""",
                (data["target_model_id"],),
            ).fetchone()
            if not active:
                raise ValueError("目标模型不存在或所属上游已禁用")
            cursor = conn.execute(
                "INSERT INTO model_routes (source, target_model_id, request_type) VALUES (?, ?, ?)",
                (data["source"], data["target_model_id"], request_type),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def update_route(self, route_id: int, data: dict):
        conn = self._connect()
        try:
            # 保护回退路由：不允许修改 source
            if data.get("source") and data["source"] != "*":
                existing_source = conn.execute(
                    "SELECT source FROM model_routes WHERE id = ?",
                    (route_id,),
                ).fetchone()
                if existing_source and existing_source["source"] == "*":
                    raise ValueError("不能修改回退路由的源模型名")
            fields = []
            values = []
            for key in ("source", "target_model_id", "request_type"):
                if key in data:
                    if key == "request_type" and data[key] not in ("responses", "messages", "chat_completions"):
                        raise ValueError("request_type must be one of: responses, messages, chat_completions")
                    fields.append(f"{key} = ?")
                    values.append(data[key])
            if not fields:
                return

            # 校验目标模型是否存在且所属上游活跃
            target_model_id: Optional[int] = data.get("target_model_id")
            if target_model_id is not None:
                # 使用新提供的 target_model_id
                active = conn.execute(
                    """SELECT 1 FROM target_models tm
                       JOIN upstreams u ON tm.upstream_id = u.id
                       WHERE tm.id = ? AND u.is_active = 1""",
                    (target_model_id,),
                ).fetchone()
                if not active:
                    raise ValueError("目标模型不存在或所属上游已禁用")
            else:
                # 未提供 target_model_id，验证现有路由的目标模型仍然有效
                existing = conn.execute(
                    "SELECT target_model_id FROM model_routes WHERE id = ?",
                    (route_id,),
                ).fetchone()
                if existing:
                    active = conn.execute(
                        """SELECT 1 FROM target_models tm
                           JOIN upstreams u ON tm.upstream_id = u.id
                           WHERE tm.id = ? AND u.is_active = 1""",
                        (existing["target_model_id"],),
                    ).fetchone()
                    if not active:
                        raise ValueError("当前路由的目标模型所属上游已禁用")

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

    def resolve_model(self, source_name: str, request_type: str = "responses") -> Optional[dict]:
        """返回值约定：
        - 找到可用匹配（路由存在 + 上游 is_active=1）→ 返回完整配置 dict
        - 匹配到但上游禁用 → 跳过，继续尝试 "*" fallback
        - "*" 也找不到或也禁用 → 返回 None
        """
        for name in (source_name, "*"):
            row = self.resolve_one(name, request_type)
            if row is not None:
                return row
        return None

    def resolve_one(self, source_name: str, request_type: str = "responses") -> Optional[dict]:
        """精确匹配单个 source 的路由配置，无 fallback（供 ConfigCache 内部使用）。"""
        conn = self._connect()
        try:
            row = conn.execute(
                """SELECT tm.name as target_name, tm.multimodal, u.format,
                          u.id as upstream_id, u.base_url, u.api_key,
                          u.timeout, u.connect_timeout, u.ssl_verify, u.retry
                   FROM model_routes mr
                   JOIN target_models tm ON mr.target_model_id = tm.id
                   JOIN upstreams u ON tm.upstream_id = u.id
                   WHERE mr.source = ? AND mr.request_type = ? AND u.is_active = 1""",
                (source_name, request_type),
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

    def get_all_routes(self, request_type: Optional[str] = None) -> dict:
        conn = self._connect()
        try:
            params = []
            where = "WHERE u.is_active = 1"
            if request_type is not None:
                where += " AND mr.request_type = ?"
                params.append(request_type)
            rows = conn.execute(
                f"""SELECT mr.source, mr.request_type, tm.name as target_name, tm.multimodal,
                          u.format, tm.upstream_id
                   FROM model_routes mr
                   JOIN target_models tm ON mr.target_model_id = tm.id
                   JOIN upstreams u ON tm.upstream_id = u.id
                   {where}""",
                params,
            ).fetchall()
            result = {}
            for r in rows:
                result[r["source"]] = {
                    "target_name": r["target_name"],
                    "multimodal": r["multimodal"],
                    "format": r["format"],
                    "upstream_id": r["upstream_id"],
                    "request_type": r["request_type"],
                }
            return result
        finally:
            conn.close()

    def validate_star_fallback(self, request_type: str = "responses") -> bool:
        return self.resolve_model("*", request_type) is not None

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


class Migrations:
    """数据库迁移管理 — 将 model_routes 表从 v0 升级到 v1（添加 proxy_type 列），
    以及将 format 字段从 target_models 迁移到 upstreams（v1 → v2）。

    SQLite 不支持 ALTER TABLE DROP CONSTRAINT，因此使用重建表方式迁移。
    迁移是幂等的 — 已迁移的数据库再次调用 migrate() 会立即返回。
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path

    def _connect(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=3000")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def status(self) -> dict:
        """返回当前迁移状态。version 0 = 未迁移，version >= 4 = 已迁移。"""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT version FROM schema_version LIMIT 1"
            ).fetchone()
            version = row["version"] if row else 0
            if version == 0:
                # 可能是真正的 v0，也可能是 _ensure_db() 创建的新 v3 数据库但没有 schema_version 行
                has_request_type = conn.execute(
                    "SELECT 1 FROM pragma_table_info('model_routes') WHERE name = 'request_type'"
                ).fetchone()
                if has_request_type:
                    return {
                        "migrated": True,
                        "version": 4,
                        "details": "已迁移到 v4: 数据库由更新的 _ensure_db() 直接创建",
                    }
                return {
                    "migrated": False,
                    "version": 0,
                    "details": "尚未执行迁移: model_routes 表缺少 request_type 列",
                }
            if version == 1:
                return {
                    "migrated": False,
                    "version": 1,
                    "details": "需要执行迁移: format 字段需要从 target_models 迁移到 upstreams",
                }
            if version == 2:
                return {
                    "migrated": False,
                    "version": 2,
                    "details": "需要执行迁移: proxy_type 列需要重命名为 request_type，数据值需要映射",
                }
            if version == 3:
                return {
                    "migrated": False,
                    "version": 3,
                    "details": "需要执行迁移: upstreams.is_default 列需要移除",
                }
            return {
                "migrated": True,
                "version": version,
                "details": f"已迁移到 v{version}: request_type 和 format 约束已更新",
            }
        finally:
            conn.close()

    def migrate(self) -> dict:
        """执行迁移（幂等）。v0 → v1 → v2 → v3 按序执行。"""
        s = self.status()
        if s["migrated"]:
            logging.info(f"[Migrations] 数据库已是最新版本 v{s['version']}，跳过迁移")
            return {"status": "already_migrated", "details": s["details"]}

        version = s["version"]

        # STEP 1: 备份现有数据库
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup_name = f"{self.db_path.stem}.bak.{timestamp}{self.db_path.suffix}"
        backup_path = self.db_path.parent / backup_name
        shutil.copy2(self.db_path, backup_path)
        logging.info(f"[Migrations] STEP 1: 备份完成 -> {backup_path}")

        if version == 0:
            self._migrate_v0_to_v1(backup_path)
        if version <= 1:
            self._migrate_v1_to_v2(backup_path)
        if version <= 2:
            self._migrate_v2_to_v3(backup_path)
        if version <= 3:
            self._migrate_v3_to_v4(backup_path)

        return {
            "status": "ok",
            "version": 4,
            "backup_path": str(backup_path),
        }

    def _migrate_v0_to_v1(self, backup_path: Path):
        """执行 v0 → v1 迁移（添加 proxy_type 列到 model_routes）。"""
        conn = self._connect()
        try:
            conn.execute("BEGIN TRANSACTION")
            try:
                old_count = conn.execute(
                    "SELECT COUNT(*) FROM model_routes"
                ).fetchone()[0]
                logging.info(
                    f"[Migrations] v0→v1 STEP 0: 原始 model_routes 记录数 = {old_count}"
                )

                conn.execute("""
                    CREATE TABLE model_routes_new (
                        id              INTEGER PRIMARY KEY AUTOINCREMENT,
                        source          TEXT NOT NULL CHECK(length(source) > 0),
                        target_model_id INTEGER NOT NULL REFERENCES target_models(id) ON DELETE RESTRICT,
                        proxy_type      TEXT NOT NULL DEFAULT 'codex'
                            CHECK(proxy_type IN ('codex', 'claude', 'pass_through')),
                        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                        updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
                        UNIQUE(source, proxy_type)
                    );
                """)
                logging.info("[Migrations] v0→v1 STEP 2: model_routes_new 表创建完成")

                conn.execute("""
                    INSERT INTO model_routes_new
                        (id, source, target_model_id, created_at, updated_at)
                    SELECT id, source, target_model_id, created_at, updated_at
                    FROM model_routes;
                """)
                logging.info("[Migrations] v0→v1 STEP 3: 数据复制完成")

                conn.execute("DROP TABLE model_routes;")
                conn.execute(
                    "ALTER TABLE model_routes_new RENAME TO model_routes;"
                )
                logging.info("[Migrations] v0→v1 STEP 4: 表替换完成")

                conn.execute("DELETE FROM schema_version;")
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (1);"
                )
                logging.info("[Migrations] v0→v1 STEP 5: schema_version 更新为 1")

                new_count = conn.execute(
                    "SELECT COUNT(*) FROM model_routes"
                ).fetchone()[0]
                if new_count < old_count:
                    raise sqlite3.OperationalError(
                        f"迁移验证失败: 原有 {old_count} 条记录, 现有 {new_count} 条记录"
                    )
                logging.info(
                    f"[Migrations] v0→v1 STEP 6: 验证通过, {new_count} 条记录"
                )

                conn.commit()
                logging.info(
                    f"[Migrations] v0→v1 迁移成功: {new_count} 条路由"
                )
            except Exception:
                conn.rollback()
                logging.error("[Migrations] v0→v1 迁移失败，已回滚", exc_info=True)
                raise
        finally:
            conn.close()

    def _migrate_v1_to_v2(self, backup_path: Path):
        """执行 v1 → v2 迁移（format 字段从 target_models 迁移到 upstreams）。"""
        conn = self._connect()
        try:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("BEGIN TRANSACTION")
            try:
                has_format = conn.execute(
                    "SELECT 1 FROM pragma_table_info('upstreams') WHERE name = 'format'"
                ).fetchone()
                if not has_format:
                    conn.execute(
                        "ALTER TABLE upstreams ADD COLUMN format TEXT NOT NULL DEFAULT 'openai_chat'"
                    )
                    logging.info("[Migrations] v1→v2 STEP 1: upstreams.format 列添加完成")
                else:
                    logging.info("[Migrations] v1→v2 STEP 1: upstreams.format 列已存在，跳过")

                has_format_col = conn.execute(
                    "SELECT 1 FROM pragma_table_info('target_models') WHERE name = 'format'"
                ).fetchone()
                if has_format_col:
                    conn.execute("""
                        UPDATE upstreams
                        SET format = COALESCE(
                            (SELECT format FROM target_models
                             WHERE target_models.upstream_id = upstreams.id
                               AND format != 'openai_chat'
                             LIMIT 1),
                            'openai_chat'
                        )
                    """)
                    logging.info("[Migrations] v1→v2 STEP 2: upstreams.format 数据合并完成")

                    conn.execute("""
                        CREATE TABLE target_models_new (
                            id          INTEGER PRIMARY KEY AUTOINCREMENT,
                            name        TEXT NOT NULL CHECK(length(name) > 0),
                            upstream_id TEXT NOT NULL REFERENCES upstreams(id) ON DELETE RESTRICT,
                            multimodal  INTEGER NOT NULL DEFAULT 1    CHECK(multimodal IN (0, 1)),
                            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                            UNIQUE(name, upstream_id)
                        );
                    """)
                    logging.info("[Migrations] v1→v2 STEP 3: target_models_new 表创建完成")

                    conn.execute("""
                        INSERT INTO target_models_new
                            (id, name, upstream_id, multimodal, created_at)
                        SELECT id, name, upstream_id, multimodal, created_at
                        FROM target_models;
                    """)
                    logging.info("[Migrations] v1→v2 STEP 4: target_models 数据复制完成")

                    conn.execute("DROP TABLE target_models;")
                    conn.execute(
                        "ALTER TABLE target_models_new RENAME TO target_models;"
                    )
                    logging.info("[Migrations] v1→v2 STEP 5: target_models 表替换完成")
                else:
                    logging.info("[Migrations] v1→v2: target_models.format 列已不存在，跳过数据迁移")

                conn.execute("DELETE FROM schema_version;")
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (2);"
                )
                logging.info("[Migrations] v1→v2 STEP 6: schema_version 更新为 2")

                old_model_count = conn.execute(
                    "SELECT COUNT(*) FROM target_models"
                ).fetchone()[0]
                upstream_with_format = conn.execute(
                    "SELECT COUNT(*) FROM upstreams WHERE format IS NOT NULL"
                ).fetchone()[0]
                logging.info(
                    f"[Migrations] v1→v2 STEP 7: 验证通过, "
                    f"target_models={old_model_count}, upstreams_with_format={upstream_with_format}"
                )

                conn.commit()
                logging.info("[Migrations] v1→v2 迁移成功")
            except Exception:
                conn.rollback()
                logging.error("[Migrations] v1→v2 迁移失败，已回滚", exc_info=True)
                raise
            finally:
                conn.execute("PRAGMA foreign_keys = ON")
        finally:
            conn.close()

    def _migrate_v2_to_v3(self, backup_path: Path):
        """执行 v2 → v3 迁移。

        model_routes:
          - proxy_type 重命名为 request_type
          - 数据映射: codex → responses, claude → messages, pass_through → chat_completions
          - CHECK 约束更新为 (responses, messages, chat_completions)
        upstreams:
          - format 列增加 CHECK 约束: (responses, messages, chat_completions)
          - 数据映射: openai_chat → chat_completions, anthropic → messages
        """
        conn = self._connect()
        try:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("BEGIN TRANSACTION")
            try:
                # ── model_routes: 重建表 ──
                old_route_count = conn.execute(
                    "SELECT COUNT(*) FROM model_routes"
                ).fetchone()[0]
                logging.info(
                    f"[Migrations] v2→v3 STEP 1: 原始 model_routes 记录数 = {old_route_count}"
                )

                conn.execute("""
                    CREATE TABLE model_routes_new (
                        id              INTEGER PRIMARY KEY AUTOINCREMENT,
                        source          TEXT NOT NULL CHECK(length(source) > 0),
                        target_model_id INTEGER NOT NULL REFERENCES target_models(id) ON DELETE RESTRICT,
                        request_type    TEXT NOT NULL DEFAULT 'responses'
                                        CHECK(request_type IN ('responses', 'messages', 'chat_completions')),
                        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                        updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
                        UNIQUE(source, request_type)
                    );
                """)
                logging.info("[Migrations] v2→v3 STEP 2: model_routes_new 表创建完成")

                conn.execute("""
                    INSERT INTO model_routes_new
                        (id, source, target_model_id, request_type, created_at, updated_at)
                    SELECT id, source, target_model_id,
                           CASE proxy_type
                               WHEN 'codex' THEN 'responses'
                               WHEN 'claude' THEN 'messages'
                               WHEN 'pass_through' THEN 'chat_completions'
                               ELSE 'responses'
                           END,
                           created_at, updated_at
                    FROM model_routes;
                """)
                logging.info("[Migrations] v2→v3 STEP 3: model_routes 数据复制完成")

                conn.execute("DROP TABLE model_routes;")
                conn.execute(
                    "ALTER TABLE model_routes_new RENAME TO model_routes;"
                )
                logging.info("[Migrations] v2→v3 STEP 4: model_routes 表替换完成")

                new_route_count = conn.execute(
                    "SELECT COUNT(*) FROM model_routes"
                ).fetchone()[0]
                if new_route_count < old_route_count:
                    raise sqlite3.OperationalError(
                        f"model_routes 迁移验证失败: 原有 {old_route_count} 条记录, 现有 {new_route_count} 条记录"
                    )
                logging.info(
                    f"[Migrations] v2→v3 STEP 5: model_routes 验证通过, {new_route_count} 条记录"
                )

                # ── upstreams: 重建表以添加 format CHECK 约束 ──
                old_upstream_count = conn.execute(
                    "SELECT COUNT(*) FROM upstreams"
                ).fetchone()[0]
                logging.info(
                    f"[Migrations] v2→v3 STEP 6: 原始 upstreams 记录数 = {old_upstream_count}"
                )

                conn.execute("""
                    CREATE TABLE upstreams_new (
                        id              TEXT PRIMARY KEY,
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
                    );
                """)
                logging.info("[Migrations] v2→v3 STEP 7: upstreams_new 表创建完成")

                conn.execute("""
                    INSERT INTO upstreams_new
                        (id, base_url, api_key, timeout, connect_timeout,
                         ssl_verify, retry, is_active,
                         format, created_at, updated_at)
                    SELECT id, base_url, api_key, timeout, connect_timeout,
                           ssl_verify, retry, is_active,
                           CASE format
                               WHEN 'openai_chat' THEN 'chat_completions'
                               WHEN 'anthropic' THEN 'messages'
                               ELSE 'chat_completions'
                           END,
                           created_at, updated_at
                    FROM upstreams;
                """)
                logging.info("[Migrations] v2→v3 STEP 8: upstreams 数据复制完成")

                conn.execute("DROP TABLE upstreams;")
                conn.execute(
                    "ALTER TABLE upstreams_new RENAME TO upstreams;"
                )
                logging.info("[Migrations] v2→v3 STEP 9: upstreams 表替换完成")

                new_upstream_count = conn.execute(
                    "SELECT COUNT(*) FROM upstreams"
                ).fetchone()[0]
                if new_upstream_count < old_upstream_count:
                    raise sqlite3.OperationalError(
                        f"upstreams 迁移验证失败: 原有 {old_upstream_count} 条记录, 现有 {new_upstream_count} 条记录"
                    )
                logging.info(
                    f"[Migrations] v2→v3 STEP 10: upstreams 验证通过, {new_upstream_count} 条记录"
                )

                # ── schema_version ──
                conn.execute("DELETE FROM schema_version;")
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (3);"
                )
                logging.info("[Migrations] v2→v3 STEP 11: schema_version 更新为 3")

                conn.commit()
                logging.info("[Migrations] v2→v3 迁移成功")
            except Exception:
                conn.rollback()
                logging.error("[Migrations] v2→v3 迁移失败，已回滚", exc_info=True)
                raise
            finally:
                conn.execute("PRAGMA foreign_keys = ON")
        finally:
            conn.close()

    def _migrate_v3_to_v4(self, backup_path: Path):
        """执行 v3 → v4 迁移（移除 upstreams.is_default 列）。"""
        conn = self._connect()
        try:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("BEGIN TRANSACTION")
            try:
                has_is_default = conn.execute(
                    "SELECT 1 FROM pragma_table_info('upstreams') WHERE name = 'is_default'"
                ).fetchone()
                if not has_is_default:
                    conn.execute("DELETE FROM schema_version;")
                    conn.execute("INSERT INTO schema_version (version) VALUES (4);")
                    conn.commit()
                    logging.info("[Migrations] v3→v4: is_default 列已不存在，直接更新版本号")
                    return

                old_count = conn.execute(
                    "SELECT COUNT(*) FROM upstreams"
                ).fetchone()[0]
                logging.info(f"[Migrations] v3→v4 STEP 1: 原始 upstreams 记录数 = {old_count}")

                conn.execute("""
                    CREATE TABLE upstreams_new (
                        id              TEXT PRIMARY KEY,
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
                    );
                """)
                logging.info("[Migrations] v3→v4 STEP 2: upstreams_new 表创建完成（无 is_default）")

                conn.execute("""
                    INSERT INTO upstreams_new
                        (id, base_url, api_key, timeout, connect_timeout,
                         ssl_verify, retry, is_active, format, created_at, updated_at)
                    SELECT id, base_url, api_key, timeout, connect_timeout,
                           ssl_verify, retry, is_active, format, created_at, updated_at
                    FROM upstreams;
                """)
                logging.info("[Migrations] v3→v4 STEP 3: 数据复制完成")

                new_count = conn.execute(
                    "SELECT COUNT(*) FROM upstreams_new"
                ).fetchone()[0]
                if new_count < old_count:
                    raise sqlite3.OperationalError(
                        f"upstreams 迁移验证失败: 原有 {old_count} 条记录, 现有 {new_count} 条记录"
                    )

                conn.execute("DROP TABLE upstreams;")
                conn.execute("ALTER TABLE upstreams_new RENAME TO upstreams;")
                logging.info("[Migrations] v3→v4 STEP 4: upstreams 表替换完成")

                conn.execute("DELETE FROM schema_version;")
                conn.execute("INSERT INTO schema_version (version) VALUES (4);")
                logging.info("[Migrations] v3→v4 STEP 5: schema_version 更新为 4")

                conn.commit()
                logging.info("[Migrations] v3→v4 迁移成功")
            except Exception:
                conn.rollback()
                logging.error("[Migrations] v3→v4 迁移失败，已回滚", exc_info=True)
                raise
            finally:
                conn.execute("PRAGMA foreign_keys = ON")
        finally:
            conn.close()


class ConfigCache:
    """内存缓存，供 proxy.py 使用。

    不过期，仅在首次访问时加载全部路由。
    通过 reload() 使缓存失效（由页面更新路由时触发 /admin/reload 调用），
    下次请求时自动查数据库重建缓存。
    """

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._routes: dict = {}
        self._loaded_at: float = 0

    def reload(self):
        with self._lock:
            self._loaded_at = 0

    def resolve(self, source_name: str, request_type: str = "responses") -> Optional[dict]:
        with self._lock:
            self._refresh_if_stale()
            key = (source_name, request_type)
            if key in self._routes:
                return self._routes[key]
            return self._routes.get(("*", request_type))

    def get_all(self, request_type: Optional[str] = None) -> dict:
        with self._lock:
            self._refresh_if_stale()
            result = {}
            for (src, pt), cfg in self._routes.items():
                if request_type is not None and pt != request_type:
                    continue
                result[src] = cfg
            return result

    def _refresh_if_stale(self):
        if self._loaded_at > 0:
            return
        try:
            db = ConfigDB(self._db_path)
            try:
                new_routes = {}
                # 加载全部 request_type 的路由，避免不同请求类型间缓存丢失
                all_routes = db.list_routes(request_type=None)
                for route in all_routes:
                    source = route["source"]
                    pt = route.get("request_type", "responses")
                    cfg = db.resolve_one(source, pt)
                    if cfg:
                        new_routes[(source, pt)] = cfg
                self._routes = new_routes
                self._loaded_at = time.time()
            finally:
                db.close()
        except Exception:
            logging.warning("[ConfigCache] 配置缓存加载失败，保留旧缓存", exc_info=True)


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
