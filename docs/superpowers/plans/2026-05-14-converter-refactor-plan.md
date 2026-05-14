# 转换器一步重构实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 一步到位重构转换器架构——引入 ProtocolAdapter 策略模式，消除硬编码 chat_completions 中间 pivot，扩展 UpstreamDriver 支持三上游格式。

**Architecture:** 新建 `proxy/adapters/` 包（ProtocolAdapter 抽象基类 + 注册表惰性发现），ResponsesAdapter/MessagesAdapter 吸收旧转换逻辑，TransformRouter 委托注册表，UpstreamDriver 支持 chat_completions/responses/messages 三格式，Handler 消除硬编码 pivot。

**Tech Stack:** Python 3 标准库 + openai SDK + anthropic SDK + unittest

---

### Task 1: 创建 ProtocolAdapter 抽象基类和 UnsupportedFormat 异常

**Files:**
- Create: `proxy/adapters/__init__.py`（空包标记）
- Create: `proxy/adapters/base.py`

- [ ] **Step 1: 创建 adapters 包目录**

```bash
mkdir -p proxy/adapters
touch proxy/adapters/__init__.py
```

- [ ] **Step 2: 编写 base.py**

```python
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
```

- [ ] **Step 3: 验证 base.py 可正常导入**

```bash
python3 -c "from proxy.adapters.base import ProtocolAdapter, UnsupportedFormat; print('OK')"
```
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add proxy/adapters/__init__.py proxy/adapters/base.py
git commit -m "feat: 创建 ProtocolAdapter 抽象基类和 UnsupportedFormat 异常"
```

---

### Task 2: 实现适配器注册表 + 惰性发现

**Files:**
- Overwrite: `proxy/adapters/__init__.py`

- [ ] **Step 1: 编写注册表逻辑**

```python
# proxy/adapters/__init__.py
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
```

- [ ] **Step 2: 验证导入（此时 adapters 模块还不存在，预期 ImportError）**

```bash
python3 -c "from proxy.adapters import get_adapter; print('OK')"
```
Expected: ImportError (responses/messages 模块尚不存在)

- [ ] **Step 3: Commit**

```bash
git add proxy/adapters/__init__.py
git commit -m "feat: 适配器注册表 + 惰性发现机制"
```

---

### Task 3: 实现 ResponsesAdapter

**Files:**
- Create: `proxy/adapters/responses.py`

**说明:** 将 `transform_responses.py` 中 `responses_to_chat()`、`chat_to_responses()`、`create_codex_sse_stream()` 三个公共函数的逻辑移入。保留 `transform_responses.py` 文件不动（新旧并行）。

- [ ] **Step 1: 检查 transform_responses.py 的公共接口和内部依赖**

```bash
grep -n "^def " proxy/transform_responses.py
```
Expected: 列出约 20+ 个函数，关注 `responses_to_chat`、`chat_to_responses`、`create_codex_sse_stream`、`output_items_to_messages`、`generate_response_id`

- [ ] **Step 2: 编写 responses.py——导入旧函数，委托给它们**

```python
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
```

- [ ] **Step 3: 验证 ResponsesAdapter 注册成功**

```bash
python3 -c "
from proxy.adapters import get_adapter
a = get_adapter('responses')
print(a.protocol, type(a).__name__)
"
```
Expected: 先报 ImportError（因为 `__init__.py` 也 import messages），暂时忽略，下一步修复。

- [ ] **Step 4: 暂时注释 __init__.py 中的 messages import**

将 `proxy/adapters/__init__.py` 中的：
```python
    from . import responses   # noqa: F401
    from . import messages    # noqa: F401
```
改为：
```python
    from . import responses   # noqa: F401
    # from . import messages    # noqa: F401 — Task 4 完成后再取消注释
