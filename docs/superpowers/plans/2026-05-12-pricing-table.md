# 计费表迁移 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 CC-switch 的 model_pricing 表迁移到本项目 config.db，实现自建计费管理，支持 USD/RMB 双币种，统一输出人民币。

**Architecture:** 新建 PricingDB 独立管理 pricing 表；改造 _CostCalculator 从 config.db 读取定价并换算为人民币；server.py 新增 REST API；前端新增独立 Tab。

**Tech Stack:** Python stdlib (sqlite3), ES Modules, 无外部依赖

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `proxy/pricing_manager.py` | PricingDB — model_pricing 表 CRUD + 种子数据 |
| Create | `test/test_pricing_manager.py` | PricingDB 测试 |
| Modify | `stats_service.py` | _CostCalculator 改造 + estimated_cost_usd → estimated_cost_cny + fetch_trend 修复 |
| Modify | `test/test_stats_service.py` | 测试适配 |
| Modify | `server.py` | 新增 /api/pricing/* 路由 + StatsService 构造函数变更 |
| Create | `static/js/pages/pricing.js` | 计费表前端页面 |
| Create | `static/css/pricing.css` | 计费表样式 |
| Modify | `static/index.html` | 新增 Tab + 容器 + CSS link |
| Modify | `static/js/app.js` | 注册 pricing 页面 |
| Modify | `static/js/pages/tokens.js` | $ → ¥ + estimated_cost_usd → estimated_cost_cny + toFixed(6) |

---

### Task 1: PricingDB — 模块骨架 + _ensure_table + 种子数据

**Files:**
- Create: `proxy/pricing_manager.py`
- Create: `test/test_pricing_manager.py`

- [ ] **Step 1: 写失败测试 — _ensure_table 建表 + 种子数据导入**

```python
# test/test_pricing_manager.py
import unittest
import sqlite3
from tempfile import TemporaryDirectory
from pathlib import Path


class TestPricingDBEnsureTable(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "config.db"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_ensure_table_creates_model_pricing(self):
        from proxy.pricing_manager import PricingDB
        db = PricingDB(self.db_path)
        db._ensure_table()
        conn = sqlite3.connect(str(self.db_path))
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='model_pricing'"
        ).fetchall()]
        conn.close()
        self.assertIn("model_pricing", tables)

    def test_ensure_table_idempotent(self):
        from proxy.pricing_manager import PricingDB
        db = PricingDB(self.db_path)
        db._ensure_table()
        db._ensure_table()  # 第二次不应报错
        conn = sqlite3.connect(str(self.db_path))
        count = conn.execute("SELECT COUNT(*) FROM model_pricing").fetchone()[0]
        conn.close()
        self.assertGreater(count, 0)

    def test_seed_data_imported_on_empty_table(self):
        from proxy.pricing_manager import PricingDB
        db = PricingDB(self.db_path)
        db._ensure_table()
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM model_pricing WHERE model_id = ?", ("claude-sonnet-4-6-20260217",)
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["display_name"], "Claude Sonnet 4.6")
        self.assertEqual(row["input_cost_per_million"], "3")
        self.assertEqual(row["currency"], "USD")

    def test_seed_data_not_reimported(self):
        from proxy.pricing_manager import PricingDB
        db = PricingDB(self.db_path)
        db._ensure_table()
        # 删一条，再调用 _ensure_table，不应重新导入
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("DELETE FROM model_pricing WHERE model_id = 'claude-sonnet-4-6-20260217'")
        conn.commit()
        count_before = conn.execute("SELECT COUNT(*) FROM model_pricing").fetchone()[0]
        conn.close()
        db._ensure_table()
        conn = sqlite3.connect(str(self.db_path))
        count_after = conn.execute("SELECT COUNT(*) FROM model_pricing").fetchone()[0]
        conn.close()
        self.assertEqual(count_before, count_after)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest test/test_pricing_manager.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'proxy.pricing_manager'`

- [ ] **Step 3: 实现 PricingDB — _ensure_table + 种子数据**

```python
# proxy/pricing_manager.py
"""模型计费定价管理 — PricingDB（model_pricing 表 CRUD + 种子数据）。"""

import sqlite3
import logging
from pathlib import Path
from typing import Optional


_SEED_PRICING = [
    # Anthropic Claude 系列
    ("claude-opus-4-7", "Claude Opus 4.7", "5", "25", "0.50", "6.25"),
    ("claude-opus-4-6-20260206", "Claude Opus 4.6", "5", "25", "0.50", "6.25"),
    ("claude-sonnet-4-6-20260217", "Claude Sonnet 4.6", "3", "15", "0.30", "3.75"),
    ("claude-opus-4-5-20251101", "Claude Opus 4.5", "5", "25", "0.50", "6.25"),
    ("claude-sonnet-4-5-20250929", "Claude Sonnet 4.5", "3", "15", "0.30", "3.75"),
    ("claude-haiku-4-5-20251001", "Claude Haiku 4.5", "1", "5", "0.10", "1.25"),
    ("claude-opus-4-20250514", "Claude Opus 4", "15", "75", "1.50", "18.75"),
    ("claude-opus-4-1-20250805", "Claude Opus 4.1", "15", "75", "1.50", "18.75"),
    ("claude-sonnet-4-20250514", "Claude Sonnet 4", "3", "15", "0.30", "3.75"),
    ("claude-3-5-haiku-20241022", "Claude 3.5 Haiku", "0.80", "4", "0.08", "1"),
    ("claude-3-5-sonnet-20241022", "Claude 3.5 Sonnet", "3", "15", "0.30", "3.75"),
    # OpenAI GPT-5.4 系列
    ("gpt-5.4", "GPT-5.4", "2.50", "15", "0.25", "0"),
    ("gpt-5.4-mini", "GPT-5.4 Mini", "0.75", "4.50", "0.075", "0"),
    ("gpt-5.4-nano", "GPT-5.4 Nano", "0.20", "1.25", "0.02", "0"),
    # OpenAI GPT-5.2 系列
    ("gpt-5.2", "GPT-5.2", "1.75", "14", "0.175", "0"),
    ("gpt-5.2-low", "GPT-5.2", "1.75", "14", "0.175", "0"),
    ("gpt-5.2-medium", "GPT-5.2", "1.75", "14", "0.175", "0"),
    ("gpt-5.2-high", "GPT-5.2", "1.75", "14", "0.175", "0"),
    ("gpt-5.2-xhigh", "GPT-5.2", "1.75", "14", "0.175", "0"),
    ("gpt-5.2-codex", "GPT-5.2 Codex", "1.75", "14", "0.175", "0"),
    ("gpt-5.2-codex-low", "GPT-5.2 Codex", "1.75", "14", "0.175", "0"),
    ("gpt-5.2-codex-medium", "GPT-5.2 Codex", "1.75", "14", "0.175", "0"),
    ("gpt-5.2-codex-high", "GPT-5.2 Codex", "1.75", "14", "0.175", "0"),
    ("gpt-5.2-codex-xhigh", "GPT-5.2 Codex", "1.75", "14", "0.175", "0"),
    # OpenAI GPT-5.3 Codex 系列
    ("gpt-5.3-codex", "GPT-5.3 Codex", "1.75", "14", "0.175", "0"),
    ("gpt-5.3-codex-low", "GPT-5.3 Codex", "1.75", "14", "0.175", "0"),
    ("gpt-5.3-codex-medium", "GPT-5.3 Codex", "1.75", "14", "0.175", "0"),
    ("gpt-5.3-codex-high", "GPT-5.3 Codex", "1.75", "14", "0.175", "0"),
    ("gpt-5.3-codex-xhigh", "GPT-5.3 Codex", "1.75", "14", "0.175", "0"),
    # OpenAI GPT-5.1 系列
    ("gpt-5.1", "GPT-5.1", "1.25", "10", "0.125", "0"),
    ("gpt-5.1-low", "GPT-5.1", "1.25", "10", "0.125", "0"),
    ("gpt-5.1-medium", "GPT-5.1", "1.25", "10", "0.125", "0"),
    ("gpt-5.1-high", "GPT-5.1", "1.25", "10", "0.125", "0"),
    ("gpt-5.1-minimal", "GPT-5.1", "1.25", "10", "0.125", "0"),
    ("gpt-5.1-codex", "GPT-5.1 Codex", "1.25", "10", "0.125", "0"),
    ("gpt-5.1-codex-mini", "GPT-5.1 Codex", "1.25", "10", "0.125", "0"),
    ("gpt-5.1-codex-max", "GPT-5.1 Codex", "1.25", "10", "0.125", "0"),
    ("gpt-5.1-codex-max-high", "GPT-5.1 Codex", "1.25", "10", "0.125", "0"),
    ("gpt-5.1-codex-max-xhigh", "GPT-5.1 Codex", "1.25", "10", "0.125", "0"),
    # OpenAI GPT-5 系列
    ("gpt-5", "GPT-5", "1.25", "10", "0.125", "0"),
    ("gpt-5-low", "GPT-5", "1.25", "10", "0.125", "0"),
    ("gpt-5-medium", "GPT-5", "1.25", "10", "0.125", "0"),
    ("gpt-5-high", "GPT-5", "1.25", "10", "0.125", "0"),
    ("gpt-5-minimal", "GPT-5", "1.25", "10", "0.125", "0"),
    ("gpt-5-codex", "GPT-5 Codex", "1.25", "10", "0.125", "0"),
    ("gpt-5-codex-low", "GPT-5 Codex", "1.25", "10", "0.125", "0"),
    ("gpt-5-codex-medium", "GPT-5 Codex", "1.25", "10", "0.125", "0"),
    ("gpt-5-codex-high", "GPT-5 Codex", "1.25", "10", "0.125", "0"),
    ("gpt-5-codex-mini", "GPT-5 Codex", "1.25", "10", "0.125", "0"),
    ("gpt-5-codex-mini-medium", "GPT-5 Codex", "1.25", "10", "0.125", "0"),
    ("gpt-5-codex-mini-high", "GPT-5 Codex", "1.25", "10", "0.125", "0"),
    # OpenAI Reasoning 系列
    ("o3", "OpenAI o3", "2", "8", "0.50", "0"),
    ("o4-mini", "OpenAI o4-mini", "1.10", "4.40", "0.275", "0"),
    ("o3-pro", "OpenAI o3-pro", "20", "80", "0", "0"),
    ("o3-mini", "OpenAI o3-mini", "0.55", "2.20", "0.55", "0"),
    ("o1", "OpenAI o1", "15", "60", "7.50", "0"),
    ("o1-mini", "OpenAI o1-mini", "0.55", "2.20", "0.55", "0"),
    ("codex-mini", "Codex Mini", "0.75", "3", "0.025", "0"),
    ("gpt-5-mini", "GPT-5 Mini", "0.25", "2", "0.025", "0"),
    ("gpt-5-nano", "GPT-5 Nano", "0.05", "0.40", "0.005", "0"),
    # OpenAI GPT-4.1 系列
    ("gpt-4.1", "GPT-4.1", "2", "8", "0.50", "0"),
    ("gpt-4.1-mini", "GPT-4.1 Mini", "0.40", "1.60", "0.10", "0"),
    ("gpt-4.1-nano", "GPT-4.1 Nano", "0.10", "0.40", "0.025", "0"),
    # Google Gemini 3.1 系列
    ("gemini-3.1-pro-preview", "Gemini 3.1 Pro Preview", "2", "12", "0.20", "0"),
    ("gemini-3.1-flash-lite-preview", "Gemini 3.1 Flash Lite Preview", "0.25", "1.50", "0.025", "0"),
    # Google Gemini 3 系列
    ("gemini-3-pro-preview", "Gemini 3 Pro Preview", "2", "12", "0.2", "0"),
    ("gemini-3-flash-preview", "Gemini 3 Flash Preview", "0.5", "3", "0.05", "0"),
    # Google Gemini 2.5 系列
    ("gemini-2.5-pro", "Gemini 2.5 Pro", "1.25", "10", "0.125", "0"),
    ("gemini-2.5-flash", "Gemini 2.5 Flash", "0.3", "2.5", "0.03", "0"),
    ("gemini-2.5-flash-lite", "Gemini 2.5 Flash Lite", "0.10", "0.40", "0.01", "0"),
    # Google Gemini 2.0 系列
    ("gemini-2.0-flash", "Gemini 2.0 Flash", "0.10", "0.40", "0.025", "0"),
    # StepFun 系列
    ("step-3.5-flash", "Step 3.5 Flash", "0.10", "0.30", "0.02", "0"),
    # Doubao 系列
    ("doubao-seed-code", "Doubao Seed Code", "0.17", "1.11", "0.02", "0"),
    ("doubao-seed-2-0-pro", "Doubao Seed 2.0 Pro", "0.47", "2.37", "0", "0"),
    ("doubao-seed-2-0-code", "Doubao Seed 2.0 Code", "0.47", "2.37", "0", "0"),
    ("doubao-seed-2-0-lite", "Doubao Seed 2.0 Lite", "0.25", "2", "0", "0"),
    ("doubao-seed-2-0-mini", "Doubao Seed 2.0 Mini", "0.03", "0.31", "0", "0"),
    # DeepSeek 系列
    ("deepseek-v3.2", "DeepSeek V3.2", "0.28", "0.42", "0.0028", "0"),
    ("deepseek-v3.1", "DeepSeek V3.1", "0.55", "1.67", "0.0055", "0"),
    ("deepseek-v3", "DeepSeek V3", "0.28", "1.11", "0.0028", "0"),
    ("deepseek-chat", "DeepSeek Chat", "0.27", "1.10", "0.007", "0"),
    ("deepseek-reasoner", "DeepSeek Reasoner", "0.55", "2.19", "0.014", "0"),
    ("deepseek-v4-flash", "DeepSeek V4 Flash", "0.14", "0.28", "0.0028", "0"),
    ("deepseek-v4-pro", "DeepSeek V4 Pro", "1.68", "3.36", "0.014", "0"),
    # Kimi 系列
    ("kimi-k2-thinking", "Kimi K2 Thinking", "0.55", "2.20", "0.10", "0"),
    ("kimi-k2-0905", "Kimi K2", "0.55", "2.20", "0.10", "0"),
    ("kimi-k2-turbo", "Kimi K2 Turbo", "1.11", "8.06", "0.14", "0"),
    ("kimi-k2.5", "Kimi K2.5", "0.60", "2.50", "0.10", "0"),
    ("kimi-k2.6", "Kimi K2.6", "0.95", "4.00", "0.16", "0"),
    # MiniMax 系列
    ("minimax-m2.1", "MiniMax M2.1", "0.27", "0.95", "0.03", "0"),
    ("minimax-m2.1-lightning", "MiniMax M2.1 Lightning", "0.27", "2.33", "0.03", "0"),
    ("minimax-m2", "MiniMax M2", "0.27", "0.95", "0.03", "0"),
    ("minimax-m2.5", "MiniMax M2.5", "0.12", "0.95", "0.03", "0"),
    ("minimax-m2.5-lightning", "MiniMax M2.5 Lightning", "0.30", "2.40", "0.03", "0"),
    ("minimax-m2.7", "MiniMax M2.7", "0.30", "1.20", "0.06", "0.375"),
    ("minimax-m2.7-highspeed", "MiniMax M2.7 Highspeed", "0.60", "2.40", "0.06", "0.375"),
    # GLM 系列
    ("glm-4.7", "GLM-4.7", "0.39", "1.75", "0.04", "0"),
    ("glm-4.6", "GLM-4.6", "0.28", "1.11", "0.03", "0"),
    ("glm-5", "GLM-5", "0.72", "2.30", "0", "0"),
    ("glm-5.1", "GLM-5.1", "0.95", "3.15", "0", "0"),
    # MiMo 系列
    ("mimo-v2-flash", "MiMo V2 Flash", "0.09", "0.29", "0.009", "0"),
    ("mimo-v2-pro", "MiMo V2 Pro", "1", "3", "0", "0"),
    # Qwen 系列
    ("qwen3.6-plus", "Qwen3.6 Plus", "0.325", "1.95", "0", "0"),
    ("qwen3.5-plus", "Qwen3.5 Plus", "0.26", "1.56", "0", "0"),
    ("qwen3-max", "Qwen3 Max", "0.78", "3.90", "0", "0"),
    ("qwen3-235b-a22l", "Qwen3 235B-A22L", "0.70", "8.40", "0", "0"),
    ("qwen3-coder-plus", "Qwen3 Coder Plus", "0.65", "3.25", "0", "0"),
    ("qwen3-coder-flash", "Qwen3 Coder Flash", "0.195", "0.975", "0", "0"),
    ("qwen3-coder-next", "Qwen3 Coder Next", "0.12", "0.75", "0", "0"),
    ("qwq-plus", "QwQ Plus", "0.80", "2.40", "0", "0"),
    ("qwq-32b", "QwQ 32B", "0.20", "0.60", "0", "0"),
    ("qwen3-32b", "Qwen3 32B", "0.16", "0.64", "0", "0"),
    # Grok 系列
    ("grok-4.20-0309-reasoning", "Grok 4.20 Reasoning", "2", "6", "0.20", "0"),
    ("grok-4.20-0309-non-reasoning", "Grok 4.20", "2", "6", "0.20", "0"),
    ("grok-4-1-fast-reasoning", "Grok 4.1 Fast Reasoning", "0.20", "0.50", "0.05", "0"),
    ("grok-4-1-fast-non-reasoning", "Grok 4.1 Fast", "0.20", "0.50", "0.05", "0"),
    ("grok-4", "Grok 4", "3", "15", "0.75", "0"),
    ("grok-code-fast-1", "Grok Code Fast", "0.20", "1.50", "0.02", "0"),
    ("grok-3", "Grok 3", "3", "15", "0.75", "0"),
    ("grok-3-mini", "Grok 3 Mini", "0.25", "0.50", "0.075", "0"),
    # Mistral 系列
    ("codestral-2508", "Codestral", "0.30", "0.90", "0.03", "0"),
    ("devstral-small-1.1", "Devstral Small 1.1", "0.07", "0.28", "0.01", "0"),
    ("devstral-2-2512", "Devstral 2", "0.40", "0.90", "0.04", "0"),
    ("devstral-medium", "Devstral Medium", "0.40", "2", "0.04", "0"),
    ("mistral-large-3-2512", "Mistral Large 3", "0.50", "1.50", "0.05", "0"),
    ("mistral-medium-3.1", "Mistral Medium 3.1", "0.40", "2", "0.04", "0"),
    ("mistral-small-3.2-24b", "Mistral Small 3.2", "0.075", "0.20", "0.01", "0"),
    ("magistral-medium", "Magistral Medium", "2", "5", "0", "0"),
    # Cohere 系列
    ("command-a", "Cohere Command A", "2.50", "10", "0", "0"),
    ("command-r-plus", "Cohere Command R+", "2.50", "10", "0", "0"),
    ("command-r", "Cohere Command R", "0.15", "0.60", "0", "0"),
]


class PricingDB:
    """model_pricing 表的 CRUD 操作。每次查询新建连接，用完关闭。

    参数:
        db_path: config.db 路径
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._ensure_table()

    def _connect(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=3000")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _ensure_table(self):
        """幂等建表。表为空时导入种子数据。"""
        conn = self._connect()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS model_pricing (
                    model_id                        TEXT PRIMARY KEY,
                    display_name                    TEXT NOT NULL,
                    input_cost_per_million          TEXT NOT NULL,
                    output_cost_per_million         TEXT NOT NULL,
                    cache_read_cost_per_million     TEXT NOT NULL DEFAULT '0',
                    cache_creation_cost_per_million TEXT NOT NULL DEFAULT '0',
                    currency                        TEXT NOT NULL DEFAULT 'USD'
                                                    CHECK(currency IN ('USD', 'RMB'))
                )
            """)
            # 只在表为空时导入种子数据
            count = conn.execute("SELECT COUNT(*) FROM model_pricing").fetchone()[0]
            if count == 0:
                conn.executemany(
                    "INSERT INTO model_pricing (model_id, display_name, "
                    "input_cost_per_million, output_cost_per_million, "
                    "cache_read_cost_per_million, cache_creation_cost_per_million) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    _SEED_PRICING,
                )
                conn.commit()
                logging.info(f"[PricingDB] 已导入 {len(_SEED_PRICING)} 条种子定价数据")
        finally:
            conn.close()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest test/test_pricing_manager.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add proxy/pricing_manager.py test/test_pricing_manager.py
git commit -m "feat: PricingDB 模块骨架 — 建表 + 种子数据导入"
```

---

### Task 2: PricingDB — list_pricings / get_pricing

**Files:**
- Modify: `proxy/pricing_manager.py`
- Modify: `test/test_pricing_manager.py`

- [ ] **Step 1: 写失败测试 — list_pricings + get_pricing**

在 `test/test_pricing_manager.py` 末尾新增：

```python
class TestPricingDBListAndGet(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "config.db"
        from proxy.pricing_manager import PricingDB
        self.db = PricingDB(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_list_pricings_returns_all(self):
        result = self.db.list_pricings()
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)
        self.assertIn("model_id", result[0])
        self.assertIn("currency", result[0])

    def test_list_pricings_with_search(self):
        result = self.db.list_pricings(search="claude")
        self.assertGreater(len(result), 0)
        for r in result:
            self.assertIn("claude", r["model_id"].lower() + r["display_name"].lower())

    def test_list_pricings_search_no_match(self):
        result = self.db.list_pricings(search="nonexistent_model_xyz")
        self.assertEqual(len(result), 0)

    def test_get_pricing_existing(self):
        result = self.db.get_pricing("claude-sonnet-4-6-20260217")
        self.assertIsNotNone(result)
        self.assertEqual(result["model_id"], "claude-sonnet-4-6-20260217")
        self.assertEqual(result["input_cost_per_million"], "3")

    def test_get_pricing_nonexistent(self):
        result = self.db.get_pricing("nonexistent_model")
        self.assertIsNone(result)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest test/test_pricing_manager.py::TestPricingDBListAndGet -v`
Expected: FAIL — `AttributeError: 'PricingDB' object has no attribute 'list_pricings'`

- [ ] **Step 3: 实现 list_pricings + get_pricing**

在 `proxy/pricing_manager.py` 的 `PricingDB` 类中，`_ensure_table` 方法之后新增：

```python
    # ─── 查询方法 ───

    def list_pricings(self, search: Optional[str] = None) -> list:
        """列出所有定价，可选按模型名/显示名模糊搜索。"""
        conn = self._connect()
        try:
            if search:
                rows = conn.execute(
                    "SELECT * FROM model_pricing "
                    "WHERE model_id LIKE ? OR display_name LIKE ? "
                    "ORDER BY model_id",
                    (f"%{search}%", f"%{search}%"),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM model_pricing ORDER BY model_id"
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_pricing(self, model_id: str) -> Optional[dict]:
        """获取单个模型定价。"""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM model_pricing WHERE model_id = ?", (model_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest test/test_pricing_manager.py -v`
Expected: all passed

- [ ] **Step 5: 提交**

```bash
git add proxy/pricing_manager.py test/test_pricing_manager.py
git commit -m "feat: PricingDB list_pricings / get_pricing + 搜索"
```

---

### Task 3: PricingDB — add_pricing / update_pricing / delete_pricing

**Files:**
- Modify: `proxy/pricing_manager.py`
- Modify: `test/test_pricing_manager.py`

- [ ] **Step 1: 写失败测试 — CRUD 操作**

在 `test/test_pricing_manager.py` 末尾新增：

```python
class TestPricingDBCRUD(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "config.db"
        from proxy.pricing_manager import PricingDB
        self.db = PricingDB(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_add_pricing(self):
        model_id = self.db.add_pricing({
            "model_id": "test-model-v1",
            "display_name": "Test Model V1",
            "input_cost_per_million": "1.5",
            "output_cost_per_million": "6",
            "cache_read_cost_per_million": "0.15",
            "cache_creation_cost_per_million": "1.5",
            "currency": "RMB",
        })
        self.assertEqual(model_id, "test-model-v1")
        result = self.db.get_pricing("test-model-v1")
        self.assertEqual(result["currency"], "RMB")
        self.assertEqual(result["input_cost_per_million"], "1.5")

    def test_add_pricing_default_currency(self):
        model_id = self.db.add_pricing({
            "model_id": "test-usd-model",
            "display_name": "Test USD",
            "input_cost_per_million": "2",
            "output_cost_per_million": "8",
        })
        result = self.db.get_pricing(model_id)
        self.assertEqual(result["currency"], "USD")

    def test_add_pricing_duplicate_fails(self):
        self.db.add_pricing({
            "model_id": "dup-model",
            "display_name": "Dup",
            "input_cost_per_million": "1",
            "output_cost_per_million": "2",
        })
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.add_pricing({
                "model_id": "dup-model",
                "display_name": "Dup Again",
                "input_cost_per_million": "3",
                "output_cost_per_million": "4",
            })

    def test_add_pricing_invalid_currency(self):
        with self.assertRaises(ValueError):
            self.db.add_pricing({
                "model_id": "bad-currency",
                "display_name": "Bad",
                "input_cost_per_million": "1",
                "output_cost_per_million": "2",
                "currency": "EUR",
            })

    def test_add_pricing_invalid_price(self):
        with self.assertRaises(ValueError):
            self.db.add_pricing({
                "model_id": "bad-price",
                "display_name": "Bad",
                "input_cost_per_million": "abc",
                "output_cost_per_million": "2",
            })

    def test_update_pricing(self):
        self.db.add_pricing({
            "model_id": "update-test",
            "display_name": "Before",
            "input_cost_per_million": "1",
            "output_cost_per_million": "2",
        })
        ok = self.db.update_pricing("update-test", {
            "display_name": "After",
            "input_cost_per_million": "5",
            "currency": "RMB",
        })
        self.assertTrue(ok)
        result = self.db.get_pricing("update-test")
        self.assertEqual(result["display_name"], "After")
        self.assertEqual(result["input_cost_per_million"], "5")
        self.assertEqual(result["currency"], "RMB")
        self.assertEqual(result["output_cost_per_million"], "2")  # 未修改的保留

    def test_update_pricing_nonexistent(self):
        ok = self.db.update_pricing("nonexistent", {"display_name": "X"})
        self.assertFalse(ok)

    def test_delete_pricing(self):
        self.db.add_pricing({
            "model_id": "delete-test",
            "display_name": "To Delete",
            "input_cost_per_million": "1",
            "output_cost_per_million": "2",
        })
        ok = self.db.delete_pricing("delete-test")
        self.assertTrue(ok)
        self.assertIsNone(self.db.get_pricing("delete-test"))

    def test_delete_pricing_nonexistent(self):
        ok = self.db.delete_pricing("nonexistent")
        self.assertFalse(ok)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest test/test_pricing_manager.py::TestPricingDBCRUD -v`
Expected: FAIL — `AttributeError`

- [ ] **Step 3: 实现 add_pricing / update_pricing / delete_pricing**

在 `proxy/pricing_manager.py` 的 `PricingDB` 类中，`get_pricing` 方法之后新增：

```python
    # ─── 写入方法 ───

    def add_pricing(self, data: dict) -> str:
        """新增定价记录。返回 model_id。"""
        model_id = data.get("model_id", "").strip()
        if not model_id:
            raise ValueError("model_id 不能为空")

        currency = data.get("currency", "USD")
        if currency not in ("USD", "RMB"):
            raise ValueError(f"currency 必须为 USD 或 RMB，收到: {currency}")

        for field in ("input_cost_per_million", "output_cost_per_million"):
            val = data.get(field, "")
            try:
                float(val)
            except (ValueError, TypeError):
                raise ValueError(f"{field} 必须为合法数字，收到: {val}")

        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO model_pricing (model_id, display_name, "
                "input_cost_per_million, output_cost_per_million, "
                "cache_read_cost_per_million, cache_creation_cost_per_million, currency) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    model_id,
                    data["display_name"],
                    data["input_cost_per_million"],
                    data["output_cost_per_million"],
                    data.get("cache_read_cost_per_million", "0"),
                    data.get("cache_creation_cost_per_million", "0"),
                    currency,
                ),
            )
            conn.commit()
            return model_id
        finally:
            conn.close()

    def update_pricing(self, model_id: str, data: dict) -> bool:
        """更新定价记录。只修改 data 中提供的字段。返回是否成功。"""
        existing = self.get_pricing(model_id)
        if not existing:
            return False

        if "currency" in data and data["currency"] not in ("USD", "RMB"):
            raise ValueError(f"currency 必须为 USD 或 RMB，收到: {data['currency']}")

        for field in ("input_cost_per_million", "output_cost_per_million",
                      "cache_read_cost_per_million", "cache_creation_cost_per_million"):
            if field in data:
                try:
                    float(data[field])
                except (ValueError, TypeError):
                    raise ValueError(f"{field} 必须为合法数字，收到: {data[field]}")

        updatable = [
            "display_name", "input_cost_per_million", "output_cost_per_million",
            "cache_read_cost_per_million", "cache_creation_cost_per_million", "currency",
        ]
        sets = []
        vals = []
        for field in updatable:
            if field in data:
                sets.append(f"{field} = ?")
                vals.append(data[field])
        if not sets:
            return True

        vals.append(model_id)
        conn = self._connect()
        try:
            conn.execute(
                f"UPDATE model_pricing SET {', '.join(sets)} WHERE model_id = ?",
                vals,
            )
            conn.commit()
            return True
        finally:
            conn.close()

    def delete_pricing(self, model_id: str) -> bool:
        """删除定价记录。返回是否成功。"""
        conn = self._connect()
        try:
            cursor = conn.execute(
                "DELETE FROM model_pricing WHERE model_id = ?", (model_id,)
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest test/test_pricing_manager.py -v`
Expected: all passed

- [ ] **Step 5: 提交**

```bash
git add proxy/pricing_manager.py test/test_pricing_manager.py
git commit -m "feat: PricingDB add/update/delete + 校验"
```

---

### Task 4: _CostCalculator 改造 + 缓存失效

**Files:**
- Modify: `stats_service.py` (改造 _CostCalculator + StatsService 构造函数)
- Modify: `test/test_stats_service.py` (适配构造函数变更)
- Modify: `server.py` (移除 cc_switch_db_path)

- [ ] **Step 1: 改造 _CostCalculator**

在 `stats_service.py` 中：

1. 顶部新增导入：`from proxy.pricing_manager import PricingDB`

2. 替换整个 `_CostCalculator` 类（约在 448-536 行）：

```python
class _CostCalculator:
    """成本计算器 — 从 config.db 加载 model_pricing 表（通过 PricingDB），内置 TTL 缓存。

    USD 价格自动 × 7 转为人民币，RMB 价格原样使用。
    calculate() 统一返回人民币金额。

    Args:
        config_db_path: config.db 路径
    """

    EXCHANGE_RATE = 7  # USD → RMB

    def __init__(self, config_db_path: str | Path) -> None:
        self._pricing_db = PricingDB(Path(config_db_path))
        self._cache_ttl = 300  # 缓存 5 分钟
        self._pricing_cache: dict = {}
        self._pricing_cache_time: float = 0.0

    # ─── 定价加载 ───

    def get_pricing(self) -> dict:
        """从 config.db 加载 model_pricing 表（带缓存），自动换算为人民币。

        Returns:
            {model_id: {input_cost, output_cost, cache_read_cost, cache_creation_cost}}
            价格单位：RMB / 1M tokens
        """
        if time.time() - self._pricing_cache_time < self._cache_ttl and self._pricing_cache:
            return self._pricing_cache

        try:
            rows = self._pricing_db.list_pricings()
            pricing = {}
            for r in rows:
                rate = 1 if r["currency"] == "RMB" else self.EXCHANGE_RATE
                pricing[r["model_id"]] = {
                    "input_cost": float(r["input_cost_per_million"]) * rate,
                    "output_cost": float(r["output_cost_per_million"]) * rate,
                    "cache_read_cost": float(r["cache_read_cost_per_million"]) * rate,
                    "cache_creation_cost": float(r["cache_creation_cost_per_million"]) * rate,
                }
            self._pricing_cache = pricing
            self._pricing_cache_time = time.time()
            return pricing
        except Exception as e:
            print(f"Error reading model pricing: {e}")
            return {}

    def invalidate_cache(self):
        """主动失效缓存，供定价修改后调用。"""
        self._pricing_cache = {}
        self._pricing_cache_time = 0.0

    # ─── 成本计算 ───

    def calculate(
        self,
        model: str,
        input_tokens: int | float | None,
        output_tokens: int | float | None,
        cache_read_tokens: int | float | None,
        cache_write_tokens: int | float | None,
    ) -> float:
        """根据模型计费规则计算成本（人民币）。

        Returns:
            总成本（人民币，float）。模型无定价时返回 0。
        """
        pricing = self.get_pricing()

        if not pricing or model not in pricing:
            return 0

        p = pricing[model]

        input_cost = (input_tokens or 0) / 1_000_000 * p["input_cost"]
        output_cost = (output_tokens or 0) / 1_000_000 * p["output_cost"]
        cache_read_cost = (cache_read_tokens or 0) / 1_000_000 * p["cache_read_cost"]
        cache_write_cost = (cache_write_tokens or 0) / 1_000_000 * p["cache_creation_cost"]

        return input_cost + output_cost + cache_read_cost + cache_write_cost
```

3. 修改 `StatsService.__init__`：移除 `cc_switch_db_path` 参数，改用 `config_db_path`

```python
    def __init__(
        self,
        access_log_db_path: str,
        config_db_path: str,
        state_db_path: str,
    ) -> None:
        self.access_log_db_path = Path(access_log_db_path)
        self.config_db_path = Path(config_db_path)
        self.state_db_path = Path(state_db_path)

        self._upstream_resolver = _UpstreamResolver(self.config_db_path)
```

4. 修改 `_get_calculator` 方法：

```python
    def _get_calculator(self) -> _CostCalculator:
        """懒加载获取 _CostCalculator 单例。"""
        if not hasattr(self, "_cost_calculator"):
            self._cost_calculator = _CostCalculator(self.config_db_path)
        return self._cost_calculator

    def invalidate_pricing_cache(self):
        """失效定价缓存，供 API 层定价修改后调用。"""
        if hasattr(self, "_cost_calculator"):
            self._cost_calculator.invalidate_cache()
```

- [ ] **Step 2: 修改 server.py — 移除 cc_switch_db_path**

1. 删除 `CC_SWITCH_DB_PATH` 常量（第 181 行）：
   ```python
   # 删除: CC_SWITCH_DB_PATH = os.path.expanduser("~/.cc-switch/cc-switch.db")
   ```

2. 修改 `_get_stats_service()`（约 189-201 行）：
   ```python
   def _get_stats_service():
       """懒加载获取 StatsService 单例。"""
       global _stats_service_instance
       if _stats_service_instance is None:
           from stats_service import StatsService
           _stats_service_instance = StatsService(
               access_log_db_path=str(ACCESS_LOG_DB_PATH),
               config_db_path=str(CONFIG_DB_PATH),
               state_db_path=STATE_DB_PATH,
           )
       return _stats_service_instance
   ```

- [ ] **Step 3: 新增 _CostCalculator 单元测试**

在 `test/test_stats_service.py` 中新增测试类：

```python
class TestCostCalculatorCNY(unittest.TestCase):
    """_CostCalculator 币种换算和缓存失效测试。"""

    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "config.db"
        from proxy.pricing_manager import PricingDB
        PricingDB(self.db_path)  # 建表 + 种子数据

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_usd_pricing_converted_to_cny(self):
        """USD 定价应自动 × 7 换算为人民币。"""
        from stats_service import _CostCalculator
        calc = _CostCalculator(self.db_path)
        pricing = calc.get_pricing()
        # claude-sonnet-4-6: input=3 USD → 应返回 3*7=21 RMB
        self.assertIn("claude-sonnet-4-6-20260217", pricing)
        self.assertAlmostEqual(pricing["claude-sonnet-4-6-20260217"]["input_cost"], 21.0)

    def test_calculate_returns_cny(self):
        """calculate() 返回人民币金额。"""
        from stats_service import _CostCalculator
        calc = _CostCalculator(self.db_path)
        # 1M input tokens × $3/1M = $3 → ¥21
        cost = calc.calculate("claude-sonnet-4-6-20260217", 1_000_000, 0, 0, 0)
        self.assertAlmostEqual(cost, 21.0)

    def test_unknown_model_returns_zero(self):
        """未知模型返回 0。"""
        from stats_service import _CostCalculator
        calc = _CostCalculator(self.db_path)
        cost = calc.calculate("nonexistent-model", 1000, 1000, 0, 0)
        self.assertEqual(cost, 0)

    def test_invalidate_cache(self):
        """invalidate_cache 后下次 get_pricing 重新加载。"""
        from stats_service import _CostCalculator
        calc = _CostCalculator(self.db_path)
        pricing1 = calc.get_pricing()
        calc.invalidate_cache()
        # 验证缓存已清空
        self.assertEqual(calc._pricing_cache, {})
        self.assertEqual(calc._pricing_cache_time, 0.0)
        # 再次获取应重新加载
        pricing2 = calc.get_pricing()
        self.assertEqual(len(pricing1), len(pricing2))

    def test_invalidate_pricing_cache_on_stats_service(self):
        """StatsService.invalidate_pricing_cache() 委托到 _CostCalculator。"""
        from stats_service import StatsService
        service = StatsService(
            access_log_db_path=str(Path(self.tmpdir.name) / "access_log.db"),
            config_db_path=str(self.db_path),
            state_db_path=str(Path(self.tmpdir.name) / "state.db"),
        )
        # 先触发 calculator 懒加载
        calc = service._get_calculator()
        calc.get_pricing()
        self.assertGreater(calc._pricing_cache_time, 0)
        # 失效
        service.invalidate_pricing_cache()
        self.assertEqual(calc._pricing_cache_time, 0.0)
```

- [ ] **Step 4: 更新 test_stats_service.py 构造函数**

所有创建 `StatsService` 的地方，移除 `cc_switch_db_path` 参数。全局搜索替换：

```python
# 旧:
StatsService(access_log_db_path=..., config_db_path=..., state_db_path=..., cc_switch_db_path=...)
# 新:
StatsService(access_log_db_path=..., config_db_path=..., state_db_path=...)
```

- [ ] **Step 5: 运行全量测试确认通过**

Run: `python3 -m pytest test/ -q`
Expected: all passed

- [ ] **Step 6: 提交**

```bash
git add stats_service.py server.py test/test_stats_service.py
git commit -m "feat: _CostCalculator 改用 PricingDB + 缓存失效 + 移除 cc_switch_db_path"
```

---

### Task 5: estimated_cost_usd → estimated_cost_cny 全局重命名

**Files:**
- Modify: `stats_service.py`
- Modify: `test/test_stats_service.py`

- [ ] **Step 1: 重命名 stats_service.py 中的字典键和赋值**

替换规则：
- **替换**：所有字典键 `"estimated_cost_usd"` → `"estimated_cost_cny"`（约 10 处）
- **替换**：`round(cost, 4)` → `round(cost, 6)`、`round(total_cost, 4)` → `round(total_cost, 6)`
- **替换**：`round(calculator.calculate(...), 6)` 中已有的 6 保留不动
- **保留**：`# 5. 计算 total_tokens 和 estimated_cost_usd` 等注释中的字段名也一并替换为 `estimated_cost_cny`

涉及行（参考）：
- 1017: `m["estimated_cost_cny"] = round(calculator.calculate(...), 6)`
- 1073: `row_dict["estimated_cost_cny"] = calculator.calculate(...)`
- 1084: `rec["estimated_cost_cny"] = calculator.calculate(...)`
- 1114: `total_tokens, estimated_cost_cny}]}`
- 1185: `# 5. 计算 total_tokens 和 estimated_cost_cny`
- 1229: `"estimated_cost_cny": round(cost, 6),`
- 1232-1233: `x["estimated_cost_cny"], reverse=True)`
- 1266: `result["estimated_cost_cny"] = round(total_cost, 6)`
- 1314-1333: 同上

- [ ] **Step 2: 重命名 test_stats_service.py**

替换规则：
- **替换**：所有 `estimated_cost_usd` → `estimated_cost_cny`（字典键、断言、注释）
- **保留**：测试方法名如 `test_sorted_by_estimated_cost_usd_desc` **不重命名**（不影响运行，重命名反而增加 diff 噪音）
- **替换**：`assertIn("estimated_cost_cny", ...)` 等断言中的字段名

- [ ] **Step 3: 运行全量测试确认通过**

Run: `python3 -m pytest test/ -q`
Expected: all passed

- [ ] **Step 4: 提交**

```bash
git add stats_service.py test/test_stats_service.py
git commit -m "refactor: estimated_cost_usd → estimated_cost_cny + 6位小数精度"
```

---

### Task 6: fetch_trend 成本计算修复

**Files:**
- Modify: `stats_service.py`
- Modify: `test/test_stats_service.py`

- [ ] **Step 1: 写失败测试 — fetch_trend 应包含 estimated_cost_cny**

在 `test/test_stats_service.py` 中找到测试 `fetch_trend` 的测试类，新增：

```python
    def test_fetch_trend_includes_estimated_cost_cny(self):
        """趋势数据应包含 estimated_cost_cny 字段。"""
        result = self.service.fetch_trend("week")
        self.assertGreater(len(result), 0)
        for point in result:
            self.assertIn("estimated_cost_cny", point)
            self.assertIsInstance(point["estimated_cost_cny"], (int, float))
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest test/test_stats_service.py -k "test_fetch_trend_includes_estimated_cost_cny" -v`
Expected: FAIL — `AssertionError: 'estimated_cost_cny' not found`

- [ ] **Step 3: 修改 fetch_trend — 逐点计算成本**

找到 `stats_service.py` 中的 `fetch_trend` 方法（约 1236 行），修改为：

```python
    def fetch_trend(self, period: str) -> list:
        """获取时间趋势数据，合并 proxy + sessions 双源。逐点计算成本。"""
        dao = self._get_dao()
        session_dao = self._get_session_dao()
        proxy_trend = dao.aggregate_trend(period)
        session_trend = session_dao.aggregate_trend(period)
        merged = _Merger.merge_trend_lists(proxy_trend, session_trend)
        merged.sort(key=lambda x: x.get("date", ""))

        # 逐点计算成本
        calculator = self._get_calculator()
        for point in merged:
            point["estimated_cost_cny"] = calculator.calculate(
                model=point.get("model", ""),
                input_tokens=point.get("input_tokens", 0),
                output_tokens=point.get("output_tokens", 0),
                cache_read_tokens=point.get("cache_read_tokens", 0),
                cache_write_tokens=point.get("cache_write_tokens", 0),
            )

        return merged
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest test/test_stats_service.py -k "test_fetch_trend" -v`
Expected: PASS

- [ ] **Step 5: 运行全量测试**

Run: `python3 -m pytest test/ -q`
Expected: all passed

- [ ] **Step 6: 提交**

```bash
git add stats_service.py test/test_stats_service.py
git commit -m "fix: fetch_trend 逐点计算 estimated_cost_cny"
```

---

### Task 7: server.py — /api/pricing/* 路由

**Files:**
- Modify: `server.py`

- [ ] **Step 1: 添加 PricingDB 导入和辅助函数**

在 `server.py` 顶部导入区域新增：

```python
from proxy.pricing_manager import PricingDB
```

在 `get_config_db()` 之后新增：

```python
def get_pricing_db():
    return PricingDB(CONFIG_DB_PATH)
```

- [ ] **Step 2: 添加 GET /api/pricing 路由**

在 `server.py` 的 `do_GET` 方法中，找到现有 API 路由区域，新增：

```python
        # ===== 计费表 API =====
        pricing_m = re.match(r"/api/pricing/?$", path)
        if pricing_m:
            search = params.get("search", [None])[0]
            db = get_pricing_db()
            result = db.list_pricings(search=search)
            return json_response(self, {"pricings": result})

        pricing_detail_m = re.match(r"/api/pricing/([^/]+)$", path)
        if pricing_detail_m:
            db = get_pricing_db()
            result = db.get_pricing(pricing_detail_m.group(1))
            if not result:
                return json_response(self, {"error": "Not found"}, 404)
            return json_response(self, result)
```

- [ ] **Step 3: 添加 POST /api/pricing 路由**

在 `do_POST` 方法中，找到 API 路由区域（在 upstreams 路由之后），新增：

```python
        # ===== 计费表 API =====
        if path == "/api/pricing":
            data = _read_json(self)
            if not data:
                return
            db = get_pricing_db()
            try:
                model_id = db.add_pricing(data)
                _get_stats_service().invalidate_pricing_cache()
                return json_response(self, {"id": model_id, "message": "Created"}, 201)
            except (sqlite3.IntegrityError, ValueError) as e:
                return json_response(self, {"error": str(e)}, 400)
```

- [ ] **Step 4: 添加 PUT /api/pricing/{model_id} 路由**

在 `do_PUT` 方法中新增：

```python
        pricing_m = re.match(r"/api/pricing/([^/]+)$", path)
        if pricing_m:
            data = _read_json(self)
            if not data:
                return
            db = get_pricing_db()
            try:
                ok = db.update_pricing(pricing_m.group(1), data)
                if not ok:
                    return json_response(self, {"error": "Not found"}, 404)
                _get_stats_service().invalidate_pricing_cache()
                return json_response(self, {"message": "Updated"})
            except ValueError as e:
                return json_response(self, {"error": str(e)}, 400)
```

- [ ] **Step 5: 添加 DELETE /api/pricing/{model_id} 路由**

在 `do_DELETE` 方法中新增：

```python
        pricing_m = re.match(r"/api/pricing/([^/]+)$", path)
        if pricing_m:
            db = get_pricing_db()
            ok = db.delete_pricing(pricing_m.group(1))
            if not ok:
                return json_response(self, {"error": "Not found"}, 404)
            _get_stats_service().invalidate_pricing_cache()
            return json_response(self, {"message": "Deleted"})
```

- [ ] **Step 6: 运行全量测试**

Run: `python3 -m pytest test/ -q`
Expected: all passed

- [ ] **Step 7: 重启服务并手动验证 API**

```bash
./server.sh restart
curl -s http://127.0.0.1:18742/api/pricing | python3 -m json.tool | head -20
curl -s http://127.0.0.1:18742/api/pricing?search=claude | python3 -m json.tool | head -10
```

- [ ] **Step 8: 提交**

```bash
git add server.py
git commit -m "feat: /api/pricing/* CRUD 路由 + 缓存失效联动"
```

---

### Task 8: 前端 — pricing.js + pricing.css

**Files:**
- Create: `static/js/pages/pricing.js`
- Create: `static/css/pricing.css`

- [ ] **Step 1: 创建 pricing.css**

参考现有 `css/models.css` 的样式风格，创建 `static/css/pricing.css`：

```css
/* 计费表页面样式 */
.pricing-toolbar {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px 20px;
    border-bottom: 1px solid hsl(var(--border));
}
.pricing-toolbar .search-box {
    flex: 0 1 320px;
}
.pricing-count {
    color: hsl(var(--muted));
    font-size: 13px;
    white-space: nowrap;
}
#pricing-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
}
#pricing-table th {
    padding: 10px 12px;
    text-align: left;
    font-weight: 600;
    color: hsl(var(--muted));
    border-bottom: 2px solid hsl(var(--border));
    white-space: nowrap;
}
#pricing-table td {
    padding: 8px 12px;
    border-bottom: 1px solid hsl(var(--border));
    vertical-align: middle;
}
#pricing-table tbody tr:hover {
    background: hsl(var(--accent) / 0.05);
}
.cell-price {
    font-family: monospace;
    text-align: right;
    white-space: nowrap;
}
.badge-usd {
    background: hsl(220 70% 55% / 0.15);
    color: hsl(220 70% 60%);
}
.badge-rmb {
    background: hsl(150 60% 40% / 0.15);
    color: hsl(150 60% 45%);
}
```

- [ ] **Step 2: 创建 pricing.js**

```javascript
import { api, escHtml, showModal, closeModal, bus } from '../core.js';

