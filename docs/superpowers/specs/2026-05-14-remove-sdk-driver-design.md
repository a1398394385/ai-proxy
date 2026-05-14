# 移除 SDK 上游驱动，回归 http.client

**日期:** 2026-05-14
**状态:** 待实施

## 背景

SDK 迁移（`86a5604` 起）引入了 `UpstreamDriver` + `openai`/`anthropic` SDK 作为 HTTP 客户端。
经过实际使用，收益不足以覆盖代价：

- 转换逻辑全在 `TransformRouter` + Adapter 层，SDK 只是 HTTP 客户端
- 流式路径无法记录上游原始响应（只能记占位符）
- 引入了 `openai`、`anthropic`、`httpx` 三个外部依赖
- SDK chunk 对象和原始 SSE 文本之间的适配增加了复杂度

**保留的架构**：`TransformRouter`、`ProtocolAdapter` 注册表、`ConfigCache`、NxM 转换矩阵——这些有实际价值。

**回退的范围**：仅 HTTP 调用层，从 SDK 回归 `http.client` + `ssl` 标准库。

## 迁移策略：方案 B（原地替换）

保留当前 `handler.py` 整体框架不动，只替换三个方法内部实现 + 删除一个文件。

改动清单：
1. `_forward_non_streaming`：删掉 SDK 调用，恢复 `http.client`
2. `_forward_streaming`：删掉 SDK 流式调用，恢复 `http.client` SSE 连接
3. `_handle_sdk_error` → `_handle_upstream_error`：改回标准库异常映射
4. 删除 `upstream_driver.py`
5. 清理 re-export 和依赖
6. 适配测试

## 详细设计

### 1. `_forward_non_streaming` 改造

当前（SDK）：`UpstreamDriver.create()` → `model_dump()` → `TransformRouter.convert_response()`

改造后：
```
从 upstream_cfg 取 base_url/api_key/timeout/retry
  ↓
urllib.parse.urlparse(base_url) → host/port/scheme
  ↓
path = 按 upstream_format 拼接：
  chat_completions → /v1/chat/completions
  responses        → /v1/responses
  messages         → /v1/messages
  ↓
_create_upstream_conn(upstream_cfg, parsed, port) → http.client 连接
conn.connect()
conn.sock.settimeout(timeout)
  ↓
重试循环（retries 次，仅 5xx 重试）
  conn.request("POST", path, body=json.dumps(upstream_body), headers=...)
  resp = conn.getresponse()
  resp_body = resp.read()
  ↓
resp.status != 200 → 转发错误 + 日志 + return
  ↓
json.loads(resp_body) → chat_response
  ↓
TransformRouter.convert_response(chat_response, upstream_format, client_format) → output
  ↓
日志 + token_stats + 存储（逻辑不变）
```

关键差异：
- 路径按 `upstream_format` 动态拼接，不再写死 `/v1/chat/completions`
- 复用 `common.py` 的 `_create_upstream_conn()`（透传路径一直在用）
- 重试逻辑：for 循环，仅 5xx 触发重试
- 连接管理：finally 块中 `conn.close()`

### 2. `_forward_streaming` 改造

当前（SDK）：`UpstreamDriver.create_stream()` → SDK chunk 迭代器 → `TransformRouter.stream_convert()`

改造后：
```
从 upstream_cfg 取 base_url/api_key/timeout
  ↓
urllib.parse.urlparse(base_url) → host/port/scheme
path = 按 upstream_format 拼接
  ↓
_create_upstream_conn() → http.client 连接
conn.request("POST", path, body=json.dumps(upstream_body),
             headers=... + "Accept: text/event-stream")
  ↓
resp = conn.getresponse()
  ↓
resp.status != 200 → SSE 错误事件 + 日志 + return
Content-Type 非 text/event-stream → 同上
  ↓
将 resp（file-like）直接传给 TransformRouter.stream_convert()
  ↓
工厂函数内部按行读取 resp，逐事件转换
  ↓
转换后 SSE → sse_buffer + 写回客户端
  ↓
日志 + token_stats（逻辑不变）
```

关键差异：
- `TransformRouter.stream_convert()` 的 `chunks` 参数改回接收 file-like `resp` 对象
- 上游原始 SSE 可完整记录到 `log_upstream_response`（SDK 模式做不到）
- 转换后 SSE 继续用 `sse_buffer` 记录到 `log_converted_response`

### 3. `_handle_sdk_error` → `_handle_upstream_error`

异常映射：

| 异常 | HTTP 状态码 |
|------|------------|
| `socket.timeout` | 504 Gateway Timeout |
| `http.client.HTTPException` / `ConnectionError` / `OSError` | 502 Bad Gateway |
| 其他 | 500 Internal Server Error |

移除：`import httpx`、`from openai import ...`

### 4. 文件清理

| 文件 | 改动 |
|------|------|
| `proxy/upstream_driver.py` | **整文件删除** |
| `proxy/__init__.py` | 移除 `from .upstream_driver import UpstreamDriver` |
| `proxy/transform.py` | 移除 `from .upstream_driver import UpstreamDriver` |
| `proxy/handler.py` | 移除两处 `from .upstream_driver import UpstreamDriver`，恢复 `import ssl`（如缺失） |

### 5. 依赖清理

移除：
- `openai`
- `anthropic`（如无其他引用）
- `httpx`
- `httpx-sse`

### 6. 测试适配

| 文件 | 改动 |
|------|------|
| `test/test_upstream_driver.py` | **整文件删除** |
| `test/test_handler.py` | mock 对象从 `OpenAI` 改为 mock `_create_upstream_conn` |
| `test/test_proxy_logger_integration.py` | 同上，6 处 mock 需要适配 |

适配方式：mock `_create_upstream_conn` 返回一个模拟连接对象，该对象的 `getresponse()` 返回模拟响应，`request()` 记录请求体。与原始测试模式一致。

## 不变的部分

以下架构完全保留，不做任何修改：

- `TransformRouter`（`proxy/transform_router.py`）
- `ProtocolAdapter` 注册表（`proxy/adapters/`）
- `ConfigCache` + `ConfigDB`（`proxy/config_manager.py`）
- 转换模块（`proxy/transform_responses.py`、`proxy/transform_anthropic.py`）
- 透传路径（`_handle_passthrough` 不变）
- 日志/Token 统计/Pricing/ResponseStore
- 前端 / Data Browser

## 验证标准

1. 全量 531 tests 通过（删除 `test_upstream_driver.py` 后应为 ~526）
2. 非流式转换路径：请求正确到达上游，响应正确转换，debug_log 四阶段完整
3. 流式转换路径：SSE 事件逐个转换，上游原始 SSE 记录到 debug_log
4. 透传路径：三种格式（chat_completions / responses / messages）均不受影响
5. 无 `openai` / `anthropic` / `httpx` 残留引用
6. `pip list` 中无这三个包（可选清理）
