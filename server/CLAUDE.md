# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 概述

`server/` 是 Hermes Data Browser 的 HTTP 后端包。纯 Python 标准库，提供 Token 统计查询、模型路由 CRUD、Fact Store 管理、计费规则配置的 REST API。

## 架构

```
__init__.py  →  main()  →  HTTPServer(HOST:18742, HermesDataHandler)
                             │
handler.py  →  HermesDataHandler._dispatch()
                 │
                 ├── GET  →  [config_api, fact_api, token_api, pricing_api, static_files]
                 ├── POST →  [config_api, pricing_api, fact_api]
                 ├── PUT  →  [config_api, pricing_api, fact_api]
                 └── DELETE → [config_api, pricing_api, fact_api]
```

分发规则：handler 列表按顺序尝试，**第一个返回 True 的模块赢**。`static_files` 必须排在 GET 链最末（始终返回 True）。

## 文件说明

### `__init__.py` — 入口 + 公共 API
- `main()` — 验证 DB 存在 → 初始化 StatsService → 启动 HTTP 服务器
- Re-export: `json_response`, `_read_json`, `row_to_dict`, DB 上下文管理器, `MAX_BODY_SIZE`

### `common.py` — 共享工具
- **路径常量**: `DB_PATH = ~/.hermes/memory_store.db`, `STATE_DB_PATH = ~/.hermes/state.db`
- **`json_response(handler, data, status)`** — JSON 响应 + CORS 头
- **`_read_json(handler)`** — 读请求 body，限制 10MB
- **`row_to_dict(row)`** — sqlite3.Row → dict（bytes → None）
- **DB 上下文管理器**:
  - `config_db()` → `ConfigDB(DATA_DB)`（来自 proxy.config_manager）
  - `pricing_db()` → `PricingDB(DATA_DB)`（来自 proxy.pricing_manager）
  - `fact_db()` → 原生 sqlite3 连接到 memory_store
  - `state_db()` → 原生 sqlite3 连接到 state.db
- **`_reload_proxies()`** — POST `/admin/reload` 到本地 proxy（热更新路由缓存）

### `handler.py` — 请求分发器
`HermesDataHandler(SimpleHTTPRequestHandler)` — 约 87 行，纯分发，无业务逻辑。
- `do_OPTIONS()` — CORS 预检
- `_dispatch(handlers, path)` — 遍历 handler 列表，首个命中即返回
- `stats_service` 类属性由 `main()` 注入

### `config_api.py` — 配置管理 API
上游/模型/路由/代理路由的完整 CRUD + 上游检测 + 模型自动发现：
- `GET/POST/PUT/DELETE /api/upstreams[/<id>]`
- `GET/POST/PUT/DELETE /api/models[/<id>]`
- `GET/POST/PUT/DELETE /api/routes[/<id>]`
- `GET/POST/PUT/DELETE /api/agent-routes[/<id>]`
- `POST /api/upstreams/<id>/test` — TCP + HTTP 连通性检测
- `POST /api/upstreams/<id>/detect-models` — 调上游 `/v1/models` 发现模型
- `POST /api/upstreams/<id>/models/bulk` — 批量添加模型
- `POST /api/config/reload` — 通知 proxy 热更新路由
- `GET /api/config/status` — proxy 健康检查 + DB 计数

### `fact_api.py` — Fact Store API
知识库 CRUD（facts 表 + entities 表 + fact_entities 关联表）：
- `GET /api/facts` — 支持 `?q=` FTS 搜索 + `?category=` 过滤
- `GET /api/facts/<id>` — 单条 fact + 关联 entities
- `GET /api/categories` — 分类列表 + 计数
- `GET /api/stats` — 聚合统计
- `POST /api/facts` — 创建 fact（自动创建/关联 entities）
- `POST /api/facts/<id>/feedback` — 调整 trust_score

### `token_api.py` — Token 统计 API
全部委托给 `handler.stats_service`（StatsService 实例）：
- `GET /api/token_stats` — 总览
- `GET /api/token_stats/by_model` — 按模型
- `GET /api/token_stats/by_upstream` — 按上游
- `GET /api/token_stats/trend` — 时间趋势
- `GET /api/token_stats/requests` — 分页请求列表（?period/day/week/month, ?model, ?source, ?limit, ?offset）
- 若 stats_service 为 None，返回 503

### `pricing_api.py` — 计费 API
- `GET/POST/PUT/DELETE /api/pricing[/<model_id>]`
- 写操作后自动调用 `stats_service.invalidate_pricing_cache()` 使计费缓存失效

### `static_files.py` — 静态文件服务
- 映射 `/` → `static/index.html`，其他路径 → `static/<path>`
- 路径遍历保护（realpath 校验）
- MIME 类型推断，`Cache-Control: no-cache`
- **永远返回 True**，必须排在 GET 链最末

## 与 proxy 包的关系
- server 通过 `proxy.common` 加载配置（`load_config`）
- server 通过 `proxy.paths.DATA_DB` 访问共享数据库
- server 通过 `proxy.config_manager.ConfigDB` 管理路由
- server 通过 `proxy.pricing_manager.PricingDB` 管理计费
- `config_api` 修改配置后调用 `_reload_proxies()` 通知 proxy 热更新

## 注意事项
- 无热重载：修改代码后需 `./server.sh restart`
- `stats_service` 是可选的（如果 `stats_service.py` 不存在或初始化失败，token_api 返回 503 但不影响其他 API）
- `DB_PATH`（memory_store.db）必须存在才能启动；`STATE_DB_PATH` 缺失只警告
