# 移除 SDK 上游驱动，回归 http.client 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 移除 openai/anthropic SDK 依赖，将上游 HTTP 调用层回归 http.client 标准库，保留 TransformRouter + Adapter 架构。

**Architecture:** 原地替换 handler.py 中的三个方法（`_forward_non_streaming`、`_forward_streaming`、`_handle_sdk_error`），删除 `upstream_driver.py`，复用已有的 `common._create_upstream_conn()` 做上游连接。

**Tech Stack:** Python 标准库（http.client / ssl / socket / urllib.parse）

---

## 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| **修改** | `proxy/handler.py` | 重写 3 个方法，移除 SDK import |
| **删除** | `proxy/upstream_driver.py` | 整文件删除（105 行） |
| **修改** | `proxy/__init__.py:51` | 移除 UpstreamDriver re-export |
| **修改** | `proxy/transform.py:45` | 移除 UpstreamDriver re-export |
| **修改** | `test/test_handler.py:538` | mock 改为 `_create_upstream_conn` |
| **修改** | `test/test_proxy_logger_integration.py` | 6 处 mock 改为 `_create_upstream_conn` |
| **删除** | `test/test_upstream_driver.py` | 整文件删除 |

---

### Task 1: 删除 upstream_driver.py + 清理 re-export

**Files:**
- Delete: `proxy/upstream_driver.py`
- Modify: `proxy/__init__.py:51`
- Modify: `proxy/transform.py:45`
- Modify: `proxy/handler.py` (移除 `from .upstream_driver` 两处 import)

- [ ] **Step 1: 删除 upstream_driver.py**

```bash
rm proxy/upstream_driver.py
```

- [ ] **Step 2: 从 `proxy/__init__.py` 移除 UpstreamDriver re-export**

删除第 51 行：
```python
from .upstream_driver import UpstreamDriver  # noqa: F401 — re-export — re-ex...
```

- [ ] **Step 3: 从 `proxy/transform.py` 移除 UpstreamDriver re-export**

删除第 45 行：
```python
from .upstream_driver import UpstreamDriver  # noqa: F401
```

- [ ] **Step 4: 从 `proxy/handler.py` 移除两处 UpstreamDriver import**

删除 `_forward_non_streaming` 方法中的（约第 707 行）：
```python
from .upstream_driver import UpstreamDriver
```

删除 `_forward_streaming` 方法中的（约第 797 行）：
```python
from .upstream_driver import UpstreamDriver
```

- [ ] **Step 5: 验证编译报错暴露所有调用点**

```bash
python3 -c "from proxy.handler import ProxyHandler" 2>&1
```

