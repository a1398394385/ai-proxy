**生成时间:** 2026-05-09
**提交:** 17f5dc9
**分支:** main

## 概述

两个独立 HTTP 服务，由 `./server.sh` 统一管理，纯 Python 标准库（无外部依赖）。

| 服务 | 入口文件 | 端口 | 用途 |
|------|----------|------|------|
| Hermes Data Browser | `server/` 包 | 18742 | Web UI — Fact Store / Token 统计 / 模型路由 CRUD / SQL 查询 |
| AI Proxy | `proxy.py` | 48743 | 统一代理 — 协议转换 + 透传（OpenAI Responses / Anthropic Messages → Chat Completions）|

> **注意**：`pass_through.py` 已删除，其功能合并到 `proxy/handler.py` 的 `_handle_passthrough()` 中。不再有独立的 48744 端口服务。

## 项目结构

```
├── proxy.py                    # 瘦入口 (96行) — ThreadedHTTPServer 启动 + 日志轮转
├── server.py                   # 瘦入口 (3行) → from server import main
├── server.sh                   # 服务管理（start/stop/status/restart）
├── proxy_config.yaml           # 全局配置（端口/上游/日志/模型映射）
├── quick_test.py               # Token 统计快速冒烟
│
├── server/                     # ★ Hermes Data Browser 核心包 (10 文件)
│   ├── __init__.py             # re-export + main() 入口
│   ├── handler.py              # HermesDataHandler 类 + 分发表
│   ├── common.py               # 共享工具 — json/DB 上下文管理器/常量
│   ├── config_api.py           # upstreams/models/routes CRUD + 上游检测
│   ├── fact_api.py             # facts/categories/stats
│   ├── token_api.py            # token_stats/* 系列端点
│   ├── pricing_api.py          # pricing CRUD
│   ├── dbquery_api.py          # /api/db/query
│   └── static_files.py         # 静态文件服务
│
├── proxy/                      # ★ AI Proxy 核心包 (11 文件)
│   ├── __init__.py             # 公共 API re-export
│   ├── handler.py              # 统一 ProxyHandler — 路由/透传/转换 (1057行)
│   ├── common.py               # 共享模块 — 配置/模型解析/上游连接
│   ├── config_manager.py       # ConfigDB + ConfigCache + Migrations
│   ├── transform.py            # Re-export shim
│   ├── transform_responses.py  # Responses API ↔ Chat Completions 转换
│   ├── transform_anthropic.py  # Anthropic Messages ↔ Chat Completions 转换
│   ├── sse_utils.py            # SSE 事件格式化
│   ├── request_logger.py       # 四阶段请求/响应日志
│   ├── token_stats.py          # Token 统计写入
│   └── response_store.py       # 内存 ResponseStore (LRU+TTL)
│
├── static/                     # Web UI 前端
│   ├── index.html              # 入口 HTML — 5 Tab 导航
│   ├── css/                    # 6 文件 — 每页面独立样式
│   └── js/
│       ├── app.js              # ES Module 入口 + 页面加载器注册
│       ├── core.js             # 核心工具 — 主题/事件总线/API/格式化
│       └── pages/
│           ├── facts.js        # Fact Store CRUD
│           ├── tokens.js       # Token 统计 + SVG 图表
│           ├── upstreams.js    # 上游/模型管理（替代旧 models.js）
│           ├── routes.js       # 路由映射 CRUD
│           └── dbquery.js      # SQL 查询编辑器
│
├── test/                       # 533 tests (14 文件)
│   ├── test_transform.py       # Responses API 转换 (2030行, 138 tests)
│   ├── test_transform_anthropic.py  # Anthropic 转换 (689行, 44 tests)
│   ├── test_handler.py         # 统一 Handler (555行, 20 tests)
│   ├── test_db_query.py        # SQL 查询端点 (199行, 17 tests)
│   ├── test_config_manager.py  # ConfigDB/ConfigCache (445行, 46 tests)
│   ├── test_request_logger.py  # RequestLogger (465行, 26 tests)
│   ├── test_proxy_logger_integration.py  # proxy+logger 集成 (468行, 9 tests)
│   ├── test_response_store.py  # ResponseStore LRU/TTL (397行, 21 tests)
│   ├── test_proxy_pass_through.py  # 透传功能 (307行, 15 tests)
│   ├── test_token_stats.py     # Token 统计 (247行, 19 tests)
│   ├── test_e2e_smoke.py       # 端对端冒烟 (221行, 7 tests)
│   ├── test_sse_utils.py       # SSE 格式化 (121行, 9 tests)
│   ├── test_proxy_config.py    # 配置加载 (116行, 5 tests)
│   └── test_config_integration.py  # 配置集成 (99行, 12 tests)
│
├── data/                       # 运行时数据（access_log.db）
└── docs/superpowers/           # 设计文稿 + 实施计划
```

## 代码速查

| 任务 | 位置 | 备注 |
|------|------|------|
| 添加 API 路由 | `proxy/handler.py` → `do_GET`/`do_POST` | 按路径分派 request_type |
| 协议转换 | `proxy/transform_responses.py` / `proxy/transform_anthropic.py` | 含状态机 |
| 模型路由 | `proxy/config_manager.py` → `ConfigCache.resolve()` | TTL 5s, 精确→`*` fallback |
| SSE 格式 | `proxy/sse_utils.py` → `_format_sse_event()` | type 注入 + 紧凑 JSON |
| 日志记录 | `proxy/request_logger.py` → `RequestLogger` | 四阶段 + token_stats |
| 上游连接 | `proxy/common.py` → `_create_upstream_conn()` | 支持 HTTP 代理/HTTPS Tunneling |
| 配置管理 | `proxy_config.yaml` + `~/.hermes/config.db` | 静态 YAML + 动态 SQLite |
| Data Browser API | `server/handler.py` → 分发表 | 各模块 handle_get/handle_post/handle_put/handle_delete |
| 添加 Data Browser API 路由 | `server/*_api.py` → 导出 handler 函数 | 注册到 `server/handler.py` 分发表 |
| 前端页面 | `static/js/pages/*.js` | 每页面一个模块 |

