# 模型管理 & 路由管理重构

## TL;DR

> **Quick Summary**: 将模型管理页拆分为「上游与模型」+「路由映射」两个独立页面；数据库新增 `proxy_type` 列支持 Codex / Claude / Pass-through 三套独立路由规则；前端改为上游中心化视图（展开抽屉查看模型）。

> **Deliverables**:
> - 数据库迁移脚本：`model_routes` 新增 `proxy_type TEXT NOT NULL DEFAULT 'codex'`，UNIQUE 改为 `(source, proxy_type)`
> - 新增 `Migrations` 类：幂等迁移 + 备份
> - `ConfigCache.resolve()` 支持 `proxy_type` 参数
> - `server.py` 新增迁移 API + routes API 适配 proxy_type
> - `proxy.py` / `pass_through.py` 传入 proxy_type 到解析链
> - 前端：拆分 `models.js` → `upstreams.js` + `routes.js`，应用配置按钮提升到导航栏
> - 前端：上游中心化视图，点击上游展开抽屉显示模型
> - 全量测试 348+ 全部通过

> **Estimated Effort**: Large
> **Parallel Execution**: YES — 4 waves
> **Critical Path**: Task 1 → Task 2 → Task 4 → Task 5 → Task 7 → Wave FINAL

---

## Context

### Original Request
1. 将路由映射拆成独立页面，支持 3 个代理（Codex / Claude / Pass-through）各自独立的路由规则
2. 模型管理改为上游中心：默认只展示上游列表，点击上游展开抽屉显示下方模型
3. 「应用配置」按钮提升到导航栏

### Interview Summary
**Key Discussions**:
- 代理分组：3 套独立路由规则（codex / claude / pass_through）
- 数据迁移：现有路由数据默认归属于 `codex`
- 应用配置按钮：提升到导航栏，对所有页面生效，同时 reload proxy.py:48743 和 pass_through.py:48744

**Research Findings**:
- `model_routes.source` 当前有 UNIQUE 约束 → 同一 source 只能有一条路由
- `ConfigCache` 无 proxy 概念，所有代理共享同一解析链
- `proxy.py` 同时处理 Codex（/v1/responses）和 Claude（/v1/messages），`pass_through.py` 处理透传
- 3 个服务各自有独立 `ConfigCache` 实例，共享同一个 `config.db`，TTL 各 5s
- `common.py:resolve_model()` 是 ConfigCache 的入口封装

### Metis Review
**Identified Gaps**（已纳入计划）:
- PUT /api/routes/{id} 存在验证漏洞（不校验 target_model 存在）→ 一并修复
- pass_through proxy 的 /admin/reload 需要单独触发 → 应用配置按钮发送两次 POST
- `*` fallback 路由需要按 proxy 类型各自保护 → DB 查询按 proxy_type 过滤
- 迁移脚本需幂等 + 自动备份 → Migrations 类
- 前端 empty state 设计 → 新页面包含
- 重复点击应用配置保护 → 保留 debounce

---

## Work Objectives

### Core Objective
将「路由映射」从模型管理页独立出来，支持 3 代理各自路由规则；模型管理改为上游中心化交互。

### Concrete Deliverables
- `config_manager.py`：新增 `Migrations` 类 + `proxy_type` 字段支持
- `common.py`：`resolve_model()` 增加 `proxy_type` 参数
- `server.py`：新增 `/api/migrate` 端点 + 所有 routes API 适配 proxy_type
- `proxy.py`：两个端点传入各自 proxy_type
- `pass_through.py`：传入 `'pass_through'` proxy_type
- `static/js/pages/upstreams.js`：上游 CRUD + 抽屉展开模型（新建）
- `static/js/pages/routes.js`：路由 CRUD + proxy_type 筛选（新建）
- `static/css/routes.css`：路由页样式（新建）
- `static/index.html`：新增「路由映射」Tab + nav bar 应用配置按钮 + 模型页结构重写
- `test/test_config_manager.py`：新增 proxy_type 相关测试
- `test/test_config_integration.py`：新增迁移测试

### Must Have
- 迁移脚本：备份 → ALTER → 验证，幂等可重复执行
- 3 套路由完全隔离：codex 路由变更不影响 claude
- 现有 348 测试全部通过
- 旧 API 调用（不传 proxy_type）行为不变，默认 codex

### Must NOT Have (Guardrails)
- 不添加路由连通性验证/测试功能
- 不添加批量操作（复制路由到其他代理、导入导出）
- 不添加路由使用统计
- 不将前端改为框架（保持 Vanilla JS + ES Module）
- 不添加配置版本管理/回滚
- 不添加多用户/权限功能
- 不改动 `upstreams` 和 `target_models` 表结构
- 不修改 `transform_responses.py` 或 `transform_anthropic.py`

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed.

### Test Decision
- **Infrastructure exists**: YES (pytest, 348 tests)
- **Automated tests**: TDD — 先写测试 → 实现 → 验证
- **Framework**: pytest (Python)
- **Each implementation task includes its test cases as part of same TODO**

### QA Policy
Every task MUST include agent-executed QA scenarios. Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.
- **Backend/API**: curl + assert JSON response
- **Frontend/UI**: Playwright — navigate, interact, assert, screenshot
- **Database**: sqlite3 CLI — verify schema, data integrity

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 0: 关键决策确认 (prometheus — 现在)
├── 确认 PUT 验证漏洞修复范围
├── 确认 pass_through 路由策略
└── → 用户确认后进入 Wave 1

