# 动态模型配置 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 proxy_config.yaml 的静态 model_map 替换为数据库驱动的动态配置系统，支持 Web 页面管理多上游、多模型、多路由映射。

**Architecture:** 新增 `config_manager.py`（ConfigDB + ConfigCache），`server.py` 新增 REST API 调用 ConfigDB，`proxy.py` 通过 ConfigCache 读取缓存。前端新增「模型管理」Tab，通过 CustomEvent 事件总线实现三表格联动。

**Tech Stack:** Python 3 标准库（sqlite3, http.server, threading），vanilla JS（无框架），SQLite WAL 模式。

---

### 文件规划

| 文件 | 操作 | 职责 |
|------|------|------|
| `config_manager.py` | **新建** | ConfigDB（数据库 CRUD + resolve）+ ConfigCache（TTL 缓存 + Lock） |
| `test/test_config_manager.py` | **新建** | config_manager 全部单元测试（TDD） |
| `proxy.py` | 修改 | 集成 ConfigCache，替换 resolve_model()，新增 /admin/reload |
| `server.py` | 修改 | 新增 /api/upstreams, /api/models, /api/routes, /api/config 四组 API |
| `static/index.html` | 修改 | 新增「模型管理」Tab，三表格 + 事件总线 |

---

### Task 1: config_manager.py — 数据库初始化 + PRAGMA

**Files:**
- Create: `config_manager.py`
- Create: `test/test_config_manager.py`

- [ ] **Step 1: 写失败测试 — 验证建表 + PRAGMA**

```python
# test/test_config_manager.py
import unittest
import tempfile
import sqlite3
from pathlib import Path
from config_manager import ConfigDB


class TestConfigDBInit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "config.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_init_creates_database_file(self):
        db = ConfigDB(self.db_path)
        self.assertTrue(self.db_path.exists())
        db.close()

    def test_init_creates_all_tables(self):
        db = ConfigDB(self.db_path)
        conn = sqlite3.connect(self.db_path)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()]
        conn.close()
        db.close()
        self.assertIn("schema_version", tables)
        self.assertIn("upstreams", tables)
        self.assertIn("target_models", tables)
        self.assertIn("model_routes", tables)

    def test_pragma_foreign_keys_enabled(self):
        db = ConfigDB(self.db_path)
        conn = db._conn
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        self.assertEqual(fk, 1)
        db.close()

    def test_pragma_wal_mode(self):
        db = ConfigDB(self.db_path)
        conn = db._conn
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(journal, "wal")
        db.close()

    def test_pragma_busy_timeout(self):
        db = ConfigDB(self.db_path)
        conn = db._conn
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        self.assertEqual(timeout, 3000)
        db.close()

    def test_init_idempotent(self):
        """重复初始化不抛异常。"""
        db1 = ConfigDB(self.db_path)
        db1.close()
        db2 = ConfigDB(self.db_path)
        db2.close()
        self.assertTrue(self.db_path.exists())
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /Users/xys/.hermes/fact-store-browser-dynamic-model && python3 -m pytest test/test_config_manager.py::TestConfigDBInit -v
```
Expected: 6 个 FAIL（ModuleNotFoundError: No module named 'config_manager'）

- [ ] **Step 3: 实现 config_manager.py 最小代码**

```python
#!/usr/bin/env python3
"""动态模型配置管理 — ConfigDB（数据库 CRUD）+ ConfigCache（内存缓存）。"""

import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional


class ConfigDB:
    """config.db 数据库操作。每次查询打开新连接（无连接池）。"""

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
        """首次启动从 proxy_config.yaml 导入种子数据。"""
        conn = self._connect()
        try:
            has_version = conn.execute(
                "SELECT COUNT(*) FROM schema_version"
            ).fetchone()[0] > 0
            if has_version:
                return  # 已导入过，跳过

            if not yaml_path.exists():
                # yaml 不存在 → 写空配置版本，避免死循环
                conn.execute("INSERT INTO schema_version (version) VALUES (1)")
                conn.commit()
                return

            # 解析 yaml（复用 proxy.py 的 _parse_yaml）
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "proxy", Path(__file__).parent / "proxy.py"
            )
            proxy_module = importlib.util.module_from_spec(spec)
            # 不执行 proxy.py 模块级代码，只需要 _parse_yaml
            # 直接内联解析逻辑

            with open(yaml_path) as f:
                config = _parse_yaml(f.read())

            conn.execute("BEGIN")
            try:
                # 导入 upstream
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

                # 导入 model_map
                model_map = config.get("model_map", {})
                default_upstream_id = "default" if upstream_data else None

                for source, cfg in model_map.items():
                    if cfg is None or not isinstance(cfg, dict):
                        continue
                    target_name = cfg.get("target", source)
                    multimodal = 1 if cfg.get("multimodal", False) else 0

                    # 确保目标模型存在
                    conn.execute(
                        """INSERT OR IGNORE INTO target_models (name, upstream_id, multimodal, format)
                           VALUES (?, ?, ?, 'openai_chat')""",
                        (target_name, default_upstream_id, multimodal),
                    )
                    # 获取 target_model_id
                    row = conn.execute(
                        """SELECT id FROM target_models
                           WHERE name=? AND upstream_id=?""",
                        (target_name, default_upstream_id),
                    ).fetchone()
                    if row is None:
                        continue
                    target_id = row["id"]

                    # 创建路由
                    conn.execute(
                        """INSERT OR REPLACE INTO model_routes (source, target_model_id)
                           VALUES (?, ?)""",
                        (source, target_id),
                    )

                conn.execute("INSERT INTO schema_version (version) VALUES (1)")
                conn.commit()
            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    def close(self):
        pass  # 每次查询独立连接，无需保持长连接

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
            # 若 is_default=1，先清除其他默认
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
        """返回引用该上游下模型的所有路由 source 列表。"""
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
            values.append(model_id)
            conn.execute(
                f"UPDATE target_models SET {', '.join(fields)} WHERE id = ?",
                values,
            )
            conn.commit()
        finally:
            conn.close()

    def delete_model(self, model_id: int):
        conn = self._connect()
        try:
            conn.execute("DELETE FROM target_models WHERE id = ?", (model_id,))
            conn.commit()
        finally:
            conn.close()

    def model_referenced_routes(self, model_id: int) -> list:
        """返回引用该模型的所有路由 source 列表（预检查用）。"""
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
                "matched_source": source_name,  # 调试用：记录实际命中的 source
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
        """返回 {source_name: {target_name, multimodal, format, upstream_id, ...}, ...}"""
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
        """启动校验：resolve_model('*') 不能返回 None。"""
        return self.resolve_model("*") is not None

    def get_counts(self) -> dict:
        """返回当前上游/模型/路由数量。"""
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


# ─── YAML 解析（内联，避免依赖 proxy.py）───────────────────────────

def _parse_yaml(text: str) -> dict:
    """极简 YAML 解析器，复用 proxy.py 相同实现。"""
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
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd /Users/xys/.hermes/fact-store-browser-dynamic-model && python3 -m pytest test/test_config_manager.py::TestConfigDBInit -v
```
Expected: 6 PASS

- [ ] **Step 5: 提交**

```bash
git add config_manager.py test/test_config_manager.py
git commit -m "feat: 新增 config_manager.py — ConfigDB 数据库初始化 + PRAGMA 配置"
```

---

### Task 2: config_manager.py — 上游 CRUD 测试与实现

**Files:**
- Modify: `test/test_config_manager.py`

- [ ] **Step 1: 写失败测试 — 上游 CRUD**

```python
# 追加到 TestConfigDBInit 之后


class TestUpstreamCRUD(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "config.db"
        self.db = ConfigDB(self.db_path)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_add_and_list_upstreams(self):
        self.db.add_upstream({
            "id": "litellm-prod",
            "base_url": "https://llm.cargoware.com/v1",
            "api_key": "sk-test123",
            "timeout": 120,
            "connect_timeout": 10,
            "ssl_verify": 1,
            "retry": 2,
        })
        upstreams = self.db.list_upstreams()
        self.assertEqual(len(upstreams), 1)
        self.assertEqual(upstreams[0]["id"], "litellm-prod")
        self.assertEqual(upstreams[0]["api_key"], "sk-test123")

    def test_get_upstream(self):
        self.db.add_upstream({"id": "test-up", "base_url": "http://x"})
        u = self.db.get_upstream("test-up")
        self.assertEqual(u["base_url"], "http://x")

    def test_get_upstream_not_found(self):
        self.assertIsNone(self.db.get_upstream("nonexistent"))

    def test_update_upstream(self):
        self.db.add_upstream({"id": "test-up", "base_url": "http://x"})
        self.db.update_upstream("test-up", {"base_url": "http://y", "timeout": 60})
        u = self.db.get_upstream("test-up")
        self.assertEqual(u["base_url"], "http://y")
        self.assertEqual(u["timeout"], 60)

    def test_disable_upstream(self):
        self.db.add_upstream({"id": "test-up", "base_url": "http://x"})
        self.db.disable_upstream("test-up")
        u = self.db.get_upstream("test-up")
        self.assertEqual(u["is_active"], 0)

    def test_list_upstreams_active_only(self):
        self.db.add_upstream({"id": "up-a", "base_url": "http://a"})
        self.db.add_upstream({"id": "up-b", "base_url": "http://b"})
        self.db.disable_upstream("up-b")
        active = self.db.list_upstreams(active_only=True)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["id"], "up-a")

    def test_is_default_clears_others_on_add(self):
        self.db.add_upstream({"id": "up-a", "base_url": "http://a", "is_default": 1})
        self.db.add_upstream({"id": "up-b", "base_url": "http://b", "is_default": 1})
        a = self.db.get_upstream("up-a")
        b = self.db.get_upstream("up-b")
        self.assertEqual(a["is_default"], 0)
        self.assertEqual(b["is_default"], 1)

    def test_is_default_clears_others_on_update(self):
        self.db.add_upstream({"id": "up-a", "base_url": "http://a", "is_default": 1})
        self.db.add_upstream({"id": "up-b", "base_url": "http://b"})
        self.db.update_upstream("up-b", {"is_default": 1})
        a = self.db.get_upstream("up-a")
        b = self.db.get_upstream("up-b")
        self.assertEqual(a["is_default"], 0)
        self.assertEqual(b["is_default"], 1)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python3 -m pytest test/test_config_manager.py::TestUpstreamCRUD -v
```
Expected: 8 FAIL（add_upstream 等函数未定义）

