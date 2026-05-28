# debug_log headers 记录 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 debug_log 的四阶段日志中记录完整 HTTP headers。

**Architecture:** debug_log 表新增 `headers TEXT` 列存储 JSON 序列化的 headers dict。request_logger 四个 `log_*` 方法加 `headers` 参数。handler.py 在各阶段捕获 headers 传入 logger。

**Tech Stack:** 纯 Python 标准库，SQLite，unittest

---

### Task 1: Schema 变更

**Files:**
- Modify: `proxy/schema.py:122-136`
- Test: `test/test_request_logger.py:80-101`（现有 `test_debug_log_columns`）

- [ ] **Step 1: 在 schema.py 的 debug_log 建表语句中加 `headers TEXT` 列**

在 `data TEXT` 之后加 `headers TEXT`：

```python
    "debug_log": """
        CREATE TABLE IF NOT EXISTS debug_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id   TEXT NOT NULL,
            stage        TEXT NOT NULL,
            model        TEXT,
            target_model TEXT,
            status_code  INTEGER,
            request_type TEXT,
            request_path TEXT,
            data         TEXT,
            headers      TEXT,
            session_id   TEXT,
            created_at   TEXT NOT NULL
        )
    """,
```

- [ ] **Step 2: 更新 test_request_logger.py 的 `test_debug_log_columns`**

在 `expected` dict 中加入 `"headers": "TEXT"`：

```python
        expected = {
            "id": "INTEGER",
            "request_id": "TEXT",
            "stage": "TEXT",
            "model": "TEXT",
            "target_model": "TEXT",
            "status_code": "INTEGER",
            "request_type": "TEXT",
            "request_path": "TEXT",
            "data": "TEXT",
            "headers": "TEXT",
            "session_id": "TEXT",
            "created_at": "TEXT",
        }
```

- [ ] **Step 3: 运行测试验证**

Run: `python3 -m pytest test/test_request_logger.py::TestDBInitialization::test_debug_log_columns -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add proxy/schema.py test/test_request_logger.py
git commit -m "feat(schema): debug_log 表新增 headers TEXT 列"
```

---

### Task 2: RequestLogger 迁移 + 接口变更

**Files:**
- Modify: `proxy/request_logger.py`

- [ ] **Step 1: 在 `_migrate_access_log()` 中加 headers 列迁移**

在 `session_id` 迁移代码块（第 79-82 行）之后追加：

```python
            # headers 列迁移
            if 'headers' not in cols_debug:
                conn.execute('ALTER TABLE debug_log ADD COLUMN headers TEXT')
```

- [ ] **Step 2: 修改 `log_raw_request` 方法签名和 INSERT**

方法签名加 `headers=None`：
```python
    def log_raw_request(self, request_id: str, model: str, target: str, body: str | dict,
                        request_type: str = None, request_path: str = None,
                        session_id: str = None, is_agent: bool = False, headers: dict = None):
```

INSERT 语句改为：
```python
                conn.execute(
                    "INSERT INTO debug_log (request_id, stage, model, target_model, request_type, request_path, data, headers, session_id, created_at) "
                    "VALUES (?, 'raw_request', ?, ?, ?, ?, ?, ?, ?, ?)",
                    (request_id, model, target, request_type, request_path, data,
                     json.dumps(headers) if headers else None, session_id, now),
                )
```

- [ ] **Step 3: 修改 `log_converted_request` 方法签名和 INSERT**

```python
    def log_converted_request(self, request_id: str, model: str, target: str, body: dict,
                              request_type: str = None, request_path: str = None, headers: dict = None):
```

INSERT 语句改为：
```python
                conn.execute(
                    "INSERT INTO debug_log (request_id, stage, model, target_model, request_type, request_path, data, headers, created_at) "
                    "VALUES (?, 'converted_request', ?, ?, ?, ?, ?, ?, ?)",
                    (request_id, model, target, request_type, request_path, data,
                     json.dumps(headers) if headers else None, now),
                )
```

- [ ] **Step 4: 修改 `log_upstream_response` 方法签名和 INSERT**

