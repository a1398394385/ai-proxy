# Token 统计统一查询层 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重构 `stats_service.py`，新增 `_fetch_unified_records()` 作为唯一数据源，所有 `fetch_*` 方法改为内存聚合，消除三源数据不一致。

**Architecture:** 三个 DAO 各新增 `query_raw()` 返回统一格式 dict；`_CostCalculator` 新增 `calculate_breakdown()` 返回 4 项独立成本；`StatsService._fetch_unified_records()` 协调三源查询 + 成本计算 + 排序分页；`fetch_*` 方法变成轻量聚合包装。

**Spec:** `docs/superpowers/specs/2026-05-13-token-stats-unified-query-design.md`

**Tech Stack:** Python 3 stdlib (sqlite3, unittest), 纯内存聚合

---

### Task 1: `_CostCalculator.calculate_breakdown()` — TDD

**Files:**
- Modify: `stats_service.py:548-577` (in `_CostCalculator` class)
- Modify: `test/test_stats_service.py` (add `TestCostCalculatorBreakdown` class)

- [ ] **Step 1: Write the failing test**

在 `test/test_stats_service.py` 中新增测试类。找到合适位置插入（建议在现有 `TestCostCalculator` 类附近）。

```python
class TestCostCalculatorBreakdown(unittest.TestCase):
    """_CostCalculator.calculate_breakdown 测试 — 4 项独立成本拆分。"""

    def setUp(self):
        import os, sqlite3
        from pathlib import Path
        from tempfile import TemporaryDirectory
        self.tmpdir = TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS model_pricing (
                model_id TEXT PRIMARY KEY,
                display_name TEXT,
                input_cost_per_million REAL DEFAULT 0,
                output_cost_per_million REAL DEFAULT 0,
                cache_read_cost_per_million REAL DEFAULT 0,
                cache_creation_cost_per_million REAL DEFAULT 0,
                currency TEXT DEFAULT 'RMB',
                multiplier REAL DEFAULT 1.0,
                created_at TEXT,
                updated_at TEXT
            )
        """)
        # 种子数据：RMB 定价
        conn.execute(
            "INSERT INTO model_pricing (model_id, display_name, input_cost_per_million, "
            "output_cost_per_million, cache_read_cost_per_million, cache_creation_cost_per_million, "
            "currency) VALUES (?, ?, ?, ?, ?, ?, 'RMB')",
            ("test-model", "Test Model", 1.0, 2.0, 0.5, 0.25),
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_breakdown_all_tokens_positive(self):
        """所有 token 维度均为正数时返回 4 项非零成本。"""
        from stats_service import _CostCalculator
        calc = _CostCalculator(str(self.db_path))

        result = calc.calculate_breakdown(
            model="test-model",
            input_tokens=1_000_000,
            output_tokens=500_000,
            cache_read_tokens=200_000,
            cache_write_tokens=100_000,
        )

        self.assertAlmostEqual(result["input_cost_cny"], 1.0, places=6)
        self.assertAlmostEqual(result["output_cost_cny"], 1.0, places=6)
        self.assertAlmostEqual(result["cache_read_cost_cny"], 0.1, places=6)
        self.assertAlmostEqual(result["cache_write_cost_cny"], 0.025, places=6)

    def test_breakdown_unknown_model_returns_zeros(self):
        """无定价模型返回 4 项 0.0。"""
        from stats_service import _CostCalculator
        calc = _CostCalculator(str(self.db_path))

        result = calc.calculate_breakdown(
            model="nonexistent-model",
            input_tokens=1_000_000,
            output_tokens=500_000,
            cache_read_tokens=200_000,
            cache_write_tokens=100_000,
        )

        self.assertEqual(result["input_cost_cny"], 0.0)
        self.assertEqual(result["output_cost_cny"], 0.0)
        self.assertEqual(result["cache_read_cost_cny"], 0.0)
        self.assertEqual(result["cache_write_cost_cny"], 0.0)

    def test_breakdown_zeros_when_all_tokens_zero(self):
        """所有 token 为 0 时返回 4 项 0.0。"""
        from stats_service import _CostCalculator
        calc = _CostCalculator(str(self.db_path))

        result = calc.calculate_breakdown(
            model="test-model",
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
        )

        self.assertEqual(result["input_cost_cny"], 0.0)
        self.assertEqual(result["output_cost_cny"], 0.0)
        self.assertEqual(result["cache_read_cost_cny"], 0.0)
        self.assertEqual(result["cache_write_cost_cny"], 0.0)

    def test_breakdown_consistent_with_calculate_total(self):
        """calculate_breakdown 的 4 项之和应等于 calculate() 的返回值。"""
        from stats_service import _CostCalculator
        calc = _CostCalculator(str(self.db_path))

        breakdown = calc.calculate_breakdown(
            model="test-model",
            input_tokens=1_500_000,
            output_tokens=800_000,
            cache_read_tokens=300_000,
            cache_write_tokens=150_000,
        )
        total_from_breakdown = (
            breakdown["input_cost_cny"] + breakdown["output_cost_cny"]
            + breakdown["cache_read_cost_cny"] + breakdown["cache_write_cost_cny"]
        )
        total = calc.calculate(
            model="test-model",
            input_tokens=1_500_000,
            output_tokens=800_000,
            cache_read_tokens=300_000,
            cache_write_tokens=150_000,
        )
        self.assertAlmostEqual(total_from_breakdown, total, places=6)

    def test_breakdown_case_insensitive_model(self):
        """模型名大小写不敏感匹配。"""
        from stats_service import _CostCalculator
        calc = _CostCalculator(str(self.db_path))

        result_lower = calc.calculate_breakdown(
            model="test-model",
            input_tokens=1_000_000, output_tokens=0,
            cache_read_tokens=0, cache_write_tokens=0,
        )
        result_upper = calc.calculate_breakdown(
            model="TEST-MODEL",
            input_tokens=1_000_000, output_tokens=0,
            cache_read_tokens=0, cache_write_tokens=0,
        )
        self.assertEqual(result_lower["input_cost_cny"], result_upper["input_cost_cny"])
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest test/test_stats_service.py::TestCostCalculatorBreakdown -q
```
Expected: 5 failures, `_CostCalculator` object has no attribute `calculate_breakdown`

- [ ] **Step 3: Implement `calculate_breakdown()`**

在 `stats_service.py` 的 `_CostCalculator` 类中，`calculate()` 方法之后新增：

