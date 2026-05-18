"""SSE 事件格式化与解析工具 — 转换模块共用。"""

import json
from typing import Generator, Optional


def _format_sse_event(event_type: str, data: dict) -> str:
    """生成标准 SSE 事件字符串，确保 data JSON 包含 "type" 字段。

    event_type 作为 data JSON 的顶层 "type" 字段注入，覆盖 data 中的已有 "type"。
    统一使用 separators=(',', ':') 紧凑格式。
    """
    payload = {**data, "type": event_type}
    return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"


def _parse_sse_event(text: str) -> Optional[dict]:
    """解析单个 SSE 事件文本，返回 {event, data} 或 None。"""
    event_type = "message"
    data_lines = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("event: "):
            event_type = stripped[7:]
        elif stripped.startswith("data: "):
            data_lines.append(stripped[6:])
        # ": " 开头为 keepalive，跳过
    raw = "\n".join(data_lines)
    if not raw:
        return None
    if raw == "[DONE]":
        return {"event": "[DONE]", "data": None}
    try:
        return {"event": event_type, "data": json.loads(raw)}
    except json.JSONDecodeError:
        return None


def iter_sse_events(upstream_response) -> Generator[dict, None, None]:
    """逐 chunk 读取 HTTP 响应流，yield 解析后的 SSE 事件。

    遇到 [DONE] 事件后立即停止读取，避免上游连接未关闭导致阻塞。

    upstream_response: 有 read(size) 方法的对象（http.client.HTTPResponse）
    """
    buf = b""
    while True:
        chunk = upstream_response.read(4096)
        if not chunk:
            break
        buf += chunk
        while b"\n\n" in buf:
            raw, buf = buf.split(b"\n\n", 1)
            event = _parse_sse_event(raw.decode("utf-8", errors="replace"))
            if event:
                yield event
                if event.get("event") == "[DONE]":
                    return  # [DONE] 后停止读取，不等上游连接关闭
