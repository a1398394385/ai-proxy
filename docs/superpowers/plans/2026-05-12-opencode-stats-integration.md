# OpenCode Token 统计接入 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `_OpenCodeDao` 作为第三数据源，接入 opencode.db 的 message 级别 token 统计到 StatsService。

**Architecture:** 在 `_SessionDao` 之后插入 `_OpenCodeDao`（阅读 `~/.local/share/opencode/opencode.db`），`_Merger` 改为可变参数，`StatsService` 所有 fetch 方法纳入 opencode 数据。

**Tech Stack:** Python 3 stdlib（sqlite3, pathlib, time），unittest + pytest

**Spec:** `docs/superpowers/specs/2026-05-12-opencode-stats-integration-design.md`

---

### 文件结构

| 文件 | 操作 | 说明 |
|------|------|------|
| `stats_service.py` | 修改 | 插入 `_OpenCodeDao`（~120 行）、`_Merger` N 源改造（~20 行改动）、`StatsService` 集成（~40 行改动） |
| `test/test_stats_service.py` | 修改 | 新增 `TestOpenCodeDao` 类（~150 行）、`_Merger` 三源测试（~30 行） |
| `server/__init__.py` | 不改 | 默认路径自动解析，无需传参 |

### 依赖顺序

```
Task 1: _OpenCodeDao 实现  ──┐
                              ├── Task 3: _Merger 改造 ── Task 5: StatsService 集成 ── Task 6: 全量验证
Task 2: _OpenCodeDao 测试 ────┘
                              Task 4: _Merger 三源测试 ──┘
```

---

### Task 1: `_OpenCodeDao` 实现

**Files:**
- Modify: `stats_service.py`（在 `_SessionDao` 类结束后、`_Merger` 类开始前插入）

- [ ] **Step 1: 插入 `_OpenCodeDao` 类**

在 `stats_service.py` 第 864 行（`class _Merger:` 之前）插入以下代码：

