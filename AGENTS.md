# AGENTS.md

## 项目概述

本项目包含两个独立 HTTP 服务，统一由 `./server.sh` 管理：

| 服务 | 文件 | 端口 | 用途 |
|------|------|------|------|
| Hermes Data Browser | `server.py` | 18742 | Web UI — Fact Store 浏览 / Token 统计 / 动态模型路由管理 |
| Codex Proxy | `proxy.py` | 48743 | OpenAI Responses API + Anthropic Messages API → Chat Completions 代理 |

## 开发命令

```bash
# 服务管理（两个服务同时管理）
./server.sh start     # 启动 Data Browser + Codex Proxy
./server.sh stop      # 停止两个服务
./server.sh status    # 查看状态
./server.sh restart   # 重启（修改代码后必须执行，无热重载）

# 测试
cd /Users/xys/.hermes/fact-store-browser
python3 -m pytest test/ -q                    # 全量（333 tests，约 17s）
python3 -m pytest test/test_transform.py -q  # 单文件

# 快速 API 冒烟
python3 quick_test.py
```

## 文件模块

### Codex Proxy 核心

| 文件 | 行数 | 职责 |
|------|------|------|
| `proxy.py` | 800 | ThreadedHTTPServer — HTTP 路由、请求转发、日志串联入口 |
| `transform.py` | 41 | Re-export shim — 对外统一公共接口（不含实现） |
| `transform_responses.py` | 891 | OpenAI Responses API ↔ Chat Completions 转换，含 `CodexStreamConverter` 状态机 |
| `transform_anthropic.py` | 510 | Anthropic Messages API ↔ Chat Completions 转换，含 `AnthropicStreamState` 状态机 |
| `sse_utils.py` | 13 | `_format_sse_event()` — 两个转换模块共用的 SSE 格式化 |
| `config_manager.py` | 563 | `ConfigDB`（SQLite CRUD）+ `ConfigCache`（内存缓存，TTL 5s）+ 内联 YAML 解析器 |
| `request_logger.py` | 206 | 四阶段请求/响应日志，写入 `data/access_log.db`（SQLite WAL） |
| `token_stats.py` | 157 | Token 统计写入，三种 usage 格式兼容（Anthropic / OpenAI Chat / OpenAI Responses） |
| `response_store.py` | 72 | 内存 `ResponseStore`（LRU + TTL），支持 `previous_response_id` 多轮对话 |
| `proxy_config.yaml` | 37 | proxy host/port、upstream 静态连接参数、logging 设置 |

### Data Browser

| 文件 | 职责 |
|------|------|
| `server.py` | HTTP server — Fact Store API + Token 统计 API + ConfigDB 管理 API（上游/模型/路由 CRUD）|
| `static/index.html` | 单文件前端（~61K）— vanilla JS，Facts / Tokens / Settings 三 Tab |

## 数据库

| 数据库 | 路径 | 核心表 | 用途 |
|--------|------|--------|------|
| memory_store.db | `~/.hermes/memory_store.db` | `facts`, `facts_fts`, `entities`, `fact_entities` | Fact Store |
| state.db | `~/.hermes/state.db` | `sessions`, `messages` | Hermes 会话 Token 统计 |
| cc-switch.db | `~/.cc-switch/cc-switch.db` | `model_pricing` | 模型计费规则（5min 缓存）|
| config.db | `~/.hermes/config.db` | `upstreams`, `target_models`, `model_routes` | 动态模型路由配置 |
| access_log.db | `data/access_log.db` | `debug_log`, `token_stats` | 代理请求日志（7天保留）|

### config.db 表结构
```sql
upstreams      (id, base_url, api_key, timeout, connect_timeout, ssl_verify, retry, is_active, is_default)
target_models  (id, name, upstream_id, multimodal, format)
model_routes   (id, source, target_model_id)   -- source 支持精确名称或 "*" fallback
```

### memory_store.db 表结构
```sql
facts         (fact_id, content, category, tags, trust_score, helpful_count, created_at)
facts_fts     -- FTS5 虚拟表（content + tags 全文索引）
entities      (entity_id, name, entity_type)
fact_entities (fact_id, entity_id)  -- 多对多
```

