"""协议转换路由器——(源格式, 目标格式) → 转换器 映射表。"""

from __future__ import annotations

from .transform import (
    responses_to_chat,
    chat_to_responses,
    create_codex_sse_stream,
    anthropic_to_chat,
    chat_to_anthropic,
    create_anthropic_sse_stream,
)


class TransformRouter:
    """协议转换路由——(源格式, 目标格式) → 转换器 映射表。"""

    # 请求转换：source（客户端格式） → target（上游格式）
    _request_converters: dict[tuple[str, str], object] = {
        ("responses",        "chat_completions"): responses_to_chat,
        ("messages",         "chat_completions"): anthropic_to_chat,
    }

    # 非流式响应转换：source（上游格式） → target（客户端格式）
    _response_converters: dict[tuple[str, str], object] = {
        ("chat_completions", "responses"):        chat_to_responses,
        ("chat_completions", "messages"):         chat_to_anthropic,
    }

    # 流式响应转换：source（上游 SSE 格式） → target（客户端 SSE 格式）
    _stream_converters: dict[tuple[str, str], object] = {
        ("chat_completions", "responses"):        create_codex_sse_stream,
        ("chat_completions", "messages"):         create_anthropic_sse_stream,
    }

    @classmethod
    def convert_request(cls, body: dict, source: str, target: str, model_cfg: dict) -> dict:
        """请求转换。KeyError 表示不支持的格式对。

        model_cfg: resolve_model() 返回值 {"target": str, "multimodal": bool, "upstream": dict}。
                   当 ConfigCache.resolve() 返回 None 时不含 "upstream" 键，
                   handler 应 fallback 到 CONFIG["upstream"]。
        """
        return cls._request_converters[(source, target)](body, model_cfg)

    @classmethod
    def convert_response(cls, response: dict, source: str, target: str) -> dict:
        """非流式响应转换。"""
        return cls._response_converters[(source, target)](response)

    @classmethod
    def stream_convert(cls, chunks, source: str, target: str, *,
                       request_messages=None, response_store=None):
        """流式响应转换（生成器）。

        工厂函数统一签名：(chunks, *, request_messages=None, response_store=None)
        """
        converter = cls._stream_converters[(source, target)]
        yield from converter(chunks, request_messages=request_messages,
                             response_store=response_store)