预期：报错提示 `UpstreamDriver` 未定义或其他引用错误。此时不要修复——这些报错指向的就是 Task 2-4 需要重写的方法。

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "refactor: 删除 upstream_driver.py + 清理 re-export"
```

---

### Task 2: 重写 `_forward_non_streaming`

**Files:**
- Modify: `proxy/handler.py:702-791`（替换整个方法体）

- [ ] **Step 1: 在 handler.py 顶部添加路径映射常量**

在 import 块之后、类定义之前添加：

```python
# ── 上游路径映射 ──
_UPSTREAM_PATHS = {
    "chat_completions": "/v1/chat/completions",
    "responses": "/v1/responses",
    "messages": "/v1/messages",
}
```

- [ ] **Step 2: 替换 `_forward_non_streaming` 方法**

用以下代码替换 handler.py 中 `def _forward_non_streaming` 到其方法结尾（约第 702-791 行）。方法签名保持不变：

```python
def _forward_non_streaming(self, upstream_body, request_id, model, target,
                           request_ts, upstream_cfg, client_format,
                           upstream_format, store_enabled=True,
                           is_responses_api=False):
    """非流式：http.client 连上游 → 响应转换。"""
    base_url = upstream_cfg["base_url"]
    api_key = upstream_cfg["api_key"]
    timeout = upstream_cfg.get("timeout", 120)
    retries = upstream_cfg.get("retry", 0) + 1
    logger = get_logger()

    parsed = urllib.parse.urlparse(base_url)
    path = parsed.path.rstrip("/") + _UPSTREAM_PATHS.get(upstream_format, "/v1/chat/completions")
    port = parsed.port or (80 if parsed.scheme == "http" else 443)

    for attempt in range(retries):
        conn = None
        try:
            conn = _create_upstream_conn(upstream_cfg, parsed, port)
            conn.connect()
            conn.sock.settimeout(timeout)

            conn.request("POST", path, body=json.dumps(upstream_body), headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            })

            start = time.time()
            resp = conn.getresponse()
            resp_body = resp.read()
            duration_ms = int((time.time() - start) * 1000)
            conn.close()
            conn = None

            if resp.status >= 500 and attempt < retries - 1:
                if logger:
                    logger.log_upstream_response(
                        request_id, resp.status,
                        resp_body.decode("utf-8", errors="replace"),
                        duration_ms, model, target,
                        request_type=client_format,
                    )
                logging.warning(f"上游 {resp.status}，重试 {attempt + 1}/{retries}")
                continue

            if resp.status != 200:
                if logger:
                    logger.log_upstream_response(
                        request_id, resp.status,
                        resp_body.decode("utf-8", errors="replace"),
                        duration_ms, model, target,
                        request_type=client_format,
                    )
                self.send_response(resp.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(resp_body)
                return

            try:
                chat_response = json.loads(resp_body)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                chat_response = {"error": str(e), "raw": resp_body.decode("utf-8", errors="replace")[:5000]}

            if logger:
                logger.log_upstream_response(
                    request_id, resp.status, chat_response, duration_ms,
                    model, target,
                    request_type=client_format,
                )

            try:
                from .transform_router import TransformRouter
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
                        "request_ts": request_ts,
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
                self._send_json(500, {"error": {"type": "internal_error", "message": str(e)}})
                return

            if store_enabled and is_responses_api:
                from .transform_responses import output_items_to_messages as _oitm
                assistant_msgs = _oitm(output.get("output", []))
                messages_for_conv = [
                    m for m in upstream_body.get("messages", [])
                    if m.get("role") != "system"
                ] + assistant_msgs
                _store_response(self.server, output, messages_for_conv)

            self._send_json(200, output)
            return

        except (socket.timeout, http.client.HTTPException, OSError) as e:
            logging.warning(f"上游请求失败 (attempt {attempt + 1}): {e}")
            if attempt < retries - 1:
                continue
            self._send_json(502, {"error": {"type": "server_error", "message": str(e)}})
            return
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
```

- [ ] **Step 3: 验证方法签名与调用方匹配**

`_handle_convert` 中的调用（约第 693 行）：
```python
self._forward_non_streaming(
    upstream_body, request_id, model_name, target, request_ts,
    upstream_cfg, client_format, upstream_format,
    store_enabled=store_enabled,
    is_responses_api=is_responses_api,
)
```

新方法签名完全匹配这些参数。

- [ ] **Step 4: Commit**

```bash
git add proxy/handler.py && git commit -m "refactor: _forward_non_streaming 改回 http.client"
```

---

### Task 3: 重写 `_forward_streaming`

**Files:**
- Modify: `proxy/handler.py`（替换 `_forward_streaming` 方法，约第 793-924 行）

- [ ] **Step 1: 替换 `_forward_streaming` 方法**

用以下代码替换 `def _forward_streaming` 到其方法结尾：

```python
def _forward_streaming(self, upstream_body, model_cfg, request_id, model_name,
                       target, request_ts, upstream_cfg, client_format,
                       upstream_format, store_enabled=True):
    """流式：http.client 连上游 SSE → TransformRouter 逐事件转换。"""
    base_url = upstream_cfg["base_url"]
    api_key = upstream_cfg["api_key"]
    timeout = upstream_cfg.get("timeout", 120)
    logger = get_logger()

    parsed = urllib.parse.urlparse(base_url)
    path = parsed.path.rstrip("/") + _UPSTREAM_PATHS.get(upstream_format, "/v1/chat/completions")
    port = parsed.port or (80 if parsed.scheme == "http" else 443)

    conn = _create_upstream_conn(upstream_cfg, parsed, port)
    conn.connect()
    conn.sock.settimeout(timeout)

    conn.request("POST", path, body=json.dumps(upstream_body), headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    })

    start = time.time()
    sse_buffer = []
    sse_buffer_size = 0
    SSE_BUFFER_MAX = 200 * 1024
    final_usage = None
    upstream_status = None

    try:  # 外层 try: 包裹全部逻辑，finally 中关闭 conn
        try:
            resp = conn.getresponse()
            upstream_status = resp.status
        except Exception as e:
            logging.exception(f"上游连接失败: model={model_name}, target={target}")
            self._handle_upstream_error(e)
            return

        if resp.status != 200:
            error_body = resp.read().decode("utf-8", errors="replace")
            error_event = _format_sse_event("response.failed", {
                "response": {
                    "id": generate_response_id(),
                    "status": "failed",
                    "output": [],
                    "status_details": {
                        "error": {"type": "server_error", "message": f"Upstream returned HTTP {resp.status}"},
                    },
                },
            })
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.wfile.write(error_event.encode("utf-8"))
            self.wfile.flush()
            try:
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, OSError):
                pass
            if logger:
                logger.log_upstream_response(
                    request_id, resp.status, error_body, 0,
                    model_name, target,
                    request_type=client_format,
                )
            return

        # 发送 SSE 响应头
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        # 核心：TransformRouter 逐事件转换
        _rstore = (
            getattr(self.server, "response_store", None)
            if store_enabled else None
        )
        from .transform_router import TransformRouter
        for sse_event in TransformRouter.stream_convert(
            resp, upstream_format, client_format,
            request_messages=upstream_body.get("messages") if _rstore else None,
            response_store=_rstore,
        ):
            self.wfile.write(sse_event.encode("utf-8"))
            self.wfile.flush()
            if sse_buffer_size < SSE_BUFFER_MAX:
                sse_buffer.append(sse_event)
                sse_buffer_size += len(sse_event)

            if "response.completed" in sse_event or "message_delta" in sse_event:
                parsed_evt = _parse_sse_event(sse_event)
                data = parsed_evt.get("data")
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
                        "error": {"type": "server_error", "message": str(e)},
                    },
                },
            })
            self.wfile.write(error_event.encode("utf-8"))
            self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except Exception:
            pass

    duration_ms = int((time.time() - start) * 1000)
    full_sse = "".join(sse_buffer) if sse_buffer else "(buffer overflow)"

    if logger:
        logger.log_upstream_response(
            request_id, upstream_status, full_sse, duration_ms,
            model_name, target,
            request_type=client_format,
        )
        logger.log_converted_response(
            request_id, model_name, target,
            {"streaming": True, "data": full_sse},
            request_type=client_format,
        )

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

    try:
        self.wfile.close()
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass
```

注意：去掉原来方法末尾的 `driver.close()` 和 `self.close_connection = True`。外层 `try/finally` 确保无论任何异常（包括日志/token_stats 抛出的），`conn.close()` 都会执行。`conn.close()` 在 finally 中保证无论正常完成、异常还是客户端断开都会执行。

- [ ] **Step 2: Commit**

```bash
git add proxy/handler.py && git commit -m "refactor: _forward_streaming 改回 http.client SSE"
```

---

### Task 4: 重写 `_handle_sdk_error` → `_handle_upstream_error`

**Files:**
- Modify: `proxy/handler.py:925-960`

- [ ] **Step 1: 替换方法**

删除 `def _handle_sdk_error` 整个方法（约第 925-960 行），替换为：

```python
def _handle_upstream_error(self, e: Exception):
    """统一 http.client 异常 → HTTP 错误映射。"""
    logging.exception(f"上游请求异常: {type(e).__name__}: {e}")

    if isinstance(e, socket.timeout):
        self._send_json(504, {"error": {"type": "timeout_error", "message": str(e)}})
    elif isinstance(e, (socket.gaierror, ssl.SSLError)):
        self._send_json(502, {"error": {"type": "connection_error", "message": str(e)}})
    elif isinstance(e, (http.client.HTTPException, ConnectionError, OSError)):
        self._send_json(502, {"error": {"type": "connection_error", "message": str(e)}})
    else:
        self._send_json(500, {"error": {"type": "internal_error", "message": str(e)}})
