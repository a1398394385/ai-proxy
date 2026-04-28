# Response Store + previous_response_id Phase 2 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现内存 Response Store（LRU+TTL 双重淘汰），支持 `previous_response_id` 多轮对话链，让 Codex CLI 的多轮会话能复用历史对话上下文。

**Architecture:** 新建 `response_store.py`（ResponseRecord + ResponseStore）；`proxy_config.yaml` 新增 `response_store` 节；`proxy.py` 的 `main()` 创建 server 后挂载 store 实例；`_handle_responses()` 读 `previous_response_id` 后注入历史消息；非流式路径在 `response_converter()` 后存入 store；流式路径给 `create_codex_sse_stream()` 增加 `request_messages`/`response_store` 参数，在 `finish()` 后存入。

**Tech Stack:** Python 3.10+, `collections.OrderedDict`（LRU）, `dataclasses`, `time`, `pytest`

---

## 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| Create | `response_store.py` | ResponseRecord dataclass + ResponseStore（LRU+TTL） |
| Modify | `proxy_config.yaml` | 新增 response_store 配置节 |
| Modify | `proxy.py` | main() 挂载 store；_handle_responses() 注入 previous_response_id；非流式/流式存储路径 |
| Modify | `transform_responses.py` | create_codex_sse_stream() 新增 request_messages/response_store 参数 |
| Modify | `transform.py` | 添加 ResponseStore/ResponseRecord re-export |
| Create | `test/test_response_store.py` | ResponseStore 单元测试 + 对话链集成测试 |

---

### Task 1：ResponseRecord + ResponseStore 实现

**Files:**
- Create: `response_store.py`
- Create: `test/test_response_store.py`

- [ ] **Step 1: 写失败测试**

```python
# 新建 test/test_response_store.py
import sys, time, unittest
sys.path.insert(0, "/Users/xys/.hermes/fact-store-browser")


class TestResponseRecord(unittest.TestCase):
    def test_fields(self):
        from response_store import ResponseRecord
        now = time.time()
        r = ResponseRecord(
            response_id="resp_1", model="gpt-4o",
            output=[{"type": "message"}],
            conversation=[{"role": "user", "content": "Hi"}],
            usage={"input_tokens": 5, "output_tokens": 3, "total_tokens": 8},
            status="completed",
            created_at=now, expires_at=now + 3600,
        )
        self.assertEqual(r.response_id, "resp_1")
        self.assertEqual(r.model, "gpt-4o")
        self.assertEqual(r.status, "completed")


class TestResponseStore(unittest.TestCase):
    def _make_record(self, resp_id="r1", ttl=3600):
        from response_store import ResponseRecord
        now = time.time()
        return ResponseRecord(
            response_id=resp_id, model="test",
            output=[{"type": "message", "content": [{"type": "output_text", "text": "Hello"}]}],
            conversation=[
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello"},
            ],
            usage={"input_tokens": 5, "output_tokens": 3, "total_tokens": 8},
            status="completed",
            created_at=time.time(), expires_at=time.time() + ttl,
        )

    def test_put_and_get(self):
        from response_store import ResponseStore
        store = ResponseStore()
        store.put("resp_1", self._make_record("resp_1"))
        result = store.get("resp_1")
        self.assertIsNotNone(result)
        self.assertEqual(result.response_id, "resp_1")

    def test_get_missing_returns_none(self):
        from response_store import ResponseStore
        self.assertIsNone(ResponseStore().get("nonexistent"))

    def test_ttl_expiry(self):
        from response_store import ResponseStore, ResponseRecord
        store = ResponseStore()
        now = time.time()
        expired = ResponseRecord("r_exp", "t", [], [], {}, "c", now, now - 1)
        store._store["r_exp"] = expired   # bypass put() 直接注入已过期条目
        self.assertIsNone(store.get("r_exp"), "TTL 已过期应返回 None")

    def test_lru_eviction(self):
        from response_store import ResponseStore
        store = ResponseStore(max_entries=2)
        store.put("r1", self._make_record("r1"))
        store.put("r2", self._make_record("r2"))
        store.get("r1")                           # 标记 r1 为最近使用
        store.put("r3", self._make_record("r3"))  # 超出 max，淘汰最旧的 r2
        self.assertIsNotNone(store.get("r1"), "r1 应保留（最近访问）")
        self.assertIsNone(store.get("r2"),    "r2 应被淘汰（LRU）")
        self.assertIsNotNone(store.get("r3"), "r3 应保留（新加入）")

    def test_get_updates_lru_order(self):
        """get() 将条目移到最近端，防止连续 put 时被误淘汰。"""
        from response_store import ResponseStore
        store = ResponseStore(max_entries=3)
        store.put("r1", self._make_record("r1"))
        store.put("r2", self._make_record("r2"))
        store.put("r3", self._make_record("r3"))
        store.get("r1")                           # r1 刷新为最近使用
        store.put("r4", self._make_record("r4"))  # 淘汰最旧的 r2
        self.assertIsNotNone(store.get("r1"))
        self.assertIsNone(store.get("r2"))

    def test_get_conversation(self):
        from response_store import ResponseStore
        store = ResponseStore()
        store.put("r1", self._make_record("r1"))
        conv = store.get_conversation("r1")
        self.assertEqual(len(conv), 2)
        self.assertEqual(conv[0]["role"], "user")

    def test_get_conversation_missing_returns_empty(self):
        from response_store import ResponseStore
        self.assertEqual(ResponseStore().get_conversation("nonexistent"), [])

    def test_expired_evicted_on_put(self):
        """put() 先清理已过期条目，避免 max_entries 被占满后再淘汰有效条目。"""
        from response_store import ResponseStore, ResponseRecord
        store = ResponseStore(max_entries=2)
        now = time.time()
        r_exp = ResponseRecord("r_exp", "t", [], [], {}, "c", now, now - 1)
        store._store["r_exp"] = r_exp           # 注入过期条目（绕过 put）
        store.put("r2", self._make_record("r2"))
        # 此时 _store 有 2 个条目（含过期），put r3 时先 evict r_exp
        store.put("r3", self._make_record("r3"))
        self.assertIsNone(store.get("r_exp"))
        self.assertIsNotNone(store.get("r2"))
        self.assertIsNotNone(store.get("r3"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_response_store.py -v
```
期望：`ModuleNotFoundError: No module named 'response_store'`

