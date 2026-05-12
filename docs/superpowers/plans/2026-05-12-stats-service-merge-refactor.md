# StatsService 双源合并重构 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 server.py 中所有 token 统计函数迁移到 stats_service.py，通过 _Merger 层统一双数据源合并和字段命名

**Architecture:** server.py 仅做参数校验 + 路由转发 → StatsService 编排层 → _Merger 合并双源求和 + 字段重命名 → _TokenStatsDao / _SessionDao 数据层

**Tech Stack:** Python 标准库 (sqlite3, unittest)，pytest 运行器

---

### Task 1: 实现 _Merger 类

**Files:**
- Create: `stats_service.py` (在 `_SessionDao` 类后面、`StatsService` 类前面插入 `_Merger` 类)
- Test: `test/test_stats_service.py`

_Merger 是整个重构的基础——字段重命名和双源求和都在这里。

- [ ] **Step 1: 写 _Merger 的失败测试**

在 `test/test_stats_service.py` 末尾添加新测试类：

```python
class TestMerger(unittest.TestCase):
    def test_merge_summary_sums_fields(self):
        proxy = {
            "period": "week",
            "request_count": 10,
            "input_tokens": 100,
            "output_tokens": 50,
            "cached_read_tokens": 200,
            "cached_write_tokens": 30,
            "total_tokens": 380,
            "avg_duration_ms": 150.0,
        }
        session = {
            "period": "week",
            "request_count": 5,
            "input_tokens": 80,
            "output_tokens": 40,
            "cached_read_tokens": 160,
            "cached_write_tokens": 20,
            "total_tokens": 300,
            "avg_duration_ms": 0,
        }
        result = _Merger.merge_summary(proxy, session)
        self.assertEqual(result["request_count"], 15)
        self.assertEqual(result["input_tokens"], 180)
        self.assertEqual(result["output_tokens"], 90)
        self.assertEqual(result["cache_read_tokens"], 360)
        self.assertEqual(result["cache_write_tokens"], 50)
        self.assertEqual(result["total_tokens"], 680)
        self.assertEqual(result["avg_duration_ms"], 150.0)

    def test_merge_summary_renames_cached_to_cache(self):
        proxy = {"period": "day", "request_count": 1, "input_tokens": 10,
                 "output_tokens": 5, "cached_read_tokens": 20,
                 "cached_write_tokens": 3, "total_tokens": 38,
                 "avg_duration_ms": 0}
        session = {"period": "day", "request_count": 0, "input_tokens": 0,
                   "output_tokens": 0, "cached_read_tokens": 0,
                   "cached_write_tokens": 0, "total_tokens": 0,
                   "avg_duration_ms": 0}
        result = _Merger.merge_summary(proxy, session)
        self.assertIn("cache_read_tokens", result)
        self.assertNotIn("cached_read_tokens", result)
        self.assertIn("cache_write_tokens", result)
        self.assertNotIn("cached_write_tokens", result)

    def test_merge_summary_empty_session(self):
        proxy = {"period": "week", "request_count": 10, "input_tokens": 100,
                 "output_tokens": 50, "cached_read_tokens": 200,
                 "cached_write_tokens": 30, "total_tokens": 380,
                 "avg_duration_ms": 100.0}
        session = {}
        result = _Merger.merge_summary(proxy, session)
        self.assertEqual(result["request_count"], 10)
        self.assertEqual(result["cache_read_tokens"], 200)

    def test_merge_model_lists_sums_same_model(self):
        proxy = [
            {"model": "claude-3.5-sonnet", "request_count": 5,
             "input_tokens": 100, "output_tokens": 50,
             "cached_read_tokens": 20, "cached_write_tokens": 10,
             "total_tokens": 180, "avg_duration_ms": 200.0},
        ]
        session = [
            {"model": "claude-3.5-sonnet", "request_count": 3,
             "input_tokens": 60, "output_tokens": 30,
             "cached_read_tokens": 15, "cached_write_tokens": 5,
             "total_tokens": 110},
        ]
        result = _Merger.merge_model_lists(proxy, session)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["model"], "claude-3.5-sonnet")
        self.assertEqual(result[0]["request_count"], 8)
        self.assertEqual(result[0]["input_tokens"], 160)
        self.assertEqual(result[0]["cache_read_tokens"], 35)
        self.assertEqual(result[0]["avg_duration_ms"], 200.0)

    def test_merge_model_lists_different_models(self):
        proxy = [
            {"model": "model-a", "request_count": 2, "input_tokens": 10,
             "output_tokens": 5, "cached_read_tokens": 0,
             "cached_write_tokens": 0, "total_tokens": 15,
             "avg_duration_ms": 100.0},
        ]
        session = [
            {"model": "model-b", "request_count": 3, "input_tokens": 20,
             "output_tokens": 10, "cached_read_tokens": 0,
             "cached_write_tokens": 0, "total_tokens": 30},
        ]
        result = _Merger.merge_model_lists(proxy, session)
        self.assertEqual(len(result), 2)

    def test_merge_model_lists_normalizes_names(self):
        proxy = [
            {"model": "claude-3.5-sonnet", "request_count": 5,
             "input_tokens": 100, "output_tokens": 50,
             "cached_read_tokens": 20, "cached_write_tokens": 10,
             "total_tokens": 180, "avg_duration_ms": 0},
        ]
        session = [
            {"model": "claude-3.5-sonnet[1m]", "request_count": 3,
             "input_tokens": 60, "output_tokens": 30,
             "cached_read_tokens": 15, "cached_write_tokens": 5,
             "total_tokens": 110},
        ]
        result = _Merger.merge_model_lists(proxy, session)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["request_count"], 8)

    def test_merge_trend_lists_sums_same_time(self):
        proxy = [
            {"time": "2026-05-11", "request_count": 5,
             "input_tokens": 100, "output_tokens": 50,
             "cached_read_tokens": 20, "cached_write_tokens": 10,
             "total_tokens": 180},
        ]
        session = [
            {"time": "2026-05-11", "request_count": 3,
             "input_tokens": 60, "output_tokens": 30,
             "cached_read_tokens": 15, "cached_write_tokens": 5,
             "total_tokens": 110},
        ]
        result = _Merger.merge_trend_lists(proxy, session)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["request_count"], 8)
        self.assertEqual(result[0]["input_tokens"], 160)
        self.assertEqual(result[0]["cache_read_tokens"], 35)

    def test_merge_trend_lists_empty_session(self):
        proxy = [
            {"time": "2026-05-11", "request_count": 5,
             "input_tokens": 100, "output_tokens": 50,
             "cached_read_tokens": 20, "cached_write_tokens": 10,
             "total_tokens": 180},
        ]
        result = _Merger.merge_trend_lists(proxy, [])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["cache_read_tokens"], 20)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest test/test_stats_service.py::TestMerger -v`
