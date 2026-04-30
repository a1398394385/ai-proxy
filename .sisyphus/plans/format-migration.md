# Plan: format 字段从 target_models 迁移到 upstreams

## TL;DR

> **目标**: 将 `format` 字段从 `target_models` 表迁移到 `upstreams` 表，因为一个上游的所有模型应该统一请求格式。
>
> **核心改动**:
> - DB Schema: `upstreams` 新增 `format` 列，`target_models` 移除 `format` 列
> - 后端 API: 上游 CRUD 接收/返回 format，模型 CRUD 不再处理 format
> - 前端: 上游模态框新增 format 选择，模型模态框和表格移除 format
> - 数据迁移: 新增 v1→v2 迁移脚本，默认 upstreams.format = 'openai_chat'
>
> **预计工作量**: 中等（涉及 DB、后端、前端、测试、迁移脚本）
> **并行执行**: YES - 4 个 Wave
> **关键路径**: DB Schema → 后端 CRUD → 前端 → 测试 → 迁移脚本

---

## Context

### 原始需求
用户希望将 format 字段从模型级别迁移到上游级别，因为一个上游里的所有模型应该统一支持某些请求格式。

### 当前架构
- `target_models` 表有 `format` 列（openai_chat / openai_responses / anthropic）
- 每个模型独立设置 format
- 前端在模型模态框中编辑 format

### 目标架构
- `upstreams` 表新增 `format` 列
- `target_models` 表移除 `format` 列
- 前端在上游模态框中编辑 format，模型模态框不再显示

### 技术决策
- **数据迁移策略**: 新增 v1→v2 迁移脚本，所有上游默认 format='openai_chat'，用户手动调整
- **向后兼容**: 不兼容 - 这是一次性 schema 变更，需要迁移脚本
- **前端处理**: 直接移除模型相关的 format UI，上游新增 format UI

---

## Work Objectives

### Core Objective
将 format 字段从 target_models 迁移到 upstreams，确保一个上游的所有模型共享同一个 format 配置。

### Concrete Deliverables
1. 修改后的 `config_manager.py`（schema + CRUD + 迁移）
2. 修改后的 `server.py`（API 调整）
3. 修改后的前端 JS（upstreams.js + models.js）
4. 新增 v1→v2 数据库迁移脚本
5. 更新后的测试代码

### Definition of Done
- [ ] 所有 348 个测试通过
- [ ] 新增迁移脚本测试
- [ ] 前端验证：上游可设置 format，模型不再显示 format
- [ ] 代理请求流程正常（resolve 返回的 format 正确）

### Must Have
- upstreams 表新增 format 列
- target_models 表移除 format 列
- 后端 API 正确接收/返回 format
- 前端 UI 正确显示/编辑 format
- 数据迁移脚本

### Must NOT Have (Guardrails)
- 不要保留 target_models.format 作为冗余字段
- 不要破坏现有路由解析逻辑
- 不要修改 proxy.py 的请求转换逻辑（format 只是配置位置变化）
- 不要修改 SSE 格式相关代码

---

## Verification Strategy

### Test Decision
- **Infrastructure exists**: YES (pytest)
- **Automated tests**: YES (Tests-after)
- **Framework**: pytest
- **Agent-Executed QA**: YES

### QA Policy
每个任务包含 Agent-Executed QA Scenarios，证据保存到 `.sisyphus/evidence/`。

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation - DB Schema + Backend):
├── Task 1: 修改 config_manager.py schema (upstreams + target_models)
├── Task 2: 修改 config_manager.py CRUD (upstream/model 方法)
├── Task 3: 修改 config_manager.py resolve 方法
└── Task 4: 修改 server.py API 端点

Wave 2 (Frontend):
├── Task 5: 修改 upstreams.js (上游模态框新增 format)
├── Task 6: 修改 upstreams.js (模型模态框移除 format)
└── Task 7: 修改 models.js (模型表格移除 format 列)

