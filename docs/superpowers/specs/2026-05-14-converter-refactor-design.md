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
    def request_to(self, target_format: str, body: dict, model_cfg: dict) -> dict:
        """客户端请求体 → 目标上游格式的请求体。

        model_cfg: {"target": str, "multimodal": bool, "upstream": dict}
        """
        ...

    @abstractmethod
    def response_from(self, source_format: str, response: dict) -> dict:
        """上游响应 dict → 客户端协议格式的响应 dict。"""
        ...

    @abstractmethod
    def stream_from(self, source_format: str, chunks, *,
                    request_messages=None, response_store=None):
        """上游 SSE 流 → 客户端协议格式的 SSE 事件生成器。"""
        ...
```

与 Hermes `ProviderTransport` 的区别:

| | Hermes ProviderTransport | 我们的 ProtocolAdapter |
|---|---|---|
| 方向 | 单向 (构建请求 + 归一化响应) | **双向** (请求转换 + 响应转换) |
| 输出 | 内部 NormalizedResponse | **下游期望的精确 JSON** |
| 核心方法 | build_kwargs() / normalize_response() | request_to() / response_from() / stream_from() |
| format 参数 | 由 api_mode 固定 | 作为 source/target 动态传入 |

### N×M 转换矩阵

每个 Adapter 内部按 target_format / source_format 分发:

```
                    upstream_format
                chat_completions  responses  messages
client_format
 responses            ✅              🔜         🔜
 messages             ✅              🔜         🔜
 chat_completions     (透传)          🔜         🔜
```

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

    # 统一入口
    def create(self, format: str, body: dict):
        if format == "chat_completions":
            return self.openai.chat.completions.create(**body)
        if format == "responses":
            return self.openai.responses.create(**body)
        if format == "messages":
            return self.anthropic.messages.create(**body)
        raise ValueError(f"不支持的上游格式: {format}")

    def create_stream(self, format: str, body: dict):
        # 同上，stream=True
        ...

    def close(self):
        if self._openai: self._openai.close()
        if self._anthropic: self._anthropic.close()
```

两个 SDK 客户端: `openai` (chat_completions + responses)、`anthropic` (messages)，均懒初始化。

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

## 测试

- 新建 `test/test_adapters.py` — ResponsesAdapter、MessagesAdapter 的 request_to / response_from / stream_from
- `test/test_transform.py` — 迁移到 adapter 测试，旧文件删除
- `test/test_transform_anthropic.py` — 迁移到 adapter 测试，旧文件删除
- `test/test_handler.py` — 适配新的 Router 接口
- 其余测试不变 (test_config_manager, test_sse_utils, test_token_stats, test_pricing_manager 等)

## 兼容性

- 不兼容旧 import: `from proxy.transform import responses_to_chat` 删除
- 新 import: `from proxy.adapters import get_adapter` 或 `from proxy.transform_router import TransformRouter`
- `__init__.py` re-export 更新