Expected: FAIL — `_Merger` 未定义

- [ ] **Step 3: 实现 _Merger 类**

在 `stats_service.py` 的 `_SessionDao` 类（结束于 L770）之后、`StatsService` 类（开始于 L773）之前插入：

```python
class _Merger:
    """双数据源合并：按规范化模型名求和，字段名统一为 cache_*"""

    _RENAME_MAP = {
        "cached_read_tokens": "cache_read_tokens",
        "cached_write_tokens": "cache_write_tokens",
    }

    @classmethod
    def _rename(cls, d: dict) -> dict:
        """将 cached_* 字段重命名为 cache_*"""
        result = {}
        for k, v in d.items():
            result[cls._RENAME_MAP.get(k, k)] = v
        return result

    @staticmethod
    def merge_summary(proxy_summary: dict, session_summary: dict) -> dict:
        """合并两个汇总 dict，各数值字段求和，字段重命名为 cache_*"""
        p = _Merger._rename(proxy_summary) if proxy_summary else {}
        s = _Merger._rename(session_summary) if session_summary else {}
        return {
            "period": p.get("period", s.get("period", "week")),
            "request_count": p.get("request_count", 0) + s.get("request_count", 0),
            "input_tokens": p.get("input_tokens", 0) + s.get("input_tokens", 0),
            "output_tokens": p.get("output_tokens", 0) + s.get("output_tokens", 0),
            "cache_read_tokens": p.get("cache_read_tokens", 0) + s.get("cache_read_tokens", 0),
            "cache_write_tokens": p.get("cache_write_tokens", 0) + s.get("cache_write_tokens", 0),
            "total_tokens": (p.get("input_tokens", 0) + s.get("input_tokens", 0)
                             + p.get("output_tokens", 0) + s.get("output_tokens", 0)
                             + p.get("cache_read_tokens", 0) + s.get("cache_read_tokens", 0)
                             + p.get("cache_write_tokens", 0) + s.get("cache_write_tokens", 0)),
            "avg_duration_ms": p.get("avg_duration_ms", 0),
        }

    @staticmethod
    def merge_model_lists(proxy_models: list, session_models: list) -> list:
        """合并两个 by_model 列表，同名模型 token 求和，字段重命名为 cache_*"""
        merged: dict = {}
        for item in proxy_models:
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

        for item in session_models:
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

        for m in merged.values():
            m["total_tokens"] = (m["input_tokens"] + m["output_tokens"]
                                 + m["cache_read_tokens"] + m["cache_write_tokens"])
        return list(merged.values())

    @staticmethod
    def merge_trend_lists(proxy_trend: list, session_trend: list) -> list:
        """合并两个趋势列表，同时间点各指标求和，字段重命名为 cache_*"""
        merged: dict = {}
        for item in proxy_trend:
            r = _Merger._rename(item)
            key = r.get("time", r.get("date", ""))
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

        for item in session_trend:
            r = _Merger._rename(item)
            key = r.get("time", r.get("date", ""))
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

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest test/test_stats_service.py::TestMerger -v`
Expected: 全部 PASS

