# Request Logger 设计文稿

> 为 Codex Proxy 添加请求/响应日志记录和 Token 统计模块。

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
Agent 请求 → proxy.py ──→ logger.log_raw_request()
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

## 文件变更

| 文件 | 操作 | 说明 |
|------|------|------|
| `request_logger.py` | **新建** | 独立模块：DB 初始化、两张表、写入函数、清理逻辑 |
| `proxy.py` | **修改** | 在转发流程的 4 个关键节点调用日志模块 |
| `proxy_config.yaml` | **修改** | 新增 `logging` 配置块 |
| `test_request_logger.py` | **新建** | 日志模块单元测试 |
| `data/` 目录 | **新建** | 存放 `access_log.db` |

## 数据库结构

路径：`~/.hermes/fact-store-browser/data/access_log.db`

### 表 1：`debug_log` — 请求调试

每个请求产生 4 行（每个 stage 一行），共享同一个 `request_id`。

```sql
CREATE TABLE IF NOT EXISTS debug_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id  TEXT NOT NULL,     -- UUID，同请求的 4 行共享
    stage       TEXT NOT NULL,     -- raw_request / converted_request / upstream_response / converted_response
    model       TEXT,              -- 原始模型名
    target_model TEXT,             -- 映射后的目标模型
    status_code INTEGER,           -- HTTP 状态码（仅 upstream_response stage）
    data        TEXT,              -- JSON 格式的请求/响应体
    created_at  TEXT NOT NULL      -- ISO8601 时间戳
);

CREATE INDEX IF NOT EXISTS idx_debug_request_id ON debug_log(request_id);
CREATE INDEX IF NOT EXISTS idx_debug_created_at ON debug_log(created_at);
```

`stage` 枚举值：

| stage | 说明 |
|-------|------|
| `raw_request` | agent 原始请求 |
| `converted_request` | proxy 转换后的请求 |
| `upstream_response` | 上游原始响应 |
| `converted_response` | proxy 转换后的响应 |

### 表 2：`token_stats` — Token 消耗统计

每个请求一行统一格式记录。

```sql
CREATE TABLE IF NOT EXISTS token_stats (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id         TEXT NOT NULL,      -- 关联 debug_log
    agent              TEXT NOT NULL,      -- "codex"（将来可扩展）
    model              TEXT NOT NULL,      -- 原始模型
    target_model       TEXT NOT NULL,      -- 映射后的目标模型
    request_ts         TEXT NOT NULL,      -- ISO8601
    duration_ms        INTEGER,
    input_tokens       INTEGER DEFAULT 0,
    output_tokens      INTEGER DEFAULT 0,
    cached_read_tokens INTEGER DEFAULT 0,
    cached_write_tokens INTEGER DEFAULT 0,
    cost_usd           REAL DEFAULT 0.0,
    status             TEXT DEFAULT 'completed',  -- completed / failed / incomplete
    created_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_token_request_id ON token_stats(request_id);
CREATE INDEX IF NOT EXISTS idx_token_request_ts ON token_stats(request_ts);
```

## request_logger.py 模块接口

### RequestLogger 类

```python
class RequestLogger:
    def __init__(self, db_path: Path, debug_retention_days: int = 7):
        """初始化：创建数据库文件、建表、清理过期数据。"""

    def close(self):
        """关闭数据库连接。"""

    def log_raw_request(self, request_id: str, model: str, target: str, body: dict):
        """阶段 1：记录 agent 原始请求。stage = raw_request"""

    def log_converted_request(self, request_id: str, model: str, target: str, body: dict):
        """阶段 2：记录 proxy 转换后的请求。stage = converted_request"""

    def log_upstream_response(self, request_id: str, status_code: int, body: str | dict, duration_ms: int):
        """阶段 3：记录上游原始响应。stage = upstream_response"""

    def log_converted_response(self, request_id: str, model: str, target: str, body: dict):
        """阶段 4：记录 proxy 转换后的响应。stage = converted_response"""

    def log_token_stats(self, request_id: str, agent: str, model: str, target_model: str,
                        request_ts: str, duration_ms: int, input_tokens: int,
                        output_tokens: int, cached_read: int, cached_write: int,
                        cost_usd: float, status: str):
        """写入 Token 统计记录。"""

    def _cleanup_expired(self):
        """启动时清理超过 retention_days 的记录。"""
```

### 全局单例

```python
# request_logger.py 模块级
_logger: Optional[RequestLogger] = None

def init_logger(db_path: Path, retention_days: int) -> RequestLogger:
    """初始化全局 logger 实例（仅在 proxy 启动时调用一次）。"""

def get_logger() -> RequestLogger:
    """获取全局 logger 实例。"""
```

## proxy.py 集成点

### `_handle_responses`（非流式 + 流式共用入口）

```python
# 1. 收到原始请求后
request_id = str(uuid.uuid4())
logger.log_raw_request(request_id, model_name, target, body)

# 2. responses_to_chat 转换后
logger.log_converted_request(request_id, model_name, target, chat_body)
```

### `_forward_non_streaming`

```python
# 3. 收到上游响应后
logger.log_upstream_response(request_id, resp.status, resp_body, duration_ms)

# 4. chat_to_responses 转换后
logger.log_converted_response(request_id, model_name, target, responses_response)
```

### `_forward_streaming`

流式场景下上游响应是 SSE 流，需要在流结束后统一写入：

```python
# 流结束后：
# - log_upstream_response(): 记录原始 SSE 流文本（截断到合理大小，如 50KB）
# - log_token_stats(): 从 final response.completed 事件中提取 usage
# - log_converted_response(): 记录不完整，标记为 streaming（或跳过此 step）
```

## 配置

`proxy_config.yaml` 新增 `logging` 块：

```yaml
logging:
  debug_retention_days: 7     # debug_log 保留天数
  log_dir: "data"             # access_log.db 存放目录（相对于 proxy.py 所在目录）
```

## 错误策略

所有日志写入函数内部用 `try/except Exception` 包裹：

```python
def log_raw_request(self, ...):
    try:
        self._execute("INSERT INTO debug_log ...", (...))
    except Exception as e:
        logging.warning(f"日志写入失败: {e}")
```

不抛出异常，不阻断 proxy 转发。

## 清理策略

启动时执行：

```sql
DELETE FROM debug_log WHERE created_at < datetime('now', '-N days');
DELETE FROM token_stats WHERE request_ts < datetime('now', '-N days');
```

N 来自 `logging.debug_retention_days` 配置。

## request_id 生成

使用 `uuid4().hex[:16]` 生成 16 位短 UUID，既足够唯一又便于日志中阅读。

## 测试策略

- `test_request_logger.py` — 独立测试：
  - DB 初始化 + 表创建
  - 5 个写入函数各一条记录验证
  - `_cleanup_expired` 清理逻辑验证
  - 写入失败不抛异常验证
  - 使用临时数据库文件（`tempfile`），不污染生产数据