```

- [ ] **Step 5: 再次验证**

```bash
python3 -c "
from proxy.adapters import get_adapter
a = get_adapter('responses')
print(a.protocol, type(a).__name__)
"
```
Expected: `responses ResponsesAdapter`

- [ ] **Step 6: Commit**

```bash
git add proxy/adapters/responses.py proxy/adapters/__init__.py
git commit -m "feat: ResponsesAdapter — responses ↔ chat_completions 双向转换"
```

---

### Task 4: 实现 MessagesAdapter

**Files:**
- Create: `proxy/adapters/messages.py`

- [ ] **Step 1: 检查 transform_anthropic.py 的公共接口**

```bash
grep -n "^def " proxy/transform_anthropic.py
```
Expected: `anthropic_to_chat`、`chat_to_anthropic`、`create_anthropic_sse_stream`

- [ ] **Step 2: 编写 messages.py**

```python
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
```

- [ ] **Step 3: 恢复 __init__.py 中的 messages import**

将 `proxy/adapters/__init__.py` 中注释的那行取消注释：
```python
    from . import responses   # noqa: F401
    from . import messages    # noqa: F401
```

- [ ] **Step 4: 验证两个 Adapter 均已注册**

```bash
python3 -c "
from proxy.adapters import get_adapter
r = get_adapter('responses')
m = get_adapter('messages')
print(r.protocol, m.protocol)
"
```
Expected: `responses messages`

- [ ] **Step 5: Commit**

```bash
git add proxy/adapters/messages.py proxy/adapters/__init__.py
git commit -m "feat: MessagesAdapter — messages ↔ chat_completions 双向转换"
```

---

### Task 5: 重写 TransformRouter 委托注册表

**Files:**
- Overwrite: `proxy/transform_router.py`

- [ ] **Step 1: 重写 TransformRouter**

```python
# proxy/transform_router.py
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
```

- [ ] **Step 2: 删除文件顶部的旧 import（来自 transform.py 的函数导入）**

确认旧 import 行已移除（旧 TransformRouter 文件顶部有 `from .transform import ...`，全部删除）。

- [ ] **Step 3: 验证新 Router 可导入并正常工作**

```bash
python3 -c "
from proxy.transform_router import TransformRouter
# 测试 request_to("chat_completions")
body = TransformRouter.convert_request(
    {'model': 'gpt-4o', 'instructions': 'hi', 'input': []},
    'responses', 'chat_completions',
    {'target': 'gpt-4o', 'multimodal': False}
)
print('request_to OK:', 'messages' in body)