- [ ] **Step 3: 创建 `response_store.py`**

```python
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional


@dataclass
class ResponseRecord:
    response_id: str
    model: str
    output: list           # Responses API output items（返回给客户端）
    conversation: list     # Chat Messages 格式（previous_response_id 重建历史用）
    usage: dict
    status: str
    created_at: float
    expires_at: float      # TTL 过期时间戳


class ResponseStore:
    """内存 response store，LRU + TTL 双重淘汰。

    使用 OrderedDict 实现 LRU：
    - move_to_end(key) O(1) 将访问项移到尾部（最新）
    - popitem(last=False) O(1) 淘汰头部（最旧）
    """

    def __init__(self, max_entries: int = 1000, ttl_seconds: int = 3600):
        self._store: OrderedDict = OrderedDict()
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()     # ThreadedHTTPServer 多线程并发保护

    def put(self, response_id: str, record: ResponseRecord):
        """存储 response；先淘汰过期条目，再按 LRU 淘汰超量条目。"""
        with self._lock:
            self._evict_expired()
            if response_id in self._store:
                del self._store[response_id]
            elif len(self._store) >= self._max_entries:
                self._store.popitem(last=False)    # 淘汰最旧（LRU head）
            self._store[response_id] = record

    def get(self, response_id: str) -> Optional[ResponseRecord]:
        """获取 response；不存在或已过期返回 None；命中则更新 LRU 顺序。"""
        with self._lock:
            if response_id not in self._store:
                return None
            record = self._store[response_id]
            if time.time() > record.expires_at:
                del self._store[response_id]
                return None
            self._store.move_to_end(response_id)   # 移到尾部（最近使用）
            return record

    @property
    def ttl_seconds(self) -> int:
        """公共只读属性，供 store 外调用方获取 TTL 配置。"""
        return self._ttl_seconds

    def get_conversation(self, response_id: str) -> list:
        """获取对应的 Chat Messages 历史（直接从 record.conversation 读取）。"""
        record = self.get(response_id)
        return record.conversation if record else []

    def _evict_expired(self):
        now = time.time()
        expired = [k for k, v in self._store.items() if now > v.expires_at]
        for k in expired:
            del self._store[k]
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_response_store.py -v
```
期望：`9 passed`

