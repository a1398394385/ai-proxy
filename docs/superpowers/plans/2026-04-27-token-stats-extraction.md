# Token 统计抽取实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 token 统计逻辑从 proxy.py 抽取为独立 `token_stats.py`，统一处理 Anthropic / OpenAI Chat / OpenAI Responses 三种 usage 格式。

**Architecture:** 新增 `token_stats.py` 提供 `record_token_stats(usage, context)` 函数，内部使用 `_find_first()` 按 key 存在性（非值大小）做优先级查找，提取字段后直接写入 access_log.db 的 token_stats 表（幂等建表）。proxy.py 两处调用点简化为传 usage + context，transform.py 不再做 Anthropic cache 适配。

**Tech Stack:** Python 标准库（sqlite3, json, logging），无外部依赖

---

### 文件职责

| 文件 | 创建/修改 | 职责 |
|------|----------|------|
| `token_stats.py` | 创建 | `record_token_stats()` + `_find_first()`，直接写 DB |
| `test/test_token_stats.py` | 创建 | token_stats 单元测试（5 种格式 + 10 个边界/辅助） |
| `proxy.py` | 修改 | 两处调用点替换，移除内联格式提取 |
| `transform.py` | 修改 | `_emit_completion` usage 改为透传原始字段 |
| `test/test_proxy_logger_integration.py` | 检查 | 确认 mock SSE 格式与新透传逻辑兼容 |

---

### Task 1: 创建 `token_stats.py` — 核心函数

**Files:**
- Create: `token_stats.py`

- [ ] **Step 1: 写 `_find_first` 辅助函数和 `record_token_stats` 函数**

```python
#!/usr/bin/env python3
"""Token 统计模块 — 统一处理 Anthropic / OpenAI Chat / OpenAI Responses 格式的 usage。

用法：
    from token_stats import record_token_stats

    record_token_stats(usage, {
        "request_id": "abc123",
        "agent": "codex",
        "model": "gpt-5.1-codex-max",
        "target_model": "qwen3.6-plus",
        "request_ts": "2026-04-27 10:00:00",
        "duration_ms": 1234,
    })

DB_PATH 假设：token_stats.py 位于项目根目录，与 data/access_log.db 同级。
与 request_logger.py 使用同一路径约定。
"""

import sqlite3
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# 依赖：token_stats.py 必须在项目根目录，与 data/ 同级
DB_PATH = Path(__file__).parent / "data" / "access_log.db"


def _find_first(usage: dict, keys: list, default=0) -> int:
    """按 keys 顺序查找 usage 中第一个存在的 key，返回其值。

    使用 key 存在性（k in usage）而非值大小做判断。
    这意味着优先级 1 的 key 即使值为 0（如 cache 未命中）也不会回退到优先级 2。
    只有 key 完全不存在时才检查下一个。
    """
    for k in keys:
        if k in usage:
            return usage[k]
    return default


def _extract_tokens(usage: dict) -> dict:
    """从 usage 中提取标准化的 token counts。

    返回: {
        "input_tokens": int,
        "output_tokens": int,
        "cached_read": int,
        "cached_write": int,
    }
    """
    # 展开嵌套的 details dict（_find_first 只查顶层 key，不做点号导航）
    prompt_details = usage.get("prompt_tokens_details", {})
    input_details = usage.get("input_tokens_details", {})

    cached_read = 0
    if "cache_read_input_tokens" in usage:
        cached_read = usage["cache_read_input_tokens"]
    elif "cached_tokens" in prompt_details:
        cached_read = prompt_details["cached_tokens"]
    elif "cached_tokens" in input_details:
        cached_read = input_details["cached_tokens"]

    cached_write = 0
    if "cache_creation_input_tokens" in usage:
        cached_write = usage["cache_creation_input_tokens"]
    elif "cache_creation_input_tokens" in input_details:
        cached_write = input_details["cache_creation_input_tokens"]

    return {
        "input_tokens": _find_first(usage, ["prompt_tokens", "input_tokens"]),
        "output_tokens": _find_first(usage, ["completion_tokens", "output_tokens"]),
        "cached_read": cached_read,
        "cached_write": cached_write,
    }


def record_token_stats(usage: dict, context: dict) -> None:
    """解析 usage 并写入 token_stats 表。失败静默，不抛异常。

    usage:  上游返回的原始 usage dict。None / 空 dict 直接 return。
    context: {
        "request_id": str,     # 缺失 → warning + return
        "agent": str,          # 默认 "unknown"
        "model": str,          # 默认 "unknown"
        "target_model": str,   # 默认 "unknown"
        "request_ts": str,     # 默认 ""
        "duration_ms": int,    # 默认 0
    }
    """
    if not usage:
        return

    request_id = context.get("request_id")
    if not request_id:
        logger.warning("token_stats: 缺少 request_id，跳过写入")
        return

    tokens = _extract_tokens(usage)

    try:
        conn = sqlite3.connect(str(DB_PATH))
        try:
            # 幂等建表：确保 token_stats 表存在（不依赖 request_logger 初始化顺序）
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS token_stats (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id          TEXT NOT NULL,
                    agent               TEXT NOT NULL,
                    model               TEXT NOT NULL,
                    target_model        TEXT NOT NULL,
                    request_ts          TEXT NOT NULL,
                    duration_ms         INTEGER,
                    input_tokens        INTEGER DEFAULT 0,
                    output_tokens       INTEGER DEFAULT 0,
                    cached_read_tokens  INTEGER DEFAULT 0,
                    cached_write_tokens INTEGER DEFAULT 0,
                    status              TEXT DEFAULT 'completed',
                    created_at          TEXT NOT NULL
                )
            """)
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "INSERT INTO token_stats "
                "(request_id, agent, model, target_model, request_ts, duration_ms, "
                "input_tokens, output_tokens, cached_read_tokens, cached_write_tokens, "
                "status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?)",
                (
                    request_id,
                    context.get("agent", "unknown"),
                    context.get("model", "unknown"),
                    context.get("target_model", "unknown"),
                    context.get("request_ts", ""),
                    context.get("duration_ms", 0),
                    tokens["input_tokens"],
                    tokens["output_tokens"],
                    tokens["cached_read"],
                    tokens["cached_write"],
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"token_stats 写入失败: {e}")
```

