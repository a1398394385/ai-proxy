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
        db_path: data db 路径
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
                                                    CHECK(currency IN ('USD', 'RMB')),
                    multiplier                      TEXT NOT NULL DEFAULT '1.0'
                )
            """)
            # 兼容旧库：确保 multiplier 列存在
            try:
                conn.execute("ALTER TABLE model_pricing ADD COLUMN multiplier TEXT NOT NULL DEFAULT '1.0'")
            except Exception:
                pass
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

        multiplier = data.get("multiplier", "1.0")
        try:
            float(multiplier)
        except (ValueError, TypeError):
            raise ValueError(f"multiplier 必须为合法数字，收到: {multiplier}")

        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO model_pricing (model_id, display_name, "
                "input_cost_per_million, output_cost_per_million, "
                "cache_read_cost_per_million, cache_creation_cost_per_million, "
                "currency, multiplier) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    model_id,
                    data["display_name"],
                    data["input_cost_per_million"],
                    data["output_cost_per_million"],
                    data.get("cache_read_cost_per_million", "0"),
                    data.get("cache_creation_cost_per_million", "0"),
                    currency,
                    multiplier,
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
                      "cache_read_cost_per_million", "cache_creation_cost_per_million",
                      "multiplier"):
            if field in data:
                try:
                    float(data[field])
                except (ValueError, TypeError):
                    raise ValueError(f"{field} 必须为合法数字，收到: {data[field]}")

        updatable = [
            "display_name", "input_cost_per_million", "output_cost_per_million",
            "cache_read_cost_per_million", "cache_creation_cost_per_million",
            "currency", "multiplier",
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