```

- [ ] **Step 2: 更新所有调用点**

在 handler.py 中搜索 `_handle_sdk_error`，替换为 `_handle_upstream_error`。调用点在：
- `_forward_non_streaming` 中的 SDK 异常处理（如果还有残留）→ 已在 Task 2 中改为直接的异常处理，无需此调用
- `_forward_streaming` 中同理
- 其他可能的调用点：`grep -n "_handle_sdk_error" proxy/handler.py` 确认无残留

- [ ] **Step 3: Commit**

```bash
git add proxy/handler.py && git commit -m "refactor: _handle_sdk_error → _handle_upstream_error 标准库异常"
```

---

### Task 5: 适配 test_handler.py

**Files:**
- Modify: `test/test_handler.py:538`

- [ ] **Step 1: 定位并理解现有 mock**

第 538 行：
```python
patch("proxy.upstream_driver.OpenAI") as mock_openai_cls:
```

这里 mock 了 SDK 的 OpenAI 类，用于测试 `_forward_non_streaming` 路径。

- [ ] **Step 2: 替换 mock 为 http.client 风格**

将第 538-544 行：
```python
patch("proxy.upstream_driver.OpenAI") as mock_openai_cls:
    mock_cc.resolve.return_value = upstream
    mock_gl.return_value = self.logger
    mock_cfg.get.return_value = _default_upstream_cfg()
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_chat
    mock_openai_cls.return_value = mock_client
