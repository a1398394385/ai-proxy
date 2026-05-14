import unittest
import sqlite3
import tempfile
import os
from pathlib import Path


class TestFindFirst(unittest.TestCase):
    """_find_first — 按 key 存在性（非值大小）做优先级查找。"""

    def test_returns_first_existing_key(self):
        from proxy.token_stats import _find_first
        usage = {"prompt_tokens": 100, "input_tokens": 200}
        self.assertEqual(_find_first(usage, ["prompt_tokens", "input_tokens"]), 100)

    def test_skips_missing_key(self):
        from proxy.token_stats import _find_first
        usage = {"input_tokens": 200}
        self.assertEqual(_find_first(usage, ["prompt_tokens", "input_tokens"]), 200)

    def test_skips_none_value(self):
        """key 存在但值为 None 时跳过，继续查下一个 key。"""
        from proxy.token_stats import _find_first
        usage = {"prompt_tokens": None, "input_tokens": 200}
        self.assertEqual(_find_first(usage, ["prompt_tokens", "input_tokens"]), 200)

    def test_zero_is_valid_value(self):
        """key 存在且值为 0 时返回 0，不回退到下一个 key。"""
        from proxy.token_stats import _find_first
        usage = {"prompt_tokens": 0, "input_tokens": 200}
        self.assertEqual(_find_first(usage, ["prompt_tokens", "input_tokens"]), 0)

    def test_no_match_returns_default(self):
        from proxy.token_stats import _find_first
        usage = {}
        self.assertEqual(_find_first(usage, ["prompt_tokens"]), 0)

    def test_all_missing_returns_default(self):
        from proxy.token_stats import _find_first
        usage = {"other": 999}
        self.assertEqual(_find_first(usage, ["prompt_tokens", "input_tokens"]), 0)


class TestExtractTokens(unittest.TestCase):
    """_extract_tokens 多格式提取测试。"""

    def test_openai_chat_format(self):
        """OpenAI Chat：prompt_tokens + completion_tokens + prompt_tokens_details.cached_tokens。"""
        from proxy.token_stats import _extract_tokens
        usage = {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "prompt_tokens_details": {"cached_tokens": 20},
        }
        result = _extract_tokens(usage)
        # Chat 的 prompt_tokens 包含 cached_tokens，需扣除
        self.assertEqual(result["input_tokens"], 80)  # 100 - 20
        self.assertEqual(result["output_tokens"], 50)
        self.assertEqual(result["cached_read"], 20)
        self.assertEqual(result["cached_write"], 0)

    def test_openai_responses_format(self):
        """OpenAI Responses API：input_tokens + output_tokens + input_tokens_details.cached_tokens。"""
        from proxy.token_stats import _extract_tokens
        usage = {
            "input_tokens": 200,
            "output_tokens": 80,
            "total_tokens": 280,
            "input_tokens_details": {"cached_tokens": 30},
        }
        result = _extract_tokens(usage)
        # Responses 的 input_tokens 包含 cached_tokens，需扣除
        self.assertEqual(result["input_tokens"], 170)  # 200 - 30
        self.assertEqual(result["output_tokens"], 80)
        self.assertEqual(result["cached_read"], 30)
        self.assertEqual(result["cached_write"], 0)

    def test_anthropic_cache_format(self):
        """Anthropic：prompt_tokens + cache_read_input_tokens + cache_creation_input_tokens 都在顶层。"""
        from proxy.token_stats import _extract_tokens
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
        from proxy.token_stats import _extract_tokens
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
        from proxy.token_stats import _extract_tokens
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

    def test_null_details_dict(self):
        """prompt_tokens_details 值为 null 时不抛 TypeError。"""
        from proxy.token_stats import _extract_tokens
        usage = {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "prompt_tokens_details": None,
        }
        result = _extract_tokens(usage)
        self.assertEqual(result["input_tokens"], 100)
        self.assertEqual(result["cached_read"], 0)

    def test_anthropic_cache_miss_zero(self):
        """Anthropic cache 未命中（值为 0）时正确返回 0，不回退到其他格式的值。"""
        from proxy.token_stats import _extract_tokens
        usage = {
            "prompt_tokens": 5000,
            "completion_tokens": 200,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }
        result = _extract_tokens(usage)
        self.assertEqual(result["cached_read"], 0)
        self.assertEqual(result["cached_write"], 0)

    def test_cache_fields_none_value(self):
        """cache 字段值为 None 时返回 0，不回退到其他格式。"""
        from proxy.token_stats import _extract_tokens
        usage = {
            "prompt_tokens": 5000,
            "completion_tokens": 200,
            "cache_read_input_tokens": None,
            "cache_creation_input_tokens": None,
        }
        result = _extract_tokens(usage)
        self.assertEqual(result["cached_read"], 0)
        self.assertEqual(result["cached_write"], 0)