- [ ] **Step 2: 验证模块可导入**

```bash
python3 -c "from token_stats import _find_first; print(_find_first({'a': 1}, ['a']))"
```
Expected: `1`

- [ ] **Step 3: Commit**

```bash
git add token_stats.py
git commit -m "新增 token_stats.py：统一处理 Anthropic/OpenAI Chat/Responses 三种 usage 格式"
```

---

### Task 2: 新增 `test/test_token_stats.py` — 16 个单元测试

**Files:**
- Create: `test/test_token_stats.py`

覆盖 5 种格式（OpenAI Chat / Responses / Anthropic cache / mixed qwen / pure Anthropic）+
10 个边界/辅助测试（_find_first × 5 + record_token_stats × 5）。

- [ ] **Step 1: 写测试代码**

```python
import json
import unittest
import sqlite3
import tempfile
import os
from pathlib import Path


class TestFindFirst(unittest.TestCase):
    """_find_first — 按 key 存在性（非值大小）做优先级查找。"""

    def test_returns_first_existing_key(self):
        from token_stats import _find_first
        usage = {"prompt_tokens": 100, "input_tokens": 200}
        self.assertEqual(_find_first(usage, ["prompt_tokens", "input_tokens"]), 100)

    def test_skips_missing_key(self):
        from token_stats import _find_first
        usage = {"input_tokens": 200}
        self.assertEqual(_find_first(usage, ["prompt_tokens", "input_tokens"]), 200)

    def test_zero_is_valid_value(self):
        """key 存在且值为 0 时返回 0，不回退到下一个 key。"""
        from token_stats import _find_first
        usage = {"prompt_tokens": 0, "input_tokens": 200}
        self.assertEqual(_find_first(usage, ["prompt_tokens", "input_tokens"]), 0)

    def test_no_match_returns_default(self):
        from token_stats import _find_first
        usage = {}
        self.assertEqual(_find_first(usage, ["prompt_tokens"]), 0)

    def test_all_missing_returns_default(self):
        from token_stats import _find_first
        usage = {"other": 999}
        self.assertEqual(_find_first(usage, ["prompt_tokens", "input_tokens"]), 0)


class TestExtractTokens(unittest.TestCase):
    """_extract_tokens 多格式提取测试 — 5 种格式。"""

    def test_openai_chat_format(self):
        """OpenAI Chat Completions：prompt_tokens + completion_tokens + prompt_tokens_details.cached_tokens。"""
        from token_stats import _extract_tokens
        usage = {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "prompt_tokens_details": {"cached_tokens": 20},
        }
        result = _extract_tokens(usage)
        self.assertEqual(result["input_tokens"], 100)
        self.assertEqual(result["output_tokens"], 50)
        self.assertEqual(result["cached_read"], 20)
        self.assertEqual(result["cached_write"], 0)

    def test_openai_responses_format(self):
        """OpenAI Responses API：input_tokens + output_tokens + input_tokens_details.cached_tokens。"""
        from token_stats import _extract_tokens
        usage = {
            "input_tokens": 200,
            "output_tokens": 80,
            "total_tokens": 280,
            "input_tokens_details": {"cached_tokens": 30},
        }
        result = _extract_tokens(usage)
        self.assertEqual(result["input_tokens"], 200)
        self.assertEqual(result["output_tokens"], 80)
        self.assertEqual(result["cached_read"], 30)
        self.assertEqual(result["cached_write"], 0)

    def test_anthropic_cache_format(self):
        """Anthropic：prompt_tokens + cache_read_input_tokens + cache_creation_input_tokens 都在顶层。"""
        from token_stats import _extract_tokens
        usage = {
            "prompt_tokens": 5000,
            "completion_tokens": 200,
            "cache_read_input_tokens": 4500,
            "cache_creation_input_tokens": 500,
        }
        result = _extract_tokens(usage)
        self.assertEqual(result["input_tokens"], 5000)
        self.assertEqual(result["output_tokens"], 200)
        self.assertEqual(result["cached_read"], 4500)
        self.assertEqual(result["cached_write"], 500)

    def test_mixed_qwen_format(self):
        """qwen 混合：Chat 的 prompt_tokens + Anthropic 的 cache_* 顶层字段共存。"""
        from token_stats import _extract_tokens
        usage = {
            "prompt_tokens": 13640,
            "completion_tokens": 152,
            "total_tokens": 13792,
            "cache_read_input_tokens": 10000,
            "cache_creation_input_tokens": 3640,
        }
        result = _extract_tokens(usage)
        self.assertEqual(result["input_tokens"], 13640)
        self.assertEqual(result["output_tokens"], 152)
        self.assertEqual(result["cached_read"], 10000)
        self.assertEqual(result["cached_write"], 3640)

    def test_pure_anthropic_responses_format(self):
        """纯 Anthropic 变体：input_tokens + output_tokens + cache_* 都在顶层。"""
        from token_stats import _extract_tokens
        usage = {
            "input_tokens": 3000,
            "output_tokens": 500,
            "cache_read_input_tokens": 2000,
            "cache_creation_input_tokens": 1000,
        }
        result = _extract_tokens(usage)
        self.assertEqual(result["input_tokens"], 3000)
        self.assertEqual(result["output_tokens"], 500)
        self.assertEqual(result["cached_read"], 2000)
        self.assertEqual(result["cached_write"], 1000)

    def test_anthropic_cache_miss_zero(self):
        """Anthropic cache 未命中（值为 0）时正确返回 0，不回退到其他格式的值。"""
        from token_stats import _extract_tokens
        usage = {
            "prompt_tokens": 5000,
            "completion_tokens": 200,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }
        result = _extract_tokens(usage)
        self.assertEqual(result["cached_read"], 0)
        self.assertEqual(result["cached_write"], 0)


class TestRecordTokenStats(unittest.TestCase):
    """record_token_stats 集成测试（写 DB）。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "access_log.db"
        import token_stats
        self._orig_db_path = token_stats.DB_PATH
        token_stats.DB_PATH = self.db_path

    def tearDown(self):
        import token_stats
        token_stats.DB_PATH = self._orig_db_path

    def _query(self):
        conn = sqlite3.connect(str(self.db_path))
        rows = conn.execute("SELECT * FROM token_stats").fetchall()
        conn.close()
        return rows

    def test_writes_token_stats(self):
        from token_stats import record_token_stats
        usage = {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "prompt_tokens_details": {"cached_tokens": 20},
        }
        context = {
            "request_id": "req-001",
            "agent": "codex",
            "model": "gpt-5.1-codex-max",
            "target_model": "qwen3.6-plus",
            "request_ts": "2026-04-27 10:00:00",
            "duration_ms": 1234,
        }
        record_token_stats(usage, context)

        rows = self._query()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r[1], "req-001")            # request_id
        self.assertEqual(r[2], "codex")              # agent
        self.assertEqual(r[6], 100)                  # input_tokens
        self.assertEqual(r[7], 50)                   # output_tokens
        self.assertEqual(r[8], 20)                   # cached_read_tokens
        self.assertEqual(r[9], 0)                    # cached_write_tokens
        self.assertEqual(r[10], "completed")         # status

    def test_empty_usage_does_not_write(self):
        from token_stats import record_token_stats
        record_token_stats({}, {"request_id": "req-002"})
        rows = self._query()
        self.assertEqual(len(rows), 0)

    def test_none_usage_does_not_write(self):
        from token_stats import record_token_stats
        record_token_stats(None, {"request_id": "req-003"})
        rows = self._query()
        self.assertEqual(len(rows), 0)

    def test_missing_request_id_does_not_write(self):
        from token_stats import record_token_stats
        record_token_stats({"prompt_tokens": 10}, {"agent": "test"})
        rows = self._query()
        self.assertEqual(len(rows), 0)

    def test_db_write_failure_does_not_raise(self):
        from token_stats import record_token_stats
        os.remove(str(self.db_path))
        try:
            record_token_stats({"prompt_tokens": 10}, {"request_id": "req-004"})
        except Exception:
            self.fail("record_token_stats 不应该抛异常")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试**

```bash
python3 -m pytest test/test_token_stats.py -v
```
Expected: 16/16 PASS（6 个 _find_first + 6 个 _extract_tokens + 5 个 record_token_stats）

- [ ] **Step 3: Commit**

```bash
git add test/test_token_stats.py
git commit -m "新增 token_stats 单元测试：5 种格式 + 11 种边界/辅助场景，16 个测试"
```

---

### Task 3: 重构 `proxy.py` — 替换两处 token 统计调用点

**Files:**
- Modify: `proxy.py`

- [ ] **Step 1: 添加 import**

在文件顶部现有 `from request_logger import` 块附近添加：

```python
from token_stats import record_token_stats
```

- [ ] **Step 2: 修改非流式路径（line ~370-383）**

将 13 行内联格式提取 + `log_token_stats` 替换为：

```python
                        usage = chat_response.get("usage", {})
                        if usage:
                            record_token_stats(usage, {
                                "request_id": request_id,
                                "agent": _extract_agent(self.headers.get("User-Agent", "")),
                                "model": model,
                                "target_model": target,
                                "request_ts": request_ts,
                                "duration_ms": duration_ms,
                            })