- [ ] **Step 5: Commit**

```bash
cd /Users/xys/.hermes/fact-store-browser && git add response_store.py test/test_response_store.py && git commit -m "feat: 新增 ResponseStore（LRU+TTL 内存存储）和 ResponseRecord dataclass"
```

---

### Task 2：proxy_config.yaml + server 挂载

**Files:**
- Modify: `proxy_config.yaml`
- Modify: `proxy.py`（`main()` 函数，line 742 之后）
- Modify: `transform.py`

- [ ] **Step 1: 写失败测试（源码检查）**

```python
# 追加到 test/test_response_store.py

class TestResponseStoreServerMount(unittest.TestCase):
    def test_main_mounts_response_store(self):
        """proxy.py main() 应在创建 server 后挂载 server.response_store。"""
        import pathlib
        src = pathlib.Path("/Users/xys/.hermes/fact-store-browser/proxy.py").read_text()
        self.assertIn("server.response_store", src,
                      "main() 应将 ResponseStore 挂载到 server.response_store")
        self.assertIn("ResponseStore", src,
                      "proxy.py 应导入并使用 ResponseStore")

    def test_proxy_config_has_response_store_section(self):
        import pathlib
        src = pathlib.Path("/Users/xys/.hermes/fact-store-browser/proxy_config.yaml").read_text()
        self.assertIn("response_store", src,
                      "proxy_config.yaml 应包含 response_store 配置节")
        self.assertIn("max_entries", src)
        self.assertIn("ttl_seconds", src)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_response_store.py::TestResponseStoreServerMount -v
```
期望：`2 failed`

- [ ] **Step 3: 在 `proxy_config.yaml` 末尾追加 response_store 节**

```yaml
response_store:
  max_entries: 1000
  ttl_seconds: 3600
```

- [ ] **Step 4: 在 `proxy.py` 的 `main()` 中挂载 store**

在 `server = ThreadedHTTPServer((host, port), ProxyHandler)` 行（第 742 行）之后插入：

```python
    from response_store import ResponseStore as _ResponseStore
    _store_cfg = CONFIG.get("response_store", {})
    server.response_store = _ResponseStore(
        max_entries=_store_cfg.get("max_entries", 1000),
        ttl_seconds=_store_cfg.get("ttl_seconds", 3600),
    )
```

- [ ] **Step 5: 在 `transform.py` 末尾追加 re-export**

```python
from response_store import ResponseStore, ResponseRecord  # noqa: F401 — re-export
```

同时确认 `_output_items_to_messages` 已在 `transform_responses` 导入块中（Phase 1 Task 13 已实现并 re-export，Phase 2/3 直接导入使用，不需要二次实现）。

- [ ] **Step 6: 运行测试确认通过**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_response_store.py::TestResponseStoreServerMount -v
```
期望：`2 passed`

- [ ] **Step 7: Commit**

```bash
cd /Users/xys/.hermes/fact-store-browser && git add proxy_config.yaml proxy.py transform.py && git commit -m "feat: proxy.py main() 挂载 ResponseStore，proxy_config.yaml 新增 response_store 节"
```

---

### Task 3：previous_response_id 注入

**Files:**
- Modify: `proxy.py`（`_handle_responses()`，在 `responses_to_chat()` 之后，约第 321 行）
- Modify: `test/test_response_store.py`

- [ ] **Step 1: 写失败测试（源码检查）**

```python
# 追加到 test/test_response_store.py

