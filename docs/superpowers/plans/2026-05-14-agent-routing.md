# Agent 智能路由 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 proxy 中实现子 agent 检测 + agent 路由覆盖层，使子 agent 请求可路由到不同上游/模型。

**Architecture:** 新增 `agent_detector.py` 检测模块（第 0 层）+ `agent_routes` DB 表 + ConfigCache `_agent_routes` 缓存。handler.py 检测到子 agent 后先查 agent 路由，未命中或上游禁用则静默回退主路由。前端在路由映射页新增 Agent 路由表格。

**Tech Stack:** Python 标准库 + SQLite + openai SDK（不变）

---

## File Structure

| 文件 | 变更类型 | 职责 |
|------|----------|------|
| `proxy/agent_detector.py` | 新增 | 子 agent 检测：`__SUBAGENT_MARKER__` + `metadata.user_id` |
| `proxy/config_manager.py` | 修改 | agent_routes 表 + CRUD + ConfigCache + v6→v7 迁移 |
| `proxy/handler.py` | 修改 | 集成 agent 检测 + 路由查找 |
| `proxy/__init__.py` | 修改 | re-export `detect_subagent` |
| `proxy/request_logger.py` | 修改 | data JSON 新增 `is_agent` 字段 |
| `server/config_api.py` | 修改 | `/api/agent-routes` CRUD 端点 |
| `server/handler.py` | 修改 | 分发表注册 |
| `static/js/pages/routes.js` | 修改 | Agent 路由表格 + 模态框 + 联动 |
| `static/css/routes.css` | 修改 | Agent 路由卡片样式 |
| `test/test_agent_detector.py` | 新增 | 检测信号测试 |
| `test/test_config_manager.py` | 修改 | agent_routes CRUD + resolve_agent + 迁移 |

---

### Task 1: agent_detector.py 检测模块

**Files:**
- Create: `proxy/agent_detector.py`
- Create: `test/test_agent_detector.py`

- [ ] **Step 1: 写检测测试**

创建 `test/test_agent_detector.py`：

```python
import unittest


class TestDetectSubagent(unittest.TestCase):
    def _make_body(self, messages=None, metadata=None):
        body = {}
        if messages is not None:
            body["messages"] = messages
        if metadata is not None:
            body["metadata"] = metadata
        return body

    def test_normal_user_message_not_subagent(self):
        from proxy.agent_detector import detect_subagent
        body = self._make_body(messages=[{"role": "user", "content": "hello"}])
        self.assertFalse(detect_subagent(body))

    def test_system_message_with_marker(self):
        from proxy.agent_detector import detect_subagent
        body = self._make_body(messages=[
            {"role": "system", "content": '<system-reminder>{"__SUBAGENT_MARKER__": {"session_id": "abc"}}</system-reminder>'}
        ])
        self.assertTrue(detect_subagent(body))

    def test_user_message_with_marker(self):
        from proxy.agent_detector import detect_subagent
        body = self._make_body(messages=[
            {"role": "user", "content": '<system-reminder>{"__SUBAGENT_MARKER__": {"agent_id": "123"}}</system-reminder>'}
        ])
        self.assertTrue(detect_subagent(body))

    def test_content_blocks_with_marker(self):
        from proxy.agent_detector import detect_subagent
        body = self._make_body(messages=[
            {"role": "user", "content": [
                {"type": "text", "text": "task description"},
                {"type": "text", "text": '<system-reminder>{"__SUBAGENT_MARKER__": {}}</system-reminder>'}
            ]}
        ])
        self.assertTrue(detect_subagent(body))

    def test_metadata_user_id_contains_agent(self):
        from proxy.agent_detector import detect_subagent
        body = self._make_body(
            messages=[{"role": "user", "content": "hello"}],
            metadata={"user_id": "sess123_agent_agent456"}
        )
        self.assertTrue(detect_subagent(body))

    def test_metadata_user_id_no_agent(self):
        from proxy.agent_detector import detect_subagent
        body = self._make_body(
            messages=[{"role": "user", "content": "hello"}],
            metadata={"user_id": "normal_user"}
        )
        self.assertFalse(detect_subagent(body))

    def test_empty_body(self):
        from proxy.agent_detector import detect_subagent
        self.assertFalse(detect_subagent({}))

    def test_no_messages_key(self):
        from proxy.agent_detector import detect_subagent
        self.assertFalse(detect_subagent({"metadata": {"user_id": "normal"}}))

    def test_content_blocks_without_text(self):
        from proxy.agent_detector import detect_subagent
        body = self._make_body(messages=[
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "http://example.com"}}
            ]}
        ])
        self.assertFalse(detect_subagent(body))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest test/test_agent_detector.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'proxy.agent_detector'`

- [ ] **Step 3: 写 agent_detector.py 实现**

创建 `proxy/agent_detector.py`：

```python
"""Agent 检测模块 — 判断请求是否来自 Claude Code 子 agent。"""


def detect_subagent(body: dict) -> bool:
    """检测请求是否来自 Claude Code 子 agent。

    两个信号：
    1. __SUBAGENT_MARKER__ 在 system/user 消息文本中
    2. metadata.user_id 含 _agent_ 字符串
    """
    if _contains_marker(body, "__SUBAGENT_MARKER__"):
        return True

    user_id = body.get("metadata", {}).get("user_id", "")
    if user_id and "_agent_" in user_id:
        return True

    return False


def _contains_marker(body: dict, marker: str) -> bool:
    """在消息文本中搜索标记。处理 string 和 content blocks 两种消息格式。"""
    def _extract_text(msg):
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        return ""

    for msg in body.get("messages", []):
        role = msg.get("role", "")
        if role in ("system", "user"):
            if marker in _extract_text(msg):
                return True
    return False
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest test/test_agent_detector.py -v`
Expected: 9 passed

- [ ] **Step 5: 提交**

```bash
git add proxy/agent_detector.py test/test_agent_detector.py
git commit -m "feat: agent 检测模块 — SUBAGENT_MARKER + metadata.user_id"
```