```python
    def log_upstream_response(self, request_id: str, status_code: int, body: str | dict, duration_ms: int,
                              model: str = None, target: str = None, request_type: str = None, headers: dict = None):
```

INSERT 语句改为：
```python
                conn.execute(
                    "INSERT INTO debug_log (request_id, stage, model, target_model, status_code, request_type, data, headers, created_at) "
                    "VALUES (?, 'upstream_response', ?, ?, ?, ?, ?, ?, ?)",
                    (request_id, model, target, status_code, request_type, data,
                     json.dumps(headers) if headers else None, now),
                )
```

- [ ] **Step 5: 修改 `log_converted_response` 方法签名和 INSERT**

```python
    def log_converted_response(self, request_id: str, model: str, target: str, body: dict,
                               request_type: str = None, headers: dict = None):
```

INSERT 语句改为：
```python
                conn.execute(
                    "INSERT INTO debug_log (request_id, stage, model, target_model, request_type, data, headers, created_at) "
                    "VALUES (?, 'converted_response', ?, ?, ?, ?, ?, ?)",
                    (request_id, model, target, request_type, data,
                     json.dumps(headers) if headers else None, now),
                )
```

- [ ] **Step 6: 运行现有测试确保无回归**

Run: `python3 -m pytest test/test_request_logger.py -q`
Expected: 全部 PASS（`headers=None` 默认值确保旧调用不受影响）

- [ ] **Step 7: Commit**

```bash
git add proxy/request_logger.py
git commit -m "feat(logger): 四阶段日志方法加 headers 参数 + 迁移"
```

---

### Task 3: RequestLogger headers 测试

**Files:**
- Modify: `test/test_request_logger.py`

- [ ] **Step 1: 在 `TestLogRawRequest` 中加 headers 测试**

```python
    def test_log_raw_request_with_headers(self):
        """log_raw_request 带 headers 参数时正确写入。"""
        rid = _generate_request_id()
        headers = {"Content-Type": "application/json", "Authorization": "Bearer sk-test"}
        self.logger.log_raw_request(rid, "gpt-4o", "qwen3.6-plus", {"model": "gpt-4o"},
                                    headers=headers)
        rows = _query_debug_log(self.db_path, rid)
        self.assertEqual(len(rows), 1)
        saved_headers = json.loads(rows[0]["headers"])
        self.assertEqual(saved_headers["Content-Type"], "application/json")
        self.assertEqual(saved_headers["Authorization"], "Bearer sk-test")

    def test_log_raw_request_without_headers(self):
        """log_raw_request 不带 headers 时，headers 列为 NULL。"""
        rid = _generate_request_id()
        self.logger.log_raw_request(rid, "gpt-4o", "qwen3.6-plus", {"model": "gpt-4o"})
        rows = _query_debug_log(self.db_path, rid)
        self.assertIsNone(rows[0]["headers"])
```

- [ ] **Step 2: 在 `TestLogConvertedRequest` 中加 headers 测试**

```python
    def test_log_converted_request_with_headers(self):
        """log_converted_request 带 headers 参数时正确写入。"""
        rid = _generate_request_id()
        headers = {"Content-Type": "application/json", "Authorization": "Bearer sk-abc"}
        self.logger.log_converted_request(rid, "gpt-4o", "qwen3.6-plus",
                                          {"model": "qwen3.6-plus"}, headers=headers)
        rows = _query_debug_log(self.db_path, rid)
        saved_headers = json.loads(rows[0]["headers"])
        self.assertEqual(saved_headers["Authorization"], "Bearer sk-abc")

    def test_log_converted_request_without_headers(self):
        """log_converted_request 不带 headers 时，headers 列为 NULL。"""
        rid = _generate_request_id()
        self.logger.log_converted_request(rid, "gpt-4o", "qwen3.6-plus", {"model": "qwen3.6-plus"})
        rows = _query_debug_log(self.db_path, rid)
        self.assertIsNone(rows[0]["headers"])
```

- [ ] **Step 3: 在 `TestLogUpstreamResponse` 中加 headers 测试**