Wave 1 (Start Immediately — 基础设施):
├── Task 1: 数据库迁移 (config_manager.py Migrations 类) [deep]
├── Task 2: ConfigDB + ConfigCache 改造 (proxy_type 支持) [deep]
├── Task 3: common.py resolve_model 改造 [quick]
└── Task 4: server.py 迁移 API + routes API 适配 [deep]

Wave 2 (After Wave 1 — 代理改造 MAX PARALLEL):
├── Task 5: proxy.py 传入 proxy_type [quick]
├── Task 6: pass_through.py 传入 proxy_type [quick]
├── Task 7: 测试编写 (config_manager + integration) [deep]
└── Task 8: server.py 导航栏 reload API 改造 [quick]

Wave 3 (After Wave 2 — 前端重构 MAX PARALLEL):
├── Task 9: index.html 结构改造 (新 Tab + nav bar按钮 + 模型页重写) [visual-engineering]
├── Task 10: upstreams.js 创建 (上游 CRUD + 抽屉展开模型) [visual-engineering]
├── Task 11: routes.js 创建 (路由 CRUD + proxy_type 筛选) [visual-engineering]
├── Task 12: app.js + core.js 适配 [quick]
└── Task 13: routes.css + models.css 样式 [visual-engineering]

Wave FINAL (After ALL tasks — 4 parallel reviews, then user okay):
├── Task F1: Plan Compliance Audit (oracle)
├── Task F2: Code Quality Review (unspecified-high)
├── Task F3: Real Manual QA (unspecified-high + playwright)
└── Task F4: Scope Fidelity Check (deep)
-> Present results -> Get explicit user okay

