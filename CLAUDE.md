# CLAUDE.md

**生成时间:** 2026-05-13
**提交:** f690a49
**分支:** main

## 概述

两个独立 HTTP 服务，由 `./server.sh` 统一管理，纯 Python 标准库 + openai SDK（转换路径）。

| 服务 | 入口 | 端口 | 用途 |
|------|------|------|------|
| Hermes Data Browser | `server/` 包 → `server.py` | 18742 | Web UI — Token 统计 / 模型路由 CRUD / 计费管理 / Fact Store |
| AI Proxy | `proxy/` 包 → `proxy.py` | 48743 | 统一代理 — 协议转换 + 透传（OpenAI Responses / Anthropic Messages → Chat Completions）|

## 项目结构

```
├── proxy.py                    # 瘦入口 — ThreadedHTTPServer + 日志轮转 (96行)
├── server.py                   # 瘦入口 — from server import main (3行)
├── server.sh                   # 服务管理（start/stop/status/restart）
├── proxy_config.yaml           # 端口 / 日志 / 上游 / 旧 model_map
├── quick_test.py               # Token 统计冒烟
│
├── stats_service.py            # ★ Token 统计查询服务 — 三源合并 (1760行)
│
├── server/                     # ★ Hermes Data Browser 核心包 (8 文件)
│   ├── __init__.py             # re-export + main() + StatsService 初始化
│   ├── handler.py              # HermesDataHandler 分发表 (87行)
│   ├── common.py               # JSON/DB 上下文/路径常量
│   ├── config_api.py           # upstreams/models/routes CRUD + 上游检测
│   ├── fact_api.py             # facts/categories/stats
│   ├── token_api.py            # token_stats 系列端点
│   ├── pricing_api.py          # pricing CRUD
│   └── static_files.py         # 静态文件服务
│
├── proxy/                      # ★ AI Proxy 核心包 (15 文件)
│   ├── __init__.py             # 公共 API re-export
│   ├── handler.py              # 统一 ProxyHandler (1112行)
│   ├── common.py               # 配置/模型解析/上游连接
│   ├── paths.py                # 统一路径管理 DATA_DB/DATA_DIR
│   ├── config_manager.py       # ConfigDB + ConfigCache + Migrations (1171行)
│   ├── transform.py            # Re-export shim
│   ├── transform_router.py     # TransformRouter — 协议转换路由（新增）
│   ├── upstream_driver.py      # UpstreamDriver — SDK 上游驱动（新增）
│   ├── transform_responses.py  # Responses ↔ Chat Completions (1012行)
│   ├── transform_anthropic.py  # Messages ↔ Chat Completions (536行)
│   ├── sse_utils.py            # SSE 事件格式化
│   ├── request_logger.py       # 四阶段日志（含 request_path）
│   ├── token_stats.py          # Token 解析+写入
│   ├── pricing_manager.py      # PricingDB — 计费种子数据 + CRUD
│   └── response_store.py       # LRU+TTL 内存缓存
│
├── static/                     # Web UI
│   ├── index.html              # 6 Tab 导航入口
│   ├── css/ [base, facts, models, pricing, routes, tokens]
│   └── js/
│       ├── app.js              # ES Module 入口
│       ├── core.js             # 主题/事件总线/API
│       └── pages/ [facts, tokens, upstreams, pricing, routes, models.js(死)]
│
├── test/                       # 531 tests (16 文件)
│   ├── test_transform.py       # Responses 转换 (138 tests)
│   ├── test_transform_anthropic.py  # Anthropic 转换 (44 tests)
│   ├── test_handler.py         # 统一 Handler (20 tests)
│   ├── test_db_query.py        # SQL 查询端点
│   ├── test_config_manager.py  # ConfigDB/ConfigCache (46 tests)
│   ├── test_request_logger.py  # RequestLogger
│   ├── test_proxy_logger_integration.py  # proxy+logger 集成
│   ├── test_response_store.py  # LRU/TTL
│   ├── test_proxy_pass_through.py  # 透传功能
│   ├── test_token_stats.py     # Token 统计
│   ├── test_e2e_smoke.py       # 端对端冒烟
│   ├── test_sse_utils.py       # SSE
│   ├── test_proxy_config.py    # 配置加载
│   ├── test_config_integration.py  # 配置集成
│   ├── test_pricing_manager.py # PricingDB
│   └── test_stats_service.py   # StatsService 三源合并 (3247行)
│
└── docs/                       # issues/ + superpowers/ 设计文稿
```