```python
    def test_log_upstream_response_with_headers(self):
        """log_upstream_response 带 headers 参数时正确写入。"""
        rid = _generate_request_id()
        headers = {"Content-Type": "application/json", "X-Request-Id": "req-123"}
        self.logger.log_upstream_response(rid, 200, "ok", 100, headers=headers)
        rows = _query_debug_log(self.db_path, rid)
        saved_headers = json.loads(rows[0]["headers"])
        self.assertEqual(saved_headers["X-Request-Id"], "req-123")

    def test_log_upstream_response_without_headers(self):
        """log_upstream_response 不带 headers 时，headers 列为 NULL。"""
        rid = _generate_request_id()
        self.logger.log_upstream_response(rid, 200, "ok", 100)
        rows = _query_debug_log(self.db_path, rid)
        self.assertIsNone(rows[0]["headers"])
```

- [ ] **Step 4: 在 `TestLogConvertedResponse` 中加 headers 测试**

```python
    def test_log_converted_response_with_headers(self):
        """log_converted_response 带 headers 参数时正确写入。"""
        rid = _generate_request_id()
        headers = {"Content-Type": "text/event-stream", "Cache-Control": "no-cache"}
        self.logger.log_converted_response(rid, "gpt-4o", "qwen3.6-plus",
                                           {"id": "resp-abc"}, headers=headers)
        rows = _query_debug_log(self.db_path, rid)
        saved_headers = json.loads(rows[0]["headers"])
        self.assertEqual(saved_headers["Content-Type"], "text/event-stream")

    def test_log_converted_response_without_headers(self):
        """log_converted_response 不带 headers 时，headers 列为 NULL。"""
        rid = _generate_request_id()
        self.logger.log_converted_response(rid, "gpt-4o", "qwen3.6-plus", {"id": "resp-abc"})
        rows = _query_debug_log(self.db_path, rid)
        self.assertIsNone(rows[0]["headers"])
```

- [ ] **Step 5: 运行新增测试**

Run: `python3 -m pytest test/test_request_logger.py -q -k "headers"`
Expected: 全部 8 个新测试 PASS

- [ ] **Step 6: Commit**

```bash
git add test/test_request_logger.py
git commit -m "test: debug_log headers 参数单元测试"
```

---

### Task 4: Handler — raw_request headers 捕获

**Files:**
- Modify: `proxy/handler.py:280-283`

- [ ] **Step 1: 修改 `do_POST` 中 `log_raw_request` 调用，传入客户端 headers**

在第 281-283 行的 `logger.log_raw_request(...)` 调用前提取 headers，并加入调用参数：

```python
        # 阶段 1：记录原始请求（完整下游 URL）
        logger = get_logger()
        if logger:
            client_headers = dict(self.headers)
            logger.log_raw_request(request_id, model_name, target, body,
                                   request_type=request_type, request_path=downstream_url,
                                   session_id=session_id, is_agent=is_agent,
                                   headers=client_headers)
```

- [ ] **Step 2: Commit**

```bash
git add proxy/handler.py
git commit -m "feat(handler): raw_request 阶段记录客户端 headers"
```

---

### Task 5: Handler — converted_request headers 捕获

**Files:**
- Modify: `proxy/handler.py`

**核心原则：** `log_converted_request` 必须与 `conn.request()` 使用同一个 `api_key` 和 `headers` dict，避免日志与实际不一致。

**方案：将 `log_converted_request` 从调用方（`_handle_passthrough`/`_handle_convert`）移入 `_forward_*` 方法内部，紧接在 headers 构建之后、`conn.request()` 之前调用。**

需要给 `_forward_*` 方法新增参数以传递日志所需上下文。

- [ ] **Step 1: 修改 `_handle_passthrough` — 删除原有 `log_converted_request` 调用，传入 `upstream_url`**

删除第 342-350 行的 `log_converted_request` 调用块。

在调用 `_forward_pass_through_streaming` 和 `_forward_pass_through_non_streaming` 时，新增 `upstream_url` 参数：

