# 设计文档：SDK 上游驱动 + TransformRouter

> 日期：2026-05-13
> 状态：Draft v2

## 一、背景与动机

当前 AI Proxy 的转换路径（convert path）使用 `http.client` 手动管理上游 HTTP 连接，代码重复且难维护。同时，转换路径硬编码为 "Chat Completions 中间格式"，扩展新转换对需改 handler。

**目标：**
1. 转换路径用 `openai` Python SDK 替代手动 HTTP 调用
2. 引入 `TransformRouter` 解耦转换路由，支持任意 `(source, target)` 格式对
3. **透传路径完全不动**——保留原始 HTTP 字节级透明，只做日志记录和 token 提取
4. 保持现有功能不变（日志、Token 统计、ResponseStore）

**不变更的部分：**
- 透传路径（passthrough）保持 `http.client` 原样转发，不改 SDK
- `_create_upstream_conn()`、`_normalize_forward_path()`、`_write_chunk()` 保留
- `_forward_pass_through_non_streaming()`、`_forward_pass_through_streaming()` 保留

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
        ├─ 相同 → 透传路径（不变，http.client 原始字节转发）
        │     仅记录日志 + 提取 token usage
        │
        └─ 不同 → 转换路径（SDK 驱动）
              │
              ├─ request_type == target_format（如 chat→chat）？
              │     └─ 短路：直接用 SDK 调上游，无需 TransformRouter
              │
              └─ 需要格式转换：
                    │
                    ▼
               TransformRouter.convert_request(source, target)
                    │
                    ▼
               openai SDK → 上游（当前所有转换路径经 Chat Completions 中间格式）
                    │
                    ▼
               TransformRouter.convert_response / stream_convert
                    │
                    ▼
               返回客户端
```

> **注意**：当前转换路径中，`target` 始终是 `chat_completions`。TransformRouter 的映射表设计为通用的 `(source, target)` 对，未来注册新路径时 handler 不需改动。

## 三、新增文件

### 3.1 `proxy/transform_router.py` — 转换路由器

负责按 `(source_format, target_format)` 对分发到具体转换器。

```python
from __future__ import annotations

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

        model_cfg: resolve_model() 返回值 {"target": str, "multimodal": bool, "upstream": dict}。
                   当 ConfigCache.resolve() 返回 None 时不含 "upstream" 键，
                   handler 应 fallback 到 CONFIG["upstream"]。model_cfg["upstream"]
                   与 UpstreamDriver 的 upstream_cfg 是同一个 dict。
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
```

**扩展新路径（未来）：**

```python
_request_converters[("chat_completions", "messages")] = chat_to_anthropic_request
_response_converters[("messages", "chat_completions")] = anthropic_to_chat_response
```

只需注册映射，handler 不需改动。

### 3.2 `proxy/upstream_driver.py` — SDK 上游驱动

封装 `openai` SDK 实例化和调用，用于转换路径。anthropic SDK 按需加载（未来转换路径扩展时使用）。

```python
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
                timeout=httpx.Timeout(connect=connect_timeout, read=timeout_cfg,
                                      write=timeout_cfg, pool=connect_timeout),
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
```

**关键设计点：**
- `httpx.Timeout` 分离连接超时和读取超时（解决 #6）
- `httpx.Client(verify=ssl_verify)` 传入 SSL 验证配置（解决 #4）
- anthropic SDK 不在此处导入，未来按需扩展时再加 lazy import
- Python 3.10+ 要求（`from __future__ import annotations` 兼容 3.9，但 `X | None` 语法需 3.10+）

**SDK 参数映射**：`create(format, body)` 通过 `**body` 解包传递给 SDK。

需要注意：
- 转换路径的 `body` 由 `TransformRouter` 生成，key 与 SDK 参数名一致
- Anthropic Messages API 中 `system` 是顶层参数，SDK 的 `messages.create(**body)` 能正确处理

### 3.3 handler.py 改造

#### 3.3.1 透传路径（passthrough）—— 不变

**透传路径保持原始 HTTP 字节级透明，不做任何改动。**

透传的核心语义是模拟客户端直连上游——请求/响应原样转发，仅附加：
- 四阶段日志记录
- Token usage 提取
- 模型名替换（model → target）

当前的 `_forward_pass_through_non_streaming()` 和 `_forward_pass_through_streaming()` 保持不变。

#### 3.3.2 转换路径（convert）—— 改用 SDK

**非流式：**

```python
# 改造前：硬编码 if/elif + 手动 HTTP
if request_type == REQUEST_TYPE_RESPONSES:
    chat_body = responses_to_chat(body, model_cfg)
    ...
