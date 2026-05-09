"""Anthropic Messages API ↔ Chat Completions 转换模块。"""
import json
import logging
from dataclasses import dataclass, field

from .sse_utils import _format_sse_event
from .transform_responses import _parse_sse_event, iter_sse_events

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
    """检测 o-series 模型（o + 数字开头），大小写不敏感。"""
    import re
    return bool(re.match(r'^o\d', model.lower()))


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
    空 content list 时跳过（不产生空消息）。
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
        # 空 content list 且无 tool_messages → 跳过（不产生空消息）
        if not chat_content and not tool_calls and not tool_messages:
            return []
        result = []
        if role == "assistant" and tool_calls:
            msg: dict = {"role": "assistant", "tool_calls": tool_calls, "content": None}
            if chat_content:
                msg["content"] = chat_content
            result.append(msg)
        elif chat_content:
            result.append({"role": role, "content": chat_content})
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


# ─── Chat Completions SSE → Anthropic Messages SSE 流式转换 ───

@dataclass
class ToolBlockState:
    """单个 tool_use content block 的缓冲状态。"""
    id: str = ""
    name: str = ""
    pending_args: str = ""
    content_index: int = -1  # 分配后记录 index，用于后续 delta 发送


@dataclass
class AnthropicStreamState:
    message_id: str = ""
    model: str = ""
    content_index: int = 0
    current_block_type: str = ""
    tool_blocks: dict = field(default_factory=dict)  # int → ToolBlockState
    finish_reason: str = ""
    usage: dict = field(default_factory=dict)
    message_start_sent: bool = False
    message_delta_sent: bool = False  # 防止重复发送 message_delta
    open_blocks: set = field(default_factory=set)  # 未关闭的 content block 索引


_STREAM_FINISH_MAP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "end_turn",
}


def _close_open_blocks(state: AnthropicStreamState) -> list:
    """关闭所有未关闭的 content block，返回事件列表。"""
    events = []
    for idx in sorted(state.open_blocks):
        events.append(_format_sse_event("content_block_stop", {"index": idx}))
    state.open_blocks.clear()
    return events


def create_anthropic_sse_stream(upstream_response):
    """读取上游 Chat Completions SSE 流，逐事件 yield Anthropic Messages 格式的 SSE 字符串。

    upstream_response: 有 read(size) 方法的对象
    """
    state = AnthropicStreamState()

    try:
        for event in iter_sse_events(upstream_response):
            if event["event"] == "[DONE]":
                break

            data = event.get("data", {})
            if not data:
                continue

            # 捕获 model / id → 发送 message_start
            if not state.message_id:
                state.message_id = data.get("id", "")
                state.model = data.get("model", "")
                for event_str in _send_message_start(state):
                    yield event_str

            # 捕获 usage
            if "usage" in data and data["usage"]:
                state.usage = data["usage"]

            # 捕获 finish_reason（独立于 delta，防止最后一个 chunk 有 finish_reason 但 delta 为空）
            choices = data.get("choices", [])
            if choices:
                choice = choices[0]
                if choice.get("finish_reason") and not state.finish_reason:
                    state.finish_reason = choice["finish_reason"]

                delta = choice.get("delta", {})
                if delta:
                    for event_str in _process_anthropic_delta(delta, state):
                        yield event_str
    except Exception as e:
        # 流中断 → error 事件
        error_data = {
            "error": {
                "type": "stream_error",
                "message": f"Stream error: {e}",
            },
        }
        yield _format_sse_event("error", error_data)
        return

    # finish_reason 出现但 delta 为空时，_process_anthropic_delta 不会被调用，
    # 需要在此补发 message_delta 以携带 usage 和 stop_reason
    if state.finish_reason and not state.message_delta_sent:
        state.message_delta_sent = True
        events = _close_open_blocks(state)
        for event_str in events:
            yield event_str

        stop_reason = _STREAM_FINISH_MAP.get(state.finish_reason, "end_turn")
        delta_event = {
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
        }
        if state.usage:
            usage_out = {
                "input_tokens": state.usage.get("prompt_tokens", 0),
                "output_tokens": state.usage.get("completion_tokens", 0),
            }
            cached = state.usage.get("prompt_tokens_details", {}).get("cached_tokens")
            if cached is not None:
                usage_out["cache_read_input_tokens"] = cached
            delta_event["usage"] = usage_out

        yield _format_sse_event("message_delta", delta_event)

    # 读完所有 chunk，发送 message_stop
    yield _format_sse_event("message_stop", {})