---

### Task 2: ConfigDB agent_routes 表 + CRUD + 迁移

**Files:**
- Modify: `proxy/config_manager.py`
- Modify: `test/test_config_manager.py`

- [ ] **Step 1: 写 ConfigDB agent_routes CRUD 测试**

在 `test/test_config_manager.py` 末尾追加：

```python
class TestAgentRouteCRUD(unittest.TestCase):
    def setUp(self):
        from proxy.config_manager import ConfigDB
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "config.db"
        self.db = ConfigDB(self.db_path)
        # 创建上游 + 模型供路由引用
        self.upstream_id = self.db.add_upstream({
            "name": "test-upstream", "base_url": "http://localhost:8000",
            "api_key": "sk-test", "format": "chat_completions"
        })
        self.model_id = self.db.add_model({
            "name": "test-model", "upstream_id": self.upstream_id
        })

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_add_and_list_agent_routes(self):
        rid = self.db.add_agent_route({
            "source": "claude-sonnet-4-6",
            "target_model_id": self.model_id,
            "request_type": "chat_completions"
        })
        self.assertIsNotNone(rid)
        routes = self.db.list_agent_routes()
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["source"], "claude-sonnet-4-6")

    def test_list_agent_routes_filter_by_request_type(self):
        self.db.add_agent_route({
            "source": "m1", "target_model_id": self.model_id,
            "request_type": "chat_completions"
        })
        self.db.add_agent_route({
            "source": "m2", "target_model_id": self.model_id,
            "request_type": "responses"
        })
        self.assertEqual(len(self.db.list_agent_routes(request_type="chat_completions")), 1)
        self.assertEqual(len(self.db.list_agent_routes(request_type="responses")), 1)

    def test_add_agent_route_source_star_rejected(self):
        with self.assertRaises(ValueError):
            self.db.add_agent_route({
                "source": "*", "target_model_id": self.model_id,
                "request_type": "chat_completions"
            })

    def test_add_agent_route_duplicate_raises(self):
        self.db.add_agent_route({
            "source": "m1", "target_model_id": self.model_id,
            "request_type": "chat_completions"
        })
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.add_agent_route({
                "source": "m1", "target_model_id": self.model_id,
                "request_type": "chat_completions"
            })

    def test_add_agent_route_inactive_upstream_rejected(self):
        self.db.update_upstream(self.upstream_id, {"is_active": 0})
        with self.assertRaises(ValueError):
            self.db.add_agent_route({
                "source": "m1", "target_model_id": self.model_id,
                "request_type": "chat_completions"
            })

    def test_get_agent_route(self):
        rid = self.db.add_agent_route({
            "source": "m1", "target_model_id": self.model_id,
            "request_type": "chat_completions"
        })
        route = self.db.get_agent_route(rid)
        self.assertIsNotNone(route)
        self.assertEqual(route["source"], "m1")

    def test_get_agent_route_not_found(self):
        self.assertIsNone(self.db.get_agent_route(999))

    def test_update_agent_route(self):
        rid = self.db.add_agent_route({
            "source": "m1", "target_model_id": self.model_id,
            "request_type": "chat_completions"
        })
        self.db.update_agent_route(rid, {"source": "m1-updated"})
        route = self.db.get_agent_route(rid)
        self.assertEqual(route["source"], "m1-updated")

    def test_update_agent_route_star_rejected(self):
        rid = self.db.add_agent_route({
            "source": "m1", "target_model_id": self.model_id,
            "request_type": "chat_completions"
        })
        with self.assertRaises(ValueError):
            self.db.update_agent_route(rid, {"source": "*"})

    def test_delete_agent_route(self):
        rid = self.db.add_agent_route({
            "source": "m1", "target_model_id": self.model_id,
            "request_type": "chat_completions"
        })
        self.db.delete_agent_route(rid)
        self.assertIsNone(self.db.get_agent_route(rid))

    def test_resolve_agent_found(self):
        self.db.add_agent_route({
            "source": "claude-sonnet-4-6", "target_model_id": self.model_id,
            "request_type": "chat_completions"
        })
        result = self.db.resolve_agent("claude-sonnet-4-6", "chat_completions")
        self.assertIsNotNone(result)
        self.assertEqual(result["target_name"], "test-model")

    def test_resolve_agent_not_found(self):
        result = self.db.resolve_agent("nonexistent", "chat_completions")
        self.assertIsNone(result)

    def test_resolve_agent_inactive_upstream_returns_none(self):
        self.db.add_agent_route({
            "source": "m1", "target_model_id": self.model_id,
            "request_type": "chat_completions"
        })
        self.db.update_upstream(self.upstream_id, {"is_active": 0})
        result = self.db.resolve_agent("m1", "chat_completions")
        self.assertIsNone(result)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest test/test_config_manager.py::TestAgentRouteCRUD -v`
Expected: FAIL — `AttributeError: 'ConfigDB' object has no attribute 'add_agent_route'`

- [ ] **Step 3: 在 ConfigDB._ensure_db() 中新增 agent_routes 建表**

在 `proxy/config_manager.py` 的 `_ensure_db()` 方法的 `conn.executescript(...)` 中，`model_routes` 表的 `CREATE TABLE` 之后追加：

```sql
CREATE TABLE IF NOT EXISTS agent_routes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL CHECK(length(source) > 0 AND source != '*'),
    target_model_id INTEGER NOT NULL REFERENCES target_models(id) ON DELETE RESTRICT,
    request_type    TEXT NOT NULL DEFAULT 'chat_completions'
                    CHECK(request_type IN ('responses', 'messages', 'chat_completions')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source, request_type)
);
```

- [ ] **Step 4: 在 ConfigDB 中新增 agent_routes CRUD 方法**

在 `proxy/config_manager.py` 的 `ConfigDB` 类中，路由映射 CRUD 段之后新增：