# 测试透传
same = TransformRouter.convert_request(
    {'model': 'gpt-4o', 'messages': []},
    'chat_completions', 'chat_completions',
    {'target': 'gpt-4o', 'multimodal': False}
)
print('passthrough OK:', same == {'model': 'gpt-4o', 'messages': []})
"
```
Expected: `request_to OK: True` `passthrough OK: True`

- [ ] **Step 4: Commit**

```bash
git add proxy/transform_router.py
git commit -m "refactor: TransformRouter 委托 Adapter 注册表"
```

---

### Task 6: 重写 UpstreamDriver 三格式支持

**Files:**
- Overwrite: `proxy/upstream_driver.py`

- [ ] **Step 1: 重写 upstream_driver.py**

```python
# proxy/upstream_driver.py
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
```

- [ ] **Step 2: 验证导入**

```bash
python3 -c "from proxy.upstream_driver import UpstreamDriver; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add proxy/upstream_driver.py
git commit -m "feat: UpstreamDriver 三格式支持 — chat_completions/responses/messages"
```

---

### Task 7: 修改 handler.py 消除硬编码 pivot

**Files:**
- Modify: `proxy/handler.py:590-694`（`_handle_convert` 方法）
- Modify: `proxy/handler.py:698-777`（`_forward_non_streaming` 方法）
- Modify: `proxy/handler.py:779-895`（`_forward_streaming` 方法）

- [ ] **Step 1: 修改 _handle_convert 方法**

将第 590-694 行替换为：

```python
    # ── 转换路径 (_handle_convert) ───────────────────────────────

    def _handle_convert(self, client_format, model_name, model_cfg, body,
                        request_id, request_ts, target):
        """转换路径：TransformRouter 路由 → SDK 调上游 → 响应转换。

        client_format: 客户端协议 (responses / messages / chat_completions)
        upstream_format: 上游协议（取自 upstream_cfg.format）
        """
        is_stream = body.get("stream", False)
        upstream_cfg = model_cfg.get("upstream") or CONFIG.get("upstream", {})
        upstream_format = upstream_cfg.get("format", "chat_completions")
        logger = get_logger()

        # 请求格式转换
        try:
            upstream_body = TransformRouter.convert_request(
                body, client_format, upstream_format, model_cfg
            )
        except KeyError:
            logging.error(
                f"不支持的转换对: {client_format} → {upstream_format}"
            )
            self._send_json(400, {
                "error": {
                    "type": "invalid_request_error",
                    "message": f"不支持的格式转换: {client_format} → {upstream_format}"
                }
            })
            return
        except Exception as e:
            logging.exception(f"请求转换失败 ({client_format})")
            if logger:
                logger.log_converted_request(
                    request_id, model_name, target,
                    {"error": str(e)}, request_type=client_format,
                )
            self._send_json(500, {
                "error": {"type": "internal_error", "message": str(e)}
            })
            return

        # 阶段 2：记录转换后的请求
        upstream_url = None
        if upstream_cfg.get("base_url"):
            upstream_url = upstream_cfg["base_url"].rstrip("/") + "/v1/chat/completions"
        if logger:
            logger.log_converted_request(
                request_id, model_name, target, upstream_body,
                request_type=client_format,
                request_path=upstream_url,
            )

        # previous_response_id：仅 responses 路径支持多轮对话
        if client_format == REQUEST_TYPE_RESPONSES:
            prev_id = body.get("previous_response_id")
            if prev_id:
                response_store = getattr(self.server, "response_store", None)
                if response_store is not None:
                    record = response_store.get(prev_id)
                    if record:
                        system_msgs = [
                            m for m in upstream_body["messages"]
                            if m.get("role") == "system"
                        ]
                        non_system_msgs = [
                            m for m in upstream_body["messages"]
                            if m.get("role") != "system"
                        ]
                        upstream_body["messages"] = (
                            system_msgs + record.conversation + non_system_msgs
                        )
                    else:
                        logging.warning(
                            f"previous_response_id={prev_id!r} 不存在或已过期"
                        )

        # 设置回调
        if client_format == REQUEST_TYPE_RESPONSES:
            store_enabled = body.get("store", True)
            is_responses_api = True
        elif client_format == REQUEST_TYPE_MESSAGES:
            store_enabled = False
            is_responses_api = False
        else:
            store_enabled = False
            is_responses_api = False

        logging.info(
            f"转换: model={model_name}, stream={is_stream}, target={target}, "
            f"client={client_format}, upstream={upstream_format}"
        )

        if is_stream:
            self._forward_streaming(
                upstream_body, model_cfg, request_id, model_name, target, request_ts,
                upstream_cfg, client_format, upstream_format,
                store_enabled=store_enabled,
            )
        else:
            self._forward_non_streaming(
                upstream_body, request_id, model_name, target, request_ts,
                upstream_cfg, client_format, upstream_format,
                store_enabled=store_enabled,
                is_responses_api=is_responses_api,
            )
```

- [ ] **Step 2: 修改 _forward_non_streaming 方法签名和内部调用**

将方法签名从：
```python
def _forward_non_streaming(self, chat_body, request_id, model, target,
                             request_ts, upstream_cfg, request_type,
                             target_format, store_enabled=True,
                             is_responses_api=False):
```
改为：
```python
def _forward_non_streaming(self, upstream_body, request_id, model, target,
                             request_ts, upstream_cfg, client_format,
                             upstream_format, store_enabled=True,
                             is_responses_api=False):
```

将方法体内的：
```python
raw_response = driver.create(target_format, chat_body)
```
改为：
```python
raw_response = driver.create(upstream_format, upstream_body)
```

将：
```python
chat_response = raw_response.model_dump()
```
保持不变。

将：
```python
output = TransformRouter.convert_response(
    chat_response, "chat_completions", request_type
)
```
改为：
```python
output = TransformRouter.convert_response(
    chat_response, upstream_format, client_format
)
```

其余 `request_type` 引用替换为 `client_format`（日志和统计 context 中）。

- [ ] **Step 3: 修改 _forward_streaming 方法签名和内部调用**

将方法签名从：
```python
def _forward_streaming(self, chat_body, model_cfg, request_id, model_name,
                         target, request_ts, upstream_cfg, request_type,
                         target_format, store_enabled=True):
