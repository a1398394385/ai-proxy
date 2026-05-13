# 转换路径 SDK 驱动 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 openai Python SDK 替换转换路径（convert path）的手动 HTTP 调用，透传路径完全不动。

**Architecture:** 新增 TransformRouter（转换路由）和 UpstreamDriver（SDK 调用封装），改造 SSE 流式工厂签名（兼容适配层），重写 handler.py 的 `_handle_convert()` 及两个转发方法。

**Tech Stack:** Python 3.10+, openai>=2.36.0, httpx (随 openai 安装)

---

## 文件结构与职责

| 文件 | 操作 | 职责 |
|------|------|------|
| `requirements.txt` | **新建** | 声明 openai 依赖 |
| `proxy/transform_router.py` | **新建** | TransformRouter 类——(source,target) → 转换器映射 |
| `proxy/upstream_driver.py` | **新建** | UpstreamDriver 类——openai SDK 封装 |
| `proxy/transform_responses.py` | 修改 | `create_codex_sse_stream()` 兼容适配层 |
| `proxy/transform_anthropic.py` | 修改 | `create_anthropic_sse_stream()` 兼容适配层 |
| `proxy/handler.py` | 修改 | 重写 `_handle_convert` 及两个转发方法 |
| `proxy/transform.py` | 修改 | 新增 re-export |
| `proxy/__init__.py` | 修改 | 新增 re-export |
| `test/test_transform_router.py` | **新建** | TransformRouter 单元测试 |
| `test/test_upstream_driver.py` | **新建** | UpstreamDriver 单元测试 |
| `test/mock_server.py` | **新建** | 最小 Chat Completions SSE mock server |
| `test/test_handler.py` | 修改 | 适配新 mock |
| `CLAUDE.md` | 修改 | 更新依赖声明 |

### 不变更文件

| 文件 | 说明 |
|------|------|
| `proxy/common.py` | `_create_upstream_conn` / `_normalize_forward_path` 保留（透传路径使用） |
| `proxy/handler.py` 透传方法 | `_handle_passthrough` / `_forward_pass_through_*` / `_write_chunk` 保留 |
| `proxy/sse_utils.py` | `_format_sse_event()` 不变 |
| `proxy/request_logger.py` | 四阶段日志不变 |
| `proxy/token_stats.py` | Token 统计不变 |
| `proxy/response_store.py` | LRU+TTL 不变 |
| `proxy/config_manager.py` | ConfigCache 不变 |

---

### Task 1: 创建 requirements.txt

**Files:**
- Create: `requirements.txt`

- [ ] **Step 1: 创建 requirements.txt**

```bash
cd /Users/xys/Github/ai-agent-tools-ai-sdk
cat > requirements.txt << 'EOF'
openai>=2.36.0
EOF
```

- [ ] **Step 2: 确认依赖已安装**

```bash
cd /Users/xys/Github/ai-agent-tools-ai-sdk && python3 -c "import openai; print(f'openai {openai.__version__} OK')"
```

Expected: `openai X.Y.Z OK`

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "chore: 添加 openai SDK 依赖声明"
```

---

### Task 2: 创建 TransformRouter

**Files:**
- Create: `proxy/transform_router.py`
- Create: `test/test_transform_router.py`

- [ ] **Step 1: 写失败的测试**

```python
# test/test_transform_router.py
import unittest
from proxy.transform_router import TransformRouter


