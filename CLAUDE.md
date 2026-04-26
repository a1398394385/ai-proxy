# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Hermes Data Browser — 本地 Web 工具，用于浏览 Hermes Agent 的 Fact Store 和 Token 使用统计。纯 Python 标准库实现，无外部依赖。

**访问地址**: http://127.0.0.1:18742

## 开发命令

```bash
# 服务管理
./server.sh start           # 启动服务
./server.sh stop            # 停止服务
./server.sh status          # 查看运行状态

# 修改代码后重启（必须，因为无热重载）
./restart_server.sh         # 或 python3 auto_restart.py

# 快速 API 冒烟测试
python3 quick_test.py
```

## 架构关键点

### 三数据库架构

| 数据库 | 路径 | 核心表 | 用途 |
|--------|------|--------|------|
| memory_store.db | `~/.hermes/memory_store.db` | `facts`, `facts_fts`, `entities`, `fact_entities` | Fact Store 数据 |
| state.db | `~/.hermes/state.db` | `sessions`, `messages` | Session 和 Token 统计 |
| cc-switch.db | `~/.cc-switch/cc-switch.db` | `model_pricing` | 模型计费规则 |

### server.py 核心模块

**计费计算** (line 34-82):
- `get_model_pricing()`: 从 cc-switch.db 读取计费规则，**缓存 5 分钟**
- `calculate_cost()`: 根据模型计费规则计算 USD 成本，无规则返回 0

**Token 统计补偿机制** (line 165-230):
- `get_token_stats()`: LEFT JOIN messages 表补偿 session token 为 0 的情况
- 如果 `input_tokens == 0` 但有 `msg_tokens`，使用 `msg_tokens` 作为 input

**趋势数据完整性** (line 280-498):
- `get_daily_token_trend()`: 固定点数（day=24h, week=7d, month=30d）
- **无数据时间点补 0**，确保时间序列完整
- 按模型分组查询后聚合，重新计算成本

**API 路由** (line 501-727):
- Fact Store CRUD: `/api/facts`, `/api/facts/:id`, `/api/categories`, `/api/stats`
- Token 统计: `/api/token_stats`, `/api/token_stats/by_model`, `/api/token_stats/trend`, `/api/token_stats/summary`
- 静态文件服务: `/` → `static/index.html`

### 前端架构

- 单文件应用: `static/index.html` (61.6K)
- 技术栈: vanilla JS + CSS 变量（暗色/亮色主题）+ SVG 面积图（无图表库）
- 三个 Tab: Facts / Tokens / Settings

## 开发注意事项

1. **修改后必须重启** — 标准 HTTP server 不支持热重载，改代码后运行 `./restart_server.sh`
2. **计费规则缓存** — 修改 cc-switch.db 后需等 5 分钟或重启服务
3. **Token 数据延迟** — 运行中 session 的 token 显示为 0，结束后才更新（Hermes 设计机制）
4. **成本为 0 的两种情况** — 模型不在 cc-switch.db 或 token 为 0
5. **数据库连接** — 每次查询新建连接，用完立即关闭（无连接池）

## 数据库表结构参考

### memory_store.db
```sql
facts (fact_id, content, category, tags, trust_score, helpful_count, created_at)
facts_fts (FTS5 虚拟表，content 和 tags 的全文索引)
entities (entity_id, name, entity_type)
fact_entities (fact_id, entity_id)  -- 多对多关联
```

### state.db
```sql
sessions (id, model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, message_count, started_at)
messages (id, session_id, token_count, created_at)
```

### cc-switch.db
```sql
model_pricing (model_id, input_cost_per_million, output_cost_per_million,
               cache_read_cost_per_million, cache_creation_cost_per_million)
```

## API 端点完整列表

详见 AGENTS.md 的 API 端点章节（已详细记录）。

## 项目扩展：Codex Proxy 代理服务器

本项目已扩展为包含 Codex Proxy 代理服务器，转发 OpenAI Responses API → Chat Completions API。

### 代理架构

| 文件 | 用途 |
|------|------|
| `proxy.py` | ThreadedHTTPServer，处理 Responses API 请求，转发到上游 LiteLLM 网关 |
| `transform.py` | 纯转换逻辑：`responses_to_chat()`（请求转换）+ `chat_to_responses()`（非流式响应）+ `create_codex_sse_stream()`（流式 SSE 转换） |
| `request_logger.py` | 请求日志模块，记录四个阶段的请求/响应数据到 `data/access_log.db`（SQLite WAL 模式） |
| `proxy_config.yaml` | 代理配置：host/port、upstream 连接参数、model_map 映射、logging 设置 |

**启动命令**: `./server.sh start`（proxy 在 48743 端口）

### 流式 SSE 格式要求

Codex CLI 的 `ResponsesStreamEvent` 要求所有 SSE 事件 data JSON 必须包含顶层 `"type"` 字段。`_format_sse_event(event_type, data)` 统一处理：
- 自动注入 `"type"` 字段
- 响应级事件（created/completed/failed/incomplete）用 `"response"` 键包裹
- Item 事件（output_item.added/output_item.done）用 `"item"` 键包裹
- 统一 `separators=(',', ':')` 紧凑格式

### 测试目录结构

```
test/
├── test_transform.py              # 转换逻辑 + SSE 格式测试
├── test_proxy_config.py           # 配置加载校验测试
├── test_proxy_logger_integration.py  # proxy + logger 集成冒烟
├── test_request_logger.py         # request_logger 单元测试
└── test_e2e_smoke.py              # 端对端冒烟测试（启动真实 proxy）
```

**运行测试**: `cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/ -v`

## 工作规范与要求

以下要求由用户在协作过程中明确提出，必须在所有开发工作中遵守：

### 开发流程

1. **严格 TDD 循环** — 先写失败测试 → 实现最小代码 → 验证通过 → commit
2. **每个 Task 完成后先审阅再 commit** — 确认改动正确，更新 plan_tracking.md
3. **git commit 使用中文** — commit message 解释 "why" 而非 "what"
4. **不破坏原有转发逻辑** — 任何修改必须确保现有功能不受影响
5. **提交前更新 plan_tracking.md** — 每个 Task/Step 完成后更新状态

### 代码原则

- **DRY** — 不重复，提取公共逻辑
- **YAGNI** — 不做不需要的功能，不预设未来需求
- **纯标准库** — 无外部依赖，所有代码使用 Python 标准库
- **函数/类单一职责** — 不写万能函数
- **修改后必须重启** — 标准 HTTP server 无热重载，改代码后运行 `./restart_server.sh`

### 协作原则

- **先计划后实现** — 复杂改动先写设计文稿和实施计划，用户审阅批准后再执行
- **保持计划同步** — 实施过程中更新 plan_tracking.md 跟踪进度
- **设计文稿先行** — 复杂功能的格式对齐、接口变更先写设计文稿确定方案