class TestPreviousResponseIdInjection(unittest.TestCase):
    def _get_handle_responses_body(self):
        import pathlib
        src = pathlib.Path("/Users/xys/.hermes/fact-store-browser/proxy.py").read_text()
        start = src.index("def _handle_responses(")
        # 取到下一个 def（_handle_messages 之前）
        end = src.index("\n    def _handle_messages(", start)
        return src[start:end]

    def test_reads_previous_response_id(self):
        body = self._get_handle_responses_body()
        self.assertIn("previous_response_id", body,
                      "_handle_responses() 应读取 previous_response_id")
        self.assertIn("response_store.get(", body,
                      "_handle_responses() 应调用 response_store.get() 读取历史")

    def test_system_msg_stays_first(self):
        """注入历史时 system 消息必须保持在首位（不被历史 messages 插入其前）。"""
        body = self._get_handle_responses_body()
        self.assertIn("system_msgs", body,
                      "proxy.py 应将 system 消息和历史消息分开处理，确保 system 在首位")
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_response_store.py::TestPreviousResponseIdInjection -v
```
期望：`2 failed`

- [ ] **Step 3: 在 `_handle_responses()` 中插入注入逻辑**

在 `_handle_responses()` 的 `chat_body = responses_to_chat(body, model_cfg)` 之后（约第 321 行），插入：

```python
        # previous_response_id：从 store 读取历史 conversation 并注入到本轮 messages
        prev_id = body.get("previous_response_id")
        if prev_id:
            response_store = getattr(self.server, "response_store", None)
            if response_store is not None:
                record = response_store.get(prev_id)
                if record:
                    # system 消息始终保持首位，历史插入 system 与 user 之间
                    system_msgs = [m for m in chat_body["messages"] if m.get("role") == "system"]
                    non_system_msgs = [m for m in chat_body["messages"] if m.get("role") != "system"]
                    chat_body["messages"] = system_msgs + record.conversation + non_system_msgs
                else:
                    logging.warning(f"previous_response_id={prev_id!r} 不存在或已过期，忽略历史")
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_response_store.py::TestPreviousResponseIdInjection -v
```
期望：`2 passed`

- [ ] **Step 5: Commit**

```bash
cd /Users/xys/.hermes/fact-store-browser && git add proxy.py test/test_response_store.py && git commit -m "feat: _handle_responses() 支持 previous_response_id，注入历史 conversation 到 messages"
```

---

### Task 4：非流式存储路径

**Files:**
- Modify: `proxy.py`（`_handle_responses()` 传参 + `_forward_non_streaming()` 存储逻辑）
- Modify: `test/test_response_store.py`

- [ ] **Step 1: 写失败测试（源码检查）**

```python
# 追加到 test/test_response_store.py

class TestNonStreamingStorePath(unittest.TestCase):
    def _get_non_streaming_body(self):
        import pathlib
        src = pathlib.Path("/Users/xys/.hermes/fact-store-browser/proxy.py").read_text()
        start = src.index("def _forward_non_streaming(")
        end = src.index("\n    def _forward_streaming(", start)
        return src[start:end]

    def test_stores_response_after_conversion(self):
        body = self._get_non_streaming_body()
        self.assertIn("_store_response(", body,
                      "_forward_non_streaming 应调用 _store_response 存储")

    def test_store_response_helper_exists(self):
        import pathlib
        src = pathlib.Path("/Users/xys/.hermes/fact-store-browser/proxy.py").read_text()
        self.assertIn("def _store_response(", src,
                      "proxy.py 应有 _store_response 辅助函数")
        self.assertIn("_output_items_to_messages", src)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_response_store.py::TestNonStreamingStorePath -v
```
期望：`2 failed`

- [ ] **Step 3: 在 `_handle_responses()` 传递 `store_enabled` 参数**

将 `_handle_responses()` 末尾的 `_forward_streaming` / `_forward_non_streaming` 调用（约第 334-337 行）改为：

```python
        store_enabled = body.get("store", True)
        if is_stream:
            self._forward_streaming(chat_body, model_cfg, request_id, model_name, target, request_ts,
                                    store_enabled=store_enabled)
        else:
            self._forward_non_streaming(chat_body, request_id, model_name, target, request_ts,
                                        store_enabled=store_enabled)
