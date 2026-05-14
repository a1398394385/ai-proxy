# 转换器一步重构设计

> 日期: 2026-05-14
> 分支: ai-agent-tools-ai-sdk
> 参考: hermes-api-multi-protocol-report.md

## 动机

当前转换器架构有三个核心问题:

1. **硬编码 chat_completions 中间 pivot** — `handler.py:_handle_convert` 强制 `intermediate = "chat_completions"`，无法支持不同格式的上游
2. **转换逻辑分散** — `transform_responses.py`(931行) 和 `transform_anthropic.py`(536行) 是庞大的函数集合，没有统一接口
3. **UpstreamDriver 只支持 chat_completions** — 无法直接调用 Anthropic Messages API 或 OpenAI Responses API 上游

借鉴 Hermes Agent 的「策略模式 + 注册表」架构，适配代理场景的「N×M 双向转换」需求，一步到位重构。

## 核心差异: 我们是代理，不是消费者

Hermes Agent 是消费者，它调用上游后用 `NormalizedResponse` 作为内部中间表示，不需要保留原始协议格式。我们是代理，必须将上游响应**精确转换**为下游客户端期望的协议格式。因此我们不需要 `NormalizedResponse` 共享类型，而是需要「ProtocolAdapter」双向转换器。

## 架构设计

### 新目录结构

```
proxy/
├── adapters/                    # ★ 新增: 协议适配层
│   ├── __init__.py              # 注册表 + 惰性发现
│   ├── base.py                  # ProtocolAdapter 抽象基类
│   ├── responses.py             # ResponsesAdapter (吸收 transform_responses.py)
│   └── messages.py              # MessagesAdapter (吸收 transform_anthropic.py)
│
├── upstream_driver.py           # ★ 重写: 三格式 SDK 驱动 (openai + anthropic)
├── transform_router.py          # ★ 重写: 委托 Adapter 注册表
│
├── handler.py                   # ★ 简化: 消除硬编码 pivot，改用 client_format/upstream_format
├── transform.py                 # 删除或极简 re-export
├── transform_responses.py       # 删除，逻辑移入 adapters/responses.py
├── transform_anthropic.py       # 删除，逻辑移入 adapters/messages.py
│
├── sse_utils.py                 # 保留
├── common.py                    # 保留
├── config_manager.py            # 不变
├── request_logger.py            # 不变
├── token_stats.py               # 不变
├── response_store.py            # 不变
├── pricing_manager.py           # 不变
└── paths.py                     # 不变
```

### ProtocolAdapter 抽象基类

```python
# proxy/adapters/base.py

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

### Adapter 内部分发逻辑

以 ResponsesAdapter 为例，展示 `request_to` / `response_from` 内部实现模式：

```python
# proxy/adapters/responses.py

class ResponsesAdapter(ProtocolAdapter):
    protocol = "responses"

    def request_to(self, upstream_format: str, body: dict, model_cfg: dict) -> dict:
        if upstream_format == "chat_completions":
            return self._responses_to_chat(body, model_cfg)
        raise UnsupportedFormat(f"responses → {upstream_format} 尚未实现")

    def response_from(self, upstream_format: str, response: dict) -> dict:
        if upstream_format == "chat_completions":
            return self._chat_to_responses(response)
        raise UnsupportedFormat(f"{upstream_format} → responses 尚未实现")

    def stream_from(self, upstream_format: str, chunks, *,
                    request_messages=None, response_store=None):
        if upstream_format == "chat_completions":
            yield from self._chat_stream_to_responses(chunks,
                request_messages=request_messages,
                response_store=response_store)
            return
        raise UnsupportedFormat(f"{upstream_format} → responses (stream) 尚未实现")
```

MessagesAdapter 同理，只实现 `chat_completions` 作为请求/响应目标格式。未来扩展新链路时，只需在对应 Adapter 中新增 `if upstream_format == "..."` 分支。

与 Hermes `ProviderTransport` 的区别:

| | Hermes ProviderTransport | 我们的 ProtocolAdapter |
|---|---|---|
| 方向 | 单向 (构建请求 + 归一化响应) | **双向** (请求转换 + 响应转换) |
| 输出 | 内部 NormalizedResponse | **下游期望的精确 JSON** |
| 核心方法 | build_kwargs() / normalize_response() | request_to() / response_from() / stream_from() |
| format 参数 | 由 api_mode 固定 | 作为 source/target 动态传入 |

### N×M 转换矩阵

采用**枢纽格式**策略：chat_completions 作为通用中间格式，所有协议先转到 chat_completions，再从 chat_completions 转到目标协议。N×M 问题退化为 N×1 + 1×M。

```
                    upstream_format
                chat_completions  responses  messages
