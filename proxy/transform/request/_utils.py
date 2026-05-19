"""请求转换通用工具函数。

包含 _merge_consecutive_assistants、_fix_tool_message_order 和
_ensure_reasoning_consistency，供 anthropic.py 和 responses.py 共用。
"""


def _merge_consecutive_assistants(messages: list) -> list:
    """合并连续的 assistant 消息，确保 Chat Completions 格式合法。

    处理场景：
    - assistant(text) + assistant(tool_calls) → assistant(content + tool_calls)
    - assistant(text) + assistant(text) → assistant(merged content)
    - assistant(tool_calls) + assistant(text) → assistant(tool_calls + content)
    """
    result = []
    for msg in messages:
        if msg.get("role") != "assistant":
            result.append(msg)
            continue
        if result and result[-1].get("role") == "assistant":
            prev = result[-1]
            # 合并 content
            curr_content = msg.get("content")
            if curr_content is not None and curr_content != "":
                prev_content = prev.get("content")
                if prev_content is None or prev_content == "":
                    prev["content"] = curr_content
                elif isinstance(prev_content, str) and isinstance(curr_content, str):
                    prev["content"] = prev_content + "\n" + curr_content
                elif isinstance(prev_content, list) and isinstance(curr_content, list):
                    prev["content"] = prev_content + curr_content
                else:
                    parts = []
                    for c in (prev_content, curr_content):
                        if isinstance(c, str):
                            parts.append({"type": "text", "text": c})
                        elif isinstance(c, list):
                            parts.extend(c)
                    prev["content"] = parts
            # 合并 tool_calls
            curr_tc = msg.get("tool_calls")
            if curr_tc:
                if prev.get("tool_calls"):
                    prev["tool_calls"].extend(curr_tc)
                else:
                    prev["tool_calls"] = list(curr_tc)
            # 合并 reasoning_content（如果当前消息有但前一条没有）
            if "reasoning_content" in msg and "reasoning_content" not in prev:
                prev["reasoning_content"] = msg["reasoning_content"]
        else:
            result.append(msg)
    return result


def _fix_tool_message_order(messages: list) -> list:
    """修复 Chat Completions 消息顺序。

    Responses API 允许：
    (a) assistant 纯文本消息出现在 tool_call/tool 对之间
    (b) 多条 assistant+tool_calls 消息连续出现

    但 Chat Completions 要求：
    (a) assistant+tool_calls 必须紧跟对应的 tool 消息，中间不能有其他消息
    (b) 不允许连续的 assistant 消息

    策略：
    1. 将夹在 assistant+tool_calls ↔ tool 之间的 user/system 消息推迟到 tool 消息之后
    2. 将连续的 assistant+tool_calls 合并为单条消息
    3. 将连续的 assistant 消息合并（text+tool_calls 或 text+text）
    """
    result = []
    deferred = []

    i = 0
    while i < len(messages):
        msg = messages[i]
        role = msg.get("role")
        has_tool_calls = msg.get("tool_calls")

        if role == "assistant" and has_tool_calls:
            # 合并连续的所有 assistant+tool_calls 为单条消息
            all_tool_calls = []
            merged_reasoning = None
            while i < len(messages):
                m = messages[i]
                if m.get("role") == "assistant" and m.get("tool_calls"):
                    all_tool_calls.extend(m["tool_calls"])
                    if "reasoning_content" in m and merged_reasoning is None:
                        merged_reasoning = m["reasoning_content"]
                    i += 1
                else:
                    break

            merged = {
                "role": "assistant",
                "tool_calls": all_tool_calls,
            }
            # 保留第一个 assistant 消息的 content
            if msg.get("content") is not None:
                merged["content"] = msg["content"]
            if merged_reasoning:
                merged["reasoning_content"] = merged_reasoning

            # 收集对应的 tool 消息（允许中间穿插 user/system 等非 tool 消息）
            tool_call_ids = {tc.get("id") for tc in all_tool_calls}
            tool_msgs = []
            while i < len(messages) and tool_call_ids:
                m = messages[i]
                if m.get("role") == "tool":
                    if m.get("tool_call_id") in tool_call_ids:
                        tool_msgs.append(m)
                        tool_call_ids.discard(m.get("tool_call_id"))
                    else:
                        break  # 不属于当前 tool_calls 的 tool 消息，停止
                elif m.get("role") == "assistant" and m.get("tool_calls"):
                    break  # 新的 assistant+tool_calls 块，停止
                else:
                    deferred.append(m)  # user/system 等消息推迟
                i += 1

            result.append(merged)
            result.extend(tool_msgs)
        else:
            result.append(msg)
            i += 1

    result.extend(deferred)
    return _merge_consecutive_assistants(result)


def _ensure_reasoning_consistency(messages: list) -> None:
    """确保 assistant 消息的 reasoning_content 一致：有则全有。"""
    has_reasoning = any(
        m.get("role") == "assistant" and "reasoning_content" in m
        for m in messages
    )
    if not has_reasoning:
        return
    for m in messages:
        if m.get("role") == "assistant" and "reasoning_content" not in m:
            m["reasoning_content"] = "thinking"
