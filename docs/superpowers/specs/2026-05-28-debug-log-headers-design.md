# debug_log headers 记录设计

**日期**: 2026-05-28
**状态**: 已批准

## 目标

在 `debug_log` 表的四阶段日志中记录 HTTP headers，覆盖完整请求链路：
- raw_request：客户端发给 proxy 的 headers
- converted_request：proxy 发给上游的 headers
- upstream_response：上游返回给 proxy 的 headers
- converted_response：proxy 返回给客户端的 headers

## Schema 变更

`debug_log` 表新增一列：

```sql
ALTER TABLE debug_log ADD COLUMN headers TEXT
```

- 存储 JSON 序列化的 headers dict（如 `{"Content-Type": "application/json"}`）
- NULL 表示该阶段未记录 headers（向后兼容旧数据）

## request_logger.py 变更

### 迁移

在 `_migrate_access_log()` 末尾追加：

```python
if 'headers' not in cols_debug:
    conn.execute('ALTER TABLE debug_log ADD COLUMN headers TEXT')
```

加列操作幂等安全，无需备份。

### 接口变更

四个 `log_*` 方法统一加 `headers: dict | None = None` 参数：

| 方法 | 新增参数 | INSERT 变更 |
|------|---------|------------|
| `log_raw_request()` | `headers=None` | 加 `headers` 列，值 `json.dumps(headers) if headers else None` |
| `log_converted_request()` | `headers=None` | 同上 |
| `log_upstream_response()` | `headers=None` | 同上 |
| `log_converted_response()` | `headers=None` | 同上 |

## handler.py 变更

各阶段 headers 来源：

### raw_request（stage 1）

`self.headers` 是 `http.client.HTTPMessage`，转为 dict：
```python
client_headers = dict(self.headers)
```
传入 `log_raw_request(..., headers=client_headers)`。

### converted_request（stage 2）

各 `_forward_*` 方法中构建的 `headers` 局部变量，直接传 dict：
```python
headers = {"Content-Type": content_type}
if api_key:
    headers["Authorization"] = f"Bearer {api_key}"
conn.request(..., headers=headers)
logger.log_converted_request(..., headers=headers)
```

### upstream_response（stage 3）

`resp.getheaders()` 返回 `[(name, value), ...]`，转为 dict：
```python
upstream_headers = dict(resp.getheaders())
logger.log_upstream_response(..., headers=upstream_headers)
```

注意：`dict(resp.getheaders())` 同名 header 只保留最后一个，对于 debug 日志足够。

### converted_response（stage 4）

在各 `log_converted_response` 调用点前，手动构建 response_headers dict 传入。

以非流式透传成功为例：
```python
response_headers = {
    "Content-Type": resp.getheader("Content-Type", "application/json"),
    "Content-Length": str(len(resp_body)),
}
logger.log_converted_response(..., headers=response_headers)
```

流式场景需区分透传和转换两种路径，headers 不同：

**透传流式**（`_forward_pass_through_streaming`）：
```python
response_headers = {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Transfer-Encoding": "chunked",
}
```

**转换流式**（`_forward_streaming`）：
```python
response_headers = {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}
```

注意：转换流式路径没有 `Transfer-Encoding: chunked`，它通过 `close_connection = True` 让框架层处理连接关闭，不显式发送该 header。

**不引入额外抽象**，在每个调用点直接构建 dict，确保与 `send_header()` 调用完全一致。

## 改动文件清单

| 文件 | 改动 |
|------|------|
| `proxy/schema.py` | `debug_log` 建表语句加 `headers TEXT` |
| `proxy/request_logger.py` | 迁移 + 四个方法加 `headers` 参数 |
| `proxy/handler.py` | 各阶段捕获 headers 并传入 logger 调用 |

## 错误路径覆盖

headers 记录需覆盖所有日志写入点，包括错误路径：

| 路径 | 阶段 3 (upstream_response) | 阶段 4 (converted_response) |
|------|---------------------------|-----------------------------|
| 上游返回 4xx/5xx | `dict(resp.getheaders())` | 错误响应的 headers dict |
| 上游连接超时/异常 | 无 resp 对象，headers=None | 无响应发送，headers=None |
| 客户端 JSON 解析失败 | 无（stage 1 日志已有 raw_request headers） | 无 |
| 路由未命中 | 无（不进入转发流程） | 无 |

原则：**有 resp 对象时必须记录 upstream_response headers；有 send_header 调用时必须记录 converted_response headers。** 异常路径中无 resp 对象或未发送响应的，headers 为 NULL。

## 不改动的部分

- `token_stats` 表及相关逻辑
- 前端页面
- 配置管理