client_format
 responses            ✅              🔜         🔜
 messages             ✅              🔜         🔜
 chat_completions     (透传)          🔜         🔜
```

初期实现：每个 Adapter 只支持与 `chat_completions` 的双向转换。未来如果需要 `responses → messages` 直接转换（不经 chat 中转），只需在 `ResponsesAdapter.request_to("messages", ...)` 中添加分支。

- `client_format == upstream_format` → Router 层透传，不经过 Adapter
- Adapter 内部若收到不支持的 format 组合 → raise `UnsupportedFormat`

### 注册表 + 惰性发现

```python
# proxy/adapters/__init__.py

_REGISTRY: dict[str, ProtocolAdapter] = {}
_discovered: bool = False

def register_adapter(cls: type) -> None:
    instance = cls()
    _REGISTRY[instance.protocol] = instance

def get_adapter(protocol: str) -> ProtocolAdapter | None:
    global _discovered
    if not _discovered:
        _discover_adapters()
    return _REGISTRY.get(protocol)

def _discover_adapters():
    global _discovered
    _discovered = True
    from . import responses   # noqa
    from . import messages    # noqa
```

每个 Adapter 文件末尾自注册: `register_adapter(ResponsesAdapter)`。

### TransformRouter 重写

从 `(client_format, upstream_format)` 函数字典 → 委托注册表中的 Adapter:

```python
class TransformRouter:

    @classmethod
    def convert_request(cls, body, client_format, upstream_format, model_cfg):
        if client_format == upstream_format:
            return body
        adapter = get_adapter(client_format)
        if adapter is None:
            raise KeyError(f"不支持的客户端协议: {client_format}")
        return adapter.request_to(upstream_format, body, model_cfg)

    @classmethod
    def convert_response(cls, response, upstream_format, client_format):
        if client_format == upstream_format:
            return response
        adapter = get_adapter(client_format)
        if adapter is None:
            raise KeyError(f"不支持的客户端协议: {client_format}")
        return adapter.response_from(upstream_format, response)

    @classmethod
    def stream_convert(cls, chunks, upstream_format, client_format, **kwargs):
        if client_format == upstream_format:
            yield from chunks
            return
        adapter = get_adapter(client_format)
        if adapter is None:
            raise KeyError(f"不支持的客户端协议: {client_format}")
        yield from adapter.stream_from(upstream_format, chunks, **kwargs)
```

**参数语义**: convert_request 的 source 是 client_format；convert_response 的 target 也是 client_format（响应要转回客户端协议）。统一使用 `client_format` / `upstream_format` 命名。

### UpstreamDriver 三格式支持

```python
class UpstreamDriver:
    def __init__(self, upstream_cfg: dict):
        self._cfg = upstream_cfg
        self.format = upstream_cfg.get("format", "chat_completions")
        self._openai: OpenAI | None = None
        self._anthropic: Anthropic | None = None

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
    def anthropic(self) -> Anthropic:
        """按需创建 Anthropic 客户端。"""
        if self._anthropic is None:
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
        if format == "chat_completions":
            return self.openai.chat.completions.create(stream=True, **body)
        if format == "responses":
            return self.openai.responses.create(stream=True, **body)
        if format == "messages":
            return self.anthropic.messages.create(stream=True, **body)
        raise ValueError(f"不支持的上游格式: {format}")

    def close(self):
        if self._openai: self._openai.close()
        if self._anthropic: self._anthropic.close()
```

两个 SDK 客户端懒初始化：实际只使用一个时不会创建另一个。

### Handler 简化

核心改动只有一行 — 消除硬编码 pivot:

```python
# 之前:
intermediate = "chat_completions"  # ← 硬编码
chat_body = TransformRouter.convert_request(body, client_format, intermediate, model_cfg)
raw_response = driver.create(target_format, chat_body)  # target_format 也总是 chat_completions

# 之后:
upstream_body = TransformRouter.convert_request(body, client_format, upstream_format, model_cfg)
raw_response = driver.create(upstream_format, upstream_body)
```

透传/转换判定不变量:

```python
if client_format == upstream_format and upstream_format:
    _handle_passthrough(...)
else:
    _handle_convert(client_format, upstream_format, ...)
```

`_handle_passthrough` 保留当前实现（http.client 原样转发），`_handle_convert` 走 Router + SDK。

## 数据流

```
客户端 POST /v1/responses  (client_format="responses")
  ↓
handler.do_POST → 解析 upstream_format="messages"
  ↓
client_format != upstream_format → _handle_convert
  ↓
TransformRouter.convert_request(body, "responses", "messages", model_cfg)
  → 查注册表 get_adapter("responses") → ResponsesAdapter
  → adapter.request_to("messages", body, cfg)
  ↓