```python
class _OpenCodeDao:
    """OpenCode 数据访问对象 — 从 opencode.db 读取 session/message token 数据。

    按 message 级别聚合（每条 assistant message 的 tokens 计入其 modelID），
    reasoning tokens 合并入 output_tokens。数据库不存在时返回空结果，不抛异常。

    Args:
        db_path: opencode.db 路径
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    # ─── 工具方法 ───

    @staticmethod
    def _period_to_condition(period: str) -> str:
        """将 period 转换为 Unix 毫秒时间戳条件。"""
        mapping = {
            "day": "86400000",
            "24h": "86400000",
            "week": "604800000",
            "7d": "604800000",
            "month": "2592000000",
            "30d": "2592000000",
        }
        delta_ms = mapping.get(period, "604800000")
        return f"m.time_created >= (strftime('%s', 'now') * 1000 - {delta_ms})"

    @staticmethod
    def _parse_model(raw: str | None) -> str:
        """从 session.model JSON 中提取 model ID。"""
        if not raw:
            return ""
        try:
            import json
            return json.loads(raw).get("id", "")
        except (json.JSONDecodeError, TypeError):
            return raw

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> dict:
        """将 message 行包装为统一格式记录。"""
        return {
            "request_id": f"oc-msg-{row['message_id']}",
            "request_type": "session",
            "model": row["model_id"] or "",
            "target_model": row["model_id"] or "",
            "request_ts": row["request_ts"],
            "duration_ms": row["duration_ms"],
            "input_tokens": row["input_tokens"] or 0,
            "output_tokens": row["output_tokens"] or 0,
            "cached_read_tokens": row["cache_read_tokens"] or 0,
            "cached_write_tokens": row["cache_write_tokens"] or 0,
            "status": "completed",
            "_source": "opencode",
        }

    def _get_conn(self) -> sqlite3.Connection | None:
        """创建数据库连接，opencode.db 不存在时返回 None。"""
        if not self.db_path.exists():
            return None
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # ─── 查询方法 ───

    def aggregate_by_model(self, period: str) -> list:
        """按 modelID 分组聚合 token 统计数据。"""
        conn = self._get_conn()
        if conn is None:
            return []
        try:
            time_condition = self._period_to_condition(period)
            rows = conn.execute(
                f"""
                SELECT
                    json_extract(m.data, '$.modelID') as model,
                    COUNT(*) as request_count,
                    SUM(CAST(json_extract(m.data, '$.tokens.input') AS INTEGER)) as total_input,
                    SUM(CAST(json_extract(m.data, '$.tokens.output') AS INTEGER)
                        + CAST(json_extract(m.data, '$.tokens.reasoning') AS INTEGER)) as total_output,
                    SUM(CAST(json_extract(m.data, '$.tokens.cache.read') AS INTEGER)) as total_cache_read,
                    SUM(CAST(json_extract(m.data, '$.tokens.cache.write') AS INTEGER)) as total_cache_write
                FROM message m
                JOIN session s ON s.id = m.session_id
                WHERE {time_condition}
                  AND json_extract(m.data, '$.tokens.input') IS NOT NULL
                GROUP BY json_extract(m.data, '$.modelID')
                ORDER BY total_output DESC
                """,
            ).fetchall()

            return [
                {
                    "model": row["model"] or "",
                    "request_count": row["request_count"],
                    "input_tokens": row["total_input"] or 0,
                    "output_tokens": row["total_output"] or 0,
                    "cached_read_tokens": row["total_cache_read"] or 0,
                    "cached_write_tokens": row["total_cache_write"] or 0,
                    "total_tokens": (row["total_input"] or 0)
                    + (row["total_output"] or 0)
                    + (row["total_cache_read"] or 0)
                    + (row["total_cache_write"] or 0),
                }
                for row in rows
            ]
        finally:
            conn.close()

    def aggregate_summary(self, period: str) -> dict:
        """汇总 opencode 数据，返回与 _TokenStatsDao.aggregate_summary 相同结构的 dict。"""
        conn = self._get_conn()
        if conn is None:
            return {
                "period": period, "request_count": 0, "input_tokens": 0,
                "output_tokens": 0, "cached_read_tokens": 0,
                "cached_write_tokens": 0, "total_tokens": 0, "avg_duration_ms": 0,
            }
        try:
            time_condition = self._period_to_condition(period)
            row = conn.execute(
                f"""
                SELECT COUNT(*) as request_count,
                       COALESCE(SUM(CAST(json_extract(m.data, '$.tokens.input') AS INTEGER)), 0) as total_input,
                       COALESCE(SUM(CAST(json_extract(m.data, '$.tokens.output') AS INTEGER)
                           + CAST(json_extract(m.data, '$.tokens.reasoning') AS INTEGER)), 0) as total_output,
                       COALESCE(SUM(CAST(json_extract(m.data, '$.tokens.cache.read') AS INTEGER)), 0) as total_cache_read,
                       COALESCE(SUM(CAST(json_extract(m.data, '$.tokens.cache.write') AS INTEGER)), 0) as total_cache_write,
                       AVG(json_extract(m.data, '$.time.completed')
                           - json_extract(m.data, '$.time.created')) as avg_duration
                FROM message m
                JOIN session s ON s.id = m.session_id
                WHERE {time_condition}
                  AND json_extract(m.data, '$.tokens.input') IS NOT NULL
                """,
            ).fetchone()
            total_input = row["total_input"]
            total_output = row["total_output"]
            total_cache_read = row["total_cache_read"]
            total_cache_write = row["total_cache_write"]
            return {
                "period": period,
                "request_count": row["request_count"] or 0,
                "input_tokens": total_input,
                "output_tokens": total_output,
                "cached_read_tokens": total_cache_read,
                "cached_write_tokens": total_cache_write,
                "total_tokens": total_input + total_output + total_cache_read + total_cache_write,
                "avg_duration_ms": round(row["avg_duration"], 2) if row["avg_duration"] else 0,
            }
        finally:
            conn.close()

    def aggregate_trend(self, period: str) -> list:
        """按时间粒度聚合 opencode 数据，返回 time key。"""
        conn = self._get_conn()
        if conn is None:
            return []
        try:
            time_condition = self._period_to_condition(period)
            if period in ("day", "24h"):
                group_expr = "strftime('%Y-%m-%d %H:00', datetime(m.time_created / 1000, 'unixepoch'))"
            else:
                group_expr = "date(datetime(m.time_created / 1000, 'unixepoch'))"

            rows = conn.execute(
                f"""
                SELECT {group_expr} as time_bucket,
                       COUNT(*) as request_count,
                       SUM(CAST(json_extract(m.data, '$.tokens.input') AS INTEGER)) as total_input,
                       SUM(CAST(json_extract(m.data, '$.tokens.output') AS INTEGER)
                           + CAST(json_extract(m.data, '$.tokens.reasoning') AS INTEGER)) as total_output,
                       SUM(CAST(json_extract(m.data, '$.tokens.cache.read') AS INTEGER)) as total_cache_read,
                       SUM(CAST(json_extract(m.data, '$.tokens.cache.write') AS INTEGER)) as total_cache_write
                FROM message m
                JOIN session s ON s.id = m.session_id
                WHERE {time_condition}
                  AND json_extract(m.data, '$.tokens.input') IS NOT NULL
                GROUP BY time_bucket
                ORDER BY time_bucket ASC
                """,
            ).fetchall()

            return [
                {
                    "time": row["time_bucket"],
                    "request_count": row["request_count"],
                    "input_tokens": row["total_input"] or 0,
                    "output_tokens": row["total_output"] or 0,
                    "cached_read_tokens": row["total_cache_read"] or 0,
                    "cached_write_tokens": row["total_cache_write"] or 0,
                    "total_tokens": (row["total_input"] or 0)
                    + (row["total_output"] or 0)
                    + (row["total_cache_read"] or 0)
                    + (row["total_cache_write"] or 0),
                }
                for row in rows
            ]
        finally:
            conn.close()

    def query_messages_paged(
        self,
        period: str,
        model: str | None = None,
        request_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple:
        """分页查询 opencode messages（请求日志）。

        Args:
            period: 时间周期
            model: 可选，按 modelID 过滤
            request_type: 可选，仅 "session" 时返回数据，其余返回空
            limit: 每页数量
            offset: 偏移量

        Returns:
            (records_list, total_count) 元组
        """
        if request_type and request_type != "session":
            return ([], 0)

        conn = self._get_conn()
        if conn is None:
            return ([], 0)
        try:
            time_condition = self._period_to_condition(period)
            conditions = [time_condition, "json_extract(m.data, '$.tokens.input') IS NOT NULL"]
            params_count: list = []
            params_data: list = []

            if model:
                conditions.append("json_extract(m.data, '$.modelID') = ?")
                params_count.append(model)
                params_data.append(model)

            where_clause = " AND ".join(conditions)

            total_row = conn.execute(
                f"SELECT COUNT(*) as cnt FROM message m "
                f"JOIN session s ON s.id = m.session_id "
                f"WHERE {where_clause}",
                params_count,
            ).fetchone()
            total_count = total_row["cnt"]

            rows = conn.execute(
                f"""
                SELECT m.id as message_id,
                       json_extract(m.data, '$.modelID') as model_id,
                       datetime(m.time_created / 1000, 'unixepoch') as request_ts,
                       CAST(json_extract(m.data, '$.tokens.input') AS INTEGER) as input_tokens,
                       CAST(json_extract(m.data, '$.tokens.output') AS INTEGER)
                           + CAST(json_extract(m.data, '$.tokens.reasoning') AS INTEGER) as output_tokens,
                       CAST(json_extract(m.data, '$.tokens.cache.read') AS INTEGER) as cache_read_tokens,
                       CAST(json_extract(m.data, '$.tokens.cache.write') AS INTEGER) as cache_write_tokens,
                       CAST(json_extract(m.data, '$.time.completed') AS INTEGER)
                           - CAST(json_extract(m.data, '$.time.created') AS INTEGER) as duration_ms
                FROM message m
                JOIN session s ON s.id = m.session_id
                WHERE {where_clause}
                ORDER BY m.time_created DESC
                LIMIT ? OFFSET ?
                """,
                params_data + [limit, offset],
            ).fetchall()

            return ([self._row_to_record(r) for r in rows], total_count)
        except Exception:
            return ([], 0)
        finally:
            conn.close()
```