```python
    def calculate_breakdown(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
    ) -> dict:
        """返回 4 项独立成本（人民币），不求和。

        Returns:
            {"input_cost_cny": float, "output_cost_cny": float,
             "cache_read_cost_cny": float, "cache_write_cost_cny": float}
        """
        pricing = self.get_pricing()
        if not pricing:
            return {"input_cost_cny": 0.0, "output_cost_cny": 0.0,
                    "cache_read_cost_cny": 0.0, "cache_write_cost_cny": 0.0}

        key = model.lower() if model else ""
        if key not in pricing:
            return {"input_cost_cny": 0.0, "output_cost_cny": 0.0,
                    "cache_read_cost_cny": 0.0, "cache_write_cost_cny": 0.0}

        p = pricing[key]
        input_cost = (input_tokens or 0) / 1_000_000 * p["input_cost"]
        output_cost = (output_tokens or 0) / 1_000_000 * p["output_cost"]
        cache_read_cost = (cache_read_tokens or 0) / 1_000_000 * p["cache_read_cost"]
        cache_write_cost = (cache_write_tokens or 0) / 1_000_000 * p["cache_creation_cost"]

        return {
            "input_cost_cny": round(input_cost, 6),
            "output_cost_cny": round(output_cost, 6),
            "cache_read_cost_cny": round(cache_read_cost, 6),
            "cache_write_cost_cny": round(cache_write_cost, 6),
        }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest test/test_stats_service.py::TestCostCalculatorBreakdown -v
```
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add stats_service.py test/test_stats_service.py
git commit -m "feat: _CostCalculator 新增 calculate_breakdown() 返回 4 项独立成本"
```

---

### Task 2: `_TokenStatsDao.query_raw()` — TDD

**Files:**
- Modify: `stats_service.py:73-132` (add `query_raw()` to `_TokenStatsDao`)
- Modify: `test/test_stats_service.py` (add test)

- [ ] **Step 1: Write the failing test**

在 `test/test_stats_service.py` 中新增（`TestTokenStatsDao` 类内，该类已存在于测试文件中）：

```python
    def test_query_raw_returns_unified_schema(self):
        """query_raw 返回统一格式记录，字段名符合 schema。"""
        from stats_service import _TokenStatsDao
        dao = _TokenStatsDao(self.data_db)
        self._seed_token_stats_data()

        records = dao.query_raw("week")

        self.assertIsInstance(records, list)
        self.assertGreater(len(records), 0)
        r = records[0]
        # 统一 schema 必须字段
        self.assertIn("request_id", r)
        self.assertIn("model", r)
        self.assertIn("request_type", r)
        self.assertIn("request_ts", r)
        self.assertIn("duration_ms", r)
        self.assertIn("status", r)
        self.assertIn("input_tokens", r)
        self.assertIn("output_tokens", r)
        self.assertIn("cache_read_tokens", r)   # 注意：不带 'd'
        self.assertIn("cache_write_tokens", r)  # 注意：不带 'd'
        self.assertIn("upstream_id", r)
        # 不应存在的旧字段
        self.assertNotIn("_source", r)
        self.assertNotIn("target_model", r)
        self.assertNotIn("cached_read_tokens", r)
        self.assertNotIn("cached_write_tokens", r)

    def test_query_raw_upstream_id_null_defaults_unknown(self):
        """upstream_id 为 NULL 时，query_raw 返回 '__unknown__'。"""
        from stats_service import _TokenStatsDao
        dao = _TokenStatsDao(self.data_db)

        conn = sqlite3.connect(str(self.data_db))
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO token_stats (request_id, request_type, model, target_model, "
            "request_ts, duration_ms, input_tokens, output_tokens, cached_read_tokens, "
            "cached_write_tokens, upstream_id, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'completed', ?)",
            ("req-1", "responses", "gpt-4", "gpt-4", now, 100, 50, 30, 0, 0, now),
        )
        conn.commit()
        conn.close()

        records = dao.query_raw("week")
        self.assertEqual(records[0]["upstream_id"], "__unknown__")

    def _seed_token_stats_data(self):
        """插入测试用 token_stats 数据。"""
        conn = sqlite3.connect(str(self.data_db))
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO token_stats (request_id, request_type, model, target_model, "
            "request_ts, duration_ms, input_tokens, output_tokens, cached_read_tokens, "
            "cached_write_tokens, upstream_id, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("req-001", "responses", "deepseek-v4-flash", "deepseek-v4-flash",
             now, 500, 1000, 500, 100, 50, "up-deepseek", "completed", now),
        )
        conn.commit()
        conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest test/test_stats_service.py::TestTokenStatsDao::test_query_raw_returns_unified_schema -q
```
Expected: AttributeError, `_TokenStatsDao` has no attribute `query_raw`

- [ ] **Step 3: Implement `_TokenStatsDao.query_raw()`**

在 `stats_service.py` 的 `_TokenStatsDao` 类中，`query_token_stats()` 方法之前新增：

```python
    def query_raw(
        self,
        period: str,
        model: str | None = None,
        request_type: str | None = None,
    ) -> list[dict]:
        """查询原始 token_stats 记录，返回统一格式 dict 列表。

        Args:
            period: 时间周期
            model: 可选，按 target_model 精确匹配（已规范化的模型名）
            request_type: 可选，按 request_type 过滤

        Returns:
            统一格式记录列表
        """
        time_condition = self._period_to_condition(period)
        conditions = [time_condition]
        params: list = []

        if model:
            conditions.append("target_model = ?")
            params.append(model)

        if request_type:
            conditions.append("request_type = ?")
            params.append(request_type)

        where_clause = " AND ".join(conditions)

        conn = self._get_conn()
        try:
            rows = conn.execute(
                f"""
                SELECT request_id, request_type, target_model,
                       request_ts, duration_ms, input_tokens, output_tokens,
                       cached_read_tokens, cached_write_tokens,
                       COALESCE(upstream_id, '__unknown__') as upstream_id,
                       status
                FROM token_stats
                WHERE {where_clause}
                ORDER BY request_ts DESC
                """,
                params,
            ).fetchall()

            return [
                {
                    "request_id": row["request_id"],
                    "model": row["target_model"] or "",
                    "request_type": row["request_type"],
                    "request_ts": row["request_ts"],
                    "duration_ms": row["duration_ms"],
                    "status": row["status"] or "completed",
                    "input_tokens": row["input_tokens"] or 0,
                    "output_tokens": row["output_tokens"] or 0,
                    "cache_read_tokens": row["cached_read_tokens"] or 0,   # DB 列名带 d
                    "cache_write_tokens": row["cached_write_tokens"] or 0, # 输出时去 d
                    "upstream_id": row["upstream_id"],
                }
                for row in rows
            ]
        finally:
            conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest test/test_stats_service.py::TestTokenStatsDao::test_query_raw_returns_unified_schema test/test_stats_service.py::TestTokenStatsDao::test_query_raw_upstream_id_null_defaults_unknown -v
```
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add stats_service.py test/test_stats_service.py
git commit -m "feat: _TokenStatsDao 新增 query_raw() 返回统一格式记录"
```

---

### Task 3: `_SessionDao.query_raw()` — TDD

**Files:**
- Modify: `stats_service.py:653-750` (add `query_raw()` to `_SessionDao`)
- Modify: `test/test_stats_service.py` (add test)

- [ ] **Step 1: Write the failing test**

在 `test/test_stats_service.py` 的 `TestSessionDao` 类中新增：