```
改为：
```python
def _forward_streaming(self, upstream_body, model_cfg, request_id, model_name,
                         target, request_ts, upstream_cfg, client_format,
                         upstream_format, store_enabled=True):
```

将：
```python
stream = driver.create_stream(target_format, chat_body)
```
改为：
```python
stream = driver.create_stream(upstream_format, upstream_body)
```

将：
```python
for sse_event in TransformRouter.stream_convert(
    stream, "chat_completions", request_type,
```
改为：
```python
for sse_event in TransformRouter.stream_convert(
    stream, upstream_format, client_format,
```

其余 `request_type` 引用替换为 `client_format`。

- [ ] **Step 4: 修改 _handle_convert 调用处（do_POST 中）**

在 `do_POST` 中（约 198-200 行），将：
```python
self._handle_convert(
    request_type, model_name, model_cfg, body, request_id, request_ts, target
)
```
改为：
```python
self._handle_convert(
    request_type, model_name, model_cfg, body, request_id, request_ts, target
)
```
（保持不变——`_handle_convert` 的 `request_type` 参数已经直接对应 `client_format`）

- [ ] **Step 5: 更新 handler.py 顶部 import**

删除以下行（因为不再需要直接导入转换函数）：
```python
from .transform import (
    generate_response_id,
    responses_to_chat,
    chat_to_responses,
    create_codex_sse_stream,
    anthropic_to_chat,
    chat_to_anthropic,
    create_anthropic_sse_stream,
    _format_sse_event,
)
```

改为只保留：
```python
from .transform import (
    generate_response_id,
    _format_sse_event,
)
```

`generate_response_id` 用于流式异常处理中的 `response.failed` 事件。
`_format_sse_event` 用于同上场景。

检查 handler.py 中是否还有直接引用 `responses_to_chat` 等函数的地方，确认全部改为走 `TransformRouter`。

- [ ] **Step 6: 验证修改后 handler 可导入**

```bash
python3 -c "from proxy.handler import ProxyHandler; print('OK')"
```
Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add proxy/handler.py
git commit -m "refactor: handler 消除硬编码 pivot，改用 client_format/upstream_format"
```

---

### Task 8: 更新 __init__.py re-export 和 transform.py

**Files:**
- Modify: `proxy/__init__.py`
- Modify: `proxy/transform.py`

- [ ] **Step 1: 更新 proxy/__init__.py re-export**

当前 `__init__.py` 从 `proxy.transform` 导入旧函数。改为从 adapters 导入：

```python
"""proxy 包 — Codex Proxy / Anthropic Proxy 统一入口。

提供请求格式转换、配置管理、日志记录、Token 统计等公共接口。
"""

from .request_logger import (  # noqa: F401
    REQUEST_TYPE_RESPONSES,
    REQUEST_TYPE_MESSAGES,
    REQUEST_TYPE_CHAT_COMPLETIONS,
    get_logger,
    init_logger,
    _generate_request_id,
)

from .common import (  # noqa: F401
    CONFIG,
    load_config,
    resolve_model,
    config_cache,
    CONFIG_PATH,
    DATA_DB,
)

# 新架构: ProtocolAdapter + TransformRouter
from .adapters import get_adapter, UnsupportedFormat  # noqa: F401

# SSE 工具
from .sse_utils import _format_sse_event  # noqa: F401

# 向后兼容 re-export — 旧 import 路径暂时保留
from .transform import (  # noqa: F401
    responses_to_chat,
    chat_to_responses,
    create_codex_sse_stream,
    anthropic_to_chat,
    chat_to_anthropic,
    create_anthropic_sse_stream,
)

from .token_stats import record_token_stats  # noqa: F401
from .handler import ProxyHandler  # noqa: F401
from .transform_router import TransformRouter  # noqa: F401
from .upstream_driver import UpstreamDriver  # noqa: F401
```

- [ ] **Step 2: 更新 proxy/transform.py——添加新 re-export 说明**

在 `proxy/transform.py` 末尾添加注释（旧导入路径暂留）：

```python
# 注意：此文件将在删除 transform_responses.py 和 transform_anthropic.py 后
# 同步删除。新代码请使用 proxy.adapters.get_adapter() + ProtocolAdapter 接口。
```

- [ ] **Step 3: 验证 re-export 可导入**

```bash
python3 -c "
from proxy import TransformRouter, UpstreamDriver, get_adapter
print('OK')
"
```
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add proxy/__init__.py proxy/transform.py
git commit -m "refactor: 更新 __init__.py re-export，添加 adapters 公共接口"
```

---

### Task 9: 创建 test_adapters.py 测试 ResponsesAdapter

**Files:**
- Create: `test/test_adapters.py`

- [ ] **Step 1: 编写测试前检查旧测试文件获取测试模式**

```bash
head -80 test/test_transform.py
```

- [ ] **Step 2: 编写 ResponsesAdapter 测试类**

```python
# test/test_adapters.py
"""测试 ProtocolAdapter — ResponsesAdapter 和 MessagesAdapter 的双向转换。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from proxy.adapters import get_adapter, UnsupportedFormat


class TestResponsesAdapter(unittest.TestCase):
    """ResponsesAdapter 测试——等价于旧 test_transform.py 的公共 API 场景。"""

    @classmethod
    def setUpClass(cls):
        cls.adapter = get_adapter("responses")
        cls.model_cfg = {"target": "gpt-4o", "multimodal": False}

    def test_protocol_is_responses(self):
        self.assertEqual(self.adapter.protocol, "responses")

    # ── request_to ──

    def test_request_to_chat_basic(self):
        """responses_to_chat: instructions → system, input → messages。"""
        body = {
            "model": "gpt-4o",
            "instructions": "You are helpful.",
            "input": [{"type": "message", "role": "user", "content": "Hello"}],
        }
        chat = self.adapter.request_to("chat_completions", body, self.model_cfg)
        self.assertEqual(chat["model"], "gpt-4o")
        self.assertIn("messages", chat)
        self.assertEqual(chat["messages"][0]["role"], "system")
        self.assertEqual(chat["messages"][0]["content"], "You are helpful.")

    def test_request_to_chat_no_instructions(self):
        """无 instructions: 没有 system 消息。"""
        body = {
            "model": "gpt-4o",
            "input": [{"type": "message", "role": "user", "content": "Hello"}],
        }
        chat = self.adapter.request_to("chat_completions", body, self.model_cfg)
        roles = [m["role"] for m in chat["messages"]]
        self.assertNotIn("system", roles)

    def test_request_to_chat_with_tools(self):
        """工具定义转换。"""
        body = {
            "model": "gpt-4o",
            "tools": [{"type": "function", "name": "get_weather",
                        "description": "Get weather"}],
            "input": [{"type": "message", "role": "user", "content": "Weather?"}],
        }
        chat = self.adapter.request_to("chat_completions", body, self.model_cfg)
        self.assertIn("tools", chat)
        self.assertEqual(len(chat["tools"]), 1)

    def test_request_to_unsupported_format(self):
        """不支持的 target_format → UnsupportedFormat。"""
        with self.assertRaises(UnsupportedFormat):
            self.adapter.request_to("messages", {"model": "gpt-4o", "input": []}, self.model_cfg)

    # ── response_from ──

    def test_response_from_chat_basic(self):
        """chat_to_responses: Chat Completions → Responses API 响应。"""
        chat_resp = {
            "id": "chatcmpl-1",
            "model": "gpt-4o",
            "choices": [{"message": {"content": "Hi!"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        }
        output = self.adapter.response_from("chat_completions", chat_resp)
        self.assertIn("id", output)
        self.assertIn("output", output)

    def test_response_from_unsupported_format(self):
        with self.assertRaises(UnsupportedFormat):
            self.adapter.response_from("messages", {})

    # ── stream_from ──

    def test_stream_from_chat_basic(self):
        """create_codex_sse_stream: SSE chunks → Responses API SSE events。"""
        chunks = [
            type("ChatChunk", (), {
                "id": "chatcmpl-1",
                "model": "gpt-4o",
                "choices": [type("Choice", (), {
                    "delta": type("Delta", (), {"content": "hello"})(),
                    "finish_reason": None,
                })()],
                "usage": None,
            })(),
            type("ChatChunk", (), {
                "id": "chatcmpl-1",
                "model": "gpt-4o",
                "choices": [type("Choice", (), {
                    "delta": type("Delta", (), {"content": ""})(),
                    "finish_reason": "stop",
                })()],
                "usage": None,
            })(),
        ]

        events = list(self.adapter.stream_from("chat_completions", iter(chunks)))
        self.assertGreater(len(events), 0)
        # 第一个事件应是 response.created
        self.assertIn("response.created", events[0])

    def test_stream_from_unsupported_format(self):
        with self.assertRaises(UnsupportedFormat):
            list(self.adapter.stream_from("messages", iter([])))
```

- [ ] **Step 3: 运行测试验证**

```bash
python3 -m pytest test/test_adapters.py -v
```
Expected: 8 tests passed (具体数量取决于适配器实现)

- [ ] **Step 4: Commit**

```bash
git add test/test_adapters.py
git commit -m "test: ResponsesAdapter 测试 — request_to/response_from/stream_from"
```

---

### Task 10: 补充 MessagesAdapter 测试

**Files:**
- Modify: `test/test_adapters.py`

- [ ] **Step 1: 在 test_adapters.py 末尾添加 MessagesAdapter 测试类**

```python

class TestMessagesAdapter(unittest.TestCase):
    """MessagesAdapter 测试——等价于旧 test_transform_anthropic.py 的公共 API 场景。"""

    @classmethod
    def setUpClass(cls):
        cls.adapter = get_adapter("messages")
        cls.model_cfg = {"target": "qwen3.6-plus", "multimodal": True}

    def test_protocol_is_messages(self):
        self.assertEqual(self.adapter.protocol, "messages")

    # ── request_to ──

    def test_request_to_chat_basic(self):
        """anthropic_to_chat: system → system message, messages → messages。"""
        body = {
            "model": "claude-sonnet",
            "system": "You are helpful.",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1000,
        }
        chat = self.adapter.request_to("chat_completions", body, self.model_cfg)
        self.assertEqual(chat["model"], "qwen3.6-plus")
        self.assertEqual(chat["messages"][0]["role"], "system")
        self.assertEqual(chat["max_tokens"], 1000)

    def test_request_to_chat_no_system(self):
        """无 system 字段: 不生成 system 消息。"""
        body = {
            "model": "claude-sonnet",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1000,
        }
        chat = self.adapter.request_to("chat_completions", body, self.model_cfg)
        roles = [m["role"] for m in chat["messages"]]
        self.assertNotIn("system", roles)

    def test_request_to_unsupported_format(self):
        with self.assertRaises(UnsupportedFormat):
            self.adapter.request_to("responses", {"model": "x", "max_tokens": 1, "messages": []},
                                    self.model_cfg)

    # ── response_from ──

    def test_response_from_chat_basic(self):
        """chat_to_anthropic: Chat Completions → Anthropic Messages 响应。"""
        chat_resp = {
            "id": "chatcmpl-1",
            "model": "qwen3.6-plus",
            "choices": [{"message": {"content": "Hi!"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        }
        output = self.adapter.response_from("chat_completions", chat_resp)
        self.assertIn("id", output)
        self.assertIn("type", output)
        self.assertEqual(output["type"], "message")
        self.assertIn("content", output)
        self.assertIn("stop_reason", output)

    def test_response_from_unsupported_format(self):
        with self.assertRaises(UnsupportedFormat):
            self.adapter.response_from("responses", {})

    # ── stream_from ──

    def test_stream_from_chat_basic(self):
        """create_anthropic_sse_stream: SSE chunks → Anthropic Messages SSE events。"""
        chunks = [
            type("ChatChunk", (), {
                "id": "chatcmpl-1",
                "model": "qwen3.6-plus",
                "choices": [type("Choice", (), {
                    "delta": type("Delta", (), {"content": "hello"})(),
                    "finish_reason": None,
                })()],
                "usage": None,
            })(),
            type("ChatChunk", (), {
                "id": "chatcmpl-1",
                "model": "qwen3.6-plus",
                "choices": [type("Choice", (), {
                    "delta": type("Delta", (), {"content": ""})(),
                    "finish_reason": "stop",
                })()],
                "usage": None,
            })(),
        ]

        events = list(self.adapter.stream_from("chat_completions", iter(chunks)))
        self.assertGreater(len(events), 0)
        # 第一个事件应是 message_start
        self.assertIn("message_start", events[0])

    def test_stream_from_unsupported_format(self):
        with self.assertRaises(UnsupportedFormat):
            list(self.adapter.stream_from("responses", iter([])))
```

- [ ] **Step 2: 运行完整 test_adapters.py**

```bash
python3 -m pytest test/test_adapters.py -v
```
Expected: ~16 tests passed (ResponsesAdapter 8 + MessagesAdapter 8)

- [ ] **Step 3: Commit**

```bash
git add test/test_adapters.py
git commit -m "test: MessagesAdapter 测试补充"
```

---

### Task 11: 适配 test_handler.py

**Files:**
- Modify: `test/test_handler.py`

- [ ] **Step 1: 修改 test_handler.py 中调用 TransformRouter 的测试**

检查 `_handle_convert` 的调用签名变化。当前测试中：
```python
handler._handle_convert(
    "chat_completions", "gpt-4o", model_cfg,
    {"model": "gpt-4o", ...}, _generate_request_id(), "ts", "gpt-4o",
)
```
这里第一个参数 `"chat_completions"` 即是 `client_format`，不需要改。

需要检查的地方：
- `test_convert_non_streaming_sdk_response` (~443行) — `_handle_convert` 调用
- `test_convert_streaming_sdk_path` (~459行) — `_handle_convert` 调用
- `test_non_streaming_output_key_fields` (~609行) — `_handle_convert` 调用
- `test_streaming_output_contains_events` (~658行) — `_handle_convert` 调用

这些测试中的调用签名第一个参数原为 `request_type`，现为 `client_format`，值相同，无需修改。但内部需要 mock 新的 `upstream_driver` 路径。

- [ ] **Step 2: 运行 test_handler.py 确认是否需要适配**

```bash
python3 -m pytest test/test_handler.py -v
```

如果部分测试失败（因为 mock 了旧 `proxy.upstream_driver.OpenAI` 但新代码可能不再通过该路径），逐个修正。

典型的修正：将类似
```python
patch("proxy.upstream_driver.OpenAI") as mock_openai_cls
```
保持不变（因为 upstream_driver.py 仍然 import OpenAI）。

- [ ] **Step 3: 全量验证（新旧代码并行）**

```bash
python3 -m pytest test/ -q
```
Expected: ~531 tests passed（新旧代码并行）

- [ ] **Step 4: Commit**

```bash
git add test/test_handler.py
git commit -m "test: handler 测试适配新 Router 接口"
```

---

### Task 12: 新旧并行验证

**Files:** 验证所有测试通过，不修改任何代码。

- [ ] **Step 1: 运行全量测试**

```bash
python3 -m pytest test/ -q
```

Expected: 全量通过，总数 ≥ 531（test_adapters.py 新增 + 旧测试文件仍存在）。

- [ ] **Step 2: 检查测试覆盖**

```bash
python3 -m pytest test/test_adapters.py test/test_transform.py test/test_transform_anthropic.py -v --tb=short
```

Expected: test_adapters + 两个旧文件全部通过。

- [ ] **Step 3: 确认可并行（新旧代码无冲突）**

检查无 import 错误、无 shadow 问题：
```bash
python3 -c "
from proxy.adapters import get_adapter
from proxy.transform import responses_to_chat, anthropic_to_chat
print('新旧并行 OK')
"
```

---

### Task 13: 删除旧测试文件

**Files:**
- Delete: `test/test_transform.py`
- Delete: `test/test_transform_anthropic.py`

- [ ] **Step 1: 删除旧测试文件**

```bash
rm test/test_transform.py test/test_transform_anthropic.py
```

- [ ] **Step 2: 运行全量测试确认**

```bash
python3 -m pytest test/ -q
```
Expected: 全部通过，总数 ~531（test_adapters.py 替代了 182 个旧测试）。

- [ ] **Step 3: Commit**

```bash
git add test/test_transform.py test/test_transform_anthropic.py
git commit -m "test: 删除旧 test_transform.py/test_transform_anthropic.py"
```

---

### Task 14: 删除旧转换模块

**Files:**
- Delete: `proxy/transform_responses.py`
- Delete: `proxy/transform_anthropic.py`
- Modify: `proxy/transform.py`（彻底删除旧 re-export，改为仅 re-export 新接口）

- [ ] **Step 1: 将 adapters 中的 import 从委托旧函数改为直接引用逻辑**

重写 `proxy/adapters/responses.py`——不再 `from proxy.transform_responses import ...`，因为该文件即将删除。将所有逻辑**直接内联到 adapter 类中**。

这步工作量最大，需要将 `transform_responses.py` 中 `responses_to_chat()`、`chat_to_responses()`、`create_codex_sse_stream()` 以及它们依赖的私有函数全部移入。

**策略:** 将 transform_responses.py 的内容整体复制到 responses.py 的 adapter 类内部（作为私有方法），只修改入口方法 `request_to`/`response_from`/`stream_from` 调用 `self._xxx()` 而非模块级函数。

同理改写 messages.py。

- [ ] **Step 2: 验证转换功能不变**

```bash
python3 -m pytest test/test_adapters.py -v
```
Expected: 全部通过。

- [ ] **Step 3: 删除旧文件**

```bash
rm proxy/transform_responses.py proxy/transform_anthropic.py
```

- [ ] **Step 4: 更新 proxy/transform.py**

```python
# proxy/transform.py
"""转换模块选择器 — re-export shim（向后兼容）。"""

from proxy.adapters import get_adapter, UnsupportedFormat  # noqa: F401

from .sse_utils import _format_sse_event  # noqa: F401

from proxy.transform_router import TransformRouter  # noqa: F401
from proxy.upstream_driver import UpstreamDriver  # noqa: F401
```

- [ ] **Step 5: 更新 proxy/__init__.py**

删除旧函数 re-export（`responses_to_chat` 等），因为 transform.py 不再导出它们：

```python
# SSE 工具
from .sse_utils import _format_sse_event  # noqa: F401

# 新架构
from .adapters import get_adapter, UnsupportedFormat  # noqa: F401
from .transform_router import TransformRouter  # noqa: F401
from .upstream_driver import UpstreamDriver  # noqa: F401
```

- [ ] **Step 6: 全量测试**

```bash
python3 -m pytest test/ -q
```
Expected: 全部通过。

- [ ] **Step 7: Commit**

```bash
git add proxy/adapters/responses.py proxy/adapters/messages.py proxy/transform_responses.py proxy/transform_anthropic.py proxy/transform.py proxy/__init__.py
git commit -m "refactor: 逻辑内联到 adapter，删除旧 transform_responses/transform_anthropic"
```

---

### Task 15: 最终验证 + 收尾 commit

- [ ] **Step 1: 全量测试最终验证**

```bash
python3 -m pytest test/ -q
```
Expected: 全部通过。

- [ ] **Step 2: 启动服务冒烟测试**

```bash
./server.sh start
python3 quick_test.py
./server.sh stop
```
Expected: 冒烟测试通过。

- [ ] **Step 3: 检查无遗漏引用旧模块的代码**

```bash
grep -rn "transform_responses\|transform_anthropic" proxy/ test/ --include="*.py" || echo "无遗留引用"
```
Expected: `无遗留引用`

- [ ] **Step 4: 最终 commit**

```bash
git add -A
git commit -m "refactor: 转换器一步重构完成 — ProtocolAdapter 策略模式 + 消除硬编码 pivot"
```