Critical Path: Task 1 → Task 2 → Task 4 → Task 5 → Task 7 → F1-F4 → user okay
Parallel Speedup: ~60% faster than sequential
Max Concurrent: 5 (Wave 3)
```

### Agent Dispatch Summary

- **1**: **4** — T1→deep, T2→deep, T3→quick, T4→deep
- **2**: **4** — T5→quick, T6→quick, T7→deep, T8→quick
- **3**: **5** — T9→visual-engineering, T10→visual-engineering, T11→visual-engineering, T12→quick, T13→visual-engineering
- **FINAL**: **4** — F1→oracle, F2→unspecified-high, F3→unspecified-high, F4→deep

---

## TODOs

- [x] 1. 数据库迁移 — Migrations 类 + proxy_type 列

  **What to do**:
  - 在 `config_manager.py` 中新增 `Migrations` 类
  - `Migrations.migrate()` 方法：备份 `config.db` → `config.db.bak.{timestamp}` → 执行迁移 SQL
  - 迁移 SQL（幂等）：
    1. `ALTER TABLE model_routes ADD COLUMN proxy_type TEXT NOT NULL DEFAULT 'codex'` （使用 try/except 防止重复执行）
    2. `CREATE UNIQUE INDEX IF NOT EXISTS idx_routes_source_proxy ON model_routes(source, proxy_type)`
    3. 删除旧的 source UNIQUE 约束（SQLite 不支持直接 DROP CONSTRAINT，使用重建表方式）
  - `Migrations.status()` 方法：检查迁移是否已执行
  - 在 `_ensure_db()` 中调用 Migrations 检查（非阻塞，只记录日志）
  - 迁移日志写入 `proxy.log`

  **Must NOT do**:
  - 不在 `_ensure_db()` 中自动执行迁移（仅检测和报告状态）
  - 不删除旧数据
  - 不修改 `upstreams` 和 `target_models` 表结构

  **迁移 SQL 参考**（SQLite 重建表方式）:
  ```sql
  -- 备份
  CREATE TABLE model_routes_backup AS SELECT * FROM model_routes;
  -- 重建
  CREATE TABLE model_routes_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL CHECK(length(source) > 0),
    target_model_id INTEGER NOT NULL REFERENCES target_models(id) ON DELETE RESTRICT,
    proxy_type TEXT NOT NULL DEFAULT 'codex' CHECK(proxy_type IN ('codex','claude','pass_through')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source, proxy_type)
  );
  INSERT INTO model_routes_new SELECT id, source, target_model_id, 'codex', created_at, updated_at FROM model_routes;
  DROP TABLE model_routes;
  ALTER TABLE model_routes_new RENAME TO model_routes;
  ```

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: 涉及 SQLite schema 迁移、数据完整性保障，需要仔细处理重建表逻辑
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**: 无

  **Parallelization**:
  - **Can Run In Parallel**: NO (sequential — 所有后续任务依赖)
  - **Parallel Group**: Wave 1 (单独，先行)
  - **Blocks**: Task 2, 3, 4, 5, 6
  - **Blocked By**: None

  **References**:
  - `config_manager.py:31-70` (`_ensure_db` 方法) — 当前 schema 定义，理解现有表结构
  - `config_manager.py:440-453` (`get_counts` 方法) — 迁移后需验证数据行数不变
  - `config_manager.py:12-29` (`__init__` + `_connect`) — 数据库连接模式
  - SQLite 官方文档：ALTER TABLE 限制 — SQLite 不支持 DROP CONSTRAINT，需重建表

  **Acceptance Criteria**:
  - [ ] `Migrations` 类存在，包含 `migrate()` 和 `status()` 方法
  - [ ] 迁移脚本幂等：重复执行不报错
  - [ ] 迁移前自动备份 config.db
  - [ ] 迁移后数据行数不变
  - [ ] 所有现有路由的 proxy_type = 'codex'
  - [ ] 新 UNIQUE(source, proxy_type) 约束生效

  **QA Scenarios**:
  ```
  Scenario: 迁移执行成功且数据完整
    Tool: Bash (sqlite3 + python3)
    Preconditions: 有现有路由数据的 config.db
    Steps:
      1. 执行迁移: python3 -c "from config_manager import Migrations; m = Migrations(Path('~/.hermes/config.db').expanduser()); print(m.migrate())"
      2. 验证备份: ls ~/.hermes/config.db.bak.*
      3. 验证数据行数: sqlite3 ~/.hermes/config.db "SELECT COUNT(*) FROM model_routes"
      4. 验证 proxy_type: sqlite3 ~/.hermes/config.db "SELECT DISTINCT proxy_type FROM model_routes"
    Expected Result: 步骤1输出 "ok", 步骤2有备份文件, 步骤3行数不变, 步骤4只有 'codex'
    Failure Indicators: migrate() 抛出异常, 行数减少, proxy_type 非 codex
    Evidence: .sisyphus/evidence/task-1-migration-ok.txt

  Scenario: 幂等性验证
    Tool: Bash (sqlite3 + python3)
    Steps:
      1. 首次迁移成功后再次执行: python3 -c "from config_manager import Migrations; m = Migrations(Path('~/.hermes/config.db').expanduser()); print(m.migrate())"
    Expected Result: 输出 "already_migrated" 或 "ok"，不报错
    Failure Indicators: 重复报 IntegrityError 或重复执行 DDL
    Evidence: .sisyphus/evidence/task-1-idempotent.txt
  ```

  **Commit**: YES — `feat(config): 新增数据迁移类，model_routes 支持 proxy_type 列`
  - Files: `config_manager.py`
  - Pre-commit: `python3 -m pytest test/test_config_manager.py -q`

- [x] 2. ConfigDB + ConfigCache 适配 proxy_type

  **What to do**:
  - `ConfigDB.list_routes(proxy_type=None)` — 新增可选参数，WHERE 过滤，默认返回全部
  - `ConfigDB.get_route(route_id)` — 返回数据包含 proxy_type 字段
  - `ConfigDB.add_route(data)` — data 接受 proxy_type（默认 'codex'），校验 proxy_type ∈ {'codex','claude','pass_through'}
  - `ConfigDB.update_route(route_id, data)` — data 接受 proxy_type，**新增验证**：校验 target_model 存在 + upstream 活跃
  - `ConfigDB.resolve_one(source_name, proxy_type='codex')` — 新增 proxy_type 参数
  - `ConfigDB.get_all_routes(proxy_type=None)` — 新增可选参数
  - `ConfigDB.validate_star_fallback(proxy_type='codex')` — 按 proxy_type 检查
  - `ConfigCache.resolve(source_name, proxy_type='codex')` — 新增参数透传
  - `ConfigCache.get_all(proxy_type=None)` — 新增可选参数

  **Must NOT do**:
  - 不修改 upstreams / target_models 相关方法
  - 不删除或重命名现有方法（保持向后兼容）

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: 涉及 ConfigDB 多个方法的签名变更 + 验证逻辑修复，需要仔细处理向后兼容
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 1
  - **Blocks**: Task 4, 5, 6
  - **Blocked By**: Task 1

  **References**:
  - `config_manager.py:339-364` (`list_routes` 方法) — 当前实现，需添加 proxy_type 过滤
  - `config_manager.py:380-415` (`resolve_one` 方法) — 核心解析逻辑，需添加 proxy_type JOIN 条件
  - `config_manager.py:417-438` (`get_all_routes` 方法) — Cache 加载入口
  - `config_manager.py:456-506` (ConfigCache 类) — resolve + get_all 方法
  - `config_manager.py:253-289` (`add_route` 方法) — 参考验证逻辑

  **Acceptance Criteria**:
  - [ ] `list_routes(proxy_type='codex')` 只返回 codex 路由
  - [ ] `resolve_one('gpt-4o', proxy_type='codex')` 和 `resolve_one('gpt-4o', proxy_type='claude')` 返回不同结果
  - [ ] `add_route` proxy_type 不在允许集合 → ValueError
  - [ ] `update_route` 现在校验 target_model 存在 + upstream 活跃状态

  **QA Scenarios**:
  ```
  Scenario: proxy_type 隔离
    Tool: Bash (python3 单行)
    Preconditions: 迁移已执行，添加 codex 和 claude 各自的路由
    Steps:
      1. 添加 codex 路由: POST /api/routes -d '{"source":"gpt-4o","target_model_id":1,"proxy_type":"codex"}'
      2. 添加 claude 路由: POST /api/routes -d '{"source":"gpt-4o","target_model_id":2,"proxy_type":"claude"}'
      3. 查询: GET /api/routes?proxy_type=codex
      4. 查询: GET /api/routes?proxy_type=claude
    Expected Result: 步骤3只返回 target_model_id=1, 步骤4只返回 target_model_id=2
    Failure Indicators: 步骤3或4返回两条路由，或返回错误路由
    Evidence: .sisyphus/evidence/task-2-isolation.txt

  Scenario: 无效 proxy_type 拒绝
    Tool: Bash (curl)
    Steps:
      1. curl -s -X POST http://localhost:18742/api/routes -H 'Content-Type: application/json' -d '{"source":"test","target_model_id":1,"proxy_type":"invalid"}'
    Expected Result: 400 Bad Request, error 包含 "proxy_type"
    Failure Indicators: 201 Created
    Evidence: .sisyphus/evidence/task-2-invalid-proxy.txt
  ```

  **Commit**: YES — `feat(config): ConfigDB/ConfigCache 支持 proxy_type 参数，修复 PUT 验证缺失`
  - Files: `config_manager.py`
  - Pre-commit: `python3 -m pytest test/test_config_manager.py -q`

- [x] 3. common.py resolve_model 改造

  **What to do**:
  - `resolve_model(model_name, proxy_type='codex')` — 新增 `proxy_type` 参数
  - 将 `proxy_type` 透传到 `config_cache.resolve(model_name, proxy_type)`

  **Must NOT do**:
  - 不修改 `_create_upstream_conn`、`_normalize_forward_path` 等其他函数

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: 单一函数参数透传，改动量小
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES (与 Task 4 并行)
  - **Parallel Group**: Wave 1 (after Task 2)
  - **Blocks**: Task 5, 6
  - **Blocked By**: Task 2

  **References**:
  - `common.py:73-91` (`resolve_model` 函数) — 当前实现

  **Acceptance Criteria**:
  - [ ] `resolve_model('gpt-4o', proxy_type='codex')` 正确返回 codex 路由
  - [ ] 不传 proxy_type → 默认 codex（向后兼容）

  **QA Scenarios**:
  ```
  Scenario: 默认向后兼容
    Tool: Bash (python3 单行)
    Steps:
      1. python3 -c "from common import resolve_model; r = resolve_model('*'); print(r is not None)"
    Expected Result: True
    Failure Indicators: 报 TypeError（缺少参数）
    Evidence: .sisyphus/evidence/task-3-backward-compat.txt
  ```

  **Commit**: YES — `feat(common): resolve_model 支持 proxy_type 参数`
  - Files: `common.py`
  - Pre-commit: `python3 -m pytest test/test_config_integration.py -q`

- [x] 4. server.py — 迁移 API + routes API 适配

  **What to do**:
  - 新增 `POST /api/migrate` — 执行数据库迁移，返回迁移状态
  - `GET /api/routes` — 支持 `?proxy_type=codex` 查询参数
  - `POST /api/routes` — body 接受 `proxy_type` 字段，校验 ∈ {'codex','claude','pass_through'}
  - `PUT /api/routes/{id}` — body 接受 `proxy_type` 字段
  - `GET /api/config/status` — 返回 proxy_type 相关统计
  - 「应用配置」改造：`POST /api/config/reload` 现在同时发送到 proxy:48743 和 pass_through:48744 的 `/admin/reload`

  **Must NOT do**:
  - 不修改 upstreams / models 的 GET/POST/PUT/DELETE 端点
  - 不修改 facts / tokens 相关端点

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: 涉及多个 API 端点改造 + 新增端点 + 双代理 reload，需要仔细处理错误场景
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 1
  - **Blocks**: Task 8, 10, 11
  - **Blocked By**: Task 2

  **References**:
  - `server.py:588-632` (GET routes/upstreams/models 端点) — 参考现有路由实现模式
  - `server.py:770-839` (POST routes/config 端点) — 需要修改和新增
  - `server.py:827-840` (/api/config/reload 端点) — 当前只 reload proxy.py，需要增加 pass_through
  - `proxy.py:112-139` (_handle_admin_reload 方法) — 理解 reload 端点接收格式
  - `config_manager.py:440-453` (get_counts 方法) — 统计信息格式

  **Acceptance Criteria**:
  - [ ] `POST /api/migrate` 返回 `{"status":"ok"}`
  - [ ] `GET /api/routes?proxy_type=codex` 只返回 codex 路由
  - [ ] `POST /api/routes` 不传 proxy_type → 默认 'codex' → 201
  - [ ] `POST /api/routes` 传 `proxy_type: "invalid"` → 400
  - [ ] `POST /api/config/reload` 同时成功 reload proxy 和 pass_through

  **QA Scenarios**:
  ```
  Scenario: 迁移 API 正常工作
    Tool: Bash (curl)
    Steps:
      1. curl -s -X POST http://localhost:18742/api/migrate
    Expected Result: {"status": "ok"}
    Failure Indicators: 404 或 500
    Evidence: .sisyphus/evidence/task-4-migrate-api.txt

  Scenario: routes 查询参数过滤
    Tool: Bash (curl)
    Preconditions: 有 codex 和 claude 各至少一条路由
    Steps:
      1. curl -s http://localhost:18742/api/routes?proxy_type=codex | python3 -c "import json,sys; routes=json.load(sys.stdin)['routes']; assert all(r['proxy_type']=='codex' for r in routes), 'found non-codex route'; print(f'PASS: {len(routes)} codex routes')"
      2. curl -s http://localhost:18742/api/routes?proxy_type=claude | python3 -c "import json,sys; routes=json.load(sys.stdin)['routes']; assert all(r['proxy_type']=='claude' for r in routes), 'found non-claude route'; print(f'PASS: {len(routes)} claude routes')"
    Expected Result: 两次均 PASS
    Failure Indicators: AssertionError
    Evidence: .sisyphus/evidence/task-4-filter.txt

  Scenario: 双代理 reload
    Tool: Bash (curl)
    Preconditions: proxy.py 和 pass_through.py 都在运行
    Steps:
      1. curl -s -X POST http://localhost:18742/api/config/reload
    Expected Result: {"status": "ok", "reloaded_at": "...", "pass_through_reloaded": true}
    Failure Indicators: message 包含 "proxy 未运行"
    Evidence: .sisyphus/evidence/task-4-reload.txt
  ```

  **Commit**: YES — `feat(server): 新增迁移API + routes API适配proxy_type + 双代理reload`
  - Files: `server.py`
  - Pre-commit: `python3 -m pytest test/ -q`

- [x] 5. proxy.py 传入 proxy_type

  **What to do**:
  - `_handle_responses()` — 传入 `proxy_type='codex'` 到 `resolve_model()`
  - `_handle_messages()` — 传入 `proxy_type='claude'` 到 `resolve_model()`

  **Must NOT do**:
  - 不修改请求转发/转换逻辑

  **Recommended Agent Profile**:
  - **Category**: `quick` — Reason: 两处函数调用加参数
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES (与 Task 6, 7, 8 并行)
  - **Parallel Group**: Wave 2
  - **Blocked By**: Task 3

  **References**:
  - `proxy.py:142-216` (resp._handle_responses) — resolve_model() 调用处
  - `proxy.py:218-305` (msg._handle_messages) — 同上

  **Acceptance Criteria**:
  - [ ] Codex 请求走 codex 路由表 / Claude 请求走 claude 路由表

  **QA Scenarios**:
  ```
  Scenario: Codex 路由隔离
    Tool: Bash (curl)
    Preconditions: codex 和 claude 各自有不同路由，已 reload
    Steps:
      1. 验证 Codex API 解析: POST /v1/responses model="test-proxy"
      2. 验证 Claude API 解析: POST /v1/messages model="test-proxy"
    Expected Result: 分别路由到不同 target_model
    Evidence: .sisyphus/evidence/task-5-codex-isolation.txt
  ```

  **Commit**: YES — `feat(proxy): Codex/Claude端点分别传入对应proxy_type`
  - Files: `proxy.py`

- [x] 6. pass_through.py 传入 proxy_type

  **What to do**:
  - `_handle_pass_through()` — 传入 `proxy_type='pass_through'` 到 `resolve_model()`

  **Must NOT do**: 不修改转发逻辑

  **Recommended Agent Profile**: `quick` — 单处参数加参
  **Skills**: `[]`
  **Parallelization**: YES (与 Task 5, 7, 8 并行) | Wave 2 | Blocked By: Task 3
  **References**: `pass_through.py:31-40` (resolve_model 调用处)

  **Acceptance Criteria**: [ ] Pass-through 请求走 pass_through 路由表

  **QA Scenarios**:
  ```
  Scenario: import 验证参数传递
    Tool: Bash (python3)
    Steps: python3 -c "from pass_through import *; print('OK')"
    Expected Result: OK
    Evidence: .sisyphus/evidence/task-6-pass-through.txt
  ```

  **Commit**: YES — `feat(pass_through): 传入proxy_type='pass_through'到路由解析`
  - Files: `pass_through.py`

- [x] 7. 测试编写 — proxy_type 隔离 + 迁移测试

  **What to do**:
  - `test_config_manager.py` 新增 5 个测试：list_routes 过滤、无效 proxy_type 拒绝、UNIQUE 约束、跨代理隔离、`*` fallback 独立
  - `test_config_integration.py` 新增 2 个测试：迁移幂等性、迁移数据不丢失
  - 目标: 全量 355+ 测试通过

  **Recommended Agent Profile**: `deep` — 多代理隔离场景设计
  **Skills**: `[]`
  **Parallelization**: YES (与 Task 5, 6, 8 并行) | Wave 2 | Blocked By: Task 1, 2
  **References**: `test/test_config_manager.py` (测试模式), `test/test_config_integration.py`

  **Acceptance Criteria**: [ ] ≥7 新测试 | [ ] `python3 -m pytest test/ -q` 355+ passed

  **QA Scenarios**:
  ```
  Scenario: 全量测试通过
    Tool: Bash (pytest)
    Steps: python3 -m pytest test/ -q
    Expected Result: 355+ passed, 0 failed
    Evidence: .sisyphus/evidence/task-7-tests.txt
  ```

  **Commit**: YES — `test: 新增proxy_type隔离测试 (7个用例)`
  - Files: `test/test_config_manager.py`, `test/test_config_integration.py`

- [x] 8. server.py — 导航栏 reload API 增强

  **What to do**:
  - `GET /api/config/status` 新增长 `pass_through_reachable` 字段
  - `/api/config/reload` 在 pass_through 离线时 graceful degradation

  **Recommended Agent Profile**: `quick` — 单端点字段扩展
  **Skills**: `[]`
  **Parallelization**: YES (与 Task 5, 6, 7 并行) | Wave 2 | Blocked By: Task 4
  **References**: `server.py:634-651`, `server.py:827-840`

  **Acceptance Criteria**: [ ] status 含 `pass_through_reachable` | [ ] graceful degradation

  **QA Scenarios**:
  ```
  Scenario: status 含新字段
    Tool: Bash (curl)
    Steps: curl -s http://localhost:18742/api/config/status
    Expected Result: JSON 含 "pass_through_reachable"
    Evidence: .sisyphus/evidence/task-8-status.txt
  ```

  **Commit**: YES — `feat(server): config/status新增pass_through状态`
  - Files: `server.py`

- [x] 9. index.html 结构改造 — 新「路由映射」Tab + nav bar 应用配置按钮 + 模型页重写

  **What to do**:
  - 导航栏：新增第 4 个 Tab「🔀 路由映射」(`data-page="routes"`)
  - 导航栏右侧：新增 `#apply-config-btn` 按钮（从模型页移出）
  - 新增 `<div id="page-routes" class="main-content hidden">` 页面容器
  - 重写 `page-models`：移除路由映射 table card 和应用配置按钮，只保留上游 table + 模型 drawer 容器
  - 模型 drawer 容器：`<div id="model-drawer" class="drawer hidden">` 初始隐藏

  **Must NOT do**:
  - 不修改 facts / tokens 页面结构
  - 不修改主题/设置/模态框结构
  - 不修改 nav-brand / nav-tabs 的布局方式

  **Recommended Agent Profile**: `visual-engineering` — 涉及 HTML 布局重组、新元素添加
  **Skills**: `[]`
  **Parallelization**: YES (与 Task 10, 11, 12, 13 并行) | Wave 3 | Blocked By: None

  **References**:
  - `static/index.html:12-35` (导航栏结构) — 参考现有 Tab 模式
  - `static/index.html:168-224` (page-models 当前结构) — 需要重写的部分
  - `static/index.html:37-49` (page-facts 结构) — 参考 main-content 模式

  **Acceptance Criteria**:
  - [ ] 导航栏有 4 个 Tab: Fact Store / Token 统计 / 模型管理 / 路由映射
  - [ ] 导航栏右侧有 `#apply-config-btn` 按钮
  - [ ] page-models 不含路由表格，含 `#model-drawer` 容器
  - [ ] page-routes 容器存在，初始 hidden

  **QA Scenarios**:
  ```
  Scenario: 导航栏结构正确
    Tool: Playwright (page.goto → snapshot)
    Preconditions: 服务运行中
    Steps:
      1. page.goto('http://localhost:18742')
      2. 验证: page.locator('.nav-tab[data-page="routes"]') 存在
      3. 验证: page.locator('#apply-config-btn') 存在
    Expected Result: 4 个 Tab 可见，应用配置按钮在导航栏
    Evidence: .sisyphus/evidence/task-9-nav.png
  ```

  **Commit**: YES — `feat(html): 新增路由映射Tab + 导航栏应用配置按钮 + 模型页重写`
  - Files: `static/index.html`

