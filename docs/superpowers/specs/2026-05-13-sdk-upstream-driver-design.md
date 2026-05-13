# 设计文档：SDK 上游驱动 + TransformRouter

> 日期：2026-05-13
> 状态：Draft

## 一、背景与动机

当前 AI Proxy 使用 `http.client` 手动管理上游 HTTP 连接，包括 SSL/TLS、超时、重试、SSE 流式解析。代码分散在 `handler.py` 的 4 个转发方法（共 ~800 行），存在大量重复的连接管理代码。

**核心问题：**
- `_create_upstream_conn()` 手动构建 HTTPS 连接，含代理 tunneling 逻辑
- 4 个转发方法各自重复：连接创建 → 超时设置 → 请求发送 → 重试循环 → 响应读取
- SSE 流式解析手动 `buf.split(b"\n\n")`，边界情况多
- 转换路径硬编码为 "Chat Completions 中间格式"，扩展新转换对需改 handler

**目标：**
1. 用 `openai` + `anthropic` Python SDK 替代手动 HTTP 调用
2. 引入 `TransformRouter` 解耦转换路由，支持任意 `(source, target)` 格式对
3. 自动检测上下游格式 → 透传/转换路由 → 选择 SDK
4. 保持现有功能不变（日志、Token 统计、ResponseStore）

## 二、架构总览

```
客户端请求 (responses / messages / chat_completions)
        │
        ▼
  do_POST() → request_type (source_format)
        │
        ▼
  ConfigCache.resolve(model) → upstream_cfg
        │
        ▼
  source_format == upstream_cfg.format ?
        │
        ├─ 相同 → 透传路径
        │     │
        │     ├── format="chat_completions" → openai SDK
        │     └── format="messages"         → anthropic SDK
        │
        └─ 不同 → 转换路径
              │
              ▼
         TransformRouter.convert_request(source, target)
              │
              ▼
         SDK 调上游（当前所有转换路径强制经过 Chat Completions 中间格式）
              │
              ▼
         TransformRouter.convert_response / stream_convert
              │
              ▼
         返回客户端
```

> **注意**：当前转换路径中，`target` 始终是 `chat_completions`（所有客户端格式先转为 Chat Completions，再通过 openai SDK 发上游）。TransformRouter 的映射表设计为通用的 `(source, target)` 对，未来注册新路径（如 `chat_completions → messages`）时 handler 不需改动。

## 三、新增文件

### 3.1 `proxy/transform_router.py` — 转换路由器

负责按 `(source_format, target_format)` 对分发到具体转换器。

```python
class TransformRouter:
    """协议转换路由——(源格式, 目标格式) → 转换器 映射表。"""

    # 请求转换：source（客户端格式） → target（上游格式）
    _request_converters: dict[tuple[str, str], Callable] = {
        ("responses",        "chat_completions"): responses_to_chat,
        ("messages",         "chat_completions"): anthropic_to_chat,
    }

    # 非流式响应转换：source（上游格式） → target（客户端格式）
    _response_converters: dict[tuple[str, str], Callable] = {
        ("chat_completions", "responses"):        chat_to_responses,
        ("chat_completions", "messages"):         chat_to_anthropic,
    }

    # 流式响应转换：source（上游 SSE 格式） → target（客户端 SSE 格式）
    _stream_converters: dict[tuple[str, str], Callable] = {
        ("chat_completions", "responses"):        create_codex_sse_stream,
        ("chat_completions", "messages"):         create_anthropic_sse_stream,
    }

    @classmethod
    def convert_request(cls, body: dict, source: str, target: str, model_cfg: dict) -> dict:
        """请求转换。KeyError 表示不支持的格式对。

        model_cfg: resolve_model() 返回值 {"target": str, "multimodal": bool, "upstream": dict}
                   传给具体转换器用于确定目标模型名和特性。与 UpstreamDriver 的 upstream_cfg
                   是同一个 dict（model_cfg["upstream"] == upstream_cfg）。
        """
        return cls._request_converters[(source, target)](body, model_cfg)

    @classmethod
    def convert_response(cls, response: dict, source: str, target: str) -> dict:
        """非流式响应转换。"""
        return cls._response_converters[(source, target)](response)

    @classmethod
    def stream_convert(cls, chunks, source: str, target: str, *,
                       request_messages=None, response_store=None):
        """流式响应转换（生成器）。

        工厂函数统一签名：(chunks, *, request_messages=None, response_store=None)
        - chunks: SDK 流式迭代器（Iterable[ChatCompletionChunk]）
        - request_messages: 用于 response_store 的对话历史（仅 responses 路径使用）
        - response_store: LRU+TTL 缓存（仅 responses 路径使用）
        """
        converter = cls._stream_converters[(source, target)]
        yield from converter(chunks, request_messages=request_messages,
                             response_store=response_store)

    @classmethod
    def supported_pairs(cls) -> list[tuple[str, str]]:
        """返回所有已注册的转换对。"""
        return list(cls._request_converters.keys())
```