- [ ] **Step 2: 验证插入位置正确（语法检查）**

```bash
python3 -c "import stats_service; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add stats_service.py
git commit -m "feat: 新增 _OpenCodeDao — 从 opencode.db 读取 message 级别 token 统计"
```

---

### Task 2: `TestOpenCodeDao` 测试

**Files:**
- Modify: `test/test_stats_service.py`（在文件末尾追加新测试类）

- [ ] **Step 1: 追加 `TestOpenCodeDao` 测试类**

在 `test/test_stats_service.py` 末尾追加：

```python
class TestOpenCodeDao(unittest.TestCase):
    """_OpenCodeDao 测试 — 用临时 SQLite 文件模拟 opencode.db 结构。"""

    def setUp(self):
        """创建临时目录和模拟 opencode.db。"""
        import json
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "opencode.db"

        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            CREATE TABLE session (
                id TEXT PRIMARY KEY,
                model TEXT,
                time_created INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE message (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                time_created INTEGER NOT NULL,
                data TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES session(id)
            )
        """)

        # 插入测试数据：2 个 session，3 条 message
        now_ms = int(time.time() * 1000)
        # session 1: model = mimo-v2.5-pro, 2 messages
        conn.execute(
            "INSERT INTO session (id, model, time_created) VALUES (?, ?, ?)",
            ("ses-001", '{"id":"mimo-v2.5-pro","providerID":"XiaoMi"}', now_ms - 3600000),
        )
        msg1_data = json.dumps({
            "role": "assistant",
            "modelID": "mimo-v2.5-pro",
            "tokens": {"input": 100, "output": 50, "reasoning": 10, "cache": {"read": 20, "write": 0}},
            "time": {"created": now_ms - 3600000, "completed": now_ms - 3500000},
        })
        conn.execute(
            "INSERT INTO message (id, session_id, time_created, data) VALUES (?, ?, ?, ?)",
            ("msg-001", "ses-001", now_ms - 3600000, msg1_data),
        )
        msg2_data = json.dumps({
            "role": "assistant",
            "modelID": "mimo-v2.5-pro",
            "tokens": {"input": 200, "output": 80, "reasoning": 0, "cache": {"read": 0, "write": 10}},
            "time": {"created": now_ms - 1800000, "completed": now_ms - 1700000},
        })
        conn.execute(
            "INSERT INTO message (id, session_id, time_created, data) VALUES (?, ?, ?, ?)",
            ("msg-002", "ses-001", now_ms - 1800000, msg2_data),
        )
        # session 2: model = glm-4.7, 1 message (no tokens → should be excluded)
        conn.execute(
            "INSERT INTO session (id, model, time_created) VALUES (?, ?, ?)",
            ("ses-002", '{"id":"glm-4.7","providerID":"ZhiPu"}', now_ms - 7200000),
        )
        msg3_data = json.dumps({
            "role": "user",
            "modelID": "glm-4.7",
            "tokens": {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
            "time": {"created": now_ms - 7200000},
        })
        conn.execute(
            "INSERT INTO message (id, session_id, time_created, data) VALUES (?, ?, ?, ?)",
            ("msg-003", "ses-002", now_ms - 7200000, msg3_data),
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        """清理临时文件。"""
        import shutil
        if os.path.exists(self.tmpdir):
            shutil.rmtree(self.tmpdir)

    def _create_dao(self):
        """创建 _OpenCodeDao 实例。"""
        from stats_service import _OpenCodeDao
        return _OpenCodeDao(self.db_path)

    # ─── aggregate_by_model 测试 ───

    def test_aggregate_by_model_week(self):
        """按模型聚合：week 周期返回 mimo-v2.5-pro 的 2 条记录（input=300, output=60+10）。"""
        dao = self._create_dao()
        result = dao.aggregate_by_model("week")
        self.assertEqual(len(result), 1)  # glm-4.7 无 tokens 被过滤
        self.assertEqual(result[0]["model"], "mimo-v2.5-pro")
        self.assertEqual(result[0]["request_count"], 2)
        self.assertEqual(result[0]["input_tokens"], 300)   # 100 + 200
        self.assertEqual(result[0]["output_tokens"], 140)   # (50+10) + (80+0)
        self.assertEqual(result[0]["cached_read_tokens"], 20)  # 20 + 0
        self.assertEqual(result[0]["cached_write_tokens"], 10) # 0 + 10

    def test_aggregate_by_model_day_no_data(self):
        """day 周期无数据 → 返回空列表。"""
        dao = self._create_dao()
        result = dao.aggregate_by_model("day")
        self.assertEqual(result, [])

    # ─── aggregate_summary 测试 ───

    def test_aggregate_summary_week(self):
        """汇总统计：request_count=2, tokens 求和正确。"""
        dao = self._create_dao()
        result = dao.aggregate_summary("week")
        self.assertEqual(result["request_count"], 2)
        self.assertEqual(result["input_tokens"], 300)
        self.assertEqual(result["output_tokens"], 140)
        self.assertEqual(result["total_tokens"], 470)  # 300 + 140 + 20 + 10
        self.assertGreater(result["avg_duration_ms"], 0)  # duration 有值

    def test_aggregate_summary_day_empty(self):
        """day 周期无数据 → 返回零值 dict。"""
        dao = self._create_dao()
        result = dao.aggregate_summary("day")
        self.assertEqual(result["request_count"], 0)
        self.assertEqual(result["input_tokens"], 0)

    # ─── aggregate_trend 测试 ───

    def test_aggregate_trend_week(self):
        """趋势数据：返回 time key，按天分组。"""
        dao = self._create_dao()
        result = dao.aggregate_trend("week")
        self.assertGreater(len(result), 0)
        for point in result:
            self.assertIn("time", point)
            self.assertIn("input_tokens", point)
            self.assertIn("output_tokens", point)

    def test_aggregate_trend_day_period(self):
        """day 周期按小时分组，返回 time key。"""
        dao = self._create_dao()
        result = dao.aggregate_trend("day")
        self.assertEqual(result, [])

    # ─── query_messages_paged 测试 ───

    def test_query_messages_paged_basic(self):
        """分页查询：返回 2 条有效 message。"""
        dao = self._create_dao()
        records, total = dao.query_messages_paged("week")
        self.assertEqual(total, 2)
        self.assertEqual(len(records), 2)

    def test_query_messages_paged_model_filter(self):
        """按模型过滤：mimo-v2.5-pro → 2 条，glm-4.7 → 0 条。"""
        dao = self._create_dao()
        _, total = dao.query_messages_paged("week", model="mimo-v2.5-pro")
        self.assertEqual(total, 2)
        _, total_none = dao.query_messages_paged("week", model="glm-4.7")
        self.assertEqual(total_none, 0)

    def test_query_messages_paged_request_type_filter(self):
        """request_type 过滤：session → 2 条，proxy → 0 条。"""
        dao = self._create_dao()
        _, total_session = dao.query_messages_paged("week", request_type="session")
        self.assertEqual(total_session, 2)
        _, total_proxy = dao.query_messages_paged("week", request_type="proxy")
        self.assertEqual(total_proxy, 0)

    def test_query_messages_paged_record_format(self):
        """记录格式：request_id 以 oc-msg- 开头，request_type 为 session，_source 为 opencode。"""
        dao = self._create_dao()
        records, _ = dao.query_messages_paged("week")
        self.assertEqual(len(records), 2)
        for r in records:
            self.assertTrue(r["request_id"].startswith("oc-msg-"))
            self.assertEqual(r["request_type"], "session")
            self.assertEqual(r["_source"], "opencode")
            self.assertEqual(r["status"], "completed")

    def test_query_messages_paged_pagination(self):
        """分页：limit=1, offset=0 → 1 条，total=2。"""
        dao = self._create_dao()
        records, total = dao.query_messages_paged("week", limit=1, offset=0)
        self.assertEqual(total, 2)
        self.assertEqual(len(records), 1)

    # ─── db 不存在测试 ───

    def test_db_not_exists(self):
        """opencode.db 不存在 → 各方法返回空结果，不抛异常。"""
        from stats_service import _OpenCodeDao
        dao = _OpenCodeDao(Path("/nonexistent/opencode.db"))

        self.assertEqual(dao.aggregate_by_model("week"), [])
        summary = dao.aggregate_summary("week")
        self.assertEqual(summary["request_count"], 0)
        self.assertEqual(dao.aggregate_trend("week"), [])
        self.assertEqual(dao.query_messages_paged("week"), ([], 0))
```

