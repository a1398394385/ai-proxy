# proxy/adapters/messages.py
"""MessagesAdapter — Anthropic Messages API ↔ Chat Completions 双向转换。"""

from __future__ import annotations

from .base import ProtocolAdapter, UnsupportedFormat
from . import register_adapter


class MessagesAdapter(ProtocolAdapter):
    """Anthropic Messages API 协议适配器。

    当前支持: messages ↔ chat_completions 双向转换。
    未来扩展: messages ↔ responses 直接转换。
    """

    protocol = "messages"

    def request_to(self, upstream_format: str, body: dict, model_cfg: dict) -> dict:
        if upstream_format == "chat_completions":
            from proxy.transform_anthropic import anthropic_to_chat
            return anthropic_to_chat(body, model_cfg)
        raise UnsupportedFormat(f"messages → {upstream_format} 尚未实现")

    def response_from(self, upstream_format: str, response: dict) -> dict:
        if upstream_format == "chat_completions":
            from proxy.transform_anthropic import chat_to_anthropic
            return chat_to_anthropic(response)
        raise UnsupportedFormat(f"{upstream_format} → messages 尚未实现")

    def stream_from(self, upstream_format: str, chunks, *,
                    request_messages=None, response_store=None):
        if upstream_format == "chat_completions":
            from proxy.transform_anthropic import create_anthropic_sse_stream
            yield from create_anthropic_sse_stream(
                chunks,
                request_messages=request_messages,
                response_store=response_store,
            )
            return
        raise UnsupportedFormat(f"{upstream_format} → messages (stream) 尚未实现")


register_adapter(MessagesAdapter)
