**Proxy 核心包。** 协议转换 + 智能透传的统一实现。

## 结构

```
proxy/
├── __init__.py               # 公共 API re-export 入口 (41行)
├── handler.py                # ★ 统一 ProxyHandler — 路由/透传/转换 (1057行)
├── common.py                 # 共享 — CONFIG/模型解析/上游连接 (191行)
├── config_manager.py         # ConfigDB + ConfigCache TTL 5s + Migrations (1041行)
├── transform.py              # Re-export shim → 分发到具体转换模块 (41行)
├── transform_responses.py    # Responses API ↔ Chat Completions + 状态机 (931行)
├── transform_anthropic.py    # Anthropic Messages ↔ Chat Completions + 状态机 (536行)
├── sse_utils.py              # _format_sse_event() 共用格式化 (13行)
├── request_logger.py         # 四阶段请求/响应日志 (233行)
├── token_stats.py            # Token 统计解析+写入 (157行)
└── response_store.py         # 内存 ResponseStore LRU+TTL (72行)
```

## 依赖层级

| 层级 | 文件 | 内部依赖 |
|------|------|----------|
| **第 0 层** | `sse_utils` `token_stats` `response_store` `config_manager` `request_logger` | 零 |
| **第 1 层** | `transform_responses` → `token_stats` `sse_utils` | 第 0 层 |
| | `transform_anthropic` → `sse_utils` `transform_responses` | 第 0-1 层 |
| **第 2 层** | `common` → `config_manager` `request_logger` `token_stats` | 第 0 层 |
| | `transform` → 第 1 层全部 | 第 0-1 层 |
| **第 3 层** | `handler` → `common` `transform` `request_logger` `token_stats` | 全依赖 |
| **第 4 层** | `__init__.py` → re-export 以上所有公共 API | 全依赖 |

## 代码速查

| 任务 | 位置 | 备注 |
|------|------|------|
| 添加新 API 路径 | `handler.py` → `do_POST()` path 映射 | 设置 request_type |
| 修改响应格式转换 | `transform_responses.py` 或 `transform_anthropic.py` | 含状态机 |
| 修改上游连接逻辑 | `common.py` → `_create_upstream_conn()` | 支持 HTTP 代理 |
| 新增数据库表 | `config_manager.py` → `Migrations` | version 递增 |
| 修改日志格式 | `request_logger.py` → `RequestLogger` | 四阶段记录 |
| 修改 Token 解析 | `token_stats.py` → `_extract_tokens()` | 三种 usage 格式 |

## 核心路由逻辑

`handler.py` → `ProxyHandler.do_POST()`：

```
路径解析 → request_type (responses/messages/chat_completions)
  ↓
ConfigCache.resolve(model) → upstream_cfg
  ↓
request_type == upstream_cfg.format ?
  ├─ 是 → _handle_passthrough() — 原样转发
  └─ 否 → _handle_convert()    — Chat Completions 中间格式转换
```

**透传/转换判定不按路径固定**，取决于运行时上游配置的 `format` 字段。

## 约定

- **导入**：包内用相对导入 `from .common import CONFIG`；包外用绝对导入 `from proxy.handler import ProxyHandler`
- **禁止直接导入**：不从 `proxy.transform_responses` 直接导入，走 `proxy.transform` shim
- **Re-export**：`__init__.py` 统一公开 API，用 `# noqa: F401` 标记
- **注释分隔**：`# ─── 标题 ───` 格式
- **SSE**：所有事件通过 `_format_sse_event()` 生成，`event_type` 注入为 data JSON 顶层 `"type"` 字段
- **工具调用延迟**：`output_item.added` 延迟到 `call_id + name` 均就绪
- **无外部依赖**：纯 Python 标准库

## 反模式

- ❌ 在 `handler.py` 以外处理 HTTP 路由分发
- ❌ 绕过 `transform.py` shim 直接导入 `transform_responses` 或 `transform_anthropic`
- ❌ 在 `common.py` 中引入 handler.py 依赖（会造成循环导入）
- ❌ 在转换模块中硬编码上游 URL — 使用 `CONFIG` + `config_cache`