- [x] 10. upstreams.js 创建 — 上游 CRUD + 抽屉展开模型

  **What to do**:
  - 新建 `static/js/pages/upstreams.js`
  - 从 `models.js` 迁移上游相关函数：`loadUpstreamTable`, `showUpstreamModal`, `saveUpstream`, `testUpstream`, `confirmDisableUpstream`, `refreshUpstreamDropdown`
  - 新增抽屉展开逻辑：
    - 点击上游行 → `toggleModelDrawer(upstreamId)` → 展开/收起下方 `<div id="model-drawer">`
    - drawer 内渲染 `model-table`（model CRUD 操作在 drawer 内）
    - drawer 显示该上游下的模型列表 +「+ 新增模型」按钮
  - 迁移模型相关函数到 upstreams.js：`showModelModal`, `saveModel`, `confirmDeleteModel`
  - 导出 `loadUpstreamPage` 函数供 app.js 调用
  - 全局挂载 onclick handler

  **Must NOT do**:
  - 不包含任何路由相关代码
  - 不修改上游 API 调用逻辑
  - 抽屉不在页面初次加载时展开（所有抽屉默认关闭）

  **Recommended Agent Profile**: `visual-engineering` — 新的交互模式（抽屉展开）+ CRUD 迁移
  **Skills**: `[]`
  **Parallelization**: YES (与 Task 9, 11, 12, 13 并行) | Wave 3 | Blocked By: Task 4

  **References**:
  - `static/js/pages/models.js:20-78` (上游/模型表格函数) — 需要迁移的代码
  - `static/js/pages/models.js:92-219` (上游/模型 CRUD 模态框函数) — 需要迁移
  - `static/js/pages/models.js:292-318` (init + exports + globals) — 参考导出模式
  - `static/css/models.css` (表格样式) — drawer 需复用现有样式

  **Acceptance Criteria**:
  - [ ] 页面初始：显示上游表格，无模型表格，所有抽屉关闭
  - [ ] 点击上游行 → 该行下方展开 drawer，显示该上游的模型列表
  - [ ] 再次点击同一上游 → drawer 关闭
  - [ ] 点击不同上游 → 前一个 drawer 关闭，新 drawer 展开
  - [ ] drawer 内可 CRUD 模型，操作后 drawer 内容刷新

  **QA Scenarios**:
  ```
  Scenario: 抽屉展开/关闭
    Tool: Playwright
    Steps:
      1. page.goto('http://localhost:18742') → 点击「模型管理」Tab
      2. 验证: 上游表格可见，无 drawer
      3. 点击第一行上游 → 验证: drawer 可见，含模型列表
      4. 再次点击同一上游 → 验证: drawer 隐藏
    Expected Result: 展开/关闭流畅，模型数据正确
    Evidence: .sisyphus/evidence/task-10-drawer.png
  ```

  **Commit**: YES — `feat(js): 新建upstreams.js — 上游中心化视图+抽屉模型管理`
  - Files: `static/js/pages/upstreams.js` (新建)

