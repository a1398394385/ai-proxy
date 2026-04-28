"""转换模块选择器 — 根据请求格式分发到对应转换模块。

公共接口：
- generate_response_id: 来自 transform_responses
- responses_to_chat: 来自 transform_responses（/v1/responses 路径）
- chat_to_responses: 来自 transform_responses
- create_codex_sse_stream: 来自 transform_responses
- _format_sse_event: 来自 sse_utils
- _parse_sse_event: 来自 transform_responses
- iter_sse_events: 来自 transform_responses
- StreamState: 来自 transform_responses
- CodexStreamConverter: 来自 transform_responses
- ToolBlockState: 来自 transform_responses
- _process_delta: 来自 transform_responses
- _emit_completion: 来自 transform_responses
- _map_tools: 来自 transform_responses
- _map_response_format: 来自 transform_responses
- anthropic_to_chat: 来自 transform_anthropic（/v1/messages 路径）— Task 4 后可用
- chat_to_anthropic: 来自 transform_anthropic — Task 5 后可用
- create_anthropic_sse_stream: 来自 transform_anthropic — Task 6 后可用
"""

from sse_utils import _format_sse_event  # noqa: F401 — re-export

from transform_responses import (  # noqa: F401 — re-export
    generate_response_id,
    responses_to_chat,
    chat_to_responses,
    create_codex_sse_stream,
    _parse_sse_event,
    iter_sse_events,
    StreamState,
    CodexStreamConverter,
    ToolBlockState,
    _map_tools,
    _map_response_format,
)

# Task 4-6 完成，激活 Anthropic re-export：
from transform_anthropic import anthropic_to_chat, chat_to_anthropic, create_anthropic_sse_stream  # noqa: F401