elif request_type == REQUEST_TYPE_MESSAGES:
    chat_body = anthropic_to_chat(body, model_cfg)
    ...

# 改造后：
upstream_cfg = model_cfg.get("upstream") or CONFIG.get("upstream", {})
target_format = upstream_cfg.get("format", "chat_completions")

# 短路：request_type 与 target_format 相同时无需转换，走透传
# （这种情况理论上在 do_POST() 已被拦截，但做防御性检查）
if request_type == target_format:
    self._handle_passthrough(...)
    return

chat_body = TransformRouter.convert_request(body, request_type, target_format, model_cfg)

# previous_response_id：仅 responses 路径支持多轮对话
if request_type == REQUEST_TYPE_RESPONSES:
    prev_id = body.get("previous_response_id")
    if prev_id:
        response_store = getattr(self.server, "response_store", None)
        if response_store is not None:
            record = response_store.get(prev_id)
            if record:
                system_msgs = [m for m in chat_body["messages"] if m.get("role") == "system"]
                non_system_msgs = [m for m in chat_body["messages"] if m.get("role") != "system"]
                chat_body["messages"] = system_msgs + record.conversation + non_system_msgs
            else:
                logging.warning(f"previous_response_id={prev_id!r} 不存在或已过期，忽略历史")

driver = UpstreamDriver(upstream_cfg)
raw_response = driver.create(target_format, chat_body)
chat_response = raw_response.model_dump()

output = TransformRouter.convert_response(chat_response, target_format, request_type)

# 存储 response（仅 responses 路径）
if store_enabled and is_responses_api:
    _store_response(self.server, output, chat_body.get("messages", []))

driver.close()
```

**流式：**

```python
target_format = upstream_cfg.get("format", "chat_completions")
if request_type == target_format:
    self._handle_passthrough(...)
    return

chat_body = TransformRouter.convert_request(body, request_type, target_format, model_cfg)

# previous_response_id：仅 responses 路径支持多轮对话
# 必须在 TransformRouter 转换之后、调上游之前执行（因为要修改 chat_body.messages）
if request_type == REQUEST_TYPE_RESPONSES:
    prev_id = body.get("previous_response_id")
    if prev_id:
        response_store = getattr(self.server, "response_store", None)
        if response_store is not None:
            record = response_store.get(prev_id)
            if record:
                system_msgs = [m for m in chat_body["messages"] if m.get("role") == "system"]
                non_system_msgs = [m for m in chat_body["messages"] if m.get("role") != "system"]
                chat_body["messages"] = system_msgs + record.conversation + non_system_msgs
            else:
                logging.warning(f"previous_response_id={prev_id!r} 不存在或已过期，忽略历史")

driver = UpstreamDriver(upstream_cfg)
stream = driver.create_stream(target_format, chat_body)

# 设置 SSE 响应头
self.send_response(200)
self.send_header("Content-Type", "text/event-stream")
self.send_header("Cache-Control", "no-cache")
self.send_header("X-Accel-Buffering", "no")
self.end_headers()

# TransformRouter 迭代转换，同时从 SSE 事件中提取 usage
_rstore = getattr(self.server, "response_store", None) if store_enabled else None
final_usage = None
for sse_event in TransformRouter.stream_convert(
    stream, target_format, request_type,
    request_messages=chat_body.get("messages") if _rstore else None,
    response_store=_rstore,
):
    self.wfile.write(sse_event.encode())
    self.wfile.flush()
    # 从 response.completed / message_delta 事件中提取 usage
    if "response.completed" in sse_event or "message_delta" in sse_event:
        try:
            data_json = sse_event.split("data: ", 1)[1]
            data = json.loads(data_json)
            usage = (data.get("response", {}).get("usage")
                     or data.get("usage"))
            if usage:
                final_usage = usage
        except (json.JSONDecodeError, IndexError):
            pass

# 流结束后记录 token 统计
if final_usage:
    record_token_stats(final_usage, ctx)
else:
    logger.warning(f"流式路径未提取到 usage: request_id={request_id}")