```python
    # ─── Agent 路由 CRUD ────────────────────────────────────────────

    def list_agent_routes(self, request_type: Optional[str] = None):
        conn = self._connect()
        try:
            params = []
            where = ""
            if request_type is not None:
                where = "WHERE ar.request_type = ?"
                params.append(request_type)
            rows = conn.execute(
                f"""SELECT ar.*, tm.name as target_name, tm.upstream_id,
                          u.name as upstream_name,
                          u.is_active as upstream_active,
                          u.format as upstream_format
                   FROM agent_routes ar
                   JOIN target_models tm ON ar.target_model_id = tm.id
                   JOIN upstreams u ON tm.upstream_id = u.id
                   {where}
                   ORDER BY ar.source""",
                params,
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_agent_route(self, route_id: int) -> Optional[dict]:
        conn = self._connect()
        try:
            row = conn.execute(
                """SELECT ar.*, tm.name as target_name, tm.upstream_id
                   FROM agent_routes ar
                   JOIN target_models tm ON ar.target_model_id = tm.id
                   WHERE ar.id = ?""",
                (route_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def add_agent_route(self, data: dict) -> int:
        source = data.get("source", "")
        if source == "*":
            raise ValueError("agent_routes 不允许 source='*'")
        request_type = data.get("request_type", "chat_completions")
        if request_type not in ("responses", "messages", "chat_completions"):
            raise ValueError("request_type must be one of: responses, messages, chat_completions")
        conn = self._connect()
        try:
            active = conn.execute(
                """SELECT 1 FROM target_models tm
                   JOIN upstreams u ON tm.upstream_id = u.id
                   WHERE tm.id = ? AND u.is_active = 1""",
                (data["target_model_id"],),
            ).fetchone()
            if not active:
                raise ValueError("目标模型不存在或所属上游已禁用")
            cursor = conn.execute(
                "INSERT INTO agent_routes (source, target_model_id, request_type) VALUES (?, ?, ?)",
                (source, data["target_model_id"], request_type),
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
                    fields.append(f"{key} = ?")
                    values.append(data[key])
            if not fields:
                return
            target_model_id = data.get("target_model_id")
            if target_model_id is not None:
                active = conn.execute(
                    """SELECT 1 FROM target_models tm
                       JOIN upstreams u ON tm.upstream_id = u.id
                       WHERE tm.id = ? AND u.is_active = 1""",
                    (target_model_id,),
                ).fetchone()
                if not active:
                    raise ValueError("目标模型不存在或所属上游已禁用")
            fields.append("updated_at = datetime('now')")
            values.append(route_id)
            conn.execute(
                f"UPDATE agent_routes SET {', '.join(fields)} WHERE id = ?",
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
        """精确匹配一条 agent 路由，无 fallback。上游禁用返回 None。"""
        conn = self._connect()
        try:
            row = conn.execute(
                """SELECT tm.name as target_name, tm.multimodal, u.format,
                          u.id as upstream_id, u.base_url, u.api_key,
                          u.timeout, u.connect_timeout, u.ssl_verify, u.retry
                   FROM agent_routes ar
                   JOIN target_models tm ON ar.target_model_id = tm.id
                   JOIN upstreams u ON tm.upstream_id = u.id
                   WHERE ar.source = ? AND ar.request_type = ? AND u.is_active = 1""",
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
                f"""SELECT ar.source, ar.request_type, tm.name as target_name, tm.multimodal,
                          u.format, tm.upstream_id
                   FROM agent_routes ar
                   JOIN target_models tm ON ar.target_model_id = tm.id
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
```

- [ ] **Step 5: 新增 v6→v7 迁移**

在 `proxy/config_manager.py` 的 `Migrations` 类中：

1. 在 `status()` 方法的 `if version == 5:` 判断之后追加：

```python
            if version == 6:
                return {
                    "migrated": False,
                    "version": 6,
                    "details": "需要执行迁移: 新增 agent_routes 表",
                }
```

2. 在 `migrate()` 方法的 `if version <= 5:` 之后追加：

```python
            if version <= 6:
                self._migrate_v6_to_v7(backup_path)
```

3. 在 `_migrate_v5_to_v6()` 方法之后新增：

```python
    def _migrate_v6_to_v7(self, backup_path: Path):
        """执行 v6 → v7 迁移（新增 agent_routes 表）。"""
        conn = self._connect()
        try:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("BEGIN TRANSACTION")
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS agent_routes (
                        id              INTEGER PRIMARY KEY AUTOINCREMENT,
                        source          TEXT NOT NULL CHECK(length(source) > 0 AND source != '*'),
                        target_model_id INTEGER NOT NULL REFERENCES target_models(id) ON DELETE RESTRICT,
                        request_type    TEXT NOT NULL DEFAULT 'chat_completions'
                                        CHECK(request_type IN ('responses', 'messages', 'chat_completions')),
                        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                        updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
                        UNIQUE(source, request_type)
                    );
                """)
                logging.info("[Migrations] v6→v7 STEP 1: agent_routes 表创建完成")

                conn.execute("DELETE FROM schema_version;")
                conn.execute("INSERT INTO schema_version (version) VALUES (7);")
                logging.info("[Migrations] v6→v7 STEP 2: schema_version 更新为 7")

                conn.commit()
                logging.info("[Migrations] v6→v7 迁移成功")
            except Exception:
                conn.rollback()
                logging.error("[Migrations] v6→v7 迁移失败，已回滚", exc_info=True)
                raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.close()
```

4. 更新 `status()` 中 `version >= 7` 的返回值（找到现有的 `return {"migrated": True, "version": version, ...}` 行，确保 v7+ 返回 migrated=True）

- [ ] **Step 6: 运行测试确认通过**

Run: `python3 -m pytest test/test_config_manager.py::TestAgentRouteCRUD -v`
Expected: 13 passed

- [ ] **Step 7: 确认原有测试不受影响**

Run: `python3 -m pytest test/test_config_manager.py -q`
Expected: 全部通过，无 regression

- [ ] **Step 8: 提交**

```bash
git add proxy/config_manager.py test/test_config_manager.py
git commit -m "feat: ConfigDB agent_routes 表 + CRUD + v6→v7 迁移"
```