- [ ] **Step 2: 单独运行新测试，确认失败（DAO 未导入或类不存在）**

```bash
python3 -m pytest test/test_stats_service.py::TestOpenCodeDao -q
```

- [ ] **Step 3: 确认全量测试通过**

```bash
python3 -m pytest test/ -q
```

预期：515 现有 tests 全通过 + ~12 个新测试通过。

- [ ] **Step 4: Commit**

```bash
git add test/test_stats_service.py
git commit -m "test: 新增 TestOpenCodeDao — opencode.db 数据聚合测试"
```

---

### Task 3: `_Merger` N 源改造

**Files:**
- Modify: `stats_service.py:865-977`（`_Merger` 类的三个 merge 方法）

- [ ] **Step 1: 改造 `merge_summary` 为可变参数**

将第 882-900 行的 `merge_summary` 方法替换为：

```python
    @staticmethod
    def merge_summary(*summaries: dict) -> dict:
        """合并 N 个汇总 dict，各数值字段求和，字段重命名为 cache*。
        avg_duration_ms 优先级：proxy > opencode > session（取第一个非零值）。"""
        result = {}
        avg_duration = 0
        for s in summaries:
            s = _Merger._rename(s)
            if not result:
                result = {
                    "period": s.get("period", "week"),
                    "request_count": s.get("request_count", 0),
                    "input_tokens": s.get("input_tokens", 0),
                    "output_tokens": s.get("output_tokens", 0),
                    "cache_read_tokens": s.get("cache_read_tokens", 0),
                    "cache_write_tokens": s.get("cache_write_tokens", 0),
                    "total_tokens": s.get("total_tokens", 0),
                    "avg_duration_ms": s.get("avg_duration_ms", 0),
                }
                avg_duration = s.get("avg_duration_ms", 0)
            else:
                result["request_count"] += s.get("request_count", 0)
                result["input_tokens"] += s.get("input_tokens", 0)
                result["output_tokens"] += s.get("output_tokens", 0)
                result["cache_read_tokens"] += s.get("cache_read_tokens", 0)
                result["cache_write_tokens"] += s.get("cache_write_tokens", 0)
                result["total_tokens"] = (result["input_tokens"] + result["output_tokens"]
                                          + result["cache_read_tokens"] + result["cache_write_tokens"])
                if avg_duration == 0 and s.get("avg_duration_ms", 0) > 0:
                    result["avg_duration_ms"] = s["avg_duration_ms"]
                    avg_duration = s["avg_duration_ms"]
        return result
```

