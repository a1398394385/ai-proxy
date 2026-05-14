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

## 实施顺序

按以下顺序执行，便于发现遗漏：

1. 删除 `upstream_driver.py` + 清理所有 import → 编译报错暴露所有调用点
2. 实现 `_forward_non_streaming` 新逻辑
3. 实现 `_forward_streaming` 新逻辑
4. 改写 `_handle_sdk_error` → `_handle_upstream_error`
5. 运行测试
6. 适配测试文件（`test_upstream_driver.py` 删除，其他 mock 适配）
7. 清理依赖（`pip uninstall openai anthropic httpx httpx-sse`）

## 详细设计

### 1. 路径映射表

`upstream_format` → 上游请求路径：

| upstream_format | 路径 |
|-----------------|------|
| `chat_completions` | `/v1/chat/completions` |
| `responses` | `/v1/responses` |
| `messages` | `/v1/messages` |

路径拼接逻辑：`parsed.path.rstrip("/") + 上表路径`（与透传路径一致）。

### 2. `_forward_non_streaming` 改造

当前（SDK）：`UpstreamDriver.create()` → `model_dump()` → `TransformRouter.convert_response()`

改造后：
```
从 upstream_cfg 取 base_url / api_key / timeout / connect_timeout / retry
  ↓
urllib.parse.urlparse(base_url) → host / port / scheme
path = 按路径映射表拼接
  ↓
重试循环 for attempt in range(retries):
  ↓
  _create_upstream_conn(upstream_cfg, parsed, port)
    → connect_timeout 用于 conn 构造参数（已在 _create_upstream_conn 内部处理）
  conn.connect()
  conn.sock.settimeout(timeout)  → timeout 用于 socket 读取
  ↓
  conn.request("POST", path, body=json.dumps(upstream_body), headers={
      "Authorization": f"Bearer {api_key}",
      "Content-Type": "application/json",
  })
  ↓
  resp = conn.getresponse()
  resp_body = resp.read()
  duration_ms = 计算
  ↓
  resp.status >= 500 and attempt < retries - 1:
    → logging.warning + continue（立即重试，无 backoff）
  ↓
  resp.status != 200:
    → 转发错误响应 + 日志 + return
  ↓
  json.loads(resp_body) → chat_response
    → json.JSONDecodeError / UnicodeDecodeError:
      chat_response = {"error": str(e), "raw": resp_body[:5000]}
  ↓
  TransformRouter.convert_response(chat_response, upstream_format, client_format)
  ↓
  日志 + token_stats + 存储（逻辑不变）
  ↓
  finally: conn.close()（每次循环内 try/finally）
```

关键差异：
- 路径按 `upstream_format` 动态拼接（见路径映射表），不再写死 `/v1/chat/completions`
- 复用 `common.py` 的 `_create_upstream_conn()`（透传路径一直在用）
- 超时配置：`connect_timeout` 用于连接建立（`_create_upstream_conn` 内），`timeout` 用于 `conn.sock.settimeout()`（读取阶段）
- 重试逻辑：`resp.status >= 500` 触发重试，覆盖全部 5xx（502/503/504 最常见，501/505 也包含），立即重试无 backoff，与原始代码一致
- 错误响应处理：`resp.status != 200` 时直接转发原始响应体给客户端
- 非JSON响应处理：`json.loads()` 失败时降级为 `{"error": ..., "raw": ...}`，不会崩溃

### 3. `_forward_streaming` 改造

当前（SDK）：`UpstreamDriver.create_stream()` → SDK chunk 迭代器 → `TransformRouter.stream_convert()`

改造后：
```
从 upstream_cfg 取 base_url / api_key / timeout / connect_timeout
  ↓
urllib.parse.urlparse(base_url) → host / port / scheme
path = 按路径映射表拼接
  ↓
_create_upstream_conn(upstream_cfg, parsed, port)
conn.connect()
conn.sock.settimeout(timeout)
  ↓
conn.request("POST", path, body=json.dumps(upstream_body), headers={
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json",
    "Accept": "text/event-stream",
})
  ↓
resp = conn.getresponse()
  ↓
resp.status != 200 → SSE 错误事件 + 日志 + return
Content-Type 非 text/event-stream → 同上
  ↓
将 resp（file-like）直接传给 TransformRouter.stream_convert(upstream_format, client_format, chunks=resp)
  ↓
adapter.stream_from() → create_xxx_sse_stream(chunks=resp)
  → 工厂函数内部按行读取 resp，逐事件转换
  ↓
转换后 SSE → sse_buffer + 写回客户端
  ↓
日志 + token_stats（逻辑不变）
  ↓
finally: conn.close()（方法最外层 try/finally）
```