```

- [ ] **Step 3: 修改流式路径（line ~560-570）**

将当前 10 行替换为：

```python
            agent = _extract_agent(self.headers.get("User-Agent", ""))
            if final_usage:
                record_token_stats(final_usage, {
                    "request_id": request_id,
                    "agent": agent,
                    "model": model,
                    "target_model": target,
                    "request_ts": request_ts,
                    "duration_ms": duration_ms,
                })
```

- [ ] **Step 4: 运行全量测试**

```bash
python3 -m pytest test/ -q
```
Expected: 127/127 PASS（111 原有 + 16 新增）

- [ ] **Step 5: Commit**

```bash
git add proxy.py
git commit -m "proxy.py 替换为 record_token_stats，移除内联格式提取逻辑"
```

---

### Task 4: 重构 `transform.py` — `_emit_completion` usage 透传

**Files:**
- Modify: `transform.py:587-610`

- [ ] **Step 1: 简化 usage 构建为透传**

将当前的 Anthropic cache 适配逻辑替换为——透传 state.usage 原始字段，只做 Chat → Responses 字段重命名，两个 details dict 合并保留：

```python
    # completed
    raw = state.usage
    usage = {
        "input_tokens": raw.get("prompt_tokens") or raw.get("input_tokens", 0),
        "output_tokens": raw.get("completion_tokens") or raw.get("output_tokens", 0),
        "total_tokens": raw.get("total_tokens", 0),
    }
    # 合并两个 details dict（上游可能同时返回 prompt_tokens_details 和 input_tokens_details）
    details = {}
    for k in ("prompt_tokens_details", "input_tokens_details"):
        if raw.get(k):
            details.update(raw[k])
    if details:
        usage["input_tokens_details"] = details
    output_details = raw.get("completion_tokens_details") or raw.get("output_tokens_details")
    if output_details:
        usage["output_tokens_details"] = output_details
    # 透传 Anthropic 格式顶层缓存字段
    for k in ("cache_read_input_tokens", "cache_creation_input_tokens"):
        if k in raw:
            usage[k] = raw[k]
    completed_response = {
        "id": state.response_id,
        "status": FINISH_REASON_MAP.get(state.finish_reason, "completed"),
        "output": state.output_items,
        "usage": usage,
    }