- [ ] **Step 3: 确认实现已在 Task 1 中完成**

Task 1 的 `config_manager.py` 已包含全部 CRUD 方法。

- [ ] **Step 4: 运行测试确认通过**

```bash
python3 -m pytest test/test_config_manager.py::TestUpstreamCRUD -v
```
Expected: 8 PASS

- [ ] **Step 5: 提交**

```bash
git add test/test_config_manager.py
git commit -m "test: 上游 CRUD 单元测试 — add/get/update/disable/list + is_default 唯一性"
```

---

### Task 3: config_manager.py — 模型 + 路由 CRUD 测试

**Files:**
- Modify: `test/test_config_manager.py`

- [ ] **Step 1: 写失败测试**

```python
# 追加到 test_config_manager.py


class TestModelCRUD(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "config.db"
        self.db = ConfigDB(self.db_path)
        self.db.add_upstream({"id": "up-a", "base_url": "http://a"})
        self.db.add_upstream({"id": "up-b", "base_url": "http://b"})

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_add_and_list_models(self):
        mid = self.db.add_model({"name": "qwen-plus", "upstream_id": "up-a"})
        self.assertIsInstance(mid, int)
        models = self.db.list_models()
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]["name"], "qwen-plus")
        self.assertEqual(models[0]["upstream_name"], "up-a")

    def test_list_models_filter_by_upstream(self):
        self.db.add_model({"name": "qwen-a", "upstream_id": "up-a"})
        self.db.add_model({"name": "qwen-b", "upstream_id": "up-b"})
        models_a = self.db.list_models(upstream_id="up-a")
        self.assertEqual(len(models_a), 1)
        self.assertEqual(models_a[0]["name"], "qwen-a")

    def test_add_duplicate_model_same_upstream_raises(self):
        self.db.add_model({"name": "qwen", "upstream_id": "up-a"})
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.add_model({"name": "qwen", "upstream_id": "up-a"})

    def test_same_name_different_upstream_ok(self):
        self.db.add_model({"name": "qwen", "upstream_id": "up-a"})
        mid = self.db.add_model({"name": "qwen", "upstream_id": "up-b"})
        self.assertIsInstance(mid, int)

    def test_update_model(self):
        mid = self.db.add_model({"name": "qwen", "upstream_id": "up-a"})
        self.db.update_model(mid, {"name": "qwen-plus", "multimodal": 0})
        m = self.db.get_model(mid)
        self.assertEqual(m["name"], "qwen-plus")
        self.assertEqual(m["multimodal"], 0)

    def test_delete_model(self):
        mid = self.db.add_model({"name": "qwen", "upstream_id": "up-a"})
        self.db.delete_model(mid)
        self.assertIsNone(self.db.get_model(mid))

    def test_delete_model_referenced_by_route_raises(self):
        """ON DELETE RESTRICT：被路由引用的模型无法删除。"""
        mid = self.db.add_model({"name": "qwen", "upstream_id": "up-a"})
        self.db.add_route({"source": "gpt-4", "target_model_id": mid})
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.delete_model(mid)

    def test_model_referenced_routes(self):
        mid = self.db.add_model({"name": "qwen", "upstream_id": "up-a"})
        self.db.add_route({"source": "gpt-4", "target_model_id": mid})
        self.db.add_route({"source": "o4-mini", "target_model_id": mid})
        refs = self.db.model_referenced_routes(mid)
        self.assertEqual(set(refs), {"gpt-4", "o4-mini"})


class TestRouteCRUD(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "config.db"
        self.db = ConfigDB(self.db_path)
        self.db.add_upstream({"id": "up-a", "base_url": "http://a"})
        self.mid = self.db.add_model({"name": "qwen", "upstream_id": "up-a"})

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_add_and_list_routes(self):
        rid = self.db.add_route({"source": "gpt-4", "target_model_id": self.mid})
        self.assertIsInstance(rid, int)
        routes = self.db.list_routes()
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["source"], "gpt-4")
        self.assertEqual(routes[0]["target_name"], "qwen")

    def test_star_route_orders_first(self):
        self.db.add_route({"source": "z-model", "target_model_id": self.mid})
        self.db.add_route({"source": "*", "target_model_id": self.mid})
        routes = self.db.list_routes()
        self.assertEqual(routes[0]["source"], "*")

    def test_update_route(self):
        rid = self.db.add_route({"source": "gpt-4", "target_model_id": self.mid})
        mid2 = self.db.add_model({"name": "claude", "upstream_id": "up-a"})
        self.db.update_route(rid, {"source": "gpt-4o", "target_model_id": mid2})
        r = self.db.get_route(rid)
        self.assertEqual(r["source"], "gpt-4o")
        self.assertEqual(r["target_name"], "claude")

    def test_delete_route(self):
        rid = self.db.add_route({"source": "gpt-4", "target_model_id": self.mid})
        self.db.delete_route(rid)
        self.assertIsNone(self.db.get_route(rid))

    def test_fk_restrict_upstream_delete(self):
        """删除被模型引用的上游会被外键阻止。"""
        with self.assertRaises(sqlite3.IntegrityError):
            conn = self.db._connect()
            conn.execute("DELETE FROM upstreams WHERE id = ?", ("up-a",))
            conn.close()
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python3 -m pytest test/test_config_manager.py::TestModelCRUD test/test_config_manager.py::TestRouteCRUD -v
```
Expected: 部分 FAIL（方法已在 Task 1 实现，大部分应 PASS）

- [ ] **Step 3: 确认实现**

Task 1 的 `config_manager.py` 已包含全部模型/路由 CRUD 方法。若测试失败，检查 API 签名是否一致。

- [ ] **Step 4: 运行测试确认通过**

```bash
python3 -m pytest test/test_config_manager.py -v
```
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add test/test_config_manager.py
git commit -m "test: 模型 + 路由 CRUD 单元测试 — add/list/update/delete + 外键约束验证"
```

---

### Task 4: config_manager.py — resolve_model 测试 + ConfigCache

**Files:**
- Modify: `test/test_config_manager.py`
- 实现: `config_manager.py`（ConfigCache 部分在 Task 1 基础上追加）

- [ ] **Step 1: 写失败测试 — resolve_model + ConfigCache**

```python
# 追加到 test_config_manager.py


class TestResolveModel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "config.db"
        self.db = ConfigDB(self.db_path)
        self.db.add_upstream({"id": "up-a", "base_url": "http://a", "api_key": "sk-a"})
        self.db.add_upstream({"id": "up-b", "base_url": "http://b", "api_key": "sk-b"})
        self.m1 = self.db.add_model({"name": "qwen", "upstream_id": "up-a"})
        self.m2 = self.db.add_model({"name": "claude", "upstream_id": "up-b"})

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_resolve_exact_match(self):
        self.db.add_route({"source": "gpt-4", "target_model_id": self.m1})
        cfg = self.db.resolve_model("gpt-4")
        self.assertEqual(cfg["target_name"], "qwen")
        self.assertEqual(cfg["upstream"]["base_url"], "http://a")
        self.assertEqual(cfg["upstream"]["api_key"], "sk-a")

    def test_resolve_fallback_to_star(self):
        self.db.add_route({"source": "*", "target_model_id": self.m2})
        cfg = self.db.resolve_model("unknown-model")
        self.assertEqual(cfg["target_name"], "claude")
        self.assertEqual(cfg["upstream"]["base_url"], "http://b")

    def test_resolve_skip_disabled_upstream(self):
        self.db.add_route({"source": "gpt-4", "target_model_id": self.m1})
        self.db.add_route({"source": "*", "target_model_id": self.m2})
        self.db.disable_upstream("up-a")
        cfg = self.db.resolve_model("gpt-4")
        # up-a 被禁用，跳过 gpt-4 匹配，走 * fallback → claude@up-b
        self.assertEqual(cfg["target_name"], "claude")

    def test_resolve_none_when_no_match(self):
        cfg = self.db.resolve_model("no-route-anywhere")
        self.assertIsNone(cfg)

    def test_resolve_none_when_star_also_disabled(self):
        self.db.add_route({"source": "*", "target_model_id": self.m1})
        self.db.disable_upstream("up-a")
        cfg = self.db.resolve_model("anything")
        self.assertIsNone(cfg)

    def test_get_all_routes(self):
        self.db.add_route({"source": "gpt-4", "target_model_id": self.m1})
        self.db.add_route({"source": "*", "target_model_id": self.m2})
        all_routes = self.db.get_all_routes()
        self.assertIn("gpt-4", all_routes)
        self.assertIn("*", all_routes)
        self.assertEqual(all_routes["gpt-4"]["target_name"], "qwen")

    def test_get_all_routes_skips_disabled_upstream(self):
        self.db.add_route({"source": "gpt-4", "target_model_id": self.m1})
        self.db.add_route({"source": "*", "target_model_id": self.m2})
        self.db.disable_upstream("up-a")
        all_routes = self.db.get_all_routes()
        self.assertNotIn("gpt-4", all_routes)
        self.assertIn("*", all_routes)

    def test_validate_star_fallback(self):
        self.db.add_route({"source": "*", "target_model_id": self.m1})
        self.assertTrue(self.db.validate_star_fallback())


