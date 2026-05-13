"""SDK 上游驱动——按 upstream_cfg 创建 openai SDK 客户端并调用。"""

from __future__ import annotations

import logging
import httpx
from openai import OpenAI

logger = logging.getLogger(__name__)


class UpstreamDriver:
    """SDK 上游驱动——按 upstream_cfg 创建 SDK 客户端并调用。"""

    def __init__(self, upstream_cfg: dict):
        self._cfg = upstream_cfg
        self.format = upstream_cfg.get("format", "chat_completions")
        self._openai_client: OpenAI | None = None

    @property
    def openai(self) -> OpenAI:
        if self._openai_client is None:
            timeout_cfg = self._cfg.get("timeout", 120)
            connect_timeout = self._cfg.get("connect_timeout", 10)
            ssl_verify = self._cfg.get("ssl_verify", True)

            self._openai_client = OpenAI(
                base_url=self._cfg["base_url"],
                api_key=self._cfg["api_key"],
                timeout=httpx.Timeout(
                    connect=connect_timeout,
                    read=timeout_cfg,
                    write=timeout_cfg,
                    pool=connect_timeout,
                ),
                max_retries=self._cfg.get("retry", 0),
                http_client=httpx.Client(verify=ssl_verify),
            )
        return self._openai_client

    # ── Chat Completions（openai SDK）──

    def chat_create(self, **kwargs) -> object:
        """非流式 Chat Completions。返回 ChatCompletion 对象。"""
        return self.openai.chat.completions.create(**kwargs)

    def chat_stream(self, **kwargs):
        """流式 Chat Completions。返回 Stream[ChatCompletionChunk]。"""
        kwargs = dict(kwargs)  # 拷贝，不修改调用者传入的 dict
        kwargs.pop("stream", None)
        kwargs.setdefault("stream_options", {"include_usage": True})
        return self.openai.chat.completions.create(stream=True, **kwargs)

    # ── 统一入口 ──

    def create(self, format: str, body: dict):
        """按 format 自动路由到对应 SDK 的非流式调用。"""
        if format == "chat_completions":
            return self.chat_create(**body)
        raise ValueError(f"不支持的上游格式: {format}")

    def create_stream(self, format: str, body: dict):
        """按 format 自动路由到对应 SDK 的流式调用。"""
        if format == "chat_completions":
            return self.chat_stream(**body)
        raise ValueError(f"不支持的上游格式: {format}")

    def close(self):
        """关闭底层 HTTP 客户端。"""
        if self._openai_client:
            self._openai_client.close()
            self._openai_client = None
