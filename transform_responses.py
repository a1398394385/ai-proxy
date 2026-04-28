"""Responses API ↔ Chat Completions 转换模块。

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

from token_stats import _find_first
from sse_utils import _format_sse_event

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

    # 工具转换：Responses API → Chat Completions
    if "tools" in body:
        chat["tools"] = _map_tools(body["tools"])
    if "stream" in body:
        chat["stream"] = body["stream"]
        if body["stream"]:
            chat["stream_options"] = {"include_usage": True}
    for key in ("tool_choice", "parallel_tool_calls"):
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
    """映射 function_call → assistant + tool_calls。"""
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
    非 "function" 类型的工具（custom、web_search 等）Chat Completions 不支持，丢弃并记录警告。
    """
    result = []
    for tool in tools:
        if tool.get("type") != "function":
            logger.warning(f"[transform] 丢弃不支持的 tool 类型: {tool.get('type')} ({tool.get('name', '?')})")
            continue
        if "function" in tool:
            result.append(tool)
        else:
            func = {}
            for key in ("name", "description", "parameters", "strict"):
                if key in tool:
                    func[key] = tool[key]
            result.append({"type": "function", "function": func})
    return result


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
class ToolBlockState:
    """工具调用块的中间状态，每个 tool_calls index 对应一个实例。"""
    output_index: int = -1
    call_id: str = ""
    name: str = ""
    accumulated_args: str = ""
    started: bool = False
    item_id: str = ""          # added/done 必须复用同一 ID