const EXCHANGE_RATE = 7;

function formatCny(value, currency) {
  const rmb = currency === 'RMB' ? value : value * EXCHANGE_RATE;
  return '¥' + parseFloat(rmb).toFixed(6);
}

export async function loadPricingPage() {
  const container = document.getElementById('page-pricing');
  if (!container) return;
  if (container.dataset.loaded) { await loadPricingTable(); return; }
  container.dataset.loaded = '1';
  container.innerHTML = `
    <div class="pricing-toolbar">
      <div class="search-box">
        <input type="text" id="pricing-search" placeholder="搜索模型名 / 显示名...">
      </div>
      <span class="pricing-count" id="pricing-count"></span>
      <button class="btn btn-primary" style="margin-left:auto" onclick="showPricingModal()">＋ 新增定价</button>
    </div>
    <div style="overflow-x:auto">
      <table id="pricing-table">
        <thead>
          <tr>
            <th>模型 ID</th>
            <th>显示名</th>
            <th style="text-align:right">输入价格</th>
            <th style="text-align:right">输出价格</th>
            <th style="text-align:right">缓存读</th>
            <th style="text-align:right">缓存写</th>
            <th>币种</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody id="pricing-tbody"></tbody>
      </table>
    </div>
  `;

  document.getElementById('pricing-search').addEventListener('keyup', loadPricingTable);
  await loadPricingTable();
}

