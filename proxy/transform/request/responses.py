"""OpenAI Responses API → Chat Completions 请求转换模块。"""

import logging

from ._utils import _ensure_reasoning_consistency, _fix_tool_message_order

logger = logging.getLogger(__name__)

def responses_to_chat(body: dict, model_cfg: dict) -> dict:
    """Responses API → Chat Completions 请求转换。

    model_cfg: model_map 中命中的条目，如 {"target": "claude-sonnet-4-6", "multimodal": False}
    """
    messages = []

    # instructions → system message
    instructions = body.get("instructions", "")
    if instructions:
        messages.append({"role": "system", "content": instructions})

    # input → messages
    # reasoning 追踪：上游 thinking mode 要求 assistant 消息必须携带 reasoning_content
    # Responses API 中 reasoning 项紧跟在 assistant 交互之后，需要注入到下一条 assistant 消息
    pending_reasoning = None  # 待注入的 reasoning 文本
    raw_input = body.get("input", [])
    if isinstance(raw_input, str):
        raw_input = [{"type": "message", "role": "user", "content": raw_input}]
    for item in raw_input:
        if item.get("type") == "reasoning":
            # 收集 reasoning 文本，等待下一条 assistant 消息时注入
            summary = item.get("summary", [])
            text = "".join(s.get("text", "") for s in summary if s.get("type") == "summary_text")
            if text:
                pending_reasoning = text
            continue
        if item.get("type") in ("web_search_call", "code_interpreter_call", "mcp_call"):
            logger.warning(f"[transform] 丢弃不支持的 input 类型: {item.get('type')}")
            continue

        msg = _map_input_item(item, model_cfg)
        if msg is not None:
            # 如果有 pending 的 reasoning，仅在实际注入到 assistant 消息后才消费
            if pending_reasoning:
                for m in msg:
                    if m.get("role") == "assistant":
                        m["reasoning_content"] = pending_reasoning
                        pending_reasoning = None  # 仅在实际注入后消费
            messages.extend(msg)

    # 修复：Responses API 允许 assistant 文本消息出现在 tool_call/tool 对之间，
    # 但 Chat Completions 不允许。将中间的 assistant 纯文本消息推迟到 tool 序列结束后。
    messages = _fix_tool_message_order(messages)

    # 基础字段映射
    chat = {
        "model": model_cfg["target"],
        "messages": messages,
    }

    if "max_output_tokens" in body:
        chat["max_tokens"] = body["max_output_tokens"]
        chat["max_completion_tokens"] = body["max_output_tokens"]

    # 工具转换：Responses API → Chat Completions
    if "tools" in body:
        chat["tools"] = _map_tools(body["tools"])
    if "stream" in body:
        chat["stream"] = body["stream"]
        if body["stream"]:
            chat["stream_options"] = {**body.get("stream_options", {}), "include_usage": True}
    for key in ("tool_choice", "parallel_tool_calls"):
        if key in body:
            chat[key] = body[key]
    # 透传 Chat Completions 原生参数
    for key in ("temperature", "top_p", "stop", "service_tier", "frequency_penalty", "presence_penalty", "n"):
        if key in body:
            chat[key] = body[key]
    # Responses 专有字段 → 不透传上游，记录 warning
    for key in ("metadata", "max_tool_calls", "truncation"):
        if key in body:
            logger.warning(f"[transform] Responses 专有字段 '{key}' 已丢弃 (不透传 Chat Completions 上游)"
)

    # reasoning.effort → reasoning_effort
    # Responses API: {"reasoning": {"effort": "xhigh"}}
    # Chat Completions API: "reasoning_effort": "xhigh"
    reasoning = body.get("reasoning", {})
    if isinstance(reasoning, dict):
        effort = reasoning.get("effort")
        if effort in ("xhigh", "high", "medium", "low"):
            chat["reasoning_effort"] = effort

    # 结构化输出映射
    text_format = body.get("text", {}).get("format")
    if text_format:
        chat["response_format"] = _map_response_format(text_format)

    # 所有模型默认推理模型，assistant 消息的 reasoning_content 必须一致存在
    _ensure_reasoning_consistency(chat["messages"])
    return chat

def _map_input_item(item: dict, model_cfg: dict) -> list:
    """将单个 input 条目映射为 Chat Completions messages。返回 list 因为某些类型可能展开为多条。"""
    item_type = item.get("type")

    if item_type == "message":
        return [_map_message(item, model_cfg)]
    elif item_type == "function_call":
        return [_map_function_call(item)]
    elif item_type == "computer_call":
        return [_map_function_call(item)]
    elif item_type == "function_call_output":
        return [_map_function_call_output(item)]
    elif item_type == "computer_call_output":
        return [_map_computer_call_output(item)]
    elif item_type == "reasoning":
        return []
    elif item_type in ("web_search_call", "code_interpreter_call", "mcp_call"):
        logger.warning(f"[transform] 丢弃不支持的 input 类型: {item_type}")
        return []
    else:
        logger.warning(f"[transform] 丢弃未知 input 类型: {item_type}")
        return []

