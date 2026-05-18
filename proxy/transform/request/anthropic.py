"""Anthropic Messages → Chat Completions 请求转换模块。"""
import json
import logging

from ._utils import _fix_tool_message_order

logger = logging.getLogger(__name__)


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
        elif tc_type == "none":
            return "none"
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


# ─── Content Block Converters ───


def _convert_text_block(block: dict) -> dict:
    """Convert Anthropic text block → Chat text content part.

    Preserves cache_control if present.
    """
    item = {"type": "text", "text": block.get("text", "")}
    if "cache_control" in block:
        item["cache_control"] = block["cache_control"]
    return item


def _convert_image_block(block: dict) -> dict:
    """Convert Anthropic image block → Chat image_url content part.

    Anthropic: {"type":"image","source":{"type":"base64","media_type":"image/png","data":"..."}}
    Chat: {"type":"image_url","image_url":{"url":"data:image/png;base64,..."}}
    """
    source = block.get("source", {})
    media_type = source.get("media_type", "image/png")
    data = source.get("data", "")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{media_type};base64,{data}"},
    }


def _convert_tool_use_block(block: dict) -> dict:
    """Convert Anthropic tool_use block → Chat tool_calls item.

    Anthropic: {"type":"tool_use","id":"...","name":"...","input":{...}}
    Chat tool_calls item: {"id":"...","type":"function","function":{"name":"...","arguments":"..."}}
    """
    return {
        "id": block.get("id", ""),
        "type": "function",
        "function": {
            "name": block.get("name", ""),
            "arguments": json.dumps(block.get("input", {})),
        },
    }


def _convert_tool_result_block(block: dict) -> dict:
    """Convert Anthropic tool_result block → Chat tool message.

    IMPORTANT FIX: When content is a list, concatenate ALL text blocks
    separated by newlines (instead of only taking parts[0]).

    Anthropic: {"type":"tool_result","tool_use_id":"...","content":"..."|[...]}
    Chat: {"role":"tool","tool_call_id":"...","content":"..."}
    """
    tc = block.get("content") or ""
    if isinstance(tc, list):
        parts = [b.get("text", "") for b in tc if b.get("type") == "text"]
        tc = "\n".join(parts) if parts else json.dumps(tc)
    return {
        "role": "tool",
        "tool_call_id": block.get("tool_use_id", ""),
        "content": tc,
    }


def _convert_thinking_block(block: dict) -> str | None:
    """Extract thinking text from Anthropic thinking block → reasoning_content.

    Returns the thinking text, or None if empty/absent.
    """
    thinking = block.get("thinking", "")
    return thinking if thinking else None


def _convert_content_blocks(content: list, role: str) -> tuple:
    """Dispatch content blocks to type-specific converters.

    Returns (text_parts, tool_calls, tool_results, reasoning_parts) tuple.
    """
    text_parts = []
    tool_calls = []
    tool_results = []
    reasoning_parts = []

    for block in content:
        block_type = block.get("type")
        if block_type == "text":
            text_parts.append(_convert_text_block(block))
        elif block_type == "image":
            text_parts.append(_convert_image_block(block))
        elif block_type == "tool_use":
            tool_calls.append(_convert_tool_use_block(block))
        elif block_type == "tool_result":
            tool_results.append(_convert_tool_result_block(block))
        elif block_type == "thinking":
            thinking = _convert_thinking_block(block)
            if thinking:
                reasoning_parts.append(thinking)
            else:
                reasoning_parts.append("")
        elif block_type == "redacted_thinking":
            reasoning_parts.append("")

    return text_parts, tool_calls, tool_results, reasoning_parts


def _convert_message_to_chat(role: str, content) -> list:
    """将单个 Anthropic 消息转换为 Chat Completions messages。

    Anthropic message sequence pattern: user → assistant → user → assistant → ...
    Two types of "user" messages:
      (a) User wrapping tool_result: This is an API construct, not real user input.
          The most reliable distinguisher: real user messages NEVER contain tool_result blocks.
      (b) Real user input: Contains text and/or image blocks.

    Three-way dispatch (mutually exclusive, priority order):
      1. tool_result present → tool messages (Anthropic wraps tool_result in user role)
      2. assistant + tool_use → assistant + tool_calls + reasoning_content
      3. Everything else → keep original role with plain content

    Key insight: tool_result blocks ONLY appear in anthropic-wrapped "user" messages,
    never in real user input. This is the primary disambiguation mechanism.
    """
    if isinstance(content, str):
        return [{"role": role, "content": content}]
    if not isinstance(content, list):
        return [{"role": role, "content": str(content)}]

    text_parts, tool_calls, tool_results, reasoning_parts = _convert_content_blocks(content, role)

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


# ─── Main Entry ───


def anthropic_to_chat(body: dict, model_cfg: dict) -> dict:
    """Anthropic Messages → OpenAI Chat Completions 请求转换。

    model_cfg: 来自 proxy 层 resolve_model()，必需字段 target（如 "qwen3.6-plus"）。
               测试中 mock 为 {"target": "qwen3.6-plus", "multimodal": True}。
    """
    chat = {
        "model": model_cfg["target"],
        "messages": [],
    }

    # system → system message
    system = body.get("system")
    if isinstance(system, str):
        chat["messages"].append({"role": "system", "content": system})
    elif isinstance(system, list):
        parts = [block["text"] for block in system if block.get("type") == "text" and block.get("text")]
        if parts:
            chat["messages"].append({"role": "system", "content": "\n".join(parts)})

    # messages
    for msg in body.get("messages", []):
        converted = _convert_message_to_chat(msg.get("role", "user"), msg.get("content"))
        chat["messages"].extend(converted)

    # max_tokens：o-series 用 max_completion_tokens，其他用 max_tokens
    if "max_tokens" in body:
        model_target = model_cfg.get("target", "")
        if _is_o_series(model_target):
            chat["max_completion_tokens"] = body["max_tokens"]
        else:
            chat["max_tokens"] = body["max_tokens"]

    # temperature, top_p, stop, stream
    stops = body.get("stop_sequences")
    if stops:
        chat["stop"] = stops
    for key in ("temperature", "top_p", "stream"):
        if key in body:
            chat[key] = body[key]

    if body.get("stream"):
        chat["stream_options"] = {"include_usage": True}

    # tool_choice 格式映射
    if "tool_choice" in body:
        chat["tool_choice"] = _map_tool_choice(body["tool_choice"])

    # tools 格式转换
    if "tools" in body:
        chat["tools"] = _map_anthropic_tools(body["tools"])

    # thinking → reasoning_effort（仅支持的模型）
    if supports_reasoning_effort(model_cfg.get("target", "")):
        effort = _resolve_reasoning_effort(body)
        if effort:
            chat["reasoning_effort"] = effort

    # 修复：Anthropic 允许 tool_result 在后续任意位置响应 tool_use，
    # 但 Chat Completions 要求 assistant+tool_calls 后必须紧跟 tool 消息。
    chat["messages"] = _fix_tool_message_order(chat["messages"])

    # DeepSeek 等推理模型要求：同一会话中 assistant 消息的 reasoning_content 必须一致存在。
    _ensure_reasoning_consistency(chat["messages"])
    return chat


from ..registry import register_request
register_request("messages", "chat_completions", anthropic_to_chat)