Wave 3 (Migration + Tests):
├── Task 8: 新增 v1→v2 数据库迁移脚本
├── Task 9: 更新 test_config_manager.py
└── Task 10: 运行全量测试验证

Wave FINAL (Review):
├── Task F1: Plan compliance audit
├── Task F2: Code quality review
├── Task F3: Real manual QA
└── Task F4: Scope fidelity check
```

---

## TODOs

### Wave 1: Backend Foundation

- [x] 1. 修改 config_manager.py - 数据库 Schema

  **What to do**:
  - 在 `_ensure_db` 的 `upstreams` 表 CREATE TABLE 中新增 `format TEXT NOT NULL DEFAULT 'openai_chat'` 列
  - 在 `_ensure_db` 的 `target_models` 表 CREATE TABLE 中移除 `format` 列
  - 确保 `list_models` 和 `get_model` 的 SQL 中通过 JOIN upstreams 获取 format
  - 修改 `add_model`: 移除 format 参数
  - 修改 `update_model`: 移除 format 字段更新
  - 修改 `add_upstream`: 接收 format 参数
  - 修改 `update_upstream`: 支持更新 format

  **Must NOT do**:
  - 不要修改 resolve_one/get_all_routes 的返回格式（仍然返回 format，只是来源变了）

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO（必须先完成 schema 才能改 CRUD）
  - **Blocks**: Task 2, Task 3

  **Acceptance Criteria**:
  - [ ] `python3 -c "from config_manager import ConfigDB; print('OK')"` 成功
  - [ ] 新创建的 DB 中 upstreams 表有 format 列，target_models 表没有

  **QA Scenarios**:
  ```
  Scenario: 新数据库 schema 正确
    Tool: Bash
    Steps:
      1. python3 -c "import tempfile; from pathlib import Path; from config_manager import ConfigDB; tmp = tempfile.TemporaryDirectory(); db = ConfigDB(Path(tmp.name) / 'test.db'); import sqlite3; conn = sqlite3.connect(str(db.db_path)); cols = [r[1] for r in conn.execute('PRAGMA table_info(upstreams)').fetchall()]; assert 'format' in cols; cols2 = [r[1] for r in conn.execute('PRAGMA table_info(target_models)').fetchall()]; assert 'format' not in cols2; print('PASS')"
    Expected Result: 输出 PASS
  ```

- [x] 2. 修改 config_manager.py - 模型 CRUD 移除 format

  **What to do**:
  - `add_model`: 移除 INSERT 中的 format 列
  - `update_model`: 移除 format 字段的更新逻辑
  - `list_models`: 确保 JOIN upstreams 获取 format（用于 API 返回兼容）
  - `get_model`: 同上

  **Must NOT do**:
  - 不要修改 list_models/get_model 的返回格式（前端暂时依赖 model.format）

  **Recommended Agent Profile**:
  - **Category**: `quick`

  **Parallelization**:
  - **Blocked By**: Task 1
  - **Can Run In Parallel**: YES（与 Task 3 同时）

  **Acceptance Criteria**:
  - [ ] `add_model` 不接收 format 参数也能正常工作
  - [ ] `update_model` 不更新 format 字段

- [x] 3. 修改 config_manager.py - 上游 CRUD 新增 format

  **What to do**:
  - `add_upstream`: INSERT 语句新增 format 列
  - `update_upstream`: 支持更新 format 字段
  - `list_upstreams`: 返回 format 字段
  - `get_upstream`: 返回 format 字段

  **Must NOT do**:
  - 不要修改上游的其他字段逻辑

  **Recommended Agent Profile**:
  - **Category**: `quick`

  **Parallelization**:
  - **Blocked By**: Task 1
  - **Can Run In Parallel**: YES（与 Task 2 同时）

  **Acceptance Criteria**:
  - [ ] `add_upstream` 可接收 format 参数
  - [ ] `update_upstream` 可更新 format
  - [ ] `get_upstream` 返回包含 format

- [x] 4. 修改 server.py - API 端点调整

  **What to do**:
  - `/api/upstreams` POST: 透传 format 字段
  - `/api/upstreams/{id}` PUT: 透传 format 字段
  - `/api/models` POST: 不再接收/透传 format 字段
  - `/api/models/{id}` PUT: 不再接收/透传 format 字段

  **Must NOT do**:
  - 不要修改路由 API
  - 不要修改其他端点

  **Recommended Agent Profile**:
  - **Category**: `quick`

  **Parallelization**:
  - **Blocked By**: Task 2, Task 3
  - **Can Run In Parallel**: NO

  **Acceptance Criteria**:
  - [ ] 上游 API 可创建/更新含 format 的上游
  - [ ] 模型 API 创建/更新时忽略 format 字段

### Wave 2: Frontend

- [x] 5. 修改 upstreams.js - 上游模态框新增 format 字段

  **What to do**:
  - `showUpstreamModal`: 在表单中新增 format 选择字段（openai_chat / openai_responses / anthropic）
  - `saveUpstream`: 收集 format 字段并提交

  **Must NOT do**:
  - 不要修改上游的其他字段

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`

  **Parallelization**:
  - **Blocked By**: Task 4
  - **Can Run In Parallel**: YES（与 Task 6, 7 同时）

  **Acceptance Criteria**:
  - [ ] 上游模态框显示 format 选择
  - [ ] 保存后上游数据包含 format