```python
        if is_stream:
            self._forward_pass_through_streaming(
                body_raw, request_id, model_name, target, request_ts,
                upstream_cfg, forward_path, request_type, session_id,
                upstream_url=upstream_url,
            )
        else:
            self._forward_pass_through_non_streaming(
                body_raw, request_id, model_name, target, request_ts,
                upstream_cfg, forward_path, request_type, session_id,
                upstream_url=upstream_url,
            )
```

- [ ] **Step 2: 修改 `_forward_pass_through_non_streaming` — 加 `upstream_url` 参数，在 headers 构建后记录日志**

方法签名加 `upstream_url=None`：

```python
    def _forward_pass_through_non_streaming(self, body_raw, request_id, model_name,
                                             target, request_ts, upstream_cfg,
                                             forward_path, request_type, session_id=None,
                                             upstream_url=None):
```

在 `headers` dict 构建（第 408-410 行）之后、`conn.request()` 之前插入日志调用：

```python
                headers = {"Content-Type": content_type}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                # 阶段 2：记录转换后的请求（与实际发送的 headers 一致）
                logger = get_logger()
                if logger:
                    logger.log_converted_request(
                        request_id, model_name, target,
                        {"passthrough": True, "format_match": True,
                         "reason": f"request_type '{request_type}' 匹配上游 format"},
                        request_type=request_type,
                        request_path=upstream_url,
                        headers=headers,
                    )
                conn.request(self.command, path, body=body_raw, headers=headers)
```

- [ ] **Step 3: 修改 `_forward_pass_through_streaming` — 同 Step 2 模式**

方法签名加 `upstream_url=None`：

```python
    def _forward_pass_through_streaming(self, body_raw, request_id, model_name,
                                         target, request_ts, upstream_cfg,
                                         forward_path, request_type, session_id=None,
                                         upstream_url=None):
```

在 headers 构建（第 528-534 行）之后、`conn.request()` 之前插入：

```python
                headers = {
                    "Content-Type": content_type,
                    "Accept": "text/event-stream",
                    "Connection": "close",
                }
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                logger = get_logger()
                if logger:
                    logger.log_converted_request(
                        request_id, model_name, target,
                        {"passthrough": True, "format_match": True,
                         "reason": f"request_type '{request_type}' 匹配上游 format"},
                        request_type=request_type,
                        request_path=upstream_url,
                        headers=headers,
                    )
                conn.request(self.command, path, body=body_raw, headers=headers)
```

- [ ] **Step 4: 修改 `_handle_convert` — 删除原有 `log_converted_request` 调用，传入 `upstream_url`**

删除第 767-776 行的 `upstream_url` 构建和 `log_converted_request` 调用块。

在调用 `_forward_streaming` 和 `_forward_non_streaming` 时，新增 `upstream_url` 参数：

```python
        if is_stream:
            self._forward_streaming(
                upstream_body, model_cfg, request_id, model_name, target, request_ts,
                upstream_cfg, client_format, upstream_format,
                store_enabled=store_enabled, session_id=session_id,
                upstream_url=upstream_url,
            )
        else:
            self._forward_non_streaming(
                upstream_body, request_id, model_name, target, request_ts,
                upstream_cfg, client_format, upstream_format,
                store_enabled=store_enabled,
                is_responses_api=is_responses_api, session_id=session_id,
                upstream_url=upstream_url,
            )
```

注意：`upstream_url` 变量仍在 `_handle_convert` 中构建（第 767-770 行），只是删掉其后的 logger 调用。

- [ ] **Step 5: 修改 `_forward_non_streaming`（转换路径）— 加 `upstream_url` 参数，在 headers 构建后记录日志**

方法签名加 `upstream_url=None`：

```python
    def _forward_non_streaming(self, upstream_body, request_id, model, target,
                                 request_ts, upstream_cfg, client_format,
                                 upstream_format, store_enabled=True,
                                 is_responses_api=False, session_id=None,
                                 upstream_url=None):
```

在 headers 构建（第 858-861 行）之后、`conn.request()` 之前插入：

