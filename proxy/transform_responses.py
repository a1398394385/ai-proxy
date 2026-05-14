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

from .token_stats import _find_first
from .sse_utils import _format_sse_event

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
    # reasoning 追踪：上游 thinking mode 要求 assistant 消息必须携带 reasoning_content
    # Responses API 中 reasoning 项紧跟在 assistant 交互之后，需要注入到下一条 assistant 消息
    pending_reasoning = None  # 待注入的 reasoning 文本
    for item in body.get("input", []):
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


def _fix_tool_message_order(messages: list) -> list:
    """修复 Chat Completions 消息顺序。

    Responses API 允许：
    (a) assistant 纯文本消息出现在 tool_call/tool 对之间
    (b) 多条 assistant+tool_calls 消息连续出现

    但 Chat Completions 要求：
    (a) assistant+tool_calls 必须紧跟对应的 tool 消息，中间不能有其他消息
    (b) 多条连续的 assistant+tool_calls 必须合并为单条消息

    策略：
    1. 将夹在 tool_call ↔ tool 之间的 assistant 纯文本消息推迟到所有 tool 消息之后
    2. 将连续的 assistant+tool_calls 合并为单条消息
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
                    # 保留 reasoning_content（如果有的话）
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

            # 收集对应的 tool 消息
            tool_call_ids = {tc.get("id") for tc in all_tool_calls}
            tool_msgs = []
            while i < len(messages):
                m = messages[i]
                if m.get("role") == "tool" and m.get("tool_call_id") in tool_call_ids:
                    tool_msgs.append(m)
                    tool_call_ids.discard(m.get("tool_call_id"))
                    i += 1
                else:
                    break

            # 中间夹着的 assistant 纯文本消息推迟
            while i < len(messages):
                m = messages[i]
                if m.get("role") == "assistant" and not m.get("tool_calls"):
                    deferred.append(m)
                    i += 1
                else:
                    break

            result.append(merged)
            result.extend(tool_msgs)
        else:
            result.append(msg)
            i += 1

    result.extend(deferred)
    return result


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

    # reasoning_content（DeepSeek thinking mode）— 必须在 message 之前插入
    reasoning_content = message.get("reasoning_content") or message.get("thinking")
    if reasoning_content:
        output.append({
            "type": "reasoning",
            "id": f"rs_{uuid.uuid4().hex[:8]}",
            "summary": [{"type": "summary_text", "text": reasoning_content}],
        })

    # 文本和拒绝内容合并到同一 message item
    content = message.get("content")
    refusal = message.get("refusal")
    if content or refusal:
        msg_content = []
        if content:
            msg_content.append({"type": "output_text", "text": content, "annotations": []})
        if refusal:
            msg_content.append({"type": "refusal", "refusal": refusal})
        output.append({
            "type": "message",
            "id": f"msg_{uuid.uuid4().hex[:8]}",
            "role": "assistant",
            "content": msg_content,
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
        chunk = upstream_response.read(4096)
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

    def _handle_tool_call_delta(self, tc_delta: dict) -> list:
        events = []
        tc_index = tc_delta.get("index", 0)
        if tc_index not in self.tool_blocks:
            self.tool_blocks[tc_index] = ToolBlockState()
        block = self.tool_blocks[tc_index]

        tc_id = tc_delta.get("id", "")
        if tc_id:
            block.call_id = tc_id

        func = tc_delta.get("function", {})
        func_name = func.get("name", "")
        if func_name:
            block.name = func_name

        func_args = func.get("arguments", "")
        if func_args:
            block.accumulated_args += func_args

        # 延迟启动：call_id 和 name 都就绪时才发 output_item.added
        if not block.started and block.call_id and block.name:
            block.output_index = self.next_output_index
            self.next_output_index += 1
            block.item_id = f"fc_{uuid.uuid4().hex[:8]}"
            block.started = True
            events.append(self._format_sse("response.output_item.added", {
                "output_index": block.output_index,
                "item": {
                    "type": "function_call",
                    "id": block.item_id,
                    "call_id": block.call_id,
                    "name": block.name,
                    "arguments": "",
                    "status": "in_progress",
                },
            }))
            # 一次性发出之前积累的 args
            if block.accumulated_args:
                events.append(self._format_sse("response.function_call_arguments.delta", {
                    "output_index": block.output_index,
                    "call_id": block.call_id,
                    "delta": block.accumulated_args,
                }))
        elif block.started and func_args:
            events.append(self._format_sse("response.function_call_arguments.delta", {
                "output_index": block.output_index,
                "call_id": block.call_id,
                "delta": func_args,
            }))
        return events

    def _close_tool_blocks(self) -> list:
        events = []
        # 已就绪的块按 tc_index 排序（保持与上游 tool_calls 数组顺序一致）
        started = sorted(
            [(idx, b) for idx, b in self.tool_blocks.items() if b.started],
            key=lambda x: x[0],
        )
        unstarted = sorted(
            [(idx, b) for idx, b in self.tool_blocks.items() if not b.started],
            key=lambda x: x[0],
        )

        for tc_index, block in started:
            events.extend(self._emit_tool_block_done(block))

        for tc_index, block in unstarted:
            # Fallback
            block.call_id = block.call_id or f"tool_call_{tc_index}"
            block.name = block.name or "unknown_tool"
            block.output_index = self.next_output_index
            self.next_output_index += 1
            block.item_id = f"fc_{uuid.uuid4().hex[:8]}"
            block.started = True
            events.append(self._format_sse("response.output_item.added", {
                "output_index": block.output_index,
                "item": {
                    "type": "function_call",
                    "id": block.item_id,
                    "call_id": block.call_id,
                    "name": block.name,
                    "arguments": "",
                    "status": "in_progress",
                },
            }))
            if block.accumulated_args:
                events.append(self._format_sse("response.function_call_arguments.delta", {
                    "output_index": block.output_index,
                    "call_id": block.call_id,
                    "delta": block.accumulated_args,
                }))
            events.extend(self._emit_tool_block_done(block))

        return events

    def _convert_usage(self, raw: dict) -> dict:
        usage = {
            "input_tokens": _find_first(raw, ["prompt_tokens", "input_tokens"]),
            "output_tokens": _find_first(raw, ["completion_tokens", "output_tokens"]),
            "total_tokens": raw.get("total_tokens", 0),
        }
        details = {"cached_tokens": 0}
        for k in ("prompt_tokens_details", "input_tokens_details"):
            if raw.get(k):
                details.update(raw[k])
        usage["input_tokens_details"] = details
        out_det = raw.get("completion_tokens_details") or raw.get("output_tokens_details")
        usage["output_tokens_details"] = out_det or {"reasoning_tokens": 0}
        for k in ("cache_read_input_tokens", "cache_creation_input_tokens"):
            if k in raw and raw[k] is not None:
                usage[k] = raw[k]
        return usage

    def process_chunk(self, chunk: dict) -> list:
        events = []
        # 首个 chunk：更新 model，发 created 三件套
        if not self.created_sent:
            model = chunk.get("model", "")
            if model:
                self.model = model
            events.extend(self._emit_created())
        # 捕获 usage
        if chunk.get("usage"):
            self.final_usage = chunk["usage"]
        # 处理 choices
        for choice in chunk.get("choices", []):
            if choice.get("finish_reason"):
                self.finish_reason = choice["finish_reason"]
            delta = choice.get("delta", {})
            if not delta:
                continue
            # 顺序：content → refusal → reasoning → tool_calls
            content = delta.get("content")
            if content:
                events.extend(self._handle_text_delta(content))
            refusal = delta.get("refusal")
            if refusal:
                events.extend(self._handle_refusal_delta(refusal))
            for key in ("reasoning_content", "thinking", "reasoning"):
                reasoning = delta.get(key)
                if reasoning:
                    events.extend(self._handle_reasoning_delta(reasoning))
                    break
            tool_calls = delta.get("tool_calls")
            if tool_calls:
                for tc in tool_calls:
                    events.extend(self._handle_tool_call_delta(tc))
        return events

    def finish(self) -> list:
        events = []
        if not self.created_sent:
            self.model = self.model or ""
            events.extend(self._emit_created())
        if self.text_content_part_opened:
            events.extend(self._close_text_block())
        if self.refusal_opened:
            events.extend(self._close_refusal_block())
        if self.text_message_opened:
            events.extend(self._emit_message_item_done())
        if self.reasoning_opened:
            events.extend(self._close_reasoning_block())
        events.extend(self._close_tool_blocks())
        # 按 output_index 排序
        self.output_items.sort(key=lambda x: x[0])
        output_list = [item for _, item in self.output_items]
        # 构建 usage
        usage = self._convert_usage(self.final_usage) if self.final_usage else None
        # 构建 response
        if self.finish_reason in INCOMPLETE_REASON_MAP:
            incomplete_details = {"reason": INCOMPLETE_REASON_MAP[self.finish_reason]}
            status = "incomplete"
            resp = self._build_response_obj(status, usage=usage, output=output_list,
                                            incomplete_details=incomplete_details)
            events.append(self._format_sse("response.incomplete", {"response": resp}))
        else:
            status = "completed"
            resp = self._build_response_obj(status, usage=usage, output=output_list)
        events.append(self._format_sse("response.completed", {"response": resp}))
        events.append("data: [DONE]\n\n")
        return events

    def _emit_tool_block_done(self, block: "ToolBlockState") -> list:
        item = {
            "type": "function_call",
            "id": block.item_id,
            "call_id": block.call_id,
            "name": block.name,
            "arguments": block.accumulated_args,
            "status": "completed",
        }
        self.output_items.append((block.output_index, item))
        return [
            self._format_sse("response.function_call_arguments.done", {
                "output_index": block.output_index,
                "call_id": block.call_id,
                "arguments": block.accumulated_args,
            }),
            self._format_sse("response.output_item.done", {
                "output_index": block.output_index,
                "item": item,
            }),
        ]

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


# 向后兼容别名
StreamState = CodexStreamConverter


def create_codex_sse_stream(chunks_or_response, *, request_messages=None, response_store=None):
    """读取上游 SSE 流（file-like 或 SDK Iterable），生成 Responses API 格式的 SSE 事件。

    chunks_or_response:
        - file-like 对象（有 read 方法）→ 兼容旧路径（透传/过渡期）
        - Iterable[dict|ChatCompletionChunk] → 新路径（openai SDK 流）
    request_messages: chat_body["messages"]，用于构建 conversation
    response_store: ResponseStore 实例；非 None 时在 finish() 后存储 response
    """
    converter = CodexStreamConverter()
    converter.response_id = generate_response_id()

    # 兼容适配：检测输入类型
    if hasattr(chunks_or_response, 'read'):
        # 旧路径：file-like 对象
        chunks_iter = iter_sse_events(chunks_or_response)
        # iter_sse_events 返回 (event_type, data) 的 dict，需要提取 data
        chunks_iter = (e.get("data") or {} for e in chunks_iter if e.get("data"))
    else:
        # 新路径：SDK 流式迭代器
        def _to_dict(chunk):
            if hasattr(chunk, 'model_dump'):
                return chunk.model_dump()
            return chunk
        chunks_iter = (_to_dict(c) for c in chunks_or_response)

    for data_dict in chunks_iter:
        if isinstance(data_dict, str) and data_dict == "[DONE]":
            break
        if isinstance(data_dict, str):
            # "data:" 前缀后的 JSON 字符串
            try:
                data_dict = json.loads(data_dict)
            except json.JSONDecodeError:
                continue
        for sse_str in converter.process_chunk(data_dict):
            yield sse_str

    for sse_str in converter.finish():
        yield sse_str

    # finish() 返回后：存储 response
    if response_store is not None:
        from .response_store import ResponseRecord
        output_list = [item for _, item in converter.output_items]
        # output_items_to_messages 在同文件的 module 层级已导入，直接调用
        assistant_msgs = output_items_to_messages(output_list)
        messages_for_conv = [
            m for m in (request_messages or []) if m.get("role") != "system"
        ] + assistant_msgs
        usage = converter._convert_usage(converter.final_usage) if converter.final_usage is not None else None
        status = "incomplete" if converter.finish_reason in ("length", "content_filter") else "completed"
        record = ResponseRecord(
            response_id=converter.response_id,
            model=converter.model or "",
            output=output_list,
            conversation=messages_for_conv,
            usage=usage,
            status=status,
            created_at=time.time(),
            expires_at=time.time() + response_store.ttl_seconds,
        )
        response_store.put(record.response_id, record)


def output_items_to_messages(output_items: list) -> list:
    """将 Responses API output items 反转为 Chat Messages 格式（用于 conversation 历史）。

    - type=reasoning: 收集 summary 文本，注入到下一条 assistant message 的 reasoning_content 字段
    - type=message: 取第一个 output_text block 的 text；纯拒绝时 fallback ""
    - type=function_call: 全部收集后合并为单条 tool_calls 消息
    """
    result = []
    tool_calls = []
    pending_reasoning = None

    for item in output_items:
        itype = item.get("type")
        if itype == "reasoning":
            summary = item.get("summary", [])
            text = "".join(s.get("text", "") for s in summary if s.get("type") == "summary_text")
            if text:
                pending_reasoning = text
        elif itype == "message":
            # 先 flush 积累的 tool_calls
            if tool_calls:
                result.append({"role": "assistant", "content": None, "tool_calls": tool_calls})
                tool_calls = []
            text = next(
                (b["text"] for b in item.get("content", []) if b.get("type") == "output_text"),
                "",
            )
            msg = {"role": "assistant", "content": text}
            if pending_reasoning:
                msg["reasoning_content"] = pending_reasoning
                pending_reasoning = None
            result.append(msg)
        elif itype == "function_call":
            tool_calls.append({
                "id": item.get("call_id", item.get("id", "")),
                "type": "function",
                "function": {
                    "name": item.get("name", ""),
                    "arguments": item.get("arguments", ""),
                },
            })

    if tool_calls:
        result.append({"role": "assistant", "content": None, "tool_calls": tool_calls})

    return result