- [x] 6. 修改 upstreams.js - 模型模态框移除 format 字段

  **What to do**:
  - `showModelModal`: 移除 format 选择字段
  - `saveModel`: 不再提交 format 字段
  - 模型表格（drawer 内）: 移除 format 列显示

  **Must NOT do**:
  - 不要修改模型的其他字段

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`

  **Parallelization**:
  - **Blocked By**: Task 4
  - **Can Run In Parallel**: YES（与 Task 5, 7 同时）

  **Acceptance Criteria**:
  - [ ] 模型模态框不显示 format 字段
  - [ ] 模型表格不显示 format 列

- [x] 7. 修改 models.js - 模型表格移除 format 列

  **What to do**:
  - `loadModelTable`: 移除表格中的 format 列
  - `showModelModal`: 移除 format 选择字段
  - `saveModel`: 不再提交 format 字段

  **Must NOT do**:
  - 不要修改 models.js 中的其他功能

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`

  **Parallelization**:
  - **Blocked By**: Task 4
  - **Can Run In Parallel**: YES（与 Task 5, 6 同时）

  **Acceptance Criteria**:
  - [ ] 模型页面表格不显示 format 列
  - [ ] 模型模态框不显示 format 字段

### Wave 3: Migration + Tests

- [x] 8. 新增 v1→v2 数据库迁移脚本

  **What to do**:
  - 在 `Migrations` 类中新增 `migrate_v1_to_v2` 方法
  - 步骤：
    1. 备份数据库
    2. 新增 upstreams.format 列（默认 'openai_chat'）
    3. 创建新的 target_models 表（不含 format）
    4. 复制 target_models 数据（不含 format）
    5. 替换 target_models 表
    6. 更新 schema_version 到 2
  - 修改 `status` 方法识别 v2
  - 修改 `migrate` 方法调用 v1→v2 迁移

  **Must NOT do**:
  - 不要删除旧数据（备份保留）
  - 不要破坏外键约束

  **Recommended Agent Profile**:
  - **Category**: `deep`

  **Parallelization**:
  - **Blocked By**: Task 1
  - **Can Run In Parallel**: YES（与 Wave 2 同时，只要 schema 已定）

  **Acceptance Criteria**:
  - [ ] 迁移脚本可成功执行
  - [ ] 迁移后 upstreams 有 format 列，target_models 没有
  - [ ] 迁移后数据完整性保持

  **QA Scenarios**:
  ```
  Scenario: v1→v2 迁移成功
    Tool: Bash
    Steps:
      1. 创建 v1 数据库（手动构造）
      2. 执行 Migrations.migrate()
      3. 验证 schema_version = 2
      4. 验证 upstreams.format 存在
      5. 验证 target_models.format 不存在
    Expected Result: 所有验证通过
  ```

