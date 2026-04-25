# Request Logger 设计文稿

> 为 Proxy 添加请求/响应日志记录和 Token 统计模块。

## 目标

在 proxy 转发流程中记录四个关键阶段的数据到独立 SQLite 数据库，用于：
1. **问题排查** — 查看原始请求、转换结果、上游响应
2. **Token 统计** — 统一格式的 token 消耗数据，后续供 fact-store-browser 展示

## 约束

- 独立模块 `request_logger.py`，不影响 proxy 原有转发逻辑
- 日志写入失败不阻断 proxy 主流程（静默降级）
- 命名不与特定协议/agent 绑定（将来扩展到 Claude Code 等 agent）
- 纯 Python 标准库实现，无外部依赖

## 架构

```
Agent 请求 → proxy.py ──→ logger.log_raw_request()        [拿到 body 字节立即写]
             │
             ├─→ transform.responses_to_chat()
             ├─→ logger.log_converted_request()
             │
             ├─→ 转发到上游
             ├─→ logger.log_upstream_response() + logger.log_token_stats()
             │
             └─→ transform.chat_to_responses()
                 └─→ logger.log_converted_response()
```

所有阶段共享同一个 `request_id`，在 `_handle_responses` 入口处生成，通过参数逐层传递。

## 文件变更

| 文件 | 操作 | 说明 |
|------|------|------|
| `request_logger.py` | **新建** | 独立模块：DB 初始化、两张表、写入函数、清理逻辑 |
| `proxy.py` | **修改** | 在转发流程的 4 个关键节点调用日志模块，传入 request_id |
| `proxy_config.yaml` | **修改** | 新增 `logging` 配置块 |
| `test_request_logger.py` | **新建** | 日志模块单元测试 |
| `test_proxy_logger_integration.py` | **新建** | proxy + logger 集成冒烟测试 |
| `data/` 目录 | **新建** | 存放 `access_log.db` |

## 数据库结构

路径：`~/.hermes/fact-store-browser/data/access_log.db`

### 表 1：`debug_log` — 请求调试

每个请求最多产生 4 行（每个 stage 一行），共享同一个 `request_id`。**任何阶段出错都补一条记录**（如 JSON 解析失败、上游超时），写入错误信息到 `data` 字段。

```sql
CREATE TABLE IF NOT EXISTS debug_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id  TEXT NOT NULL,     -- UUID，同请求的 4 行共享
    stage       TEXT NOT NULL,     -- raw_request / converted_request / upstream_response / converted_response
    model       TEXT,              -- 原始模型名
    target_model TEXT,             -- 映射后的目标模型
    status_code INTEGER,           -- HTTP 状态码（仅 upstream_response stage）
    data        TEXT,              -- JSON 格式的请求/响应体，或错误信息
    created_at  TEXT NOT NULL      -- YYYY-MM-DD HH:MM:SS 格式
);

CREATE INDEX IF NOT EXISTS idx_debug_request_id ON debug_log(request_id);
CREATE INDEX IF NOT EXISTS idx_debug_created_at ON debug_log(created_at);
```

`stage` 枚举值：

| stage | 说明 | 写入时机 |
|-------|------|----------|
| `raw_request` | agent 原始请求 | `_handle_responses` 入口，拿到 body 字节后立即记录（在 JSON 解析之前） |
| `converted_request` | proxy 转换后的请求 | `responses_to_chat` 返回后 |
| `upstream_response` | 上游原始响应 | `_forward_non_streaming` 中 `resp.read()` 返回后；`_forward_streaming` 中流结束后 |
| `converted_response` | proxy 转换后的响应 | `_forward_non_streaming` 中 `chat_to_responses` 返回后 |

### 表 2：`token_stats` — Token 消耗统计

每个请求一行统一格式记录。**此表不清理，永久保留**，供 fact-store-browser 消费。