---

### Task 3: ConfigCache resolve_agent + _agent_routes 缓存

**Files:**
- Modify: `proxy/config_manager.py`（ConfigCache 类）

- [ ] **Step 1: 在 ConfigCache.__init__ 中新增 _agent_routes**

在 `ConfigCache.__init__` 的 `self._loaded_at: float = 0` 之后追加：

```python
        self._agent_routes: dict = {}
```

- [ ] **Step 2: 修改 _refresh_if_stale 加载 agent_routes**

在 `_refresh_if_stale()` 方法中，`self._routes = new_routes` 之后、`self._loaded_at = time.time()` 之前追加：

```python
                # 加载 agent_routes
                new_agent_routes = {}
                all_agent_routes = db.get_all_agent_routes(request_type=None)
                for source, cfg in all_agent_routes.items():
                    pt = cfg.get("request_type", "chat_completions")
                    agent_cfg = db.resolve_agent(source, pt)
                    if agent_cfg:
                        new_agent_routes[(source, pt)] = agent_cfg
                self._agent_routes = new_agent_routes
```

- [ ] **Step 3: 新增 resolve_agent 方法**

在 `ConfigCache` 类的 `resolve()` 方法之后新增：

```python
    def resolve_agent(self, source_name: str, request_type: str) -> Optional[dict]:
        """子 agent 专用路由查找 — 精确匹配，无 fallback。

        找到且上游活跃 → 返回与 resolve() 相同格式的 dict。
        未找到或上游禁用 → 返回 None（调用方回退主路由）。
        """
        with self._lock:
            self._refresh_if_stale()
            key = (source_name, request_type)
            return self._agent_routes.get(key)
```

- [ ] **Step 4: 新增 get_all_agent_routes 方法**

在 `resolve_agent()` 之后新增：

```python
    def get_all_agent_routes(self, request_type: Optional[str] = None) -> dict:
        with self._lock:
            self._refresh_if_stale()
            result = {}
            for (src, pt), cfg in self._agent_routes.items():
                if request_type is not None and pt != request_type:
                    continue
                result[src] = cfg
            return result
```

- [ ] **Step 5: 运行全量测试确认无 regression**

Run: `python3 -m pytest test/ -q`
Expected: 全部通过

- [ ] **Step 6: 提交**

```bash
git add proxy/config_manager.py
git commit -m "feat: ConfigCache resolve_agent + _agent_routes 缓存"
```

---

### Task 4: config_api.py agent-routes CRUD 端点

**Files:**
- Modify: `server/config_api.py`

- [ ] **Step 1: 在 handle_get 中新增 agent-routes 路径**

在 `handle_get()` 方法的 `if path == "/api/routes":` 块之后追加：

```python
    if path == "/api/agent-routes":
        request_type = qs.get("request_type", [None])[0]
        with config_db() as db:
            routes = db.list_agent_routes(request_type=request_type)
        json_response(handler, {"routes": routes})
        return True

    m = re.match(r"/api/agent-routes/(\d+)$", path)
    if m:
        with config_db() as db:
            route = db.get_agent_route(int(m.group(1)))
        if route:
            json_response(handler, route)
        else:
            json_response(handler, {"error": "Not found"}, 404)
        return True
```

注意：`/api/agent-routes/:id` 的正则匹配必须放在 `/api/agent-routes` 精确匹配之后。

- [ ] **Step 2: 在 handle_post 中新增 agent-routes 路径**

在 `handle_post()` 方法的 `if path == "/api/routes":` 块之后追加：

```python
    if path == "/api/agent-routes":
        data = _read_json(handler)
        if not data:
            return True
        request_type = data.get("request_type", "chat_completions")
        if request_type not in ("responses", "messages", "chat_completions"):
            json_response(
                handler,
                {"error": "request_type must be one of: responses, messages, chat_completions"},
                400,
            )
            return True
        with config_db() as db:
            model = db.get_model(data["target_model_id"])
            if not model:
                json_response(handler, {"error": "target_model_id 不存在"}, 400)
                return True
            if not model.get("upstream_active"):
                json_response(handler, {"error": "目标模型所属上游已禁用"}, 400)
                return True
            try:
                rid = db.add_agent_route(data)
            except (sqlite3.IntegrityError, ValueError) as e:
                json_response(handler, {"error": str(e)}, 409)
                return True
        _reload_proxies()
        json_response(handler, {"id": rid, "message": "Created"}, 201)
        return True
```

- [ ] **Step 3: 在 handle_put 中新增 agent-routes 路径**

在 `handle_put()` 方法的 `m = re.match(r"/api/routes/(\d+)$", path):` 块之后追加：

```python
    m = re.match(r"/api/agent-routes/(\d+)$", path)
    if m:
        data = _read_json(handler)
        if not data:
            return True
        try:
            with config_db() as db:
                db.update_agent_route(int(m.group(1)), data)
        except (sqlite3.IntegrityError, ValueError) as e:
            json_response(handler, {"error": str(e)}, 409)
            return True
        _reload_proxies()
        json_response(handler, {"message": "Updated"})
        return True
```

- [ ] **Step 4: 在 handle_delete 中新增 agent-routes 路径**

在 `handle_delete()` 方法的 `m = re.match(r"/api/routes/(\d+)$", path):` 块之后追加：

```python
    m = re.match(r"/api/agent-routes/(\d+)$", path)
    if m:
        rid = int(m.group(1))
        with config_db() as db:
            route = db.get_agent_route(rid)
            if not route:
                json_response(handler, {"error": "Not found"}, 404)
                return True
            try:
                db.delete_agent_route(rid)
            except sqlite3.IntegrityError as e:
                json_response(handler, {"error": str(e)}, 409)
                return True
        _reload_proxies()
        json_response(handler, {"message": "Deleted"})
        return True
```

- [ ] **Step 5: 运行全量测试确认无 regression**

Run: `python3 -m pytest test/ -q`
Expected: 全部通过

- [ ] **Step 6: 提交**