```

（`_handle_messages()` 的调用保持不变，Anthropic 路径不需要 response store。）

- [ ] **Step 4: 在 `proxy.py` 中添加 `_store_response` 辅助函数**

在 `ProxyHandler` 类定义之前（或文件末尾 `main()` 之前）添加：

```python
def _store_response(server, responses_response: dict, messages_for_conv: list):
    """将 responses_response 存入 server.response_store（如已挂载）。

    messages_for_conv: 已包含完整对话历史的消息列表（调用方负责构建，含 assistant 输出；
                       不包含 system，避免多轮时重复叠加）。
    """
    # 懒导入 response_store 类（避免 proxy.py 模块级导入时的循环依赖，proxy.py
    # 导入 transform，transform 导入 response_store，response_store 不导入 proxy.py）
    response_store = getattr(server, "response_store", None)
    if response_store is None:
        return
    from response_store import ResponseRecord as _RR
    output = responses_response.get("output", [])
    record = _RR(
        response_id=responses_response.get("id", ""),
        model=responses_response.get("model", ""),
        output=output,
        conversation=messages_for_conv,
        usage=responses_response.get("usage", {}),
        status=responses_response.get("status", "completed"),
        created_at=time.time(),
        expires_at=time.time() + response_store.ttl_seconds,
    )
    response_store.put(record.response_id, record)
```

- [ ] **Step 5: 更新 `_forward_non_streaming()` 签名和存储逻辑**

将函数签名改为：

```python
    def _forward_non_streaming(self, chat_body: dict, request_id: str, model: str, target: str, request_ts: str, response_converter=None, store_enabled: bool = True, is_responses_api: bool = False):
```

在 `self._send_json(200, responses_response)` 之前（约第 478 行）插入存储逻辑：

```python
                # 存储 response（仅当 store_enabled=True 且 is_responses_api=True 时）
                # 使用 is_responses_api 显式标记（而非根据 response_converter 类型推断），
                # 防止 _handle_messages（Anthropic 路径）不传参数时误触发存储
                if store_enabled and is_responses_api:
                    from transform_responses import _output_items_to_messages as _oitm
                    assistant_msgs = _oitm(responses_response.get("output", []))
                    messages_for_conv = [
                        m for m in chat_body.get("messages", []) if m.get("role") != "system"
                    ] + assistant_msgs
                    _store_response(self.server, responses_response, messages_for_conv)
```

同时更新 `_handle_responses()` 中的调用处：

```python
            self._forward_non_streaming(chat_body, request_id, model_name, target, request_ts,
                                        store_enabled=store_enabled, is_responses_api=True)
```

（`_handle_messages()` 的调用保持不变，`is_responses_api` 默认为 `False`，不会误触发存储。）

- [ ] **Step 6: 运行测试确认通过**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_response_store.py::TestNonStreamingStorePath -v
```
期望：`2 passed`

- [ ] **Step 7: Commit**

```bash
cd /Users/xys/.hermes/fact-store-browser && git add proxy.py test/test_response_store.py && git commit -m "feat: 非流式路径在 response_converter() 后将 response 存入 ResponseStore"
```

---

### Task 5：流式存储路径

**Files:**
- Modify: `transform_responses.py`（`create_codex_sse_stream()` 签名和存储逻辑）
- Modify: `proxy.py`（`_forward_streaming()` 签名和工厂调用）
- Modify: `test/test_response_store.py`

- [ ] **Step 1: 写失败测试**

