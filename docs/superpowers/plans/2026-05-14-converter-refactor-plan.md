# 转换器一步重构实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 一步到位重构转换器架构——引入 ProtocolAdapter 策略模式，消除硬编码 chat_completions 中间 pivot，扩展 UpstreamDriver 支持三上游格式。

**Architecture:** 新建 `proxy/adapters/` 包（ProtocolAdapter 抽象基类 + 注册表惰性发现），ResponsesAdapter/MessagesAdapter 吸收旧转换逻辑，TransformRouter 委托注册表，UpstreamDriver 支持 chat_completions/responses/messages 三格式，Handler 消除硬编码 pivot。

**测试迁移策略:** 将旧 test_transform.py (138 tests) 和 test_transform_anthropic.py (44 tests) 的测试方法复制到 test_adapters.py，修改为调用 `adapter.request_to()` / `adapter.response_from()` / `adapter.stream_from()` 接口，断言逻辑保持不变。迁移后总计 182 + UnsupportedFormat 额外测试 ≈ 190 tests。

**Mock 路径:** `patch("proxy.upstream_driver.OpenAI")` 路径不变——UpstreamDriver 模块级仍 `from openai import OpenAI`，`@property openai` 懒初始化内部调用 `OpenAI(...)` 时会命中 mock。

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

- [ ] **Step 4: 运行现有测试确认无回归**

```bash
python3 -m pytest test/ -q
```
Expected: 531 passed

- [ ] **Step 5: Commit**

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
python3 -c "from proxy.adapters import get_adapter; print('OK')" 2>&1
```
Expected: ImportError (responses/messages 模块尚不存在，这是正确的——下一步创建它们)

- [ ] **Step 3: 验证现有测试无回归**

```bash
python3 -m pytest test/ -q
```
Expected: 531 passed（注册表未被 import，不影响现有代码）

- [ ] **Step 4: Commit**

```bash
git add proxy/adapters/__init__.py
git commit -m "feat: 适配器注册表 + 惰性发现机制"
```

---

### Task 3: 实现 ResponsesAdapter

**Files:**
- Create: `proxy/adapters/responses.py`

**说明:** 将 `transform_responses.py` 中 `responses_to_chat()`、`chat_to_responses()`、`create_codex_sse_stream()` 三个公共函数的逻辑委托调用。保留 `transform_responses.py` 文件不动（新旧并行）。

- [ ] **Step 1: 检查 transform_responses.py 的公共接口**

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

- [ ] **Step 3: 暂时注释 __init__.py 中的 messages import**

将 `proxy/adapters/__init__.py` 中：
```python
    from . import responses   # noqa: F401
    from . import messages    # noqa: F401
```
改为：
```python
    from . import responses   # noqa: F401
    # from . import messages    # noqa: F401 — Task 4 完成后取消注释
```

- [ ] **Step 4: 验证 ResponsesAdapter 注册成功**

```bash
python3 -c "
from proxy.adapters import get_adapter
a = get_adapter('responses')
print(a.protocol, type(a).__name__)
"
```
Expected: `responses ResponsesAdapter`

- [ ] **Step 5: 验证现有测试无回归**

```bash
python3 -m pytest test/ -q
```
Expected: 531 passed（新模块未被 handler 引用，不影响现有代码）

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

- [ ] **Step 5: 验证现有测试无回归**

```bash
python3 -m pytest test/ -q
```
Expected: 531 passed

- [ ] **Step 6: Commit**

```bash
git add proxy/adapters/messages.py proxy/adapters/__init__.py
git commit -m "feat: MessagesAdapter — messages ↔ chat_completions 双向转换"
```

---

### Task 5: 重写 TransformRouter 委托注册表

**Files:**
- Overwrite: `proxy/transform_router.py`

- [ ] **Step 1: 备份旧文件（以备回滚）**

```bash
cp proxy/transform_router.py proxy/transform_router.py.bak
```

- [ ] **Step 2: 重写 TransformRouter**

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

- [ ] **Step 3: 删除文件中旧的所有 import 和函数字典**

确认旧 `from .transform import (...)` import 和 `_request_converters` / `_response_converters` / `_stream_converters` 字典全部删除。

- [ ] **Step 4: 验证新 Router 可导入并工作**

```bash
python3 -c "
from proxy.transform_router import TransformRouter
body = TransformRouter.convert_request(
    {'model': 'gpt-4o', 'instructions': 'hi', 'input': []},
    'responses', 'chat_completions',
    {'target': 'gpt-4o', 'multimodal': False}
)
print('request_to OK:', 'messages' in body)