- [x] 9. 更新 test_config_manager.py

  **What to do**:
  - `TestModelCRUD.test_add_and_list_models`: 移除 format 断言
  - `TestModelCRUD.test_update_model`: 移除 format 更新测试
  - `TestUpstreamCRUD`: 新增 format 相关测试
  - `TestResolveModel`: 确保 resolve 返回的 format 正确（来自 upstreams）
  - `TestConfigCache`: 确保缓存返回的 format 正确

  **Must NOT do**:
  - 不要删除测试文件中的其他测试

  **Recommended Agent Profile**:
  - **Category**: `quick`

  **Parallelization**:
  - **Blocked By**: Task 1, 2, 3
  - **Can Run In Parallel**: YES（与 Task 8 同时）

  **Acceptance Criteria**:
  - [ ] `python3 -m pytest test/test_config_manager.py -q` 全部通过

- [x] 10. 运行全量测试验证

  **What to do**:
  - 运行 `python3 -m pytest test/ -q`
  - 确保所有 348 个测试通过
  - 如有失败，分析并修复

  **Must NOT do**:
  - 不要修改与本次变更无关的测试

  **Recommended Agent Profile**:
  - **Category**: `quick`

  **Parallelization**:
  - **Blocked By**: Task 8, 9
  - **Can Run In Parallel**: NO

  **Acceptance Criteria**:
  - [ ] `python3 -m pytest test/ -q` 输出 348 passed

### Wave FINAL: Verification

- [x] F1. **Plan Compliance Audit** — `oracle`
  读取计划，验证每个 Must Have 已实现，每个 Must NOT Have 未出现。

- [x] F2. **Code Quality Review** — `unspecified-high`
  运行测试，检查代码质量。

- [x] F3. **Real Manual QA** — `unspecified-high`
  启动服务，验证前端功能。

- [x] F4. **Scope Fidelity Check** — `deep`
  对比计划和实际修改，确认无 scope creep。

---

## Commit Strategy

- **Wave 1**: `refactor(db): 将 format 字段从 target_models 迁移到 upstreams - 后端`
- **Wave 2**: `refactor(ui): 将 format 字段从 target_models 迁移到 upstreams - 前端`
- **Wave 3**: `feat(migration): 新增 v1→v2 数据库迁移脚本`
- **Wave FINAL**: `test: 更新测试以适配 format 字段迁移`

---

## Success Criteria

### Verification Commands
```bash
# 1. 全量测试
python3 -m pytest test/ -q
# Expected: 348 passed

# 2. 数据库 schema 验证
python3 -c "
import sqlite3
conn = sqlite3.connect('~/.hermes/config.db')
up_cols = [r[1] for r in conn.execute('PRAGMA table_info(upstreams)').fetchall()]
tm_cols = [r[1] for r in conn.execute('PRAGMA table_info(target_models)').fetchall()]
assert 'format' in up_cols, 'upstreams 缺少 format 列'
assert 'format' not in tm_cols, 'target_models 不应有 format 列'
print('Schema OK')
"

# 3. API 验证
curl -s http://localhost:18742/api/upstreams | python3 -m json.tool | grep format
curl -s http://localhost:18742/api/models | python3 -m json.tool | grep -c format
```

### Final Checklist
- [ ] 所有 Must Have 已实现
- [ ] 所有 Must NOT Have 未出现
- [ ] 348 个测试全部通过
- [ ] 前端 UI 正确显示/编辑 format
- [ ] 迁移脚本可成功执行