```python
    def test_query_raw_returns_unified_schema(self):
        """query_raw 返回统一格式，upstream_id 固定为 'hermes'。"""
        import time as _time
        from stats_service import _SessionDao

        conn = sqlite3.connect(str(self.state_db))
        now_ts = _time.time()
        conn.execute(
            "INSERT INTO sessions (model, started_at, input_tokens, output_tokens, "
            "cache_read_tokens, cache_write_tokens) VALUES (?, ?, ?, ?, ?, ?)",
            ("claude-sonnet-4-6[1m]", now_ts, 1000, 500, 100, 50),
        )
        conn.commit()
        conn.close()

        dao = _SessionDao(self.state_db)
        records = dao.query_raw("week")

        self.assertEqual(len(records), 1)
        r = records[0]
        self.assertEqual(r["upstream_id"], "hermes")
        self.assertEqual(r["model"], "claude-sonnet-4-6")  # 去掉了 [1m] 后缀
        self.assertEqual(r["request_type"], "session")
        self.assertEqual(r["input_tokens"], 1000)
        self.assertEqual(r["output_tokens"], 500)
        self.assertEqual(r["cache_read_tokens"], 100)
        self.assertEqual(r["cache_write_tokens"], 50)
        self.assertEqual(r["status"], "completed")
        self.assertNotIn("_source", r)
        self.assertNotIn("target_model", r)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest test/test_stats_service.py::TestSessionDao::test_query_raw_returns_unified_schema -q
```
Expected: AttributeError, `_SessionDao` has no attribute `query_raw`

- [ ] **Step 3: Implement `_SessionDao.query_raw()`**

在 `stats_service.py` 的 `_SessionDao` 类中，`query_sessions()` 方法之前新增：

```python
    def query_raw(
        self,
        period: str,
        model: str | None = None,
    ) -> list[dict]:
        """查询原始 sessions 记录，返回统一格式 dict 列表。

        Args:
            period: 时间周期
            model: 可选，按规范化模型名过滤（匹配 model 或 model[ctx] 前缀）

        Returns:
            统一格式记录列表。state.db 不存在时返回空列表。
        """
        conn = self._get_conn()
        if conn is None:
            return []
        try:
            time_condition = self._period_to_condition(period)
            conditions = [time_condition, "input_tokens IS NOT NULL"]
            params: list = []

            if model:
                conditions.append("(model = ? OR model LIKE ?)")
                params.extend([model, f"{model}[%"])

            where_clause = " AND ".join(conditions)

            rows = conn.execute(
                f"""
                SELECT id, model, started_at, input_tokens, output_tokens,
                       cache_read_tokens, cache_write_tokens
                FROM sessions
                WHERE {where_clause}
                ORDER BY started_at DESC
                """,
                params,
            ).fetchall()

            records = []
            for row in rows:
                model_name = row["model"] or ""
                normalized = self._normalize_model_name(model_name)
                ts = row["started_at"]
                if isinstance(ts, (int, float)):
                    ts = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                records.append({
                    "request_id": f"sess-{row['id']}",
                    "model": normalized,
                    "request_type": "session",
                    "request_ts": ts,
                    "duration_ms": None,
                    "status": "completed",
                    "input_tokens": row["input_tokens"] or 0,
                    "output_tokens": row["output_tokens"] or 0,
                    "cache_read_tokens": row["cache_read_tokens"] or 0,
                    "cache_write_tokens": row["cache_write_tokens"] or 0,
                    "upstream_id": "hermes",
                })
            return records
        except Exception:
            return []
        finally:
            conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest test/test_stats_service.py::TestSessionDao::test_query_raw_returns_unified_schema -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add stats_service.py test/test_stats_service.py
git commit -m "feat: _SessionDao 新增 query_raw() 返回统一格式，upstream_id='hermes'"
```

---

### Task 4: `_OpenCodeDao.query_raw()` — TDD

**Files:**
- Modify: `stats_service.py:1082-1142` (add `query_raw()` to `_OpenCodeDao`)
- Modify: `test/test_stats_service.py` (add test)

- [ ] **Step 1: Write the failing test**

在 `test/test_stats_service.py` 的 `TestOpenCodeDao` 类中新增：

```python
    def test_query_raw_returns_unified_schema(self):
        """query_raw 返回统一格式，upstream_id 固定为 'opencode'。"""
        from stats_service import _OpenCodeDao
        dao = _OpenCodeDao(self.db_path)
        records = dao.query_raw("week")

        self.assertGreater(len(records), 0)
        r = records[0]
        self.assertEqual(r["upstream_id"], "opencode")
        self.assertEqual(r["request_type"], "session")
        self.assertIn("input_tokens", r)
        self.assertIn("output_tokens", r)
        self.assertIn("cache_read_tokens", r)
        self.assertIn("cache_write_tokens", r)
        self.assertNotIn("_source", r)
        self.assertNotIn("target_model", r)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest test/test_stats_service.py::TestOpenCodeDao::test_query_raw_returns_unified_schema -q
```
Expected: AttributeError

- [ ] **Step 3: Implement `_OpenCodeDao.query_raw()`**

在 `stats_service.py` 的 `_OpenCodeDao` 类中，`query_messages_paged()` 方法之前新增：

```python
    def query_raw(
        self,
        period: str,
        model: str | None = None,
    ) -> list[dict]:
        """查询原始 opencode message 记录，返回统一格式 dict 列表。

        Args:
            period: 时间周期
            model: 可选，按 modelID 过滤

        Returns:
            统一格式记录列表。opencode.db 不存在时返回空列表。
        """
        conn = self._get_conn()
        if conn is None:
            return []
        try:
            time_condition = self._period_to_condition(period)
            conditions = [time_condition, "json_extract(m.data, '$.tokens.input') IS NOT NULL"]
            params: list = []

            if model:
                conditions.append("json_extract(m.data, '$.modelID') = ?")
                params.append(model)

            where_clause = " AND ".join(conditions)

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
                WHERE {where_clause}
                ORDER BY m.time_created DESC
                """,
                params,
            ).fetchall()

            return [
                {
                    "request_id": f"oc-msg-{row['message_id']}",
                    "model": row["model_id"] or "",
                    "request_type": "session",
                    "request_ts": row["request_ts"],
                    "duration_ms": row["duration_ms"],
                    "status": "completed",
                    "input_tokens": row["input_tokens"] or 0,
                    "output_tokens": row["output_tokens"] or 0,
                    "cache_read_tokens": row["cache_read_tokens"] or 0,
                    "cache_write_tokens": row["cache_write_tokens"] or 0,
                    "upstream_id": "opencode",
                }
                for row in rows
            ]
        except Exception:
            return []
        finally:
            conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest test/test_stats_service.py::TestOpenCodeDao::test_query_raw_returns_unified_schema -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add stats_service.py test/test_stats_service.py
git commit -m "feat: _OpenCodeDao 新增 query_raw() 返回统一格式，upstream_id='opencode'"
```

---

### Task 5: `StatsService._fetch_unified_records()` — TDD

**Files:**
- Modify: `stats_service.py:1248-1280` (add method to `StatsService`)
- Modify: `test/test_stats_service.py` (add test class)

- [ ] **Step 1: Write the failing test**

在 `test/test_stats_service.py` 中新增测试类：

