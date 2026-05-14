"""proxy 包 — Codex Proxy / Anthropic Proxy 统一入口。

提供请求格式转换、配置管理、日志记录、Token 统计等公共接口。
所有内部模块以相对导入方式引用。

使用示例：
    from proxy import proxy_handler
    from proxy.common import CONFIG
"""

from .request_logger import (  # noqa: F401 — re-export
    REQUEST_TYPE_RESPONSES,
    REQUEST_TYPE_MESSAGES,
    REQUEST_TYPE_CHAT_COMPLETIONS,
    get_logger,
    init_logger,
    _generate_request_id,
)

from .common import (  # noqa: F401 — re-export
    CONFIG,
    load_config,
    resolve_model,
    config_cache,
    CONFIG_PATH,
    DATA_DB,
)

# 新架构: ProtocolAdapter + 注册表
from .adapters import get_adapter, UnsupportedFormat  # noqa: F401

# SSE 工具单独导出
from .sse_utils import _format_sse_event  # noqa: F401

# 向后兼容 re-export — 旧 import 路径暂时保留
from .transform import (  # noqa: F401
    responses_to_chat,
    chat_to_responses,
    create_codex_sse_stream,
    anthropic_to_chat,
    chat_to_anthropic,
    create_anthropic_sse_stream,
)

from .token_stats import record_token_stats  # noqa: F401 — re-export

from .agent_detector import detect_subagent  # noqa: F401
from .handler import ProxyHandler  # noqa: F401

from .transform_router import TransformRouter  # noqa: F401 — re-export
from .upstream_driver import UpstreamDriver  # noqa: F401 — re-export — re-export