- [ ] **Step 5: 确认现有测试不被破坏**

Run: `python3 -m pytest test/test_stats_service.py -q`
Expected: 与改动前相同的通过数

- [ ] **Step 6: 提交**

```bash
git add stats_service.py test/test_stats_service.py
git commit -m "feat: 实现 _Merger 双源合并类 + 字段重命名 + 测试"
```

---

### Task 2: 给 _SessionDao 补充 aggregate_summary 和 aggregate_trend 方法

**Files:**
- Modify: `stats_service.py` — _SessionDao 类
- Test: `test/test_stats_service.py` — TestSessionDao 类

- [ ] **Step 1: 写 _SessionDao.aggregate_summary 和 aggregate_trend 的失败测试**

在 `test/test_stats_service.py` 的 `TestSessionDao` 类中添加：

```python
def test_aggregate_summary_basic(self):
    self._insert_session(model="model-a", input_tokens=100, output_tokens=50,
                         cache_read_tokens=20, cache_write_tokens=10)
    self._insert_session(model="model-b", input_tokens=200, output_tokens=100,
                         cache_read_tokens=40, cache_write_tokens=20)
    dao = self._create_dao()
    result = dao.aggregate_summary("week")
    self.assertEqual(result["request_count"], 2)
    self.assertEqual(result["input_tokens"], 300)
    self.assertEqual(result["output_tokens"], 150)
    self.assertEqual(result["cached_read_tokens"], 60)
    self.assertEqual(result["cached_write_tokens"], 30)

def test_aggregate_summary_db_not_exists(self):
    dao = _SessionDao(Path("/nonexistent/state.db"))
    result = dao.aggregate_summary("week")
    self.assertEqual(result["request_count"], 0)
    self.assertEqual(result["input_tokens"], 0)

def test_aggregate_trend_basic(self):
    self._insert_session(model="model-a", input_tokens=100, output_tokens=50,
                         cache_read_tokens=20, cache_write_tokens=10)
    dao = self._create_dao()
    result = dao.aggregate_trend("week")
    self.assertIsInstance(result, list)
    self.assertGreaterEqual(len(result), 1)
    point = result[0]
    self.assertIn("time", point)
    self.assertIn("input_tokens", point)
    self.assertIn("cached_read_tokens", point)

def test_aggregate_trend_db_not_exists(self):
    dao = _SessionDao(Path("/nonexistent/state.db"))
    result = dao.aggregate_trend("week")
    self.assertEqual(result, [])
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest test/test_stats_service.py::TestSessionDao::test_aggregate_summary_basic -v`
Expected: FAIL — `aggregate_summary` 方法不存在

- [ ] **Step 3: 实现 _SessionDao.aggregate_summary 和 aggregate_trend**

在 `stats_service.py` 的 `_SessionDao` 类中 `aggregate_by_model` 方法之后（L770 前）添加：