- [x] 11. routes.js 创建 — 路由 CRUD + proxy_type 筛选

  **What to do**:
  - 新建 `static/js/pages/routes.js`
  - 从 `models.js` 迁移路由相关函数：`loadRouteTable`, `showRouteModal`, `saveRoute`, `confirmDeleteRoute`
  - 新增 proxy_type 筛选：页面顶部 3 个标签按钮（Codex / Claude / Pass-through），点击切换显示对应代理的路由
  - `loadRouteTable(proxyType)` — 传入 proxy_type 参数
  - 默认显示 codex 路由
  - 新增路由时自动关联当前选中的 proxy_type
  - 导出 `loadRoutePage` 函数供 app.js 调用
  - 全局挂载 onclick handler

  **Must NOT do**:
  - 不包含模型/上游 CRUD 代码
  - 不添加路由统计

  **Recommended Agent Profile**: `visual-engineering` — 新的标签切换 + CRUD 迁移
  **Skills**: `[]`
  **Parallelization**: YES (与 Task 9, 10, 12, 13 并行) | Wave 3 | Blocked By: Task 4

  **References**:
  - `static/js/pages/models.js:57-78` (路由表格函数) — 需要迁移
  - `static/js/pages/models.js:222-277` (路由 CRUD 模态框函数) — 需要迁移
  - `static/js/pages/models.js:292-318` — 导出模式
  - `server.py:588-632` (routes API) — 理解 proxy_type 查询参数

  **Acceptance Criteria**:
  - [ ] 3 个 proxy_type 标签按钮可见
  - [ ] 点击 Codex 标签 → 显示 codex 路由
  - [ ] 点击 Claude 标签 → 显示 claude 路由
  - [ ] 新增路由的 proxy_type 自动对应当前选中的标签

  **QA Scenarios**:
  ```
  Scenario: proxy_type 标签切换
    Tool: Playwright
    Steps:
      1. page.goto('http://localhost:18742') → 点击「路由映射」Tab
      2. 验证: Codex 标签默认选中，表格显示 codex 路由
      3. 点击 Claude 标签 → 表格切换为 claude 路由
      4. 点击「+ 新增路由」→ modal 中 proxy_type 字段已设为 claude
    Expected Result: 标签切换流畅，数据过滤正确
    Evidence: .sisyphus/evidence/task-11-tabs.png
  ```

  **Commit**: YES — `feat(js): 新建routes.js — 路由CRUD + proxy_type标签切换`
  - Files: `static/js/pages/routes.js` (新建)

