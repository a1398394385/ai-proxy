# proxy/adapters/responses.py
"""ResponsesAdapter — OpenAI Responses API ↔ Chat Completions 双向转换。"""

from __future__ import annotations

from .base import ProtocolAdapter, UnsupportedFormat
from . import register_adapter


class ResponsesAdapter(ProtocolAdapter):
    """Responses API 协议适配器。

    当前支持: responses ↔ chat_completions 双向转换。
    未来扩展: responses ↔ messages 直接转换。
    """

    protocol = "responses"

    def request_to(self, upstream_format: str, body: dict, model_cfg: dict) -> dict:
        if upstream_format == "chat_completions":
            from proxy.transform_responses import responses_to_chat
            return responses_to_chat(body, model_cfg)
        raise UnsupportedFormat(f"responses → {upstream_format} 尚未实现")

    def response_from(self, upstream_format: str, response: dict) -> dict:
        if upstream_format == "chat_completions":
            from proxy.transform_responses import chat_to_responses
            return chat_to_responses(response)
        raise UnsupportedFormat(f"{upstream_format} → responses 尚未实现")

    def stream_from(self, upstream_format: str, chunks, *,
                    request_messages=None, response_store=None):
        if upstream_format == "chat_completions":
            from proxy.transform_responses import create_codex_sse_stream
            yield from create_codex_sse_stream(
                chunks,
                request_messages=request_messages,
                response_store=response_store,
            )
            return
        raise UnsupportedFormat(f"{upstream_format} → responses (stream) 尚未实现")


register_adapter(ResponsesAdapter)