UpstreamDriver.create("messages", converted_body)
  → self.anthropic.messages.create(**converted_body)
  ↓
TransformRouter.convert_response(raw_response_dict, "messages", "responses")
  → get_adapter("responses") → ResponsesAdapter
  → adapter.response_from("messages", raw_response_dict)
  ↓
返回 JSON 给客户端
```

## 异常处理

- `get_adapter(protocol)` 返回 None → KeyError → handler 返回 400 "不支持的客户端协议"
- `adapter.request_to(target, ...)` 遇到不支持的 target → UnsupportedFormat → handler 返回 400
- SDK 异常 → `_handle_sdk_error` 保持不变 (isinstance 链映射 HTTP 状态码)
- `close()` 确保连接在 finally 中释放

## 线程安全

Handler 每个请求实例化一个新的 `UpstreamDriver`（创建于 `_handle_convert` / `_forward_non_streaming` / `_forward_streaming` 中），方法返回前调用 `driver.close()` 释放。不存在跨请求共享 Driver 实例的场景，因此 `@property` 懒初始化中的 `_openai is None` 检查无线程竞争风险，无需加锁。

## 迁移步骤

1. 创建 `proxy/adapters/` 目录 + `base.py`（ProtocolAdapter 抽象基类 + UnsupportedFormat 异常）
2. 创建 `proxy/adapters/__init__.py`（注册表 + 惰性发现）
3. 创建 `proxy/adapters/responses.py`（ResponsesAdapter，仅支持 chat_completions 双向转换，逻辑来自 transform_responses.py）
4. 创建 `proxy/adapters/messages.py`（MessagesAdapter，仅支持 chat_completions 双向转换，逻辑来自 transform_anthropic.py）
5. 重写 `proxy/transform_router.py`（委托注册表取代函数字典）
6. 重写 `proxy/upstream_driver.py`（三格式 support + 懒初始化 SDK 客户端）
7. 修改 `proxy/handler.py` — 消除硬编码 intermediate，改用 client_format/upstream_format
8. 更新 `proxy/__init__.py` re-export
9. 更新 `proxy/transform.py` — 删除或极简 shim
10. 新建 `test/test_adapters.py` — 覆盖 ResponsesAdapter、MessagesAdapter 的 request_to/response_from/stream_from
11. 适配 `test/test_handler.py` — 新 Router 接口
12. **新旧并行验证**：`python3 -m pytest test/ -q` 全量通过，确保保持 531 tests（此时新旧代码共存）
13. 删除 `test/test_transform.py`、`test/test_transform_anthropic.py`
14. 删除 `proxy/transform_responses.py`、`proxy/transform_anthropic.py`
15. **最终验证**：`python3 -m pytest test/ -q` 全量通过后 commit

## 测试

### test_adapters.py（新建）

每个 Adapter 至少覆盖:

```
ResponsesAdapter:
  request_to("chat_completions", ...) → 等价于旧 responses_to_chat 行为
  request_to("messages", ...) → raise UnsupportedFormat
  response_from("chat_completions", ...) → 等价于旧 chat_to_responses 行为
  stream_from("chat_completions", ...) → 等价于旧 create_codex_sse_stream 行为

MessagesAdapter:
  request_to("chat_completions", ...) → 等价于旧 anthropic_to_chat 行为
  request_to("responses", ...) → raise UnsupportedFormat
  response_from("chat_completions", ...) → 等价于旧 chat_to_anthropic 行为
  stream_from("chat_completions", ...) → 等价于旧 create_anthropic_sse_stream 行为
```

### 迁移的测试

- `test_transform.py`（138 tests）→ `test_adapters.py` 中 ResponsesAdapter 测试，必须覆盖全部 138 个场景
- `test_transform_anthropic.py`（44 tests）→ `test_adapters.py` 中 MessagesAdapter 测试，必须覆盖全部 44 个场景

迁移验证标准:
1. 新旧代码并行期运行 `python3 -m pytest test/ -q`，确认 test_adapters + 旧测试文件同时通过
2. 删除旧文件后再次全量通过，确保测试总数持平（不丢失覆盖）
3. Adapter 新增 UnsupportedFormat 异常测试（额外增加，不计入迁移数）

### 适配的测试

- `test/test_handler.py`（20 tests）— 适配新的 Router 接口签名（参数名从 source/target 改为 client_format/upstream_format）

### 不变的测试

其余全部: test_config_manager, test_sse_utils, test_token_stats, test_pricing_manager, test_request_logger, test_response_store, test_e2e_smoke, test_stats_service 等

## 兼容性

- 不兼容旧 import: `from proxy.transform import responses_to_chat` 删除
- 新 import: `from proxy.adapters import get_adapter` 或 `from proxy.transform_router import TransformRouter`
- `__init__.py` re-export 更新
