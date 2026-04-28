"""Anthropic Messages API ↔ Chat Completions 转换模块。"""
import json
import logging

logger = logging.getLogger(__name__)


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

    return chat


def _is_o_series(model: str) -> bool:
    """检测 o-series 模型（o + 数字开头）。"""
    import re
    return bool(re.match(r'^o\d', model))


def supports_reasoning_effort(model: str) -> bool:
    """检测模型是否支持 reasoning_effort 字段（o-series + gpt-5+）。

    参考 cc-switch transform.rs line 22-27。
    """
    m = model.lower()
    return _is_o_series(model) or (
        m.startswith("gpt-") and len(m) > 4 and m[4].isdigit() and int(m[4]) >= 5
    )


def _convert_message_to_chat(role: str, content) -> list:
    """将单个 Anthropic 消息转换为 Chat messages（可能多条）。

    返回顺序：assistant/user 消息在前，tool 消息在后。
    """
    if isinstance(content, str):
        return [{"role": role, "content": content}]
    if isinstance(content, list):
        chat_content = []
        tool_calls = []
        tool_messages = []
        for block in content:
            block_type = block.get("type")
            if block_type == "text":
                text_item = {"type": "text", "text": block.get("text", "")}
                if "cache_control" in block:
                    text_item["cache_control"] = block["cache_control"]
                chat_content.append(text_item)
            elif block_type == "image":
                source = block.get("source", {})
                media_type = source.get("media_type", "image/png")
                data = source.get("data", "")
                chat_content.append({
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
                    # 如果数组含复杂对象，取第一个 text 块内容
                    text_parts = [b.get("text", "") for b in tc if b.get("type") == "text"]
                    if text_parts:
                        tc = text_parts[0]
                    else:
                        tc = json.dumps(tc)
                tool_messages.append({
                    "role": "tool",
                    "tool_call_id": block.get("tool_use_id", ""),
                    "content": tc,
                })
            elif block_type in ("thinking", "redacted_thinking"):
                pass  # 丢弃
        result = []
        if role == "assistant" and tool_calls:
            msg = {"role": "assistant", "tool_calls": tool_calls}
            if chat_content:
                msg["content"] = chat_content
            result.append(msg)
        elif chat_content:
            result.append({"role": role, "content": chat_content})
        elif not tool_messages:
            result.append({"role": role, "content": ""})
        result.extend(tool_messages)
        return result
    return [{"role": role, "content": str(content)}]


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


def _resolve_reasoning_effort(body: dict) -> str | None:
    """将 Anthropic thinking/output_config 映射为 reasoning_effort 值。"""
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


# ─── Chat Completions → Anthropic Messages 响应转换 ───

_FINISH_REASON_MAP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "end_turn",
}


def chat_to_anthropic(response: dict) -> dict:
    """Chat Completions → Anthropic Messages 非流式响应转换。"""
    chat_id = response.get("id", "")
    choice = response.get("choices", [{}])[0]
    message = choice.get("message", {})
    finish_reason = choice.get("finish_reason", "stop")

    content = []

    # 文本内容
    text = message.get("content")
    if isinstance(text, str):
        content.append({"type": "text", "text": text})
    elif isinstance(text, list):
        for block in text:
            if block.get("type") == "output_text":
                content.append({"type": "text", "text": block.get("text", "")})
            elif block.get("type") == "text":
                content.append({"type": "text", "text": block.get("text", "")})

    # 拒绝内容
    refusal = message.get("refusal")
    if refusal:
        content.append({"type": "text", "text": refusal})

    # 工具调用
    tool_calls = message.get("tool_calls", [])
    for tc in tool_calls:
        func = tc.get("function", {})
        try:
            input_dict = json.loads(func.get("arguments", "{}"))
        except (json.JSONDecodeError, TypeError):
            input_dict = {}
        content.append({
            "type": "tool_use",
            "id": tc.get("id", ""),
            "name": func.get("name", ""),
            "input": input_dict,
        })

    result = {
        "id": chat_id,
        "type": "message",
        "role": "assistant",
        "content": content,
        "model": response.get("model", ""),
        "stop_reason": _FINISH_REASON_MAP.get(finish_reason, "end_turn"),
        "stop_sequence": None,
    }

    # usage
    usage = response.get("usage", {})
    result["usage"] = {
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
    }
    cached = usage.get("prompt_tokens_details", {}).get("cached_tokens")
    if cached is not None:
        result["usage"]["cache_read_input_tokens"] = cached
    if "cache_creation_input_tokens" in usage:
        result["usage"]["cache_creation_input_tokens"] = usage["cache_creation_input_tokens"]

    return result