class TestTransformRouter(unittest.TestCase):

    def test_known_request_converter(self):
        """已知转换对应返回正确的请求转换函数。"""
        from proxy.transform_responses import responses_to_chat
        router = TransformRouter
        result = router.convert_request(
            {"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
            source="responses",
            target="chat_completions",
            model_cfg={"target": "gpt-4", "multimodal": False, "upstream": {}},
        )
        self.assertIsInstance(result, dict)
        self.assertIn("messages", result)

    def test_unknown_pair_raises_keyerror(self):
        """未注册的转换对抛出 KeyError。"""
        router = TransformRouter
        with self.assertRaises(KeyError):
            router.convert_request(
                {"model": "gpt-4"},
                source="no_such_format",
                target="chat_completions",
                model_cfg={"target": "gpt-4", "multimodal": False, "upstream": {}},
            )

    def test_response_converter_known_pair(self):
        """已知响应转换对应返回正确的函数。"""
        from proxy.transform_responses import chat_to_responses
        router = TransformRouter
        result = router.convert_response(
            {"id": "1", "choices": [{"message": {"role": "assistant", "content": "ok"}}]},
            source="chat_completions",
            target="responses",
        )
        self.assertIsInstance(result, dict)

```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /Users/xys/Github/ai-agent-tools-ai-sdk && python3 -m pytest test/test_transform_router.py -v
```

Expected: 全 FAIL（transform_router 模块不存在）

- [ ] **Step 3: 实现 TransformRouter**

```python
# proxy/transform_router.py
"""协议转换路由器——(源格式, 目标格式) → 转换器 映射表。"""

from __future__ import annotations

from .transform import (
    responses_to_chat,
    chat_to_responses,
    create_codex_sse_stream,
    anthropic_to_chat,
    chat_to_anthropic,
    create_anthropic_sse_stream,
)


class TransformRouter:
    """协议转换路由——(源格式, 目标格式) → 转换器 映射表。"""

    # 请求转换：source（客户端格式） → target（上游格式）
    _request_converters: dict[tuple[str, str], object] = {
        ("responses",        "chat_completions"): responses_to_chat,
        ("messages",         "chat_completions"): anthropic_to_chat,
    }

    # 非流式响应转换：source（上游格式） → target（客户端格式）
    _response_converters: dict[tuple[str, str], object] = {
        ("chat_completions", "responses"):        chat_to_responses,
        ("chat_completions", "messages"):         chat_to_anthropic,
    }

    # 流式响应转换：source（上游 SSE 格式） → target（客户端 SSE 格式）
    _stream_converters: dict[tuple[str, str], object] = {
        ("chat_completions", "responses"):        create_codex_sse_stream,
        ("chat_completions", "messages"):         create_anthropic_sse_stream,
    }

    @classmethod
    def convert_request(cls, body: dict, source: str, target: str, model_cfg: dict) -> dict:
        """请求转换。KeyError 表示不支持的格式对。

        model_cfg: resolve_model() 返回值 {"target": str, "multimodal": bool, "upstream": dict}。
                   当 ConfigCache.resolve() 返回 None 时不含 "upstream" 键，
                   handler 应 fallback 到 CONFIG["upstream"]。
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
        """
        converter = cls._stream_converters[(source, target)]
        yield from converter(chunks, request_messages=request_messages,
                             response_store=response_store)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd /Users/xys/Github/ai-agent-tools-ai-sdk && python3 -m pytest test/test_transform_router.py -v
```

Expected: 3 tests 全部通过。

- [ ] **Step 5: Commit**

```bash
git add proxy/transform_router.py test/test_transform_router.py
git commit -m "feat: 新增 TransformRouter 协议转换路由"
```

---

### Task 3: 创建 UpstreamDriver

**Files:**
- Create: `proxy/upstream_driver.py`
- Create: `test/test_upstream_driver.py`

- [ ] **Step 1: 写失败的测试**

```python
# test/test_upstream_driver.py
import unittest
from unittest.mock import patch, MagicMock


class TestUpstreamDriver(unittest.TestCase):

    def setUp(self):
        self.cfg = {
            "base_url": "https://test.example.com/v1",
            "api_key": "test-key",
            "timeout": 30,
            "connect_timeout": 5,
            "retry": 1,
            "ssl_verify": True,
            "format": "chat_completions",
        }

    def test_constructor_passes_ssl_verify_to_httpx_client(self):
        """验证 UpstreamDriver 将 ssl_verify 传给 httpx.Client（mock 方式）。"""
        import httpx
        from proxy.upstream_driver import UpstreamDriver

        with patch("httpx.Client") as mock_client_cls:
            driver = UpstreamDriver(self.cfg)
            _ = driver.openai
        # 验证 httpx.Client 被创建时传入了 verify=True
        mock_client_cls.assert_called_once()
        call_kwargs = mock_client_cls.call_args.kwargs
        self.assertTrue(call_kwargs.get("verify"))

    def test_ssl_verify_false_passed_to_httpx_client(self):
        """ssl_verify=False 时 httpx.Client.verify=False。"""
        import httpx
        from proxy.upstream_driver import UpstreamDriver

        cfg = {**self.cfg, "ssl_verify": False}
        with patch("httpx.Client") as mock_client_cls:
            driver = UpstreamDriver(cfg)
            _ = driver.openai
        call_kwargs = mock_client_cls.call_args.kwargs
        self.assertFalse(call_kwargs.get("verify"))

    def test_timeout_separates_connect_and_read(self):
        """httpx.Timeout 使用 connect_timeout 和 timeout 分别设置。"""
        import httpx
        from proxy.upstream_driver import UpstreamDriver

        with patch("httpx.Timeout") as mock_timeout:
            driver = UpstreamDriver(self.cfg)
            _ = driver.openai
        mock_timeout.assert_called_once()
        call_kwargs = mock_timeout.call_args.kwargs
        self.assertEqual(call_kwargs["connect"], 5.0)
        self.assertEqual(call_kwargs["read"], 30.0)

    def test_rejects_unsupported_format(self):
        """不支持的上游格式抛出 ValueError。"""
        from proxy.upstream_driver import UpstreamDriver
        driver = UpstreamDriver(self.cfg)
        with self.assertRaises(ValueError):
            driver.create("unsupported_format", {"model": "test"})

    def test_chat_create_unpacks_dict_to_sdk(self):
        """chat_create 将 dict 解包传给 openai SDK。"""
        from proxy.upstream_driver import UpstreamDriver

        driver = UpstreamDriver(self.cfg)
        mock_create = MagicMock(return_value=MagicMock(model_dump=lambda: {"id": "1"}))
        with patch.object(driver.openai.chat.completions, "create", mock_create):
            driver.chat_create(
                model="gpt-4",
                messages=[{"role": "user", "content": "hi"}],
                temperature=0.7,
            )
        mock_create.assert_called_once_with(
            model="gpt-4",
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.7,
        )

    def test_chat_stream_copies_kwargs_no_side_effect(self):
        """chat_stream 不修改调用者传入的 dict（无副作用）。"""
        from proxy.upstream_driver import UpstreamDriver

        driver = UpstreamDriver(self.cfg)
        original = {"model": "gpt-4", "stream": True, "messages": []}
        saved = dict(original)
        mock_create = MagicMock()
        with patch.object(driver.openai.chat.completions, "create", mock_create):
            driver.chat_stream(**original)
        # original dict 不应被修改
        self.assertEqual(original, saved)

    def test_chat_stream_removes_duplicate_stream_key(self):
        """chat_stream 移除可能重复的 stream key 但不抛 TypeError。"""
        from proxy.upstream_driver import UpstreamDriver

        driver = UpstreamDriver(self.cfg)
        mock_create = MagicMock()
        with patch.object(driver.openai.chat.completions, "create", mock_create):
            driver.chat_stream(
                model="gpt-4",
                messages=[{"role": "user", "content": "hi"}],
                stream=True,
            )
        call_kwargs = mock_create.call_args.kwargs
        self.assertTrue(call_kwargs["stream"])

    def test_close_cleans_up_client(self):
        """close() 关闭底层客户端。"""
        from proxy.upstream_driver import UpstreamDriver
        driver = UpstreamDriver(self.cfg)
        _ = driver.openai
        driver.close()
        self.assertIsNone(driver._openai_client)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /Users/xys/Github/ai-agent-tools-ai-sdk && python3 -m pytest test/test_upstream_driver.py -v
```

- [ ] **Step 3: 实现 UpstreamDriver**

```python
# proxy/upstream_driver.py
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
```

- [ ] **Step 4: 运行测试**

```bash
cd /Users/xys/Github/ai-agent-tools-ai-sdk && python3 -m pytest test/test_upstream_driver.py -v
```

注意：`client._client._client` 访问 httpx 内部属性可能因 openai SDK 版本不同而变化。如失败，调整测试的访问路径或改用 mock 模式。

- [ ] **Step 5: Commit**

```bash
git add proxy/upstream_driver.py test/test_upstream_driver.py
git commit -m "feat: 新增 UpstreamDriver SDK 上游驱动"
```

---

### Task 4: SSE 流式工厂兼容适配层

**Files:**
- Modify: `proxy/transform_responses.py:923-947`
- Modify: `proxy/transform_anthropic.py:331-368`

- [ ] **Step 1: 改造 create_codex_sse_stream 签名**

```python
# proxy/transform_responses.py — 替换 create_codex_sse_stream 函数

def create_codex_sse_stream(chunks_or_response, *, request_messages=None, response_store=None):
    """读取上游 SSE 流（file-like 或 SDK Iterable），生成 Responses API 格式的 SSE 事件。

    chunks_or_response:
        - file-like 对象（有 read 方法）→ 兼容旧路径（透传/过渡期）
        - Iterable[dict|ChatCompletionChunk] → 新路径（openai SDK 流）
    request_messages: chat_body["messages"]，用于构建 conversation
    response_store: ResponseStore 实例；非 None 时在 finish() 后存储 response
    """
    converter = CodexStreamConverter()
    converter.response_id = generate_response_id()

    # 兼容适配：检测输入类型
    if hasattr(chunks_or_response, 'read'):
        # 旧路径：file-like 对象
        chunks_iter = iter_sse_events(chunks_or_response)
        # iter_sse_events 返回 (event_type, data) 的 dict，需要提取 data
        chunks_iter = (e.get("data") or {} for e in chunks_iter if e.get("data"))
    else:
        # 新路径：SDK 流式迭代器
        def _to_dict(chunk):
            if hasattr(chunk, 'model_dump'):
                return chunk.model_dump()
            return chunk
        chunks_iter = (_to_dict(c) for c in chunks_or_response)

    for data_dict in chunks_iter:
        if isinstance(data_dict, str) and data_dict == "[DONE]":
            break
        if isinstance(data_dict, str):
            # "data:" 前缀后的 JSON 字符串
            try:
                data_dict = json.loads(data_dict)
            except json.JSONDecodeError:
                continue
        for sse_str in converter.process_chunk(data_dict):
            yield sse_str

    for sse_str in converter.finish():
        yield sse_str

    # finish() 返回后：存储 response
    if response_store is not None:
        from .response_store import ResponseRecord
        output_list = [item for _, item in converter.output_items]
        from .transform_responses import output_items_to_messages
        assistant_msgs = output_items_to_messages(output_list)
        messages_for_conv = (request_messages or []) + assistant_msgs
        record = ResponseRecord(
            response_id=converter.response_id,
            model=converter.model or "",
            output=output_list,
            conversation=messages_for_conv,
            usage=converter.usage,
            status="completed",
            created_at=time.time(),
            expires_at=time.time() + response_store.ttl_seconds,
        )
        response_store.put(record.response_id, record)
```

注意：需要在文件顶部添加 `import json`（如未导入）。

- [ ] **Step 2: 改造 create_anthropic_sse_stream 签名**

```python
# proxy/transform_anthropic.py — 替换 create_anthropic_sse_stream 函数

def create_anthropic_sse_stream(chunks_or_response, *, request_messages=None, response_store=None):
    """读取上游 SSE 流（file-like 或 SDK Iterable），生成 Anthropic Messages 格式的 SSE 事件。

    chunks_or_response: 同 create_codex_sse_stream。
    request_messages / response_store: 当前未使用，预留签名一致性。
    """
    state = AnthropicStreamState()

    # 兼容适配：检测输入类型
    if hasattr(chunks_or_response, 'read'):
        chunks_iter = iter_sse_events(chunks_or_response)
        chunks_iter = (e.get("data") or {} for e in chunks_iter if e.get("data"))
    else:
        def _to_dict(chunk):
            if hasattr(chunk, 'model_dump'):
                return chunk.model_dump()
            return chunk
        chunks_iter = (_to_dict(c) for c in chunks_or_response)

    try:
        for data_dict in chunks_iter:
            if isinstance(data_dict, str) and data_dict == "[DONE]":
                break
            if isinstance(data_dict, str):
                try:
                    data_dict = json.loads(data_dict)
                except json.JSONDecodeError:
                    continue
            if not data_dict:
                continue

            # 捕获 model / id → 发送 message_start
            if not state.message_id:
                state.message_id = data_dict.get("id", "")
                state.model = data_dict.get("model", "")
                for event_str in _send_message_start(state):
                    yield event_str

            if "usage" in data_dict and data_dict["usage"]:
                state.usage = data_dict["usage"]

            choices = data_dict.get("choices", [])
            if choices:
                choice = choices[0]
                if choice.get("finish_reason") and not state.finish_reason:
                    state.finish_reason = choice["finish_reason"]
                delta = choice.get("delta", {})
                if delta:
                    for event_str in _process_anthropic_delta(delta, state):
                        yield event_str
    except Exception as e:
        error_data = {
            "error": {"type": "stream_error", "message": f"Stream error: {e}"},
        }
        yield _format_sse_event("error", error_data)
        return

    # 补发 message_delta
    if state.finish_reason and not state.message_delta_sent:
        state.message_delta_sent = True
        events = _close_open_blocks(state)
        for event_str in events:
            yield event_str
        stop_reason = _STREAM_FINISH_MAP.get(state.finish_reason, "end_turn")
        delta_event = {"delta": {"stop_reason": stop_reason, "stop_sequence": None}}
        if state.usage:
            usage_out = {
                "input_tokens": state.usage.get("prompt_tokens", 0),
                "output_tokens": state.usage.get("completion_tokens", 0),
            }
            cached = state.usage.get("prompt_tokens_details", {}).get("cached_tokens")
            if cached is not None:
                usage_out["cache_read_input_tokens"] = cached
            delta_event["usage"] = usage_out
        yield _format_sse_event("message_delta", delta_event)

    yield _format_sse_event("message_stop", {})
```

注意：需要删除 `create_anthropic_sse_stream` 末尾的旧 `message_stop` yield（原文件第 405 行），避免重复发送。

> **代码说明**：上述 `create_anthropic_sse_stream` 完整展示了改造后的函数。与原版本（`proxy/transform_anthropic.py:331-405`）的**仅有差异**是输入层——原版调用 `iter_sse_events(upstream_response)` 解析原始 SSE 字节，新版换为 `hasattr` 检测分支 + `model_dump()` 适配 SDK 对象。核心的状态机逻辑（`_process_anthropic_delta`、`_send_message_start`、`_close_open_blocks`）和 state 管理完全不变。

- [ ] **Step 3: 增加流式工厂签名一致性测试**

在 `test_transform_router.py` 中增加:

```python
def test_stream_converter_has_unified_signature(self):
    """流式转换器注册表中所有函数接受 (chunks, *, request_messages, response_store)。"""
    import inspect
    from proxy.transform_router import TransformRouter
    for (source, target), func in TransformRouter._stream_converters.items():
        sig = inspect.signature(func)
        self.assertIn("request_messages", sig.parameters)
        self.assertIn("response_store", sig.parameters)
```

- [ ] **Step 4: 运行现有测试确认兼容性**

```bash
cd /Users/xys/Github/ai-agent-tools-ai-sdk && python3 -m pytest test/test_transform.py test/test_transform_anthropic.py test/test_transform_router.py -v
```

Expected: 全部通过

- [ ] **Step 5: Commit**

```bash
git add proxy/transform_responses.py proxy/transform_anthropic.py test/test_transform_router.py
git commit -m "refactor: SSE 流式工厂兼容适配层——同时支持 file-like 和 SDK Iterable"
```

---

### Task 5: 重写 handler.py 转换路径

**Files:**
- Modify: `proxy/handler.py`

- [ ] **Step 1: 重写 _handle_convert 方法**

`_handle_convert` 方法（第 588-680 行）替换为：

```python
    def _handle_convert(self, request_type, model_name, model_cfg, body,
                        request_id, request_ts, target):
        """转换路径：TransformRouter 路由 → SDK 调上游 → 响应转换。

        替代旧的硬编码 responses_to_chat / anthropic_to_chat 分发。
        """
        is_stream = body.get("stream", False)
        upstream_cfg = model_cfg.get("upstream") or CONFIG.get("upstream", {})
        target_format = upstream_cfg.get("format", "chat_completions")
        logger = get_logger()

        # 防御性检查：request_type == target_format 应已被 do_POST 拦截
        if request_type == target_format:
            logging.warning(
                f"_handle_convert 收到相同格式: request_type={request_type}, "
                f"upstream_format={target_format}，回退到透传"
            )
            # 调用透传（与 do_POST 中逻辑一致）
            body_raw = json.dumps(body).encode("utf-8")
            self._handle_passthrough(
                request_type, model_name, target, request_ts, request_id,
                upstream_cfg, body_raw, json.loads(body_raw)
            )
            return

        # 请求格式转换
        try:
            chat_body = TransformRouter.convert_request(
                body, request_type, target_format, model_cfg
            )
        except KeyError:
            logging.error(
                f"不支持的转换对: {request_type} → {target_format}"
            )
            self._send_json(400, {
                "error": {
                    "type": "invalid_request_error",
                    "message": f"不支持的格式转换: {request_type} → {target_format}"
                }
            })
            return
        except Exception as e:
            logging.exception(f"请求转换失败 ({request_type})")
            if logger:
                logger.log_converted_request(
                    request_id, model_name, target,
                    {"error": str(e)}, request_type=request_type,
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
                request_id, model_name, target, chat_body,
                request_type=request_type,
                request_path=upstream_url,
            )

        # previous_response_id：仅 responses 路径支持多轮对话
        if request_type == REQUEST_TYPE_RESPONSES:
            prev_id = body.get("previous_response_id")
            if prev_id:
                response_store = getattr(self.server, "response_store", None)
                if response_store is not None:
                    record = response_store.get(prev_id)
                    if record:
                        system_msgs = [
                            m for m in chat_body["messages"]
                            if m.get("role") == "system"
                        ]
                        non_system_msgs = [
                            m for m in chat_body["messages"]
                            if m.get("role") != "system"
                        ]
                        chat_body["messages"] = (
                            system_msgs + record.conversation + non_system_msgs
                        )
                    else:
                        logging.warning(
                            f"previous_response_id={prev_id!r} 不存在或已过期"
                        )

        # 设置回调
        if request_type == REQUEST_TYPE_RESPONSES:
            store_enabled = body.get("store", True)
            is_responses_api = True
        elif request_type == REQUEST_TYPE_MESSAGES:
            store_enabled = False
            is_responses_api = False
        else:
            store_enabled = False
            is_responses_api = False

        logging.info(
            f"转换: model={model_name}, stream={is_stream}, target={target}, "
            f"source={request_type}, target_format={target_format}"
        )

        if is_stream:
            self._forward_streaming_v2(
                chat_body, model_cfg, request_id, model_name, target, request_ts,
                upstream_cfg, request_type, target_format,
                store_enabled=store_enabled,
            )
        else:
            self._forward_non_streaming_v2(
                chat_body, request_id, model_name, target, request_ts,
                upstream_cfg, request_type, target_format,
                store_enabled=store_enabled,
                is_responses_api=is_responses_api,
            )
```

- [ ] **Step 2: 添加 _forward_non_streaming_v2**

在 `ProxyHandler` 中新增方法：

```python
    def _forward_non_streaming_v2(self, chat_body, request_id, model, target,
                                   request_ts, upstream_cfg, request_type,
                                   target_format, store_enabled=True,
                                   is_responses_api=False):
        """非流式转换请求：SDK 调用 + 响应转换 + Token 统计。"""
        from .upstream_driver import UpstreamDriver

        driver = UpstreamDriver(upstream_cfg)
        logger = get_logger()

        try:
            raw_response = driver.create(target_format, chat_body)
            chat_response = raw_response.model_dump()
        except Exception as e:
            self._handle_sdk_error(e)
            driver.close()
            return

        duration_ms = 0  # SDK 不直接暴露耗时，由 upstream_driver 的 Timeout 管理
        request_ts_for_stats = request_ts

        # 阶段 3：记录上游响应
        if logger:
            logger.log_upstream_response(
                request_id, 200, chat_response, 0,
                model, target,
                request_type=request_type,
            )

        # 阶段 4：转换响应 + Token 统计
        try:
            output = TransformRouter.convert_response(
                chat_response, target_format, request_type
            )
            if logger:
                logger.log_converted_response(
                    request_id, model, target, output,
                    request_type=request_type,
                )

            usage = chat_response.get("usage", {})
            if usage:
                ctx = {
                    "request_id": request_id,
                    "request_type": request_type,
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
                    {"error": str(e)}, request_type=request_type,
                )
            self._send_json(500, {
                "error": {"type": "internal_error", "message": str(e)}
            })
            driver.close()
            return

        # 存储 response（仅 responses 路径）
        if store_enabled and is_responses_api:
            _store_response(self.server, output, chat_body.get("messages", []))

        self._send_json(200, output)
        driver.close()
```

- [ ] **Step 3: 添加 _forward_streaming_v2**

```python
    def _forward_streaming_v2(self, chat_body, model_cfg, request_id, model_name,
                               target, request_ts, upstream_cfg, request_type,
                               target_format, store_enabled=True):
        """流式转换请求：SDK 流式调用 + TransformRouter 逐事件转换。"""
        from .upstream_driver import UpstreamDriver

        driver = UpstreamDriver(upstream_cfg)
        logger = get_logger()

        try:
            stream = driver.create_stream(target_format, chat_body)
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
        sse_buffer = []
        final_usage = None

        try:
            _rstore = (
                getattr(self.server, "response_store", None)
                if store_enabled else None
            )
            for sse_event in TransformRouter.stream_convert(
                stream, target_format, request_type,
                request_messages=chat_body.get("messages") if _rstore else None,
                response_store=_rstore,
            ):
                self.wfile.write(sse_event.encode("utf-8"))
                self.wfile.flush()
                sse_buffer.append(sse_event)

                # 用 _parse_sse_event 做结构化解析，不依赖字符串切割
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
        full_sse = "".join(sse_buffer)

        # 日志
        if logger:
            logger.log_upstream_response(
                request_id, 200, full_sse, duration_ms,
                model_name, target,
                request_type=request_type,
            )
            logger.log_converted_response(
                request_id, model_name, target,
                {"streaming": True, "note": "SDK 流式响应"},
                request_type=request_type,
            )

        # Token 统计
        if final_usage:
            ctx = {
                "request_id": request_id,
                "request_type": request_type,
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

- [ ] **Step 4: 添加 _handle_sdk_error 辅助方法**

```python
    def _handle_sdk_error(self, e: Exception):
        """统一 SDK 异常 → HTTP 错误映射。

        使用 isinstance 按继承链从具体到通用依次检查，避免遗漏子类。
        继承链：BadRequestError → RateLimitError → APITimeoutError →
               APIConnectionError → APIError → Exception
        """
        import httpx

        try:
            from openai import (
                APIError, APIConnectionError, APITimeoutError,
                RateLimitError, BadRequestError,
            )
            # 按继承链从具体到通用
            if isinstance(e, APITimeoutError):
                self._send_json(504, {"error": {"type": "timeout_error", "message": str(e)}})
                return
            if isinstance(e, RateLimitError):
                self._send_json(429, {"error": {"type": "rate_limit_error", "message": str(e)}})
                return
            if isinstance(e, BadRequestError):
                self._send_json(400, {"error": {"type": "invalid_request_error", "message": str(e)}})
                return
            if isinstance(e, APIConnectionError):
                self._send_json(502, {"error": {"type": "connection_error", "message": str(e)}})
                return
            if isinstance(e, APIError):
                self._send_json(502, {"error": {"type": "upstream_error", "message": str(e)}})
                return
        except ImportError:
            pass

        if isinstance(e, httpx.HTTPError):
            self._send_json(502, {"error": {"type": "connection_error", "message": str(e)}})
        else:
            self._send_json(502, {"error": {"type": "upstream_error", "message": str(e)}})
```

- [ ] **Step 5: 在 handler.py 顶部添加导入**

```python
# 在现有 import 块末尾添加：
from .transform_router import TransformRouter  # noqa: E402
```

- [ ] **Step 6: 保留旧方法**

保留 `_forward_non_streaming` 和 `_forward_streaming`（不改名）。因为它们被 `_handle_convert` 调用——但更新 `_handle_convert` 后，旧方法只调用新 v2 版本。旧方法可继续存在作为 fallback。

更好的方式：重写 `_handle_convert` 后，`_forward_non_streaming` 和 `_forward_streaming` 不再被调用。**但暂不删除**，用 `# 保留：旧转换路径，v2 替换后不再使用` 注释标记。

- [ ] **Step 7: 运行现有测试定位需要适配的测试**

```bash
cd /Users/xys/Github/ai-agent-tools-ai-sdk && python3 -m pytest test/test_handler.py -v
```

Expected: 部分测试可能失败（因为 _handle_convert 现在调用 TransformRouter 和 UpstreamDriver）

- [ ] **Step 8: Commit**

```bash
git add proxy/handler.py
git commit -m "refactor: 转换路径改用 SDK 驱动 + TransformRouter 路由"
```

---

### Task 6: 创建 mock server + 更新测试

**Files:**
- Create: `test/mock_server.py`
- Modify: `test/test_handler.py`

- [ ] **Step 1: 创建 mock server**

```python
# test/mock_server.py
"""最小 Chat Completions SSE mock server，供 e2e 测试使用。"""

import json
import time
import socket
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler


class MockChatHandler(BaseHTTPRequestHandler):
    """模拟 Chat Completions API — 支持非流式 JSON 和流式 SSE。"""

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body_raw = self.rfile.read(content_length)
        body = json.loads(body_raw)
        is_stream = body.get("stream", False)

        if is_stream:
            self._handle_stream(body)
        else:
            self._handle_non_stream(body)

    def _handle_non_stream(self, body):
        model = body.get("model", "mock-model")
        messages = body.get("messages", [])
        content = f"mock response to: {messages[-1].get('content', '')[:100] if messages else 'empty'}"

        response = {
            "id": f"chatcmpl-mock-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "prompt_tokens_details": {"cached_tokens": 20},
            },
        }
        self._send_json(200, response)

    def _handle_stream(self, body):
        model = body.get("model", "mock-model")

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        chat_id = f"chatcmpl-mock-{int(time.time())}"
        words = ["Hello", ", ", "this", " is", " a", " mock", " streaming", " response", "!"]

        for i, word in enumerate(words):
            chunk = {
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {"content": word},
                    "finish_reason": None,
                }],
            }
            if i == len(words) - 1:
                chunk["choices"][0]["finish_reason"] = "stop"
                chunk["usage"] = {
                    "prompt_tokens": 100,
                    "completion_tokens": len(words),
                    "total_tokens": 100 + len(words),
                }

            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
            self.wfile.flush()
            time.sleep(0.01)

        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _send_json(self, status, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_mock_server(port=0):
    """启动 mock server，返回 (server, port)。port=0 自动分配。"""
    server = HTTPServer(("127.0.0.1", port), MockChatHandler)
    actual_port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, actual_port
```

- [ ] **Step 2: 新增 handler 的 SDK 转换路径测试**

（测试实现见 Task 8——此步骤仅新增 mock_server，测试由 Task 8 完成。）

- [ ] **Step 3: Commit**

```bash
git add test/mock_server.py test/test_handler.py
git commit -m "test: 新增 mock server + handler SDK 转换路径测试"
```

---

### Task 7: 更新 re-exports 和 CLAUDE.md

**Files:**
- Modify: `proxy/transform.py`
- Modify: `proxy/__init__.py`
- Modify: `CLAUDE.md`

- [ ] **Step 1: 更新 proxy/transform.py**

在 `proxy/transform.py` 末尾添加：

```python
# SDK 驱动和转换路由（Task 2-3 新增）
from .transform_router import TransformRouter  # noqa: F401
from .upstream_driver import UpstreamDriver  # noqa: F401
```

- [ ] **Step 2: 更新 proxy/__init__.py**

在 `proxy/__init__.py` 的 import 块末尾添加：

```python
from .transform_router import TransformRouter  # noqa: F401 — re-export
from .upstream_driver import UpstreamDriver  # noqa: F401 — re-export
```

- [ ] **Step 3: 更新 CLAUDE.md**

将以下章节更新：
- 依赖：加 `openai>=2.36.0`（非纯标准库）
- Proxy 结构表：加 `transform_router.py` 和 `upstream_driver.py`
- 代码速查表：加 TransformRouter 和 UpstreamDriver 的快速索引

- [ ] **Step 4: Commit**

```bash
git add proxy/transform.py proxy/__init__.py CLAUDE.md
git commit -m "docs: 更新 re-exports + CLAUDE.md 依赖声明"
```

---

### Task 8: 修复 test_handler.py 测试

**Files:**
- Modify: `test/test_handler.py`

- [ ] **Step 1: 运行全量 handler 测试确定失败范围**

```bash
cd /Users/xys/Github/ai-agent-tools-ai-sdk && python3 -m pytest test/test_handler.py -v 2>&1 | head -60
```

- [ ] **Step 2: 添加 SDK 转换路径测试**

```python
# test/test_handler.py — 添加新的测试类

class TestHandlerConvertSDK(unittest.TestCase):
    """测试 _handle_convert 的 SDK 驱动路径（v2）。"""

    @classmethod
    def setUpClass(cls):
        from test.mock_server import start_mock_server
        cls.mock_server, cls.mock_port = start_mock_server()

    @classmethod
    def tearDownClass(cls):
        cls.mock_server.shutdown()

    def setUp(self):
        """注入 mock 配置：直接写 CONFIG 的 upstream 段 + mock UpstreamDriver。

        _handle_convert 从 CONFIG["upstream"] 取 fallback 上游配置，
        从 model_cfg["upstream"] 取实际上游配置（由 handler 上层传入）。
        不需要真实 data db。
        """
        from proxy.common import CONFIG
        self._old_config = dict(CONFIG)
        CONFIG["upstream"] = {}

    def tearDown(self):
        from proxy.common import CONFIG
        CONFIG.clear()
        CONFIG.update(self._old_config)

    def _build_handler(self, upstream_cfg):
        """构建用于测试的 ProxyHandler 实例，注入 mock 上游配置。"""
        from proxy.handler import ProxyHandler
        from proxy.common import CONFIG
        CONFIG["upstream"] = upstream_cfg

        class MockServer:
            response_store = None

        h = ProxyHandler.__new__(ProxyHandler)
        h.server = MockServer()
        h.client_address = ("127.0.0.1", 12345)
        h.headers = {}
        h.path = "/v1/responses"
        h.command = "POST"

        import io
        h.wfile = io.BytesIO()
        return h

    def test_non_streaming_responses_to_chat_completions(self):
        """非流式 responses → chat_completions 端到端测试。"""
        upstream_cfg = {
            "base_url": f"http://127.0.0.1:{self.mock_port}/v1",
            "api_key": "mock-key",
            "timeout": 30,
            "connect_timeout": 5,
            "ssl_verify": False,
            "retry": 0,
            "format": "chat_completions",
        }
        h = self._build_handler(upstream_cfg)
        body = {"model": "gpt-4", "input": "hello"}
        model_cfg = {
            "target": "gpt-4",
            "multimodal": False,
            "upstream": upstream_cfg,
        }
        from proxy.request_logger import _generate_request_id
        h._handle_convert(
            "responses", "gpt-4", model_cfg, body,
            _generate_request_id(), "2025-01-01 00:00:00", "gpt-4"
        )
        # 验证 wfile 有响应内容
        h.wfile.seek(0)
        raw = h.wfile.read()
        self.assertGreater(len(raw), 0)

    def test_chat_to_chat_shorts_to_passthrough(self):
        """chat → chat 短路回退到透传（防御性检查）。"""
        upstream_cfg = {
            "base_url": f"http://127.0.0.1:{self.mock_port}/v1",
            "api_key": "mock-key",
            "format": "chat_completions",
        }
        h = self._build_handler(upstream_cfg)
        body = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "hi"}],
        }
        model_cfg = {
            "target": "gpt-4",
            "multimodal": False,
            "upstream": upstream_cfg,
        }
        # request_type == target_format → 应回退透传
        from proxy.request_logger import _generate_request_id
        # 此测试验证不抛 KeyError
        try:
            h._handle_convert(
                "chat_completions", "gpt-4", model_cfg, body,
                _generate_request_id(), "2025-01-01 00:00:00", "gpt-4"
            )
        except Exception:
            self.fail("chat→chat 短路回退透传时不应抛异常")
```

- [ ] **Step 3: 逐个修复现有失败测试**

预期失败的测试及修复策略：

| 测试（预期失败） | 原因 | 修复策略 |
|---------|------|---------|
| 使用 `patch("proxy.handler.urllib.parse.urlparse")` 的测试 | 新 convert 路径不再调 urlparse | 改为 `patch("proxy.upstream_driver.OpenAI")` mock SDK 构造函数 |
| 使用 `patch("proxy.handler._create_upstream_conn")` 的测试 | 新 convert 路径不再调 _create_upstream_conn | 同上——mock SDK 层代替 mock HTTP 层 |
| 使用 `patch("proxy.handler.http.client.HTTPSConnection")` 的测试 | 新 convert 路径用 SDK 而非 raw HTTP | mock `UpstreamDriver.chat_create` / `chat_stream` 返回 fixture 响应 |
| 直接测试 `_forward_non_streaming` / `_forward_streaming` 的测试 | 方法仍存在（未删除），但 _handle_convert 不再调用 | 改为调用 `_handle_convert` 通过 TransformRouter/UpstreamDriver mock |

**Mock 策略示例：**

```python
# 旧 mock 方式（不再有效）：
with patch("proxy.handler._create_upstream_conn") as mock_conn:
    mock_conn.return_value = fake_conn
    handler._handle_convert(...)

# 新 mock 方式：
with patch("proxy.upstream_driver.OpenAI") as mock_openai_cls:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_chat_response
    mock_openai_cls.return_value = mock_client
    handler._handle_convert(...)
```

每个测试修复后运行单个测试确认通过：

```bash
cd /Users/xys/Github/ai-agent-tools-ai-sdk && python3 -m pytest test/test_handler.py::TestName::test_method -v
```

- [ ] **Step 3: 确认所有 handler 测试通过**

```bash
cd /Users/xys/Github/ai-agent-tools-ai-sdk && python3 -m pytest test/test_handler.py -v
```

- [ ] **Step 4: Commit**

```bash
git add test/test_handler.py
git commit -m "test: 修复 handler 测试适配 SDK 驱动"
```

---

### Task 9: 全量测试 + 回归验证 + 手动冒烟

- [ ] **Step 0: 添加 snapshot 回归测试**

在 `test/test_handler.py` 添加：

```python
class TestConvertOutputConsistency(unittest.TestCase):
    """验证新旧路径对相同请求产生一致的转换输出。"""

    @classmethod
    def setUpClass(cls):
        from test.mock_server import start_mock_server
        cls.mock_server, cls.mock_port = start_mock_server()

    @classmethod
    def tearDownClass(cls):
        cls.mock_server.shutdown()

    def _build_handler_sdk(self, upstream_cfg):
        """构建使用新 SDK 路径的 handler。"""
        from proxy.handler import ProxyHandler
        from proxy.common import CONFIG
        CONFIG["upstream"] = upstream_cfg
        class MockServer:
            response_store = None
        h = ProxyHandler.__new__(ProxyHandler)
        h.server = MockServer()
        h.client_address = ("127.0.0.1", 12345)
        h.headers = {}
        h.command = "POST"
        import io
        h.wfile = io.BytesIO()
        return h

    def test_non_streaming_output_key_fields(self):
        """非流式转换输出含 id/model/choices/usage 关键字段。"""
        upstream_cfg = {
            "base_url": f"http://127.0.0.1:{self.mock_port}/v1",
            "api_key": "mock-key",
            "timeout": 30,
            "connect_timeout": 5,
            "ssl_verify": False,
            "retry": 0,
            "format": "chat_completions",
        }
        h = self._build_handler_sdk(upstream_cfg)
        h.path = "/v1/messages"
        body = {
            "model": "claude-sonnet-4",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hello"}],
        }
        model_cfg = {
            "target": "claude-sonnet-4",
            "multimodal": False,
            "upstream": upstream_cfg,
        }
        from proxy.request_logger import _generate_request_id
        h._handle_convert(
            "messages", "claude-sonnet-4", model_cfg, body,
            _generate_request_id(), "2025-01-01 00:00:00", "claude-sonnet-4"
        )
        h.wfile.seek(0)
        raw = h.wfile.read()
        # Anthropic 响应格式应含 id/type/role/content/model/stop_reason/usage
        self.assertIn(b'"id"', raw)
        self.assertIn(b'"type"', raw)
        self.assertIn(b'"role"', raw)
        self.assertIn(b'"content"', raw)
        self.assertIn(b'"model"', raw)
        self.assertIn(b'"stop_reason"', raw)
        self.assertIn(b'"usage"', raw)

    def test_streaming_output_contains_events(self):
        """流式转换输出含 content_block_start / content_block_delta 事件。"""
        upstream_cfg = {
            "base_url": f"http://127.0.0.1:{self.mock_port}/v1",
            "api_key": "mock-key",
            "timeout": 30,
            "connect_timeout": 5,
            "ssl_verify": False,
            "retry": 0,
            "format": "chat_completions",
        }
        h = self._build_handler_sdk(upstream_cfg)
        h.path = "/v1/messages"
        body = {
            "model": "claude-sonnet-4",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        }
        model_cfg = {
            "target": "claude-sonnet-4",
            "multimodal": False,
            "upstream": upstream_cfg,
        }
        # 需要 mock request_logger 以避免 logger 未初始化
        from proxy.request_logger import _generate_request_id, init_logger
        try:
            init_logger()
        except Exception:
            pass
        from proxy.request_logger import get_logger
        if not get_logger():
            from unittest.mock import patch
            with patch("proxy.request_logger.get_logger", return_value=None):
                h._handle_convert(
                    "messages", "claude-sonnet-4", model_cfg, body,
                    _generate_request_id(), "2025-01-01 00:00:00", "claude-sonnet-4"
                )
        else:
            h._handle_convert(
                "messages", "claude-sonnet-4", model_cfg, body,
                _generate_request_id(), "2025-01-01 00:00:00", "claude-sonnet-4"
            )
        h.wfile.seek(0)
        raw = h.wfile.read().decode("utf-8", errors="replace")
        # Anthropic 流式关键事件类型
        self.assertIn("message_start", raw)
        self.assertIn("content_block_start", raw)
        self.assertIn("content_block_delta", raw)
        self.assertIn("message_stop", raw)
```

- [ ] **Step 1: 运行全量测试**

```bash
cd /Users/xys/Github/ai-agent-tools-ai-sdk && python3 -m pytest test/ -q
```

Expected: 531+ tests 全部通过。

- [ ] **Step 2: 修复任何失败测试**

逐个排查：确认是测试需要适配还是实际 bug。修复后重新运行全量测试直到全部通过。

- [ ] **Step 3: 手动冒烟测试**

```bash
# 启动服务
cd /Users/xys/Github/ai-agent-tools-ai-sdk && ./server.sh restart

# 测试 1：非流式转换 — responses → chat_completions
curl -s -X POST http://localhost:48743/v1/responses \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4","input":"hello","stream":false}' | python3 -m json.tool

# 测试 2：流式转换 — responses → chat_completions
curl -s -X POST http://localhost:48743/v1/responses \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4","input":"hello","stream":true}'

# 测试 3：透传 — chat_completions → chat_completions（保持不变）
curl -s -X POST http://localhost:48743/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4","messages":[{"role":"user","content":"hi"}],"stream":true}'
```

验证三个测试都返回正确格式的响应。

- [ ] **Step 4: Commit**（如有修复）

```bash
git add -A && git commit -m "test: 全量测试通过 + 手动冒烟验证"
```

---

### Task 10: 清理旧转换路径代码

**Files:**
- Modify: `proxy/handler.py`

- [ ] **Step 1: 删除旧的 _forward_non_streaming 方法**

删除 `_forward_non_streaming`（约 683-816 行），保留 v2 版本。

- [ ] **Step 2: 删除旧的 _forward_streaming 方法**

删除 `_forward_streaming`（约 818-1071 行），保留 v2 版本。

- [ ] **Step 3: 重命名 v2 方法**

```python
# _forward_non_streaming_v2 → _forward_non_streaming
# _forward_streaming_v2 → _forward_streaming
```

- [ ] **Step 4: 确认测试全部通过**

```bash
cd /Users/xys/Github/ai-agent-tools-ai-sdk && python3 -m pytest test/ -q
```

- [ ] **Step 5: Commit**

```bash
git add proxy/handler.py
git commit -m "refactor: 删除旧转换路径代码，v2 设为默认"
```

---

## 自审检查

- [x] 每个 spec 需求都有对应 task
- [x] 无 TBD/TODO/占位符
- [x] 类型签名一致（Task 2 TransformRouter ↔ Task 3 UpstreamDriver ↔ Task 5 handler）
- [x] 旧代码保留直到新代码验证通过（兼容适配层 + v2 后缀命名）