```python
def aggregate_summary(self, period: str) -> dict:
    """汇总 sessions 数据，返回与 _TokenStatsDao.aggregate_summary 相同结构的 dict。"""
    conn = self._get_conn()
    if conn is None:
        return {"period": period, "request_count": 0, "input_tokens": 0,
                "output_tokens": 0, "cached_read_tokens": 0,
                "cached_write_tokens": 0, "total_tokens": 0, "avg_duration_ms": 0}
    try:
        time_condition = self._period_to_condition(period)
        row = conn.execute(
            f"""SELECT COUNT(*) as session_count,
                       COALESCE(SUM(input_tokens), 0) as total_input,
                       COALESCE(SUM(output_tokens), 0) as total_output,
                       COALESCE(SUM(cache_read_tokens), 0) as total_cache_read,
                       COALESCE(SUM(cache_write_tokens), 0) as total_cache_write
                FROM sessions
                WHERE {time_condition} AND input_tokens IS NOT NULL""",
        ).fetchone()
        total_input = row["total_input"]
        total_output = row["total_output"]
        total_cache_read = row["total_cache_read"]
        total_cache_write = row["total_cache_write"]
        return {
            "period": period,
            "request_count": row["session_count"] or 0,
            "input_tokens": total_input,
            "output_tokens": total_output,
            "cached_read_tokens": total_cache_read,
            "cached_write_tokens": total_cache_write,
            "total_tokens": total_input + total_output + total_cache_read + total_cache_write,
            "avg_duration_ms": 0,
        }
    except Exception:
        return {"period": period, "request_count": 0, "input_tokens": 0,
                "output_tokens": 0, "cached_read_tokens": 0,
                "cached_write_tokens": 0, "total_tokens": 0, "avg_duration_ms": 0}
    finally:
        conn.close()

def aggregate_trend(self, period: str) -> list:
    """按时间粒度聚合 sessions 数据，返回与 _TokenStatsDao.aggregate_trend 相同结构的 list。"""
    conn = self._get_conn()
    if conn is None:
        return []
    try:
        time_condition = self._period_to_condition(period)
        if period in ("day", "24h"):
            group_expr = "strftime('%Y-%m-%d %H:00', started_at)"
        else:
            group_expr = "date(started_at)"

        rows = conn.execute(
            f"""SELECT {group_expr} as time_bucket,
                       COUNT(*) as session_count,
                       SUM(input_tokens) as total_input,
                       SUM(output_tokens) as total_output,
                       SUM(cache_read_tokens) as total_cache_read,
                       SUM(cache_write_tokens) as total_cache_write
                FROM sessions
                WHERE {time_condition} AND input_tokens IS NOT NULL
                GROUP BY time_bucket
                ORDER BY time_bucket ASC""",
        ).fetchall()

        return [
            {
                "time": row["time_bucket"],
                "request_count": row["session_count"],
                "input_tokens": row["total_input"],
                "output_tokens": row["total_output"],
                "cached_read_tokens": row["total_cache_read"],
                "cached_write_tokens": row["total_cache_write"],
                "total_tokens": (row["total_input"] + row["total_output"]
                                 + row["total_cache_read"] + row["total_cache_write"]),
            }
            for row in rows
        ]
    except Exception:
        return []
    finally:
        conn.close()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest test/test_stats_service.py::TestSessionDao -v`
Expected: 全部 PASS

- [ ] **Step 5: 全量测试确认无回归**

Run: `python3 -m pytest test/test_stats_service.py -q`
Expected: 与改动前相同的通过数

- [ ] **Step 6: 提交**

```bash
git add stats_service.py test/test_stats_service.py
git commit -m "feat: _SessionDao 补充 aggregate_summary 和 aggregate_trend 方法"
```

---

### Task 3: 改造 StatsService 的 fetch_by_model / fetch_trend / fetch_summary

**Files:**
- Modify: `stats_service.py` — StatsService 类的 3 个 fetch 方法
- Test: `test/test_stats_service.py` — 更新现有测试

这 3 个方法当前只查 proxy DAO，需改为查双 DAO + _Merger 合并。同时 fetch_by_model 需要加成本计算。

- [ ] **Step 1: 写 merge 后的 fetch_summary 测试**

在 `test/test_stats_service.py` 添加新测试类：