driver.close()
```

> **usage 提取说明**：SSE 工厂在 `response.completed`（Responses 格式）或 `message_delta`（Anthropic 格式）事件中携带 usage。
> handler 在迭代时从这些事件中解析。这是当前 `_forward_streaming` 已有的模式（handler.py:977-981），保持一致。

## 四、SSE 流式工厂适配

当前 `create_codex_sse_stream()` / `create_anthropic_sse_stream()` 接收 file-like 对象（`resp.read(size)`）。

### 改造策略：兼容适配层

先让工厂同时支持旧签名（file-like）和新签名（Iterable[dict]），验证通过后再删旧签名。

```python
# 适配逻辑：
def create_codex_sse_stream(chunks_or_response, *, request_messages=None, response_store=None):
    if hasattr(chunks_or_response, 'read'):
        # 旧路径：file-like 对象（透传路径，或改造过渡期）
        chunks = _sse_response_to_dicts(chunks_or_response)
    else:
        # 新路径：SDK 流式迭代器
        chunks = (c.model_dump() if hasattr(c, 'model_dump') else c for c in chunks_or_response)

    state = CodexStreamState()
    for chunk_dict in chunks:
        for event_str in _process_codex_chunk(chunk_dict, state):
            yield event_str

    if response_store and state.response_id:
        _store_response_from_state(state, request_messages, response_store)
```

**好处：**
- 透传路径不受影响（继续传 file-like）
- 转换路径传 SDK 迭代器
- 过渡期共存，验证通过后删除 `hasattr(..., 'read')` 分支

**统一签名**：两个工厂函数都改为 `(chunks_or_response, *, request_messages=None, response_store=None)`。

**`iter_sse_events()` / `_parse_sse_event()` 保留**——兼容层内部使用。

## 五、Token 统计适配

### 转换路径（SDK）

```python
# 非流式：从 SDK 对象提取
raw_response = driver.chat_create(...)
chat_response = raw_response.model_dump()
usage_dict = chat_response.get("usage", {})
if usage_dict:
    record_token_stats(usage_dict, ctx)

# 流式：从 SSE 工厂状态机提取（工厂内部已在 message_delta 事件中提取 usage）
# handler 层从 TransformRouter.stream_convert 的最终状态获取
```

### 透传路径（不变）

保持现有的从原始 SSE 字节中提取 usage 的逻辑。

### 防御性检查

当流式路径结束后 `final_usage` 为 None 时，记录 warning：

```python
if not final_usage:
    logger.warning(f"流式路径未提取到 usage: request_id={request_id}, "
                   f"model={model}, target={target}")
```

`token_stats.py` 中的 `_extract_tokens()` 已支持多种 usage 格式（OpenAI / Anthropic），无需修改。

## 六、错误处理

### SDK 异常映射

```python
from openai import APIError, APIConnectionError, APITimeoutError, RateLimitError, BadRequestError

_EXCEPTION_MAP = {
    APITimeoutError:    (504, "timeout_error"),
    RateLimitError:     (429, "rate_limit_error"),
    APIConnectionError: (502, "connection_error"),
    BadRequestError:    (400, "invalid_request_error"),
    APIError:           (502, "upstream_error"),
}
```

handler 中统一 try/except（含 httpx 层兜底）：

```python
import httpx

try:
    result = driver.create(target_format, body)
except APIError as e:
    status, error_type = _EXCEPTION_MAP.get(type(e), (502, "upstream_error"))
    self._send_json(status, {"error": {"type": error_type, "message": str(e)}})
except httpx.HTTPError as e:
    self._send_json(502, {"error": {"type": "connection_error", "message": str(e)}})
except Exception as e:
    self._send_json(502, {"error": {"type": "upstream_error", "message": str(e)}})
finally:
    driver.close()