same = TransformRouter.convert_request(
    {'model': 'gpt-4o', 'messages': []},
    'chat_completions', 'chat_completions',
    {'target': 'gpt-4o', 'multimodal': False}
)
print('passthrough OK:', same == {'model': 'gpt-4o', 'messages': []})
"
```
Expected: `request_to OK: True` `passthrough OK: True`

- [ ] **Step 5: 运行全量测试**

```bash
python3 -m pytest test/ -q
```
Expected: 部分 handler 测试可能失败（handler 仍使用旧 Router 签名），这是预期的——Task 7 会修复。

- [ ] **Step 6: Commit**

```bash
git add proxy/transform_router.py
git commit -m "refactor: TransformRouter 委托 Adapter 注册表"
```

---

### Task 6: 重写 UpstreamDriver 三格式支持

**Files:**
- Overwrite: `proxy/upstream_driver.py`

- [ ] **Step 1: 备份旧文件**

```bash
cp proxy/upstream_driver.py proxy/upstream_driver.py.bak
```

- [ ] **Step 2: 重写 upstream_driver.py**

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

- [ ] **Step 3: 验证 UpstreamDriver 导入和新方法**

```bash
python3 -c "
from proxy.upstream_driver import UpstreamDriver
d = UpstreamDriver({'base_url': 'http://x', 'api_key': 'k'})
print('format:', d.format)
print('has create:', hasattr(d, 'create'))
print('has create_stream:', hasattr(d, 'create_stream'))
print('has close:', hasattr(d, 'close'))
print('OK')
"
```
Expected: `format: chat_completions` `has create: True` `has create_stream: True` `has close: True` `OK`

- [ ] **Step 4: 验证现有测试**

```bash
python3 -m pytest test/ -q
```
Expected: 确认失败测试仅限于 Router 签名变更（handler 路径），非 UpstreamDriver 引起。Driver 自身的 `create("chat_completions", body)` 路径与旧接口兼容。

- [ ] **Step 5: Commit**

```bash
git add proxy/upstream_driver.py
git commit -m "feat: UpstreamDriver 三格式支持 — chat_completions/responses/messages"
```

---

### Task 7: 修改 handler.py 消除硬编码 pivot（完整版）

**Files:**
- Modify: `proxy/handler.py`

**说明:** 三处核心修改——`_handle_convert`、`_forward_non_streaming`、`_forward_streaming`。变量 `request_type` → `client_format`，`intermediate` → `upstream_format`，`chat_body` → `upstream_body`，`target_format` → `upstream_format`。

- [ ] **Step 1: 备份**

```bash
cp proxy/handler.py proxy/handler.py.bak
```

- [ ] **Step 2: 修改 handler.py 顶部 import（约第 28-37 行）**

将：
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
改为：
```python
from .transform import (
    generate_response_id,
    _format_sse_event,
)
```

- [ ] **Step 3: 修改 _handle_convert 方法（约 590-694 行）**

完整替换为：

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

- [ ] **Step 4: 修改 _forward_non_streaming 方法（约 698-777 行）**

完整替换为：

```python
    # ── 转换路径内部方法（v2 SDK 驱动） ──────────────────────────

    def _forward_non_streaming(self, upstream_body, request_id, model, target,
                                 request_ts, upstream_cfg, client_format,
                                 upstream_format, store_enabled=True,
                                 is_responses_api=False):
        """非流式转换请求：SDK 调用 + 响应转换 + Token 统计。"""
        from .upstream_driver import UpstreamDriver

        driver = UpstreamDriver(upstream_cfg)
        logger = get_logger()

        import time as _time
        _request_start = _time.time()
        try:
            raw_response = driver.create(upstream_format, upstream_body)
            chat_response = raw_response.model_dump()
        except Exception as e:
            self._handle_sdk_error(e)
            driver.close()
            return
        duration_ms = int((_time.time() - _request_start) * 1000)
        request_ts_for_stats = request_ts

        # 阶段 3：记录上游响应
        if logger:
            logger.log_upstream_response(
                request_id, 200, chat_response, 0,
                model, target,
                request_type=client_format,
            )

        # 阶段 4：转换响应 + Token 统计
        try:
            output = TransformRouter.convert_response(
                chat_response, upstream_format, client_format
            )
            if logger:
                logger.log_converted_response(
                    request_id, model, target, output,
                    request_type=client_format,
                )

            usage = chat_response.get("usage", {})
            if usage:
                ctx = {
                    "request_id": request_id,
                    "request_type": client_format,
                    "model": model,
                    "target_model": target,
                    "request_ts": request_ts_for_stats,
                    "duration_ms": duration_ms,
                }
                if upstream_cfg.get("id") is not None:
                    ctx["upstream_id"] = upstream_cfg["id"]
                record_token_stats(usage, ctx)

        except Exception as e:
            logging.exception("响应转换失败")
            if logger:
                logger.log_converted_response(
                    request_id, model, target,
                    {"error": str(e)}, request_type=client_format,
                )
            self._send_json(500, {
                "error": {"type": "internal_error", "message": str(e)}
            })
            driver.close()
            return

        # 存储 response（仅 responses 路径）
        if store_enabled and is_responses_api:
            from .transform_responses import output_items_to_messages as _oitm
            assistant_msgs = _oitm(output.get("output", []))
            messages_for_conv = [
                m for m in upstream_body.get("messages", [])
                if m.get("role") != "system"
            ] + assistant_msgs
            _store_response(self.server, output, messages_for_conv)

        self._send_json(200, output)
        driver.close()
