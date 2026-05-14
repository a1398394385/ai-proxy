"""协议适配器注册表——惰性发现 + 全局单例。"""

from __future__ import annotations

from .base import ProtocolAdapter, UnsupportedFormat  # noqa: F401 — re-export

_REGISTRY: dict[str, ProtocolAdapter] = {}
_discovered: bool = False


def register_adapter(cls: type) -> None:
    """注册一个 ProtocolAdapter 子类。各 Adapter 模块末尾调用。"""
    instance = cls()
    _REGISTRY[instance.protocol] = instance


def get_adapter(protocol: str) -> ProtocolAdapter | None:
    """获取 protocol 对应的 Adapter 实例。首次调用触发惰性发现。"""
    global _discovered
    if not _discovered:
        _discover_adapters()
    return _REGISTRY.get(protocol)


def _discover_adapters():
    """导入所有 Adapter 模块，触发自注册。"""
    global _discovered
    _discovered = True
    from . import responses   # noqa: F401
    from . import messages    # noqa: F401
