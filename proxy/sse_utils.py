"""SSE 事件格式化工具 — 两个转换模块共用。"""

import json


def _format_sse_event(event_type: str, data: dict) -> str:
    """生成标准 SSE 事件字符串，确保 data JSON 包含 "type" 字段。

    event_type 作为 data JSON 的顶层 "type" 字段注入，覆盖 data 中的已有 "type"。
    统一使用 separators=(',', ':') 紧凑格式。
    """
    payload = {**data, "type": event_type}
    return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