**扩展新路径（未来）：**

```python
# 例：downstream chat → anthropic 请求（上游是 messages 格式）
_request_converters[("chat_completions", "messages")] = chat_to_anthropic_request
_response_converters[("messages", "chat_completions")] = anthropic_to_chat_response
```

只需注册映射，handler 不需改动。

### 3.2 `proxy/upstream_driver.py` — SDK 上游驱动

封装 `openai` / `anthropic` SDK 实例化和调用，替换 `_create_upstream_conn()`。

```python
from openai import OpenAI
from anthropic import Anthropic

class UpstreamDriver:
    """SDK 上游驱动——按 upstream_cfg 创建 SDK 客户端并调用。"""

    def __init__(self, upstream_cfg: dict):
        self._cfg = upstream_cfg
        self.format = upstream_cfg.get("format", "chat_completions")
        self._openai_client: OpenAI | None = None
        self._anthropic_client: Anthropic | None = None

    # ── 客户端懒加载 ──

    @property
    def openai(self) -> OpenAI:
        if self._openai_client is None:
            self._openai_client = OpenAI(
                base_url=self._cfg["base_url"],
                api_key=self._cfg["api_key"],
                timeout=self._cfg.get("timeout", 120),
                max_retries=self._cfg.get("retry", 0),
            )
        return self._openai_client

    @property
    def anthropic(self) -> Anthropic:
        if self._anthropic_client is None:
            try:
                from anthropic import Anthropic
            except ImportError:
                raise ImportError(
                    "anthropic SDK 未安装。Messages 透传路径需要此依赖。"
                    "安装命令：pip install anthropic"
                )
            self._anthropic_client = Anthropic(
                base_url=self._cfg["base_url"],
                api_key=self._cfg["api_key"],
                timeout=self._cfg.get("timeout", 120),
                max_retries=self._cfg.get("retry", 0),
            )
        return self._anthropic_client

    # ── Chat Completions（openai SDK）──

    def chat_create(self, **kwargs) -> object:
        """非流式 Chat Completions。返回 ChatCompletion 对象。"""
        return self.openai.chat.completions.create(**kwargs)

    def chat_stream(self, **kwargs):
        """流式 Chat Completions。返回 Stream[ChatCompletionChunk]。"""
        kwargs.pop("stream", None)  # 移除可能的重复 key
        kwargs.setdefault("stream_options", {"include_usage": True})
        return self.openai.chat.completions.create(stream=True, **kwargs)

    # ── Messages（anthropic SDK）──

    def messages_create(self, **kwargs) -> object:
        """非流式 Anthropic Messages。返回 Message 对象。"""
        return self.anthropic.messages.create(**kwargs)

    def messages_stream(self, **kwargs):
        """流式 Anthropic Messages。返回 MessageStream。"""
        kwargs.pop("stream", None)  # 移除可能的重复 key
        return self.anthropic.messages.stream(**kwargs)

    # ── 统一入口 ──

    def create(self, format: str, body: dict):
        """按 format 自动路由到对应 SDK 的非流式调用。"""
        if format == "chat_completions":
            return self.chat_create(**body)
        elif format == "messages":
            return self.messages_create(**body)
        raise ValueError(f"不支持的上游格式: {format}")

    def create_stream(self, format: str, body: dict):
        """按 format 自动路由到对应 SDK 的流式调用。"""
        if format == "chat_completions":
            return self.chat_stream(**body)
        elif format == "messages":
            return self.messages_stream(**body)
        raise ValueError(f"不支持的上游格式: {format}")
```