```python
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                }
                logger = get_logger()
                if logger:
                    logger.log_converted_request(
                        request_id, model, target, upstream_body,
                        request_type=client_format,
                        request_path=upstream_url,
                        headers=headers,
                    )
                conn.request("POST", path, body=json.dumps(upstream_body), headers=headers)
```

- [ ] **Step 6: 修改 `_forward_streaming`（转换路径）— 同 Step 5 模式**

方法签名加 `upstream_url=None`：

```python
    def _forward_streaming(self, upstream_body, model_cfg, request_id, model_name,
                             target, request_ts, upstream_cfg, client_format,
                             upstream_format, store_enabled=True, session_id=None,
                             upstream_url=None):
```

在 headers 构建（第 999-1003 行）之后、`conn.request()` 之前插入：

```python
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        logger = get_logger()
        if logger:
            logger.log_converted_request(
                request_id, model_name, target, upstream_body,
                request_type=client_format,
                request_path=upstream_url,
                headers=headers,
            )
        conn.request("POST", path, body=json.dumps(upstream_body), headers=headers)
```

- [ ] **Step 7: 运行现有测试验证**

Run: `python3 -m pytest test/ -q`
Expected: 全部 PASS。`upstream_url=None` 默认值确保旧签名兼容。

- [ ] **Step 8: Commit**

```bash
git add proxy/handler.py
git commit -m "feat(handler): converted_request 阶段记录发给上游的 headers（日志与实际 key 一致）"
```

---

### Task 6: Handler — upstream_response headers 捕获

**Files:**
- Modify: `proxy/handler.py`

需要在所有 `log_upstream_response` 调用处传入 `headers=dict(resp.getheaders())`。

- [ ] **Step 1: `_forward_pass_through_non_streaming` 成功路径（第 439-446 行）**

```python
                if logger:
                    log_data = error_body_str[:5000]
                    upstream_headers = dict(resp.getheaders())
                    logger.log_upstream_response(
                        request_id, resp.status, log_data, duration_ms,
                        model_name, target,
                        request_type=request_type,
                        headers=upstream_headers,
                    )
```

- [ ] **Step 2: `_forward_pass_through_streaming` 成功路径（第 636-641 行）**

```python
                if logger:
                    upstream_headers = dict(resp.getheaders())
                    logger.log_upstream_response(
                        request_id, upstream_status, full_sse, duration_ms,
                        model_name, target,
                        request_type=request_type,
                        headers=upstream_headers,
                    )
```

- [ ] **Step 3: `_forward_pass_through_streaming` 错误路径（第 564-570 行）**

```python
                    logger = get_logger()
                    if logger:
                        upstream_headers = dict(resp.getheaders())
                        logger.log_upstream_response(
                            request_id, upstream_status,
                            error_body_str[:5000],
                            0, model_name, target,
                            request_type=request_type,
                            headers=upstream_headers,
                        )
```

- [ ] **Step 4: `_forward_pass_through_streaming` 异常路径（第 675-683 行）**

异常时无 `resp` 对象，`headers=None`（保持现有行为，无需改动）。

- [ ] **Step 5: `_forward_non_streaming` 成功路径（第 911-916 行）**

```python
                if logger:
                    upstream_headers = dict(resp.getheaders())
                    logger.log_upstream_response(
                        request_id, resp.status, chat_response, duration_ms,
                        model, target,
                        request_type=client_format,
                        headers=upstream_headers,
                    )
```

- [ ] **Step 6: `_forward_non_streaming` 错误路径（第 893-899 行）**

```python
                    if logger:
                        upstream_headers = dict(resp.getheaders())
                        logger.log_upstream_response(
                            request_id, resp.status,
                            resp_body_str,
                            duration_ms, model, target,
                            request_type=client_format,
                            headers=upstream_headers,
                        )
```

- [ ] **Step 7: `_forward_non_streaming` 重试路径（第 872-879 行）**

