"""SDK 上游驱动——按 upstream_cfg 创建对应 SDK 客户端并调用。

支持三种上游格式:
- chat_completions  → openai.chat.completions
- responses         → openai.responses
- messages          → anthropic.messages
"""

from __future__ import annotations

import logging
import httpx
from openai import OpenAI

logger = logging.getLogger(__name__)


class UpstreamDriver:
    """多格式上游驱动——按 upstream_cfg 创建对应的 SDK 客户端并调用。

    Handler 每个请求创建新实例并在线程内使用，方法返回前调用 close()，
    无跨请求共享，无线程竞争。
    """

    def __init__(self, upstream_cfg: dict):
        self._cfg = upstream_cfg
        self.format = upstream_cfg.get("format", "chat_completions")
        self._openai: OpenAI | None = None
        self._anthropic: object | None = None  # Anthropic client

    # ── SDK 客户端懒初始化 ──

    @property
    def openai(self) -> OpenAI:
        """按需创建 OpenAI 客户端。"""
        if self._openai is None:
            timeout = self._cfg.get("timeout", 120)
            connect_timeout = self._cfg.get("connect_timeout", 10)
            ssl_verify = self._cfg.get("ssl_verify", True)
            self._openai = OpenAI(
                base_url=self._cfg["base_url"],
                api_key=self._cfg["api_key"],
                timeout=httpx.Timeout(
                    connect=connect_timeout,
                    read=timeout, write=timeout, pool=connect_timeout,
                ),
                max_retries=self._cfg.get("retry", 0),
                http_client=httpx.Client(verify=ssl_verify),
            )
        return self._openai

    @property
    def anthropic(self):
        """按需创建 Anthropic 客户端。"""
        if self._anthropic is None:
            from anthropic import Anthropic
            timeout = self._cfg.get("timeout", 120)
            connect_timeout = self._cfg.get("connect_timeout", 10)
            ssl_verify = self._cfg.get("ssl_verify", True)
            self._anthropic = Anthropic(
                base_url=self._cfg["base_url"],
                api_key=self._cfg["api_key"],
                timeout=httpx.Timeout(
                    connect=connect_timeout,
                    read=timeout, write=timeout, pool=connect_timeout,
                ),
                max_retries=self._cfg.get("retry", 0),
                http_client=httpx.Client(verify=ssl_verify),
            )
        return self._anthropic

    # ── 统一入口 ──

    def create(self, format: str, body: dict):
        """按 format 路由到对应 SDK 的非流式调用。"""
        if format == "chat_completions":
            return self.openai.chat.completions.create(**body)
        if format == "responses":
            return self.openai.responses.create(**body)
        if format == "messages":
            return self.anthropic.messages.create(**body)
        raise ValueError(f"不支持的上游格式: {format}")

    def create_stream(self, format: str, body: dict):
        """按 format 路由到对应 SDK 的流式调用。"""
        kwargs = dict(body)
        kwargs.pop("stream", None)
        if format == "chat_completions":
            kwargs.setdefault("stream_options", {"include_usage": True})
            return self.openai.chat.completions.create(stream=True, **kwargs)
        if format == "responses":
            return self.openai.responses.create(stream=True, **kwargs)
        if format == "messages":
            return self.anthropic.messages.create(stream=True, **kwargs)
        raise ValueError(f"不支持的上游格式: {format}")

    def close(self):
        """关闭底层 HTTP 客户端。"""
        if self._openai:
            self._openai.close()
            self._openai = None
        if self._anthropic:
            self._anthropic.close()
            self._anthropic = None
