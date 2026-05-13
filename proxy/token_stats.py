#!/usr/bin/env python3
"""Token 统计模块 — 统一处理 Anthropic / OpenAI Chat / OpenAI Responses 格式的 usage。

用法：
    from proxy.token_stats import record_token_stats

    record_token_stats(usage, {
        "request_id": "abc123",
        "request_type": "codex",
        "model": "gpt-5.1-codex-max",
        "target_model": "qwen3.6-plus",
        "request_ts": "2026-04-27 10:00:00",
        "duration_ms": 1234,
    })

DB_PATH 假设：token_stats.py 位于 proxy/ 子目录下，因此 parent.parent 指向项目根目录。
与 request_logger.py 使用同一路径约定。
"""

import sqlite3
import logging
from datetime import datetime
from pathlib import Path

from .paths import get_data_path

logger = logging.getLogger(__name__)

DB_PATH = get_data_path("access_log.db")


def _find_first(usage: dict, keys: list, default=0) -> int:
    """按 keys 顺序查找 usage 中第一个存在的 key，返回其值。

    使用 key 存在性（k in usage）而非值大小做判断。
    这意味着优先级 1 的 key 即使值为 0（如 cache 未命中）也不会回退到优先级 2。
    只有 key 完全不存在时才检查下一个。
    """
    for k in keys:
        if k in usage and usage[k] is not None:
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
    # if/elif 链用 key 存在性做优先级判断：
    #   优先 Anthropic 格式（cache_*_input_tokens 在 usage 顶层），
    #   其次 OpenAI Chat 格式（prompt_tokens_details.cached_tokens），
    #   最后 OpenAI Responses 格式（input_tokens_details.cached_tokens）。
    # 假设上游不会在同一响应中同时返回多种格式的 cache 字段 —
    # 如果 Anthropic 的 cache_read_input_tokens 为 0（缓存未命中），
    # 不会回退到 Chat/Responses 格式的值，因为 key 已存在。
    prompt_details = usage.get("prompt_tokens_details") or {}
    input_details = usage.get("input_tokens_details") or {}

    cached_read = 0
    if "cache_read_input_tokens" in usage and usage["cache_read_input_tokens"] is not None:
        cached_read = usage["cache_read_input_tokens"]
    elif "cached_tokens" in prompt_details and prompt_details["cached_tokens"] is not None:
        cached_read = prompt_details["cached_tokens"]
    elif "cached_tokens" in input_details and input_details["cached_tokens"] is not None:
        cached_read = input_details["cached_tokens"]

    cached_write = 0
    if "cache_creation_input_tokens" in usage and usage["cache_creation_input_tokens"] is not None:
        cached_write = usage["cache_creation_input_tokens"]
    elif "cache_creation_input_tokens" in input_details and input_details["cache_creation_input_tokens"] is not None:
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
        "request_type": str,          # 默认 "unknown"
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
                    request_type        TEXT NOT NULL,
                    model               TEXT NOT NULL,
                    target_model        TEXT NOT NULL,
                    upstream_id         INTEGER,
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

            # ─── 兼容旧表：确保 upstream_id 列存在 ───
            try:
                conn.execute("ALTER TABLE token_stats ADD COLUMN upstream_id INTEGER")
            except Exception:
                pass  # 列已存在

            # ─── 性能索引 ───
            conn.execute("CREATE INDEX IF NOT EXISTS idx_token_stats_request_ts ON token_stats(request_ts)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_token_stats_target_model ON token_stats(target_model)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_token_stats_upstream_id ON token_stats(upstream_id)")

            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            conn.execute(
                "INSERT INTO token_stats "
                "(request_id, request_type, model, target_model, upstream_id, "
                "request_ts, duration_ms, "
                "input_tokens, output_tokens, cached_read_tokens, cached_write_tokens, "
                "status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?)",
                (
                    request_id,
                    context.get("request_type", "unknown"),
                    context.get("model", "unknown"),
                    context.get("target_model", "unknown"),
                    context.get("upstream_id"),
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