```python
class TestFetchSummaryMerged(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.access_log_db = Path(self.tmpdir.name) / "access_log.db"
        self.state_db = Path(self.tmpdir.name) / "state.db"
        self.config_db = Path(self.tmpdir.name) / "config.db"
        self.cc_switch_db = Path(self.tmpdir.name) / "cc-switch.db"
        self._create_access_log_db()
        self._create_state_db()

    def _create_access_log_db(self):
        conn = sqlite3.connect(str(self.access_log_db))
        conn.execute("""CREATE TABLE token_stats (
            id INTEGER PRIMARY KEY, request_id TEXT, request_type TEXT,
            model TEXT, target_model TEXT, request_ts TEXT, duration_ms REAL,
            input_tokens INTEGER, output_tokens INTEGER,
            cached_read_tokens INTEGER, cached_write_tokens INTEGER, status TEXT)""")
        conn.execute("INSERT INTO token_stats VALUES (1,'r1','chat','m1','m1',datetime('now'),100,100,50,20,10,'completed')")
        conn.commit()
        conn.close()

    def _create_state_db(self):
        conn = sqlite3.connect(str(self.state_db))
        conn.execute("""CREATE TABLE sessions (
            id INTEGER PRIMARY KEY, model TEXT, started_at TEXT,
            input_tokens INTEGER, output_tokens INTEGER,
            cache_read_tokens INTEGER, cache_write_tokens INTEGER)""")
        conn.execute("INSERT INTO sessions VALUES (1,'m1',datetime('now'),80,40,15,5)")
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _create_service(self):
        return StatsService(
            access_log_db_path=str(self.access_log_db),
            config_db_path=str(self.config_db),
            state_db_path=str(self.state_db),
            cc_switch_db_path=str(self.cc_switch_db),
        )

    def test_fetch_summary_merges_both_sources(self):
        svc = self._create_service()
        result = svc.fetch_summary("week")
        self.assertEqual(result["request_count"], 2)  # 1 proxy + 1 session
        self.assertEqual(result["input_tokens"], 180)  # 100 + 80
        self.assertEqual(result["output_tokens"], 90)   # 50 + 40
        self.assertIn("cache_read_tokens", result)
        self.assertNotIn("cached_read_tokens", result)
        self.assertEqual(result["cache_read_tokens"], 35)  # 20 + 15
        self.assertEqual(result["cache_write_tokens"], 15)  # 10 + 5

    def test_fetch_summary_proxy_only(self):
        # state.db 没有 sessions 数据
        conn = sqlite3.connect(str(self.state_db))
        conn.execute("DELETE FROM sessions")
        conn.commit()
        conn.close()
        svc = self._create_service()
        result = svc.fetch_summary("week")
        self.assertEqual(result["request_count"], 1)
        self.assertEqual(result["input_tokens"], 100)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest test/test_stats_service.py::TestFetchSummaryMerged -v`
Expected: FAIL — fetch_summary 返回的是 proxy 独有的数据，未合并 sessions

- [ ] **Step 3: 改造 StatsService.fetch_summary**

修改 `stats_service.py` 中 `StatsService.fetch_summary`：

```python
def fetch_summary(self, period: str) -> dict:
    """获取汇总统计数据，合并 proxy + sessions 双源。"""
    dao = self._get_dao()
    session_dao = self._get_session_dao()
    proxy = dao.aggregate_summary(period)
    session = session_dao.aggregate_summary(period)
    result = _Merger.merge_summary(proxy, session)
    # 成本计算
    calculator = self._get_calculator()
    cost = calculator.calculate(
        model="", input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
        cache_read_tokens=result["cache_read_tokens"],
        cache_write_tokens=result["cache_write_tokens"],
    )
    result["estimated_cost_usd"] = round(cost, 4)
    return result
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest test/test_stats_service.py::TestFetchSummaryMerged -v`
Expected: PASS

- [ ] **Step 5: 改造 StatsService.fetch_by_model**

修改 `stats_service.py` 中 `StatsService.fetch_by_model`：

```python
def fetch_by_model(self, period: str) -> list:
    """按模型维度获取统计数据，合并 proxy + sessions 双源。"""
    dao = self._get_dao()
    session_dao = self._get_session_dao()
    proxy_models = dao.aggregate_by_model(period)
    session_models = session_dao.aggregate_by_model(period)
    merged = _Merger.merge_model_lists(proxy_models, session_models)
    calculator = self._get_calculator()
    for m in merged:
        m["estimated_cost_usd"] = round(calculator.calculate(
            model=m["model"], input_tokens=m["input_tokens"],
            output_tokens=m["output_tokens"],
            cache_read_tokens=m["cache_read_tokens"],
            cache_write_tokens=m["cache_write_tokens"],
        ), 6)
    merged.sort(key=lambda x: x.get("total_tokens", 0), reverse=True)
    return merged
```

- [ ] **Step 6: 改造 StatsService.fetch_trend**