**SDK 参数映射**：`create(format, body)` 通过 `**body` 解包传递给 SDK。

需要注意：
- 转换路径的 `body` 由 `TransformRouter` 生成，key 与 SDK 参数名一致（`model`, `messages`, `temperature` 等）
- 透传路径的 `body` 来自客户端原始 JSON，可能包含 `stream` 键——需在非流式调用前移除
- anthropic SDK 要求必填 `max_tokens`，透传路径需确保该字段存在（缺失时设默认值）
- Anthropic Messages API 中 `system` 是顶层参数（不在 messages 数组中），SDK 的 `messages.create(**body)` 能正确处理——`**body` 解包时 `system` 作为独立参数传入，无需特殊处理

```python
# 透传路径参数处理示例
sdk_body = {k: v for k, v in body.items() if k != "stream"}
if upstream_format == "messages" and "max_tokens" not in sdk_body:
    sdk_body["max_tokens"] = 4096
# 替换 model 为 target
if "model" in sdk_body:
    sdk_body["model"] = target
result = driver.create(upstream_format, sdk_body)
```

### 3.3 handler.py 改造

#### 3.3.1 透传路径（passthrough）

**非流式：**

```python
# 改造前：~60 行手动 HTTP
conn = _create_upstream_conn(upstream_cfg, parsed, port)
conn.connect()
conn.sock.settimeout(timeout)
conn.request(self.command, path, body=body_raw, headers=headers)
resp = conn.getresponse()
resp_body = resp.read()
# ...

# 改造后：~10 行 SDK 调用
driver = UpstreamDriver(upstream_cfg)
# 构造 SDK 参数：移除 stream、替换 model
sdk_body = {k: v for k, v in body.items() if k != "stream"}
sdk_body["model"] = target
if upstream_format == "messages" and "max_tokens" not in sdk_body:
    sdk_body["max_tokens"] = 4096
result = driver.create(upstream_format, sdk_body)
resp_dict = result.model_dump()
```

**流式：**

```python
# 改造前：~100 行手动 SSE 中继
while True:
    chunk = resp.read(4096)
    buf += chunk
    while b"\n\n" in buf:
        event_raw, buf = buf.split(b"\n\n", 1)
        self._write_chunk(event_raw)
        # ...

# 改造后：
final_usage = None
if upstream_format == "chat_completions":
    stream = driver.chat_stream(model=target, messages=body["messages"], ...)
    for chunk in stream:
        # 从最后一个 chunk 提取 usage（stream_options.include_usage=True 时）
        if hasattr(chunk, 'usage') and chunk.usage:
            final_usage = chunk.usage.model_dump()
        sse_event = f"data: {chunk.model_dump_json()}\n\n"
        self.wfile.write(sse_event.encode())
        self.wfile.flush()
    self.wfile.write(b"data: [DONE]\n\n")
    self.wfile.flush()

elif upstream_format == "messages":
    # Anthropic SDK 流式：SDK 解析 SSE → 类型化事件 → 序列化回 SSE 转发
    with driver.messages_stream(model=target, messages=body["messages"], ...) as stream:
        for event in stream:
            # Anthropic SSE 格式：event + data 两行
            event_type = event.type
            data_json = json.dumps(event.model_dump())
            sse_event = f"event: {event_type}\ndata: {data_json}\n\n"
            self.wfile.write(sse_event.encode())
            self.wfile.flush()
    # 从 stream.get_final_message() 提取 usage
    final_message = stream.get_final_message()
    if final_message and final_message.usage:
        final_usage = final_message.usage.model_dump()

# 流结束后记录 Token 统计
if final_usage:
    record_token_stats(final_usage, ctx)
```