from config_manager import ConfigCache


class TestConfigCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "config.db"
        self.db = ConfigDB(self.db_path)
        self.db.add_upstream({"id": "up-a", "base_url": "http://a"})
        self.m1 = self.db.add_model({"name": "qwen", "upstream_id": "up-a"})
        self.db.add_route({"source": "*", "target_model_id": self.m1})

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_cache_resolve_returns_config(self):
        cache = ConfigCache(self.db_path, ttl=5)
        cfg = cache.resolve("unknown")
        self.assertEqual(cfg["target_name"], "qwen")

    def test_cache_hit_avoids_db_read(self):
        cache = ConfigCache(self.db_path, ttl=99)
        cfg1 = cache.resolve("*")
        # 修改数据库（绕过缓存）
        m2 = self.db.add_model({"name": "claude", "upstream_id": "up-a"})
        self.db.add_route({"source": "*", "target_model_id": m2})
        cfg2 = cache.resolve("*")
        # TTL 未过期，应返回缓存中的旧值
        self.assertEqual(cfg1["target_name"], cfg2["target_name"])

    def test_reload_refreshes_cache(self):
        cache = ConfigCache(self.db_path, ttl=99)
        cfg1 = cache.resolve("*")
        m2 = self.db.add_model({"name": "claude", "upstream_id": "up-a"})
        self.db.add_route({"source": "*", "target_model_id": m2})
        cache.reload()
        cfg2 = cache.resolve("*")
        self.assertEqual(cfg2["target_name"], "claude")

    def test_ttl_expiry_refreshes(self):
        cache = ConfigCache(self.db_path, ttl=0)  # 立即过期
        cfg1 = cache.resolve("*")
        m2 = self.db.add_model({"name": "claude", "upstream_id": "up-a"})
        self.db.add_route({"source": "*", "target_model_id": m2})
        cfg2 = cache.resolve("*")
        self.assertEqual(cfg2["target_name"], "claude")

    def test_get_all(self):
        cache = ConfigCache(self.db_path, ttl=5)
        all_routes = cache.get_all()
        self.assertIn("*", all_routes)
```

- [ ] **Step 2: 运行测试确认部分失败（ConfigCache 未定义）**

```bash
python3 -m pytest test/test_config_manager.py::TestResolveModel test/test_config_manager.py::TestConfigCache -v
```
Expected: TestResolveModel PASS（已在 Task 1 实现），TestConfigCache FAIL（类未定义）

- [ ] **Step 3: 实现 ConfigCache**

```python
# 追加到 config_manager.py 末尾


class ConfigCache:
    """内存缓存，供 proxy.py 使用。"""

    def __init__(self, db_path: Path, ttl: float = 5):
        self._db_path = db_path
        self._ttl = ttl
        self._lock = threading.Lock()
        self._cache: dict = {}
        self._loaded_at: float = 0

    def reload(self):
        """强制重新加载（由 /admin/reload 触发）。"""
        with self._lock:
            self._loaded_at = 0

    def resolve(self, source_name: str) -> Optional[dict]:
        self._refresh_if_stale()
        with self._lock:
            return self._cache.get("__resolve__", {}).get(source_name)

    def get_all(self) -> dict:
        self._refresh_if_stale()
        with self._lock:
            return self._cache.get("__all__", {})

    def _refresh_if_stale(self):
        now = time.time()
        if now - self._loaded_at < self._ttl and self._cache:
            return
        with self._lock:
            if now - self._loaded_at < self._ttl and self._cache:
                return  # double-check
            db = ConfigDB(self._db_path)
            try:
                all_routes = db.get_all_routes()
                # 预解析所有 source_name：对每个 source 做 resolve
                resolve_map = {}
                for source in list(all_routes.keys()) + ["*"]:
                    if source == "*":
                        continue
                    cfg = db.resolve_model(source)
                    if cfg:
                        resolve_map[source] = cfg
                # 但 resolve_model 需要处理任意 source_name，不能全缓存
                # 改为：缓存整个路由表 + 上游信息，resolve 时从缓存计算
                self._cache = {
                    "__all__": all_routes,
                    "__resolve_cache__": {},
                    "__upstreams__": {},
                }
                # 缓存所有上游信息
                for u in db.list_upstreams(active_only=True):
                    self._cache["__upstreams__"][u["id"]] = u
                # 缓存 resolve_model 的基准结果
                db2 = ConfigDB(self._db_path)
                for source in all_routes:
                    cfg = db2.resolve_model(source)
                    if cfg:
                        self._cache["__resolve_cache__"][source] = cfg
                # 特殊处理 * fallback
                star_cfg = db2.resolve_model("*")
                if star_cfg:
                    self._cache["__resolve_cache__"]["*"] = star_cfg
                db2.close()

                self._loaded_at = now
            finally:
                db.close()
```

Wait — that's overly complex. Let me simplify. ConfigCache should just call ConfigDB.resolve_model on each cache miss. The cache stores per-source_name results.

Let me rewrite:

```python
# 替换上述实现为：


class ConfigCache:
    """内存缓存，供 proxy.py 使用。"""

    def __init__(self, db_path: Path, ttl: float = 5):
        self._db_path = db_path
        self._ttl = ttl
        self._lock = threading.Lock()
        self._cache: dict = {}       # {source_name: config_dict}
        self._loaded_at: float = 0

    def reload(self):
        """强制重新加载。"""
        with self._lock:
            self._loaded_at = 0

    def resolve(self, source_name: str) -> Optional[dict]:
        now = time.time()
        with self._lock:
            if now - self._loaded_at >= self._ttl:
                self._cache.clear()
                self._loaded_at = now

            if source_name not in self._cache and now - self._loaded_at > 0:
                # 需要上锁读取 DB
                pass

        # 简化实现：TTL 过期后，下一次 resolve 重新读库
        with self._lock:
            if self._loaded_at == 0 or not self._cache:
                self._load_all()
            return self._cache.get(source_name)

    def get_all(self) -> dict:
        with self._lock:
            if self._loaded_at == 0 or not self._cache:
                self._load_all()
            return {k: v for k, v in self._cache.items() if k != "__star__"}

    def _load_all(self):
        db = ConfigDB(self._db_path)
        try:
            self._cache.clear()
            routes = db.get_all_routes()
            for source in routes:
                cfg = db.resolve_model(source)
                if cfg:
                    self._cache[source] = cfg
            # handle * fallback
            star_cfg = db.resolve_model("*")
            if star_cfg:
                self._cache["*"] = star_cfg
            self._loaded_at = time.time()
        finally:
            db.close()
```

Hmm, but this doesn't handle the case where `resolve("unknown-model")` comes in — it won't be in `self._cache` and needs fallback.

Simpler approach: store the `ConfigDB.resolve_model` result for each key, and also store a fallback *. On resolve:
1. Check exact key in cache
2. If not found, check * in cache
3. Return None if neither found

```python
class ConfigCache:
    """内存缓存，供 proxy.py 使用。"""

    def __init__(self, db_path: Path, ttl: float = 5):
        self._db_path = db_path
        self._ttl = ttl
        self._lock = threading.Lock()
        self._routes: dict = {}     # {source: config_dict}
        self._loaded_at: float = 0

    def reload(self):
        with self._lock:
            self._loaded_at = 0

    def resolve(self, source_name: str) -> Optional[dict]:
        with self._lock:
            self._refresh_if_stale()
            # 精确匹配
            if source_name in self._routes:
                return self._routes[source_name]
            # fallback
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
            db = ConfigDB(self._db_path)
            try:
                new_routes = {}
                all_routes = db.get_all_routes()
                for source in all_routes:
                    cfg = db.resolve_model(source)
                    if cfg:
                        new_routes[source] = cfg
                star_cfg = db.resolve_model("*")
                if star_cfg:
                    new_routes["*"] = star_cfg
                # 全部成功后才替换
                self._routes = new_routes
                self._loaded_at = time.time()
            finally:
                db.close()
        except Exception:
            # 数据库异常时保留旧缓存，不更新 _loaded_at
            # TTL 机制下次继续尝试，避免一次性失败导致永不过期
            pass
```

数据库异常时保留旧缓存，resolve() 继续返回旧值，不抛 500。下次 TTL 到期后自动重试。
```

- [ ] **Step 4: 运行测试确认通过**

```bash
python3 -m pytest test/test_config_manager.py::TestResolveModel test/test_config_manager.py::TestConfigCache -v
```
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add config_manager.py test/test_config_manager.py
git commit -m "feat: 新增 resolve_model 查询 + ConfigCache 内存缓存（TTL + Lock）"
```

---

### Task 5: config_manager.py — 种子导入测试

**Files:**
- Modify: `test/test_config_manager.py`

- [ ] **Step 1: 写失败测试**

```python
# 追加到 test_config_manager.py