```python
class TestFetchUnifiedRecords(unittest.TestCase):
    """_fetch_unified_records 集成测试 — 三源数据合并 + 成本计算 + 分页。"""

    def setUp(self):
        import json, time
        self.tmpdir = tempfile.mkdtemp()
        self.data_db = Path(self.tmpdir) / "access_log.db"
        self.state_db = Path(self.tmpdir) / "state.db"
        self.opencode_db = Path(self.tmpdir) / "opencode.db"

        # ── data db: token_stats + upstreams + target_models + model_pricing ──
        conn = sqlite3.connect(str(self.data_db))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""CREATE TABLE token_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL,
            request_type TEXT NOT NULL, model TEXT NOT NULL, target_model TEXT NOT NULL,
            request_ts TEXT NOT NULL, duration_ms INTEGER, input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0, cached_read_tokens INTEGER DEFAULT 0,
            cached_write_tokens INTEGER DEFAULT 0, upstream_id INTEGER,
            status TEXT DEFAULT 'completed', created_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE upstreams (
            id TEXT PRIMARY KEY, base_url TEXT, api_key TEXT, name TEXT,
            timeout INTEGER, connect_timeout INTEGER, ssl_verify INTEGER,
            retry INTEGER, is_active INTEGER, format TEXT)""")
        conn.execute("""CREATE TABLE target_models (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, upstream_id TEXT,
            multimodal INTEGER)""")
        conn.execute("""CREATE TABLE model_pricing (
            model_id TEXT PRIMARY KEY, display_name TEXT,
            input_cost_per_million REAL DEFAULT 0, output_cost_per_million REAL DEFAULT 0,
            cache_read_cost_per_million REAL DEFAULT 0, cache_creation_cost_per_million REAL DEFAULT 0,
            currency TEXT DEFAULT 'RMB', multiplier REAL DEFAULT 1.0,
            created_at TEXT, updated_at TEXT)""")

        # 上游 + 模型映射
        conn.execute("INSERT INTO upstreams (id, base_url, name, is_active, format) "
                     "VALUES ('up-ds', 'https://api.deepseek.com', 'DeepSeek', 1, 'chat_completions')")
        conn.execute("INSERT INTO target_models (name, upstream_id) VALUES ('deepseek-v4-flash', 'up-ds')")
        # 定价
        conn.execute("INSERT INTO model_pricing (model_id, display_name, input_cost_per_million, "
                     "output_cost_per_million, cache_read_cost_per_million, cache_creation_cost_per_million, "
                     "currency) VALUES ('deepseek-v4-flash', 'DeepSeek V4 Flash', 1.0, 2.0, 0.5, 0.25, 'RMB')")
        # token_stats 数据
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("INSERT INTO token_stats (request_id, request_type, model, target_model, "
                     "request_ts, duration_ms, input_tokens, output_tokens, cached_read_tokens, "
                     "cached_write_tokens, upstream_id, status, created_at) "
                     "VALUES ('req-001', 'responses', 'deepseek-v4-flash', 'deepseek-v4-flash', "
                     "?, 500, 1000, 500, 100, 50, 'up-ds', 'completed', ?)", (now, now))
        conn.commit()
        conn.close()

        # ── state db: sessions ──
        sconn = sqlite3.connect(str(self.state_db))
        sconn.execute("""CREATE TABLE sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, model TEXT,
            started_at REAL NOT NULL, input_tokens INTEGER, output_tokens INTEGER,
            cache_read_tokens INTEGER DEFAULT 0, cache_write_tokens INTEGER DEFAULT 0,
            message_count INTEGER DEFAULT 0)""")
        sconn.execute("INSERT INTO sessions (model, started_at, input_tokens, output_tokens, "
                      "cache_read_tokens, cache_write_tokens) VALUES (?, ?, ?, ?, ?, ?)",
                      ("claude-sonnet-4-6[1m]", time.time(), 2000, 1000, 200, 100))
        sconn.commit()
        sconn.close()

        # ── opencode db: message ──
        oconn = sqlite3.connect(str(self.opencode_db))
        oconn.execute("""CREATE TABLE session (id TEXT PRIMARY KEY, model TEXT, time_created INTEGER)""")
        oconn.execute("""CREATE TABLE message (
            id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
            time_created INTEGER NOT NULL, data TEXT NOT NULL)""")
        now_ms = int(time.time() * 1000)
        msg_data = json.dumps({
            "role": "assistant", "modelID": "mimo-v2.5-pro",
            "tokens": {"input": 300, "output": 150, "reasoning": 0, "cache": {"read": 50, "write": 10}},
            "time": {"created": now_ms - 86400000, "completed": now_ms - 86300000},
        })
        oconn.execute("INSERT INTO message (id, session_id, time_created, data) VALUES (?, ?, ?, ?)",
                      ("msg-001", "ses-001", now_ms - 86400000, msg_data))
        oconn.commit()
        oconn.close()

    def tearDown(self):
        import os
        for p in [self.data_db, self.state_db, self.opencode_db]:
            if p.exists():
                os.remove(str(p))
                for s in ["-wal", "-shm"]:
                    wp = Path(str(p) + s)
                    if wp.exists():
                        os.remove(str(wp))
        os.rmdir(self.tmpdir)

    def _create_service(self):
        from stats_service import StatsService
        return StatsService(
            data_db_path=str(self.data_db),
            state_db_path=str(self.state_db),
            opencode_db_path=str(self.opencode_db),
        )

    def test_returns_all_records_from_three_sources(self):
        """三源数据合并后应包含全部记录。"""
        svc = self._create_service()
        records = svc._fetch_unified_records("week")

        # proxy: 1, session: 1, opencode: 1 → 共 3 条
        self.assertEqual(len(records), 3)
        upstream_ids = {r["upstream_id"] for r in records}
        self.assertIn("up-ds", upstream_ids)
        self.assertIn("hermes", upstream_ids)
        self.assertIn("opencode", upstream_ids)

    def test_cost_breakdown_fields_present(self):
        """每条记录包含 4 项独立成本。"""
        svc = self._create_service()
        records = svc._fetch_unified_records("week")

        for r in records:
            self.assertIn("input_cost_cny", r)
            self.assertIn("output_cost_cny", r)
            self.assertIn("cache_read_cost_cny", r)
            self.assertIn("cache_write_cost_cny", r)
            self.assertGreaterEqual(r["input_cost_cny"], 0)
            self.assertGreaterEqual(r["output_cost_cny"], 0)

    def test_model_normalization_for_sessions(self):
        """sessions 来源的 model 已去掉 [ctx] 后缀。"""
        svc = self._create_service()
        records = svc._fetch_unified_records("week")

        session_recs = [r for r in records if r["upstream_id"] == "hermes"]
        self.assertEqual(len(session_recs), 1)
        self.assertEqual(session_recs[0]["model"], "claude-sonnet-4-6")

    def test_field_rename_cached_to_cache(self):
        """token_stats 来源的字段名为 cache_* 而非 cached_*。"""
        svc = self._create_service()
        records = svc._fetch_unified_records("week")

        proxy_recs = [r for r in records if r["upstream_id"] == "up-ds"]
        self.assertEqual(len(proxy_recs), 1)
        r = proxy_recs[0]
        self.assertNotIn("cached_read_tokens", r)
        self.assertNotIn("cached_write_tokens", r)
        self.assertIn("cache_read_tokens", r)
        self.assertIn("cache_write_tokens", r)

    def test_pagination_returns_slice_and_total(self):
        """带分页参数时返回 (records, total) 元组。"""
        svc = self._create_service()
        result = svc._fetch_unified_records("week", limit=2, offset=0)

        self.assertIsInstance(result, tuple)
        records, total = result
        self.assertEqual(total, 3)
        self.assertEqual(len(records), 2)

    def test_model_filter_across_sources(self):
        """模型筛选跨三源生效。"""
        svc = self._create_service()
        records = svc._fetch_unified_records("week", model="deepseek-v4-flash")

        # 应只匹配 token_stats 中那条 deepseek-v4-flash 记录
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["model"], "deepseek-v4-flash")

    def test_no_source_field(self):
        """不包含 _source 字段。"""
        svc = self._create_service()
        records = svc._fetch_unified_records("week")
        for r in records:
            self.assertNotIn("_source", r)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest test/test_stats_service.py::TestFetchUnifiedRecords -q
```
Expected: 7 failures, `StatsService` has no attribute `_fetch_unified_records`

