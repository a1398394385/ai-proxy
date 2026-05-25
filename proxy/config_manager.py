#!/usr/bin/env python3
"""动态模型配置管理 — ConfigDB（数据库 CRUD）+ ConfigCache（内存缓存）。"""

import sqlite3
import shutil
import threading
import time
import logging
from datetime import datetime
from pathlib import Path
import json
from typing import Optional

from .schema import ensure_table


class ConfigDB:
    """数据数据库操作。每次查询打开新连接（无连接池）。

    参数:
        db_path: data db 路径
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
        """创建数据库和表（幂等）。自动执行未完成的迁移。"""
        conn = self._connect()
        try:
            for t in ('schema_version', 'upstreams', 'upstream_api_keys', 'target_models', 'route_templates', 'model_routes', 'agent_routes'):
                ensure_table(conn, t)

            # 新数据库写入 schema_version = 10（幂等）
            row = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()
            if row[0] == 0:
                conn.execute("INSERT INTO schema_version (version) VALUES (10)")
            conn.commit()
        finally:
            conn.close()
        # 自动执行未完成的迁移
        try:
            mg = Migrations(self.db_path)
            s = mg.status()
            if not s["migrated"]:
                logging.info(f"[ConfigDB] 自动执行数据库迁移: {s['details']}")
                result = mg.migrate()
                if result["status"] == "ok":
                    logging.info(f"[ConfigDB] 迁移成功 → v{result['version']}")
                else:
                    logging.error(f"[ConfigDB] 迁移失败: {result}")
        except Exception as e:
            logging.error(f"[ConfigDB] 迁移检查异常: {e}")

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
            sql += " ORDER BY name"
            return [dict(r) for r in conn.execute(sql).fetchall()]
        finally:
            conn.close()

    def get_upstream(self, upstream_id: int) -> Optional[dict]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM upstreams WHERE id = ?", (upstream_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def add_upstream(self, data: dict) -> int:
        conn = self._connect()
        try:
            cursor = conn.execute(
                """INSERT INTO upstreams (name, base_url, api_key, timeout,
                   connect_timeout, ssl_verify, retry, format)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    data["name"],
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
            return cursor.lastrowid
        finally:
            conn.close()

    def update_upstream(self, upstream_id: int, data: dict):
        conn = self._connect()
        try:
            fields = []
            values = []
            for key in ("name", "base_url", "api_key", "timeout",
                        "connect_timeout", "ssl_verify", "retry", "format",
                        "is_active", "key_cooldown_secs"):
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

    def disable_upstream(self, upstream_id: int):
        self.update_upstream(upstream_id, {"is_active": 0})

    def upstream_active_routes(self, upstream_id: int) -> list:
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

    def delete_upstream_with_models(self, upstream_id: int):
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

    # ─── 上游 API Key CRUD ─────────────────────────────────────────

    @staticmethod
    def _mask_key(api_key: str) -> str:
        if len(api_key) <= 4:
            return api_key
        return "****" + api_key[-4:]

    def list_upstream_keys(self, upstream_id: int) -> list:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, upstream_id, api_key, label, is_active, created_at"
                " FROM upstream_api_keys WHERE upstream_id = ? ORDER BY id",
                (upstream_id,),
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                result.append(d)
            return result
        finally:
            conn.close()

    def add_upstream_key(self, upstream_id: int, api_key: str, label: str = "") -> int:
        conn = self._connect()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM upstream_api_keys WHERE upstream_id = ?",
                (upstream_id,),
            ).fetchone()[0]
            if count >= 20:
                raise ValueError("每个上游最多配置 20 个 key")
            cursor = conn.execute(
                "INSERT INTO upstream_api_keys (upstream_id, api_key, label) VALUES (?, ?, ?)",
                (upstream_id, api_key, label),
            )
            conn.commit()
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            raise ValueError("该 key 已存在于此上游")
        finally:
            conn.close()

    def update_upstream_key(self, key_id: int, data: dict):
        conn = self._connect()
        try:
            fields = []
            values = []
            for key in ("label", "is_active"):
                if key in data:
                    fields.append(f"{key} = ?")
                    values.append(data[key])
            if not fields:
                return
            values.append(key_id)
            conn.execute(
                f"UPDATE upstream_api_keys SET {', '.join(fields)} WHERE id = ?",
                values,
            )
            conn.commit()
        finally:
            conn.close()

    def delete_upstream_key(self, key_id: int):
        conn = self._connect()
        try:
            conn.execute("DELETE FROM upstream_api_keys WHERE id = ?", (key_id,))
            conn.commit()
        finally:
            conn.close()

    def get_first_active_key(self, upstream_id: int) -> str:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT api_key FROM upstream_api_keys"
                " WHERE upstream_id = ? AND is_active = 1 ORDER BY id LIMIT 1",
                (upstream_id,),
            ).fetchone()
            return row["api_key"] if row else ""
        finally:
            conn.close()

    def list_models(self, upstream_id: Optional[int] = None):
        conn = self._connect()
        try:
            sql = """SELECT tm.id, tm.name, tm.upstream_id, tm.multimodal,
                            tm.max_context, tm.max_input, tm.max_output, tm.rpm, tm.created_at,
                            u.name AS upstream_name
                     FROM target_models tm
                     JOIN upstreams u ON tm.upstream_id = u.id"""
            params = []
            if upstream_id is not None:
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
                """SELECT tm.id, tm.name, tm.upstream_id, tm.multimodal,
                            tm.max_context, tm.max_input, tm.max_output, tm.rpm, tm.created_at,
                            u.name AS upstream_name
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
                """INSERT INTO target_models (name, upstream_id, multimodal,
                           max_context, max_input, max_output, rpm)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    data["name"],
                    data["upstream_id"],
                    data.get("multimodal", 1),
                    data.get("max_context"),
                    data.get("max_input"),
                    data.get("max_output"),
                    data.get("rpm"),
                ),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def add_models_bulk(self, upstream_id: int, models: list) -> dict:
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
            for key in ("name", "upstream_id", "multimodal", "max_context", "max_input", "max_output", "rpm"):
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
        """删除模型。FK ON DELETE SET NULL 自动将关联路由的 target_model_id 设为 NULL。
        check_refs 参数保留以兼容调用方，不再阻塞删除。"""
        conn = self._connect()
        try:
            # 记录受影响的路由数（用于前端提示）
            affected = conn.execute(
                "SELECT COUNT(*) FROM model_routes WHERE target_model_id = ?",
                (model_id,),
            ).fetchone()[0] + conn.execute(
                "SELECT COUNT(*) FROM agent_routes WHERE target_model_id = ?",
                (model_id,),
            ).fetchone()[0]
            conn.execute("DELETE FROM target_models WHERE id = ?", (model_id,))
            conn.commit()
            msg = "Deleted"
            if affected > 0:
                msg += f", {affected} 条路由已标记为失效"
            return {"message": msg, "affected_routes": affected}
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
                          u.name as upstream_name,
                          u.is_active as upstream_active,
                          u.format as upstream_format
                   FROM model_routes mr
                   LEFT JOIN target_models tm ON mr.target_model_id = tm.id
                   LEFT JOIN upstreams u ON tm.upstream_id = u.id
                   {where}
                   ORDER BY
                     CASE mr.source WHEN '*' THEN 0 ELSE 1 END,
                     mr.source""",
                params,
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                if d.get("target_model_id") is None:
                    d["target_name"] = None
                    d["upstream_active"] = 0
                    d["upstream_name"] = "(已删除)"
                    d["upstream_format"] = None
                result.append(d)
            return result
        finally:
            conn.close()

    def get_route(self, route_id: int) -> Optional[dict]:
        conn = self._connect()
        try:
            row = conn.execute(
                """SELECT mr.*, tm.name as target_name, tm.upstream_id
                   FROM model_routes mr
                   LEFT JOIN target_models tm ON mr.target_model_id = tm.id
                   WHERE mr.id = ?""",
                (route_id,),
            ).fetchone()
            d = dict(row) if row else None
            if d and d.get("target_model_id") is None:
                d["target_name"] = None
            return d
        finally:
            conn.close()

    def add_route(self, data: dict, allow_null_target: bool = False) -> int:
        request_type = data.get("request_type", "responses")
        if request_type not in ("responses", "messages", "chat_completions"):
            raise ValueError("request_type must be one of: responses, messages, chat_completions")
        target_model_id = data.get("target_model_id")
        if target_model_id is not None:
            target_model_id = int(target_model_id)
        conn = self._connect()
        try:
            if target_model_id is not None:
                # 校验目标模型所属上游是否活跃
                active = conn.execute(
                    """SELECT 1 FROM target_models tm
                       JOIN upstreams u ON tm.upstream_id = u.id
                       WHERE tm.id = ? AND u.is_active = 1""",
                    (target_model_id,),
                ).fetchone()
                if not active:
                    raise ValueError("目标模型不存在或所属上游已禁用")
            elif not allow_null_target:
                raise ValueError("target_model_id 不能为空")
            cursor = conn.execute(
                "INSERT INTO model_routes (source, target_model_id, request_type) VALUES (?, ?, ?)",
                (data["source"], target_model_id, request_type),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def update_route(self, route_id: int, data: dict):
        conn = self._connect()
        try:
            # 保护默认路由：不允许修改 source
            if data.get("source") and data["source"] != "*":
                existing_source = conn.execute(
                    "SELECT source FROM model_routes WHERE id = ?",
                    (route_id,),
                ).fetchone()
                if existing_source and existing_source["source"] == "*":
                    raise ValueError("不能修改默认路由的源模型名")
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
                active = conn.execute(
                    "SELECT 1 FROM target_models tm"
                    " JOIN upstreams u ON tm.upstream_id = u.id"
                    " WHERE tm.id = ? AND u.is_active = 1",
                    (target_model_id,),
                ).fetchone()
                if not active:
                    raise ValueError("目标模型不存在或所属上游已禁用")
            elif "target_model_id" in data:
                # 显式设为 NULL（重关联失效路由时允许设为 NULL）
                pass

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

    # ─── Agent 路由 CRUD ───────────────────────────────────────

    def list_agent_routes(self, request_type: Optional[str] = None):
        conn = self._connect()
        try:
            params = []
            where = ""
            if request_type is not None:
                where = "WHERE ar.request_type = ?"
                params.append(request_type)
            rows = conn.execute(
                "SELECT ar.*, tm.name as target_name, tm.upstream_id,"
                " u.name as upstream_name,"
                " u.is_active as upstream_active,"
                " u.format as upstream_format"
                " FROM agent_routes ar"
                " LEFT JOIN target_models tm ON ar.target_model_id = tm.id"
                " LEFT JOIN upstreams u ON tm.upstream_id = u.id"
                " " + where + " ORDER BY ar.source",
                params,
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                if d.get("target_model_id") is None:
                    d["target_name"] = None
                    d["upstream_active"] = 0
                    d["upstream_name"] = "(已删除)"
                    d["upstream_format"] = None
                result.append(d)
            return result
        finally:
            conn.close()

    def get_agent_route(self, route_id: int) -> Optional[dict]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT ar.*, tm.name as target_name, tm.upstream_id"
                " FROM agent_routes ar"
                " LEFT JOIN target_models tm ON ar.target_model_id = tm.id"
                " WHERE ar.id = ?",
                (route_id,),
            ).fetchone()
            d = dict(row) if row else None
            if d and d.get("target_model_id") is None:
                d["target_name"] = None
            return d
        finally:
            conn.close()

    def add_agent_route(self, data: dict, allow_null_target: bool = False) -> int:
        source = data.get("source", "")
        if source == "*":
            raise ValueError("agent_routes 不允许 source='*'")
        request_type = data.get("request_type", "chat_completions")
        if request_type not in ("responses", "messages", "chat_completions"):
            raise ValueError("request_type must be one of: responses, messages, chat_completions")
        target_model_id = data.get("target_model_id")
        if target_model_id is not None:
            target_model_id = int(target_model_id)
        conn = self._connect()
        try:
            if target_model_id is not None:
                active = conn.execute(
                    "SELECT 1 FROM target_models tm"
                    " JOIN upstreams u ON tm.upstream_id = u.id"
                    " WHERE tm.id = ? AND u.is_active = 1",
                    (target_model_id,),
                ).fetchone()
                if not active:
                    raise ValueError("目标模型不存在或所属上游已禁用")
            elif not allow_null_target:
                raise ValueError("target_model_id 不能为空")
            cursor = conn.execute(
                "INSERT INTO agent_routes (source, target_model_id, request_type) VALUES (?, ?, ?)",
                (source, target_model_id, request_type),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def update_agent_route(self, route_id: int, data: dict):
        conn = self._connect()
        try:
            if data.get("source") == "*":
                raise ValueError("agent_routes 不允许 source='*'")
            fields = []
            values = []
            for key in ("source", "target_model_id", "request_type"):
                if key in data:
                    if key == "request_type" and data[key] not in ("responses", "messages", "chat_completions"):
                        raise ValueError("request_type must be one of: responses, messages, chat_completions")
                    fields.append(str(key) + " = ?")
                    values.append(data[key])
            if not fields:
                return
            target_model_id = data.get("target_model_id")
            if target_model_id is not None:
                active = conn.execute(
                    "SELECT 1 FROM target_models tm"
                    " JOIN upstreams u ON tm.upstream_id = u.id"
                    " WHERE tm.id = ? AND u.is_active = 1",
                    (target_model_id,),
                ).fetchone()
                if not active:
                    raise ValueError("目标模型不存在或所属上游已禁用")
            elif "target_model_id" in data:
                # 显式设为 NULL
                pass
            fields.append("updated_at = datetime('now')")
            values.append(route_id)
            conn.execute(
                "UPDATE agent_routes SET " + ", ".join(fields) + " WHERE id = ?",
                values,
            )
            conn.commit()
        finally:
            conn.close()



    def delete_agent_route(self, route_id: int):
        conn = self._connect()
        try:
            conn.execute("DELETE FROM agent_routes WHERE id = ?", (route_id,))
            conn.commit()
        finally:
            conn.close()

    def resolve_agent(self, source_name: str, request_type: str) -> Optional[dict]:
        """精确匹配一条 agent 路由，无默认路由回退。上游禁用返回 None。"""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT tm.name as target_name, tm.multimodal, u.format,"
                " u.id as upstream_id, u.name as upstream_name, u.base_url, u.api_key,"
                " u.timeout, u.connect_timeout, u.ssl_verify, u.retry"
                " FROM agent_routes ar"
                " JOIN target_models tm ON ar.target_model_id = tm.id"
                " JOIN upstreams u ON tm.upstream_id = u.id"
                " WHERE ar.source = ? AND ar.request_type = ? AND u.is_active = 1",
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
                    "name": d["upstream_name"],
                    "base_url": d["base_url"],
                    "api_key": d["api_key"],
                    "timeout": d["timeout"],
                    "connect_timeout": d["connect_timeout"],
                    "ssl_verify": bool(d["ssl_verify"]),
                    "retry": d["retry"],
                    "format": d["format"],
                },
            }
        finally:
            conn.close()

    def get_all_agent_routes(self, request_type: Optional[str] = None) -> dict:
        conn = self._connect()
        try:
            params = []
            where = "WHERE u.is_active = 1"
            if request_type is not None:
                where += " AND ar.request_type = ?"
                params.append(request_type)
            rows = conn.execute(
                "SELECT ar.source, ar.request_type, tm.name as target_name, tm.multimodal,"
                " u.format, tm.upstream_id"
                " FROM agent_routes ar"
                " JOIN target_models tm ON ar.target_model_id = tm.id"
                " JOIN upstreams u ON tm.upstream_id = u.id"
                " " + where + " ORDER BY ar.source",
                params,
            ).fetchall()
            result = {}
            for r in rows:
                result[r["source"]] = dict(r)
            return result
        finally:
            conn.close()

    # ─── 配置查询（供 proxy 使用）────────────────────────────────

    def resolve_model(self, source_name: str, request_type: str = "responses") -> Optional[dict]:
        """返回值约定：
        - 找到可用匹配（路由存在 + 上游 is_active=1）→ 返回完整配置 dict
        - 匹配到但上游禁用 → 跳过，继续尝试 "*" 默认路由
        - "*" 也找不到或也禁用 → 返回 None
        """
        for name in (source_name, "*"):
            row = self.resolve_one(name, request_type)
            if row is not None:
                return row
        return None

    def resolve_one(self, source_name: str, request_type: str = "responses") -> Optional[dict]:
        """精确匹配单个 source 的路由配置，无默认路由回退（供 ConfigCache 内部使用）。"""
        conn = self._connect()
        try:
            row = conn.execute(
                """SELECT tm.name as target_name, tm.multimodal, u.format,
                          u.id as upstream_id, u.name as upstream_name, u.base_url, u.api_key,
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
                    "name": d["upstream_name"],
                    "base_url": d["base_url"],
                    "api_key": d["api_key"],
                    "timeout": d["timeout"],
                    "connect_timeout": d["connect_timeout"],
                    "ssl_verify": bool(d["ssl_verify"]),
                    "retry": d["retry"],
                    "format": d["format"],
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

    def validate_star_default(self, request_type: str = "responses") -> bool:
        return self.resolve_model("*", request_type) is not None

    def get_counts(self) -> dict:
        conn = self._connect()
        try:
            upstreams = conn.execute(
                "SELECT COUNT(*) FROM upstreams WHERE is_active = 1"
            ).fetchone()[0]
            models = conn.execute("SELECT COUNT(*) FROM target_models").fetchone()[0]
            routes = conn.execute("SELECT COUNT(*) FROM model_routes").fetchone()[0]
            agent_routes = conn.execute("SELECT COUNT(*) FROM agent_routes").fetchone()[0]
            return {"upstreams": upstreams, "models": models, "routes": routes, "agent_routes": agent_routes}
        finally:
            conn.close()



    # ─── 路由模板 CRUD ──────────────────────────────────────────

    def list_templates(self, request_type: Optional[str] = None) -> list:
        """返回模板列表（不含 items 字段，用于边栏展示）。"""
        conn = self._connect()
        try:
            if request_type is not None:
                rows = conn.execute(
                    "SELECT id, name, request_type, created_at, updated_at, last_applied_at"
                    " FROM route_templates WHERE request_type = ? ORDER BY updated_at DESC",
                    (request_type,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, name, request_type, created_at, updated_at, last_applied_at"
                    " FROM route_templates ORDER BY request_type, updated_at DESC"
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_template(self, template_id: int) -> Optional[dict]:
        """获取单个模板（含 items JSON）。"""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM route_templates WHERE id = ?",
                (template_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def _resolve_template_items(self, items: dict, conn) -> tuple:
        """辅助方法：展开模板 items 中的 model_routes 和 agent_routes，LEFT JOIN 获取状态。"""
        model_routes = []
        for item in items.get("model_routes", []):
            row = conn.execute(
                "SELECT tm.id, tm.name as target_name, u.name as upstream_name,"
                " u.is_active as upstream_active, u.format as upstream_format"
                " FROM target_models tm"
                " JOIN upstreams u ON tm.upstream_id = u.id"
                " WHERE tm.id = ?",
                (item["target_model_id"],),
            ).fetchone()
            if row:
                d = dict(row)
                d["valid"] = True
                d["source"] = item["source"]
            else:
                d = {
                    "source": item["source"],
                    "target_model_id": item["target_model_id"],
                    "target_name": None,
                    "upstream_name": "(已删除)",
                    "upstream_active": 0,
                    "upstream_format": None,
                    "valid": False,
                }
            model_routes.append(d)
        agent_routes = []
        for item in items.get("agent_routes", []):
            row = conn.execute(
                "SELECT tm.id, tm.name as target_name, u.name as upstream_name,"
                " u.is_active as upstream_active, u.format as upstream_format"
                " FROM target_models tm"
                " JOIN upstreams u ON tm.upstream_id = u.id"
                " WHERE tm.id = ?",
                (item["target_model_id"],),
            ).fetchone()
            if row:
                d = dict(row)
                d["valid"] = True
                d["source"] = item["source"]
            else:
                d = {
                    "source": item["source"],
                    "target_model_id": item["target_model_id"],
                    "target_name": None,
                    "upstream_name": "(已删除)",
                    "upstream_active": 0,
                    "upstream_format": None,
                    "valid": False,
                }
            agent_routes.append(d)
        return model_routes, agent_routes

    def get_template_preview(self, template_id: int) -> Optional[dict]:
        """解析模板 items JSON，LEFT JOIN target_models/upstreams 返回展开预览。"""
        tmpl = self.get_template(template_id)
        if not tmpl:
            return None
        try:
            items = json.loads(tmpl["items"])
        except (json.JSONDecodeError, TypeError):
            items = {"model_routes": [], "agent_routes": []}
        conn = self._connect()
        try:
            model_routes, agent_routes = self._resolve_template_items(items, conn)
        finally:
            conn.close()
        return {
            "id": tmpl["id"],
            "name": tmpl["name"],
            "request_type": tmpl["request_type"],
            "model_routes": model_routes,
            "agent_routes": agent_routes,
            "created_at": tmpl["created_at"],
            "updated_at": tmpl["updated_at"],
            "last_applied_at": tmpl["last_applied_at"],
        }


    def save_template(self, data: dict) -> int:
        """从当前路由快照创建模板。"""
        request_type = data.get("request_type", "chat_completions")
        name = data.get("name", "").strip()
        if not name or len(name) > 100:
            raise ValueError("模板名称不能为空且不超过 100 字符")
        if "/" in name:
            raise ValueError("模板名称不能包含 /")

        conn = self._connect()
        try:
            # 获取当前路由快照
            model_routes = conn.execute(
                "SELECT source, target_model_id FROM model_routes WHERE request_type = ?",
                (request_type,),
            ).fetchall()
            agent_routes = conn.execute(
                "SELECT source, target_model_id FROM agent_routes WHERE request_type = ?",
                (request_type,),
            ).fetchall()
            items = json.dumps({
                "model_routes": [{"source": r["source"], "target_model_id": r["target_model_id"]} for r in model_routes],
                "agent_routes": [{"source": r["source"], "target_model_id": r["target_model_id"]} for r in agent_routes],
            })
            cursor = conn.execute(
                "INSERT INTO route_templates (name, request_type, items) VALUES (?, ?, ?)",
                (name, request_type, items),
            )
            conn.commit()
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            raise ValueError(f"模板名称 '{name}' 在该请求类型下已存在")
        finally:
            conn.close()

    def update_template(self, template_id: int, data: dict):
        """更新模板名称或 items。支持 resnapshot=True 从当前路由快照重建 items。"""
        fields = []
        values = []
        if "name" in data:
            name = data["name"].strip()
            if not name or len(name) > 100:
                raise ValueError("模板名称不能为空且不超过 100 字符")
            if "/" in name:
                raise ValueError("模板名称不能包含 /")
            fields.append("name = ?")
            values.append(name)
        if data.get("resnapshot"):
            # 从当前路由快照重建 items
            request_type = data.get("request_type")
            if not request_type:
                # 从现有模板读取 request_type
                tmpl = self.get_template(template_id)
                if tmpl:
                    request_type = tmpl["request_type"]
            conn = self._connect()
            try:
                mr = conn.execute(
                    "SELECT source, target_model_id FROM model_routes WHERE request_type = ?",
                    (request_type,),
                ).fetchall()
                ar = conn.execute(
                    "SELECT source, target_model_id FROM agent_routes WHERE request_type = ?",
                    (request_type,),
                ).fetchall()
                items_str = json.dumps({
                    "model_routes": [{"source": r["source"], "target_model_id": r["target_model_id"]} for r in mr],
                    "agent_routes": [{"source": r["source"], "target_model_id": r["target_model_id"]} for r in ar],
                })
                fields.append("items = ?")
                values.append(items_str)
            finally:
                conn.close()
        if "items" in data and not data.get("resnapshot"):
            item_data = data["items"]
            if isinstance(item_data, dict):
                items_str = json.dumps(item_data)
            else:
                items_str = item_data
            try:
                json.loads(items_str)
            except (json.JSONDecodeError, TypeError):
                raise ValueError("items 必须为有效 JSON")
            fields.append("items = ?")
            values.append(items_str)
        if not fields:
            return
        fields.append("updated_at = datetime('now')")
        values.append(template_id)
        conn = self._connect()
        try:
            try:
                conn.execute(
                    f"UPDATE route_templates SET {', '.join(fields)} WHERE id = ?",
                    values,
                )
            except sqlite3.IntegrityError:
                raise ValueError("模板名称已存在")
            conn.commit()
        finally:
            conn.close()

    def delete_template(self, template_id: int):
        """删除模板（不影响当前路由）。"""
        conn = self._connect()
        try:
            conn.execute("DELETE FROM route_templates WHERE id = ?", (template_id,))
            conn.commit()
        finally:
            conn.close()

    def apply_template(self, template_id: int) -> dict:
        """原子替换当前该 request_type 的全部路由。返回 {applied, skipped_invalid}。"""
        tmpl = self.get_template(template_id)
        if not tmpl:
            raise ValueError("模板不存在")
        request_type = tmpl["request_type"]
        try:
            items = json.loads(tmpl["items"])
        except (json.JSONDecodeError, TypeError):
            raise ValueError("模板数据损坏，无法解析")
        model_routes_items = items.get("model_routes", [])
        agent_routes_items = items.get("agent_routes", [])

        conn = self._connect()
        try:
            conn.execute("BEGIN TRANSACTION")
            try:
                # 删除当前 request_type 的全部路由
                conn.execute("DELETE FROM model_routes WHERE request_type = ?", (request_type,))
                conn.execute("DELETE FROM agent_routes WHERE request_type = ?", (request_type,))

                skipped = 0
                # 插入主路由
                for item in model_routes_items:
                    target_id = item.get("target_model_id")
                    if target_id is not None:
                        exists = conn.execute(
                            "SELECT 1 FROM target_models WHERE id = ?",
                            (target_id,),
                        ).fetchone()
                        if not exists:
                            target_id = None
                            skipped += 1
                    conn.execute(
                        "INSERT INTO model_routes (source, target_model_id, request_type) VALUES (?, ?, ?)",
                        (item["source"], target_id, request_type),
                    )
                # 插入 Agent 路由
                for item in agent_routes_items:
                    target_id = item.get("target_model_id")
                    if target_id is not None:
                        exists = conn.execute(
                            "SELECT 1 FROM target_models WHERE id = ?",
                            (target_id,),
                        ).fetchone()
                        if not exists:
                            target_id = None
                            skipped += 1
                    conn.execute(
                        "INSERT INTO agent_routes (source, target_model_id, request_type) VALUES (?, ?, ?)",
                        (item["source"], target_id, request_type),
                    )
                # 更新 last_applied_at
                conn.execute(
                    "UPDATE route_templates SET last_applied_at = datetime('now') WHERE id = ?",
                    (template_id,),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        finally:
            conn.close()

        return {
            "applied": len(model_routes_items) + len(agent_routes_items),
            "invalid_count": skipped,
        }



class Migrations:
    """数据库迁移管理 — 将 model_routes 表从 v0 升级到 v5（upstreams INTEGER PK + name）。

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
        """返回当前迁移状态。version 0 = 未迁移，version >= 5 = 已迁移。"""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT version FROM schema_version LIMIT 1"
            ).fetchone()
            version = row["version"] if row else 0
            if version == 0:
                # 可能是真正的 v0，也可能是 _ensure_db() 创建的新 v5 数据库但没有 schema_version 行（旧版）
                has_name_col = conn.execute(
                    "SELECT 1 FROM pragma_table_info('upstreams') WHERE name = 'name'"
                ).fetchone()
                if has_name_col:
                    return {
                        "migrated": True,
                        "version": 5,
                        "details": "已迁移到 v5: 数据库由最新的 _ensure_db() 直接创建",
                    }
                has_request_type = conn.execute(
                    "SELECT 1 FROM pragma_table_info('model_routes') WHERE name = 'request_type'"
                ).fetchone()
                if has_request_type:
                    return {
                        "migrated": False,
                        "version": 4,
                        "details": "需要执行迁移: upstreams.id 需要改为 INTEGER，新增 name 列",
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
            if version == 4:
                return {
                    "migrated": False,
                    "version": 4,
                    "details": "需要执行迁移: upstreams.id 需要改为 INTEGER，新增 name 列",
                }
            if version == 5:
                # 检查 model_routes 的 FK 是否错误地引用了 target_models_old
                row = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='model_routes'"
                ).fetchone()
                if row and "target_models_old" in row["sql"]:
                    return {
                        "migrated": False,
                        "version": 5,
                        "details": "需要执行迁移: model_routes 外键修复",
                    }
                return {
                    "migrated": True,
                    "version": 5,
                    "details": "已迁移到 v5: model_routes 外键正确",
                }
            if version == 6:
                return {
                    "migrated": False,
                    "version": 6,
                    "details": "需要执行迁移: 新增 agent_routes 表",
                }
            if version == 7:
                return {
                    "migrated": False,
                    "version": 7,
                    "details": "需要执行迁移: FK ON DELETE RESTRICT → SET NULL，新增 route_templates 表",
                }
            if version == 8:
                # 验证 route_templates 表存在
                has_templates = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='route_templates'"
                ).fetchone()
                if not has_templates:
                    return {
                        "migrated": False,
                        "version": 7,
                        "details": "需要执行迁移: 缺少 route_templates 表",
                    }
                # 验证 target_models 是否有新字段（max_context）
                has_max_context = conn.execute(
                    "SELECT 1 FROM pragma_table_info('target_models') WHERE name = 'max_context'"
                ).fetchone()
                if not has_max_context:
                    return {
                        "migrated": False,
                        "version": 8,
                        "details": "需要执行迁移: target_models 表缺少 max_context/max_input/max_output/rpm 列",
                    }
                return {
                    "migrated": True,
                    "version": 9,
                    "details": "已迁移到 v9: target_models 新增 max_context/max_input/max_output/rpm 列",
                }
            if version == 9:
                # 额外校验：target_models 是否缺少 max_context（可能有数据库在迁移 v8→v9 之前就跳到了 v9）
                has_max_context = conn.execute(
                    "SELECT 1 FROM pragma_table_info('target_models') WHERE name = 'max_context'"
                ).fetchone()
                if not has_max_context:
                    return {
                        "migrated": False,
                        "version": 8,
                        "details": "需要执行迁移: target_models 表缺少 max_context/max_input/max_output/rpm 列",
                    }
                has_keys_table = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='upstream_api_keys'"
                ).fetchone()
                if not has_keys_table:
                    return {
                        "migrated": False,
                        "version": 9,
                        "details": "需要执行迁移: 新增 upstream_api_keys 表 + key_cooldown_secs 列",
                    }
                # 表已存在，但检查数据是否已迁移（upstreams.api_key 是否已清空）
                remaining = conn.execute(
                    "SELECT COUNT(*) FROM upstreams WHERE api_key != ''"
                ).fetchone()[0]
                if remaining > 0:
                    return {
                        "migrated": False,
                        "version": 9,
                        "details": f"需要执行迁移: {remaining} 个上游仍有 api_key 待迁移到 upstream_api_keys",
                    }
                # 数据已迁移，确认 key_cooldown_secs 列
                has_cooldown = conn.execute(
                    "SELECT 1 FROM pragma_table_info('upstreams') WHERE name = 'key_cooldown_secs'"
                ).fetchone()
                if not has_cooldown:
                    return {
                        "migrated": False,
                        "version": 9,
                        "details": "需要执行迁移: upstreams 缺少 key_cooldown_secs 列",
                    }
                return {
                    "migrated": True,
                    "version": 10,
                    "details": "已迁移到 v10: upstream_api_keys 表 + key_cooldown_secs 列",
                }
            # fallback：未知或更高版本的数据库
            # 校验 target_models 是否缺少 max_context
            has_max_context = conn.execute(
                "SELECT 1 FROM pragma_table_info('target_models') WHERE name = 'max_context'"
            ).fetchone()
            if not has_max_context:
                return {
                    "migrated": False,
                    "version": 8,
                    "details": "需要执行迁移: target_models 表缺少 max_context/max_input/max_output/rpm 列",
                }
            # 额外校验 keys 迁移是否已完成（兼容数据库 version 被意外设为 >= 10 但 v9→v10 未实际执行的情况）
            has_keys_table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='upstream_api_keys'"
            ).fetchone()
            if not has_keys_table:
                return {
                    "migrated": False,
                    "version": 9,
                    "details": "需要执行迁移: 新增 upstream_api_keys 表 + key_cooldown_secs 列",
                }
            remaining = conn.execute(
                "SELECT COUNT(*) FROM upstreams WHERE api_key != ''"
            ).fetchone()[0]
            if remaining > 0:
                return {
                    "migrated": False,
                    "version": 9,
                    "details": f"需要执行迁移: {remaining} 个上游仍有 api_key 待迁移到 upstream_api_keys",
                }
            has_cooldown = conn.execute(
                "SELECT 1 FROM pragma_table_info('upstreams') WHERE name = 'key_cooldown_secs'"
            ).fetchone()
            if not has_cooldown:
                return {
                    "migrated": False,
                    "version": 9,
                    "details": "需要执行迁移: upstreams 缺少 key_cooldown_secs 列",
                }
            return {
                "migrated": True,
                "version": version,
                "details": f"已迁移到 v{version}: upstream_api_keys / max_context / format / PK 已更新",
            }
        finally:
            conn.close()

    def migrate(self) -> dict:
        """执行迁移（幂等）。v0 → v1 → v2 → v3 → v4 → v5 按序执行。"""
        s = self.status()
        if s["migrated"]:
            logging.info(f"[Migrations] 数据库已是最新版本 v{s['version']}，跳过迁移")
            return {"status": "already_migrated", "details": s["details"]}

        version = s["version"]

        # STEP 1: 备份现有数据库（非致命——Windows 上可能因文件锁定/权限失败）
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup_name = f"{self.db_path.stem}.bak.{timestamp}{self.db_path.suffix}"
        backup_path = self.db_path.parent / backup_name
        try:
            shutil.copy2(self.db_path, backup_path)
            logging.info(f"[Migrations] STEP 1: 备份完成 -> {backup_path}")
        except Exception as e:
            logging.warning(f"[Migrations] STEP 1: 备份失败，继续迁移: {e}")
            backup_path = None

        if version == 0:
            self._migrate_v0_to_v1(backup_path)
        if version <= 1:
            self._migrate_v1_to_v2(backup_path)
        if version <= 2:
            self._migrate_v2_to_v3(backup_path)
        if version <= 3:
            self._migrate_v3_to_v4(backup_path)
        if version <= 4:
            self._migrate_v4_to_v5(backup_path)
        if version <= 5:
            self._migrate_v5_to_v6(backup_path)
        if version <= 6:
            self._migrate_v6_to_v7(backup_path)
        if version <= 7:
            self._migrate_v7_to_v8(backup_path)
        if version <= 8:
            self._migrate_v8_to_v9(backup_path)
        if version <= 9:
            self._migrate_v9_to_v10(backup_path)

        return {
            "status": "ok",
            "version": 10,
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

    def _migrate_v4_to_v5(self, backup_path: Path):
        """执行 v4 → v5 迁移（upstreams.id → INTEGER AUTOINCREMENT，新增 name 列）。"""
        conn = self._connect()
        try:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("BEGIN TRANSACTION")
            try:
                # 检查 id 是否已为 INTEGER
                col_type = conn.execute(
                    "SELECT type FROM pragma_table_info('upstreams') WHERE name = 'id'"
                ).fetchone()
                if col_type and col_type[0].upper() == 'INTEGER':
                    conn.execute("DELETE FROM schema_version;")
                    conn.execute("INSERT INTO schema_version (version) VALUES (5);")
                    conn.commit()
                    logging.info("[Migrations] v4→v5: upstreams.id 已为 INTEGER，直接更新版本号")
                    return

                # ── upstreams: rename old, create new, copy data ──
                old_upstream_count = conn.execute(
                    "SELECT COUNT(*) FROM upstreams"
                ).fetchone()[0]
                logging.info(
                    f"[Migrations] v4→v5 STEP 1: 原始 upstreams 记录数 = {old_upstream_count}"
                )

                conn.execute("ALTER TABLE upstreams RENAME TO upstreams_old;")
                logging.info("[Migrations] v4→v5 STEP 2: upstreams 重命名为 upstreams_old")

                ensure_table(conn, 'upstreams')
                logging.info("[Migrations] v4→v5 STEP 3: 新 upstreams 表创建完成")

                conn.execute("""
                    INSERT INTO upstreams
                        (name, base_url, api_key, timeout, connect_timeout,
                         ssl_verify, retry, is_active,
                         format, created_at, updated_at)
                    SELECT id, base_url, api_key, timeout, connect_timeout,
                           ssl_verify, retry, is_active,
                           CASE format
                               WHEN 'openai_chat' THEN 'chat_completions'
                               ELSE format
                           END,
                           created_at, updated_at
                    FROM upstreams_old;
                """)
                logging.info("[Migrations] v4→v5 STEP 4: upstreams 数据复制完成")

                new_upstream_count = conn.execute(
                    "SELECT COUNT(*) FROM upstreams"
                ).fetchone()[0]
                if new_upstream_count < old_upstream_count:
                    raise sqlite3.OperationalError(
                        f"upstreams 迁移验证失败: 原有 {old_upstream_count} 条记录, "
                        f"现有 {new_upstream_count} 条记录"
                    )
                logging.info(
                    f"[Migrations] v4→v5 STEP 5: upstreams 验证通过, {new_upstream_count} 条记录"
                )

                # ── target_models: rename old, create new, copy with FK mapping ──
                old_model_count = conn.execute(
                    "SELECT COUNT(*) FROM target_models"
                ).fetchone()[0]
                logging.info(
                    f"[Migrations] v4→v5 STEP 6: 原始 target_models 记录数 = {old_model_count}"
                )

                conn.execute("ALTER TABLE target_models RENAME TO target_models_old;")
                logging.info("[Migrations] v4→v5 STEP 7: target_models 重命名为 target_models_old")

                ensure_table(conn, 'target_models')
                logging.info("[Migrations] v4→v5 STEP 8: 新 target_models 表创建完成")

                conn.execute("""
                    INSERT INTO target_models
                        (id, name, upstream_id, multimodal, created_at)
                    SELECT tm.id, tm.name, u.id, tm.multimodal, tm.created_at
                    FROM target_models_old tm
                    JOIN upstreams_old uo ON uo.id = tm.upstream_id
                    JOIN upstreams u ON u.name = uo.id;
                """)
                logging.info("[Migrations] v4→v5 STEP 9: target_models 数据复制完成（FK 映射完成）")

                new_model_count = conn.execute(
                    "SELECT COUNT(*) FROM target_models"
                ).fetchone()[0]
                if new_model_count < old_model_count:
                    raise sqlite3.OperationalError(
                        f"target_models 迁移验证失败: 原有 {old_model_count} 条记录, "
                        f"现有 {new_model_count} 条记录"
                    )
                logging.info(
                    f"[Migrations] v4→v5 STEP 10: target_models 验证通过, {new_model_count} 条记录"
                )

                # ── Drop old tables ──
                conn.execute("DROP TABLE upstreams_old;")
                conn.execute("DROP TABLE target_models_old;")
                logging.info("[Migrations] v4→v5 STEP 11: 旧表清理完成")

                # ── Update schema version ──
                conn.execute("DELETE FROM schema_version;")
                conn.execute("INSERT INTO schema_version (version) VALUES (5);")
                logging.info("[Migrations] v4→v5 STEP 12: schema_version 更新为 5")

                conn.commit()
                logging.info("[Migrations] v4→v5 迁移成功")
            except Exception:
                conn.rollback()
                logging.error("[Migrations] v4→v5 迁移失败，已回滚", exc_info=True)
                raise
            finally:
                conn.execute("PRAGMA foreign_keys = ON")
        finally:
            conn.close()


    # ─── v5→v6: 修复 model_routes 外键 ───


    def _migrate_v5_to_v6(self, backup_path: Path):
        """执行 v5 → v6 迁移（修复 model_routes 的 FK 到 target_models_old 的问题）。"""
        conn = self._connect()
        try:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("BEGIN TRANSACTION")
            try:
                old_count = conn.execute(
                    "SELECT COUNT(*) FROM model_routes"
                ).fetchone()[0]
                logging.info(
                    f"[Migrations] v5→v6 STEP 1: 原始 model_routes 记录数 = {old_count}"
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
                logging.info("[Migrations] v5→v6 STEP 2: model_routes_new 表创建完成")

                conn.execute("""
                    INSERT INTO model_routes_new
                        (id, source, target_model_id, request_type, created_at, updated_at)
                    SELECT id, source, target_model_id, request_type, created_at, updated_at
                    FROM model_routes;
                """)
                logging.info("[Migrations] v5→v6 STEP 3: 数据复制完成")

                conn.execute("DROP TABLE model_routes;")
                conn.execute("ALTER TABLE model_routes_new RENAME TO model_routes;")
                logging.info("[Migrations] v5→v6 STEP 4: 表替换完成")

                new_count = conn.execute(
                    "SELECT COUNT(*) FROM model_routes"
                ).fetchone()[0]
                if new_count < old_count:
                    raise sqlite3.OperationalError(
                        f"迁移验证失败: 原有 {old_count} 条记录, 现有 {new_count} 条记录"
                    )
                logging.info(
                    f"[Migrations] v5→v6 STEP 5: 验证通过, {new_count} 条记录"
                )

                conn.execute("DELETE FROM schema_version;")
                conn.execute("INSERT INTO schema_version (version) VALUES (6);")
                logging.info("[Migrations] v5→v6 STEP 6: schema_version 更新为 6")

                conn.commit()
                logging.info("[Migrations] v5→v6 迁移成功")
            except Exception:
                conn.rollback()
                logging.error("[Migrations] v5→v6 迁移失败，已回滚", exc_info=True)
                raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.close()



    def _migrate_v6_to_v7(self, backup_path: Path):
        """执行 v6 \u2192 v7 迁移（新增 agent_routes 表）。"""
        conn = self._connect()
        try:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("BEGIN TRANSACTION")
            try:
                ensure_table(conn, 'agent_routes')
                logging.info("[Migrations] v6\u2192v7 STEP 1: agent_routes \u8868\u521b\u5efa\u5b8c\u6210")

                conn.execute("DELETE FROM schema_version;")
                conn.execute("INSERT INTO schema_version (version) VALUES (7);")
                logging.info("[Migrations] v6\u2192v7 STEP 2: schema_version \u66f4\u65b0\u4e3a 7")

                conn.commit()
                logging.info("[Migrations] v6\u2192v7 \u8fc1\u79fb\u6210\u529f")
            except Exception:
                conn.rollback()
                logging.error("[Migrations] v6\u2192v7 \u8fc1\u79fb\u5931\u8d25\uff0c\u5df2\u56de\u6eda", exc_info=True)
                raise
            finally:
                conn.execute("PRAGMA foreign_keys = ON")
        finally:
            conn.close()


    def _migrate_v7_to_v8(self, backup_path: Path):
        """执行 v7 -> v8 迁移。

        变更：
          - model_routes.target_model_id FK: ON DELETE RESTRICT -> ON DELETE SET NULL，列改为可空
          - agent_routes.target_model_id FK: 同上
          - 新增 route_templates 表
        """
        conn = self._connect()
        try:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("BEGIN TRANSACTION")
            try:
                # ---- 重建 model_routes 表（SET NULL FK, 可空 target_model_id）---
                old_route_count = conn.execute(
                    "SELECT COUNT(*) FROM model_routes"
                ).fetchone()[0]
                logging.info(
                    f"[Migrations] v7->v8 STEP 1: 原始 model_routes 记录数 = {old_route_count}"
                )

                conn.execute("""
                    CREATE TABLE model_routes_new (
                        id              INTEGER PRIMARY KEY AUTOINCREMENT,
                        source          TEXT NOT NULL CHECK(length(source) > 0),
                        target_model_id INTEGER REFERENCES target_models(id) ON DELETE SET NULL,
                        request_type    TEXT NOT NULL DEFAULT 'responses'
                                        CHECK(request_type IN ('responses', 'messages', 'chat_completions')),
                        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                        updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
                        UNIQUE(source, request_type)
                    );
                """)
                logging.info("[Migrations] v7->v8 STEP 2: model_routes_new 表创建完成")

                conn.execute("""
                    INSERT INTO model_routes_new
                        (id, source, target_model_id, request_type, created_at, updated_at)
                    SELECT id, source, target_model_id, request_type, created_at, updated_at
                    FROM model_routes;
                """)
                logging.info("[Migrations] v7->v8 STEP 3: model_routes 数据复制完成")

                conn.execute("DROP TABLE model_routes;")
                conn.execute("ALTER TABLE model_routes_new RENAME TO model_routes;")
                logging.info("[Migrations] v7->v8 STEP 4: model_routes 表替换完成")

                new_route_count = conn.execute(
                    "SELECT COUNT(*) FROM model_routes"
                ).fetchone()[0]
                if new_route_count < old_route_count:
                    raise sqlite3.OperationalError(
                        f"model_routes 迁移验证失败: 原有 {old_route_count} 条记录, "
                        f"现有 {new_route_count} 条记录"
                    )
                logging.info(
                    f"[Migrations] v7->v8 STEP 5: model_routes 验证通过, {new_route_count} 条记录"
                )

                # ---- 重建 agent_routes 表（SET NULL FK, 可空 target_model_id）---
                old_agent_count = conn.execute(
                    "SELECT COUNT(*) FROM agent_routes"
                ).fetchone()[0]
                logging.info(
                    f"[Migrations] v7->v8 STEP 6: 原始 agent_routes 记录数 = {old_agent_count}"
                )

                conn.execute("""
                    CREATE TABLE agent_routes_new (
                        id              INTEGER PRIMARY KEY AUTOINCREMENT,
                        source          TEXT NOT NULL CHECK(length(source) > 0 AND source != '*'),
                        target_model_id INTEGER REFERENCES target_models(id) ON DELETE SET NULL,
                        request_type    TEXT NOT NULL DEFAULT 'chat_completions'
                                        CHECK(request_type IN ('responses', 'messages', 'chat_completions')),
                        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                        updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
                        UNIQUE(source, request_type)
                    );
                """)
                logging.info("[Migrations] v7->v8 STEP 7: agent_routes_new 表创建完成")

                conn.execute("""
                    INSERT INTO agent_routes_new
                        (id, source, target_model_id, request_type, created_at, updated_at)
                    SELECT id, source, target_model_id, request_type, created_at, updated_at
                    FROM agent_routes;
                """)
                logging.info("[Migrations] v7->v8 STEP 8: agent_routes 数据复制完成")

                conn.execute("DROP TABLE agent_routes;")
                conn.execute("ALTER TABLE agent_routes_new RENAME TO agent_routes;")
                logging.info("[Migrations] v7->v8 STEP 9: agent_routes 表替换完成")

                new_agent_count = conn.execute(
                    "SELECT COUNT(*) FROM agent_routes"
                ).fetchone()[0]
                if new_agent_count < old_agent_count:
                    raise sqlite3.OperationalError(
                        f"agent_routes 迁移验证失败: 原有 {old_agent_count} 条记录, "
                        f"现有 {new_agent_count} 条记录"
                    )
                logging.info(
                    f"[Migrations] v7->v8 STEP 10: agent_routes 验证通过, {new_agent_count} 条记录"
                )

                # ---- 创建 route_templates 表（幂等）---
                from .schema import ensure_table
                ensure_table(conn, 'route_templates')
                logging.info("[Migrations] v7->v8 STEP 11: route_templates 表创建完成")

                # ---- schema_version ---
                conn.execute("DELETE FROM schema_version;")
                conn.execute("INSERT INTO schema_version (version) VALUES (8);")
                logging.info("[Migrations] v7->v8 STEP 12: schema_version 更新为 8")

                conn.commit()
                logging.info("[Migrations] v7->v8 迁移成功")
            except Exception:
                conn.rollback()
                if backup_path:
                    import shutil
                    from pathlib import Path
                    shutil.copy2(backup_path, self.db_path)
                    logging.error(
                        "[Migrations] v7->v8 迁移失败，已从备份恢复", exc_info=True
                    )
                else:
                    logging.error(
                        "[Migrations] v7->v8 迁移失败（无备份，仅事务回滚）",
                        exc_info=True,
                    )
                raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.close()


    # ─── v8→v9: target_models 新增字段 ───

    def _migrate_v8_to_v9(self, backup_path: Path):
        """执行 v8 → v9 迁移。

        变更：
          - target_models 新增 max_context / max_input / max_output / rpm 列
        """
        conn = self._connect()
        try:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("BEGIN TRANSACTION")
            try:
                # 检查各列是否已存在（幂等）
                existing_cols = {
                    r[0] for r in conn.execute(
                        "SELECT name FROM pragma_table_info('target_models')"
                    ).fetchall()
                }

                if "max_context" not in existing_cols:
                    conn.execute(
                        "ALTER TABLE target_models ADD COLUMN max_context INTEGER DEFAULT NULL"
                    )
                    logging.info("[Migrations] v8→v9 STEP 1: target_models.max_context 列添加完成")

                if "max_input" not in existing_cols:
                    conn.execute(
                        "ALTER TABLE target_models ADD COLUMN max_input INTEGER DEFAULT NULL"
                    )
                    logging.info("[Migrations] v8→v9 STEP 2: target_models.max_input 列添加完成")

                if "max_output" not in existing_cols:
                    conn.execute(
                        "ALTER TABLE target_models ADD COLUMN max_output INTEGER DEFAULT NULL"
                    )
                    logging.info("[Migrations] v8→v9 STEP 3: target_models.max_output 列添加完成")

                if "rpm" not in existing_cols:
                    conn.execute(
                        "ALTER TABLE target_models ADD COLUMN rpm INTEGER DEFAULT NULL"
                    )
                    logging.info("[Migrations] v8→v9 STEP 4: target_models.rpm 列添加完成")

                conn.execute("DELETE FROM schema_version")
                conn.execute("INSERT INTO schema_version (version) VALUES (9)")
                logging.info("[Migrations] v8→v9 STEP 5: schema_version 更新为 9")

                conn.commit()
                logging.info("[Migrations] v8→v9 迁移成功")
            except Exception:
                conn.rollback()
                logging.error("[Migrations] v8→v9 迁移失败，已回滚", exc_info=True)
                raise
            finally:
                conn.execute("PRAGMA foreign_keys = ON")
        finally:
            conn.close()


    def _migrate_v9_to_v10(self, backup_path: Path):
        """执行 v9 → v10 迁移。

        变更：
          - 新增 upstream_api_keys 表
          - upstreams 新增 key_cooldown_secs 列（默认 60）
          - 迁移 upstreams.api_key → upstream_api_keys
        """
        conn = self._connect()
        try:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("BEGIN TRANSACTION")
            try:
                # Step 1: 创建 upstream_api_keys 表（幂等）
                from .schema import ensure_table
                ensure_table(conn, 'upstream_api_keys')
                logging.info("[Migrations] v9→v10 STEP 1: upstream_api_keys 表创建完成")

                # Step 2: 添加 key_cooldown_secs 列（幂等，检查是否已存在）
                existing_cols = {
                    r[0] for r in conn.execute(
                        "SELECT name FROM pragma_table_info('upstreams')"
                    ).fetchall()
                }
                if "key_cooldown_secs" not in existing_cols:
                    conn.execute(
                        "ALTER TABLE upstreams ADD COLUMN key_cooldown_secs INTEGER NOT NULL DEFAULT 60"
                    )
                    logging.info("[Migrations] v9→v10 STEP 2: upstreams.key_cooldown_secs 列添加完成")
                else:
                    logging.info("[Migrations] v9→v10 STEP 2: key_cooldown_secs 列已存在，跳过")

                # Step 3: 统计需要迁移的 api_key
                expected_count = conn.execute(
                    "SELECT COUNT(*) FROM upstreams WHERE api_key != ''"
                ).fetchone()[0]
                logging.info(
                    f"[Migrations] v9→v10 STEP 3: 待迁移 api_key 数量 = {expected_count}"
                )

                # Step 4: 迁移 api_key 到 upstream_api_keys（INSERT OR IGNORE，幂等）
                conn.execute("""
                    INSERT OR IGNORE INTO upstream_api_keys (upstream_id, api_key, label)
                    SELECT id, api_key, '迁移自旧字段' FROM upstreams WHERE api_key != ''
                """)
                logging.info("[Migrations] v9→v10 STEP 4: api_key 迁移插入完成")

                # Step 5: 验证迁移结果
                actual_count = conn.execute(
                    "SELECT COUNT(*) FROM upstream_api_keys WHERE label = '迁移自旧字段'"
                ).fetchone()[0]
                if actual_count < expected_count:
                    raise sqlite3.OperationalError(
                        f"迁移验证失败: 预期 {expected_count} 条, 实际插入 {actual_count} 条"
                    )
                logging.info(
                    f"[Migrations] v9→v10 STEP 5: 验证通过, {actual_count} 条记录"
                )

                # Step 6: 清空 upstreams.api_key（仅验证通过后执行）
                conn.execute("UPDATE upstreams SET api_key = ''")
                logging.info("[Migrations] v9→v10 STEP 6: upstreams.api_key 已清空")

                # Step 7: 更新 schema_version
                conn.execute("DELETE FROM schema_version")
                conn.execute("INSERT INTO schema_version (version) VALUES (10)")
                logging.info("[Migrations] v9→v10 STEP 7: schema_version 更新为 10")

                conn.commit()
                logging.info("[Migrations] v9→v10 迁移成功")
            except Exception:
                conn.rollback()
                logging.error("[Migrations] v9→v10 迁移失败，已回滚", exc_info=True)
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
        self._agent_routes: dict = {}
        self._upstream_name_to_id: dict[str, int] = {}
        self._upstream_model_map: dict[int, dict[str, bool]] = {}
        self._upstream_config_map: dict[int, dict] = {}
        self._upstream_keys: dict[int, list[str]] = {}
        self._upstream_has_any_key: set[int] = set()
        self._key_counters: dict[int, int] = {}
        self._key_cooldowns: dict[tuple, float] = {}  # (upstream_id, api_key) → cooldown_until
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

    def resolve_agent(self, source_name: str, request_type: str = "responses") -> Optional[dict]:
        with self._lock:
            self._refresh_if_stale()
            key = (source_name, request_type)
            return self._agent_routes.get(key)

    def resolve_direct(self, model_name: str) -> Optional[dict]:
        """直线路由：按'上游名/模型名'前缀匹配，优先级高于路由表。

        遍历所有活跃上游名（长度降序），做前缀匹配。
        命中且模型已注册时返回完整路由配置，否则返回 None。
        """
        with self._lock:
            self._refresh_if_stale()
            for up_name in sorted(self._upstream_name_to_id, key=len, reverse=True):
                prefix = up_name + "/"
                if not model_name.startswith(prefix):
                    continue
                model_suffix = model_name[len(prefix):]
                up_id = self._upstream_name_to_id[up_name]
                if model_suffix not in self._upstream_model_map.get(up_id, {}):
                    continue
                up_cfg = self._upstream_config_map.get(up_id)
                if up_cfg is None:
                    return None
                return {
                    "target_name": model_suffix,
                    "multimodal": self._upstream_model_map[up_id][model_suffix],
                    "format": up_cfg["format"],
                    "upstream": dict(up_cfg),
                }
        return None

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
                # 加载 agent_routes（使用 resolve_agent 获取完整 upstream 配置，与主路由 resolve_one 一致）
                new_agent_routes = {}
                try:
                    all_agent_routes = db.list_agent_routes(request_type=None)
                    for route in all_agent_routes:
                        source = route["source"]
                        pt = route["request_type"]
                        cfg = db.resolve_agent(source, pt)
                        if cfg:
                            new_agent_routes[(source, pt)] = cfg
                except Exception:
                    logging.warning("[ConfigCache] agent_routes 加载失败", exc_info=True)
                self._agent_routes = new_agent_routes
                # 加载上游缓存（直线路由用）
                try:
                    all_upstreams = db.list_upstreams(active_only=True)
                    new_name_to_id = {}
                    new_config_map = {}
                    for u in all_upstreams:
                        uid = u["id"]
                        new_name_to_id[u["name"]] = uid
                        new_config_map[uid] = {
                            "id": uid,
                            "name": u["name"],
                            "base_url": u["base_url"],
                            "api_key": u["api_key"],
                            "timeout": u["timeout"],
                            "connect_timeout": u["connect_timeout"],
                            "ssl_verify": bool(u["ssl_verify"]),
                            "retry": u["retry"],
                            "format": u["format"],
                            "key_cooldown_secs": u.get("key_cooldown_secs", 60),
                        }
                    all_models = [m for m in db.list_models()
                                  if m["upstream_id"] in new_config_map]
                    new_model_map: dict[int, dict[str, bool]] = {}
                    for m in all_models:
                        uid = m["upstream_id"]
                        if uid not in new_model_map:
                            new_model_map[uid] = {}
                        new_model_map[uid][m["name"]] = bool(m["multimodal"])
                    self._upstream_name_to_id = new_name_to_id
                    self._upstream_model_map = new_model_map
                    self._upstream_config_map = new_config_map
                except Exception:
                    logging.warning("[ConfigCache] 上游缓存加载失败，保留旧缓存", exc_info=True)

                # 加载 upstream_api_keys
                try:
                    new_upstream_keys: dict[int, list[str]] = {}
                    new_has_any_key: set[int] = set()
                    conn = db._connect()
                    try:
                        all_keys_rows = conn.execute(
                            "SELECT upstream_id, api_key, is_active"
                            " FROM upstream_api_keys"
                            " ORDER BY upstream_id, id"
                        ).fetchall()
                    finally:
                        conn.close()
                    for row in all_keys_rows:
                        uid = row["upstream_id"]
                        if uid not in self._upstream_config_map:
                            continue
                        if row["is_active"]:
                            if uid not in new_upstream_keys:
                                new_upstream_keys[uid] = []
                            new_upstream_keys[uid].append(row["api_key"])
                        new_has_any_key.add(uid)
                    self._upstream_keys = new_upstream_keys
                    self._upstream_has_any_key = new_has_any_key
                    self._key_counters = {}
                except Exception:
                    logging.warning(
                        "[ConfigCache] upstream_api_keys 加载失败，保留旧缓存",
                        exc_info=True,
                    )

                self._loaded_at = time.time()

            finally:
                db.close()
        except Exception:
            logging.warning("[ConfigCache] 配置缓存加载失败，保留旧缓存", exc_info=True)


    def pick_key(self, upstream_id: int) -> str:
        """Round-Robin 轮询选择 API key。

        跳过冷却中的 key。全部冷却时降级返回当前轮询位置的 key。
        无 key 时返回空字符串。
        """
        with self._lock:
            self._refresh_if_stale()
            keys = self._upstream_keys.get(upstream_id, [])
            if not keys:
                return ""
            now = time.time()
            counter = self._key_counters.get(upstream_id, 0) % len(keys)
            n = len(keys)
            # 尝试在冷却外的 key 中轮询
            for offset in range(n):
                idx = (counter + offset) % n
                candidate = keys[idx]
                cooldown_key = (upstream_id, candidate)
                if self._key_cooldowns.get(cooldown_key, 0) < now:
                    self._key_counters[upstream_id] = (idx + 1) % n
                    return candidate
            # 全部冷却 → 降级返回当前 key
            return keys[counter]

    def mark_cooldown(self, upstream_id: int, api_key: str, seconds: int):
        """标记 key 进入冷却。

        冷却键使用 (upstream_id, api_key) 而非 index，
        确保冷却状态跨 reload 保持不变。
        """
        with self._lock:
            key_tuple = (upstream_id, api_key)
            self._key_cooldowns[key_tuple] = time.time() + seconds

    def get_key_status(self, upstream_id: int) -> list:
        """查询上游所有 key 的冷却状态。"""
        with self._lock:
            self._refresh_if_stale()
            keys = self._upstream_keys.get(upstream_id, [])
            if not keys:
                return []
            now = time.time()
            result = []
            for idx, key in enumerate(keys):
                masked = key if len(key) <= 4 else "****" + key[-4:]
                cooldown_until = self._key_cooldowns.get((upstream_id, key), 0)
                remaining = max(0.0, cooldown_until - now)
                result.append({
                    "idx": idx,
                    "masked_key": masked,
                    "cooling_down": remaining > 0,
                    "cooldown_remaining_secs": round(remaining, 1),
                })
            return result


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