class TestSeedImport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "config.db"
        self.yaml_path = Path(self.tmp.name) / "proxy_config.yaml"

    def tearDown(self):
        self.tmp.cleanup()

    def _make_yaml(self, content: str):
        self.yaml_path.write_text(content)

    def test_seed_empty_db(self):
        self._make_yaml("""\
upstream:
  base_url: "http://test:4000"
  api_key: "sk-test"
  timeout: 120
  connect_timeout: 10
  ssl_verify: true
  retry: 1

model_map:
  "codex-mini-latest":
    target: "qwen-plus"
    multimodal: true
  "gpt-4o":
    target: "qwen-plus"
    multimodal: true
  "*":
    target: "qwen-plus"
    multimodal: true
""")
        db = ConfigDB(self.db_path)
        db._seed_from_yaml(self.yaml_path)
        # 验证上游
        u = db.get_upstream("default")
        self.assertIsNotNone(u)
        self.assertEqual(u["base_url"], "http://test:4000")
        self.assertEqual(u["api_key"], "sk-test")
        # 验证模型
        models = db.list_models()
        self.assertGreater(len(models), 0)
        # 验证路由
        routes = db.list_routes()
        self.assertGreater(len(routes), 0)
        # 验证 * fallback
        star_routes = [r for r in routes if r["source"] == "*"]
        self.assertEqual(len(star_routes), 1)
        db.close()

    def test_seed_skip_if_already_seeded(self):
        self._make_yaml("upstream:\n  base_url: \"http://a:4000\"\nmodel_map:\n  \"*\":\n    target: \"m1\"\n    multimodal: false\n")
        db1 = ConfigDB(self.db_path)
        db1._seed_from_yaml(self.yaml_path)
        db1.close()

        # 修改 yaml
        self._make_yaml("upstream:\n  base_url: \"http://b:5000\"\nmodel_map:\n  \"*\":\n    target: \"m2\"\n    multimodal: false\n")
        db2 = ConfigDB(self.db_path)
        db2._seed_from_yaml(self.yaml_path)  # 应跳过
        u = db2.get_upstream("default")
        self.assertEqual(u["base_url"], "http://a:4000")  # 未被覆盖
        db2.close()

    def test_seed_yaml_missing_writes_version(self):
        """yaml 不存在时写入空版本，不崩溃。"""
        db = ConfigDB(self.db_path)
        missing_path = Path(self.tmp.name) / "nonexistent.yaml"
        db._seed_from_yaml(missing_path)
        # 不应抛异常
        upstreams = db.list_upstreams()
        self.assertEqual(len(upstreams), 0)
        db.close()
```

- [ ] **Step 2: 运行测试确认失败/通过**

```bash
python3 -m pytest test/test_config_manager.py::TestSeedImport -v
```
Expected: PASS（种子导入已在 Task 1 实现）

- [ ] **Step 3: 提交**

```bash
git add test/test_config_manager.py
git commit -m "test: 种子导入测试 — 空库导入/yaml缺失兜底/已导入跳过"
```

---

### Task 6: proxy.py — 集成 ConfigCache

**Files:**
- Modify: `proxy.py`

**load_config() 保留逻辑**：load_config() 不再校验 model_map / `*` fallback，但保留以下功能：
- 加载 `proxy_config.yaml`（仅读取 upstream 段用于日志配置等）
- 配置 logging（log_level、FileHandler）
- 启动时校验改为 `config_cache.resolve("*") is not None`
- config.db 种子导入由 config_manager 自动处理，proxy.py 无需关心

- [ ] **Step 1: 修改 proxy.py 导入和全局单例**

在 proxy.py 顶部导入区添加：

```python
from config_manager import ConfigCache
```

在 `load_config()` 函数之前（或紧接 CONFIG 定义之后）添加全局缓存单例：

```python
# ─── 动态配置缓存（替代静态 model_map）───────────────────────────
CONFIG_DB_PATH = Path.home() / ".hermes" / "config.db"
config_cache = ConfigCache(CONFIG_DB_PATH)
```

- [ ] **Step 2: 替换 resolve_model()**

```python
def resolve_model(model_name: str) -> dict:
    """使用动态配置缓存查找模型路由。
    
    返回格式与旧版兼容：
    {"target": str, "multimodal": bool}
    -> 新增 {"target": str, "multimodal": bool, "upstream": dict}
    """
    cfg = config_cache.resolve(model_name)
    if cfg is None:
        # 返回兜底配置：使用模型名自身作为 target
        return {"target": model_name, "multimodal": False}
    return {
        "target": cfg["target_name"],
        "multimodal": bool(cfg["multimodal"]),
        "_upstream": cfg["upstream"],  # 上游配置，转发时使用
    }
```

- [ ] **Step 3: 替换 _handle_models()**

```python
def _handle_models(self):
    """返回动态配置中所有非 * 的源模型列表。"""
    routes = config_cache.get_all()
    models = [k for k in routes if k != "*"]
    self._send_json(200, {"data": [{"id": m, "object": "model"} for m in models]})
```

- [ ] **Step 4: 修改 load_config() 校验逻辑**

在 `load_config()` 中：
- 移除 `model_map` 和 `"*"` fallback 的启动校验
- 改为校验 ConfigCache：

```python
def load_config(config_path: Path = None):
    global CONFIG
    path = config_path or CONFIG_PATH
    if not path.exists():
        print(f"FATAL: 配置文件不存在: {path}", file=sys.stderr)
        sys.exit(1)

    with open(path, "r") as f:
        CONFIG = _parse_yaml(f.read())

    # 设置日志...
    if not logging.root.handlers:
        log_level = CONFIG.get("proxy", {}).get("log_level", "INFO")
        numeric_level = getattr(logging, log_level.upper(), logging.INFO)
        log_file = Path(__file__).parent / "proxy.log"
        file_handler = logging.FileHandler(log_file)
        stream_handler = logging.StreamHandler(sys.stdout)
        logging.basicConfig(
            level=numeric_level,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[file_handler, stream_handler],
        )

    # 新校验：* fallback 必须可用
    if config_cache.resolve("*") is None:
        print('FATAL: 动态配置中 "*" fallback 路由不可用（不存在或上游已禁用）', file=sys.stderr)
        sys.exit(1)
```

- [ ] **Step 5: 调整转发逻辑（upstream 动态切换）**

`_forward_non_streaming` 和 `_forward_streaming` 中，当前固定使用 `CONFIG["upstream"]`。改为优先使用模型配置中的 `_upstream`，fallback 到 CONFIG：

```python
# 在 _handle_responses() 中
model_cfg = resolve_model(model_name)
target = model_cfg["target"]
upstream_cfg = model_cfg.get("_upstream") or CONFIG.get("upstream", {})
```

```python
# _forward_non_streaming 和 _forward_streaming 签名增加 upstream_override
def _forward_non_streaming(self, chat_body, request_id, model, target, request_ts, upstream_override=None):
    upstream_cfg = upstream_override or CONFIG.get("upstream", {})
    # 其余不变...
```

- [ ] **Step 6: 运行现有测试**

```bash
python3 -m pytest test/ -v
```
Expected: 全部 PASS（确保未破坏原有转发逻辑）

- [ ] **Step 7: 提交**

```bash
git add proxy.py
git commit -m "refactor: proxy.py 集成 ConfigCache — 动态模型路由替换静态 model_map"
```

---

### Task 7: proxy.py — 新增 /admin/reload 端点

**Files:**
- Modify: `proxy.py`

- [ ] **Step 1: 修改 _admin_reload handler**

在 `ProxyHandler.do_POST` 中添加路由：

```python
def do_POST(self):
    if self.path in ("/v1/responses", "/v1/responses/compact"):
        self._handle_responses()
    elif self.path == "/admin/reload":
        self._handle_admin_reload()
    else:
        self._send_json(404, {"error": "not found"})