```python
# 追加到 test/test_response_store.py

class TestStreamingStorePath(unittest.TestCase):
    @staticmethod
    def _make_mock_stream(chunks):
        class MockStream:
            def __init__(self):
                self.data = b"".join(chunks)
                self.pos = 0
            def read(self, size):
                chunk = self.data[self.pos:self.pos + size]
                self.pos += size
                return chunk
        return MockStream()

    def test_streaming_stores_record_in_store(self):
        """耗尽 create_codex_sse_stream 生成器后，store 应有对应的 record。"""
        import json
        from transform_responses import create_codex_sse_stream
        from response_store import ResponseStore

        store = ResponseStore()
        chunks = [
            b'data: {"id":"c1","model":"test","choices":[{"delta":{"content":"Hi"},"index":0}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}\n\n',
            b'data: [DONE]\n\n',
        ]
        request_messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]

        events_text = "".join(create_codex_sse_stream(
            self._make_mock_stream(chunks),
            request_messages=request_messages,
            response_store=store,
        ))

        self.assertEqual(len(store._store), 1, "store 应有 1 条 record")
        record = list(store._store.values())[0]
        self.assertEqual(record.status, "completed")
        # usage 格式验证：应是 input_tokens/output_tokens（Responses API 格式），
        # 而非 raw prompt_tokens/completion_tokens（Chat Completions 格式）
        self.assertIn("input_tokens", record.usage)
        self.assertIn("output_tokens", record.usage)
        self.assertNotIn("prompt_tokens", record.usage)
        self.assertNotIn("completion_tokens", record.usage)
        # conversation 不含 system，但含 user 和 assistant
        roles = [m["role"] for m in record.conversation]
        self.assertNotIn("system", roles, "conversation 不应含 system 消息")
        self.assertIn("user", roles)
        self.assertIn("assistant", roles)

    def test_streaming_no_store_when_none(self):
        """response_store=None 时不报错，正常流式输出。"""
        from transform_responses import create_codex_sse_stream
        chunks = [
            b'data: {"id":"c1","model":"test","choices":[{"delta":{"content":"Hi"},"index":0}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}\n\n',
            b'data: [DONE]\n\n',
        ]
        result = list(create_codex_sse_stream(self._make_mock_stream(chunks)))
        self.assertTrue(any("response.completed" in e for e in result))

    def test_forward_streaming_passes_store_to_factory(self):
        """_forward_streaming 应传入 request_messages 和 response_store 参数。"""
        import pathlib
        src = pathlib.Path("/Users/xys/.hermes/fact-store-browser/proxy.py").read_text()
        start = src.index("def _forward_streaming(")
        end = src.index("\n    def _send_json(", start)
        func_body = src[start:end]
        self.assertIn("request_messages", func_body,
                      "_forward_streaming 应向 sse_stream_factory 传 request_messages")
        self.assertIn("response_store", func_body,
                      "_forward_streaming 应向 sse_stream_factory 传 response_store")
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_response_store.py::TestStreamingStorePath -v
```
期望：`test_streaming_stores_record_in_store` 和 `test_forward_streaming_passes_store_to_factory` 失败

- [ ] **Step 3: 更新 `create_codex_sse_stream()` 签名（`transform_responses.py`）**

将 `create_codex_sse_stream` 函数（Phase 1 Task 9 已重写）整体替换为：

```python
def create_codex_sse_stream(upstream_response, request_messages: list = None, response_store=None):
    """读取上游 SSE 流，逐事件 yield Responses API 格式的 SSE 字符串。

    request_messages: chat_body["messages"]，用于构建完整 conversation（Phase 2 新增）
    response_store: ResponseStore 实例；非 None 时在 finish() 后存储 response（Phase 2 新增）
    """
    converter = CodexStreamConverter()
    converter.response_id = generate_response_id()

    for event in iter_sse_events(upstream_response):
        if event["event"] == "[DONE]":
            break
        data = event.get("data")
        if not data:
            continue
        for sse_str in converter.process_chunk(data):
            yield sse_str

    for sse_str in converter.finish():
        yield sse_str

    # finish() 返回后 output_items 已按 output_index 排序为 (index, item) 元组
    if response_store is not None:
        from response_store import ResponseRecord
        output_list = [item for _, item in converter.output_items]
        assistant_msgs = _output_items_to_messages(output_list)
        conversation = [
            m for m in (request_messages or []) if m.get("role") != "system"
        ] + assistant_msgs
        # final_usage=None 表示从未收到 usage chunk（设计文稿 §3.2 语义），此时不调用 _convert_usage
        # 而是传 None 给 _build_response_obj 让其 fallback 到全零 usage。
        # 注意：不能写 if converter.final_usage，因为空 dict {} 也是 falsy
        usage = converter._convert_usage(converter.final_usage) if converter.final_usage is not None else None
        status = "incomplete" if converter.finish_reason in ("length", "content_filter") else "completed"
        record = ResponseRecord(
            response_id=converter.response_id,
            model=converter.model,
            output=output_list,
            conversation=conversation,
            usage=usage,
            status=status,
            created_at=time.time(),
            expires_at=time.time() + response_store.ttl_seconds,
        )
        response_store.put(record.response_id, record)
```

- [ ] **Step 4: 更新 `_forward_streaming()` 签名和工厂调用（`proxy.py`）**