return
```

## 七、变更的代码

| 文件 | 变更 | 说明 |
|------|------|------|
| `proxy/handler.py` | 重写 `_forward_non_streaming()` | SDK 调用替代手动 HTTP |
| `proxy/handler.py` | 重写 `_forward_streaming()` | SDK 流式替代手动 SSE |
| `proxy/handler.py` | 修改 `_handle_convert()` | 使用 TransformRouter 替代硬编码 if/elif |
| `proxy/transform_responses.py` | 改造 `create_codex_sse_stream()` 签名 | 兼容适配层（同时支持 file-like 和 Iterable） |
| `proxy/transform_anthropic.py` | 改造 `create_anthropic_sse_stream()` 签名 | 同上 |
| `proxy/transform.py` | 更新 re-export | 导出 TransformRouter 和 UpstreamDriver |
| `proxy/__init__.py` | 更新 re-export | 同上 |

### 不变的代码

| 文件 | 说明 |
|------|------|
| `proxy/handler.py` 透传路径 | `_handle_passthrough()`、`_forward_pass_through_*()` 不变 |
| `proxy/common.py` | `_create_upstream_conn()`、`_normalize_forward_path()` 不变 |
| `proxy/request_logger.py` | 四阶段日志不变 |
| `proxy/token_stats.py` | Token 统计不变 |
| `proxy/response_store.py` | LRU+TTL 缓存不变 |
| `proxy/config_manager.py` | ConfigCache 不变 |
| `proxy/pricing_manager.py` | 计费不变 |
| `proxy/sse_utils.py` | `_format_sse_event()` 不变 |
| `proxy/handler.py` `_write_chunk()` | 透传路径仍使用 |

## 八、依赖变更

| 包 | 版本 | 用途 |
|----|------|------|
| `openai` | ≥2.36.0（已安装） | 转换路径的 Chat Completions 调用 |
| `httpx` | ≥0.27.0 | openai SDK 的底层依赖（已随 openai 安装） |

需要创建 `requirements.txt`（仓库根目录）：

```
openai>=2.36.0
```

**anthropic SDK 暂不引入**——当前无转换路径使用它。未来注册 `(chat_completions, messages)` 转换对时再添加。

项目从 "纯 Python 标准库（零外部依赖）" 变为 "一个 SDK 依赖"。

**Python 版本要求**：≥3.10（`X | None` 类型注解语法）。

## 九、测试策略

### 9.1 新增测试

| 测试文件 | 覆盖内容 |
|---------|---------|
| `test/test_transform_router.py` | TransformRouter 注册/查找/KeyError |
| `test/test_upstream_driver.py` | UpstreamDriver 客户端创建、SDK 调用（mock）、SSL/超时配置 |
| `test/mock_server.py` | 基于 `http.server` 的最小 Chat Completions SSE mock server。支持非流式 JSON + 流式 SSE 逐 chunk + usage 返回。 |

### 9.2 修改测试

| 测试文件 | 修改内容 |
|---------|---------|
| `test/test_handler.py` | mock UpstreamDriver 替代 mock http.client（仅转换路径测试） |
| `test/test_e2e_smoke.py` | 端到端冒烟，指向 `mock_server.py` 验证全链路 |

### 9.3 不变测试

| 测试文件 | 原因 |
|---------|---------|
| `test/test_transform.py` | 转换逻辑不变 |
| `test/test_transform_anthropic.py` | 转换逻辑不变 |
| `test/test_proxy_pass_through.py` | 透传路径不变 |
| `test/test_config_manager.py` | ConfigCache 不变 |
| `test/test_request_logger.py` | 日志不变 |
| `test/test_token_stats.py` | Token 统计不变 |
| `test/test_stats_service.py` | StatsService 不变 |

### 9.4 验证清单

- [ ] SDK 反序列化后重序列化的 SSE 与原始 SSE 的关键字段无损（delta content, tool_calls, usage, finish_reason）
- [ ] SSL verify=False 时 SDK 能连接自签名上游
- [ ] 连接超时独立于读取超时（上游不可达时 10s 而非 120s）
- [ ] 流式路径 final_usage 为 None 时有 warning 日志

## 十、迁移步骤

1. 创建 `requirements.txt`（仓库根目录）
2. 新建 `proxy/transform_router.py`
3. 新建 `proxy/upstream_driver.py`（含 SSL/超时配置）
4. 改造 SSE 流式工厂签名（兼容适配层：同时支持 file-like 和 Iterable）
5. 重写 `handler.py` 的 `_handle_convert()` + `_forward_non_streaming()` + `_forward_streaming()`
6. 更新 `proxy/transform.py` shim 和 `proxy/__init__.py` re-export
7. 新增 `test_transform_router.py` + `test_upstream_driver.py`
8. 新增 `test/mock_server.py`
9. 修改 `test_handler.py` 适配新 mock
10. 全量测试通过（531+ tests）
11. 验证转换路径 + 透传路径均正常（手动冒烟）
12. 更新 `CLAUDE.md`（依赖声明、架构描述）

## 十一、风险与缓解

| 风险 | 缓解 |
|------|------|
| SDK 流式行为与手动 SSE 不一致 | 先跑通非流式，再逐步替换流式 |
| SSE 工厂改造引入 bug | 兼容适配层，旧路径继续工作 |
| 依赖引入打破"零外部依赖"原则 | 明确接受，在 CLAUDE.md 更新声明 |
| SSL 验证配置未传入 SDK | 使用 `httpx.Client(verify=...)` 显式传入 |
| 连接超时与读取超时不分离 | 使用 `httpx.Timeout(connect=10, read=120, ...)` |
| 流式 usage 静默丢失 | final_usage 为 None 时记录 warning |
