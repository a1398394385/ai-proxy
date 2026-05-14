"""Agent 检测模块 — 判断请求是否来自 Claude Code 子 agent。"""


def detect_subagent(body: dict) -> bool:
    """检测请求是否来自 Claude Code 子 agent。

    两个信号：
    1. __SUBAGENT_MARKER__ 在 system/user 消息文本中
    2. metadata.user_id 含 _agent_ 字符串
    """
    if _contains_marker(body, "__SUBAGENT_MARKER__"):
        return True

    user_id = body.get("metadata", {}).get("user_id", "")
    if user_id and "_agent_" in user_id:
        return True

    return False


def _contains_marker(body: dict, marker: str) -> bool:
    """在消息文本中搜索标记。处理 string 和 content blocks 两种消息格式。"""
    def _extract_text(msg):
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        return ""

    for msg in body.get("messages", []):
        role = msg.get("role", "")
        if role in ("system", "user"):
            if marker in _extract_text(msg):
                return True
    return False