## 开发命令

```bash
# 服务管理
./server.sh start     # 启动 Data Browser + AI Proxy
./server.sh stop      # 停止两个服务
./server.sh status    # 查看状态
./server.sh restart   # 重启（修改代码后必须执行，无热重载）

# 测试
python3 -m pytest test/ -q                    # 全量（533 tests）
python3 -m pytest test/test_transform.py -q  # 单文件
python3 -m pytest test/test_handler.py -q    # Handler 测试

# 快速冒烟
python3 quick_test.py
```

## 代理请求流程

```
客户端（Codex/Claude）
  ↓  POST /v1/responses 或 /v1/messages
ProxyHandler.do_POST()
  ↓  路径 → request_type（responses / messages / chat_completions）
  ↓  ConfigCache.resolve(model) → upstream_cfg
  ↓  request_type == upstream_cfg.format ?
  │   是 → _handle_passthrough() → 原样转发 + 日志 + token_stats
  │   否 → _handle_convert() → Chat Completions 中间格式转换
  ↓
上游 /chat/completions
  ↓
非流式：chat_to_xxx()
流式：  create_xxx_sse_stream()（逐事件 yield）
  ↓  record_token_stats() → data/access_log.db
  ↓  ResponseStore.put() → 内存（供 previous_response_id 使用）
```

## 数据库

| 数据库 | 路径 | 核心表 | 用途 |
|--------|------|--------|------|
| memory_store.db | `~/.hermes/memory_store.db` | `facts`, `facts_fts`, `entities`, `fact_entities` | Fact Store |
| config.db | `~/.hermes/config.db` | `upstreams`, `target_models`, `model_routes` | 动态模型路由 |
| access_log.db | `data/access_log.db` | `debug_log`, `token_stats` | 代理请求日志（7天保留）|

**SQLite 规则**：每次查询新建连接，用完立即关闭（无连接池）。每次连接必须 `PRAGMA foreign_keys = ON`。

### config.db 表结构

```sql
upstreams      (id, base_url, api_key, timeout, connect_timeout, ssl_verify, retry, is_active, is_default)
target_models  (id, name, upstream_id, multimodal, format)
model_routes   (id, source, target_model_id)   -- source 支持精确名称或 "*" fallback
```

## 约定

### 开发流程
1. **TDD** — 先写失败测试 → 实现最小代码 → 验证通过 → commit
2. **修改后必须重启** — `./server.sh restart`（无热重载）
3. **不破坏现有功能** — 任何修改后确认 406 tests 仍全部通过
4. **commit message 用中文** — 解释 "why" 而非 "what"，格式 `feat:`/`fix:`/`refactor:`/`docs:`/`chore:`，禁止 `--no-verify`
5. **先计划后实现** — 复杂改动先写设计文稿（`docs/superpowers/specs/`），用户审阅批准后再执行

### 测试
- 继承 `unittest.TestCase`，用 pytest 运行
- `setUp`/`tearDown` 模式管理临时数据库（非 pytest fixtures）
- 无 conftest.py，无 pytest markers，无外部测试依赖
- **前端 UI 测试使用 Playwright MCP**（`mcp__playwright__*` 工具），不使用 web-access skill 的 CDP Proxy 连接本地 Chrome
- **使用 Playwright 前必须先读取 skill**：`Read .claude/skills/playwright-agent-tools/SKILL.md`，该 skill 包含 ref 机制、工具参数格式、常见失败修复等关键操作知识

### 代码风格
- 纯 Python 标准库 — 无 Flask/Django/第三方依赖
- Docstring 标注模块和函数职责
- Re-export 模式：`proxy/__init__.py` 统一公开 API（`# noqa: F401`）
- 注释分隔线：`# ─── 标题 ───` 格式
- 无 linter 配置（.flake8/.pylintrc/.editorconfig 均不存在）

### 导入约定
- proxy 包内：相对导入 `from .common import CONFIG`
- proxy 包外：绝对导入 `from proxy.handler import ProxyHandler`
- 禁止从 `proxy.transform_responses` 直接导入（走 `proxy.transform` shim）

## 模型路由

`ConfigCache` 从 `~/.hermes/config.db` 读取路由（TTL 5s），是代理的模型解析核心。

**路由优先级**：精确匹配 `source` → `*` fallback；`is_active=0` 的上游自动跳过。

`proxy_config.yaml` 中的 `model_map` 段已不生效（由 config.db 取代），仅作历史参考。

配置变更后通过 `POST /admin/reload` 立即生效（清零 `_loaded_at` 触发下次强制刷新）。

## 注意事项

- **models.js 是死代码**（338行）：`static/js/pages/models.js` 存在但未被 `app.js` 导入，实际逻辑在 `upstreams.js`
- **透传/转换判定**：取决于 `request_type == upstream_cfg.format`，并非按路径固定
- **no conftest.py**：测试间无共享 fixture，DB 测试用 `TemporaryDirectory`
- **调试日志**：流式模式需查看 `proxy.log`，结构化日志在 `data/access_log.db`
- **proxy/ 包依赖**：`sse_utils`/`token_stats`/`config_manager`/`request_logger`/`response_store` 为第 0 层（零内部依赖）
- **无 CI/CD**：无 GitHub Actions、Makefile 等自动化