```bash
git add server/config_api.py
git commit -m "feat: config_api agent-routes CRUD 端点"
```

---

### Task 5: server/handler.py 分发表注册

**Files:** 无变更

无需修改 — `config_api.handle_get/post/put/delete` 已在分发表中，新 `/api/agent-routes` 路径由 config_api 内部正则匹配处理。

- [ ] **Step 1: 验证分发表已包含 config_api**

Run: `grep -n "config_api.handle" server/handler.py`
Expected: 4 行输出（handle_get, handle_post, handle_put, handle_delete）

确认新端点 `/api/agent-routes` 由 config_api 内部路由处理，无需修改 handler.py 分发表。

---

### Task 6: proxy/handler.py 集成 agent 检测 + 路由

**Files:**
- Modify: `proxy/handler.py`

- [ ] **Step 1: 在 handler.py 顶部导入 detect_subagent**

在 `from .token_stats import record_token_stats` 之后追加：

```python
from .agent_detector import detect_subagent
```

- [ ] **Step 2: 修改 do_POST 中的路由解析逻辑**

在 `handler.py` 的 `do_POST()` 方法中，找到这段代码（第 155-160 行）：

```python
        # 解析模型路由：先查 config_cache.resolve 获取完整信息（含 format）
        raw_cfg = config_cache.resolve(model_name, request_type)
        if raw_cfg is None:
            model_cfg = {"target": model_name, "multimodal": False}
            upstream_cfg = CONFIG.get("upstream", {})
            upstream_format = ""
```

替换 `raw_cfg = config_cache.resolve(model_name, request_type)` 和 `if raw_cfg is None:` 之间的部分为：

```python
        # Agent 检测：子 agent 优先查 agent_routes，未命中回退主路由
        is_agent = detect_subagent(body)
        raw_cfg = None
        if is_agent:
            raw_cfg = config_cache.resolve_agent(model_name, request_type)
        if raw_cfg is None:
            raw_cfg = config_cache.resolve(model_name, request_type)
        if raw_cfg is None:
```

注意：第二个 `if raw_cfg is None:` 是原有的分支，保持不变。

- [ ] **Step 3: 运行全量测试确认无 regression**

Run: `python3 -m pytest test/ -q`
Expected: 全部通过

- [ ] **Step 4: 提交**

```bash
git add proxy/handler.py
git commit -m "feat: handler 集成 agent 检测 + 路由查找"
```

---

### Task 7: request_logger.py is_agent 日志字段

**Files:**
- Modify: `proxy/request_logger.py`
- Modify: `proxy/handler.py`

- [ ] **Step 1: 修改 log_raw_request 方法签名，新增 is_agent 参数**

在 `proxy/request_logger.py` 的 `log_raw_request` 方法中：

将签名从：
```python
def log_raw_request(self, request_id: str, model: str, target: str, body: str | dict,
                    request_type: str = None, request_path: str = None):
```

改为：
```python
def log_raw_request(self, request_id: str, model: str, target: str, body: str | dict,
                    request_type: str = None, request_path: str = None, is_agent: bool = False):
```

在方法体内，将 `data = body if isinstance(body, str) else json.dumps(body)` 改为：

```python
                if isinstance(body, str):
                    data = body
                else:
                    log_body = dict(body) if isinstance(body, dict) else body
                    data = json.dumps({"_log_meta": {"is_agent": is_agent}, "body": log_body})
```

这样 `is_agent` 信息被隔离在 `_log_meta` 命名空间下，不污染原始请求体字段。`body` 保留完整的原始请求内容。

- [ ] **Step 2: 修改 handler.py 调用，传入 is_agent**

在 `handler.py` 的 `do_POST()` 中，找到 `logger.log_raw_request(` 调用，追加 `is_agent=is_agent` 参数：

```python
logger.log_raw_request(
    request_id, model_name, target, body,
    request_type=request_type, request_path=downstream_url,
    is_agent=is_agent,
)
```

注意：仅 `raw_request` 阶段记录 `is_agent`，其他阶段不需要。

- [ ] **Step 3: 运行测试确认无 regression**

Run: `python3 -m pytest test/ -q`
Expected: 全部通过

- [ ] **Step 4: 提交**

```bash
git add proxy/request_logger.py proxy/handler.py
git commit -m "feat: 日志记录 is_agent 标记"
```

---

### Task 8: proxy/__init__.py re-export

**Files:**
- Modify: `proxy/__init__.py`

- [ ] **Step 1: 追加 detect_subagent 的 re-export**

在 `from .token_stats import record_token_stats  # noqa: F401` 之后追加：

```python
from .agent_detector import detect_subagent  # noqa: F401
```

- [ ] **Step 2: 运行测试确认**

Run: `python3 -m pytest test/ -q`
Expected: 全部通过

- [ ] **Step 3: 提交**

```bash
git add proxy/__init__.py
git commit -m "feat: re-export detect_subagent"
```

---

### Task 9: routes.js 前端 Agent 路由表格 + 模态框

**Files:**
- Modify: `static/js/pages/routes.js`

- [ ] **Step 1: 在 loadRoutePage 中追加 Agent 路由卡片 HTML**

在 `loadRoutePage()` 函数的 `</div>` (主路由 table-card 闭合标签) 之后、模板字符串结束前，追加 Agent 路由卡片：

```javascript
    <div class="table-card agent-route-card">
      <div class="table-header">
        <div class="table-title">
          <span>🤖 Agent 路由</span>
          <span class="agent-badge" id="agent-route-count">覆盖层 · 0</span>
        </div>
        <div class="page-actions">
          <button class="btn btn-secondary btn-sm" onclick="showAgentRouteModal()">+ 新增 Agent 路由</button>
        </div>
      </div>
      <div class="table-scroll">
        <table id="agent-route-table">
          <thead><tr><th>源模型</th><th>覆盖目标</th><th>上游</th><th>请求格式</th><th>状态</th><th>操作</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>
```

- [ ] **Step 2: 修改 switchRequestType 联动刷新**

将 `switchRequestType()` 函数修改为同时刷新两个表格：