#### 3.3.2 转换路径（convert）

**非流式：**

```python
# 改造前：硬编码 if/elif
if request_type == REQUEST_TYPE_RESPONSES:
    chat_body = responses_to_chat(body, model_cfg)
    response_converter = chat_to_responses
elif request_type == REQUEST_TYPE_MESSAGES:
    chat_body = anthropic_to_chat(body, model_cfg)
    response_converter = chat_to_anthropic

# 改造后：TransformRouter 自动路由
target_format = upstream_cfg.get("format", "chat_completions")
chat_body = TransformRouter.convert_request(body, request_type, target_format, model_cfg)

driver = UpstreamDriver(upstream_cfg)
raw_response = driver.create(target_format, chat_body)
chat_response = raw_response.model_dump()

output = TransformRouter.convert_response(chat_response, target_format, request_type)
```

**流式：**

```python
target_format = upstream_cfg.get("format", "chat_completions")
chat_body = TransformRouter.convert_request(body, request_type, target_format, model_cfg)

driver = UpstreamDriver(upstream_cfg)
stream = driver.create_stream(target_format, chat_body)

# TransformRouter 迭代转换，透传 response_store 和 request_messages
_rstore = getattr(self.server, "response_store", None) if store_enabled else None
for sse_event in TransformRouter.stream_convert(
    stream, target_format, request_type,
    request_messages=chat_body.get("messages") if _rstore else None,
    response_store=_rstore,
):
    self.wfile.write(sse_event.encode())
    self.wfile.flush()
```

## 四、SSE 流式工厂适配

当前 `create_codex_sse_stream()` / `create_anthropic_sse_stream()` 接收 file-like 对象（`resp.read(size)`），手动解析 SSE 字节流。

### 改造方案

工厂函数统一签名，接收 SDK 流式对象（`Iterable`）+ 可选参数：

```python
# 改造前（两个工厂签名不一致）：
def create_codex_sse_stream(upstream_response):
    """upstream_response: 有 read(size) 方法的对象"""

def create_anthropic_sse_stream(upstream_response):
    """upstream_response: 有 read(size) 方法的对象"""

# 改造后（统一签名）：
def create_codex_sse_stream(chunks, *, request_messages=None, response_store=None):
    """chunks: Iterable[ChatCompletionChunk]（来自 openai SDK）
    request_messages / response_store: 仅 responses 路径使用，用于存储多轮对话
    """

def create_anthropic_sse_stream(chunks, *, request_messages=None, response_store=None):
    """chunks: Iterable[ChatCompletionChunk]（来自 openai SDK）
    request_messages / response_store: 当前未使用，预留签名一致性
    """
```

内部逻辑：

```python
def create_codex_sse_stream(chunks, *, request_messages=None, response_store=None):
    state = CodexStreamState()
    for chunk in chunks:
        chunk_dict = chunk.model_dump()
        for event_str in _process_codex_chunk(chunk_dict, state):
            yield event_str
    # 流结束时存入 response_store（如提供）
    if response_store and state.response_id:
        _store_response_from_state(state, request_messages, response_store)
```

**核心变化**：输入从 "原始 SSE 字节流" 变为 "SDK 解析后的 dict"，`_process_*_chunk()` 内部逻辑（状态机、tool call 缓冲、usage 提取）完全复用。