```

- [ ] **Step 2: 运行全量测试**

```bash
python3 -m pytest test/ -q
```
Expected: 127/127 PASS

- [ ] **Step 3: Commit**

```bash
git add transform.py
git commit -m "transform._emit_completion usage 改为透传原始字段，移除 Anthropic cache 适配"
```

---

### Task 5: 确认集成测试兼容性

**Files:**
- Read: `test/test_proxy_logger_integration.py:287-302`

- [ ] **Step 1: 确认 mock SSE 路径与新的透传逻辑兼容**

打开 `test/test_proxy_logger_integration.py` 的 `test_streaming_flow_logs_upstream_and_token_stats` 方法，确认：
- 该测试 mock 了 `create_codex_sse_stream`，return_value 是 `sse_events` 列表
- mock 的 SSE 事件直接就是最终格式，绕过了 `_emit_completion`
- `response.completed` 事件的 usage 部分是 `{"input_tokens":100,"output_tokens":50,"input_tokens_details":{"cached_tokens":20}}`（Responses API 格式）
- 这个 usage 进入 `record_token_stats` 后会通过 `_extract_tokens` 正确提取（`input_tokens` → input，`input_tokens_details.cached_tokens` → cached_read）

如果 mock 路径确认无误，**无需修改测试代码**。

- [ ] **Step 2: 运行集成测试确认**

```bash
python3 -m pytest test/test_proxy_logger_integration.py -v
```
Expected: all PASS

- [ ] **Step 3: Commit（如有修改）**

```bash
# 如无需修改则跳过
git add test/test_proxy_logger_integration.py
git commit -m "适配集成测试到新的 token_stats 路径"
```

---

### Task 6: 最终验证

- [ ] **Step 1: 运行全量测试**

```bash
python3 -m pytest test/ -q
```
Expected: 127/127 PASS

- [ ] **Step 2: 重启 proxy**

```bash
./server.sh restart
```

- [ ] **Step 3: 冒烟测试（需上游网络可用）**

```bash
# 注意：此测试依赖上游 LiteLLM 网关可达 (172.40.2.162:4000)
# 如上游不可用则跳过，以 Task 6 Step 1（全量测试通过）为准
curl -s -N -X POST http://127.0.0.1:48743/v1/responses \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","max_output_tokens":3,"stream":true,"input":[{"type":"message","role":"user","content":"Say h"}]}' \
  --max-time 60 2>&1 | grep "response.completed" | head -1