```javascript
function switchRequestType(rt) {
  currentRequestType = rt;
  document.querySelectorAll('.route-type-card').forEach(b => b.classList.toggle('active', b.dataset.pt === rt));
  loadRouteTable(rt);
  loadAgentRouteTable(rt);
}
```

- [ ] **Step 3: 新增 loadAgentRouteTable 函数**

```javascript
async function loadAgentRouteTable(requestType) {
  let url = '/api/agent-routes';
  if (requestType) url += '?request_type=' + encodeURIComponent(requestType);
  const tbody = document.querySelector('#agent-route-table tbody');
  const countEl = document.getElementById('agent-route-count');
  try {
    const data = await api(url);
    if (countEl) countEl.textContent = '覆盖层 · ' + (data.routes ? data.routes.length : 0);
    if (tbody) tbody.innerHTML = (data.routes || []).map(r => {
    const isDisabled = !r.upstream_active;
    const rowClass = isDisabled ? 'route-disabled' : '';
    return `<tr class="${rowClass}">
      <td><span class="badge badge-amber">${escHtml(r.source)}</span></td>
      <td><span class="badge badge-green">${escHtml(r.target_name)}</span>
          <span class="route-override-hint">← 覆盖主路由</span></td>
      <td><span class="badge" style="background:hsl(var(--muted) / 0.7);color:hsl(var(--muted-foreground))">${escHtml(r.upstream_name || r.upstream_id)}</span></td>
      <td><span class="badge ${FORMAT_COLORS[r.upstream_format] || ''}">${FORMAT_LABELS[r.upstream_format] || r.upstream_format || '-'}</span></td>
      <td><span class="route-status"><span class="route-status-dot ${r.upstream_active ? 'active' : 'inactive'}"></span>${r.upstream_active ? '活跃' : '已禁用'}</span></td>
      <td>
        <div class="route-actions">
          <button class="btn btn-secondary btn-sm" onclick="showAgentRouteModal(${r.id})">编辑</button>
          <button class="btn btn-danger btn-sm" onclick="confirmDeleteAgentRoute(${r.id}, '${escHtml(r.source)}')">删除</button>
        </div>
      </td>
    </tr>`;
  }).join('') || '<tr><td colspan="6" class="empty-state"><div class="empty-state-icon">🤖</div>暂无 Agent 路由配置<br><span style="font-size:11px">子 agent 请求将使用主路由表</span></td></tr>';
  } catch (e) {
    if (countEl) countEl.textContent = '覆盖层 · ?';
    if (tbody) tbody.innerHTML = '<tr><td colspan="6" class="empty-state">加载失败</td></tr>';
  }
}
```

- [ ] **Step 4: 新增 showAgentRouteModal 函数**

```javascript
async function showAgentRouteModal(editId) {
  let data = { source: '', target_model_id: '', request_type: currentRequestType };
  let title = '新增 Agent 路由';
  let routeUpstreamId = null;
  let routeModelId = null;
  let models, upstreams;
  try {
    if (editId) {
      title = '编辑 Agent 路由 #' + editId;
      const routes = await api('/api/agent-routes');
      const found = routes.routes.find(r => r.id === editId);
      if (found) data = found;
    }
    [models, upstreams] = await Promise.all([api('/api/models'), api('/api/upstreams')]);
    if (editId && data.target_model_id) {
      routeModelId = data.target_model_id;
      const tm = models.models.find(m => m.id === data.target_model_id);
      if (tm) routeUpstreamId = tm.upstream_id;
    }
  } catch (_) {
    alert('加载数据失败，请检查服务是否正常运行');
    return;
  }
  const cascadingHtml = buildCascadingSelect(upstreams, models, routeUpstreamId, routeModelId);
  showModal(title,
    `<div class="form-group"><label class="form-label">源模型名</label>
       <input type="text" class="form-input" id="r-source" value="${escHtml(data.source)}" placeholder="如 claude-sonnet-4-6">
       <div class="form-hint">子 agent 请求的模型名称，匹配时覆盖主路由指向</div>
     </div>
     <hr class="form-divider">
     ${cascadingHtml}
     <input type="hidden" id="r-proxy" value="${escHtml(data.request_type)}">`,
    `<button class="btn btn-secondary" onclick="closeModal()">取消</button><button class="btn btn-primary" onclick="saveAgentRoute(${editId || 0})">保存 Agent 路由</button>`);
  const modal = document.querySelector('.modal');
  if (modal) modal.classList.add('route-modal');
  bindCascadeModelSelect();
}
```

- [ ] **Step 5: 新增 saveAgentRoute 和 confirmDeleteAgentRoute 函数**

```javascript
async function saveAgentRoute(editId) {
  const data = {
    source: document.getElementById('r-source').value.trim(),
    target_model_id: parseInt(document.getElementById('r-target').value),
    request_type: document.getElementById('r-proxy').value,
  };
  if (!data.source) { alert('源模型名不能为空'); return; }
  if (data.source === '*') { alert('Agent 路由不支持 * fallback'); return; }
  if (!data.target_model_id) { alert('请选择目标模型'); return; }
  if (editId) {
    await api('/api/agent-routes/' + editId, { method: 'PUT', body: JSON.stringify(data) });
  } else {
    await api('/api/agent-routes', { method: 'POST', body: JSON.stringify(data) });
  }
  closeModal();
  bus.emit('config:route-changed', {});
  loadAgentRouteTable(currentRequestType);
}

async function confirmDeleteAgentRoute(id, source) {
  if (!confirm('确认删除 Agent 路由 "' + source + '"？')) return;
  const result = await api('/api/agent-routes/' + id, { method: 'DELETE' });
  if (result.error) { alert(result.error); }
  else { bus.emit('config:route-changed', {}); loadAgentRouteTable(currentRequestType); }
}
```

- [ ] **Step 6: 更新 exports 和 global scope mounting**

在文件末尾的 export 行和 global scope mounting 行追加新函数：

