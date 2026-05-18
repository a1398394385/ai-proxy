# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**生成时间:** 2026-05-18
**分支:** main

## 概述

两个独立 HTTP 服务，由 `./server.sh` 统一管理，纯 Python 标准库，0 第三方依赖。

| 服务 | 入口 | 端口 | 用途 |
|------|------|------|------|
| Hermes Data Browser | `server/` 包 → `server.py` | 18742 | Web UI — Token 统计 / 模型路由 CRUD / 计费管理 / Fact Store |
| AI Proxy | `proxy/` 包 → `proxy.py` | 48743 | 统一代理 — 协议转换 + 透传（Responses / Messages / Chat Completions 三种格式 NxM 互转）|

## 子目录文档

各子目录有独立的 CLAUDE.md，处理该目录内的代码时应优先阅读：

| 目录 | 文档 | 内容 |
|------|------|------|
| `proxy/` | [proxy/CLAUDE.md](proxy/CLAUDE.md) | 协议转换管线、配置管理、请求日志、架构分层 |
| `server/` | [server/CLAUDE.md](server/CLAUDE.md) | Data Browser 后端 API、分发机制、DB 上下文 |
| `static/` | [static/CLAUDE.md](static/CLAUDE.md) | 前端 SPA 架构、ES Module 图、事件系统、CSS 主题 |
| `test/` | [test/CLAUDE.md](test/CLAUDE.md) | 测试模式、Mock 约定、文件速查 |

## 项目结构

```
├── proxy.py                    # 瘦入口 — ThreadedHTTPServer + 日志轮转 (96行)
├── server.py                   # 瘦入口 — from server import main (3行)
├── server.sh                   # 服务管理（start/stop/status/restart）
├── proxy_config.yaml           # 端口 / 日志 / 上游
├── stats_service.py            # Token 统计查询服务 — 三源合并 (1760行)
│
├── server/                     # Hermes Data Browser 核心包 (8 文件)
│   ├── __init__.py             # re-export + main() + StatsService 初始化
│   ├── handler.py              # HermesDataHandler 分发表 (87行) — 纯分发
│   ├── common.py               # json_response / _read_json / DB 上下文管理器
│   ├── config_api.py           # upstreams/models/routes CRUD + 上游检测 + reload
│   ├── fact_api.py             # facts/categories/stats + FTS 搜索
│   ├── token_api.py            # 委托 stats_service 的 5 类查询端点
│   ├── pricing_api.py          # pricing CRUD + 缓存失效
│   └── static_files.py         # 静态文件服务（GET 链最末 fallback）
│
├── proxy/                      # AI Proxy 核心包 (18 文件)
│   ├── __init__.py             # 公共 API re-export
│   ├── handler.py              # 统一 ProxyHandler (1112行) — do_POST 路由/透传/转换
│   ├── common.py               # CONFIG / resolve_model / _create_upstream_conn
│   ├── paths.py                # DATA_DIR / DATA_DB 路径常量
│   ├── schema.py               # 所有 SQLite 表定义（单一真相来源）
│   ├── config_manager.py       # ConfigDB + ConfigCache + Migrations (v0→v7)
│   ├── transform/              # NxM 协议转换矩阵
│   │   ├── registry.py         # (client_fmt, upstream_fmt) → 转换函数 注册表
│   │   ├── router.py           # TransformRouter 分发类
│   │   ├── request/            # 请求转换（→ Chat Completions）
│   │   │   ├── _utils.py       # _fix_tool_message_order, _merge_consecutive_assistants
│   │   │   ├── anthropic.py    # Messages → Chat
│   │   │   └── responses.py    # Responses → Chat
│   │   └── response/           # 响应转换（Chat Completions →）
│   │       ├── anthropic.py    # Chat → Messages（含流式 SSE）
│   │       └── responses.py    # Chat → Responses（含流式 SSE）
│   ├── sse_utils.py            # SSE 事件格式化 + 解析
│   ├── request_logger.py       # 四阶段日志（含 request_path / session_id）
│   ├── token_stats.py          # _extract_tokens + record_token_stats
│   ├── pricing_manager.py      # PricingDB — 计费种子数据 + CRUD
│   ├── response_store.py       # LRU+TTL 内存缓存（previous_response_id 多轮）
│   └── agent_detector.py       # detect_subagent — 子代理请求识别
│
├── static/                     # Web UI — 纯 ES Module SPA，无框架
│   ├── index.html              # 5 Tab 导航入口
│   ├── css/ [base, facts, models, pricing, routes, tokens]
│   └── js/
│       ├── app.js              # ES Module 入口 — 加载器 + 生命周期
│       ├── core.js             # 主题 / 事件总线 bus / 动作委托 / API / 模态框
│       └── pages/ [facts, tokens, upstreams, pricing, routes]  # 5 活跃页面
│
├── test/                       # 18 文件，约 9400 行（unittest.TestCase）
│   ├── test_transform.py       # Responses 转换 (2349行, 138 tests)
│   ├── test_transform_anthropic.py  # Anthropic 转换 (796行)
│   ├── test_transform_router.py     # TransformRouter 注册表/分发
│   ├── test_sse_utils.py       # SSE 事件格式化
│   ├── test_handler.py         # ProxyHandler 路由/透传/转换
│   ├── test_proxy_pass_through.py  # 透传路径
│   ├── test_proxy_logger_integration.py  # proxy+logger 四阶段集成
│   ├── test_config_manager.py  # ConfigDB CRUD + 迁移
│   ├── test_config_integration.py  # ConfigDB → ConfigCache 全流程
│   ├── test_proxy_config.py    # proxy_config.yaml 加载校验
│   ├── test_request_logger.py  # RequestLogger DB 操作
│   ├── test_token_stats.py     # Token 提取（三种 API 格式）
│   ├── test_stats_service.py   # StatsService 三源合并 (1638行)
│   ├── test_pricing_manager.py # PricingDB CRUD
│   ├── test_response_store.py  # ResponseStore LRU/TTL
│   ├── test_agent_detector.py  # 子代理检测
│   ├── test_e2e_smoke.py       # 端对端冒烟（默认跳过）
│   └── mock_server.py          # Chat Completions SSE mock
│
└── docs/                       # issues/ + superpowers/ 设计文稿
```