def _send_message_start(state: AnthropicStreamState) -> list:
    """发送 message_start 事件（首次 chunk 含 id/model 时）。"""
    if state.message_start_sent:
        return []
    state.message_start_sent = True
    msg_start = {
        "message": {
            "id": state.message_id,
            "model": state.model,
            "role": "assistant",
            "content": [],
        },
    }
    return [_format_sse_event("message_start", msg_start)]


def _process_anthropic_delta(delta: dict, state: AnthropicStreamState) -> list:
    """处理单个 Chat delta，返回 Anthropic SSE 事件字符串列表。"""
    events = []

    # 发送 message_start
    events.extend(_send_message_start(state))

    # 推理 delta（双字段兼容：reasoning_content + reasoning）
    for key in ("reasoning_content", "reasoning"):
        if delta.get(key):
            reasoning_text = delta[key]
            if state.current_block_type != "thinking":
                idx = state.content_index
                events.append(_format_sse_event("content_block_start", {
                    "index": idx,
                    "content_block": {"type": "thinking", "thinking": ""},
                }))
                state.current_block_type = "thinking"
                state.open_blocks.add(idx)
            events.append(_format_sse_event("content_block_delta", {
                "index": state.content_index,
                "delta": {"type": "thinking_delta", "thinking": reasoning_text},
            }))
            break

    # 文本 delta
    content = delta.get("content", "")
    if content:
        if state.current_block_type != "text":
            events.extend(_close_open_blocks(state))
            idx = state.content_index
            events.append(_format_sse_event("content_block_start", {
                "index": idx,
                "content_block": {"type": "text", "text": ""},
            }))
            state.current_block_type = "text"
            state.open_blocks.add(idx)
        events.append(_format_sse_event("content_block_delta", {
            "index": state.content_index,
            "delta": {"type": "text_delta", "text": content},
        }))

    # 工具调用 delta
    tool_calls = delta.get("tool_calls")
    if tool_calls:
        for tc in tool_calls:
            idx = tc.get("index", 0)
            if idx not in state.tool_blocks:
                state.tool_blocks[idx] = ToolBlockState()

            ts = state.tool_blocks[idx]

            # id 到达
            tc_id = tc.get("id", "")
            if tc_id and not ts.id:
                ts.id = tc_id

            # name 到达
            tc_name = tc.get("function", {}).get("name", "")
            if tc_name and not ts.name:
                ts.name = tc_name

            # arguments 到达 — 缓冲
            tc_args = tc.get("function", {}).get("arguments")
            if tc_args:
                ts.pending_args += tc_args

            # id/name 到齐了但还没发 content_block_start → 先发 start + 缓冲的参数
            if ts.id and ts.name and ts.content_index < 0:
                ts.content_index = state.content_index
                events.append(_format_sse_event("content_block_start", {
                    "index": ts.content_index,
                    "content_block": {"type": "tool_use", "id": ts.id, "name": ts.name, "input": {}},
                }))
                state.open_blocks.add(ts.content_index)
                # 发送已缓冲的参数片段
                if ts.pending_args:
                    events.append(_format_sse_event("content_block_delta", {
                        "index": ts.content_index,
                        "delta": {"type": "input_json_delta", "partial_json": ts.pending_args},
                    }))
                state.content_index += 1
            elif ts.content_index >= 0 and tc_args:
                # start 已发，后续 arguments 片段直接发 delta
                events.append(_format_sse_event("content_block_delta", {
                    "index": ts.content_index,
                    "delta": {"type": "input_json_delta", "partial_json": tc_args},
                }))

    # finish_reason → message_delta
    if state.finish_reason and not state.message_delta_sent:
        state.message_delta_sent = True
        events.extend(_close_open_blocks(state))

        stop_reason = _STREAM_FINISH_MAP.get(state.finish_reason, "end_turn")

        delta_event = {
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
        }
        # 仅在 finish_reason 出现时携带 usage
        if state.usage:
            usage_out = {
                "input_tokens": state.usage.get("prompt_tokens", 0),
                "output_tokens": state.usage.get("completion_tokens", 0),
            }
            cached = state.usage.get("prompt_tokens_details", {}).get("cached_tokens")
            if cached is not None:
                usage_out["cache_read_input_tokens"] = cached
            delta_event["usage"] = usage_out

        events.append(_format_sse_event("message_delta", delta_event))

    return events