```javascript
// exports
export { loadRoutePage, initRoutePage, loadRouteTable, showRouteModal, showFallbackModal, saveRoute, confirmDeleteRoute, switchRequestType, loadAgentRouteTable, showAgentRouteModal, saveAgentRoute, confirmDeleteAgentRoute };

// global scope
window.showAgentRouteModal = showAgentRouteModal;
window.saveAgentRoute = saveAgentRoute;
window.confirmDeleteAgentRoute = confirmDeleteAgentRoute;
```

- [ ] **Step 7: 修改 loadRoutePage 初始加载**

将 `loadRoutePage()` 末尾的 `loadRouteTable('chat_completions');` 改为：

```javascript
  loadRouteTable('chat_completions');
  loadAgentRouteTable('chat_completions');
```

- [ ] **Step 8: 重启服务验证前端**

Run: `./server.sh restart`

在浏览器访问 `http://localhost:18742`，切换到路由映射页，确认：
1. 主路由表正常显示
2. Agent 路由卡片在下方出现，琥珀色边框
3. 三卡片切换时两个表格联动刷新
4. "新增 Agent 路由"按钮弹出模态框
5. 输入 `*` 作为 source 时提示不支持

- [ ] **Step 9: 提交**

```bash
git add static/js/pages/routes.js
git commit -m "feat: 前端 Agent 路由表格 + 模态框 + 联动"
```

---

### Task 10: routes.css 前端样式

**Files:**
- Modify: `static/css/routes.css`

- [ ] **Step 1: 追加 Agent 路由样式**

在 `routes.css` 末尾追加：

```css
/* ===== Agent 路由卡片 ===== */
.agent-route-card {
  border-color: hsl(45 100% 50% / 0.3) !important;
}

.agent-route-card .table-header {
  background: hsl(45 100% 50% / 0.05) !important;
  border-bottom: 1px solid hsl(45 100% 50% / 0.15) !important;
}

.agent-badge {
  font-size: 10px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 10px;
  background: hsl(45 100% 50% / 0.15);
  color: hsl(45 100% 50%);
  margin-left: 8px;
}

.badge-amber {
  background: hsl(45 100% 50% / 0.15) !important;
  color: hsl(45 100% 50%) !important;
}

.route-override-hint {
  font-size: 10px;
  color: hsl(var(--muted-foreground));
  margin-left: 4px;
}
```

- [ ] **Step 2: 重启服务验证样式**

Run: `./server.sh restart`

在浏览器确认 Agent 路由卡片有琥珀色边框和标签样式。

- [ ] **Step 3: 提交**

```bash
git add static/css/routes.css
git commit -m "feat: Agent 路由卡片样式（琥珀色边框 + 覆盖层标签）"
```

---

### Task 11: handler 集成测试

**Files:**
- Modify: `test/test_handler.py`

- [ ] **Step 1: 在 test_handler.py 中追加 agent 路由集成测试**

追加一个测试类（在现有测试类之后）：

