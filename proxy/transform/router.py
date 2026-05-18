"""协议转换路由——委托注册表的 NxM 转换矩阵。"""

from .registry import UnsupportedFormat, lookup_request, lookup_response, lookup_stream


class TransformRouter:
    """协议转换路由——(client_format, upstream_format) → 转换函数。

    注册由各子模块末尾的 register_*() 调用完成，
    __init__.py 的 re-export 导入子模块时自动触发。
    """

    @classmethod
    def convert_request(cls, body: dict, client_format: str,
                        upstream_format: str, model_cfg: dict) -> dict:
        if client_format == upstream_format:
            return body
        func = lookup_request(client_format, upstream_format)
        if func is None:
            raise UnsupportedFormat(f"不支持的请求转换: {client_format} → {upstream_format}")
        return func(body, model_cfg)

    @classmethod
    def convert_response(cls, response: dict, upstream_format: str,
                         client_format: str) -> dict:
        if client_format == upstream_format:
            return response
        func = lookup_response(upstream_format, client_format)
        if func is None:
            raise UnsupportedFormat(f"不支持的响应转换: {upstream_format} → {client_format}")
        return func(response)

    @classmethod
    def stream_convert(cls, chunks, upstream_format: str, client_format: str, *,
                       request_messages=None, response_store=None):
        if client_format == upstream_format:
            yield from chunks
            return
        func = lookup_stream(upstream_format, client_format)
        if func is None:
            raise UnsupportedFormat(f"不支持的流式转换: {upstream_format} → {client_format}")
        yield from func(chunks, request_messages=request_messages,
                        response_store=response_store)
