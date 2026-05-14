"""协议转换路由器——委托 Adapter 注册表的 N×M 转换矩阵。"""

from __future__ import annotations

from proxy.adapters import get_adapter


class TransformRouter:
    """协议转换路由——(client_format, upstream_format) → Adapter 方法。

    参数统一使用 client_format / upstream_format 命名：
    - convert_request: client_format → upstream_format
    - convert_response: upstream_format → client_format
    - stream_convert:   upstream_format → client_format
    """

    @classmethod
    def convert_request(cls, body: dict, client_format: str,
                        upstream_format: str, model_cfg: dict) -> dict:
        """客户端请求体 → 上游格式请求体。相同格式直接返回原始 body。"""
        if client_format == upstream_format:
            return body
        adapter = get_adapter(client_format)
        if adapter is None:
            raise KeyError(f"不支持的客户端协议: {client_format}")
        return adapter.request_to(upstream_format, body, model_cfg)

    @classmethod
    def convert_response(cls, response: dict, upstream_format: str,
                         client_format: str) -> dict:
        """上游响应 → 客户端格式响应。相同格式直接返回原始 response。"""
        if client_format == upstream_format:
            return response
        adapter = get_adapter(client_format)
        if adapter is None:
            raise KeyError(f"不支持的客户端协议: {client_format}")
        return adapter.response_from(upstream_format, response)

    @classmethod
    def stream_convert(cls, chunks, upstream_format: str, client_format: str, *,
                       request_messages=None, response_store=None):
        """上游 SSE 流 → 客户端格式 SSE 事件生成器。

        工厂函数统一签名：(chunks, *, request_messages=None, response_store=None)
        """
        if client_format == upstream_format:
            yield from chunks
            return
        adapter = get_adapter(client_format)
        if adapter is None:
            raise KeyError(f"不支持的客户端协议: {client_format}")
        yield from adapter.stream_from(
            upstream_format, chunks,
            request_messages=request_messages,
            response_store=response_store,
        )