def _map_message(item: dict, model_cfg: dict) -> dict:
    """映射 message 类型的 input 条目。"""
    role = item.get("role", "user")
    if role == "developer":
        role = "system"
    content = item.get("content")

    if isinstance(content, str):
        return {"role": role, "content": content}

    if isinstance(content, list):
        mapped = []
        for part in content:
            part_type = part.get("type")
            if part_type == "input_text":
                mapped.append({"type": "text", "text": part.get("text", "")})
            elif part_type == "output_text":
                # assistant 消息的标准 content 类型
                mapped.append({"type": "text", "text": part.get("text", "")})
            elif part_type == "input_image":
                mapped.append(_map_input_image(part, model_cfg))
            elif part_type == "input_file":
                mapped.append(_map_input_file(part))
            else:
                logger.warning(f"[transform] 丢弃不支持的 content 类型: {part_type}")
        return {"role": role, "content": mapped} if mapped else {"role": role, "content": ""}

    return {"role": role, "content": str(content) if content else ""}

def _map_input_image(part: dict, model_cfg: dict) -> dict:
    """映射 input_image，根据 multimodal 配置分支。"""
    if model_cfg.get("multimodal", False):
        image_url = part.get("image_url", "")
        detail = part.get("detail", "auto")
        return {
            "type": "image_url",
            "image_url": {"url": image_url, "detail": detail},
        }
    else:
        logger.warning("[transform] 模型不支持多模态，input_image 已替换为占位文本")
        return {"type": "text", "text": "[image: unsupported]"}

def _map_input_file(part: dict) -> dict:
    """映射 input_file 为占位文本。"""
    filename = part.get("filename", "unknown")
    logger.debug(f"[transform] 文件内容 {part.get('file_id', '?')} 无法转换，已替换为占位标记 [{filename}]")
    return {"type": "text", "text": f"[file: {filename}]"}

def _map_function_call(item: dict) -> dict:
    """映射 function_call → assistant + tool_calls（所有模型默认推理模型，必须带 reasoning_content）。"""
    call_id = item.get("call_id") or item.get("id", "")
    name = item.get("name", "")
    arguments = item.get("arguments", "")
    if isinstance(arguments, dict):
        arguments = json.dumps(arguments)
    return {
        "role": "assistant",
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": arguments},
        }],
        "reasoning_content": "thinking",
    }

def _map_function_call_output(item: dict) -> dict:
    """映射 function_call_output → tool message。"""
    return {
        "role": "tool",
        "tool_call_id": item.get("call_id") or item.get("tool_call_id", ""),
        "content": item.get("output", ""),
    }

def _map_computer_call_output(item: dict) -> dict:
    """映射 computer_call_output → tool message。"""
    return {
        "role": "tool",
        "tool_call_id": item.get("call_id") or item.get("tool_call_id", ""),
        "content": item.get("output", ""),
    }

def _map_tools(tools: list) -> list:
    """将 Responses API 工具格式转换为 Chat Completions 格式。

    Responses API: {"type":"function", "name":"...", "parameters":{...}, "description":"...", "strict":...}
    Chat Completions: {"type":"function", "function": {"name":"...", "parameters":{...}, ...}}

    已有 "function" 键的工具保持不变（幂等）。
    非 "function" 类型的工具（custom、web_search 等）降级为 function 格式：
    优先提取已有 schema，没有则兜底为 {"input": "string"} 参数。
    """
    result = []
    for tool in tools:
        tool_type = tool.get("type", "function")
        tool_name = tool.get("name", "")
        if not tool_name and isinstance(tool.get("function"), dict):
            tool_name = tool["function"].get("name", "")

        if not tool_name:
            logger.warning(f"[transform] 跳过 name 为空的 tool: {tool}")
            continue

        if tool_type == "function":
            if "function" in tool:
                result.append(tool)
            else:
                func = {}
                for key in ("name", "description", "parameters", "strict"):
                    if key in tool:
                        func[key] = tool[key]
                result.append({"type": "function", "function": func})
        else:
            # 非标准 tool 降级为 function 格式
            logger.info(f"[transform] 非标准 tool 降级为 function: type={tool_type}, name={tool_name}")
            params = _extract_freeform_tool_params(tool)
            desc = tool.get("description", "")
            if desc:
                desc = f"{desc}\n[原始工具类型: {tool_type}]"
            result.append({
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": desc,
                    "parameters": params,
                },
            })
    return result

def _extract_freeform_tool_params(tool: dict) -> dict:
    """从非标准 tool 中提取 parameters schema，无 schema 则兜底为 input 字符串参数。

    优先级：input_schema → schema → parameters → 兜底 input 字符串。
    """
    for key in ("input_schema", "schema", "parameters"):
        schema = tool.get(key)
        if isinstance(schema, dict) and schema:
            return schema
    return {
        "type": "object",
        "properties": {
            "input": {
                "type": "string",
                "description": "工具输入内容（原始文本）",
            },
        },
        "required": ["input"],
    }

def _map_response_format(text_format: dict) -> dict:
    """映射 text.format → response_format。"""
    fmt_type = text_format.get("type", "text")

    if fmt_type == "json_schema":
        return {
            "type": "json_schema",
            "json_schema": {
                "name": text_format.get("name", ""),
                "schema": text_format.get("schema", {}),
                "strict": text_format.get("strict", False),
            },
        }
    else:
        return {"type": fmt_type}



# ─── 自注册 ───
from ..registry import register_request  # noqa: E402
register_request("responses", "chat_completions", responses_to_chat)