- [x] 12. app.js + core.js 适配 — 新页面注册 + 导航栏应用配置

  **What to do**:
  - `app.js`：
    - 导入 `loadUpstreamPage` 和 `loadRoutePage`
    - 注册 `pageLoaders.upstreams` 和 `pageLoaders.routes`
    - 页面切换逻辑新增 `upstreams` 和 `routes` case
    - 导航栏 `#apply-config-btn` 点击事件绑定 `applyConfig()`
    - 移除旧的 models 页面切换逻辑
  - `core.js`：
    - `applyDefaultPage()` 的 validPages 新增 `'upstreams'` 和 `'routes'`
    - `showSettings()` 的默认页面下拉框新增这些选项

  **Must NOT do**:
  - 不修改 facts / tokens 页面逻辑

  **Recommended Agent Profile**: `quick` — 注册/导入/绑定，逻辑简单
  **Skills**: `[]`
  **Parallelization**: YES (与 Task 9, 10, 11, 13 并行) | Wave 3 | Blocked By: Task 10, 11 (需要知道导出函数名)

  **References**:
  - `static/js/app.js:1-65` (完整入口文件) — 理解页面注册模式
  - `static/js/core.js:53-75` (applyDefaultPage) — 需要扩展 validPages
  - `static/js/core.js:77-107` (showSettings) — 默认页面选项

  **Acceptance Criteria**:
  - [ ] 点击「路由映射」Tab → 切换到 routes 页面
  - [ ] 点击「模型管理」Tab → 切换到 upstreams 页面
  - [ ] 导航栏「应用配置」按钮可点击，触发 reload
  - [ ] 设置弹窗的默认页面下拉框含所有 4 个页面

  **QA Scenarios**:
  ```
  Scenario: Tab 切换正常
    Tool: Playwright
    Steps:
      1. 依次点击 4 个 Tab → 验证每次切换对应页面可见
    Expected Result: 4 个 Tab 均正常切换
    Evidence: .sisyphus/evidence/task-12-tabs.png
  ```

  **Commit**: YES — `feat(js): 注册新页面+导航栏应用配置+默认页面更新`
  - Files: `static/js/app.js`, `static/js/core.js`