```python
class TestAgentRouting(unittest.TestCase):
    def setUp(self):
        from proxy.config_manager import ConfigDB, ConfigCache
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db = ConfigDB(self.db_path)
        self.cache = ConfigCache(self.db_path)
        # 创建上游 + 模型
        self.upstream_id = self.db.add_upstream({
            "name": "main-upstream", "base_url": "http://main:8000",
            "api_key": "sk-main", "format": "chat_completions"
        })
        self.main_model_id = self.db.add_model({
            "name": "main-target", "upstream_id": self.upstream_id
        })
        self.db.add_route({
            "source": "claude-sonnet-4-6", "target_model_id": self.main_model_id,
            "request_type": "chat_completions"
        })

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_agent_route_overrides_main(self):
        # 创建 agent 专用上游 + 模型
        agent_up_id = self.db.add_upstream({
            "name": "agent-upstream", "base_url": "http://agent:8001",
            "api_key": "sk-agent", "format": "chat_completions"
        })
        agent_model_id = self.db.add_model({
            "name": "agent-target", "upstream_id": agent_up_id
        })
        self.db.add_agent_route({
            "source": "claude-sonnet-4-6", "target_model_id": agent_model_id,
            "request_type": "chat_completions"
        })
        self.cache.reload()
        # agent 路由命中
        result = self.cache.resolve_agent("claude-sonnet-4-6", "chat_completions")
        self.assertIsNotNone(result)
        self.assertEqual(result["target_name"], "agent-target")
        # 主路由不受影响
        main_result = self.cache.resolve("claude-sonnet-4-6", "chat_completions")
        self.assertIsNotNone(main_result)
        self.assertEqual(main_result["target_name"], "main-target")

    def test_agent_route_fallback_to_main_when_not_found(self):
        self.cache.reload()
        # agent 路由无匹配 → 应返回 None，调用方回退主路由
        result = self.cache.resolve_agent("nonexistent-model", "chat_completions")
        self.assertIsNone(result)

    def test_agent_route_inactive_upstream_returns_none(self):
        agent_up_id = self.db.add_upstream({
            "name": "dead-upstream", "base_url": "http://dead:8002",
            "api_key": "sk-dead", "format": "chat_completions"
        })
        agent_model_id = self.db.add_model({
            "name": "dead-target", "upstream_id": agent_up_id
        })
        self.db.add_agent_route({
            "source": "claude-sonnet-4-6", "target_model_id": agent_model_id,
            "request_type": "chat_completions"
        })
        self.db.update_upstream(agent_up_id, {"is_active": 0})
        self.cache.reload()
        result = self.cache.resolve_agent("claude-sonnet-4-6", "chat_completions")
        self.assertIsNone(result)

    def test_detect_subagent_with_marker(self):
        from proxy.agent_detector import detect_subagent
        body = {"messages": [{"role": "user", "content": "__SUBAGENT_MARKER__ test"}]}
        self.assertTrue(detect_subagent(body))

    def test_detect_subagent_normal_request(self):
        from proxy.agent_detector import detect_subagent
        body = {"messages": [{"role": "user", "content": "normal request"}]}
        self.assertFalse(detect_subagent(body))

    def test_handler_agent_detection_with_route_override(self):
        """验证完整的 agent 检测 → 路由覆盖流程（不启动 HTTP 服务，仅测试逻辑）。"""
        from proxy.agent_detector import detect_subagent
        # 场景：子 agent 请求有 agent 路由 → resolve_agent 命中
        agent_up_id = self.db.add_upstream({
            "name": "agent-only", "base_url": "http://agent-only:8003",
            "api_key": "sk-agent-only", "format": "chat_completions"
        })
        agent_model_id = self.db.add_model({
            "name": "cheap-model", "upstream_id": agent_up_id
        })
        self.db.add_agent_route({
            "source": "claude-sonnet-4-6", "target_model_id": agent_model_id,
            "request_type": "chat_completions"
        })
        self.cache.reload()

        # 模拟 handler 逻辑：检测 → 选择路由
        body = {"messages": [{"role": "user", "content": '{"__SUBAGENT_MARKER__": {}}'}]}
        is_agent = detect_subagent(body)
        self.assertTrue(is_agent)

        raw_cfg = None
        if is_agent:
            raw_cfg = self.cache.resolve_agent("claude-sonnet-4-6", "chat_completions")
        if raw_cfg is None:
            raw_cfg = self.cache.resolve("claude-sonnet-4-6", "chat_completions")
        self.assertIsNotNone(raw_cfg)
        self.assertEqual(raw_cfg["target_name"], "cheap-model")

    def test_handler_normal_request_ignores_agent_route(self):
        """验证普通请求不查 agent_routes，直接走主路由。"""
        from proxy.agent_detector import detect_subagent
        agent_up_id = self.db.add_upstream({
            "name": "agent-only", "base_url": "http://agent-only:8003",
            "api_key": "sk-agent-only", "format": "chat_completions"
        })
        agent_model_id = self.db.add_model({
            "name": "cheap-model", "upstream_id": agent_up_id
        })
        self.db.add_agent_route({
            "source": "claude-sonnet-4-6", "target_model_id": agent_model_id,
            "request_type": "chat_completions"
        })
        self.cache.reload()

        # 普通请求
        body = {"messages": [{"role": "user", "content": "hello"}]}
        is_agent = detect_subagent(body)
        self.assertFalse(is_agent)

        raw_cfg = None
        if is_agent:
            raw_cfg = self.cache.resolve_agent("claude-sonnet-4-6", "chat_completions")
        if raw_cfg is None:
            raw_cfg = self.cache.resolve("claude-sonnet-4-6", "chat_completions")
        self.assertIsNotNone(raw_cfg)
        self.assertEqual(raw_cfg["target_name"], "main-target")

    def test_handler_agent_fallback_to_main(self):
        """验证子 agent 请求无 agent 路由时回退主路由。"""
        from proxy.agent_detector import detect_subagent
        self.cache.reload()

        body = {"messages": [{"role": "user", "content": '{"__SUBAGENT_MARKER__": {}}'}]}
        is_agent = detect_subagent(body)
        self.assertTrue(is_agent)

        # 无 agent 路由 → resolve_agent 返回 None → 回退主路由
        raw_cfg = None
        if is_agent:
            raw_cfg = self.cache.resolve_agent("claude-sonnet-4-6", "chat_completions")
        if raw_cfg is None:
            raw_cfg = self.cache.resolve("claude-sonnet-4-6", "chat_completions")
        self.assertIsNotNone(raw_cfg)
        self.assertEqual(raw_cfg["target_name"], "main-target")
```

- [ ] **Step 2: 运行集成测试**

Run: `python3 -m pytest test/test_handler.py::TestAgentRouting -v`
Expected: 5 passed

- [ ] **Step 3: 运行全量测试**

Run: `python3 -m pytest test/ -q`
Expected: 全部通过

- [ ] **Step 4: 提交**

```bash
git add test/test_handler.py
git commit -m "test: agent 路由集成测试（覆盖/回退/上游禁用）"
```

---

### Task 12: 端到端冒烟验证

**Files:** 无变更

- [ ] **Step 1: 重启服务**

Run: `./server.sh restart`

- [ ] **Step 2: 验证 Web UI**

在浏览器 `http://localhost:18742` → 路由映射页：
1. 主路由表正常显示
2. Agent 路由表格在下方，琥珀色边框
3. 三卡片切换联动
4. 新增 Agent 路由：source=某个模型名，选上游+目标模型 → 保存成功
5. Agent 路由表显示新记录
6. 编辑 Agent 路由 → 修改目标模型 → 保存成功
7. 删除 Agent 路由 → 确认后删除成功
8. 尝试输入 `*` 作为 source → 提示不支持

- [ ] **Step 3: 验证迁移幂等性**

对生产数据库执行迁移两次，确认无副作用：

Run:
```bash
python3 -c "from proxy.paths import DATA_DB; from proxy.config_manager import Migrations; print(Migrations(DATA_DB).migrate())"
python3 -c "from proxy.paths import DATA_DB; from proxy.config_manager import Migrations; print(Migrations(DATA_DB).migrate())"
```

Expected: 第二次返回 `{"status": "already_migrated", ...}`

- [ ] **Step 4: 验证代理请求行为**

先配置一条 agent 路由（通过 UI 或 API），然后用 curl 测试：

1. 普通请求（无 SUBAGENT_MARKER）→ 应走主路由：

```bash
curl -s -X POST http://localhost:48743/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "你的测试模型", "messages": [{"role": "user", "content": "hello"}]}' | python3 -m json.tool
```

2. 子 agent 请求（含 SUBAGENT_MARKER）→ 应走 agent 路由：

```bash
curl -s -X POST http://localhost:48743/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "你的测试模型", "messages": [{"role": "user", "content": "__SUBAGENT_MARKER__ test"}]}' | python3 -m json.tool
```

检查 proxy.log 确认两条请求分别走了不同的目标模型。

- [ ] **Step 5: 最终全量测试**

Run: `python3 -m pytest test/ -q`
Expected: 全部通过，0 failures
