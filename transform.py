"""纯转换逻辑模块 — 无 IO，可独立测试。

包含：
- responses_to_chat(): Responses API → Chat Completions
- chat_to_responses(): Chat Completions → Responses API
- StreamState + create_codex_sse_stream(): SSE 流转换
- SSE 解析器 iter_sse_events + _parse_sse_event
- generate_response_id(): 生成 resp-{timestamp_ms}-{random_hex8}
"""

import json
import uuid
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


def generate_response_id() -> str:
    """生成 OpenAI 规范 response ID: resp-{timestamp_ms}-{random_hex8}"""
    ts = int(time.time() * 1000)
    rand = uuid.uuid4().hex[:8]
    return f"resp-{ts}-{rand}"


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
    for item in body.get("input", []):
        msg = _map_input_item(item, model_cfg)
        if msg is not None:
            messages.extend(msg)

    # 基础字段映射
    chat = {
        "model": model_cfg["target"],
        "messages": messages,
    }

    if "max_output_tokens" in body:
        chat["max_tokens"] = body["max_output_tokens"]

    # 透传字段
    for key in ("tools", "tool_choice", "parallel_tool_calls", "stream"):
        if key in body:
            chat[key] = body[key]

    # reasoning.effort 透传
    reasoning = body.get("reasoning", {})
    if reasoning and "effort" in reasoning:
        chat["reasoning"] = {"effort": reasoning["effort"]}

    # 结构化输出映射
    text_format = body.get("text", {}).get("format")
    if text_format:
        chat["response_format"] = _map_response_format(text_format)

    return chat


def _map_input_item(item: dict, model_cfg: dict) -> list:
    """将单个 input 条目映射为 Chat Completions messages。返回 list 因为某些类型可能展开为多条。"""
    item_type = item.get("type")

    if item_type == "message":
        return [_map_message(item, model_cfg)]
    elif item_type == "function_call":
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
    content = item.get("content")

    if isinstance(content, str):
        return {"role": role, "content": content}

    if isinstance(content, list):
        mapped = []
        for part in content:
            part_type = part.get("type")
            if part_type == "input_text":
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
    """映射 function_call → assistant + tool_calls。"""
    call_id = item.get("id", "")
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
    }


def _map_function_call_output(item: dict) -> dict:
    """映射 function_call_output → tool message。"""
    return {
        "role": "tool",
        "tool_call_id": item.get("tool_call_id", ""),
        "content": item.get("output", ""),
    }


def _map_computer_call_output(item: dict) -> dict:
    """映射 computer_call_output → tool message。"""
    return {
        "role": "tool",
        "tool_call_id": item.get("tool_call_id", ""),
        "content": item.get("output", ""),
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


FINISH_REASON_MAP = {
    "stop": "completed",
    "length": "incomplete",
    "tool_calls": "completed",
    "content_filter": "incomplete",
}

INCOMPLETE_REASON_MAP = {
    "length": "max_tokens",
    "content_filter": "content_filter",
}


def chat_to_responses(response: dict) -> dict:
    """Chat Completions → Responses API 非流式响应转换。"""
    chat_id = response.get("id", "")
    if chat_id.startswith("chatcmpl-"):
        resp_id = "resp-" + chat_id[len("chatcmpl-"):]
    else:
        resp_id = f"resp-{uuid.uuid4().hex[:8]}"

    choice = response.get("choices", [{}])[0]
    message = choice.get("message", {})
    finish_reason = choice.get("finish_reason", "stop")

    output = []

    # 文本内容
    content = message.get("content")
    if content:
        output.append({
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": content}],
            "status": FINISH_REASON_MAP.get(finish_reason, "completed"),
        })

    # 拒绝内容
    refusal = message.get("refusal")
    if refusal:
        output.append({
            "type": "message",
            "role": "assistant",
            "content": [{"type": "refusal", "refusal": refusal}],
            "status": FINISH_REASON_MAP.get(finish_reason, "completed"),
        })

    # 工具调用
    tool_calls = message.get("tool_calls", [])
    for tc in tool_calls:
        func = tc.get("function", {})
        output.append({
            "type": "function_call",
            "id": tc.get("id", ""),
            "call_id": tc.get("id", ""),
            "name": func.get("name", ""),
            "arguments": func.get("arguments", ""),
        })

    result = {
        "id": resp_id,
        "model": response.get("model", ""),
        "status": FINISH_REASON_MAP.get(finish_reason, "completed"),
        "output": output,
    }

    # incomplete_details
    if finish_reason in INCOMPLETE_REASON_MAP:
        result["incomplete_details"] = {
            "reason": INCOMPLETE_REASON_MAP[finish_reason],
        }

    # usage 映射
    usage = response.get("usage", {})
    result["usage"] = {
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
        "input_tokens_details": {
            "cached_tokens": usage.get("prompt_tokens_details", {}).get("cached_tokens", 0),
        },
        "output_tokens_details": {
            "reasoning_tokens": usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0),
        },
    }

    return result