- [ ] **Step 2: 改造 `merge_model_lists` 为可变参数**

将第 903-939 行的 `merge_model_lists` 方法体中 `proxy_models`/`session_models` 两个参数改为 `*lists`：

```python
    @staticmethod
    def merge_model_lists(*lists: list) -> list:
        """合并 N 个 by_model 列表，同名模型 token 求和，字段重命名为 cache*"""
        merged: dict = {}
        for items in lists:
            for item in items:
                r = _Merger._rename(item)
                model = _SessionDao._normalize_model_name(r["model"])
                if model not in merged:
                    merged[model] = {"model": model, "request_count": 0,
                                     "input_tokens": 0, "output_tokens": 0,
                                     "cache_read_tokens": 0, "cache_write_tokens": 0,
                                     "avg_duration_ms": 0}
                m = merged[model]
                m["request_count"] += r.get("request_count", 0)
                m["input_tokens"] += r.get("input_tokens", 0)
                m["output_tokens"] += r.get("output_tokens", 0)
                m["cache_read_tokens"] += r.get("cache_read_tokens", 0)
                m["cache_write_tokens"] += r.get("cache_write_tokens", 0)
                m["avg_duration_ms"] = r.get("avg_duration_ms", 0)

        for m in merged.values():
            m["total_tokens"] = (m["input_tokens"] + m["output_tokens"]
                                 + m["cache_read_tokens"] + m["cache_write_tokens"])
        return list(merged.values())
```

- [ ] **Step 3: 改造 `merge_trend_lists` 为可变参数**

将第 942-977 行的 `merge_trend_lists` 方法体中 `proxy_trend`/`session_trend` 两个参数改为 `*lists`：

```python
    @staticmethod
    def merge_trend_lists(*lists: list) -> list:
        """合并 N 个趋势列表，同时间点各指标求和，字段重命名为 cache*，key 统一为 date"""
        merged: dict = {}
        for items in lists:
            for item in items:
                r = _Merger._rename(item)
                key = r.get("date", r.get("time", ""))
                if key not in merged:
                    merged[key] = {"date": key, "request_count": 0,
                                   "input_tokens": 0, "output_tokens": 0,
                                   "cache_read_tokens": 0, "cache_write_tokens": 0}
                m = merged[key]
                m["request_count"] += r.get("request_count", 0)
                m["input_tokens"] += r.get("input_tokens", 0)
                m["output_tokens"] += r.get("output_tokens", 0)
                m["cache_read_tokens"] += r.get("cache_read_tokens", 0)
                m["cache_write_tokens"] += r.get("cache_write_tokens", 0)

        for m in merged.values():
            m["total_tokens"] = (m["input_tokens"] + m["output_tokens"]
                                 + m["cache_read_tokens"] + m["cache_write_tokens"])
        return list(merged.values())
```

- [ ] **Step 4: 验证 _Merger 类文档字符串**

将第 865-866 行的类文档字符串更新为：