- [x] 13. routes.css + models.css 样式

  **What to do**:
  - 新建 `static/css/routes.css`：路由页面专属样式（空状态、proxy_type 标签按钮、表格样式复用 base.css）
  - `static/css/models.css`：新增 `.drawer` 样式（展开动画、边距、背景色）
  - 确保深色/浅色主题都正确

  **Must NOT do**: 不修改 base.css/tokens.css/facts.css

  **Recommended Agent Profile**: `visual-engineering` — 纯样式工作
  **Skills**: `[]`
  **Parallelization**: YES (与 Task 9, 10, 11, 12 并行) | Wave 3 | Blocked By: None

  **References**:
  - `static/css/models.css` (现有样式) — table-card, table-header 等复用
  - `static/css/base.css:349-407` (modal 样式) — CSS 变量命名约定

  **Acceptance Criteria**:
  - [ ] drawer 展开时有平滑过渡
  - [ ] proxy_type 标签按钮选中态与非选中态视觉区分明显
  - [ ] 深色/浅色主题下样式正确

  **QA Scenarios**:
  ```
  Scenario: drawer 样式和主题
    Tool: Playwright
    Steps:
      1. 展开 drawer → screenshot
      2. 切换浅色主题 → 展开 drawer → screenshot
    Expected Result: 两种主题下 drawer 都美观可用
    Evidence: .sisyphus/evidence/task-13-drawer-dark.png, .sisyphus/evidence/task-13-drawer-light.png
  ```

  **Commit**: YES — `style: 新增routes.css + drawer样式`
  - Files: `static/css/routes.css` (新建), `static/css/models.css`