```python
                    if logger:
                        upstream_headers = dict(resp.getheaders())
                        logger.log_upstream_response(
                            request_id, resp.status,
                            resp_body_str,
                            duration_ms, model, target,
                            request_type=client_format,
                            headers=upstream_headers,
                        )
```

- [ ] **Step 8: `_forward_streaming` 成功路径（第 1122-1127 行）**

```python
        if logger:
            upstream_headers = dict(resp.getheaders())
            logger.log_upstream_response(
                request_id, upstream_status, full_sse, duration_ms,
                model_name, target,
                request_type=client_format,
                headers=upstream_headers,
            )
```

- [ ] **Step 9: `_forward_streaming` 错误路径（第 1054-1059 行）**

```python
                if logger:
                    upstream_headers = dict(resp.getheaders())
                    logger.log_upstream_response(
                        request_id, resp.status, error_body, 0,
                        model_name, target,
                        request_type=client_format,
                        headers=upstream_headers,
                    )
```

- [ ] **Step 10: Commit**

```bash
git add proxy/handler.py
git commit -m "feat(handler): upstream_response 阶段记录上游返回的 headers"
```

---

### Task 7: Handler — converted_response headers 捕获

**Files:**
- Modify: `proxy/handler.py`

在每个 `log_converted_response` 调用点前构建 response_headers dict。

- [ ] **Step 1: `_forward_pass_through_non_streaming` 成功路径（第 447-452 行）**

```python
                if logger:
                    response_headers = {
                        "Content-Type": resp.getheader("Content-Type", "application/json"),
                        "Content-Length": str(len(resp_body)),
                    }
                    logger.log_converted_response(
                        request_id, model_name, target,
                        {"passthrough": True},
                        request_type=request_type,
                        headers=response_headers,
                    )
```

- [ ] **Step 2: `_forward_pass_through_streaming` 成功路径（第 644-649 行）**

> 注意：此处包含 `Transfer-Encoding: chunked` 是因为 `_forward_pass_through_streaming`（handler.py:578）通过 `self.send_header("Transfer-Encoding", "chunked")` 显式发送了此 header，与 `_forward_streaming`（转换路径）不同。

```python
                if logger:
                    response_headers = {
                        "Content-Type": "text/event-stream",
                        "Cache-Control": "no-cache",
                        "X-Accel-Buffering": "no",
                        "Transfer-Encoding": "chunked",
                    }
                    logger.log_converted_response(
                        request_id, model_name, target,
                        {"passthrough": True, "streaming": True},
                        request_type=request_type,
                        headers=response_headers,
                    )
```

- [ ] **Step 3: `_forward_non_streaming` 成功路径（第 923-927 行）**

```python
                    if logger:
                        response_headers = {"Content-Type": "application/json"}
                        logger.log_converted_response(
                            request_id, model, target, output,
                            request_type=client_format,
                            headers=response_headers,
                        )
```

- [ ] **Step 4: `_forward_non_streaming` 转换失败路径（第 946-950 行）**

```python
                    if logger:
                        response_headers = {"Content-Type": "application/json"}
                        logger.log_converted_response(
                            request_id, model, target,
                            {"error": str(e)}, request_type=client_format,
                            headers=response_headers,
                        )
```

- [ ] **Step 5: `_forward_streaming` 成功路径（第 1128-1132 行）**

```python
            logger.log_converted_response(
                request_id, model_name, target,
                {"streaming": True, "data": full_sse},
                request_type=client_format,
                headers={
                    "Content-Type": "text/event-stream",
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )
```

- [ ] **Step 6: Commit**

```bash
git add proxy/handler.py
git commit -m "feat(handler): converted_response 阶段记录返回给客户端的 headers"
```

---

### Task 8: 全量测试验证

**Files:** 无新增/修改

- [ ] **Step 1: 运行全量测试**

Run: `python3 -m pytest test/ -q`
Expected: 全部 530+ tests PASS，0 failures

- [ ] **Step 2: 如有失败，修复后重跑直到全绿**

- [ ] **Step 3: Final commit（如有修复）**

```bash
git add -A
git commit -m "fix: headers 参数引入后的测试修复"
```