## 开发命令

```bash
./server.sh start     # 启动 Data Browser + AI Proxy
./server.sh stop      # 停止服务
./server.sh restart   # 重启（修改代码后必须执行，无热重载）
./server.sh status    # 查看状态

python3 -m pytest test/ -q                              # 全量 531 tests
python3 -m pytest test/test_stats_service.py -q         # StatsService
python3 -m pytest test/test_config_manager.py -q        # ConfigDB
python3 -m pytest test/test_transform.py -q             # 转换
python3 -m pytest test/test_handler.py -q -k "test名"   # 指定测试

python3 quick_test.py                                   # Token 快速冒烟
```

## 代理请求流程

```
客户端 POST /v1/responses 或 /v1/messages
  ↓
ProxyHandler.do_POST() → 路径解析出 request_type
  ↓
ConfigCache.resolve(model, request_type) → upstream_cfg
  ↓  cache 通过 POST /admin/reload 主动失效
request_type == upstream_cfg.format ?
  ├─ 是 → _handle_passthrough() — 原样转发 + 日志 + token_stats
  └─ 否 → _handle_convert()    — Chat Completions 中间格式转换
  ↓
上游 /chat/completions
  ↓
非流式：chat_to_xxx()
流式：  create_xxx_sse_stream()（逐 yield）
  ↓ record_token_stats() → DATA_DB
  ↓ ResponseStore.put() → 供 previous_response_id 使用
```

## Token 统计：三源合并

```
StatsService 对外 5 个 fetch_* 方法:
  fetch_summary()        — 总览：请求量 / token 汇总 / 成本
  fetch_by_model()       — 按模型分
  fetch_by_upstream()    — 按上游分
  fetch_trend()          — 时间趋势
  fetch_requests()       — 原始请求列表（分页）

数据流:
  1. _TokenStatsDao  ← DATA_DB.token_stats (proxy 日志)
  2. _SessionDao     ← state.db.sessions (Hermes AI Sessions)
  3. _OpenCodeDao    ← opencode.db.message (OpenCode 消息)
  4. _Merger.merge_*() → 统一字段名、求和
  5. _CostCalculator   → DATA_DB.model_pricing → 人民币成本
```

## 数据库

只有一个**数据数据库** `~/.ai-agent-tools/data/access_log.db`（由 `proxy.paths.DATA_DB` 引用），
存放所有运行时数据：Token 日志、模型路由、计费规则。

| 数据库 | 路径 | 所属 | 表 |
|--------|------|------|----|
| **data db** | `~/.ai-agent-tools/data/access_log.db` | 本项目 | `token_stats`, `debug_log`, `upstreams`, `target_models`, `model_routes`, `model_pricing`, `schema_version` |
| memory_store | `~/.hermes/memory_store.db` | Hermes Agent | `facts`, `facts_fts`, `entities`, `fact_entities` |
| state db | `~/.hermes/state.db` | Hermes Agent | `sessions` (model, started_at, input_tokens, output_tokens, cache_read/write_tokens) |
| opencode | `~/.local/share/opencode/opencode.db` | OpenCode CLI | `message` (id, time_created, data JSON: modelID, tokens) |

**SQLite 规则**：每次查询新建连接，用完关闭。必须 `PRAGMA foreign_keys = ON`。

### data db 结构

```sql
-- Token 统计（proxy 请求日志）
token_stats   (id, request_id, request_type, model, target_model, request_ts,
               duration_ms, input_tokens, output_tokens, cached_read_tokens,
               cached_write_tokens, status)
debug_log     (id, request_id, stage, model, target_model, request_type,
               request_path, data TEXT, created_at)

-- 模型路由（以下四表在 data db 内）
schema_version   (version INTEGER)
upstreams        (id TEXT PK, base_url, api_key, timeout, connect_timeout,
                  ssl_verify, retry, is_active, format CHECK[responses|messages|chat_completions])
target_models    (id INTEGER PK, name, upstream_id FK, multimodal)
model_routes     (id INTEGER PK, source, target_model_id FK,
                  request_type CHECK[responses|messages|chat_completions],
                  UNIQUE(source, request_type))

-- 计费
model_pricing    (model_id TEXT PK, display_name, input/output_cost_per_million,
                  cache_read/creation_cost_per_million, currency CHECK[USD|RMB])
```

