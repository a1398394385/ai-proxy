"""Agent 检测模块 — 判断请求是否来自子 agent。

层级：第 0 层，零内部依赖。

检测信号：
1. Claude Code: __SUBAGENT_MARKER__ 在 <system-reminder> 标签内
2. Claude Code: metadata.user_id 含 _agent_ 字符串
3. Codex: client_metadata 含 x-codex-installation-id 且 tools 不含 spawn_agent
"""


def detect_subagent(body: dict) -> bool:
    """检测请求是否来自子 agent。"""
    if _claude_code_subagent(body):
        return True
    if _codex_subagent(body):
        return True
    return False


def _claude_code_subagent(body: dict) -> bool:
    # 信号 1: __SUBAGENT_MARKER__ 在 <system-reminder> 标签内
    if _contains_marker(body, "__SUBAGENT_MARKER__"):
        return True

    # 信号 2: OMC SubagentStart hook 注入的 <system-reminder> 标签
    if _contains_marker(body, "SubagentStart"):
        return True

    # 信号 3: metadata.user_id 含 _agent_ 字符串
    user_id = body.get("metadata", {}).get("user_id", "")
    if user_id and "_agent_" in user_id:
        return True

    return False


def _codex_subagent(body: dict) -> bool:
    """Codex 子 agent: 有 installation-id 但无 agent 管理工具。"""
    client_meta = body.get("client_metadata")
    if not isinstance(client_meta, dict) or "x-codex-installation-id" not in client_meta:
        return False

    tools = body.get("tools", [])
    tool_names = {t.get("name") for t in tools if isinstance(t, dict) and t.get("name")}
    agent_tools = {"spawn_agent", "send_input", "resume_agent", "wait_agent", "close_agent"}
    return not (tool_names & agent_tools)


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

    # 扫描 system 消息 + 所有 user 消息
    for msg in body.get("messages", []):
        role = msg.get("role", "")
        if role in ("system", "user"):
            if marker in _extract_text(msg):
                return True
    return False