```sql
CREATE TABLE IF NOT EXISTS token_stats (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id         TEXT NOT NULL,      -- 关联 debug_log
    agent              TEXT NOT NULL,      -- 从 User-Agent 请求头提取，当前固定 "codex"
    model              TEXT NOT NULL,      -- 原始模型
    target_model       TEXT NOT NULL,      -- 映射后的目标模型
    request_ts         TEXT NOT NULL,      -- YYYY-MM-DD HH:MM:SS
    duration_ms        INTEGER,
    input_tokens       INTEGER DEFAULT 0,
    output_tokens      INTEGER DEFAULT 0,
    cached_read_tokens INTEGER DEFAULT 0,
    cached_write_tokens INTEGER DEFAULT 0,
    status             TEXT DEFAULT 'completed',  -- completed / failed / incomplete
    created_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_token_request_ts ON token_stats(request_ts);
```

**注意：`cost_usd` 字段已移除。** 计费规则由 fact-store-browser 从 `cc-switch.db` 读取时实时计算，proxy 不保存计费信息。

## request_logger.py 模块接口

### RequestLogger 类

```python
class RequestLogger:
    def __init__(self, db_path: Path, debug_retention_days: int = 7):
        """初始化：创建 data/ 目录、打开 SQLite 连接（WAL 模式 + check_same_thread=False）、建表、清理过期数据。"""

    def close(self):
        """关闭数据库连接。"""

    def log_raw_request(self, request_id: str, model: str, target: str, body: str | dict):
        """阶段 1：记录 agent 原始请求。stage = raw_request"""

    def log_converted_request(self, request_id: str, model: str, target: str, body: dict):
        """阶段 2：记录 proxy 转换后的请求。stage = converted_request"""

    def log_upstream_response(self, request_id: str, status_code: int, body: str | dict, duration_ms: int):
        """阶段 3：记录上游原始响应。stage = upstream_response。
        
        duration_ms 在此处计算并记录。非流式场景 body 为完整响应体；流式场景 body 为完整 SSE 文本。
        """

    def log_converted_response(self, request_id: str, model: str, target: str, body: dict):
        """阶段 4：记录 proxy 转换后的响应。stage = converted_response"""

    def log_token_stats(self, request_id: str, agent: str, model: str, target_model: str,
                        request_ts: str, duration_ms: int, input_tokens: int,
                        output_tokens: int, cached_read: int, cached_write: int,
                        status: str):
        """写入 Token 统计记录。duration_ms 复用 log_upstream_response 中记录的值。"""

    def _cleanup_expired(self):
        """启动时清理超过 debug_retention_days 的 debug_log 记录。token_stats 不清理。"""
```

### 全局单例

```python
# request_logger.py 模块级
_logger: Optional[RequestLogger] = None

def init_logger(db_path: Path, retention_days: int) -> RequestLogger:
    """初始化全局 logger 实例（仅在 proxy 启动时调用一次）。"""

def get_logger() -> Optional[RequestLogger]:
    """获取全局 logger 实例。返回 None 表示未初始化（测试环境）。"""
```

### 数据库连接策略

**短连接方案**（每次写入时创建/关闭）：

```python
def _get_conn(self) -> sqlite3.Connection:
    """每次写入时创建新连接，写入后关闭。
    
    优势：
    - 多线程安全（无需 check_same_thread=False）
    - WAL 模式通过 PRAGMA journal_mode=WAL 开启，提升并发读写
    - 每次写入性能影响极小（SQLite 本地文件毫秒级）
    """
    conn = sqlite3.connect(str(self.db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn
```

**注意**：由于采用短连接方案，`__init__` 中只负责创建目录和建表，不持有持久连接。`close()` 方法为空实现（短连接无需关闭），保留接口以兼容未来可能的持久连接方案。

## proxy.py 集成点

### request_id 生成

```python
import uuid

def _generate_request_id() -> str:
    return uuid.uuid4().hex[:16]
```

### `_handle_responses`（非流式 + 流式共用入口）