```

- [ ] **Step 5: 修改 _forward_streaming 方法（约 779-896 行）**

完整替换为：

```python
    def _forward_streaming(self, upstream_body, model_cfg, request_id, model_name,
                             target, request_ts, upstream_cfg, client_format,
                             upstream_format, store_enabled=True):
        """流式转换请求：SDK 流式调用 + TransformRouter 逐事件转换。"""
        from .upstream_driver import UpstreamDriver

        driver = UpstreamDriver(upstream_cfg)
        logger = get_logger()

        try:
            stream = driver.create_stream(upstream_format, upstream_body)
        except Exception as e:
            self._handle_sdk_error(e)
            driver.close()
            return

        # 发送 SSE 响应头
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        import time as _time
        start = _time.time()
        SSE_BUFFER_MAX = 200 * 1024
        sse_buffer = []
        sse_buffer_size = 0
        final_usage = None

        try:
            _rstore = (
                getattr(self.server, "response_store", None)
                if store_enabled else None
            )
            for sse_event in TransformRouter.stream_convert(
                stream, upstream_format, client_format,
                request_messages=upstream_body.get("messages") if _rstore else None,
                response_store=_rstore,
            ):
                self.wfile.write(sse_event.encode("utf-8"))
                self.wfile.flush()
                if sse_buffer_size < SSE_BUFFER_MAX:
                    sse_buffer.append(sse_event)
                    sse_buffer_size += len(sse_event)

                # 用 _parse_sse_event 做结构化解析
                if "response.completed" in sse_event or "message_delta" in sse_event:
                    parsed = _parse_sse_event(sse_event)
                    data = parsed.get("data")
                    if data:
                        usage = (
                            data.get("response", {}).get("usage")
                            or data.get("usage")
                        )
                        if usage:
                            final_usage = usage
        except (BrokenPipeError, ConnectionResetError):
            logging.warning("客户端断开连接")
        except Exception as e:
            logging.exception("流式转换异常")
            try:
                error_event = _format_sse_event("response.failed", {
                    "response": {
                        "id": generate_response_id(),
                        "status": "failed",
                        "output": [],
                        "status_details": {
                            "error": {
                                "type": "server_error",
                                "message": str(e),
                            },
                        },
                    },
                })
                self.wfile.write(error_event.encode("utf-8"))
                self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except Exception:
                pass

        duration_ms = int((_time.time() - start) * 1000)
        full_sse = "".join(sse_buffer) if sse_buffer else "(buffer overflow)"

        # 日志
        if logger:
            logger.log_upstream_response(
                request_id, 200, full_sse, duration_ms,
                model_name, target,
                request_type=client_format,
            )
            logger.log_converted_response(
                request_id, model_name, target,
                {"streaming": True, "note": "SDK 流式响应"},
                request_type=client_format,
            )

        # Token 统计
        if final_usage:
            ctx = {
                "request_id": request_id,
                "request_type": client_format,
                "model": model_name,
                "target_model": target,
                "request_ts": request_ts,
                "duration_ms": duration_ms,
            }
            if upstream_cfg.get("id") is not None:
                ctx["upstream_id"] = upstream_cfg["id"]
            record_token_stats(final_usage, ctx)
        else:
            logging.warning(
                f"流式路径未提取到 usage: request_id={request_id}, "
                f"model={model_name}, target={target}"
            )

        driver.close()