```python
class _Merger:
    """N 数据源合并：按规范化模型名求和，字段名统一为 cache_*，趋势 key 统一为 date"""
```

- [ ] **Step 5: 运行测试确认无回归**

```bash
python3 -m pytest test/test_stats_service.py -q
```

确保现有 _Merger 相关测试（`TestStatsService`、`TestMerger` 等）仍然通过。

- [ ] **Step 6: Commit**

```bash
git add stats_service.py
git commit -m "refactor: _Merger 改造为 N 源合并（可变参数）"
```

---

### Task 4: `_Merger` 三源测试

**Files:**
- Modify: `test/test_stats_service.py`

- [ ] **Step 1: 在 `TestOpenCodeDao` 类中追加三源合并测试**

在 `test_db_not_exists` 方法之后、类结束之前追加：

```python
    # ─── _Merger 三源合并测试 ───

    def test_merger_three_source_summary(self):
        """merge_summary 三源求和正确：proxy + session + opencode 数值字段累加。"""
        from stats_service import _Merger

        p = {"period": "week", "request_count": 10, "input_tokens": 100,
             "output_tokens": 50, "cached_read_tokens": 20, "cached_write_tokens": 5,
             "total_tokens": 175, "avg_duration_ms": 300}
        s = {"period": "week", "request_count": 3, "input_tokens": 30,
             "output_tokens": 15, "cached_read_tokens": 10, "cached_write_tokens": 2,
             "total_tokens": 57, "avg_duration_ms": 0}
        o = {"period": "week", "request_count": 5, "input_tokens": 200,
             "output_tokens": 100, "cached_read_tokens": 50, "cached_write_tokens": 10,
             "total_tokens": 360, "avg_duration_ms": 250}

        result = _Merger.merge_summary(p, s, o)
        self.assertEqual(result["request_count"], 18)  # 10+3+5
        self.assertEqual(result["input_tokens"], 330)  # 100+30+200
        self.assertEqual(result["output_tokens"], 165)  # 50+15+100
        # avg_duration_ms: proxy(300) > opencode(250) > session(0), 取 proxy
        self.assertEqual(result["avg_duration_ms"], 300)

    def test_merger_three_source_summary_empty_sources(self):
        """merge_summary 部分源为空：健壮处理。"""
        from stats_service import _Merger

        p = {"period": "week", "request_count": 10, "input_tokens": 100,
             "output_tokens": 50, "cached_read_tokens": 20, "cached_write_tokens": 5,
             "total_tokens": 175, "avg_duration_ms": 300}
        empty = {"period": "week", "request_count": 0, "input_tokens": 0,
                 "output_tokens": 0, "cached_read_tokens": 0, "cached_write_tokens": 0,
                 "total_tokens": 0, "avg_duration_ms": 0}

        result = _Merger.merge_summary(p, empty, empty)
        self.assertEqual(result["request_count"], 10)

    def test_merger_three_source_model_lists(self):
        """merge_model_lists 三源同名模型求和，不同模型独立保留。"""
        from stats_service import _Merger

        p = [{"model": "gpt-4", "request_count": 5, "input_tokens": 100,
              "output_tokens": 50, "cached_read_tokens": 10, "cached_write_tokens": 5,
              "avg_duration_ms": 200}]
        s = [{"model": "gpt-4", "request_count": 3, "input_tokens": 60,
              "output_tokens": 30, "cached_read_tokens": 5, "cached_write_tokens": 2,
              "avg_duration_ms": 0}]
        o = [{"model": "claude-sonnet", "request_count": 2, "input_tokens": 200,
              "output_tokens": 100, "cached_read_tokens": 20, "cached_write_tokens": 10,
              "avg_duration_ms": 0}]

        result = _Merger.merge_model_lists(p, s, o)
        self.assertEqual(len(result), 2)  # gpt-4 + claude-sonnet
        models = {m["model"] for m in result}
        self.assertIn("gpt-4", models)
        self.assertIn("claude-sonnet", models)

        gpt4 = next(m for m in result if m["model"] == "gpt-4")
        self.assertEqual(gpt4["input_tokens"], 160)  # 100+60
        self.assertEqual(gpt4["request_count"], 8)  # 5+3

    def test_merger_three_source_trend_lists(self):
        """merge_trend_lists 三源同时间桶求和。"""
        from stats_service import _Merger

        p = [{"time": "2026-05-10", "request_count": 2, "input_tokens": 100,
              "output_tokens": 50, "cached_read_tokens": 10, "cached_write_tokens": 5}]
        s = [{"time": "2026-05-10", "request_count": 1, "input_tokens": 30,
              "output_tokens": 15, "cached_read_tokens": 5, "cached_write_tokens": 2}]
        o = [{"time": "2026-05-12", "request_count": 3, "input_tokens": 200,
              "output_tokens": 100, "cached_read_tokens": 20, "cached_write_tokens": 10}]

        result = _Merger.merge_trend_lists(p, s, o)
        self.assertEqual(len(result), 2)  # 05-10 + 05-12

        day10 = next(r for r in result if r["date"] == "2026-05-10")
        self.assertEqual(day10["input_tokens"], 130)  # 100+30
        self.assertEqual(day10["request_count"], 3)  # 2+1

        day12 = next(r for r in result if r["date"] == "2026-05-12")
        self.assertEqual(day12["input_tokens"], 200)
```

