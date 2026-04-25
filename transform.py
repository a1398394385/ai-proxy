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


def iter_sse_events(upstream_response):
    """逐 chunk 读取 HTTP 响应流，yield 解析后的 SSE 事件。

    upstream_response: 有 read(size) 方法的对象（http.client.HTTPResponse）
    """
    buf = b""
    while True:
        chunk = upstream_response.read(256)
        if not chunk:
            break
        buf += chunk
        while b"\n\n" in buf:
            raw, buf = buf.split(b"\n\n", 1)
            event = _parse_sse_event(raw.decode("utf-8", errors="replace"))
            if event:
                yield event


@dataclass
class StreamState:
    response_id: str = ""
    model: str = ""
    reasoning_id: str = ""
    # 推理 item
    reasoning_buffer: str = ""
    has_reasoning: bool = False
    reasoning_item_announced: bool = False
    # 文本 message item
    text_buffer: str = ""
    has_text: bool = False
    message_item_announced: bool = False
    # 工具调用积累
    tool_calls: dict = field(default_factory=dict)
    # 完成状态
    finish_reason: str = ""
    usage: dict = field(default_factory=dict)
    # 完整 output 数组
    output_items: list = field(default_factory=list)
    created_sent: bool = False

    @property
    def message_output_index(self) -> int:
        return 1 if self.has_reasoning else 0


def create_codex_sse_stream(upstream_response):
    """读取上游 SSE 流，逐事件 yield Responses API 格式的 SSE 字符串。

    upstream_response: http.client.HTTPResponse
    """
    state = StreamState()
    state.response_id = generate_response_id()

    for event in iter_sse_events(upstream_response):
        if event["event"] == "[DONE]":
            break

        data = event.get("data", {})
        if not data:
            continue

        # 捕获 model
        if not state.model:
            state.model = data.get("model", "")

        # 捕获 usage
        if "usage" in data and data["usage"]:
            state.usage = data["usage"]

        # 捕获 finish_reason
        choices = data.get("choices", [])
        if choices:
            choice = choices[0]
            if choice.get("finish_reason"):
                state.finish_reason = choice["finish_reason"]

            delta = choice.get("delta", {})
            if delta:
                for event_str in _process_delta(delta, state):
                    yield event_str

    # 所有 chunk 读完，发送 completion
    if state.created_sent:
        for event_str in _emit_completion(state):
            yield event_str


def _emit_created(state: StreamState) -> list:
    """阶段 1: response.created + response.metadata。

    设计意图：暂不发送 output_item.added，因为不知道第一个内容是推理还是文本。
    """
    events = []
    created = {
        "id": state.response_id,
        "object": "response",
        "model": state.model,
        "status": "in_progress",
        "output": [],
    }
    events.append(("response.created", created))
    metadata = {
        "model": state.model,
        "previous_response_id": None,
    }
    events.append(("response.metadata", metadata))
    state.created_sent = True
    return events


def _process_delta(delta: dict, state: StreamState) -> list:
    """处理单个 Chat Completions delta，返回 SSE 事件字符串列表。"""
    events = []

    # 首次：发送 created + metadata
    if not state.created_sent:
        for etype, edata in _emit_created(state):
            events.append(f"event: {etype}\ndata: {json.dumps(edata, ensure_ascii=False, separators=(',', ':'))}\n\n")

    # 推理 delta（检测顺序：reasoning_content → thinking → reasoning）
    for key in ("reasoning_content", "thinking", "reasoning"):
        if delta.get(key):
            reasoning_text = delta[key]
            if not state.has_reasoning:
                state.has_reasoning = True
                state.reasoning_id = f"rs_{uuid.uuid4().hex[:8]}"
                # output_item.added for reasoning
                events.append(
                    f'event: response.output_item.added\n'
                    f'data: {{"output_index":0,"item":{{"type":"reasoning",'
                    f'"id":"{state.reasoning_id}","summary":[],"status":"in_progress"}}}}\n\n'
                )
                state.reasoning_item_announced = True
            # reasoning delta
            state.reasoning_buffer += reasoning_text
            events.append(
                f'event: response.reasoning_summary_text.delta\n'
                f'data: {{"output_index":0,"summary_index":0,"delta":{json.dumps(reasoning_text, ensure_ascii=False)}}}\n\n'
            )
            break  # 只处理第一个命中的推理字段

    # 文本 delta
    content = delta.get("content", "")
    if content:
        if not state.message_item_announced:
            idx = state.message_output_index
            events.append(
                f'event: response.output_item.added\n'
                f'data: {{"output_index":{idx},"item":{{"type":"message","role":"assistant",'
                f'"content":[],"status":"in_progress"}}}}\n\n'
            )
            state.message_item_announced = True
        state.text_buffer += content
        state.has_text = True
        idx = state.message_output_index
        events.append(
            f'event: response.output_text.delta\n'
            f'data: {{"output_index":{idx},"content_index":0,"delta":{json.dumps(content, ensure_ascii=False)}}}\n\n'
        )

    # 工具调用 delta（积累，不发事件）
    tool_calls = delta.get("tool_calls")
    if tool_calls:
        for tc in tool_calls:
            idx = tc.get("index", 0)
            if idx not in state.tool_calls:
                state.tool_calls[idx] = {
                    "id": tc.get("id", ""),
                    "name": tc.get("function", {}).get("name", ""),
                    "arguments_buffer": "",
                }
            func_args = tc.get("function", {}).get("arguments", "")
            if func_args:
                state.tool_calls[idx]["arguments_buffer"] += func_args

    return events