@dataclass
class CodexStreamConverter:
    """完整的 Codex SSE 流转换器，替代旧 StreamState + 三个顶层函数。"""

    response_id: str = ""
    model: str = ""
    next_output_index: int = 0

    # 文本消息状态
    text_message_id: str = ""
    text_output_index: int = -1
    text_message_opened: bool = False
    text_content_part_opened: bool = False
    accumulated_text: str = ""

    # 推理状态
    reasoning_id: str = ""
    reasoning_output_index: int = -1
    reasoning_opened: bool = False
    accumulated_reasoning: str = ""

    # 拒绝状态
    refusal_opened: bool = False
    refusal_content_index: int = 0   # 在 _handle_refusal_delta 首次打开时保存，避免时序竞态
    accumulated_refusal: str = ""

    # 工具调用状态（key: tool_calls index → ToolBlockState）
    tool_blocks: dict = field(default_factory=dict)

    # 完成状态
    finish_reason: str = ""
    final_usage: Optional[dict] = None   # None = 未收到 usage chunk

    # output_items 存 (output_index, item) 元组，finish() 中按 output_index 排序
    output_items: list = field(default_factory=list)
    created_sent: bool = False

    def _format_sse(self, event_type: str, data: dict) -> str:
        return _format_sse_event(event_type, data)

    def _build_response_obj(
        self,
        status: str,
        usage: dict = None,
        output: list = None,
        incomplete_details: dict = None,
    ) -> dict:
        obj = {
            "id": self.response_id,
            "object": "response",
            "created_at": int(time.time()),
            "status": status,
            "model": self.model,
            "output": output if output is not None else [],
            "usage": usage or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        }
        if incomplete_details is not None:
            obj["incomplete_details"] = incomplete_details
        return obj

    def _emit_created(self) -> list:
        resp = self._build_response_obj("in_progress")
        events = [
            self._format_sse("response.created",     {"response": resp}),
            self._format_sse("response.in_progress", {"response": resp}),
            self._format_sse("response.metadata",    {"headers": {"model": self.model}}),
        ]
        self.created_sent = True
        return events

    def _handle_text_delta(self, text: str) -> list:
        events = []
        if not self.text_message_opened:
            self.text_output_index = self.next_output_index
            self.next_output_index += 1
            self.text_message_id = f"msg_{uuid.uuid4().hex[:8]}"
            self.text_message_opened = True
            events.append(self._format_sse("response.output_item.added", {
                "output_index": self.text_output_index,
                "item": {
                    "type": "message",
                    "id": self.text_message_id,
                    "status": "in_progress",
                    "role": "assistant",
                    "content": [],
                },
            }))
        if not self.text_content_part_opened:
            self.text_content_part_opened = True
            events.append(self._format_sse("response.content_part.added", {
                "output_index": self.text_output_index,
                "content_index": 0,
                "part": {"type": "output_text", "text": "", "annotations": []},
            }))
        self.accumulated_text += text
        events.append(self._format_sse("response.output_text.delta", {
            "output_index": self.text_output_index,
            "content_index": 0,
            "delta": text,
        }))
        return events

    def _close_text_block(self) -> list:
        if not self.text_content_part_opened:
            return []
        events = [
            self._format_sse("response.output_text.done", {
                "output_index": self.text_output_index,
                "content_index": 0,
                "text": self.accumulated_text,
            }),
            self._format_sse("response.content_part.done", {
                "output_index": self.text_output_index,
                "content_index": 0,
                "part": {"type": "output_text", "text": self.accumulated_text, "annotations": []},
            }),
        ]
        self.text_content_part_opened = False
        return events

    def _handle_refusal_delta(self, refusal: str) -> list:
        events = []
        if not self.text_message_opened:
            self.text_output_index = self.next_output_index
            self.next_output_index += 1
            self.text_message_id = f"msg_{uuid.uuid4().hex[:8]}"
            self.text_message_opened = True       # 必须在 added 后置 True，纯 refusal 场景靠此使 finish() 步骤 4 命中
            events.append(self._format_sse("response.output_item.added", {
                "output_index": self.text_output_index,
                "item": {
                    "type": "message",
                    "id": self.text_message_id,
                    "status": "in_progress",
                    "role": "assistant",
                    "content": [],
                },
            }))
        if not self.refusal_opened:
            self.refusal_content_index = 1 if self.text_content_part_opened else 0
            self.refusal_opened = True
            events.append(self._format_sse("response.content_part.added", {
                "output_index": self.text_output_index,
                "content_index": self.refusal_content_index,
                "part": {"type": "refusal", "refusal": ""},
            }))
        self.accumulated_refusal += refusal
        events.append(self._format_sse("response.refusal.delta", {
            "output_index": self.text_output_index,
            "content_index": self.refusal_content_index,
            "delta": refusal,
        }))
        return events

    def _close_refusal_block(self) -> list:
        if not self.refusal_opened:
            return []
        return [
            self._format_sse("response.refusal.done", {
                "output_index": self.text_output_index,
                "content_index": self.refusal_content_index,
                "refusal": self.accumulated_refusal,
            }),
            self._format_sse("response.content_part.done", {
                "output_index": self.text_output_index,
                "content_index": self.refusal_content_index,
                "part": {"type": "refusal", "refusal": self.accumulated_refusal},
            }),
        ]

    def _emit_message_item_done(self) -> list:
        content = []
        if self.accumulated_text:
            content.append({
                "type": "output_text",
                "text": self.accumulated_text,
                "annotations": [],
            })
        if self.accumulated_refusal:
            content.append({"type": "refusal", "refusal": self.accumulated_refusal})
        if not content:
            content.append({"type": "output_text", "text": "", "annotations": []})
        item = {
            "type": "message",
            "id": self.text_message_id,
            "status": "completed",
            "role": "assistant",
            "content": content,
        }
        self.output_items.append((self.text_output_index, item))
        return [self._format_sse("response.output_item.done", {
            "output_index": self.text_output_index,
            "item": item,
        })]

    def _handle_reasoning_delta(self, reasoning: str) -> list:
        events = []
        if not self.reasoning_opened:
            self.reasoning_output_index = self.next_output_index
            self.next_output_index += 1
            self.reasoning_id = f"rs_{uuid.uuid4().hex[:8]}"
            self.reasoning_opened = True
            events.append(self._format_sse("response.output_item.added", {
                "output_index": self.reasoning_output_index,
                "item": {"type": "reasoning", "id": self.reasoning_id, "summary": []},
            }))
        self.accumulated_reasoning += reasoning
        events.append(self._format_sse("response.reasoning.delta", {
            "output_index": self.reasoning_output_index,
            "delta": reasoning,
        }))
        return events

    def _close_reasoning_block(self) -> list:
        if not self.reasoning_opened:
            return []
        item = {
            "type": "reasoning",
            "id": self.reasoning_id,
            "summary": [{"type": "summary_text", "text": self.accumulated_reasoning}],
        }
        self.output_items.append((self.reasoning_output_index, item))
        return [
            self._format_sse("response.reasoning.done", {
                "output_index": self.reasoning_output_index,
                "text": self.accumulated_reasoning,
            }),
            self._format_sse("response.output_item.done", {
                "output_index": self.reasoning_output_index,
                "item": item,
            }),
        ]


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
    # 即使从未发过 created（无 delta 的空响应），也需发送 completed 让客户端正常结束
    for event_str in _emit_completion(state):
        yield event_str


