"""转换函数注册表——零内部依赖，子模块和 router 都从此导入。"""


class UnsupportedFormat(Exception):
    """不支持的转换格式组合。"""
    pass


# 三个字典统一使用 (client_fmt, upstream_fmt) 作为 key
_REQUEST_CONVERTERS = {}
_RESPONSE_CONVERTERS = {}
_STREAM_CONVERTERS = {}


def register_request(client_fmt: str, upstream_fmt: str, func) -> None:
    _REQUEST_CONVERTERS[(client_fmt, upstream_fmt)] = func


def register_response(client_fmt: str, upstream_fmt: str, func) -> None:
    _RESPONSE_CONVERTERS[(client_fmt, upstream_fmt)] = func


def register_stream(client_fmt: str, upstream_fmt: str, func) -> None:
    _STREAM_CONVERTERS[(client_fmt, upstream_fmt)] = func


def lookup_request(client_fmt: str, upstream_fmt: str):
    return _REQUEST_CONVERTERS.get((client_fmt, upstream_fmt))


def lookup_response(upstream_fmt: str, client_fmt: str):
    return _RESPONSE_CONVERTERS.get((client_fmt, upstream_fmt))


def lookup_stream(upstream_fmt: str, client_fmt: str):
    return _STREAM_CONVERTERS.get((client_fmt, upstream_fmt))