**`iter_sse_events()` / `_parse_sse_event()` 保留但仅供测试使用**（构造模拟 SSE 输入）。

## 五、Token 统计适配

SDK 返回的对象自带 `usage` 属性：

```python
# openai SDK
chat_completion = driver.chat_create(...)
usage = chat_completion.usage
# usage.prompt_tokens, usage.completion_tokens, usage.prompt_tokens_details.cached_tokens

# anthropic SDK
message = driver.messages_create(...)
usage = message.usage
# usage.input_tokens, usage.output_tokens, usage.cache_read_input_tokens

# 统一转为 dict 后传入现有 record_token_stats()
usage_dict = chat_completion.model_dump()["usage"]  # 或 message.model_dump()["usage"]
record_token_stats(usage_dict, ctx)
```

`token_stats.py` 中的 `_extract_tokens()` 已支持多种 usage 格式（OpenAI / Anthropic），无需修改。

## 六、错误处理

### SDK 异常映射

```python
from openai import APIError, APIConnectionError, APITimeoutError, RateLimitError, BadRequestError
from anthropic import AnthropicError, APITimeoutError as AnthropicTimeout, RateLimitError as AnthropicRateLimit

_EXCEPTION_MAP = {
    # openai
    APITimeoutError:    (504, "timeout_error"),
    RateLimitError:     (429, "rate_limit_error"),
    APIConnectionError: (502, "connection_error"),
    BadRequestError:    (400, "invalid_request_error"),
    APIError:           (502, "upstream_error"),
    # anthropic
    AnthropicTimeout:   (504, "timeout_error"),
    AnthropicRateLimit: (429, "rate_limit_error"),
    AnthropicError:     (502, "upstream_error"),
}
```

handler 中统一 try/except（含 httpx 层兜底）：

```python
import httpx

try:
    result = driver.create(target_format, body)
except (APIError, AnthropicError) as e:
    status, error_type = _EXCEPTION_MAP.get(type(e), (502, "upstream_error"))
    self._send_json(status, {"error": {"type": error_type, "message": str(e)}})
except httpx.HTTPError as e:
    # 兜底：SDK 底层 httpx 异常（DNS 解析失败、连接拒绝等）
    self._send_json(502, {"error": {"type": "connection_error", "message": str(e)}})
except ImportError as e:
    # anthropic SDK 未安装
    self._send_json(503, {"error": {"type": "dependency_missing", "message": str(e)}})
except Exception as e:
    self._send_json(502, {"error": {"type": "upstream_error", "message": str(e)}})
return
```

## 七、删除的代码

| 文件 | 删除内容 | 原因 |
|------|---------|------|
| `proxy/handler.py` | `_forward_pass_through_non_streaming()` | 替换为 SDK 调用 |
| `proxy/handler.py` | `_forward_pass_through_streaming()` | 替换为 SDK 调用 |
| `proxy/handler.py` | `_forward_non_streaming()` | 替换为 SDK 调用 |
| `proxy/handler.py` | `_forward_streaming()` | 替换为 SDK 调用 |
| `proxy/handler.py` | `_write_chunk()` | 不再使用 chunked 编码 |

> **保留**：`proxy/common.py` 中的 `_create_upstream_conn()` 和 `_normalize_forward_path()` 本次不删除。后续迭代添加 HTTP 代理支持时，通过 SDK `http_client` 参数注入代理后，再删除这两个函数。

> **Breaking Change**：透传流式路径从 `Transfer-Encoding: chunked` 改为直接 `wfile.write() + flush()`。当前转换路径已经是非 chunked 的，此变更统一了行为。理论上依赖 chunked 编码的客户端会受影响，但 SSE 规范不要求 chunked，主流客户端均兼容。

## 八、保留不变的模块