```python
def _handle_responses(self):
    """核心：Responses → Chat → Responses 转换。"""
    content_length = int(self.headers.get("Content-Length", 0))
    body_raw = self.rfile.read(content_length)
    
    # 生成 request_id
    request_id = _generate_request_id()
    model_name = body.get("model", "*") if parsed else "?"
    target = "unknown"
    request_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 尝试解析 JSON
    try:
        body = json.loads(body_raw)
    except json.JSONDecodeError as e:
        # JSON 解析失败：仍然记录 raw_request（含错误信息）
        logger.log_raw_request(request_id, model_name, target, {"raw_error": str(e), "raw_body": body_raw.decode("utf-8", errors="replace")})
        self._send_json(400, ...)
        return
    
    model_cfg = resolve_model(body.get("model", "*"))
    target = model_cfg["target"]
    
    # 阶段 1：记录原始请求（已解析为 dict 的 body）
    logger.log_raw_request(request_id, model_name, target, body)
    
    # 转换请求体
    try:
        chat_body = responses_to_chat(body, model_cfg)
        # 阶段 2：记录转换后的请求
        logger.log_converted_request(request_id, model_name, target, chat_body)
    except Exception as e:
        # 转换失败：补一条 converted_request 错误记录
        logger.log_converted_request(request_id, model_name, target, {"error": str(e)})
        self._send_json(500, ...)
        return
    
    # 转发到上游（传入 request_id, model_name, target, request_ts）
    if body.get("stream", False):
        self._forward_streaming(chat_body, model_cfg, request_id, model_name, target, request_ts)
    else:
        self._forward_non_streaming(chat_body, request_id, model_name, target, request_ts)
```

### `_forward_non_streaming`

```python
def _forward_non_streaming(self, chat_body: dict, request_id: str, model: str, target: str, request_ts: str):
    # ... 建立连接、发送请求 ...
    
    start = time.time()
    resp = conn.getresponse()
    resp_body = resp.read()
    duration_ms = int((time.time() - start) * 1000)
    
    if resp.status != 200:
        logger.log_upstream_response(request_id, resp.status, resp_body.decode("utf-8", errors="replace"), duration_ms)
        # 错误场景：返回错误给 agent，不再记录 converted_response
        return
    
    # 阶段 3：记录上游响应
    try:
        chat_response = json.loads(resp_body)
    except json.JSONDecodeError:
        chat_response = {"error": "non-JSON response", "raw": resp_body.decode("utf-8", errors="replace")[:5000]}
    
    logger.log_upstream_response(request_id, resp.status, chat_response, duration_ms)
    
    # 转换响应
    responses_response = chat_to_responses(chat_response)
    
    # 阶段 4：记录转换后的响应
    logger.log_converted_response(request_id, model, target, responses_response)
    
    # 阶段 5：记录 Token 统计（duration_ms 复用上面的值）
    agent = _extract_agent(self.headers.get("User-Agent", ""))
    _log_token_stats_from_chat_response(request_id, agent, model, target, request_ts, duration_ms, chat_response)
    
    self._send_json(200, responses_response)
```

### `_forward_streaming`

流式场景处理：

```python
def _forward_streaming(self, chat_body: dict, model_cfg: dict, request_id: str, model: str, target: str, request_ts: str):
    # ... 建立连接 ...
    
    resp = conn.getresponse()
    
    # 检查上游 HTTP 状态，非 200 直接报错
    if resp.status != 200:
        error_event = f'event: response.failed\n...'
        self.wfile.write(error_event.encode("utf-8"))
        self.wfile.flush()
        logger.log_upstream_response(request_id, resp.status, resp.read().decode("utf-8", errors="replace"), 0)
        return
    
    start = time.time()
    
    # 收集完整 SSE 流
    sse_buffer = []
    final_usage = None
    for sse_event in create_codex_sse_stream(resp):
        self.wfile.write(sse_event.encode("utf-8"))
        self.wfile.flush()
        sse_buffer.append(sse_event)
        # 从 completed 事件中提取 usage
        if "response.completed" in sse_event:
            try:
                data = json.loads(sse_event.split("data: ", 1)[1])
                final_usage = data.get("usage")
            except (json.JSONDecodeError, IndexError):
                pass
    
    duration_ms = int((time.time() - start) * 1000)
    full_sse = "".join(sse_buffer)
    
    # 阶段 3：记录上游 SSE（完整，不截断）
    logger.log_upstream_response(request_id, resp.status, full_sse, duration_ms)
    
    # 阶段 4：流式无 converted_response，跳过（写入跳过标记）
    logger.log_converted_response(request_id, model, target, {"streaming": True, "note": "SSE 流式响应，无 converted_response"})
    
    # 阶段 5：记录 Token 统计（从 final_usage 提取）
    agent = _extract_agent(self.headers.get("User-Agent", ""))
    if final_usage:
        logger.log_token_stats(request_id, agent, model, target, request_ts, duration_ms,
                              final_usage.get("prompt_tokens", 0),
                              final_usage.get("completion_tokens", 0),
                              final_usage.get("prompt_tokens_details", {}).get("cached_tokens", 0),
                              0, "completed")
    else:
        logger.log_token_stats(request_id, agent, model, target, request_ts, duration_ms,
                              0, 0, 0, 0, "incomplete")
```

