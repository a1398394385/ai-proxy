import json
import unittest


class TestFormatSSEEvent(unittest.TestCase):
    """_format_sse_event 独立测试 — 确保迁移后行为不变。"""

    # ─── 基本功能 ───

    def test_basic_event_format(self):
        """基本 SSE 事件格式：event: {type}\ndata: {json}\n\n"""
        from proxy.sse_utils import _format_sse_event
        result = _format_sse_event("message_start", {"id": "123", "model": "claude"})
        self.assertIn("event: message_start\n", result)
        self.assertIn("data: ", result)
        self.assertTrue(result.endswith("\n\n"))
        data_part = result.split("data: ", 1)[1].strip()
        data = json.loads(data_part)
        self.assertEqual(data["type"], "message_start")

    def test_type_field_injected(self):
        """event_type 作为 data JSON 的顶层 'type' 字段注入。"""
        from proxy.sse_utils import _format_sse_event
        result = _format_sse_event("content_block_delta", {"index": 0})
        data_part = result.split("data: ", 1)[1].strip()
        data = json.loads(data_part)
        self.assertEqual(data["type"], "content_block_delta")
        self.assertEqual(data["index"], 0)

    def test_type_field_overwritten(self):
        """data 中已有的 'type' 字段被 event_type 覆盖。"""
        from proxy.sse_utils import _format_sse_event
        result = _format_sse_event("correct_type", {"type": "wrong_type", "x": 1})
        data_part = result.split("data: ", 1)[1].strip()
        data = json.loads(data_part)
        self.assertEqual(data["type"], "correct_type")
        self.assertEqual(data["x"], 1)

    # ─── Responses API 'response' 包裹 ───
    # 注意：_format_sse_event 本身不做包裹，包裹由调用方传入 data 时处理
    # （如 create_codex_sse_stream 在调用 _format_sse_event 前已构造 {"response": {...}}）

    def test_response_event_wrapped(self):
        """response.* 事件的 data 中 'response' 键由调用方预先构造。"""
        from proxy.sse_utils import _format_sse_event
        # 模拟调用方传入已包裹的 data
        result = _format_sse_event("response.created", {
            "response": {"id": "resp-123"}
        })
        data_part = result.split("data: ", 1)[1].strip()
        data = json.loads(data_part)
        self.assertIn("response", data)
        self.assertEqual(data["response"]["id"], "resp-123")
        self.assertEqual(data["type"], "response.created")

    def test_response_incomplete_wrapped(self):
        """response.incomplete 的 data 中 'response' 键由调用方预先构造。"""
        from proxy.sse_utils import _format_sse_event
        result = _format_sse_event("response.incomplete", {
            "response": {"incomplete_details": {"reason": "max_tokens"}}
        })
        data_part = result.split("data: ", 1)[1].strip()
        data = json.loads(data_part)
        self.assertIn("response", data)
        self.assertEqual(data["response"]["incomplete_details"]["reason"], "max_tokens")

    # ─── Responses API 'item' 包裹 ───

    def test_output_item_event_wrapped(self):
        """output_item.* 事件的 data 中 'item' 键由调用方预先构造。"""
        from proxy.sse_utils import _format_sse_event
        result = _format_sse_event("response.output_item.added", {
            "output_index": 0,
            "item": {"type": "reasoning", "id": "item-1", "summary": [], "status": "in_progress"}
        })
        data_part = result.split("data: ", 1)[1].strip()
        data = json.loads(data_part)
        self.assertIn("item", data)
        self.assertEqual(data["item"]["id"], "item-1")
        self.assertEqual(data["type"], "response.output_item.added")
        self.assertEqual(data["output_index"], 0)

    # ─── Anthropic 事件（不匹配 response. / output_item. 前缀） ───

    def test_anthropic_event_no_wrap(self):
        """Anthropic 事件（message_start 等）不被包裹键包裹。"""
        from proxy.sse_utils import _format_sse_event
        result = _format_sse_event("message_start", {
            "message": {"id": "msg_1", "model": "claude-sonnet-4-6", "role": "assistant", "content": []}
        })
        data_part = result.split("data: ", 1)[1].strip()
        data = json.loads(data_part)
        self.assertEqual(data["type"], "message_start")
        self.assertIn("message", data)
        self.assertNotIn("response", data)
        self.assertNotIn("item", data)

    def test_anthropic_delta_event_no_wrap(self):
        """content_block_delta 不被包裹。"""
        from proxy.sse_utils import _format_sse_event
        result = _format_sse_event("content_block_delta", {
            "index": 0, "delta": {"type": "text_delta", "text": "hello"}
        })
        data_part = result.split("data: ", 1)[1].strip()
        data = json.loads(data_part)
        self.assertEqual(data["type"], "content_block_delta")
        self.assertNotIn("response", data)
        self.assertNotIn("item", data)

    # ─── compact JSON 格式 ───

    def test_compact_json_format(self):
        """使用紧凑格式（无多余空格）。"""
        from proxy.sse_utils import _format_sse_event
        result = _format_sse_event("message_stop", {})
        # compact JSON: {"type":"message_stop"} 不含多余空格
        self.assertIn('{"type":"message_stop"}', result)


if __name__ == "__main__":
    unittest.main()