```

- [ ] **Step 2: 实现 _handle_admin_reload**

```python
def _handle_admin_reload(self):
    """重新加载动态配置。仅允许本地请求。"""
    client_ip = self.client_address[0]
    if client_ip not in ("127.0.0.1", "::1"):
        self._send_json(403, {"error": "forbidden", "message": "仅允许本地请求"})
        return

    try:
        config_cache.reload()
        logging.info(f"配置已重载 (来自 {client_ip})")
        self._send_json(200, {
            "status": "ok",
            "reloaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
    except Exception as e:
        logging.exception("配置重载失败")
        self._send_json(500, {"status": "error", "message": str(e)})
```

- [ ] **Step 3: 运行测试**

```bash
python3 -m pytest test/ -v
```

- [ ] **Step 4: 提交**

```bash
git add proxy.py
git commit -m "feat: 新增 /admin/reload 端点 — 仅允许本地 IP，触发 ConfigCache 重载"
```

---

### Task 8: server.py — 上游 API 路由

**Files:**
- Modify: `server.py`

- [ ] **Step 1: 导入 config_manager**

```python
# server.py 顶部添加
from pathlib import Path
from config_manager import ConfigDB

# 模块级全局
CONFIG_DB_PATH = Path.home() / ".hermes" / "config.db"


def get_config_db():
    return ConfigDB(CONFIG_DB_PATH)
```

- [ ] **Step 2: 在 do_GET 中添加上游路由**

```python
def do_GET(self):
    # ... 现有路由 ...

    # ===== 模型配置 API =====
    if path == "/api/upstreams":
        db = get_config_db()
        upstreams = db.list_upstreams()
        db.close()
        return json_response(self, {"upstreams": upstreams})

    m = re.match(r"/api/upstreams/([^/]+)$", path)
    if m:
        db = get_config_db()
        u = db.get_upstream(m.group(1))
        db.close()
        if u:
            return json_response(self, u)
        return json_response(self, {"error": "Not found"}, 404)
```

- [ ] **Step 3: 在 do_POST 中添加上游路由**

```python
def do_POST(self):
    parsed = urlparse(self.path)
    # ... 现有路由 ...

    # ===== 模型配置 API =====
    if parsed.path == "/api/upstreams":
        data = self._read_json()
        if not data:
            return
        db = get_config_db()
        try:
            uid = db.add_upstream(data)
            db.close()
            return json_response(self, {"id": uid, "message": "Created"}, 201)
        except sqlite3.IntegrityError as e:
            db.close()
            return json_response(self, {"error": str(e)}, 409)

    test_m = re.match(r"/api/upstreams/([^/]+)/test$", parsed.path)
    if test_m:
        uid = test_m.group(1)
        db = get_config_db()
        u = db.get_upstream(uid)
        db.close()
        if not u:
            return json_response(self, {"error": "Not found"}, 404)
        result = _test_upstream_connectivity(u)
        return json_response(self, result)
```

- [ ] **Step 4: 在 do_PUT 中添加上游路由**

```python
def do_PUT(self):
    parsed = urlparse(self.path)
    # ... 现有路由 ...

    m = re.match(r"/api/upstreams/([^/]+)$", parsed.path)
    if m:
        data = self._read_json()
        if not data:
            return
        db = get_config_db()
        try:
            db.update_upstream(m.group(1), data)
            db.close()
            return json_response(self, {"message": "Updated"})
        except sqlite3.IntegrityError as e:
            db.close()
            return json_response(self, {"error": str(e)}, 409)
```

- [ ] **Step 5: 在 do_DELETE 中添加上游路由**

```python
def do_DELETE(self):
    parsed = urlparse(self.path)
    # ... 现有路由 ...

    m = re.match(r"/api/upstreams/([^/]+)$", parsed.path)
    if m:
        uid = m.group(1)
        db = get_config_db()
        u = db.get_upstream(uid)
        if not u:
            db.close()
            return json_response(self, {"error": "Not found"}, 404)
        # 检查活跃路由
        active_routes = db.upstream_active_routes(uid)
        if active_routes:
            db.close()
            return json_response(self, {
                "error": "上游有活跃路由引用，无法禁用",
                "referenced_routes": active_routes,
            }, 409)
        db.disable_upstream(uid)
        db.close()
        return json_response(self, {"message": "Disabled"})
```

- [ ] **Step 6: 添加辅助方法 + 连通性测试函数**

```python
def _read_json(self):
    """读取请求体 JSON，错误时发送 400 并返回 None。"""
    length = int(self.headers.get("Content-Length", 0))
    body = self.rfile.read(length)
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        json_response(self, {"error": "Invalid JSON"}, 400)
        return None


def _test_upstream_connectivity(upstream: dict) -> dict:
    """测试上游连通性：TCP + HTTP GET /。"""
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(upstream["base_url"])
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    result = {"reachable": False, "http_status": None, "latency_ms": 0}

    # TCP 测试
    start = time.time()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    try:
        sock.connect((host, port))
        result["latency_ms"] = int((time.time() - start) * 1000)
        sock.close()
    except (socket.timeout, OSError) as e:
        result["error"] = str(e)
        return result

    # HTTP 测试
    start = time.time()
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", "/")
        resp = conn.getresponse()
        result["reachable"] = True
        result["http_status"] = resp.status
        result["latency_ms"] = int((time.time() - start) * 1000)
        if resp.status == 401:
            result["warning"] = "返回 401，API Key 可能无效，但网络可达"
        if resp.status == 404:
            result["warning"] = "返回 404，端点可能不存在，但服务存活"
    except Exception as e:
        result["error"] = str(e)
    finally:
        conn.close()

    return result
```

- [ ] **Step 7: 添加 http.client 导入**

```python
# server.py 顶部
import http.client
import re
```

- [ ] **Step 8: 运行测试确认未破坏原有功能**

```bash
python3 -m pytest test/ -v
python3 quick_test.py
```

- [ ] **Step 9: 提交**

```bash
git add server.py
git commit -m "feat: server.py 新增上游管理 API — CRUD + 连通性测试"
```

---

### Task 9: server.py — 模型 + 路由 + 配置 API

**Files:**
- Modify: `server.py`

- [ ] **Step 1: 在 do_GET 中添加模型/路由/配置路由**

在 Task 8 的上游路由之后追加：

```python
    # GET /api/models + /api/models/:id
    if path == "/api/models":
        upstream_filter = qs.get("upstream_id", [None])[0]
        db = get_config_db()
        models = db.list_models(upstream_id=upstream_filter)
        db.close()
        return json_response(self, {"models": models})

    m = re.match(r"/api/models/(\d+)$", path)
    if m:
        db = get_config_db()
        model = db.get_model(int(m.group(1)))
        db.close()
        if model:
            return json_response(self, model)
        return json_response(self, {"error": "Not found"}, 404)

    # GET /api/routes + /api/routes/:id
    if path == "/api/routes":
        db = get_config_db()
        routes = db.list_routes()
        db.close()
        return json_response(self, {"routes": routes})

    m = re.match(r"/api/routes/(\d+)$", path)
    if m:
        db = get_config_db()
        route = db.get_route(int(m.group(1)))
        db.close()
        if route:
            return json_response(self, route)
        return json_response(self, {"error": "Not found"}, 404)

    # GET /api/config/status
    if path == "/api/config/status":
        db = get_config_db()
        counts = db.get_counts()
        db.close()
        # 尝试连接 proxy
        proxy_reachable = False
        try:
            conn = http.client.HTTPConnection("127.0.0.1", 48743, timeout=2)
            conn.request("GET", "/health")
            resp = conn.getresponse()
            proxy_reachable = resp.status == 200
            conn.close()
        except Exception:
            pass
        return json_response(self, {
            "proxy_reachable": proxy_reachable,
            "config_db": counts,
        })
```

- [ ] **Step 2: 在 do_POST 中添加模型/路由/配置路由**

```python
    # POST /api/models
    if parsed.path == "/api/models":
        data = self._read_json()
        if not data:
            return
        db = get_config_db()
        try:
            mid = db.add_model(data)
            db.close()
            return json_response(self, {"id": mid, "message": "Created"}, 201)
        except sqlite3.IntegrityError as e:
            db.close()
            return json_response(self, {"error": str(e)}, 409)

    # POST /api/routes
    if parsed.path == "/api/routes":
        data = self._read_json()
        if not data:
            return
        db = get_config_db()
        # 校验 target_model_id
        model = db.get_model(data["target_model_id"])
        if not model:
            db.close()
            return json_response(self, {"error": "target_model_id 不存在"}, 400)
        if not model.get("upstream_active"):
            db.close()
            return json_response(self, {"error": "目标模型所属上游已禁用"}, 400)
        try:
            rid = db.add_route(data)
            db.close()
            return json_response(self, {"id": rid, "message": "Created"}, 201)
        except sqlite3.IntegrityError as e:
            db.close()
            return json_response(self, {"error": str(e)}, 409)

    # POST /api/config/reload
    if parsed.path == "/api/config/reload":
        try:
            conn = http.client.HTTPConnection("127.0.0.1", 48743, timeout=5)
            conn.request("POST", "/admin/reload")
            resp = conn.getresponse()
            body = json.loads(resp.read())
            conn.close()
            return json_response(self, body, resp.status)
        except Exception:
            return json_response(self, {
                "status": "error",
                "message": "proxy 未运行，配置将在 TTL 过期后自动生效",
            })
```

- [ ] **Step 3: 在 do_PUT 中添加模型/路由路由**

```python
    m = re.match(r"/api/models/(\d+)$", parsed.path)
    if m:
        data = self._read_json()
        if not data:
            return
        db = get_config_db()
        try:
            db.update_model(int(m.group(1)), data)
            db.close()
            return json_response(self, {"message": "Updated"})
        except sqlite3.IntegrityError as e:
            db.close()
            return json_response(self, {"error": str(e)}, 409)

    m = re.match(r"/api/routes/(\d+)$", parsed.path)
    if m:
        data = self._read_json()
        if not data:
            return
        db = get_config_db()
        try:
            db.update_route(int(m.group(1)), data)
            db.close()
            return json_response(self, {"message": "Updated"})
        except sqlite3.IntegrityError as e:
            db.close()
            return json_response(self, {"error": str(e)}, 409)
```

- [ ] **Step 4: 在 do_DELETE 中添加模型/路由路由**

```python
    m = re.match(r"/api/models/(\d+)$", parsed.path)
    if m:
        mid = int(m.group(1))
        db = get_config_db()
        # 预检查引用
        refs = db.model_referenced_routes(mid)
        if refs:
            db.close()
            return json_response(self, {
                "error": "模型被以下路由引用，无法删除",
                "referenced_routes": refs,
            }, 409)
        try:
            db.delete_model(mid)
            db.close()
            return json_response(self, {"message": "Deleted"})
        except sqlite3.IntegrityError as e:
            db.close()
            return json_response(self, {"error": str(e)}, 409)

    m = re.match(r"/api/routes/(\d+)$", parsed.path)
    if m:
        rid = int(m.group(1))
        db = get_config_db()
        route = db.get_route(rid)
        if not route:
            db.close()
            return json_response(self, {"error": "Not found"}, 404)
        if route["source"] == "*":
            # 检查是否是最后一条 * 路由
            routes = db.list_routes()
            star_count = sum(1 for r in routes if r["source"] == "*")
            if star_count <= 1:
                db.close()
                return json_response(self, {
                    "error": "不能删除最后一条 * fallback 路由",
                }, 409)
        try:
            db.delete_route(rid)
            db.close()
            return json_response(self, {"message": "Deleted"})
        except sqlite3.IntegrityError as e:
            db.close()
            return json_response(self, {"error": str(e)}, 409)
```

- [ ] **Step 5: 运行测试**

```bash
python3 -m pytest test/ -v
python3 quick_test.py
```

- [ ] **Step 6: 提交**

```bash
git add server.py
git commit -m "feat: server.py 新增模型/路由/配置管理 API — 完整 CRUD + reload + status"
```

---

### Task 10: 前端 — 模型管理页面（HTML/CSS 骨架）

**Files:**
- Modify: `static/index.html`

- [ ] **Step 1: 添加「模型管理」导航标签**

在导航栏 `.nav-tabs` 中添加：

```html
<button class="nav-tab" data-page="models">
  <span>🔌</span> 模型管理
</button>
```

- [ ] **Step 2: 添加模型管理页面 HTML 骨架**

```html
<!-- 模型管理页面 -->
<div id="page-models" class="main-content hidden">
  <!-- 配置状态栏 -->
  <div class="config-status-bar" id="config-status" style="display:flex;align-items:center;gap:12px;padding:10px 16px;margin-bottom:16px;background:hsl(var(--card));border:1px solid hsl(var(--border));border-radius:var(--radius);font-size:12px;">
    <span id="status-proxy-indicator" style="width:8px;height:8px;border-radius:50%;background:hsl(var(--red));display:inline-block;"></span>
    <span id="status-proxy-text">检查中...</span>
    <span style="margin-left:auto;color:hsl(var(--muted-foreground))" id="status-counts"></span>
  </div>

  <!-- 上游列表卡片 -->
  <div class="table-card" style="margin-bottom:20px">
    <div class="table-header">
      <span class="table-title">📡 上游配置</span>
      <div style="display:flex;gap:8px;align-items:center">
        <span style="font-size:12px;color:hsl(var(--muted-foreground))" id="upstream-count"></span>
        <button class="btn btn-primary btn-sm" onclick="showUpstreamModal()">+ 新增上游</button>
      </div>
    </div>
    <div class="table-scroll">
      <table id="upstream-table">
        <thead>
          <tr>
            <th>状态</th>
            <th>名称</th>
            <th>地址</th>
            <th>超时(s)</th>
            <th>默认</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

  <!-- 模型列表卡片 -->
  <div class="table-card" style="margin-bottom:20px">
    <div class="table-header">
      <span class="table-title">🤖 目标模型</span>
      <div style="display:flex;gap:8px;align-items:center">
        <select id="model-filter-upstream" style="padding:4px 8px;border-radius:6px;border:1px solid hsl(var(--border));background:hsl(var(--secondary));color:hsl(var(--foreground));font-size:12px;">
          <option value="">全部上游</option>
        </select>
        <button class="btn btn-primary btn-sm" onclick="showModelModal()">+ 新增模型</button>
      </div>
    </div>
    <div class="table-scroll">
      <table id="model-table">
        <thead>
          <tr>
            <th>模型名</th>
            <th>所属上游</th>
            <th>Format</th>
            <th>Multimodal</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

  <!-- 路由映射卡片 -->
  <div class="table-card" style="margin-bottom:20px">
    <div class="table-header">
      <span class="table-title">🔀 路由映射</span>
      <button class="btn btn-primary btn-sm" onclick="showRouteModal()">+ 新增路由</button>
    </div>
    <div class="table-scroll">
      <table id="route-table">
        <thead>
          <tr>
            <th>源模型</th>
            <th>→ 目标模型</th>
            <th>上游</th>
            <th>状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

  <!-- 底部操作栏 -->
  <div style="display:flex;justify-content:flex-end;gap:12px;margin-top:16px;">
    <button class="btn btn-primary" id="apply-config-btn" onclick="applyConfig()">
      ✅ 应用配置
    </button>
  </div>
</div>
```

- [ ] **Step 3: 添加 CSS（脉冲动画 + 状态栏样式）**

追加到 `<style>` 标签内：

```css
/* 配置状态栏 */
.config-status-bar {
  font-size: 12px;
}

/* 橙色脉冲动画 */
@keyframes pulse-orange {
  0%, 100% { box-shadow: 0 0 0 0 hsl(var(--orange) / 0.4); }
  50% { box-shadow: 0 0 0 8px hsl(var(--orange) / 0); }
}

#apply-config-btn.pulse-orange {
  animation: pulse-orange 1.5s infinite;
  background: hsl(var(--orange));
  border-color: hsl(var(--orange));
  color: white;
}

/* API Key 脱敏 */
.api-key-masked {
  font-family: monospace;
  color: hsl(var(--muted-foreground));
}

/* format tooltip */
.format-with-tooltip {
  cursor: help;
  border-bottom: 1px dashed hsl(var(--muted-foreground));
}
```

- [ ] **Step 4: 提交**

```bash
git add static/index.html
git commit -m "feat: 前端新增模型管理页面 — HTML 骨架 + CSS 样式"
```

---

### Task 11: 前端 — 事件总线 + 三表格 JS 逻辑

**Files:**
- Modify: `static/index.html`

- [ ] **Step 1: 添加导航切换（model 页面）**

```javascript
// 在导航切换监听中追加
document.querySelectorAll('.nav-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    const page = tab.dataset.page;
    currentPage = page;
    // ... 现有逻辑 ...
    document.getElementById('page-models').classList.toggle('hidden', page !== 'models');
    // ... 现有逻辑 ...
    if (page === 'models') loadModelConfig();
  });
});
```

- [ ] **Step 2: 实现事件总线**

```javascript
// ===== 工具函数 =====
function escHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ===== 事件总线 =====
const bus = {
  emit(name, detail) {
    document.dispatchEvent(new CustomEvent(name, { detail }));
  },
  on(name, fn) {
    document.addEventListener(name, fn);
  }
};
```

- [ ] **Step 3: 实现状态栏刷新**

```javascript
async function refreshConfigStatus() {
  try {
    const status = await api('/api/config/status');
    const indicator = document.getElementById('status-proxy-indicator');
    const text = document.getElementById('status-proxy-text');
    const counts = document.getElementById('status-counts');

    if (status.proxy_reachable) {
      indicator.style.background = 'hsl(var(--green))';
      text.textContent = 'proxy 在线 · 配置已生效';
    } else {
      indicator.style.background = 'hsl(var(--orange))';
      text.textContent = 'proxy 离线 · 配置生效中（TTL 模式）';
    }
    counts.textContent = `${status.config_db.upstreams} 上游 · ${status.config_db.models} 模型 · ${status.config_db.routes} 路由`;
  } catch (e) {
    console.error('状态查询失败:', e);
  }
}
```

- [ ] **Step 4: 实现上游表格渲染**

```javascript
async function loadUpstreamTable() {
  const data = await api('/api/upstreams');
  const tbody = document.querySelector('#upstream-table tbody');
  document.getElementById('upstream-count').textContent = `${data.upstreams.length} 个上游`;

  tbody.innerHTML = data.upstreams.map(u => `
    <tr style="${u.is_active ? '' : 'opacity:0.5'}">
      <td><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${u.is_active ? 'hsl(var(--green))' : 'hsl(var(--red))'};"></span> ${u.is_active ? '活跃' : '已禁用'}</td>
      <td><span class="badge badge-blue">${escHtml(u.id)}</span></td>
      <td style="font-family:monospace;font-size:12px">${escHtml(u.base_url)}</td>
      <td>${u.timeout}s</td>
      <td>${u.is_default ? '✅' : ''}</td>
      <td>
        <button class="btn btn-secondary btn-sm" onclick="showUpstreamModal('${escHtml(u.id)}')">编辑</button>
        <button class="btn btn-secondary btn-sm" onclick="testUpstream('${escHtml(u.id)}')">测试</button>
        ${u.is_active ? `<button class="btn btn-danger btn-sm" onclick="confirmDisableUpstream('${escHtml(u.id)}')">禁用</button>` : ''}
      </td>
    </tr>
  `).join('');
}

