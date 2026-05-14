# proxy/adapters/base.py
"""ProtocolAdapter 抽象基类——一个客户端协议的双向转换器。"""

from __future__ import annotations

from abc import ABC, abstractmethod


class UnsupportedFormat(Exception):
    """不支持的转换格式组合。"""
    pass


class ProtocolAdapter(ABC):
    """一个客户端协议的双向转换器。

    负责两条转换路径：
    - 请求方向: client_format → upstream_format  (request_to)
    - 响应方向: upstream_format → client_format  (response_from / stream_from)
    """

    @property
    @abstractmethod
    def protocol(self) -> str:
        """客户端协议名: "responses" | "messages" """
        ...

    @abstractmethod
    def request_to(self, upstream_format: str, body: dict, model_cfg: dict) -> dict:
        """客户端请求体 → 目标上游格式的请求体。

        model_cfg: {"target": str, "multimodal": bool, "upstream": dict}
        不支持的 upstream_format → raise UnsupportedFormat
        """
        ...

    @abstractmethod
    def response_from(self, upstream_format: str, response: dict) -> dict:
        """上游响应 dict → 客户端协议格式的响应 dict。

        不支持的 upstream_format → raise UnsupportedFormat
        """
        ...

    @abstractmethod
    def stream_from(self, upstream_format: str, chunks, *,
                    request_messages=None, response_store=None):
        """上游 SSE 流 → 客户端协议格式的 SSE 事件生成器。

        不支持的 upstream_format → raise UnsupportedFormat
        """
        ...
