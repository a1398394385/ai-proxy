"""协议转换公共 API — NxM 转换矩阵。"""

# ─── 请求转换 ───
from .request.anthropic import anthropic_to_chat              # noqa: F401
from .request.responses import responses_to_chat               # noqa: F401

# ─── 响应转换 ───
from .response.anthropic import chat_to_anthropic, create_anthropic_sse_stream  # noqa: F401
from .response.responses import (                              # noqa: F401
    chat_to_responses,
    create_responses_sse_stream,
    generate_response_id,
    output_items_to_messages,
    ResponsesStreamConverter,
    ToolBlockState,
)

# ─── 向后兼容别名 ───
create_codex_sse_stream = create_responses_sse_stream          # noqa: F401
CodexStreamConverter = ResponsesStreamConverter                 # noqa: F401
StreamState = ResponsesStreamConverter                          # noqa: F401

# ─── SSE 工具 ───
from ..sse_utils import _format_sse_event                     # noqa: F401
from ..sse_utils import _parse_sse_event, iter_sse_events      # noqa: F401

# ─── 路由 + 异常 ───
from .router import TransformRouter                            # noqa: F401
from .registry import UnsupportedFormat                         # noqa: F401

# ─── 内部工具（测试引用）───
from .request.responses import _map_tools, _map_response_format  # noqa: F401
from .request._utils import _fix_tool_message_order, _merge_consecutive_assistants  # noqa: F401