- [ ] **Step 2: 运行新测试**

```bash
python3 -m pytest test/test_stats_service.py::TestOpenCodeDao -q
```

- [ ] **Step 3: 全量确认**

```bash
python3 -m pytest test/ -q
```

- [ ] **Step 4: Commit**

```bash
git add test/test_stats_service.py
git commit -m "test: 新增 _Merger 三源合并测试（summary/model_lists/trend_lists）"
```

---

### Task 5: `StatsService` 集成 openCode

**Files:**
- Modify: `stats_service.py:980-1430`（`StatsService` 类）

- [ ] **Step 1: 构造函数新增 `opencode_db_path` 参数和单例**

修改 `__init__`（第 988-997 行）为：

```python
    # ─── 默认 opencode 路径 ───
    _OPENCODE_DB_DEFAULT = Path.home() / ".local" / "share" / "opencode" / "opencode.db"

    def __init__(
        self,
        data_db_path: str,
        state_db_path: str,
        opencode_db_path: str | None = None,
    ) -> None:
        self.data_db_path = Path(data_db_path) if data_db_path else DATA_DB
        self.state_db_path = Path(state_db_path)

        self.opencode_db_path = Path(opencode_db_path) if opencode_db_path else self._OPENCODE_DB_DEFAULT
        self._opencode_dao = None  # 懒加载

        # 初始化上游解析器
        self._upstream_resolver = _UpstreamResolver(self.data_db_path)
```

- [ ] **Step 2: 新增 `_get_opencode_dao` 方法**

在 `_get_session_dao` 方法（第 1007 行）之后插入：

```python
    def _get_opencode_dao(self):
        """获取 OpenCodeDao 实例，数据库不存在时返回 None。"""
        if self._opencode_dao is None:
            dao = _OpenCodeDao(self.opencode_db_path)
            self._opencode_dao = dao if dao.db_path.exists() else None
        return self._opencode_dao
```

- [ ] **Step 3: 更新 `fetch_by_model`（第 1014-1028 行）**

在 `session_models = ...` 之后加入 opencode 数据：

```python
    def fetch_by_model(self, period: str) -> list:
        """按模型维度获取统计数据，合并 proxy + sessions + opencode 三源。"""
        dao = self._get_dao()
        session_dao = self._get_session_dao()
        opencode_dao = self._get_opencode_dao()
        proxy_models = dao.aggregate_by_model(period)
        session_models = session_dao.aggregate_by_model(period)
        opencode_models = opencode_dao.aggregate_by_model(period) if opencode_dao else []
        merged = _Merger.merge_model_lists(proxy_models, session_models, opencode_models)
        calculator = self._get_calculator()
        for m in merged:
            m["estimated_cost_cny"] = round(calculator.calculate(
                model=m["model"], input_tokens=m["input_tokens"],
                output_tokens=m["output_tokens"],
                cache_read_tokens=m["cache_read_tokens"],
                cache_write_tokens=m["cache_write_tokens"],
            ), 6)
            m["display_name"] = calculator.get_display_name(m["model"])
        merged.sort(key=lambda x: x.get("total_tokens", 0), reverse=True)
        return merged
```

- [ ] **Step 4: 更新 `fetch_summary`（第 1266-1287 行）**

```python
    def fetch_summary(self, period: str) -> dict:
        """获取汇总统计数据，合并 proxy + sessions + opencode 三源。成本按模型逐个计算后求和。"""
        dao = self._get_dao()
        session_dao = self._get_session_dao()
        opencode_dao = self._get_opencode_dao()
        proxy = dao.aggregate_summary(period)
        session = session_dao.aggregate_summary(period)
        opencode = opencode_dao.aggregate_summary(period) if opencode_dao else {}
        result = _Merger.merge_summary(proxy, session, opencode)
        # 成本按模型逐个计算再求和
        proxy_models = dao.aggregate_by_model(period)
        session_models = session_dao.aggregate_by_model(period)
        opencode_models = opencode_dao.aggregate_by_model(period) if opencode_dao else []
        merged_models = _Merger.merge_model_lists(proxy_models, session_models, opencode_models)
        calculator = self._get_calculator()
        total_cost = 0
        for m in merged_models:
            total_cost += calculator.calculate(
                model=m["model"], input_tokens=m["input_tokens"],
                output_tokens=m["output_tokens"],
                cache_read_tokens=m["cache_read_tokens"],
                cache_write_tokens=m["cache_write_tokens"],
            )
        result["estimated_cost_cny"] = round(total_cost, 6)
        return result
```

- [ ] **Step 5: 更新 `fetch_trend`（第 1240-1264 行）**

```python
    def fetch_trend(self, period: str) -> list:
        """获取时间趋势数据，合并 proxy + sessions + opencode 三源。逐点计算成本。"""
        dao = self._get_dao()
        session_dao = self._get_session_dao()
        opencode_dao = self._get_opencode_dao()
        proxy_trend = dao.aggregate_trend(period)
        session_trend = session_dao.aggregate_trend(period)
        opencode_trend = opencode_dao.aggregate_trend(period) if opencode_dao else []
        merged = _Merger.merge_trend_lists(proxy_trend, session_trend, opencode_trend)
        merged.sort(key=lambda x: x.get("date", ""))

        # 加权均摊：从 per-model 汇总数据计算总成本，按 token 比例均摊到每个时间桶
        by_model = self.fetch_by_model(period)
        total_cost = sum(m.get("estimated_cost_cny", 0) for m in by_model)
        total_tokens = sum(m.get("total_tokens", 0) or 0 for m in by_model)

        for point in merged:
            point_tokens = point.get("total_tokens", 0) or (
                point.get("input_tokens", 0) + point.get("output_tokens", 0)
                + point.get("cache_read_tokens", 0) + point.get("cache_write_tokens", 0)
            )
            if total_tokens > 0:
                point["estimated_cost_cny"] = total_cost * point_tokens / total_tokens
            else:
                point["estimated_cost_cny"] = 0.0

        return merged
```