```

- [ ] **Step 4: 检查 token_stats 数据库**

```bash
sqlite3 data/access_log.db "SELECT request_id, input_tokens, output_tokens, cached_read_tokens, cached_write_tokens FROM token_stats ORDER BY id DESC LIMIT 3"
```

---

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| `_find_first` 用 `k in usage` 判断存在性，不用 `v > 0` | 0 是合法的业务值（cache 未命中），不应被跳过 |
| `_extract_tokens` 中 cache_* 用 if/elif 链而非 `_find_first` | cache_read 和 cache_write 的嵌套路径（details.cached_tokens）需要展开；if/elif 更清晰表达 Anthropic → Chat → Responses 优先级 |
| `record_token_stats` 内建 `CREATE TABLE IF NOT EXISTS` | 不依赖 request_logger 初始化顺序，首次运行也能工作 |
| `sqlite3.connect` 不加 `check_same_thread` | 每次调用新建连接，每个线程操作自己的连接，天然线程安全 |
| `request_logger.log_token_stats()` 保留但不再调用 | 向后兼容，避免破坏性变更 |
| `DB_PATH` 硬编码 `Path(__file__).parent / "data" / "access_log.db"` | 与 request_logger.py 同一路径约定，模块需放在项目根目录 |
| context 缺字段用默认值，仅 `request_id` 缺失时跳过写入 | 统计容错性优先于完整性 |