function escHtml(s) {
  const d = document.createElement('div'); d.textContent = s; return d.innerHTML;
}
```

- [ ] **Step 5: 实现模型表格渲染**

```javascript
async function loadModelTable(upstreamId) {
  let url = '/api/models';
  if (upstreamId) url += `?upstream_id=${encodeURIComponent(upstreamId)}`;
  const data = await api(url);
  const tbody = document.querySelector('#model-table tbody');

  tbody.innerHTML = data.models.map(m => `
    <tr>
      <td><span class="badge badge-green">${escHtml(m.name)}</span></td>
      <td><span class="badge" style="background:hsl(var(--muted));color:hsl(var(--muted-foreground))">${escHtml(m.upstream_name)}</span></td>
      <td><span class="format-with-tooltip" title="当前所有上游统一使用格式转换，此字段暂不生效">${escHtml(m.format)}</span></td>
      <td>${m.multimodal ? '✅' : '❌'}</td>
      <td>
        <button class="btn btn-secondary btn-sm" onclick="showModelModal(${m.id})">编辑</button>
        <button class="btn btn-danger btn-sm" onclick="confirmDeleteModel(${m.id}, '${escHtml(m.name)}')">删除</button>
      </td>
    </tr>
  `).join('') || '<tr><td colspan="5" class="empty-state">暂无模型</td></tr>';
}

// 上游过滤器变化时重新加载
document.getElementById('model-filter-upstream').addEventListener('change', (e) => {
  loadModelTable(e.target.value);
});

// 刷新上游 dropdown 选项
async function refreshUpstreamDropdown() {
  const data = await api('/api/upstreams');
  const active = data.upstreams.filter(u => u.is_active);
  const select = document.getElementById('model-filter-upstream');
  select.innerHTML = '<option value="">全部上游</option>' +
    active.map(u => `<option value="${escHtml(u.id)}">${escHtml(u.id)}</option>`).join('');
}

