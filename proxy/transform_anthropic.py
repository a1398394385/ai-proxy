"""Anthropic Messages API ↔ Chat Completions 转换模块。

请求转换（Anthropic → Chat）已迁移到 .transform_anthropic_request。
响应转换和 SSE 流式转换保留在此文件。
"""
import json
import logging
from dataclasses import dataclass, field

from .transform_anthropic_request import anthropic_to_chat  # noqa: F401

from .sse_utils import _format_sse_event
from .transform_responses import _parse_sse_event, iter_sse_events

logger = logging.getLogger(__name__)


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

    # 工具调用（SDK model_dump 可能含 tool_calls=None）
    tool_calls = message.get("tool_calls") or []
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
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
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


def create_anthropic_sse_stream(chunks_or_response, *, request_messages=None, response_store=None):
    """读取上游 SSE 流（file-like 或 SDK Iterable），生成 Anthropic Messages 格式的 SSE 事件。

    chunks_or_response: 同 create_codex_sse_stream。
    request_messages / response_store: 当前未使用，预留签名一致性。
    """
    state = AnthropicStreamState()

    # 兼容适配：检测输入类型
    if hasattr(chunks_or_response, 'read'):
        chunks_iter = iter_sse_events(chunks_or_response)
        chunks_iter = (e.get("data") or {} for e in chunks_iter if e.get("data"))
    else:
        def _to_dict(chunk):
            if hasattr(chunk, 'model_dump'):
                return chunk.model_dump()
            return chunk
        chunks_iter = (_to_dict(c) for c in chunks_or_response)

    try:
        for data_dict in chunks_iter:
            if isinstance(data_dict, str) and data_dict == "[DONE]":
                break
            if isinstance(data_dict, str):
                try:
                    data_dict = json.loads(data_dict)
                except json.JSONDecodeError:
                    continue
            if not data_dict:
                continue

            # 捕获 model / id → 发送 message_start
            if not state.message_id:
                state.message_id = data_dict.get("id", "")
                state.model = data_dict.get("model", "")
                for event_str in _send_message_start(state):
                    yield event_str

            if "usage" in data_dict and data_dict["usage"]:
                state.usage = data_dict["usage"]

            choices = data_dict.get("choices", [])
            if choices:
                choice = choices[0]
                if choice.get("finish_reason") and not state.finish_reason:
                    state.finish_reason = choice["finish_reason"]
                delta = choice.get("delta", {})
                if delta:
                    for event_str in _process_anthropic_delta(delta, state):
                        yield event_str
    except Exception as e:
        error_data = {
            "error": {"type": "stream_error", "message": f"Stream error: {e}"},
        }
        yield _format_sse_event("error", error_data)
        return

    # 补发 message_delta
    if state.finish_reason and not state.message_delta_sent:
        state.message_delta_sent = True
        events = _close_open_blocks(state)
        for event_str in events:
            yield event_str
        stop_reason = _STREAM_FINISH_MAP.get(state.finish_reason, "end_turn")
        delta_event = {"delta": {"stop_reason": stop_reason, "stop_sequence": None}}
        if state.usage:
            usage_out = {
                "input_tokens": state.usage.get("prompt_tokens", 0),
                "output_tokens": state.usage.get("completion_tokens", 0),
            }
            cached = (state.usage.get("prompt_tokens_details") or {}).get("cached_tokens")
            if cached is not None:
                usage_out["cache_read_input_tokens"] = cached
            delta_event["usage"] = usage_out
        yield _format_sse_event("message_delta", delta_event)

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
            cached = (state.usage.get("prompt_tokens_details") or {}).get("cached_tokens")
            if cached is not None:
                usage_out["cache_read_input_tokens"] = cached
            delta_event["usage"] = usage_out

        events.append(_format_sse_event("message_delta", delta_event))

    return events