| 模块 | 原因 |
|------|------|
| `proxy/transform_responses.py` | 转换逻辑不变，仅流式工厂签名微调 |
| `proxy/transform_anthropic.py` | 同上 |
| `proxy/request_logger.py` | 四阶段日志不变 |
| `proxy/token_stats.py` | Token 统计不变，`_extract_tokens()` 已支持多格式 |
| `proxy/response_store.py` | LRU+TTL 缓存不变 |
| `proxy/config_manager.py` | ConfigCache 路由解析不变 |
| `proxy/pricing_manager.py` | 计费不变 |
| `proxy/sse_utils.py` | `_format_sse_event()` 不变 |

## 九、依赖变更

| 包 | 版本 | 用途 |
|----|------|------|
| `openai` | ≥2.36.0（已安装） | Chat Completions 调用（主力） |
| `anthropic` | ≥0.50.0 | Messages 调用（透传路径） |

需要创建 `requirements.txt`：

```
openai>=2.36.0
anthropic>=0.50.0
```

项目从 "纯 Python 标准库（零外部依赖）" 变为 "两个 SDK 依赖"。

## 十、测试策略

### 10.1 新增测试

| 测试文件 | 覆盖内容 |
|---------|---------|
| `test/test_transform_router.py` | TransformRouter 注册/查找/KeyError |
| `test/test_upstream_driver.py` | UpstreamDriver 客户端创建、SDK 调用（mock） |
| `test/mock_server.py` | 基于 `http.server` 的最小 Chat Completions SSE mock server，供 e2e 测试使用。支持非流式 JSON + 流式 SSE 逐 chunk + usage 返回。 |

### 10.2 修改测试

| 测试文件 | 修改内容 |
|---------|---------|
| `test/test_handler.py` | mock UpstreamDriver 替代 mock http.client |
| `test/test_proxy_pass_through.py` | mock SDK stream 替代 mock socket |
| `test/test_e2e_smoke.py` | 端到端冒烟，验证全链路 |

### 10.3 不变测试

| 测试文件 | 原因 |
|---------|------|
| `test/test_transform.py` | 转换逻辑不变 |
| `test/test_transform_anthropic.py` | 转换逻辑不变 |
| `test/test_config_manager.py` | ConfigCache 不变 |
| `test/test_request_logger.py` | 日志不变 |
| `test/test_token_stats.py` | Token 统计不变 |
| `test/test_stats_service.py` | StatsService 不变 |

## 十一、迁移步骤

1. 创建 `requirements.txt` + 安装依赖
2. 新建 `proxy/transform_router.py`
3. 新建 `proxy/upstream_driver.py`
4. **同时改造** SSE 流式工厂签名 + handler.py 转发方法（两者互相依赖，不可分开执行）
5. 清理 `common.py`（删除废弃函数）
6. 更新 `proxy/__init__.py` re-export
7. 新增 `test_transform_router.py` + `test_upstream_driver.py`
8. 新增 `test/mock_server.py`（最小 Chat Completions SSE mock server，供 e2e 测试使用）
9. 修改 `test_handler.py` 适配新 mock
10. 全量测试通过（531+ tests）
11. 手动冒烟测试（`./server.sh restart` + 客户端调用）
12. 更新 `CLAUDE.md`（依赖声明、架构描述）

## 十二、风险与缓解

| 风险 | 缓解 |
|------|------|
| SDK 流式行为与手动 SSE 不一致 | 先跑通非流式，再逐步替换流式 |
| `anthropic` SDK 未安装 | UpstreamDriver 中 lazy import + `ImportError`，handler 返回 503 + 安装提示 |
| SSE 流式工厂改造引入 bug | 保留 `iter_sse_events()` 用于测试对比 |
| 依赖引入打破"零外部依赖"原则 | 明确接受，在 CLAUDE.md 更新声明 |
| HTTP 代理支持缺失 | 本次迭代不含代理功能，保留 `_create_upstream_conn()` 作为 fallback，后续迭代通过 SDK `http_client` 参数注入代理 |