## 开发命令

```bash
./server.sh start     # 启动 Data Browser + AI Proxy
./server.sh stop      # 停止服务
./server.sh restart   # 重启（修改代码后必须执行，无热重载）
./server.sh status    # 查看状态

python3 -m pytest test/ -q                              # 全量 530+ tests
python3 -m pytest test/test_transform.py -q             # 转换
python3 -m pytest test/test_config_manager.py -q        # ConfigDB
python3 -m pytest test/test_handler.py -q -k "test名"   # 指定测试

python3 test/quick_test.py                              # Token 快速冒烟
```

## 代理请求流程

**设计愿景**：Responses / Messages / Chat Completions 三种格式 NxM 互转，通过 Chat Completions 作为中间格式（非中枢协议，未来可扩展任意格式对）。

```
客户端 POST /v1/responses 或 /v1/messages 或 /v1/chat/completions
  ↓
ProxyHandler.do_POST() → 路径解析 → request_type
  ↓
detect_subagent(body) → 子代理？走 agent_routes 优先
  ↓
ConfigCache.resolve(model, request_type) → upstream_cfg
  │   优先级: source 精确匹配 → * fallback（同 request_type）
  │   跳过 is_active=0 的上游
  ↓  缓存通过 POST /admin/reload 主动失效
request_type == upstream_cfg.format ?
  ├─ 是 → _handle_passthrough() — 原样转发 + 日志 + token_stats
  └─ 否 → _handle_convert()    — TransformRouter 按 (client_fmt, upstream_fmt) 分发
              ↓
           非流式：上游响应 → 客户端格式响应
           流式：  上游 SSE → 客户端格式 SSE（逐 yield）
              ↓ record_token_stats() → DATA_DB.token_stats
              ↓ ResponseStore.put() → 供 previous_response_id 使用
```

## Token 统计：三源合并

StatsService (`stats_service.py`) 从三个独立数据源聚合 Token 使用数据：

| 数据源 | 表 | 来源 |
|--------|-----|------|
| DATA_DB.token_stats | proxy 请求日志 | 本项目 proxy |
| state.db.sessions | Hermes AI 会话 | Hermes Agent |
| opencode.db.message | OpenCode 消息 | OpenCode CLI |

