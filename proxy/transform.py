"""转换模块选择器 — 根据请求格式分发到对应转换模块。

公共接口：
- generate_response_id: 来自 transform_responses
- responses_to_chat: 来自 transform_responses（/v1/responses 路径）
- chat_to_responses: 来自 transform_responses
- create_codex_sse_stream: 来自 transform_responses (旧名，别名 → create_responses_sse_stream)
- _format_sse_event: 来自 sse_utils
- _parse_sse_event: 来自 transform_responses
- iter_sse_events: 来自 transform_responses
- StreamState: 来自 transform_responses
- CodexStreamConverter: 来自 transform_responses (旧名，别名 → ResponsesStreamConverter)
- ToolBlockState: 来自 transform_responses
- output_items_to_messages: 来自 transform_responses
- ResponsesStreamConverter: 来自 transform_responses (Task 6-7 重命名)
- create_responses_sse_stream: 来自 transform_responses (Task 6-7 重命名)
- _map_tools: 来自 transform_responses
- _map_response_format: 来自 transform_responses
- anthropic_to_chat: 来自 transform_anthropic（/v1/messages 路径）— Task 4 后可用
- chat_to_anthropic: 来自 transform_anthropic — Task 5 后可用
- create_anthropic_sse_stream: 来自 transform_anthropic — Task 6 后可用
"""

from .sse_utils import _format_sse_event  # noqa: F401 — re-export

from .transform_responses import (  # noqa: F401 — re-export
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
    output_items_to_messages,
)

# 注意：此文件将在旧转换模块删除后变为纯 re-export。
# 新代码请使用 proxy.adapters.get_adapter() + ProtocolAdapter 接口。
from .transform_anthropic import anthropic_to_chat, chat_to_anthropic, create_anthropic_sse_stream  # noqa: F401

# SDK 驱动和转换路由（Task 2-3 新增）
from .transform_router import TransformRouter  # noqa: F401

# 重命名别名（Task 6-7：CodexStreamConverter → ResponsesStreamConverter）
ResponsesStreamConverter = CodexStreamConverter  # noqa: F401
create_responses_sse_stream = create_codex_sse_stream  # noqa: F401