修改 `stats_service.py` 中 `StatsService.fetch_trend`：

```python
def fetch_trend(self, period: str) -> list:
    """获取时间趋势数据，合并 proxy + sessions 双源。"""
    dao = self._get_dao()
    session_dao = self._get_session_dao()
    proxy_trend = dao.aggregate_trend(period)
    session_trend = session_dao.aggregate_trend(period)
    merged = _Merger.merge_trend_lists(proxy_trend, session_trend)
    merged.sort(key=lambda x: x.get("date", ""))
    return merged
```

- [ ] **Step 7: 添加 fetch_all_summaries 方法**

在 `StatsService` 类中添加：

```python
def fetch_all_summaries(self) -> dict:
    """获取 day/week/month 三个周期的汇总数据。"""
    result = {}
    for period in ("day", "week", "month"):
        result[period] = self.fetch_summary(period)
    return result
```

- [ ] **Step 8: 更新现有测试**

更新 `test/test_stats_service.py` 中 `TestStatsService` 的测试——原来 `test_fetch_summary_basic` 和 `test_fetch_by_model_basic` 只验证 proxy 数据，现在需要同时准备 sessions 数据。在 `_insert_test_data` 之外也需要给 state.db 插入 sessions 数据。

在 `TestStatsService.setUp` 中追加 state.db 的创建和 session 数据插入：

```python
# 创建 state.db 并插入 sessions 数据
self.state_db_path = Path(self.tmpdir) / "state.db"
state_conn = sqlite3.connect(str(self.state_db_path))
state_conn.execute("""CREATE TABLE sessions (
    id INTEGER PRIMARY KEY, model TEXT, started_at TEXT,
    input_tokens INTEGER, output_tokens INTEGER,
    cache_read_tokens INTEGER, cache_write_tokens INTEGER)""")
state_conn.commit()
state_conn.close()
```

同时更新 `_create_service` 传入 `state_db_path`。

- [ ] **Step 9: 全量测试确认无回归**

Run: `python3 -m pytest test/test_stats_service.py -q`
Expected: 全部 PASS

- [ ] **Step 10: 提交**

```bash
git add stats_service.py test/test_stats_service.py
git commit -m "feat: StatsService 三个 fetch 方法改为双源合并 + fetch_all_summaries"
```

---

### Task 4: 修改 fetch_by_upstream 的字段重命名

**Files:**
- Modify: `stats_service.py` — StatsService.fetch_by_upstream 方法
- Test: `test/test_stats_service.py` — TestFetchByUpstreamMerged

`fetch_by_upstream` 已是求和策略，但输出字段名是 `cached_read_tokens`/`cached_write_tokens`，需要统一为 `cache_*`。

- [ ] **Step 1: 添加字段名验证测试**

在 `TestFetchByUpstreamMerged` 中添加：

```python
def test_output_uses_cache_not_cached_prefix(self):
    result = self._create_service().fetch_by_upstream("week")
    for u in result["upstreams"]:
        self.assertIn("cache_read_tokens", u)
        self.assertNotIn("cached_read_tokens", u)
        self.assertIn("cache_write_tokens", u)
        self.assertNotIn("cached_write_tokens", u)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest test/test_stats_service.py::TestFetchByUpstreamMerged::test_output_uses_cache_not_cached_prefix -v`
Expected: FAIL — 当前输出用 `cached_*`

- [ ] **Step 3: 修改 fetch_by_upstream 的输出字段名**

在 `fetch_by_upstream` 方法中，将最终结果组装时的 `cached_read_tokens`/`cached_write_tokens` 改为 `cache_read_tokens`/`cache_write_tokens`。

具体修改 `fetch_by_upstream` 中 L1014-1024 的 result.append 块：

```python
result.append({
    "upstream_id": name,
    "base_url": base_url,
    "request_count": agg["request_count"],
    "input_tokens": agg["input_tokens"],
    "output_tokens": agg["output_tokens"],
    "cache_read_tokens": agg["cached_read_tokens"],
    "cache_write_tokens": agg["cached_write_tokens"],
    "total_tokens": agg["total_tokens"],
    "estimated_cost_usd": round(cost, 6),
})
```

注意：内部合并 map 仍用 `cached_*`（因为 DAO 返回的是这个格式），只在最终输出时重命名。

- [ ] **Step 4: 更新 fetch_by_upstream 的已有测试断言**