**规则：每次查询新建 SQLite 连接，用完立即关闭（无连接池）。**

## 代理 API 端点（port 48743）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查，返回 `{status: ok, pid}` |
| GET | `/v1/models` | 返回 config.db 中所有非 `*` 源模型列表 |
| GET | `/v1/responses` | 返回 426（触发 Codex CLI 回退到 POST+SSE 模式）|
| POST | `/v1/responses` | OpenAI Responses API（Codex CLI 主路径）|
| POST | `/v1/responses/compact` | 同上 |
| POST | `/v1/messages` | Anthropic Messages API（Claude Code）|
| POST | `/admin/reload` | 强制刷新 ConfigCache（仅 127.0.0.1/::1）|

## 代理请求流程

```
客户端（Codex/Claude）
  ↓  POST /v1/responses 或 /v1/messages
ProxyHandler
  ↓  ConfigCache.resolve(model) → upstream_cfg（TTL 5s，自动刷新 config.db）
  ↓  responses_to_chat() / anthropic_to_chat()   ← 请求格式转换
  ↓  （可选）previous_response_id → ResponseStore.get() → 注入历史 messages
  ↓  HTTP POST → upstream /chat/completions
非流式：chat_to_responses() / chat_to_anthropic()
流式：  create_codex_sse_stream() / create_anthropic_sse_stream()（逐事件 yield）
  ↓  record_token_stats() → data/access_log.db
  ↓  ResponseStore.put() → 内存（供下轮 previous_response_id 使用）
```

## 动态模型路由

`ConfigCache` 从 `~/.hermes/config.db` 读取路由（TTL 5s），是代理的模型解析核心。

**路由优先级**：精确匹配 `source` → `*` fallback；`is_active=0` 的上游自动跳过。

`proxy_config.yaml` 中的 `model_map` 段已不生效（由 config.db 取代），仅作历史参考。

配置变更后通过 `POST /admin/reload` 立即生效（清零 `_loaded_at` 触发下次强制刷新）。

## SSE 格式要求

所有 SSE 事件由 `sse_utils._format_sse_event(event_type, data)` 生成：
- 将 `event_type` 注入为 data JSON 顶层 `"type"` 字段（Codex `ResponsesStreamEvent` 要求）
- 统一 `separators=(',', ':')` 紧凑格式

**流式状态机**：
- `CodexStreamConverter`（`transform_responses.py`）— 维护 text / refusal / reasoning / tool_calls 各块的打开/关闭状态；`output_item.added` 延迟到 `call_id + name` 均就绪后才发送
- `AnthropicStreamState`（`transform_anthropic.py`）— 维护 content block index，工具调用 start 延迟到 `id + name` 就绪

## 测试

```
test/
├── test_transform.py                 # Responses API 转换 + SSE 格式（最大，90K）
├── test_transform_anthropic.py       # Anthropic Messages 转换（30K）
├── test_config_manager.py            # ConfigDB / ConfigCache 单元测试（15K）
├── test_request_logger.py            # 请求日志单元测试（16K）
├── test_proxy_logger_integration.py  # proxy + logger 集成（17K）
├── test_response_store.py            # ResponseStore LRU/TTL（17K）
├── test_token_stats.py               # Token 统计写入（9K）
├── test_sse_utils.py                 # SSE 格式化（5K）
├── test_proxy_config.py              # 配置加载校验（3.5K）
├── test_config_integration.py        # 配置集成（3.4K）
└── test_e2e_smoke.py                 # 端对端冒烟，启动真实 proxy（6.7K）
```

**当前：333 tests passing**

## 开发规范

### 开发流程

1. **TDD** — 先写失败测试 → 实现最小代码 → 验证通过 → commit
2. **修改后必须重启** — `./server.sh restart`（无热重载）
3. **不破坏现有功能** — 任何修改后确认 333 tests 仍全部通过
4. **commit message 用中文** — 解释 "why" 而非 "what"，`--no-verify` 禁止使用

### 协作规范

- **先计划后实现** — 复杂改动先写设计文稿，用户审阅批准后再执行
- **每个 Task 完成后审阅再 commit** — 确认改动正确