将 `_forward_streaming()` 函数签名改为：

```python
    def _forward_streaming(self, chat_body: dict, model_cfg: dict, request_id: str, model: str, target: str, request_ts: str, response_converter=None, sse_stream_factory=None, store_enabled: bool = True):
```

将核心流式循环（约第 619 行）：

```python
            # 核心：通过 sse_stream_factory 逐事件转换并发送
            for sse_event in sse_stream_factory(resp):
```

改为：

```python
            # 核心：通过 sse_stream_factory 逐事件转换并发送
            # _rstore 非 None 时传入流式存储所需的额外参数（Phase 2），
            # create_codex_sse_stream 内部通过 response_store is not None 判断是否存储
            _rstore = getattr(self.server, "response_store", None) if store_enabled else None
            stream_gen = sse_stream_factory(
                resp,
                request_messages=chat_body.get("messages") if _rstore else None,
                response_store=_rstore,
            )
            for sse_event in stream_gen:
```

（注意：将原 `for sse_event in sse_stream_factory(resp):` 之后的循环体 `self.wfile.write(...)` 等改为 `for sse_event in stream_gen:` 之后的循环体，保持不变。）

- [ ] **Step 5: 运行测试确认通过**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_response_store.py::TestStreamingStorePath -v
```
期望：`3 passed`

- [ ] **Step 6: Commit**

```bash
cd /Users/xys/.hermes/fact-store-browser && git add transform_responses.py proxy.py test/test_response_store.py && git commit -m "feat: 流式路径 finish() 后存入 ResponseStore；create_codex_sse_stream 新增 request_messages/response_store 参数"
```

---

### Task 6：对话链集成测试 + 全量验收

**Files:**
- Modify: `test/test_response_store.py`

- [ ] **Step 1: 追加对话链集成测试**

```python
# 追加到 test/test_response_store.py