将 `TestFetchByUpstreamMerged` 中所有 `cached_read_tokens` / `cached_write_tokens` 断言改为 `cache_read_tokens` / `cache_write_tokens`。

- [ ] **Step 5: 运行测试确认通过**

Run: `python3 -m pytest test/test_stats_service.py::TestFetchByUpstreamMerged -v`
Expected: 全部 PASS

- [ ] **Step 6: 提交**

```bash
git add stats_service.py test/test_stats_service.py
git commit -m "fix: fetch_by_upstream 输出字段名统一为 cache_*"
```

---

### Task 5: 修改 fetch_requests 和 fetch_by_model_requests 的字段重命名

**Files:**
- Modify: `stats_service.py` — StatsService.fetch_requests 和 fetch_by_model_requests
- Test: `test/test_stats_service.py` — TestFetchRequestsMerged, TestFetchByModelRequestsMerged

这两个方法已是求和策略，但单条记录中仍有 `cached_*` 字段名，需统一为 `cache_*`。

- [ ] **Step 1: 添加字段名验证测试**

在 `TestFetchRequestsMerged` 中添加：

```python
def test_request_record_uses_cache_prefix(self):
    self._insert_token_stat(target_model="m1", input_tokens=100, output_tokens=50,
                            cached_read_tokens=20, cached_write_tokens=10)
    result = self._create_service().fetch_requests("week")
    for req in result["requests"]:
        if req.get("_source") == "proxy":
            self.assertIn("cache_read_tokens", req)
            self.assertNotIn("cached_read_tokens", req)
```

- [ ] **Step 2: 运行测试确认失败**

- [ ] **Step 3: 在 fetch_requests 和 fetch_by_model_requests 中添加字段重命名**

在组装 `unified_requests` 时，对每条 proxy 记录做 `_Merger._rename()`：

```python
for row in token_rows:
    row_dict = dict(row) if hasattr(row, 'keys') else dict(row)
    row_dict = _Merger._rename(row_dict)
    row_dict["_source"] = "proxy"
    row_dict["estimated_cost_usd"] = calculator.calculate(
        model=row_dict.get("target_model", row_dict.get("model", "")),
        input_tokens=row_dict.get("input_tokens", 0),
        output_tokens=row_dict.get("output_tokens", 0),
        cache_read_tokens=row_dict.get("cache_read_tokens", 0),
        cache_write_tokens=row_dict.get("cache_write_tokens", 0),
    )
    unified_requests.append(row_dict)
```

同样对 session 记录：`_row_to_record` 返回的是 `cached_*`，也需 rename：

```python
for rec in session_rows:
    rec = _Merger._rename(rec)
    rec["estimated_cost_usd"] = calculator.calculate(
        model=rec.get("target_model", rec.get("model", "")),
        input_tokens=rec.get("input_tokens", 0),
        output_tokens=rec.get("output_tokens", 0),
        cache_read_tokens=rec.get("cache_read_tokens", 0),
        cache_write_tokens=rec.get("cache_write_tokens", 0),
    )
    unified_requests.append(rec)
```

对 `fetch_by_model_requests` 中的两个 for 循环做同样修改：

```python
for row in token_rows:
    row_dict = dict(row) if hasattr(row, 'keys') else dict(row)
    row_dict = _Merger._rename(row_dict)
    row_dict["_source"] = "proxy"
    row_dict["estimated_cost_usd"] = calculator.calculate(
        model=row_dict.get("target_model", row_dict.get("model", "")),
        input_tokens=row_dict.get("input_tokens", 0),
        output_tokens=row_dict.get("output_tokens", 0),
        cache_read_tokens=row_dict.get("cache_read_tokens", 0),
        cache_write_tokens=row_dict.get("cache_write_tokens", 0),
    )
    unified_requests.append(row_dict)

for rec in session_rows:
    rec = _Merger._rename(rec)
    rec["estimated_cost_usd"] = calculator.calculate(
        model=rec.get("target_model", rec.get("model", "")),
        input_tokens=rec.get("input_tokens", 0),
        output_tokens=rec.get("output_tokens", 0),
        cache_read_tokens=rec.get("cache_read_tokens", 0),
        cache_write_tokens=rec.get("cache_write_tokens", 0),
    )
    unified_requests.append(rec)
```

- [ ] **Step 4: 更新已有测试中的 `cached_*` 断言**

在 `TestFetchRequestsMerged` 和 `TestFetchByModelRequestsMerged` 中将 `cached_read_tokens`/`cached_write_tokens` 改为 `cache_read_tokens`/`cache_write_tokens`。