流式场景下 debug_log 也会有 4 条记录：
1. `raw_request` — agent 原始请求
2. `converted_request` — proxy 转换后的请求
3. `upstream_response` — 完整 SSE 流
4. `converted_response` — 跳过标记（含 `streaming: true`），使 debug_log 行数一致

### Agent 识别

```python
def _extract_agent(user_agent: str) -> str:
    """从 User-Agent 请求头提取 agent 标识。
    
    当前仅支持 codex，将来扩展时添加更多匹配规则。
    """
    if "codex" in user_agent.lower():
        return "codex"
    return "unknown"
```

## 配置

`proxy_config.yaml` 新增 `logging` 块：

```yaml
logging:
  debug_retention_days: 7     # debug_log 保留天数
  log_dir: "data"             # access_log.db 存放目录（相对于 proxy.py 所在目录）
```

### 向后兼容

`proxy.py` 中读取配置时提供默认值：

```python
logging_cfg = CONFIG.get("logging", {})
retention_days = logging_cfg.get("debug_retention_days", 7)
log_dir = logging_cfg.get("log_dir", "data")
```

旧配置文件不缺少 `logging` 块时也能正常运行。

## 错误策略

所有日志写入函数内部用 `try/except Exception` 包裹：

```python
def log_raw_request(self, ...):
    try:
        conn = self._get_conn()
        conn.execute("INSERT INTO debug_log ...", (...))
        conn.commit()
        conn.close()
    except Exception as e:
        logging.warning(f"日志写入失败: {e}")
```

不抛出异常，不阻断 proxy 转发。

## 清理策略

启动时仅清理 `debug_log`，`token_stats` 永久保留：

```sql
DELETE FROM debug_log WHERE created_at < datetime('now', '-N days');
```

N 来自 `logging.debug_retention_days` 配置。

### 时间戳格式确认

所有时间戳统一使用 `YYYY-MM-DD HH:MM:SS`（无时区），即 Python `datetime.now().strftime("%Y-%m-%d %H:%M:%S")`。SQLite 的 `datetime('now', '-N days')` 返回的也是相同格式（UTC），比较时类型一致。

**注意**：`datetime('now')` 返回 UTC 时间，而 Python `datetime.now()` 返回本地时间。为避免时区差异导致清理不准确，清理 SQL 改为：

```sql
DELETE FROM debug_log WHERE created_at < datetime('now', 'localtime', '-N days');
```

这样双方都使用本地时间比较。

## request_id 生成

使用 `uuid4().hex[:16]` 生成 16 位短 UUID，既足够唯一又便于日志中阅读。

## 测试策略

### 单元测试 `test_request_logger.py`

- DB 初始化 + data/ 目录自动创建
- 两张表结构验证
- 5 个写入函数各一条记录验证
- `_cleanup_expired` 清理逻辑验证（debug_log 被清理，token_stats 不受影响）
- 写入失败不抛异常验证
- 多线程并发写入安全验证
- 使用临时数据库文件（`tempfile`），不污染生产数据

### 集成测试 `test_proxy_logger_integration.py`

使用 `importlib` 动态加载 proxy 模块（避免启动时 exec_module 触发真实 DB 连接）：

- 模拟完整请求流程 → 检查 DB 中有 4 条 debug_log + 1 条 token_stats
- JSON 解析失败场景 → 仍有一条 raw_request 错误记录
- 上游 500 错误场景 → 有 raw_request + converted_request + upstream_response，无 converted_response
- 流式请求场景 → 流结束后检查 DB 记录