- [ ] **Step 6: 更新 `fetch_by_upstream` — 加入 `[OpenCode]` 桶**

在 `fetch_by_upstream` 方法中，在 session_upstream_data 处理之后、合并逻辑之前插入 opencode 处理：

```python
        # X. opencode 数据 → 归入 "[OpenCode]" 桶
        opencode_dao = self._get_opencode_dao()
        if opencode_dao:
            oc_models = opencode_dao.aggregate_by_model(period)
            oc_name = "[OpenCode]"
            for oc_row in oc_models:
                if oc_name not in session_upstream_data:
                    session_upstream_data[oc_name] = {
                        "upstream": oc_name,
                        "request_count": 0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cached_read_tokens": 0,
                        "cached_write_tokens": 0,
                    }
                agg = session_upstream_data[oc_name]
                agg["request_count"] += oc_row["request_count"] or 0
                agg["input_tokens"] += oc_row["input_tokens"] or 0
                agg["output_tokens"] += oc_row["output_tokens"] or 0
                agg["cached_read_tokens"] += oc_row["cached_read_tokens"] or 0
                agg["cached_write_tokens"] += oc_row["cached_write_tokens"] or 0
```

然后在结果构造部分，对 `[OpenCode]` 的 `base_url` 设为 `None`、`cost` 为 0：

在现有 `if name == "__unknown__"` 的处理旁边，添加 `elif name == "[OpenCode]"` 分支。具体找到这行（约第 1195 行附近）：

```python
            elif name == "__unknown__":
                base_url = None
```

在其后追加：

```python
            elif name == "[OpenCode]":
                base_url = None
```

- [ ] **Step 7: 更新 `fetch_requests` — 加入 opencode 分页数据**

在 `fetch_requests` 方法中，在 session 查询之后、unified_requests 之前插入：

```python
        opencode_dao = self._get_opencode_dao()
        if opencode_dao:
            oc_rows, oc_total = opencode_dao.query_messages_paged(
                period=period,
                model=model,
                request_type=request_type,
                limit=fetch_limit,
                offset=0,
            )
        else:
            oc_rows, oc_total = [], 0

        # ... 在 unified_requests 构建中，在 session_rows 循环之后加入：

        for rec in oc_rows:
            rec = _Merger._rename(rec)
            rec["estimated_cost_cny"] = calculator.calculate(
                model=rec.get("target_model", rec.get("model", "")),
                input_tokens=rec.get("input_tokens", 0),
                output_tokens=rec.get("output_tokens", 0),
                cache_read_tokens=rec.get("cache_read_tokens", 0),
                cache_write_tokens=rec.get("cache_write_tokens", 0),
            )
            unified_requests.append(rec)

        total = token_total + session_total + oc_total
```

类似地更新 `fetch_by_model_requests` 方法。

- [ ] **Step 8: 语法检查**

```bash
python3 -c "from stats_service import StatsService; print('OK')"
```

- [ ] **Step 9: 运行全量测试**

```bash
python3 -m pytest test/ -q
```

- [ ] **Step 10: Commit**

```bash
git add stats_service.py
git commit -m "feat: StatsService 集成 openCode 数据源（fetch_summary/fetch_by_model/fetch_trend/fetch_by_upstream/fetch_requests）"
```

---

### Task 6: 最终验证

- [ ] **Step 1: 全量测试**

```bash
python3 -m pytest test/ -q
```

确认 515 + ~16 新测试全部通过。

- [ ] **Step 2: 重启服务验证 API**

```bash
./server.sh restart
```

- [ ] **Step 3: 冒烟 — 验证 KPI 卡片端点**

```bash
curl -s http://127.0.0.1:18742/api/token_stats/summary | python3 -m json.tool | head -20
```

确认 `estimated_cost_cny` 包含 opencode 数据。

- [ ] **Step 4: Commit（如有修正）**

```bash
git add -A
git commit -m "chore: 最终验证 opencode 集成 — 全量测试通过 + 服务重启"
```

---

### 补充说明

**反向兼容性**：
- `StatsService.__init__` 新增 `opencode_db_path` 参数有默认值，`server/__init__.py` 无需改动
- `_Merger` 方法签名改为 `*args`，所有现有调用方传递的 2 个参数仍正常工作
- 前端完全不变（`request_type: "session"` 匹配现有过滤逻辑）

**边缘情况**：
- opencode.db 不存在 → `_get_opencode_dao()` 返回 None，合并时跳过该源
- message 无 tokens → `IS NOT NULL` 过滤条件自动排除
- 模型 ID 为空字符串 → `aggregate_by_model` 返回 `model: ""`，不影响合并