关键差异：
- `TransformRouter.stream_convert()` 的 `chunks` 参数改回接收 file-like `resp` 对象。**TransformRouter 和 Adapter 代码无需修改**——参数名 `chunks` 不变，Python 鸭子类型兼容；工厂函数 `create_codex_sse_stream` / `create_anthropic_sse_stream` 原始设计就是接受 file-like 对象，SDK 迁移时做了兼容适配，回归后自然恢复
- 上游原始 SSE 可完整记录到 `log_upstream_response`（SDK 模式做不到）
- 转换后 SSE 继续用 `sse_buffer` 记录到 `log_converted_response`

**连接生命周期**：
- `conn` 在方法开头创建
- 整个方法体包裹在 `try/finally` 中
- `finally` 块执行 `conn.close()`
- 客户端断开（`BrokenPipeError` / `ConnectionResetError`）在内层 try 中捕获，不会跳过 finally
- 无论正常完成、异常、客户端断开，连接都会被关闭，无泄漏风险

### 4. `_handle_sdk_error` → `_handle_upstream_error`

异常映射（完整版）：

| 异常 | 场景 | HTTP 状态码 |
|------|------|------------|
| `socket.timeout` | 上游读取超时 | 504 Gateway Timeout |
| `socket.gaierror` | DNS 解析失败 | 502 Bad Gateway |
| `ssl.SSLError` | SSL 握手/证书错误 | 502 Bad Gateway |
| `http.client.HTTPException` | HTTP 协议错误 | 502 Bad Gateway |
| `ConnectionError` / `OSError` | 网络层错误 | 502 Bad Gateway |
| `json.JSONDecodeError` | 响应解析失败 | 502 Bad Gateway |
| 其他 | 未知异常 | 500 Internal Server Error |

移除：`import httpx`、`from openai import ...`
保留：`import socket`、`import ssl`、`import http.client`

### 5. 文件清理

| 文件 | 改动 |
|------|------|
| `proxy/upstream_driver.py` | **整文件删除** |
| `proxy/__init__.py` | 移除 `from .upstream_driver import UpstreamDriver` |
| `proxy/transform.py` | 移除 `from .upstream_driver import UpstreamDriver` |
| `proxy/handler.py` | 移除两处 `from .upstream_driver import UpstreamDriver`，确认 `import ssl` 存在 |

### 6. 依赖清理

移除：
- `openai`
- `anthropic`（如无其他引用）
- `httpx`
- `httpx-sse`

### 7. 测试适配

| 文件 | 改动 |
|------|------|
| `test/test_upstream_driver.py` | **整文件删除**（5 tests） |
| `test/test_handler.py` | 1 处 mock：`proxy.upstream_driver.OpenAI` → mock `proxy.common._create_upstream_conn` |
| `test/test_proxy_logger_integration.py` | 6 处 mock：`proxy.upstream_driver.OpenAI` → mock `proxy.common._create_upstream_conn` |
| `test/test_proxy_pass_through.py` | **无需改动**（不使用 `_create_upstream_conn`，透传路径不变） |

Mock 适配方式：
```python
# 之前（SDK）
with patch("proxy.upstream_driver.OpenAI") as mock_openai_cls:
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client

# 之后（http.client）
mock_conn = MagicMock()
mock_resp = MagicMock()
mock_resp.status = 200
mock_resp.read.return_value = json.dumps({"choices": [...], "usage": {...}}).encode()
mock_conn.getresponse.return_value = mock_resp
with patch("proxy.common._create_upstream_conn", return_value=mock_conn):
    ...
```

## 不变的部分

以下代码完全保留，不做任何修改：

- `TransformRouter`（`proxy/transform_router.py`）— 参数名 `chunks` 不变，duck-typing 兼容 file-like
- `ProtocolAdapter` 注册表（`proxy/adapters/`）
- `ConfigCache` + `ConfigDB`（`proxy/config_manager.py`）
- 转换模块（`proxy/transform_responses.py`、`proxy/transform_anthropic.py`）
- 透传路径（`_handle_passthrough` 不变）
- 日志 / Token 统计 / Pricing / ResponseStore
- 前端 / Data Browser

## 验证标准

1. 全量测试通过（删除 `test_upstream_driver.py` 后应为 ~526 tests）
2. 非流式转换路径：请求正确到达上游，响应正确转换，debug_log 四阶段完整
3. 流式转换路径：SSE 事件逐个转换，上游原始 SSE 记录到 debug_log
4. 透传路径：三种格式（chat_completions / responses / messages）均不受影响
5. 无 `openai` / `anthropic` / `httpx` 残留引用
6. `pip list` 中无这三个包（可选清理）