- [ ] **Step 3: Implement `_fetch_unified_records()`**

在 `stats_service.py` 的 `StatsService` 类中，`_get_dao()` 方法之前新增：

```python
    # ─── 统一数据查询 ───

    def _fetch_unified_records(
        self,
        period: str,
        model: str | None = None,
        request_type: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list | tuple[list, int]:
        """统一原始数据查询 — 所有统计视图的唯一数据源。

        依次从三源拉取原始行，转换为统一格式，逐条计算 4 项成本，
        合并后按 request_ts DESC 排序。

        Args:
            period: "day"/"week"/"month"
            model: 可选，内部完成规范化后对各源分别匹配
            request_type: 可选，过滤请求类型
            limit: 指定时启用分页，返回 (records, total)
            offset: 分页偏移

        Returns:
            无分页: [record, ...]
            有分页: ([record, ...], total_count)
        """
        # 1. 模型名规范化
        normalized_model = _SessionDao._normalize_model_name(model) if model else None

        # 2. 查询三源
        records = []
        try:
            records.extend(self._get_dao().query_raw(period, normalized_model, request_type))
        except Exception:
            pass
        try:
            records.extend(self._get_session_dao().query_raw(period, normalized_model))
        except Exception:
            pass
        opencode_dao = self._get_opencode_dao()
        if opencode_dao:
            try:
                records.extend(opencode_dao.query_raw(period, normalized_model))
            except Exception:
                pass

        # 3. 逐条计算 4 项成本
        calculator = self._get_calculator()
        for r in records:
            breakdown = calculator.calculate_breakdown(
                model=r["model"],
                input_tokens=r["input_tokens"],
                output_tokens=r["output_tokens"],
                cache_read_tokens=r["cache_read_tokens"],
                cache_write_tokens=r["cache_write_tokens"],
            )
            r["input_cost_cny"] = breakdown["input_cost_cny"]
            r["output_cost_cny"] = breakdown["output_cost_cny"]
            r["cache_read_cost_cny"] = breakdown["cache_read_cost_cny"]
            r["cache_write_cost_cny"] = breakdown["cache_write_cost_cny"]

        # 4. 按 request_ts DESC 排序
        records.sort(key=lambda r: r["request_ts"], reverse=True)

        # 5. 分页或全量返回
        if limit is not None:
            total = len(records)
            return (records[offset:offset + limit], total)
        return records
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest test/test_stats_service.py::TestFetchUnifiedRecords -v
```
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add stats_service.py test/test_stats_service.py
git commit -m "feat: StatsService 新增 _fetch_unified_records() 统一数据查询方法"
```

---

### Task 6: 重写 `fetch_*` 方法为内存聚合 — TDD

**Files:**
- Modify: `stats_service.py:1294-1719` (rewrite all fetch_* methods)
- Modify: `test/test_stats_service.py` (add cross-consistency test)

- [ ] **Step 1: Write cross-consistency test**

在 `test/test_stats_service.py` 中新增：

```python
class TestCrossViewConsistency(unittest.TestCase):
    """所有视图的数据一致性验证 — 同源同结果。"""

    def setUp(self):
        import json, time
        self.tmpdir = tempfile.mkdtemp()
        self.data_db = Path(self.tmpdir) / "access_log.db"
        self.state_db = Path(self.tmpdir) / "state.db"
        self.opencode_db = Path(self.tmpdir) / "opencode.db"

        conn = sqlite3.connect(str(self.data_db))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""CREATE TABLE token_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT, request_type TEXT,
            model TEXT, target_model TEXT, request_ts TEXT, duration_ms INTEGER,
            input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
            cached_read_tokens INTEGER DEFAULT 0, cached_write_tokens INTEGER DEFAULT 0,
            upstream_id INTEGER, status TEXT DEFAULT 'completed', created_at TEXT)""")
        conn.execute("""CREATE TABLE upstreams (
            id TEXT PRIMARY KEY, base_url TEXT, name TEXT, is_active INTEGER, format TEXT)""")
        conn.execute("""CREATE TABLE target_models (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, upstream_id TEXT)""")
        conn.execute("""CREATE TABLE model_pricing (
            model_id TEXT PRIMARY KEY, display_name TEXT,
            input_cost_per_million REAL DEFAULT 0, output_cost_per_million REAL DEFAULT 0,
            cache_read_cost_per_million REAL DEFAULT 0, cache_creation_cost_per_million REAL DEFAULT 0,
            currency TEXT DEFAULT 'RMB', multiplier REAL DEFAULT 1.0,
            created_at TEXT, updated_at TEXT)""")

        conn.execute("INSERT INTO upstreams (id, name, is_active, format) "
                     "VALUES ('up-a', 'Upstream A', 1, 'chat_completions')")
        conn.execute("INSERT INTO target_models (name, upstream_id) VALUES ('model-a', 'up-a')")
        conn.execute("INSERT INTO target_models (name, upstream_id) VALUES ('model-b', 'up-a')")
        # 定价
        conn.execute("INSERT INTO model_pricing (model_id, input_cost_per_million, "
                     "output_cost_per_million, cache_read_cost_per_million, cache_creation_cost_per_million, "
                     "currency) VALUES ('model-a', 1.0, 2.0, 0.5, 0.25, 'RMB')")
        conn.execute("INSERT INTO model_pricing (model_id, input_cost_per_million, "
                     "output_cost_per_million, cache_read_cost_per_million, cache_creation_cost_per_million, "
                     "currency) VALUES ('model-b', 3.0, 4.0, 1.0, 0.5, 'RMB')")

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # model-a: 2 条 proxy 记录
        conn.execute("INSERT INTO token_stats (request_id, request_type, model, target_model, "
                     "request_ts, input_tokens, output_tokens, cached_read_tokens, cached_write_tokens, "
                     "upstream_id, status, created_at) VALUES "
                     "('req-1', 'responses', 'model-a', 'model-a', ?, 100, 50, 10, 5, 'up-a', 'completed', ?)",
                     (now, now))
        conn.execute("INSERT INTO token_stats (request_id, request_type, model, target_model, "
                     "request_ts, input_tokens, output_tokens, cached_read_tokens, cached_write_tokens, "
                     "upstream_id, status, created_at) VALUES "
                     "('req-2', 'responses', 'model-b', 'model-b', ?, 200, 100, 20, 10, 'up-a', 'completed', ?)",
                     (now, now))
        conn.commit()
        conn.close()

        sconn = sqlite3.connect(str(self.state_db))
        sconn.execute("""CREATE TABLE sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, model TEXT,
            started_at REAL, input_tokens INTEGER, output_tokens INTEGER,
            cache_read_tokens INTEGER DEFAULT 0, cache_write_tokens INTEGER DEFAULT 0,
            message_count INTEGER DEFAULT 0)""")
        sconn.execute("INSERT INTO sessions (model, started_at, input_tokens, output_tokens, "
                      "cache_read_tokens, cache_write_tokens) VALUES ('model-a', ?, 300, 150, 30, 15)",
                      (time.time(),))
        sconn.commit()
        sconn.close()

        oconn = sqlite3.connect(str(self.opencode_db))
        oconn.execute("CREATE TABLE session (id TEXT PRIMARY KEY, model TEXT, time_created INTEGER)")
        oconn.execute("CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, "
                      "time_created INTEGER, data TEXT)")
        now_ms = int(time.time() * 1000)
        msg_data = json.dumps({
            "role": "assistant", "modelID": "model-a",
            "tokens": {"input": 50, "output": 25, "reasoning": 0, "cache": {"read": 5, "write": 2}},
            "time": {"created": now_ms - 3600000, "completed": now_ms - 3500000},
        })
        oconn.execute("INSERT INTO message (id, session_id, time_created, data) VALUES (?, ?, ?, ?)",
                      ("msg-1", "s-1", now_ms - 3600000, msg_data))
        oconn.commit()
        oconn.close()

    def tearDown(self):
        import os
        for p in [self.data_db, self.state_db, self.opencode_db]:
            if p.exists():
                os.remove(str(p))
                for s in ["-wal", "-shm"]:
                    wp = Path(str(p) + s)
                    if wp.exists():
                        os.remove(str(wp))
        os.rmdir(self.tmpdir)

    def _create_service(self):
        from stats_service import StatsService
        return StatsService(
            data_db_path=str(self.data_db),
            state_db_path=str(self.state_db),
            opencode_db_path=str(self.opencode_db),
        )

    def test_summary_matches_by_model_sum(self):
        """fetch_summary 的 token 总数等于 fetch_by_model 各行求和。"""
        svc = self._create_service()
        summary = svc.fetch_summary("week")
        by_model = svc.fetch_by_model("week")

        model_input_sum = sum(m["input_tokens"] for m in by_model)
        model_output_sum = sum(m["output_tokens"] for m in by_model)
        model_cache_read_sum = sum(m["cache_read_tokens"] for m in by_model)
        model_cache_write_sum = sum(m["cache_write_tokens"] for m in by_model)
        model_total_sum = sum(m["total_tokens"] for m in by_model)
        model_cost_sum = sum(m["estimated_cost_cny"] for m in by_model)

        self.assertEqual(summary["input_tokens"], model_input_sum)
        self.assertEqual(summary["output_tokens"], model_output_sum)
        self.assertEqual(summary["cache_read_tokens"], model_cache_read_sum)
        self.assertEqual(summary["cache_write_tokens"], model_cache_write_sum)
        self.assertAlmostEqual(summary["estimated_cost_cny"], model_cost_sum, places=6)

    def test_summary_matches_by_upstream_sum(self):
        """fetch_summary 的 token 总数等于 fetch_by_upstream 各行求和。"""
        svc = self._create_service()
        summary = svc.fetch_summary("week")
        by_up = svc.fetch_by_upstream("week")["upstreams"]

        up_input_sum = sum(u["input_tokens"] for u in by_up)
        up_output_sum = sum(u["output_tokens"] for u in by_up)
        up_cost_sum = sum(u["estimated_cost_cny"] for u in by_up)

        self.assertEqual(summary["input_tokens"], up_input_sum)
        self.assertEqual(summary["output_tokens"], up_output_sum)
        self.assertAlmostEqual(summary["estimated_cost_cny"], up_cost_sum, places=6)

    def test_fetch_requests_total_matches_all_records(self):
        """fetch_requests 的 total 等于全量记录数。"""
        svc = self._create_service()
        result = svc.fetch_requests("week", limit=10)
        all_records = svc._fetch_unified_records("week")
        self.assertEqual(result["total"], len(all_records))

    def test_virtual_upstream_names(self):
        """虚拟上游 [Hermes]/[OpenCode] 展示名正确。"""
        svc = self._create_service()
        result = svc.fetch_by_upstream("week")
        name_map = {u["upstream_id"]: u["upstream_name"] for u in result["upstreams"]}

        self.assertEqual(name_map.get("hermes"), "[Hermes]")
        self.assertEqual(name_map.get("opencode"), "[OpenCode]")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest test/test_stats_service.py::TestCrossViewConsistency -q
```
Expected: failures — `fetch_summary`/`fetch_by_model`/`fetch_by_upstream` still using old code paths, results won't match

- [ ] **Step 3: Rewrite all `fetch_*` methods**

用以下代码替换 `stats_service.py` 中 `StatsService` 类的所有 `fetch_*` 方法（从 line 1294 到 line 1719）：

```python
    # ─── Provider 接口 ───

    def fetch_summary(self, period: str) -> dict:
        """获取汇总统计数据，合并三源。"""
        records = self._fetch_unified_records(period)
        if not records:
            return {
                "period": period, "request_count": 0, "input_tokens": 0,
                "output_tokens": 0, "cache_read_tokens": 0,
                "cache_write_tokens": 0, "total_tokens": 0,
                "estimated_cost_cny": 0.0, "avg_duration_ms": 0,
            }
        total_input = sum(r["input_tokens"] for r in records)
        total_output = sum(r["output_tokens"] for r in records)
        total_cache_read = sum(r["cache_read_tokens"] for r in records)
        total_cache_write = sum(r["cache_write_tokens"] for r in records)
        total_cost = sum(
            r["input_cost_cny"] + r["output_cost_cny"]
            + r["cache_read_cost_cny"] + r["cache_write_cost_cny"]
            for r in records
        )
        durations = [r["duration_ms"] for r in records if r["duration_ms"]]
        avg_duration = round(sum(durations) / len(durations), 2) if durations else 0
        return {
            "period": period,
            "request_count": len(records),
            "input_tokens": total_input,
            "output_tokens": total_output,
            "cache_read_tokens": total_cache_read,
            "cache_write_tokens": total_cache_write,
            "total_tokens": total_input + total_output + total_cache_read + total_cache_write,
            "estimated_cost_cny": round(total_cost, 6),
            "avg_duration_ms": avg_duration,
        }

    def fetch_by_model(self, period: str) -> list:
        """按模型维度获取统计数据。"""
        records = self._fetch_unified_records(period)
        calculator = self._get_calculator()
        grouped: dict = {}
        for r in records:
            model = r["model"]
            if model not in grouped:
                grouped[model] = {
                    "model": model, "request_count": 0,
                    "input_tokens": 0, "output_tokens": 0,
                    "cache_read_tokens": 0, "cache_write_tokens": 0,
                    "total_tokens": 0, "estimated_cost_cny": 0.0,
                    "avg_duration_ms": 0,
                }
                grouped[model]["display_name"] = calculator.get_display_name(model)
            m = grouped[model]
            m["request_count"] += 1
            m["input_tokens"] += r["input_tokens"]
            m["output_tokens"] += r["output_tokens"]
            m["cache_read_tokens"] += r["cache_read_tokens"]
            m["cache_write_tokens"] += r["cache_write_tokens"]
            m["total_tokens"] += (r["input_tokens"] + r["output_tokens"]
                                 + r["cache_read_tokens"] + r["cache_write_tokens"])
            m["estimated_cost_cny"] += (r["input_cost_cny"] + r["output_cost_cny"]
                                       + r["cache_read_cost_cny"] + r["cache_write_cost_cny"])

        result = []
        for m in grouped.values():
            m["estimated_cost_cny"] = round(m["estimated_cost_cny"], 6)
            result.append(m)
        result.sort(key=lambda x: x["total_tokens"], reverse=True)
        return result

    def fetch_by_upstream(self, period: str) -> dict:
        """按上游维度获取统计数据。"""
        records = self._fetch_unified_records(period)
        resolver = self._upstream_resolver
        grouped: dict = {}

        for r in records:
            uid = r["upstream_id"]
            if uid not in grouped:
                grouped[uid] = {
                    "upstream_id": uid,
                    "request_count": 0, "input_tokens": 0, "output_tokens": 0,
                    "cache_read_tokens": 0, "cache_write_tokens": 0,
                    "total_tokens": 0, "total_cost": 0.0,
                }
            g = grouped[uid]
            g["request_count"] += 1
            g["input_tokens"] += r["input_tokens"]
            g["output_tokens"] += r["output_tokens"]
            g["cache_read_tokens"] += r["cache_read_tokens"]
            g["cache_write_tokens"] += r["cache_write_tokens"]
            g["total_tokens"] += (r["input_tokens"] + r["output_tokens"]
                                 + r["cache_read_tokens"] + r["cache_write_tokens"])
            g["total_cost"] += (r["input_cost_cny"] + r["output_cost_cny"]
                               + r["cache_read_cost_cny"] + r["cache_write_cost_cny"])

        result = []
        for uid, agg in grouped.items():
            # upstream_name 解析优先级
            if uid == "hermes":
                upstream_name = "[Hermes]"
                base_url = None
            elif uid == "opencode":
                upstream_name = "[OpenCode]"
                base_url = None
            else:
                info = resolver.resolve_by_id(uid)
                upstream_name = info["upstream_name"]
                base_url = info.get("base_url")
            result.append({
                "upstream_id": uid,
                "upstream_name": upstream_name,
                "base_url": base_url,
                "request_count": agg["request_count"],
                "input_tokens": agg["input_tokens"],
                "output_tokens": agg["output_tokens"],
                "cache_read_tokens": agg["cache_read_tokens"],
                "cache_write_tokens": agg["cache_write_tokens"],
                "total_tokens": agg["total_tokens"],
                "estimated_cost_cny": round(agg["total_cost"], 6),
            })

        result.sort(key=lambda x: x["estimated_cost_cny"], reverse=True)
        return {"upstreams": result}

    def fetch_trend(self, period: str) -> list:
        """获取时间趋势数据，逐桶聚合。"""
        records = self._fetch_unified_records(period)

        # 时间分桶规则
        if period in ("day", "24h"):
            def bucket_key(ts):
                return ts[:13] + ":00"
        else:
            def bucket_key(ts):
                return ts[:10]

        buckets: dict = {}
        for r in records:
            key = bucket_key(r["request_ts"])
            if key not in buckets:
                buckets[key] = {"date": key, "request_count": 0,
                                "input_tokens": 0, "output_tokens": 0,
                                "cache_read_tokens": 0, "cache_write_tokens": 0,
                                "estimated_cost_cny": 0.0}
            b = buckets[key]
            b["request_count"] += 1
            b["input_tokens"] += r["input_tokens"]
            b["output_tokens"] += r["output_tokens"]
            b["cache_read_tokens"] += r["cache_read_tokens"]
            b["cache_write_tokens"] += r["cache_write_tokens"]
            b["estimated_cost_cny"] += (r["input_cost_cny"] + r["output_cost_cny"]
                                        + r["cache_read_cost_cny"] + r["cache_write_cost_cny"])

        result = []
        for b in buckets.values():
            b["total_tokens"] = (b["input_tokens"] + b["output_tokens"]
                                 + b["cache_read_tokens"] + b["cache_write_tokens"])
            b["estimated_cost_cny"] = round(b["estimated_cost_cny"], 6)
            result.append(b)
        result.sort(key=lambda x: x["date"])
        return result

    def fetch_requests(
        self,
        period: str,
        model: str | None = None,
        request_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """获取请求详情列表（分页）。"""
        records, total = self._fetch_unified_records(
            period=period, model=model, request_type=request_type,
            limit=limit, offset=offset,
        )
        return {
            "requests": records,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def fetch_all_summaries(self) -> dict:
        """获取 day/week/month 三个周期的汇总数据。"""
        result = {}
        for period in ("day", "week", "month"):
            result[period] = self.fetch_summary(period)
        return result
```

删除以下方法（旧实现）：
- `fetch_by_model_requests()` — 功能被 `fetch_requests(model=m)` 覆盖

- [ ] **Step 4: Run cross-consistency tests**

```bash
python3 -m pytest test/test_stats_service.py::TestCrossViewConsistency -v
```
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add stats_service.py test/test_stats_service.py
git commit -m "feat: 重写所有 fetch_* 方法为 _fetch_unified_records() 内存聚合"
```

---

### Task 7: 删除死代码

**Files:**
- Modify: `stats_service.py` (delete dead classes/methods)

- [ ] **Step 1: Identify and delete dead code**

删除以下内容（保留不删的标注）：

**`_TokenStatsDao` 类中删除：**
- `query_token_stats()` (lines 73-132)
- `aggregate_by_model()` (lines 134-181)
- `aggregate_by_upstream()` (lines 183-238)
- `aggregate_trend()` (lines 240-297)
- `aggregate_summary()` (lines 299-341)

**`_SessionDao` 类中删除：**
- `query_sessions()` (lines 653-694)
- `query_sessions_paged()` (lines 695-750)
- `aggregate_by_model()` (lines 752-814)
- `aggregate_summary()` (lines 816-853)
- `aggregate_trend()` (lines 855-897)

**`_OpenCodeDao` 类中删除：**
- `aggregate_by_model()` (lines 957-996)
- `aggregate_summary()` (lines 997-1035)
- `aggregate_trend()` (lines 1037-1080)
- `query_messages_paged()` (lines 1082-1142)

**删除整个 `_Merger` 类** (lines 1145-1246)

**`StatsService` 类中删除：**
- `_load_upstream_map()` (lines 1722-1726)
- `_resolve_upstream()` (lines 1727-1729)
- `fetch_by_model_requests()` (lines 1616-1719)

删除 `import time`（如果只被 `_UpstreamResolver` 的 TTL 逻辑使用则保留；若仅被已删除方法使用则删）。

- [ ] **Step 2: Run existing tests to check nothing broke**

```bash
python3 -m pytest test/test_stats_service.py -q 2>&1 | tail -5
```

Expected: 部分测试会失败，因为 `TestMerger`、`TestFetchRequestsMerged` 等旧测试引用了已删除的类和方法。这是预期行为。

- [ ] **Step 3: Delete old test classes**

在 `test/test_stats_service.py` 中删除以下测试类：
- `TestMerger`（及其所有测试方法）
- `TestFetchRequestsMerged`
- `TestFetchByModelRequestsMerged`
- `TestFetchByUpstreamMerged`
- `TestFetchByModelRequests`（如果只测 `fetch_by_model_requests`）

保留 `TestTokenStatsDao`、`TestSessionDao`、`TestOpenCodeDao`、`TestStatsService`，以及新增的 `TestCostCalculatorBreakdown`、`TestFetchUnifiedRecords`、`TestCrossViewConsistency`。

- [ ] **Step 4: Run remaining tests**

```bash
python3 -m pytest test/test_stats_service.py -q
```

Expected: 所有保留的测试通过（含 4 个新测试类 + 保留的旧测试）。

- [ ] **Step 5: Commit**

```bash
git add stats_service.py test/test_stats_service.py
git commit -m "refactor: 删除死代码 — _Merger、aggregate_*、query_*_paged、_load_upstream_map、fetch_by_model_requests"
```

---

### Task 8: 更新 `token_api.py`

**Files:**
- Modify: `server/token_api.py:93-116`

- [ ] **Step 1: Update the by_model requests endpoint**

将 `server/token_api.py` 中 `handle_get()` 的这段代码（line 93-116）：

```python
    m = re.match(r"/api/token_stats/by_model/([^/]+)/requests$", path)
    if m:
        if not _check_stats_service(handler):
            return True
        model = unquote(m.group(1))
        period = qs.get("period", ["week"])[0]
        if period not in ("day", "week", "month"):
            json_response(handler, {"error": "Invalid period"}, 400)
            return True
        try:
            limit = int(qs.get("limit", ["50"])[0])
            offset = int(qs.get("offset", ["0"])[0])
        except (ValueError, TypeError):
            json_response(handler, {"error": "Invalid limit/offset"}, 400)
            return True
        if limit > 200:
            json_response(handler, {"error": "Limit exceeds maximum (200)"}, 400)
            return True
        result = handler.stats_service.fetch_by_model_requests(
            model=model, period=period, limit=limit, offset=offset
        )
        json_response(handler, result)
        return True
```

替换为：

```python
    m = re.match(r"/api/token_stats/by_model/([^/]+)/requests$", path)
    if m:
        if not _check_stats_service(handler):
            return True
        model = unquote(m.group(1))
        period = qs.get("period", ["week"])[0]
        if period not in ("day", "week", "month"):
            json_response(handler, {"error": "Invalid period"}, 400)
            return True
        try:
            limit = int(qs.get("limit", ["50"])[0])
            offset = int(qs.get("offset", ["0"])[0])
        except (ValueError, TypeError):
            json_response(handler, {"error": "Invalid limit/offset"}, 400)
            return True
        if limit > 200:
            json_response(handler, {"error": "Limit exceeds maximum (200)"}, 400)
            return True
        result = handler.stats_service.fetch_requests(
            period=period, model=model, limit=limit, offset=offset
        )
        result["model"] = model  # 保持与旧 fetch_by_model_requests 返回格式兼容
        json_response(handler, result)
        return True
```

- [ ] **Step 2: Verify no syntax errors**

```bash
python3 -c "from server.token_api import handle_get; print('OK')"
```
Expected: `OK` (no import errors)

- [ ] **Step 3: Commit**

```bash
git add server/token_api.py
git commit -m "refactor: token_api by_model 端点改用 fetch_requests + 补 model 字段"
```

---

### Task 9: 更新前端 `tokens.js`

**Files:**
- Modify: `static/js/pages/tokens.js:528` (展开行 isSession)
- Modify: `static/js/pages/tokens.js:700` (请求日志 isSession)
- Modify: `static/js/pages/tokens.js:537-542` (成本展示)

- [ ] **Step 1: 第一处改动 — 展开行 isSession 判断 (line 528)**

```javascript
// 旧代码 (line 528):
const isSession = r.type === 'session' || r.request_type === 'session';

// 改为:
const isSession = r.upstream_id === 'hermes' || r.upstream_id === 'opencode';
```

- [ ] **Step 2: 第二处改动 — 请求日志行 isSession 判断 (line 700)**

```javascript
// 旧代码 (line 700):
const isSession = r.type === 'session' || r.request_type === 'session';

// 改为:
const isSession = r.upstream_id === 'hermes' || r.upstream_id === 'opencode';
```

- [ ] **Step 3: 第三处改动 — 成本展示从单值改为 4 项和 (line 537 附近)**

在展开行的 token 单元格渲染中，找到 `r.estimated_cost_cny` 并替换：

```javascript
// 旧:
const costStr = (r.estimated_cost_cny || 0).toFixed(6);

// 改为:
const costStr = ((r.input_cost_cny || 0) + (r.output_cost_cny || 0) + (r.cache_read_cost_cny || 0) + (r.cache_write_cost_cny || 0)).toFixed(6);
```

- [ ] **Step 4: 重启服务验证前端**

```bash
./server.sh restart
```
Then manually verify: open the token stats page, switch through all 3 sub-tabs, verify data loads correctly.

- [ ] **Step 5: Commit**

```bash
git add static/js/pages/tokens.js
git commit -m "fix: 前端 isSession 判断改用 upstream_id，成本改用 4 项求和"
```

---

### Task 10: 全量测试 + 最终验证

**Files:** 无新修改

- [ ] **Step 1: Run full stats_service test suite**

```bash
python3 -m pytest test/test_stats_service.py -v
```
Expected: ~50+ tests pass (new + remaining old tests), 0 failures.

- [ ] **Step 2: Run full project test suite**

```bash
python3 -m pytest test/ -q
```
Expected: 531+ tests pass (stats_service tests may change count due to deletions), 0 failures.

- [ ] **Step 3: Start services and smoke test**

```bash
./server.sh restart
./server.sh status
```
Then smoke test API endpoints:
```bash
curl -s http://localhost:18742/api/token_stats?period=week | python3 -m json.tool | head -20
curl -s http://localhost:18742/api/token_stats/by_model?period=week | python3 -m json.tool | head -20
curl -s http://localhost:18742/api/token_stats/by_upstream?period=week | python3 -m json.tool | head -20
curl -s http://localhost:18742/api/token_stats/trend?period=week | python3 -m json.tool | head -20
curl -s "http://localhost:18742/api/token_stats/requests?period=week&limit=5" | python3 -m json.tool | head -20
```

All should return valid JSON without errors.

- [ ] **Step 4: Final commit (if any cleanup needed)**

```bash
git status
# Only if there are uncommitted changes
```

---