class TestRecordTokenStats(unittest.TestCase):
    """record_token_stats 集成测试（写 DB）。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "access_log.db"
        from proxy import token_stats
        self._orig_db_path = token_stats.DB_PATH
        token_stats.DB_PATH = self.db_path

    def tearDown(self):
        from proxy import token_stats
        token_stats.DB_PATH = self._orig_db_path

    def _query(self):
        conn = sqlite3.connect(str(self.db_path))
        rows = conn.execute("SELECT * FROM token_stats").fetchall()
        conn.close()
        return rows

    def test_writes_token_stats(self):
        from proxy.token_stats import record_token_stats
        usage = {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "prompt_tokens_details": {"cached_tokens": 20},
        }
        context = {
            "request_id": "req-001",
            "request_type": "codex",
            "model": "gpt-5.1-codex-max",
            "target_model": "qwen3.6-plus",
            "request_ts": "2026-04-27 10:00:00",
            "duration_ms": 1234,
            "response_type": "chat_completions",
        }
        record_token_stats(usage, context)

        rows = self._query()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r[1], "req-001")                   # request_id
        self.assertEqual(r[2], "gpt-5.1-codex-max")        # model
        self.assertEqual(r[3], "qwen3.6-plus")             # target_model
        self.assertEqual(r[4], 80)                          # input_tokens = 100 - 20
        self.assertEqual(r[5], 50)                          # output_tokens
        self.assertEqual(r[6], 20)                          # cached_read_tokens
        self.assertEqual(r[7], 0)                           # cached_write_tokens
        self.assertEqual(r[8], "codex")                     # request_type
        self.assertEqual(r[9], "chat_completions")          # response_type = 上游 format
        self.assertEqual(r[10], "2026-04-27 10:00:00")     # request_ts
        self.assertEqual(r[11], 1234)                       # duration_ms
        self.assertEqual(r[12], "completed")                # status
        self.assertEqual(r[13], None)                       # upstream_id

    def test_empty_usage_does_not_write(self):
        from proxy.token_stats import record_token_stats
        try:
            record_token_stats({}, {"request_id": "req-002"})
        except Exception:
            self.fail("record_token_stats 不应该抛异常")

    def test_none_usage_does_not_write(self):
        from proxy.token_stats import record_token_stats
        try:
            record_token_stats(None, {"request_id": "req-003"})
        except Exception:
            self.fail("record_token_stats 不应该抛异常")

    def test_missing_request_id_does_not_write(self):
        from proxy.token_stats import record_token_stats
        try:
            record_token_stats({"prompt_tokens": 10}, {"agent": "test"})
        except Exception:
            self.fail("record_token_stats 不应该抛异常")

    def test_db_write_failure_does_not_raise(self):
        from proxy.token_stats import record_token_stats
        # 先写入一条记录以创建 DB 文件
        record_token_stats(
            {"prompt_tokens": 10},
            {"request_id": "req-setup"},
        )
        self.assertTrue(self.db_path.exists())
        os.remove(str(self.db_path))
        try:
            record_token_stats({"prompt_tokens": 10}, {"request_id": "req-004"})
        except Exception:
            self.fail("record_token_stats 不应该抛异常")


if __name__ == "__main__":
    unittest.main()