async function refreshModelDropdown() {
  // 在模态框打开时调用，不在这里实现
}
```

- [ ] **Step 6: 实现路由表格渲染**

```javascript
async function loadRouteTable() {
  const data = await api('/api/routes');
  const tbody = document.querySelector('#route-table tbody');

  tbody.innerHTML = data.routes.map(r => `
    <tr style="${r.source === '*' ? 'background:hsl(var(--primary) / 0.05);' : ''} ${r.upstream_active ? '' : 'opacity:0.5'}">
      <td><span class="badge badge-purple">${escHtml(r.source)}${r.source === '*' ? ' (★ fallback)' : ''}</span></td>
      <td>→ <span class="badge badge-green">${escHtml(r.target_name)}</span></td>
      <td><span class="badge" style="background:hsl(var(--muted));color:hsl(var(--muted-foreground))">${escHtml(r.upstream_id)}</span></td>
      <td>${r.upstream_active ? '<span style="color:hsl(var(--green))">活跃</span>' : '<span style="color:hsl(var(--red))">上游已禁用</span>'}</td>
      <td>
        <button class="btn btn-secondary btn-sm" onclick="showRouteModal(${r.id})">编辑</button>
        <button class="btn btn-danger btn-sm" onclick="confirmDeleteRoute(${r.id}, '${escHtml(r.source)}')">删除</button>
      </td>
    </tr>
  `).join('') || '<tr><td colspan="5" class="empty-state">暂无路由</td></tr>';
}
```

- [ ] **Step 7: 实现模态框（上游/模型/路由编辑）**

```javascript
// ─── 上游模态框 ───
async function showUpstreamModal(editId) {
  let data = { id: '', base_url: '', api_key: '', timeout: 120, connect_timeout: 10, ssl_verify: 1, retry: 1, is_default: 0 };
  let title = '新增上游';

  if (editId) {
    title = `编辑上游: ${editId}`;
    const upstreams = await api('/api/upstreams');
    const found = upstreams.upstreams.find(u => u.id === editId);
    if (found) data = found;
  }

  showModal(title, `
    <div class="form-group">
      <label class="form-label">名称 (ID)</label>
      <input type="text" class="form-input" id="up-id" value="${escHtml(data.id)}" ${editId ? 'readonly' : ''} placeholder="如 litellm-prod">
    </div>
    <div class="form-group">
      <label class="form-label">Base URL</label>
      <input type="text" class="form-input" id="up-url" value="${escHtml(data.base_url)}" placeholder="https://...">
    </div>
    <div class="form-group">
      <label class="form-label">API Key</label>
      <input type="text" class="form-input" id="up-key" value="${escHtml(data.api_key)}" placeholder="sk-...">
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
      <div class="form-group">
        <label class="form-label">超时 (s)</label>
        <input type="number" class="form-input" id="up-timeout" value="${data.timeout}" min="1">
      </div>
      <div class="form-group">
        <label class="form-label">连接超时 (s)</label>
        <input type="number" class="form-input" id="up-conn-timeout" value="${data.connect_timeout}" min="1">
      </div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;">
      <div class="form-group">
        <label class="form-label">SSL 验证</label>
        <select class="form-input" id="up-ssl">${['','selected'][data.ssl_verify] ? '<option value="1" selected>开启</option><option value="0">关闭</option>' : '<option value="1">开启</option><option value="0" selected>关闭</option>'}</select>
      </div>
      <div class="form-group">
        <label class="form-label">重试次数</label>
        <input type="number" class="form-input" id="up-retry" value="${data.retry}" min="0">
      </div>
      <div class="form-group">
        <label class="form-label">设为默认</label>
        <select class="form-input" id="up-default">
          <option value="1" ${data.is_default ? 'selected' : ''}>是</option>
          <option value="0" ${!data.is_default ? 'selected' : ''}>否</option>
        </select>
      </div>
    </div>
  `, `
    <button class="btn btn-secondary" onclick="closeModal()">取消</button>
    <button class="btn btn-primary" onclick="saveUpstream('${editId || ''}')">保存</button>
  `);
}

async function saveUpstream(editId) {
  const data = {
    base_url: document.getElementById('up-url').value,
    api_key: document.getElementById('up-key').value,
    timeout: parseInt(document.getElementById('up-timeout').value) || 120,
    connect_timeout: parseInt(document.getElementById('up-conn-timeout').value) || 10,
    ssl_verify: parseInt(document.getElementById('up-ssl').value),
    retry: parseInt(document.getElementById('up-retry').value) || 1,
    is_default: parseInt(document.getElementById('up-default').value),
  };
  if (!editId) data.id = document.getElementById('up-id').value.trim();
  if (!data.id && !editId) { alert('名称不能为空'); return; }
  if (!data.base_url) { alert('Base URL 不能为空'); return; }

  try {
    if (editId) {
      await api(`/api/upstreams/${editId}`, { method: 'PUT', body: JSON.stringify(data) });
    } else {
      await api('/api/upstreams', { method: 'POST', body: JSON.stringify(data) });
    }
    closeModal();
    bus.emit('config:upstream-changed', {});
    bus.emit('config:dirty', { source: 'upstream' });
    loadAllModelConfigTables();
  } catch (e) {
    alert('操作失败: ' + e.message);
  }
}

async function testUpstream(id) {
  const result = await api(`/api/upstreams/${id}/test`, { method: 'POST' });
  if (result.reachable) {
    alert(`✅ 连通正常 (${result.latency_ms}ms)${result.warning ? '\n⚠️ ' + result.warning : ''}`);
  } else {
    alert(`❌ 不可达: ${result.error || '未知错误'}`);
  }
}

async function confirmDisableUpstream(id) {
  // 获取上游信息
  const data = await api('/api/upstreams');
  const u = data.upstreams.find(x => x.id === id);
  if (!u) return;

  // 获取模型和路由计数
  const models = await api(`/api/models?upstream_id=${encodeURIComponent(id)}`);
  const routes = await api('/api/routes');
  const affectedRoutes = routes.routes.filter(r => r.upstream_id === id);

  const msg = `确认禁用上游 "${id}"？\n\n` +
    `关联模型: ${models.models.length} 个\n` +
    `活跃路由引用: ${affectedRoutes.length} 个\n\n` +
    `禁用后相关路由将无法使用。`;

  if (!confirm(msg)) return;

  const result = await api(`/api/upstreams/${id}`, { method: 'DELETE' });
  if (result.error) {
    alert(`❌ 无法禁用: ${result.error}\n\n被引用的路由: ${(result.referenced_routes || []).join(', ')}`);
  } else {
    bus.emit('config:upstream-changed', {});
    bus.emit('config:dirty', { source: 'upstream' });
    loadAllModelConfigTables();
  }
}

// ─── 模型模态框 ───
async function showModelModal(editId) {
  let data = { name: '', upstream_id: '', multimodal: 1, format: 'openai_chat' };
  let title = '新增模型';

  if (editId) {
    title = `编辑模型 #${editId}`;
    const models = await api('/api/models');
    const found = models.models.find(m => m.id === editId);
    if (found) data = found;
  }

  const upstreams = await api('/api/upstreams');
  const activeUpstreams = upstreams.upstreams.filter(u => u.is_active);
  const upstreamOpts = activeUpstreams.map(u =>
    `<option value="${escHtml(u.id)}" ${data.upstream_id === u.id ? 'selected' : ''}>${escHtml(u.id)}</option>`
  ).join('');

  showModal(title, `
    <div class="form-group">
      <label class="form-label">模型名</label>
      <input type="text" class="form-input" id="m-name" value="${escHtml(data.name)}" placeholder="如 qwen-plus">
    </div>
    <div class="form-group">
      <label class="form-label">所属上游</label>
      <select class="form-input" id="m-upstream">${upstreamOpts}</select>
    </div>
    <div class="form-group">
      <label class="form-label">Format <span title="当前所有上游统一使用格式转换，此字段暂不生效" style="cursor:help;border-bottom:1px dashed">ⓘ</span></label>
      <select class="form-input" id="m-format">
        <option value="openai_chat" ${data.format === 'openai_chat' ? 'selected' : ''}>openai_chat</option>
        <option value="openai_responses" ${data.format === 'openai_responses' ? 'selected' : ''}>openai_responses</option>
        <option value="anthropic" ${data.format === 'anthropic' ? 'selected' : ''}>anthropic</option>
        <option value="openai_chat,openai_responses" ${data.format === 'openai_chat,openai_responses' ? 'selected' : ''}>openai_chat + openai_responses</option>
      </select>
    </div>
    <div class="form-group">
      <label class="form-label">Multimodal</label>
      <select class="form-input" id="m-multimodal">
        <option value="1" ${data.multimodal ? 'selected' : ''}>✅ 支持</option>
        <option value="0" ${!data.multimodal ? 'selected' : ''}>❌ 不支持</option>
      </select>
    </div>
  `, `
    <button class="btn btn-secondary" onclick="closeModal()">取消</button>
    <button class="btn btn-primary" onclick="saveModel(${editId || 0})">保存</button>
  `);
}

async function saveModel(editId) {
  const data = {
    name: document.getElementById('m-name').value.trim(),
    upstream_id: document.getElementById('m-upstream').value,
    multimodal: parseInt(document.getElementById('m-multimodal').value),
    format: document.getElementById('m-format').value,
  };
  if (!data.name) { alert('模型名不能为空'); return; }
  if (!data.upstream_id) { alert('请选择上游'); return; }

  try {
    if (editId) {
      await api(`/api/models/${editId}`, { method: 'PUT', body: JSON.stringify(data) });
    } else {
      await api('/api/models', { method: 'POST', body: JSON.stringify(data) });
    }
    closeModal();
    bus.emit('config:model-changed', {});
    bus.emit('config:dirty', { source: 'model' });
    loadAllModelConfigTables();
  } catch (e) {
    alert('操作失败: ' + e.message);
  }
}

async function confirmDeleteModel(id, name) {
  if (!confirm(`确认删除模型 "${name}"？`)) return;
  const result = await api(`/api/models/${id}`, { method: 'DELETE' });
  if (result.error) {
    alert(`❌ 无法删除: ${result.error}\n\n被引用的路由: ${(result.referenced_routes || []).join(', ')}`);
  } else {
    bus.emit('config:model-changed', {});
    bus.emit('config:dirty', { source: 'model' });
    loadAllModelConfigTables();
  }
}