def _emit_created(state: StreamState) -> list:
    """阶段 1: response.created + response.metadata。

    设计意图：暂不发送 output_item.added，因为不知道第一个内容是推理还是文本。
    """
    events = []
    created = {
        "type": "response.created",
        "response": {
            "id": state.response_id,
            "object": "response",
            "model": state.model,
            "status": "in_progress",
            "output": [],
        },
    }
    events.append(("response.created", created))
    metadata = {
        "type": "response.metadata",
        "headers": {"model": state.model},
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
            events.append(_format_sse_event(etype, edata))

    # 推理 delta（检测顺序：reasoning_content → thinking → reasoning）
    for key in ("reasoning_content", "thinking", "reasoning"):
        if delta.get(key):
            reasoning_text = delta[key]
            if not state.has_reasoning:
                state.has_reasoning = True
                state.reasoning_id = f"rs_{uuid.uuid4().hex[:8]}"
                # output_item.added for reasoning
                events.append(_format_sse_event("response.output_item.added", {
                    "output_index": 0,
                    "item": {"type": "reasoning", "id": state.reasoning_id, "summary": [], "status": "in_progress"},
                }))
                state.reasoning_item_announced = True
            # reasoning delta
            state.reasoning_buffer += reasoning_text
            events.append(_format_sse_event("response.reasoning_summary_text.delta", {
                "output_index": 0, "summary_index": 0, "delta": reasoning_text,
            }))
            break  # 只处理第一个命中的推理字段

    # 文本 delta
    content = delta.get("content", "")
    if content:
        if not state.message_item_announced:
            idx = state.message_output_index
            events.append(_format_sse_event("response.output_item.added", {
                "output_index": idx,
                "item": {"type": "message", "role": "assistant", "content": [], "status": "in_progress"},
            }))
            state.message_item_announced = True
        state.text_buffer += content
        state.has_text = True
        idx = state.message_output_index
        events.append(_format_sse_event("response.output_text.delta", {
            "output_index": idx, "content_index": 0, "delta": content,
        }))

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
        events.append(_format_sse_event("response.reasoning_summary_text.done", {
            "output_index": 0, "summary_index": 0, "text": state.reasoning_buffer,
        }))
        reasoning_item = {
            "type": "reasoning",
            "id": state.reasoning_id,
            "summary": [{"type": "summary_text", "text": state.reasoning_buffer}],
            "status": "completed",
        }
        events.append(_format_sse_event("response.output_item.done", {
            "output_index": 0, "item": reasoning_item,
        }))
        state.output_items.append(reasoning_item)

    # 文本完成
    if state.has_text:
        idx = state.message_output_index
        events.append(_format_sse_event("response.output_text.done", {
            "output_index": idx, "content_index": 0, "text": state.text_buffer,
        }))
        message_item = {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": state.text_buffer}],
            "status": "completed",
        }
        events.append(_format_sse_event("response.output_item.done", {
            "output_index": idx, "item": message_item,
        }))
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
        events.append(_format_sse_event("response.output_item.done", {
            "output_index": output_idx, "item": tc_item,
        }))
        state.output_items.append(tc_item)

    # incomplete
    if state.finish_reason in INCOMPLETE_REASON_MAP:
        events.append(_format_sse_event("response.incomplete", {
            "response": {"incomplete_details": {"reason": INCOMPLETE_REASON_MAP[state.finish_reason]}},
        }))

    # completed
    raw = state.usage
    usage = {
        "input_tokens": _find_first(raw, ["prompt_tokens", "input_tokens"]),
        "output_tokens": _find_first(raw, ["completion_tokens", "output_tokens"]),
        "total_tokens": raw.get("total_tokens", 0),
    }
    # 合并两个 details dict，始终确保 cached_tokens 字段存在（Codex CLI 要求）
    details = {"cached_tokens": 0}
    for k in ("prompt_tokens_details", "input_tokens_details"):
        if raw.get(k):
            details.update(raw[k])
    usage["input_tokens_details"] = details
    output_details = raw.get("completion_tokens_details") if "completion_tokens_details" in raw else raw.get("output_tokens_details")
    usage["output_tokens_details"] = output_details or {}
    # 透传 Anthropic 格式顶层缓存字段
    for k in ("cache_read_input_tokens", "cache_creation_input_tokens"):
        if k in raw and raw[k] is not None:
            usage[k] = raw[k]
    completed_response = {
        "id": state.response_id,
        "status": FINISH_REASON_MAP.get(state.finish_reason, "completed"),
        "output": state.output_items,
        "usage": usage,
    }
    if state.finish_reason in INCOMPLETE_REASON_MAP:
        completed_response["incomplete_details"] = {"reason": INCOMPLETE_REASON_MAP[state.finish_reason]}
    events.append(_format_sse_event("response.completed", {"response": completed_response}))

    return events