---

## Final Verification Wave

> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.

- [x] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists. For each "Must NOT Have": search codebase for forbidden patterns. Check evidence files exist in `.sisyphus/evidence/`. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [x] F2. **Code Quality Review** — `unspecified-high`
  Run `python3 -m pytest test/ -q`. Review all changed files for: `# type: ignore`, bare excepts, hardcoded credentials, commented-out code. Check AI slop: excessive comments, over-abstraction, generic names.
  Output: `Tests [N pass/N fail] | Lint [PASS/FAIL] | Files [N clean/N issues] | VERDICT`

- [x] F3. **Real Manual QA** — `unspecified-high` (+ `playwright` skill)
  Start from clean state. Execute EVERY QA scenario from EVERY task. Test cross-task integration:
  - 迁移 → 添加 codex + claude 路由 → 验证隔离 → 前端标签切换 → 应用配置 → 验证代理路由
  - 抽屉展开/关闭 → 模型 CRUD → 主题切换
  Save to `.sisyphus/evidence/final-qa/`.
  Output: `Scenarios [N/N pass] | Integration [N/N] | Edge Cases [N tested] | VERDICT`

- [x] F4. **Scope Fidelity Check** — `deep`
  For each task: read "What to do", read actual diff. Verify 1:1 — everything in spec was built, nothing beyond spec was built. Check "Must NOT do" compliance. Flag unaccounted changes.
  Output: `Tasks [N/N compliant] | Contamination [CLEAN/N issues] | Unaccounted [CLEAN/N files] | VERDICT`

---

## Commit Strategy

| Wave | Tasks | Commit Message | Key Files |
|------|-------|---------------|-----------|
| 1 | 1 | `feat(config): 新增数据迁移类，model_routes支持proxy_type列` | config_manager.py |
| 1 | 2 | `feat(config): ConfigDB/ConfigCache支持proxy_type参数，修复PUT验证缺失` | config_manager.py |
| 1 | 3 | `feat(common): resolve_model支持proxy_type参数` | common.py |
| 1 | 4 | `feat(server): 新增迁移API + routes API适配proxy_type + 双代理reload` | server.py |
| 2 | 5 | `feat(proxy): Codex/Claude端点分别传入对应proxy_type` | proxy.py |
| 2 | 6 | `feat(pass_through): 传入proxy_type='pass_through'到路由解析` | pass_through.py |
| 2 | 7 | `test: 新增proxy_type隔离测试 (7个用例)` | test/test_config_manager.py, test/test_config_integration.py |
| 2 | 8 | `feat(server): config/status新增pass_through状态` | server.py |
| 3 | 9 | `feat(html): 新增路由映射Tab + 导航栏应用配置按钮 + 模型页重写` | static/index.html |
| 3 | 10 | `feat(js): 新建upstreams.js — 上游中心化视图+抽屉模型管理` | static/js/pages/upstreams.js |
| 3 | 11 | `feat(js): 新建routes.js — 路由CRUD + proxy_type标签切换` | static/js/pages/routes.js |
| 3 | 12 | `feat(js): 注册新页面+导航栏应用配置+默认页面更新` | static/js/app.js, static/js/core.js |
| 3 | 13 | `style: 新增routes.css + drawer样式` | static/css/routes.css, static/css/models.css |
| — | F1-F4 | Review commits (no separate commit needed) | — |

---

## Success Criteria

### Verification Commands
```bash
# 全量测试
python3 -m pytest test/ -q            # Expected: 355+ passed, 0 failed

# 迁移 API
curl -s -X POST http://localhost:18742/api/migrate  # Expected: {"status": "ok"}

# 路由隔离
curl -s "http://localhost:18742/api/routes?proxy_type=codex"  # Expected: 仅 codex 路由
curl -s "http://localhost:18742/api/routes?proxy_type=claude" # Expected: 仅 claude 路由

# 双代理 reload
curl -s -X POST http://localhost:18742/api/config/reload  # Expected: status=ok + pass_through_reloaded

# 前端启动
./server.sh restart                    # Expected: 3 services running
curl -s http://localhost:18742         # Expected: HTML with 4 nav tabs
```

### Final Checklist
- [ ] 所有 "Must Have" 已实现
- [ ] 所有 "Must NOT Have" 未出现
- [ ] 348+ 原有测试全部通过
- [ ] ≥7 个新测试通过
- [ ] 迁移可重复执行不报错
- [ ] 3 套路由完全隔离
- [ ] 前端 4 个 Tab 正常切换
- [ ] 抽屉展开/关闭正常
- [ ] 应用配置按钮同时 reload 两个 proxy
- [ ] 深色/浅色主题下样式正常