```

- [ ] **Step 6: 确认 do_POST 中 _handle_convert 调用无需改动**

`do_POST` 第 198 行调用 `self._handle_convert(request_type, ...)`——`request_type` 变量名保持不变，它对应 `client_format`，值正确。无需修改。

- [ ] **Step 7: 验证 handler 可导入**

```bash
python3 -c "from proxy.handler import ProxyHandler; print('OK')"
```
Expected: `OK`

- [ ] **Step 8: 运行全量测试**

```bash
python3 -m pytest test/ -q
```
Expected: 全部通过。若 handler 测试有失败，检查 mock 路径和调用签名。

- [ ] **Step 9: Commit**

```bash
git add proxy/handler.py
git commit -m "refactor: handler 消除硬编码 pivot，改用 client_format/upstream_format"
```

---

### Task 8: 更新 __init__.py re-export 和 transform.py

**Files:**
- Modify: `proxy/__init__.py`
- Modify: `proxy/transform.py`

- [ ] **Step 1: 更新 proxy/__init__.py**

当前从 `proxy.transform` 导入旧函数。改为同时从 `proxy.adapters` 导入新接口，旧接口暂时保留：

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

# 新架构: ProtocolAdapter + 注册表
from .adapters import get_adapter, UnsupportedFormat  # noqa: F401

# SSE 工具
from .sse_utils import _format_sse_event  # noqa: F401

# 向后兼容 re-export — 旧 import 路径暂时保留（Task 14c 删除）
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

- [ ] **Step 2: 更新 proxy/transform.py——添加说明注释**

在文件末尾添加：
```python
# 注意：此文件将在 Task 14c 删除 transform_responses.py 和 transform_anthropic.py
# 后同步变为纯 re-export。新代码请使用 proxy.adapters.get_adapter() + ProtocolAdapter 接口。
```

- [ ] **Step 3: 验证 re-export 可导入**

```bash
python3 -c "
from proxy import TransformRouter, UpstreamDriver, get_adapter, UnsupportedFormat
print('OK')
"
```
Expected: `OK`

- [ ] **Step 4: 运行全量测试**

```bash
python3 -m pytest test/ -q
```
Expected: 全部通过

- [ ] **Step 5: Commit**

```bash
git add proxy/__init__.py proxy/transform.py
git commit -m "refactor: 更新 __init__.py re-export，添加 adapters 公共接口"
```

---

### Task 9: 创建 test_adapters.py——迁移旧测试

**Files:**
- Create: `test/test_adapters.py`

**策略:** 复制旧 `test_transform.py` (138 tests) 和 `test_transform_anthropic.py` (44 tests) 的测试方法到本文件，将直接调用 `responses_to_chat()` 改为 `self.adapter.request_to("chat_completions", ...)`, `chat_to_responses()` 改为 `self.adapter.response_from("chat_completions", ...)` 等。断言逻辑保持不变。新增 UnsupportedFormat 异常测试。

- [ ] **Step 1: 阅读旧测试文件了解结构**

```bash
head -100 test/test_transform.py
head -100 test/test_transform_anthropic.py
```

- [ ] **Step 2: 创建 test_adapters.py——完整迁移**

文件结构：

```python
# test/test_adapters.py
"""测试 ProtocolAdapter — ResponsesAdapter 和 MessagesAdapter 的双向转换。

从 test_transform.py (138 tests) 和 test_transform_anthropic.py (44 tests)
迁移而来。每个旧测试方法改为调用 adapter.request_to() / .response_from() / .stream_from()，
断言逻辑不变。新增 UnsupportedFormat 异常路径测试。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from proxy.adapters import get_adapter, UnsupportedFormat


# ═══════════════════════════════════════════════════════════════════
# ResponsesAdapter 测试
# 迁移自 test_transform.py——所有 138 个测试场景
# ═══════════════════════════════════════════════════════════════════

class TestResponsesAdapter(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.adapter = get_adapter("responses")
        cls.model_cfg = {"target": "gpt-4o", "multimodal": False}

    def test_protocol_is_responses(self):
        self.assertEqual(self.adapter.protocol, "responses")

    # ── request_to("chat_completions") — 等价于旧 responses_to_chat ──

    def test_request_to_chat_instructions_to_system(self):
        """instructions → system message。"""
        body = {
            "model": "gpt-4o",
            "instructions": "You are helpful.",
            "input": [{"type": "message", "role": "user", "content": "Hello"}],
        }
        result = self.adapter.request_to("chat_completions", body, self.model_cfg)
        self.assertEqual(result["model"], "gpt-4o")
        self.assertIn("messages", result)
        self.assertEqual(result["messages"][0]["role"], "system")
        self.assertEqual(result["messages"][0]["content"], "You are helpful.")

    def test_request_to_chat_no_instructions(self):
        """无 instructions → 无 system 消息。"""
        body = {
            "model": "gpt-4o",
            "input": [{"type": "message", "role": "user", "content": "Hello"}],
        }
        result = self.adapter.request_to("chat_completions", body, self.model_cfg)
        roles = [m["role"] for m in result["messages"]]
        self.assertNotIn("system", roles)

    def test_request_to_chat_with_tools(self):
        """工具定义转换。"""
        body = {
            "model": "gpt-4o",
            "tools": [{"type": "function", "name": "get_weather",
                        "description": "Get weather"}],
            "input": [{"type": "message", "role": "user", "content": "Weather?"}],
        }
        result = self.adapter.request_to("chat_completions", body, self.model_cfg)
        self.assertIn("tools", result)
        self.assertEqual(len(result["tools"]), 1)

    # ... 其余 135 个测试从 test_transform.py 迁移 ...
    # 每个旧函数调用模式:
    #   旧: result = responses_to_chat(body, model_cfg)
    #   新: result = self.adapter.request_to("chat_completions", body, self.model_cfg)
    #   旧: result = chat_to_responses(response)
    #   新: result = self.adapter.response_from("chat_completions", response)
    #   旧: list(create_codex_sse_stream(chunks, ...))
    #   新: list(self.adapter.stream_from("chat_completions", chunks, ...))

    # ── response_from("chat_completions") — 等价于旧 chat_to_responses ──

    def test_response_from_chat_basic(self):
        chat_resp = {
            "id": "chatcmpl-1",
            "model": "gpt-4o",
            "choices": [{"message": {"content": "Hi!"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        }
        result = self.adapter.response_from("chat_completions", chat_resp)
        self.assertIn("id", result)
        self.assertIn("output", result)

    # ... 其余 response_from 测试从旧 chat_to_responses 迁移 ...

    # ── stream_from("chat_completions") — 等价于旧 create_codex_sse_stream ──

    def test_stream_from_chat_basic(self):
        chunks = [
            type("Chunk", (), {
                "id": "chatcmpl-1", "model": "gpt-4o",
                "choices": [type("C", (), {
                    "delta": type("D", (), {"content": "hello"})(),
                    "finish_reason": None,
                })()],
                "usage": None,
            })(),
            type("Chunk", (), {
                "id": "chatcmpl-1", "model": "gpt-4o",
                "choices": [type("C", (), {
                    "delta": type("D", (), {"content": ""})(),
                    "finish_reason": "stop",
                })()],
                "usage": None,
            })(),
        ]
        events = list(self.adapter.stream_from("chat_completions", iter(chunks)))
        self.assertGreater(len(events), 0)
        self.assertIn("response.created", events[0])

    # ... 其余 stream_from 测试从旧 create_codex_sse_stream 迁移 ...

    # ── UnsupportedFormat 异常测试（新增，不计入迁移数）──

    def test_request_to_unsupported_format(self):
        with self.assertRaises(UnsupportedFormat):
            self.adapter.request_to("messages", {"model": "x", "input": []}, self.model_cfg)

    def test_response_from_unsupported_format(self):
        with self.assertRaises(UnsupportedFormat):
            self.adapter.response_from("messages", {})

    def test_stream_from_unsupported_format(self):
        with self.assertRaises(UnsupportedFormat):
            list(self.adapter.stream_from("messages", iter([])))


# ═══════════════════════════════════════════════════════════════════
# MessagesAdapter 测试
# 迁移自 test_transform_anthropic.py——所有 44 个测试场景
# ═══════════════════════════════════════════════════════════════════

class TestMessagesAdapter(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.adapter = get_adapter("messages")
        cls.model_cfg = {"target": "qwen3.6-plus", "multimodal": True}

    def test_protocol_is_messages(self):
        self.assertEqual(self.adapter.protocol, "messages")

    # ── request_to("chat_completions") — 等价于旧 anthropic_to_chat ──

    def test_request_to_chat_system_str(self):
        body = {
            "model": "claude-sonnet",
            "system": "You are helpful.",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1000,
        }
        result = self.adapter.request_to("chat_completions", body, self.model_cfg)
        self.assertEqual(result["model"], "qwen3.6-plus")
        self.assertEqual(result["messages"][0]["role"], "system")
        self.assertEqual(result["max_tokens"], 1000)

    # ... 其余 41 个测试从 test_transform_anthropic.py 迁移 ...
    # 迁移模式同上: anthropic_to_chat → request_to("chat_completions")
    #                chat_to_anthropic → response_from("chat_completions")
    #                create_anthropic_sse_stream → stream_from("chat_completions")

    # ── UnsupportedFormat 异常测试（新增）──

    def test_request_to_unsupported_format(self):
        with self.assertRaises(UnsupportedFormat):
            self.adapter.request_to("responses",
                {"model": "x", "max_tokens": 1, "messages": []}, self.model_cfg)

    def test_response_from_unsupported_format(self):
        with self.assertRaises(UnsupportedFormat):
            self.adapter.response_from("responses", {})

    def test_stream_from_unsupported_format(self):
        with self.assertRaises(UnsupportedFormat):
            list(self.adapter.stream_from("responses", iter([])))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 完整迁移剩余测试**

用脚本或手动将旧文件中的每个测试方法复制过来，修改函数调用名称。模式：

| 旧调用 | 新调用 |
|--------|--------|
| `responses_to_chat(body, cfg)` | `self.adapter.request_to("chat_completions", body, self.model_cfg)` |
| `chat_to_responses(resp)` | `self.adapter.response_from("chat_completions", resp)` |
| `create_codex_sse_stream(chunks, ...)` | `self.adapter.stream_from("chat_completions", chunks, ...)` |
| `anthropic_to_chat(body, cfg)` | `self.adapter.request_to("chat_completions", body, self.model_cfg)` |
| `chat_to_anthropic(resp)` | `self.adapter.response_from("chat_completions", resp)` |
| `create_anthropic_sse_stream(chunks, ...)` | `self.adapter.stream_from("chat_completions", chunks, ...)` |

- [ ] **Step 4: 运行 test_adapters.py 验证**

```bash
python3 -m pytest test/test_adapters.py -v
```
Expected: 182 + 6 (UnsupportedFormat) = ~188 tests passed

- [ ] **Step 5: 运行全量测试确认新旧并行**

```bash
python3 -m pytest test/ -q
```
Expected: 531 + 188 = ~719 tests（新旧测试共存）

- [ ] **Step 6: Commit**

```bash
git add test/test_adapters.py
git commit -m "test: 迁移 182 个旧测试到 test_adapters.py + UnsupportedFormat 异常测试"
```

---

### Task 10: 适配 test_handler.py

**Files:**
- Modify: `test/test_handler.py`

- [ ] **Step 1: 运行 handler 测试查看失败情况**

```bash
python3 -m pytest test/test_handler.py -v
```

- [ ] **Step 2: 逐个修正失败测试**

主要需要关注的地方：
1. `test_full_flow_convert` (~533-545行) — mock 路径 `patch("proxy.upstream_driver.OpenAI")` **不变**（因为模块级仍 `from openai import OpenAI`）
2. `_handle_convert` 调用签名——参数名从 `request_type` 变为 `client_format`，但**值不变**（都是 `"chat_completions"` 或 `"messages"` 等字符串），测试中传入的值一致，不需要修改
3. `test_convert_output_consistency` 中的测试——如果直接调 `_handle_convert` 传入 `"chat_completions"` 作为第一个参数，这已经是 `client_format`，不需要改

如果失败涉及 Router 内部的参数名变化（`source`→`client_format`, `target`→`upstream_format`），只需修改测试中的调用参数名。

- [ ] **Step 3: 运行全量测试**

```bash
python3 -m pytest test/ -q
```
Expected: 全部通过（新旧并行）

- [ ] **Step 4: Commit**

```bash
git add test/test_handler.py
git commit -m "test: handler 测试适配新 Router 接口"
```

---

### Task 11: 新旧并行验证

**Files:** 无修改，只验证。

- [ ] **Step 1: 运行全量测试**

```bash
python3 -m pytest test/ -q
```
Expected: 全部通过，新旧测试文件共存。

- [ ] **Step 2: 检查三个测试文件均通过**

```bash
python3 -m pytest test/test_adapters.py test/test_transform.py test/test_transform_anthropic.py -v --tb=short
```
Expected: 全部通过。

- [ ] **Step 3: 确认新旧代码无冲突**

```bash
python3 -c "
from proxy.adapters import get_adapter
from proxy.transform import responses_to_chat, anthropic_to_chat
print('新旧并行 OK')
"
```
Expected: `新旧并行 OK`

---

### Task 12: 删除旧测试文件

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
Expected: 全部通过，总数不掉（test_adapters.py 替代了 182 个旧测试）。

- [ ] **Step 3: Commit**

```bash
git rm test/test_transform.py test/test_transform_anthropic.py
git commit -m "test: 删除旧 test_transform.py/test_transform_anthropic.py"
```

---

### Task 13: 内联 transform_responses 逻辑到 ResponsesAdapter

**Files:**
- Modify: `proxy/adapters/responses.py`

- [ ] **Step 1: 列出 transform_responses.py 的依赖关系**

```bash
grep -n "^def \|^class " proxy/transform_responses.py
```

找出 `responses_to_chat`、`chat_to_responses`、`create_codex_sse_stream` 直接和间接调用的所有私有函数。

- [ ] **Step 2: 将公共函数和依赖的私有函数复制到 ResponsesAdapter**

策略：
- `responses_to_chat()` 的函数体 → `ResponsesAdapter._responses_to_chat()`
- `chat_to_responses()` 的函数体 → `ResponsesAdapter._chat_to_responses()`
- `create_codex_sse_stream()` 的函数体 → `ResponsesAdapter._chat_stream_to_responses()`
- 它们依赖的私有函数（如 `_map_input_item`、`_fix_tool_message_order`、`_map_tools` 等）→ `ResponsesAdapter._xxx()` 私有方法或模块级函数
- 共享工具（`generate_response_id`、`_parse_sse_event`、`iter_sse_events`、`StreamState`、`CodexStreamConverter` 等）→ 保留为模块级函数 / 类
- 需要 `from .token_stats import _find_first` 和 `from .sse_utils import _format_sse_event` 的 import 保留

- [ ] **Step 3: 更新 request_to/response_from/stream_from 调用 self._xxx()**

```python
# request_to 内部
def request_to(self, upstream_format: str, body: dict, model_cfg: dict) -> dict:
    if upstream_format == "chat_completions":
        return self._responses_to_chat(body, model_cfg)
    raise UnsupportedFormat(...)
```

不再 `from proxy.transform_responses import responses_to_chat`。

- [ ] **Step 4: 运行 test_adapters.py 验证等价**

```bash
python3 -m pytest test/test_adapters.py -v
```
Expected: ~188 tests passed（ResponsesAdapter 部分 141 tests）。

- [ ] **Step 5: 运行全量测试**

```bash
python3 -m pytest test/ -q
```
Expected: 全部通过。

- [ ] **Step 6: Commit**

```bash
git add proxy/adapters/responses.py
git commit -m "refactor: 内联 transform_responses 逻辑到 ResponsesAdapter"
```

---

### Task 14: 内联 transform_anthropic 逻辑到 MessagesAdapter

**Files:**
- Modify: `proxy/adapters/messages.py`

- [ ] **Step 1: 列出 transform_anthropic.py 的依赖关系**

```bash
grep -n "^def \|^class " proxy/transform_anthropic.py
```

- [ ] **Step 2: 将函数体复制到 MessagesAdapter**

与 Task 13 同样的模式：
- `anthropic_to_chat()` → `MessagesAdapter._anthropic_to_chat()`
- `chat_to_anthropic()` → `MessagesAdapter._chat_to_anthropic()`
- `create_anthropic_sse_stream()` → `MessagesAdapter._chat_stream_to_anthropic()`
- 依赖的私有函数 → `_xxx()` 私有方法

- [ ] **Step 3: 更新入口方法调用 self._xxx()**

```python
def request_to(self, upstream_format: str, body: dict, model_cfg: dict) -> dict:
    if upstream_format == "chat_completions":
        return self._anthropic_to_chat(body, model_cfg)
    raise UnsupportedFormat(...)
```

- [ ] **Step 4: 运行 test_adapters.py 验证**

```bash
python3 -m pytest test/test_adapters.py -v
```
Expected: ~188 tests passed（含 MessagesAdapter 47 tests）。

- [ ] **Step 5: 全量测试**

```bash
python3 -m pytest test/ -q
```
Expected: 全部通过。

- [ ] **Step 6: Commit**

```bash
git add proxy/adapters/messages.py
git commit -m "refactor: 内联 transform_anthropic 逻辑到 MessagesAdapter"
```

---

### Task 15: 删除旧转换模块 + 更新 re-export

**Files:**
- Delete: `proxy/transform_responses.py`
- Delete: `proxy/transform_anthropic.py`
- Modify: `proxy/transform.py`（纯 re-export）
- Modify: `proxy/__init__.py`（删除旧函数 re-export）
- Modify: `proxy/handler.py`（删除 `from .transform_responses import output_items_to_messages` 引用）
- Remove: `proxy/transform_router.py.bak`
- Remove: `proxy/upstream_driver.py.bak`

- [ ] **Step 1: 处理 handler.py 中对 transform_responses 的残留引用**

handler.py `_forward_non_streaming` 中有一行：
```python
from .transform_responses import output_items_to_messages as _oitm
```
改为从 adapters 导入或直接使用 adapter：
```python
# 从公开入口导入
from proxy.adapters.responses import _output_items_to_messages as _oitm
# 或者在 responses.py 中暴露该函数
```
实际方案：在 `proxy/adapters/responses.py` 中将 `output_items_to_messages` 暴露为模块级函数。

- [ ] **Step 2: 在 responses.py 中暴露 output_items_to_messages**

```python
# proxy/adapters/responses.py 顶部添加公开导出
def output_items_to_messages(items):
    """公开工具函数——将 response.output items 转为 Chat Completions 格式消息列表。"""
    ...  # 原 transform_responses.py 中的实现
```

- [ ] **Step 3: 更新 handler.py 引用**

```python
from proxy.adapters.responses import output_items_to_messages as _oitm
```

- [ ] **Step 4: 删除旧文件**

```bash
rm proxy/transform_responses.py proxy/transform_anthropic.py
```

- [ ] **Step 5: 更新 proxy/transform.py 为纯 re-export**

```python
# proxy/transform.py
"""转换模块选择器 — re-export shim。"""

from proxy.adapters import get_adapter, UnsupportedFormat  # noqa: F401
from .sse_utils import _format_sse_event  # noqa: F401
from proxy.transform_router import TransformRouter  # noqa: F401
from proxy.upstream_driver import UpstreamDriver  # noqa: F401
```

- [ ] **Step 6: 更新 proxy/__init__.py**

删除旧函数 re-export（`responses_to_chat` 等），只保留新接口：

```python
# SSE 工具
from .sse_utils import _format_sse_event  # noqa: F401

# 新架构
from .adapters import get_adapter, UnsupportedFormat  # noqa: F401
from .transform_router import TransformRouter  # noqa: F401
from .upstream_driver import UpstreamDriver  # noqa: F401
```

- [ ] **Step 7: 删除备份文件**

```bash
rm proxy/transform_router.py.bak proxy/upstream_driver.py.bak proxy/handler.py.bak
```

- [ ] **Step 8: 全量测试**

```bash
python3 -m pytest test/ -q
```
Expected: 全部通过。

- [ ] **Step 9: 检查无遗漏引用**

```bash
grep -rn "transform_responses\|transform_anthropic" proxy/ test/ --include="*.py" || echo "无遗留引用"
```
Expected: `无遗留引用`

- [ ] **Step 10: Commit**

```bash
git add proxy/adapters/responses.py proxy/handler.py proxy/transform.py proxy/__init__.py
git rm proxy/transform_responses.py proxy/transform_anthropic.py
git rm proxy/transform_router.py.bak proxy/upstream_driver.py.bak proxy/handler.py.bak
git commit -m "refactor: 删除旧转换模块，完成 Adapter 内联"
```

---

### Task 16: 最终验证 + 收尾

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

- [ ] **Step 3: 最终 commit**

```bash
git add -A
git status
git commit -m "refactor: 转换器一步重构完成 — ProtocolAdapter 策略模式 + 消除硬编码 pivot"
```
