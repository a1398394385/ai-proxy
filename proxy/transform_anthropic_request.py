"""Anthropic Messages → Chat Completions 请求转换模块。

从 transform_anthropic.py 拆分而来，包含所有的 Anthropic → Chat 请求转换逻辑。
"""
import json
import logging

logger = logging.getLogger(__name__)


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


def _is_o_series(model: str) -> bool:
    """检测 o-series 模型（o + 数字开头），大小写不敏感。"""
    import re
    return bool(re.match(r'^o\d', model.lower()))


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
            m["reasoning_content"] = ""


def supports_reasoning_effort(model: str) -> bool:
    """检测模型是否支持 reasoning_effort 字段（o-series + gpt-5+）。

    参考 cc-switch transform.rs line 22-27。
    """
    m = model.lower()
    return _is_o_series(model) or (
        m.startswith("gpt-") and len(m) > 4 and m[4].isdigit() and int(m[4]) >= 5
    )


def _map_tool_choice(tc) -> str | dict:
    """映射 Anthropic tool_choice → Chat Completions 格式。"""
    if isinstance(tc, str):
        mapping = {"auto": "auto", "any": "required", "none": "none"}
        return mapping.get(tc, tc)
    if isinstance(tc, dict):
        tc_type = tc.get("type", "auto")
        if tc_type == "auto":
            return "auto"
        elif tc_type == "any":
            return "required"
        elif tc_type == "tool":
            return {"type": "function", "function": {"name": tc.get("name", "")}}
    return tc


def _map_anthropic_tools(tools: list) -> list:
    """Anthropic 工具格式 → Chat Completions 工具格式。"""
    result = []
    for tool in tools:
        func = {}
        for key in ("name", "description"):
            if key in tool:
                func[key] = tool[key]
        func["parameters"] = tool.get("input_schema", {})
        result.append({"type": "function", "function": func})
    return result


def _resolve_reasoning_effort(body: dict) -> str | None:
    """将 Anthropic thinking/output_config 映射为 reasoning_effort 值。

    output_config.effort 非 Anthropic 官方字段，来自 Claude Code 内部扩展（Beta API），
    优先级高于 thinking.type。
    """
    output_config = body.get("output_config", {})
    if isinstance(output_config, dict) and "effort" in output_config:
        effort_map = {"low": "low", "medium": "medium", "high": "high", "max": "xhigh"}
        return effort_map.get(output_config["effort"])
    thinking = body.get("thinking")
    if isinstance(thinking, dict):
        if thinking.get("type") == "adaptive":
            return "xhigh"
        if thinking.get("type") == "enabled":
            budget = thinking.get("budget_tokens", 0)
            if budget < 4000:
                return "low"
            elif budget < 16000:
                return "medium"
            else:
                return "high"
    return None


def _convert_message_to_chat(role: str, content) -> list:
    """将单个 Anthropic 消息转换为 Chat Completions messages。

    Anthropic 消息模式：user → assistant → user → assistant → ...
    其中 tool_result 被 Anthropic 包装在 user 角色中。

    转换规则（三条路，互斥）：
    1. 含 tool_result → tool 消息（Anthropic 用 user 包装 tool_result）
    2. assistant + tool_use → assistant + tool_calls + reasoning_content
    3. 其他 → 保持原 role 的普通消息
    """
    if isinstance(content, str):
        return [{"role": role, "content": content}]
    if not isinstance(content, list):
        return [{"role": role, "content": str(content)}]

    text_parts = []
    tool_calls = []
    tool_results = []
    reasoning_parts = []

    for block in content:
        block_type = block.get("type")
        if block_type == "text":
            item = {"type": "text", "text": block.get("text", "")}
            if "cache_control" in block:
                item["cache_control"] = block["cache_control"]
            text_parts.append(item)
        elif block_type == "image":
            source = block.get("source", {})
            media_type = source.get("media_type", "image/png")
            data = source.get("data", "")
            text_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{media_type};base64,{data}"},
            })
        elif block_type == "tool_use":
            tool_calls.append({
                "id": block.get("id", ""),
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    "arguments": json.dumps(block.get("input", {})),
                },
            })
        elif block_type == "tool_result":
            tc = block.get("content") or ""
            if isinstance(tc, list):
                parts = [b.get("text", "") for b in tc if b.get("type") == "text"]
                tc = parts[0] if parts else json.dumps(tc)
            tool_results.append({
                "role": "tool",
                "tool_call_id": block.get("tool_use_id", ""),
                "content": tc,
            })
        elif block_type == "thinking":
            thinking = block.get("thinking", "")
            if thinking:
                reasoning_parts.append(thinking)

    if not text_parts and not tool_calls and not tool_results and not reasoning_parts:
        return []

    # ① tool_result 优先：Anthropic 用 user 包装 tool_result → 转为 tool 消息
    if tool_results:
        # text 与 tool_result 同在时，text 是工具返回的附属内容，追加到最后一个 tool 消息
        if text_parts:
            extra = "\n".join(t.get("text", "") for t in text_parts if t.get("type") == "text")
            if extra:
                tool_results[-1]["content"] = (tool_results[-1].get("content") or "") + "\n" + extra
        return tool_results

    # ② assistant + tool_use → assistant + tool_calls
    if role == "assistant" and tool_calls:
        msg = {"role": "assistant", "tool_calls": tool_calls, "content": None}
        if text_parts:
            msg["content"] = text_parts
        if reasoning_parts:
            msg["reasoning_content"] = "".join(reasoning_parts)
        return [msg]

    # ③ 普通消息（user 文本/图片，assistant 纯文本）
    if text_parts:
        msg = {"role": role, "content": text_parts}
        if role == "assistant" and reasoning_parts:
            msg["reasoning_content"] = "".join(reasoning_parts)
        return [msg]

    # assistant 仅有 thinking（无 text 无 tool_use）
    if role == "assistant" and reasoning_parts:
        return [{"role": "assistant", "content": None, "reasoning_content": "".join(reasoning_parts)}]

    return []


def anthropic_to_chat(body: dict, model_cfg: dict) -> dict:
    """Anthropic Messages → OpenAI Chat Completions 请求转换。

    model_cfg: 来自 proxy 层 resolve_model()，必需字段 target（如 "qwen3.6-plus"）。
               测试中 mock 为 {"target": "qwen3.6-plus", "multimodal": True}。
    """
    # TODO: Task 2 中实现完整逻辑
    raise NotImplementedError("将在 Task 2 中实现")