// ─── 路由模态框 ───
async function showRouteModal(editId) {
  let data = { source: '', target_model_id: '' };
  let title = '新增路由';

  if (editId) {
    title = `编辑路由 #${editId}`;
    const routes = await api('/api/routes');
    const found = routes.routes.find(r => r.id === editId);
    if (found) data = found;
  }

  const models = await api('/api/models');
  const byUpstream = {};
  models.models.forEach(m => {
    if (!byUpstream[m.upstream_name]) byUpstream[m.upstream_name] = [];
    byUpstream[m.upstream_name].push(m);
  });

  let modelOpts = '';
  for (const [upstream, mlist] of Object.entries(byUpstream)) {
    modelOpts += `<optgroup label="${escHtml(upstream)}">`;
    mlist.forEach(m => {
      modelOpts += `<option value="${m.id}" ${data.target_model_id === m.id ? 'selected' : ''}>${escHtml(m.name)}</option>`;
    });
    modelOpts += '</optgroup>';
  }

  showModal(title, `
    <div class="form-group">
      <label class="form-label">源模型名</label>
      <input type="text" class="form-input" id="r-source" value="${escHtml(data.source)}" placeholder="如 gpt-4o 或 * (fallback)">
    </div>
    <div class="form-group">
      <label class="form-label">目标模型</label>
      <select class="form-input" id="r-target">${modelOpts}</select>
    </div>
  `, `
    <button class="btn btn-secondary" onclick="closeModal()">取消</button>
    <button class="btn btn-primary" onclick="saveRoute(${editId || 0})">保存</button>
  `);
}

async function saveRoute(editId) {
  const data = {
    source: document.getElementById('r-source').value.trim(),
    target_model_id: parseInt(document.getElementById('r-target').value),
  };
  if (!data.source) { alert('源模型名不能为空'); return; }

  try {
    if (editId) {
      await api(`/api/routes/${editId}`, { method: 'PUT', body: JSON.stringify(data) });
    } else {
      await api('/api/routes', { method: 'POST', body: JSON.stringify(data) });
    }
    closeModal();
    bus.emit('config:route-changed', {});
    bus.emit('config:dirty', { source: 'route' });
    loadAllModelConfigTables();
  } catch (e) {
    alert('操作失败: ' + e.message);
  }
}

async function confirmDeleteRoute(id, source) {
  if (source === '*') {
    const routes = await api('/api/routes');
    const starCount = routes.routes.filter(r => r.source === '*').length;
    if (starCount <= 1) {
      alert('❌ 不能删除最后一条 * fallback 路由');
      return;
    }
  }
  if (!confirm(`确认删除路由 "${source}"？`)) return;
  const result = await api(`/api/routes/${id}`, { method: 'DELETE' });
  if (result.error) {
    alert(`❌ ${result.error}`);
  } else {
    bus.emit('config:route-changed', {});
    bus.emit('config:dirty', { source: 'route' });
    loadAllModelConfigTables();
  }
}

// ─── 应用配置 ───
async function applyConfig() {
  const btn = document.getElementById('apply-config-btn');
  btn.textContent = '⏳ 应用中...';
  btn.disabled = true;

  const result = await api('/api/config/reload', { method: 'POST' });

  if (result.status === 'ok') {
    bus.emit('config:applied', { reloaded_at: result.reloaded_at });
    btn.classList.remove('pulse-orange');
    btn.textContent = '✅ 应用配置';
    refreshConfigStatus();
    alert(`配置已生效 (${result.reloaded_at})`);
  } else {
    alert('⚠️ ' + (result.message || '重载失败'));
    btn.textContent = '🔄 重试';
  }
  btn.disabled = false;
}

// ─── 事件订阅 ───
bus.on('config:dirty', () => {
  const btn = document.getElementById('apply-config-btn');
  btn.classList.add('pulse-orange');
  btn.textContent = '⚠️ 应用配置';
});

bus.on('config:applied', () => {
  const btn = document.getElementById('apply-config-btn');
  btn.classList.remove('pulse-orange');
  btn.textContent = '✅ 应用配置';
});

bus.on('config:upstream-changed', () => {
  refreshUpstreamDropdown();
  refreshConfigStatus();
});

bus.on('config:model-changed', () => {
  refreshConfigStatus();
});

bus.on('config:route-changed', () => {
  refreshConfigStatus();
});

// ─── 统一刷新 ───
function loadAllModelConfigTables() {
  loadUpstreamTable();
  loadModelTable(document.getElementById('model-filter-upstream').value);
  loadRouteTable();
}

async function loadModelConfig() {
  await refreshConfigStatus();
  await refreshUpstreamDropdown();
  loadAllModelConfigTables();
}
```

- [ ] **Step 5: 提交**

```bash
git add static/index.html
git commit -m "feat: 前端事件总线 + 三表格完整 CRUD 交互逻辑"
```

---

### Task 12: 集成测试

**Files:**
- Create: `test/test_config_integration.py`

- [ ] **Step 1: 写集成测试 — server.py API 端点**

```python
"""动态模型配置 — server.py API 集成测试。"""
import json
import unittest
import tempfile
import threading
import time
from pathlib import Path
from http.server import HTTPServer

# 由于 server.py 有模块级代码，这里仅测试 config_manager + 手动请求
from config_manager import ConfigDB, ConfigCache


class TestConfigIntegration(unittest.TestCase):
    """端到端：ConfigDB → ConfigCache → resolve 完整链路。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "config.db"
        self.db = ConfigDB(self.db_path)
        # 种子数据
        self.db.add_upstream({"id": "up-a", "base_url": "http://a:4000", "api_key": "sk-a"})
        self.db.add_upstream({"id": "up-b", "base_url": "http://b:5000", "api_key": "sk-b"})
        self.m1 = self.db.add_model({"name": "qwen", "upstream_id": "up-a", "multimodal": 1})
        self.m2 = self.db.add_model({"name": "claude", "upstream_id": "up-b", "multimodal": 0})
        self.db.add_route({"source": "gpt-4", "target_model_id": self.m1})
        self.db.add_route({"source": "codex-mini", "target_model_id": self.m1})
        self.db.add_route({"source": "*", "target_model_id": self.m2})

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_config_cache_resolve(self):
        cache = ConfigCache(self.db_path, ttl=5)
        cfg = cache.resolve("gpt-4")
        self.assertEqual(cfg["target_name"], "qwen")
        self.assertEqual(cfg["upstream"]["base_url"], "http://a:4000")

    def test_config_cache_fallback(self):
        cache = ConfigCache(self.db_path, ttl=5)
        cfg = cache.resolve("unknown-model")
        self.assertEqual(cfg["target_name"], "claude")

    def test_disable_upstream_affects_resolve(self):
        self.db.disable_upstream("up-a")
        cache = ConfigCache(self.db_path, ttl=0)  # TTL=0 确保即时刷新
        cfg = cache.resolve("gpt-4")
        # gpt-4 → qwen@up-a, 但 up-a 已禁用 → 走 * → claude@up-b
        self.assertEqual(cfg["target_name"], "claude")

    def test_star_fallback_validation(self):
        self.db.disable_upstream("up-b")
        # * → claude@up-b, up-b 禁用 → * 不可用
        self.assertFalse(self.db.validate_star_fallback())

    def test_model_referenced_routes_precheck(self):
        refs = self.db.model_referenced_routes(self.m1)
        self.assertEqual(set(refs), {"gpt-4", "codex-mini"})
        # 不能删除被引用的模型
        self.assertGreater(len(refs), 0)

    def test_upstream_active_routes(self):
        refs = self.db.upstream_active_routes("up-a")
        self.assertIn("gpt-4", refs)

    def test_get_counts(self):
        counts = self.db.get_counts()
        self.assertEqual(counts["upstreams"], 2)
        self.assertEqual(counts["models"], 2)
        self.assertEqual(counts["routes"], 3)

    def test_cache_reload(self):
        cache = ConfigCache(self.db_path, ttl=99)
        cfg1 = cache.resolve("gpt-4")
        # 修改路由
        rid = self.db.get_route_by_source("gpt-4")["id"]
        self.db.update_route(rid, {"target_model_id": self.m2})
        cache.reload()
        cfg2 = cache.resolve("gpt-4")
        self.assertEqual(cfg2["target_name"], "claude")

    def test_cache_get_all(self):
        cache = ConfigCache(self.db_path, ttl=5)
        all_routes = cache.get_all()
        self.assertIn("gpt-4", all_routes)
        self.assertIn("codex-mini", all_routes)
        self.assertIn("*", all_routes)
```

- [ ] **Step 2: 运行集成测试**

```bash
python3 -m pytest test/test_config_integration.py -v
```
Expected: 全部 PASS

- [ ] **Step 3: 运行全部测试确认无回归**

```bash
python3 -m pytest test/ -v
```
Expected: 全部 PASS

- [ ] **Step 4: 提交**

```bash
git add test/test_config_integration.py
git commit -m "test: 集成测试 — ConfigDB → ConfigCache → resolve 完整链路"
```

---

### 验证清单

- [ ] `python3 -m pytest test/ -v` 全部通过
- [ ] `python3 quick_test.py` 通过
- [ ] 启动 server + proxy：`./server.sh start`
- [ ] 浏览器打开 http://127.0.0.1:18742 → 模型管理 Tab
- [ ] 新增上游 → 连通性测试 → 新增模型 → 新增路由 → 点击"应用配置"
- [ ] curl 测试 proxy 模型列表：`curl http://127.0.0.1:48743/v1/models`
- [ ] curl 测试 /admin/reload 安全：`curl http://127.0.0.1:48743/admin/reload -X POST` 返回 200