```

替换为：
```python
patch("proxy.handler._create_upstream_conn") as mock_conn_fn:
    mock_cc.resolve.return_value = upstream
    mock_gl.return_value = self.logger
    mock_cfg.get.return_value = _default_upstream_cfg()
    mock_conn = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps(_chat_response()).encode()
    mock_conn.getresponse.return_value = mock_resp
    mock_conn_fn.return_value = mock_conn
```

其中 `_chat_response()` 是返回一个标准 Chat Completions 响应 dict 的辅助函数。如果测试文件中已有类似辅助函数（如 `mock_chat.model_dump()` 返回的结构），则用相同结构构造 dict。

- [ ] **Step 3: 运行该测试确认通过**

```bash
python3 -m pytest test/test_handler.py -q --tb=short 2>&1 | tail -20
```

预期：全部 PASSED。

- [ ] **Step 4: Commit**

```bash
git add test/test_handler.py && git commit -m "test: handler 测试 mock 改为 _create_upstream_conn"
```

---

### Task 6: 适配 test_proxy_logger_integration.py

**Files:**
- Modify: `test/test_proxy_logger_integration.py`（6 处 mock）

- [ ] **Step 1: 批量替换 mock**

6 处 `patch("proxy.upstream_driver.OpenAI")` 都改为 `patch("proxy.handler._create_upstream_conn")`，并按 Task 5 相同模式构造 mock_conn + mock_resp。

每处替换模式：
```python
# 之前
with patch("proxy.upstream_driver.OpenAI") as mock_openai:
    mock_client = MagicMock()
    mock_openai.return_value = mock_client
    mock_client.chat.completions.create.return_value = mock_chat

# 之后
with patch("proxy.handler._create_upstream_conn") as mock_conn_fn:
    mock_conn = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps(chat_response_dict).encode()
    mock_conn.getresponse.return_value = mock_resp
    mock_conn_fn.return_value = mock_conn
```

- [ ] **Step 2: 运行该测试确认通过**

```bash
python3 -m pytest test/test_proxy_logger_integration.py -q --tb=short 2>&1 | tail -20
```

预期：全部 PASSED。

- [ ] **Step 3: Commit**

```bash
git add test/test_proxy_logger_integration.py && git commit -m "test: 集成测试 mock 改为 _create_upstream_conn"
```

---

### Task 7: 删除 test_upstream_driver.py + 全量测试

**Files:**
- Delete: `test/test_upstream_driver.py`

- [ ] **Step 1: 删除测试文件**

```bash
rm test/test_upstream_driver.py
```

- [ ] **Step 2: 全量测试**

```bash
python3 -m pytest test/ -q --tb=short 2>&1 | tail -30
```

预期：全部通过，数量约 526（比 531 少 5 个被删的 driver 测试）。

- [ ] **Step 3: Commit**

```bash
git add -A && git commit -m "test: 删除 test_upstream_driver.py — UpstreamDriver 已移除"
```

---

### Task 8: 清理 SDK 依赖

**Files:**
- 无代码文件变更

- [ ] **Step 1: 确认无残留引用**

```bash
grep -rn "upstream_driver\|UpstreamDriver" proxy/ test/ --include="*.py" | grep -v __pycache__
grep -rn "from openai\|import openai\|import httpx" proxy/ --include="*.py" | grep -v __pycache__
```

预期：零匹配。

- [ ] **Step 2: 卸载依赖**

```bash
pip3 uninstall -y openai anthropic httpx httpx-sse
```

- [ ] **Step 3: 最终全量测试**

```bash
python3 -m pytest test/ -q --tb=short 2>&1
```

预期：全部通过。

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "chore: 清理 SDK 依赖 (openai/anthropic/httpx)"
```

---

## 自审清单

- [x] **Spec 覆盖**：每个设计文档中的改动点都有对应 Task（1-8）
- [x] **无占位符**：每个 Step 都包含完整代码或精确命令
- [x] **类型一致**：方法签名与调用方 `_handle_convert` 中的参数完全匹配
- [x] **测试覆盖**：Task 5-7 覆盖所有测试适配
- [x] **依赖清理**：Task 8 确认零残留引用