对外 5 个 `fetch_*` 方法：`fetch_summary()` / `fetch_by_model()` / `fetch_by_upstream()` / `fetch_trend()` / `fetch_requests()`。

成本计算通过 `DATA_DB.model_pricing` 表，支持 USD/RMB 双币种和 multiplier。

## 数据库

**本项目数据库** `~/.ai-agent-tools/data/access_log.db`（`proxy.paths.DATA_DB`），存放运行时数据。

| 表 | 用途 |
|----|------|
| `token_stats` | proxy 请求日志（request_id, model, tokens, duration, status） |
| `debug_log` | 四阶段请求/响应日志（含 request_path, session_id） |
| `upstreams` | 上游配置（base_url, api_key, format, is_active） |
| `target_models` | 模型注册（name, upstream_id, multimodal） |
| `model_routes` | 路由映射（source → target_model, request_type） |
| `agent_routes` | 子代理路由（v7 新增） |
| `model_pricing` | 计费规则（input/output/cache 成本，USD/RMB） |
| `schema_version` | 迁移版本号 |

**外部数据库**（只读）：

| 数据库 | 表 | 用途 |
|--------|-----|------|
| `~/.hermes/memory_store.db` | `facts`, `facts_fts`, `entities`, `fact_entities` | Fact Store |
| `~/.hermes/state.db` | `sessions` | Hermes AI 会话 |
| `~/.local/share/opencode/opencode.db` | `message` | OpenCode 消息 |

**SQLite 规则**：每次查询新建连接，用完关闭。`PRAGMA foreign_keys = ON`。

## 模型路由

`ConfigCache` 从 `model_routes` + `target_models` + `upstreams` 三表关联读取路由。按需加载（不过期），通过 `reload()` 主动失效。

**匹配优先级**：`source` 精确匹配 → `*` fallback（逐 request_type）。`is_active=0` 的上游自动跳过。

`proxy_config.yaml` 的 `model_map` 段已不生效（由 data db 配置取代）。

## 约定

### 开发流程
1. **TDD** — 先写失败测试 → 最小实现 → 通过 → commit
2. **修改后必须重启** — `./server.sh restart`（无热重载）
3. **不破坏现有功能** — 确认全量测试通过
4. **commit message** — 中文，`feat:`/`fix:`/`refactor:`/`test:`/`docs:`/`chore:`，禁止 `--no-verify`
5. **先设计后实现** — 复杂改动写 `docs/superpowers/specs/`

### 测试
- 继承 `unittest.TestCase`，pytest 运行（无 pytest fixtures/conftest/markers）
- DB 测试用 `tempfile.TemporaryDirectory` 隔离
- 前端 UI 测试用 Playwright MCP：`mcp__playwright__*` 工具

### 代码风格
- 纯 Python 标准库，0 第三方依赖
- Re-export：`proxy/__init__.py` 统一公开 API（`# noqa: F401`）
- 注释分隔：`# ─── 标题 ───`
- 无 linter 配置

### 导入约定
- proxy 包内：相对导入 `from .common import CONFIG`
- proxy 包外：绝对导入 `from proxy.handler import ProxyHandler`
- 共享路径：`from proxy.paths import DATA_DB`（server/stats_service 共用）
- 协议转换统一走 `from proxy.transform import ...`

## 注意事项

- **无 CI/CD**：无 GitHub Actions、Makefile 等自动化
- **透传/转换判定**：取决于 `request_type == upstream_cfg.format`，不按路径固定
- **proxy Layer 0**：`schema`, `paths`, `sse_utils`, `token_stats`, `config_manager`, `request_logger`, `response_store`, `pricing_manager`, `agent_detector`, `transform/registry`, `transform/router` — 零内部依赖
- **config 与 token_stats 共享 data db**：无独立的 config.db 文件
- **测试 "config.db" 是临时文件**：仅测试用，不代表生产路径
- **server/handler.py 是纯分发器**：无业务逻辑，所有端点逻辑在各 `*_api.py` 中
- **server 分发顺序重要**：`static_files` 必须排在 GET 链最末（始终返回 True）
- **前端无框架**：纯 ES Module + 事件总线 + 动作委托，无 React/Vue/打包
- **`debug_log.request_path`**：记录上下游完整 URL
- **agent_routes**（v7）：子代理专项路由表，`detect_subagent()` 触发时优先使用