- [ ] **Step 5: 全量测试确认**

Run: `python3 -m pytest test/test_stats_service.py -q`
Expected: 全部 PASS

- [ ] **Step 6: 提交**

```bash
git add stats_service.py test/test_stats_service.py
git commit -m "fix: fetch_requests/fetch_by_model_requests 输出字段名统一为 cache_*"
```

---

### Task 6: 清理 server.py — 删除迁移函数 + 更新路由转发

**Files:**
- Modify: `server.py` — 删除 ~12 个函数 + 更新 API 路由

这是最大的清理步骤。删除所有已迁移到 stats_service.py 的函数，将 API 端点改为调用 `_get_stats_service()`。

- [ ] **Step 1: 删除 server.py 中的迁移函数**

删除以下函数（及其间的空行/注释）：
- `_get_stats_calculator()` (L175)
- `get_cc_switch_db()` (L193)
- `get_model_pricing()` (L211)
- `calculate_cost()` (L221)
- `get_time_range()` (L328)
- `_get_proxy_token_aggregate()` (L341)
- `_get_proxy_token_by_model()` (L371)
- `_get_proxy_token_trend()` (L407)
- `get_token_stats()` (L544)
- `_normalize_model_name()` (L612)
- `get_token_stats_by_model()` (L618)
- `get_daily_token_trend()` (L698)

- [ ] **Step 2: 更新 API 路由**

将 `HermesDataHandler.do_GET` 中的 token 统计 API 路由改为调用 stats_service：

```python
if path == "/api/token_stats":
    period = qs.get("period", ["week"])[0]
    if period not in ("day", "week", "month"): period = "week"
    stats = _get_stats_service().fetch_summary(period)
    return json_response(self, stats)

if path == "/api/token_stats/by_model":
    period = qs.get("period", ["week"])[0]
    if period not in ("day", "week", "month"): period = "week"
    models = _get_stats_service().fetch_by_model(period)
    return json_response(self, {"models": models, "count": len(models)})

if path == "/api/token_stats/trend":
    period = qs.get("period", ["week"])[0]
    if period not in ("day", "week", "month"): period = "week"
    trends = _get_stats_service().fetch_trend(period)
    return json_response(self, {"trends": trends, "count": len(trends)})

if path == "/api/token_stats/summary":
    return json_response(self, _get_stats_service().fetch_all_summaries())
```

（`/requests`、`/by_upstream`、`/by_model/*/requests` 三个路由已经是调用 stats_service，无需改）

- [ ] **Step 3: 运行全量测试**

Run: `python3 -m pytest test/ -q`
Expected: 全部通过。如有 server.py 中引用已删除函数的测试，一并删除。

- [ ] **Step 4: 端到端验证**

Run: `./server.sh restart`
然后 curl 测试各端点：

```bash
curl -s http://127.0.0.1:18742/api/token_stats?period=week | python3 -m json.tool
curl -s http://127.0.0.1:18742/api/token_stats/by_model?period=week | python3 -m json.tool
curl -s http://127.0.0.1:18742/api/token_stats/trend?period=week | python3 -m json.tool
curl -s http://127.0.0.1:18742/api/token_stats/summary | python3 -m json.tool
```

确认：
1. 返回数据非空
2. 字段名是 `cache_read_tokens` 而非 `cached_read_tokens`
3. `?period=day` 不再返回全 0

- [ ] **Step 5: 提交**

```bash
git add server.py
git commit -m "refactor: server.py token 统计函数全部迁移到 stats_service，仅保留路由转发"
```

---

### Task 7: 前端验证

**Files:**
- 无代码改动（前端字段名已是 `cache_*`，与 Merger 输出一致）

- [ ] **Step 1: 启动服务并在浏览器中验证**

Run: `./server.sh restart`

在浏览器中逐一验证：
1. Token 统计页 — KPI 卡片数据非 0（尤其是 day 周期）
2. 周期切换 24h/7天/30天 — 数据变化正常
3. 模型统计表格 — 数据正常
4. 趋势图表 — 数据正常
5. 请求日志 tab — 数据正常
6. 按上游统计 tab — 数据正常
7. 模型管理 / 路由映射 / 数据库查询页 — 不受影响

- [ ] **Step 2: 全量测试最终确认**

Run: `python3 -m pytest test/ -q`
Expected: 全部通过