def _emit_completion(state: StreamState) -> list:
    """阶段 5-6: 完成时发送 reasoning done, text done, tool calls, incomplete, completed。"""
    events = []

    # 推理完成
    if state.has_reasoning:
        events.append(
            f'event: response.reasoning_summary_text.done\n'
            f'data: {{"output_index":0,"summary_index":0,"text":{json.dumps(state.reasoning_buffer, ensure_ascii=False)}}}\n\n'
        )
        reasoning_item = {
            "type": "reasoning",
            "id": state.reasoning_id,
            "summary": [{"type": "summary_text", "text": state.reasoning_buffer}],
            "status": "completed",
        }
        events.append(
            f'event: response.output_item.done\n'
            f'data: {{"output_index":0,"item":{json.dumps(reasoning_item, ensure_ascii=False, separators=(",", ":"))}}}\n\n'
        )
        state.output_items.append(reasoning_item)

    # 文本完成
    if state.has_text:
        idx = state.message_output_index
        events.append(
            f'event: response.output_text.done\n'
            f'data: {{"output_index":{idx},"content_index":0,"text":{json.dumps(state.text_buffer, ensure_ascii=False)}}}\n\n'
        )
        message_item = {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": state.text_buffer}],
            "status": "completed",
        }
        events.append(
            f'event: response.output_item.done\n'
            f'data: {{"output_index":{idx},"item":{json.dumps(message_item, ensure_ascii=False, separators=(",", ":"))}}}\n\n'
        )
        state.output_items.append(message_item)

    # 工具调用完成（按 index 排序）
    sorted_tc = sorted(state.tool_calls.items(), key=lambda x: x[0])
    for i, (idx, tc) in enumerate(sorted_tc):
        tc_id = tc["id"] or f"call_{uuid.uuid4().hex[:8]}"
        if state.has_text:
            output_idx = state.message_output_index + i + 1
        else:
            output_idx = i + (1 if state.has_reasoning else 0)
        tc_item = {
            "type": "function_call",
            "id": tc_id,
            "call_id": tc_id,
            "name": tc["name"],
            "arguments": tc["arguments_buffer"],
        }
        events.append(
            f'event: response.output_item.done\n'
            f'data: {{"output_index":{output_idx},"item":{json.dumps(tc_item, ensure_ascii=False, separators=(",", ":"))}}}\n\n'
        )
        state.output_items.append(tc_item)

    # incomplete
    if state.finish_reason in INCOMPLETE_REASON_MAP:
        events.append(
            f'event: response.incomplete\n'
            f'data: {{"reason":{json.dumps(INCOMPLETE_REASON_MAP[state.finish_reason])}}}\n\n'
        )

    # completed
    usage = state.usage
    completed = {
        "id": state.response_id,
        "status": FINISH_REASON_MAP.get(state.finish_reason, "completed"),
        "output": state.output_items,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "input_tokens_details": {
                "cached_tokens": usage.get("prompt_tokens_details", {}).get("cached_tokens", 0),
            },
            "output_tokens_details": {
                "reasoning_tokens": usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0),
            },
        },
    }
    if state.finish_reason in INCOMPLETE_REASON_MAP:
        completed["incomplete_details"] = {"reason": INCOMPLETE_REASON_MAP[state.finish_reason]}
    events.append(
        f'event: response.completed\ndata: {json.dumps(completed, ensure_ascii=False, separators=(",", ":"))}\n\n'
    )

    return events