class TestConversationChain(unittest.TestCase):
    """验证 previous_response_id 多轮对话链核心逻辑（不启动真实代理）。"""

    @staticmethod
    def _make_mock_stream(chunks):
        class MockStream:
            def __init__(self):
                self.data = b"".join(chunks)
                self.pos = 0
            def read(self, size):
                chunk = self.data[self.pos:self.pos + size]
                self.pos += size
                return chunk
        return MockStream()

    def test_round1_stores_and_round2_can_inject(self):
        """
        轮次1: user→"Hi", assistant→"Hello" → 存入 store
        轮次2: 从 store 取 conversation → 注入到新 messages → system 在首位、历史在中间、新 user 在末尾
        """
        import json
        from transform_responses import create_codex_sse_stream
        from response_store import ResponseStore

        store = ResponseStore()

        # 轮次 1
        round1_chunks = [
            b'data: {"id":"c1","model":"test","choices":[{"delta":{"content":"Hello"},"index":0}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":2,"completion_tokens":1,"total_tokens":3}}\n\n',
            b'data: [DONE]\n\n',
        ]
        round1_messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
        ]
        events1 = list(create_codex_sse_stream(
            self._make_mock_stream(round1_chunks),
            request_messages=round1_messages,
            response_store=store,
        ))

        # 从流事件提取 response_id
        resp_id = None
        for e in events1:
            for line in e.split("\n"):
                if line.startswith("data: "):
                    try:
                        data = json.loads(line[6:])
                        if data.get("type") == "response.created":
                            resp_id = data["response"]["id"]
                    except json.JSONDecodeError:
                        pass
        self.assertIsNotNone(resp_id, "轮次 1 应生成 response_id")

        # 验证 store 中有该 record
        record = store.get(resp_id)
        self.assertIsNotNone(record)
        self.assertEqual(record.status, "completed")

        # conversation 不含 system，含 user + assistant
        conv_roles = [m["role"] for m in record.conversation]
        self.assertNotIn("system", conv_roles, "conversation 不应含 system 消息")
        self.assertIn("user", conv_roles)
        self.assertIn("assistant", conv_roles)

        # 轮次 2：模拟 _handle_responses() 中的注入逻辑
        previous_conv = store.get_conversation(resp_id)
        round2_messages = [
            {"role": "system", "content": "You are helpful."},   # 新 system
            {"role": "user", "content": "What?"},
        ]
        system_msgs = [m for m in round2_messages if m.get("role") == "system"]
        non_system_msgs = [m for m in round2_messages if m.get("role") != "system"]
        injected = system_msgs + previous_conv + non_system_msgs

        # 顺序验证
        self.assertEqual(injected[0]["role"], "system", "system 消息必须在首位")
        self.assertEqual(injected[-1]["content"], "What?", "新 user 消息应在末尾")
        # 历史 "Hi" 在中间
        all_contents = [m.get("content") for m in injected]
        self.assertIn("Hi", all_contents, "历史 user 消息应在中间")
        # 消息角色顺序验证：system → user(历史) → assistant(历史) → user(新)
        roles = [m["role"] for m in injected]
        self.assertEqual(roles, ["system", "user", "assistant", "user"],
                         f"消息顺序错误，实际: {roles}")
        # assistant 消息不重复
        assistant_count = sum(1 for r in roles if r == "assistant")
        self.assertEqual(assistant_count, 1, "conversation 中 assistant 消息不应重复")

    def test_pure_refusal_non_streaming_store_uses_empty_string(self):
        """非流式路径：chat_to_responses 纯拒绝输出存入 store 后，content 为空字符串。"""
        from transform import chat_to_responses, _output_items_to_messages
        from response_store import ResponseStore, ResponseRecord

        store = ResponseStore()
        chat_resp = {
            "id": "chatcmpl-refonly",
            "model": "test",
            "choices": [{
                "message": {"content": None, "refusal": "I cannot help with that."},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        }
        responses_resp = chat_to_responses(chat_resp)
        output = responses_resp.get("output", [])
        assistant_msgs = _output_items_to_messages(output)

        import time
        messages_for_conv = [{"role": "user", "content": "Bad request"}] + assistant_msgs
        record = ResponseRecord(
            response_id=responses_resp.get("id", "ref_test"),
            model="test", output=output, conversation=messages_for_conv,
            usage={}, status="completed", created_at=time.time(),
            expires_at=time.time() + 3600,
        )
        store.put(record.response_id, record)

        conv = store.get_conversation(record.response_id)
        assistant_convs = [m for m in conv if m["role"] == "assistant"]
        self.assertEqual(len(assistant_convs), 1)
        self.assertIsNotNone(assistant_convs[0]["content"])
        self.assertEqual(assistant_convs[0]["content"], "")

    def test_pure_refusal_streaming_conversation_uses_empty_string(self):
        """流式路径：纯拒绝响应存入 store 后，conversation 的 assistant content 为空字符串（不是 None）。"""
        from transform_responses import create_codex_sse_stream
        from response_store import ResponseStore

        store = ResponseStore()
        refusal_chunks = [
            b'data: {"id":"c1","model":"test","choices":[{"delta":{"refusal":"I cannot help"},"index":0}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":2,"total_tokens":3}}\n\n',
            b'data: [DONE]\n\n',
        ]
        list(create_codex_sse_stream(
            self._make_mock_stream(refusal_chunks),
            request_messages=[{"role": "user", "content": "Do bad thing"}],
            response_store=store,
        ))

        record = list(store._store.values())[0]
        assistant_msgs = [m for m in record.conversation if m["role"] == "assistant"]
        self.assertEqual(len(assistant_msgs), 1)
        self.assertIsNotNone(assistant_msgs[0]["content"],
                             "assistant content 不能为 None（上游会报 400）")
        self.assertEqual(assistant_msgs[0]["content"], "",
                         "纯拒绝时 content 应为空字符串")
```

- [ ] **Step 2: 运行全量测试确认全部通过**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/ -v --tb=short
```
期望：全部通过（包含原有 Phase 1 测试 + Phase 2 新增测试）

- [ ] **Step 3: 最终 Commit**

```bash
cd /Users/xys/.hermes/fact-store-browser && git add test/test_response_store.py && git commit -m "test: Phase 2 对话链集成测试——previous_response_id 注入、纯拒绝 content 保护"
```

---

*Phase 3（MCP 支持）见独立计划文件 `docs/superpowers/plans/2026-04-28-mcp-support-phase3.md`。*
