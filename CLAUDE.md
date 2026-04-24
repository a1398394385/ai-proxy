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

1. **零外部依赖** — 只用 Python 标准库，不要引入 pip 包
2. **修改后必须重启** — 标准 HTTP server 不支持热重载，改代码后运行 `./restart_server.sh`
3. **计费规则缓存** — 修改 cc-switch.db 后需等 5 分钟或重启服务
4. **Token 数据延迟** — 运行中 session 的 token 显示为 0，结束后才更新（Hermes 设计机制）
5. **成本为 0 的两种情况** — 模型不在 cc-switch.db 或 token 为 0
6. **数据库连接** — 每次查询新建连接，用完立即关闭（无连接池）

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