## 模型路由

`ConfigCache` 从 data db 的 `model_routes` + `target_models` + `upstreams` 三表关联读取路由。
按需加载（不过期），通过 `reload()` 主动失效（由 `POST /admin/reload` 触发）。

**匹配优先级**：`source` 精确匹配 → `*` fallback（逐 request_type）。上游 `is_active=0` 自动跳过。

源 `proxy_config.yaml` 的 `model_map` 段已不生效（由 data db 配置取代），仅作历史参考。

## 代码速查

| 任务 | 位置 | 备注 |
|------|------|------|
| 添加 API 路径 | `proxy/handler.py` → `do_POST()` | 设 request_type |
| 修改转换逻辑 | `proxy/transform_responses.py` / `proxy/transform_anthropic.py` | 含状态机 |
| 修改上游连接 | `proxy/common.py` → `_create_upstream_conn()` | 支持 HTTP 代理/tunnel |
| 数据表变更 | `proxy/config_manager.py` → `ConfigDB._ensure_db()` | 幂等建表 |
| DB 迁移 | `proxy/config_manager.py` → `Migrations` | version++ |
| Token 查询 | `stats_service.py` → `StatsService.fetch_*()` | 三源合并 |
| 计费规则 | `proxy/pricing_manager.py` → `PricingDB` | model_pricing CRUD + 种子 |
| 日志格式 | `proxy/request_logger.py` → `RequestLogger` | 4 stage + request_path |
| Token 解析 | `proxy/token_stats.py` → `_extract_tokens()` | 3 种 usage 格式 |
| Data Browser API | `server/handler.py` → 分发表 | handler 优先命中返回 |
| 添加 Browser 路由 | `server/*_api.py` → 注册到分发表 | 按 path 精确匹配 |
| 前端页面 | `static/js/pages/*.js` | 每页面一模块 |

## 约定

### 开发流程
1. **TDD** — 先写失败测试 → 最小实现 → 通过 → commit
2. **修改后必须重启** — `./server.sh restart`（无热重载）
3. **不破坏现有功能** — 确认 531 tests 全部通过
4. **commit message** — 中文，`feat:`/`fix:`/`refactor:`/`test:`/`docs:`/`chore:`，禁止 `--no-verify`
5. **先设计后实现** — 复杂改动写 `docs/superpowers/specs/`，审阅后再执行

### 测试
- 继承 `unittest.TestCase`，pytest 运行（无 pytest fixtures/conftest/markers）
- DB 测试用 `tempfile.TemporaryDirectory`
- 前端 UI 测试用 Playwright MCP：`mcp__playwright__*` 工具，使用前先 `Read .claude/skills/playwright-agent-tools/SKILL.md`

### 代码风格
- 纯 Python 标准库，0 第三方依赖
- Re-export：`proxy/__init__.py` 统一公开（`# noqa: F401`）
- 注释分隔：`# ─── 标题 ───`
- 无 linter 配置

### 导入约定
- proxy 包内：相对导入 `from .common import CONFIG`
- proxy 包外：绝对导入 `from proxy.handler import ProxyHandler`
- 共享路径：`from proxy.paths import DATA_DB`（server/stats_service 共用）
- 禁止直接引 `proxy.transform_responses`（走 `proxy.transform` shim）

## 注意事项

- **models.js 是死代码**（16.5KB）：`static/js/pages/models.js` 存在但未被 `app.js` 导入，逻辑在 `upstreams.js`
- **config.db 无独立文件**：所有配置表（upstreams/target_models/model_routes）与 token_stats 共享 data db。旧 CLAUDE.md 中 `~/.hermes/config.db` 是错误描述
- **测试用 "config.db" 文件名**：测试代码中 `Path(tmpdir) / "config.db"` 是临时测试文件，不代表生产路径
- **透传/转换判定**：取决于 `request_type == upstream_cfg.format`，不按路径固定
- **`debug_log.request_path` 列**：记录上下游完整 URL（2026-05 新增）
- **proxy/ 第 0 层**：`paths`, `sse_utils`, `token_stats`, `config_manager`, `request_logger`, `response_store`, `pricing_manager` — 零内部依赖
- **无 CI/CD**：无 GitHub Actions、Makefile 等自动化