export function initPricingPage() {}

async function loadPricingTable() {
  const search = document.getElementById('pricing-search')?.value?.trim() || '';
  const url = search ? `/api/pricing?search=${encodeURIComponent(search)}` : '/api/pricing';
  const data = await api(url);
  const items = data.pricings || [];
  document.getElementById('pricing-count').textContent = items.length + ' 条定价';

  const tbody = document.getElementById('pricing-tbody');
  tbody.innerHTML = items.map(p => {
    const currencyBadge = p.currency === 'RMB'
      ? '<span class="badge badge-rmb">RMB</span>'
      : '<span class="badge badge-usd">USD</span>';
    const mid = escHtml(p.model_id).replace(/'/g, "\\'");
    return `<tr>
      <td style="font-family:monospace">${escHtml(p.model_id)}</td>
      <td>${escHtml(p.display_name)}</td>
      <td class="cell-price">${formatCny(p.input_cost_per_million, p.currency)}</td>
      <td class="cell-price">${formatCny(p.output_cost_per_million, p.currency)}</td>
      <td class="cell-price">${formatCny(p.cache_read_cost_per_million, p.currency)}</td>
      <td class="cell-price">${formatCny(p.cache_creation_cost_per_million, p.currency)}</td>
      <td>${currencyBadge}</td>
      <td>
        <button class="btn btn-secondary btn-sm" onclick="editPricing('${mid}')">编辑</button>
        <button class="btn btn-danger btn-sm" onclick="deletePricing('${mid}')">删除</button>
      </td>
    </tr>`;
  }).join('');
}

async function editPricing(modelId) {
  const data = await api(`/api/pricing/${encodeURIComponent(modelId)}`);
  if (data.error) return alert(data.error);
  showPricingModal(data);
}

async function deletePricing(modelId) {
  if (!confirm(`确定删除模型 ${modelId} 的定价？`)) return;
  const result = await api(`/api/pricing/${encodeURIComponent(modelId)}`, { method: 'DELETE' });
  if (result.error) return alert(result.error);
  await loadPricingTable();
}

function showPricingModal(existing = null) {
  const isEdit = !!existing;
  const title = isEdit ? '编辑定价' : '新增定价';
  const content = `
    <div class="form-group"><label class="form-label">模型 ID</label>
      <input class="form-input" id="pm-model-id" value="${escHtml(existing?.model_id || '')}" ${isEdit ? 'disabled' : ''}></div>
    <div class="form-group"><label class="form-label">显示名</label>
      <input class="form-input" id="pm-display-name" value="${escHtml(existing?.display_name || '')}"></div>
    <div class="form-group"><label class="form-label">输入价格 / 1M tokens</label>
      <input class="form-input" id="pm-input" type="number" step="0.000001" value="${existing?.input_cost_per_million || ''}"></div>
    <div class="form-group"><label class="form-label">输出价格 / 1M tokens</label>
      <input class="form-input" id="pm-output" type="number" step="0.000001" value="${existing?.output_cost_per_million || ''}"></div>
    <div class="form-group"><label class="form-label">缓存读价格 / 1M</label>
      <input class="form-input" id="pm-cache-read" type="number" step="0.000001" value="${existing?.cache_read_cost_per_million || '0'}"></div>
    <div class="form-group"><label class="form-label">缓存写价格 / 1M</label>
      <input class="form-input" id="pm-cache-write" type="number" step="0.000001" value="${existing?.cache_creation_cost_per_million || '0'}"></div>
    <div class="form-group"><label class="form-label">币种</label>
      <select class="form-input" id="pm-currency">
        <option value="USD" ${existing?.currency !== 'RMB' ? 'selected' : ''}>USD (美元)</option>
        <option value="RMB" ${existing?.currency === 'RMB' ? 'selected' : ''}>RMB (人民币)</option>
      </select></div>
  `;
  const editId = isEdit ? `'${escHtml(existing.model_id).replace(/'/g, "\\'")}'` : 'null';
  const footer = `<button class="btn btn-secondary" onclick="closeModal()">取消</button><button class="btn btn-primary" onclick="savePricing(${editId})">保存</button>`;
  showModal(title, content, footer);
}

async function savePricing(editModelId) {
  const payload = {
    model_id: document.getElementById('pm-model-id').value.trim(),
    display_name: document.getElementById('pm-display-name').value.trim(),
    input_cost_per_million: document.getElementById('pm-input').value,
    output_cost_per_million: document.getElementById('pm-output').value,
    cache_read_cost_per_million: document.getElementById('pm-cache-read').value || '0',
    cache_creation_cost_per_million: document.getElementById('pm-cache-write').value || '0',
    currency: document.getElementById('pm-currency').value,
  };
  if (!payload.model_id || !payload.display_name || !payload.input_cost_per_million || !payload.output_cost_per_million) {
    alert('模型 ID、显示名、输入/输出价格为必填');
    return;
  }
  const url = editModelId
    ? `/api/pricing/${encodeURIComponent(editModelId)}`
    : '/api/pricing';
  const method = editModelId ? 'PUT' : 'POST';
  const result = await api(url, { method, body: JSON.stringify(payload) });
  if (result.error) { alert(result.error); return; }
  closeModal();
  await loadPricingTable();
}

// 挂载到 window（ES Module onclick 需要）
window.showPricingModal = showPricingModal;
window.editPricing = editPricing;
window.deletePricing = deletePricing;
window.savePricing = savePricing;
```

- [ ] **Step 3: 提交**

```bash
git add static/js/pages/pricing.js static/css/pricing.css
git commit -m "feat: 计费表前端页面 pricing.js + pricing.css"
```

---

### Task 9: 前端集成 — index.html + app.js

**Files:**
- Modify: `static/index.html`
- Modify: `static/js/app.js`

- [ ] **Step 1: 修改 index.html**

1. 在 `<head>` 中 CSS link 列表末尾新增：
```html
<link rel="stylesheet" href="css/pricing.css">
```

2. 在导航 Tab 按钮区域，在"路由映射"按钮之后新增：
```html
      <button class="nav-tab" data-page="pricing">
        <span>💰</span> 计费表
      </button>
```

3. 在 `#page-routes` 之后新增页面容器：
```html
  <!-- 计费表页面 -->
  <div id="page-pricing" class="main-content hidden">
  </div>
```

- [ ] **Step 2: 修改 app.js — 注册 pricing 页面**

1. 顶部新增静态导入（与现有页面一致）：
```javascript
import { loadPricingPage, initPricingPage } from './pages/pricing.js';
```

2. 在 `pageLoaders` 映射中新增：
```javascript
pageLoaders.pricing = loadPricingPage;
```

3. 在 `initXxxPage()` 调用区域新增：
```javascript
initPricingPage();
```

4. 在 Tab 点击事件处理器中，新增 visibility toggle 和加载调用：
```javascript
document.getElementById('page-pricing').classList.toggle('hidden', page !== 'pricing');
// ...
if (page === 'pricing') loadPricingPage();
```

5. 在 `core.js` 的 `pageLoaders` 初始对象中新增 `pricing: null` 键（如需要）。

- [ ] **Step 3: 重启服务并用 Playwright 验证**

```bash
./server.sh restart
```

用 Playwright MCP 验证：
1. 导航到 `http://127.0.0.1:18742`
2. 点击"💰 计费表" Tab
3. 确认表格加载、搜索可用、新增/编辑/删除弹窗正常

- [ ] **Step 4: 提交**

```bash
git add static/index.html static/js/app.js
git commit -m "feat: 计费表 Tab 注册 + 页面集成"
```

---

### Task 10: Token 统计页适配 — $ → ¥ + toFixed(6)

**Files:**
- Modify: `static/js/pages/tokens.js`

- [ ] **Step 1: 逐个替换 tokens.js 中的费用显示**

**8 处 `estimated_cost_usd` → `estimated_cost_cny`：**
- L87: `$${(stats.estimated_cost_usd || 0).toFixed(4)}` → `¥${(stats.estimated_cost_cny || 0).toFixed(6)}`
- L178: `d.estimated_cost_usd || 0` → `d.estimated_cost_cny || 0`
- L317: `(d.estimated_cost_usd || 0) / costYMax` → `(d.estimated_cost_cny || 0) / costYMax`
- L383: `'$' + data.estimated_cost_usd.toFixed(4)` → `'¥' + data.estimated_cost_cny.toFixed(6)`
- L476: `$${m.estimated_cost_usd.toFixed(4)}` → `¥${m.estimated_cost_cny.toFixed(6)}`
- L534: `$${(r.estimated_cost_usd || 0).toFixed(4)}` → `¥${(r.estimated_cost_cny || 0).toFixed(6)}`
- L705: `$${(r.estimated_cost_usd || 0).toFixed(4)}` → `¥${(r.estimated_cost_cny || 0).toFixed(6)}`
- L811: `(b.estimated_cost_usd || 0) - (a.estimated_cost_usd || 0)` → `(b.estimated_cost_cny || 0) - (a.estimated_cost_cny || 0)`
- L856: `$${(u.estimated_cost_usd || 0).toFixed(4)}` → `¥${(u.estimated_cost_cny || 0).toFixed(6)}`

注意：替换后需确认 `toFixed` 调用中的变量一定是 `estimated_cost_cny`，不要漏改。

- [ ] **Step 2: 重启服务并验证**

```bash
./server.sh restart
```

用 Playwright 验证 Token 统计页：
1. 费用显示为 `¥` 前缀
2. 费用精度为 6 位小数
3. 趋势图成本线有数据（不再全为 0）

- [ ] **Step 3: 运行全量测试**

Run: `python3 -m pytest test/ -q`
Expected: all passed

- [ ] **Step 4: 提交**

```bash
git add static/js/pages/tokens.js
git commit -m "refactor: Token 统计页 $→¥ + toFixed(6) + estimated_cost_cny"
```

---

### Task 11: 端到端验证

- [ ] **Step 1: 运行全量测试**

Run: `python3 -m pytest test/ -q`
Expected: 406+ tests all passed

- [ ] **Step 2: 重启服务**

```bash
./server.sh restart
```

- [ ] **Step 3: API 验证**

```bash
# 计费表 CRUD
curl -s http://127.0.0.1:18742/api/pricing | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'{len(d[\"pricings\"])} 条定价')"
curl -s http://127.0.0.1:18742/api/pricing?search=deepseek | python3 -c "import sys,json; d=json.load(sys.stdin); print([p['model_id'] for p in d['pricings']])"

# 新增 RMB 定价
curl -s -X POST http://127.0.0.1:18742/api/pricing \
  -H 'Content-Type: application/json' \
  -d '{"model_id":"test-rmb","display_name":"Test RMB","input_cost_per_million":"10","output_cost_per_million":"50","currency":"RMB"}'

# 验证 Token 统计返回人民币
curl -s http://127.0.0.1:18742/api/token_stats/summary | python3 -c "import sys,json; d=json.load(sys.stdin); print('estimated_cost_cny' in d, d.get('estimated_cost_cny'))"
```

- [ ] **Step 4: Playwright 前端验证**

1. 打开计费表 Tab — 表格正常加载，搜索可用
2. 点击"新增定价" — 弹窗正常，提交后刷新
3. 点击"编辑" — 弹窗预填数据，保存后刷新
4. 点击"删除" — 确认弹窗，删除后刷新
5. 打开 Token 统计 Tab — 费用显示 `¥` + 6 位小数
6. 趋势图 — 成本线有数据

- [ ] **Step 5: 清理测试数据 + 最终提交**

```bash
# 删除测试数据
curl -s -X DELETE http://127.0.0.1:18742/api/pricing/test-rmb
```

---

## Self-Review

**Spec 覆盖检查：**
- [x] model_pricing 表迁入 config.db — Task 1
- [x] USD/RMB 双币种 + currency 字段 — Task 1, 3
- [x] 汇率固定 7 — Task 4
- [x] 页面统一显示人民币 ¥ — Task 8, 10
- [x] 后端统一输出人民币 — Task 4
- [x] 6 位小数 — Task 5, 10
- [x] 完整 CRUD — Task 3, 7
- [x] 搜索 — Task 2, 7
- [x] 独立 Tab — Task 9
- [x] _CostCalculator 改造 — Task 4
- [x] 缓存失效 — Task 4, 7
- [x] StatsService 构造函数变更 — Task 4
- [x] 种子数据不显式写 currency — Task 1
- [x] 趋势图成本修复 — Task 6
- [x] tokens.js 适配 — Task 10

**占位符扫描：** 无 TBD/TODO/模糊描述

**类型一致性：** `estimated_cost_cny` 在 Task 5 命名，Task 6/10/11 一致使用。PricingDB 方法签名在 Task 1-3 定义，Task 4/7 调用一致。`invalidate_pricing_cache()` 在 Task 4 定义，Task 7 调用一致。`showModal(title, content, footer)` 在 Task 8 使用正确的三参数模式。

**审阅反馈修复记录：**
- 致命 #1：showModal 改用 footer HTML + window 全局函数（匹配 routes.js 模式）
- 致命 #2：页面注册改用静态 import + pageLoaders + Tab 点击处理器（匹配 app.js 模式）
- 致命 #3：Task 4 已显式定义 invalidate_cache() + invalidate_pricing_cache() + 单元测试
- 严重 #4：PricingDB 共用 config.db 是设计决策，后期 Migrations 独立重构时统一管理
- 严重 #5：Task 4 补充了完整代码和 _CostCalculator 单元测试
- 严重 #6：Task 4/5 新增 TestCostCalculatorCNY 测试类
- 中等 #7：Task 5 细化替换范围，区分字典键和方法名
- 中等 #8：Task 6 保持原 TDD 流程（rename 后字段名一致，测试先写再修）
- 中等 #9：pricing.js 中 model_id 转义加了 `.replace(/'/g, "\\'")`
- 中等 #10：Task 10 列出全部 9 处替换位置
- 次要 #11：CSS 改用 .form-group/.form-label/.form-input（复用 base.css）
- 次要 #12：toFixed(6) 保留（用户明确要求 6 位小数）
- 次要 #13：种子数据 reasoning effort 变体与主模型同价（来自 cc-switch 原始数据，保持原样）
