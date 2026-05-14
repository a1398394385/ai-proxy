# Agent 智能路由设计

> 将 Claude Code 子 agent 检测机制引入 proxy，实现主/子 agent 请求的差异化路由。

## 概述

在现有路由映射系统基础上，新增 **Agent 路由覆盖层**。当检测到请求来自 Claude Code 子 agent 时，优先使用 agent 路由表的匹配结果；无匹配时回退到主路由表。Agent 路由只做精确覆盖，不支持 `*` fallback。

### 核心决策

| 决策项 | 选择 | 理由 |
|--------|------|------|
| 路由模型 | 覆盖层（非独立路由） | 主路由为默认，agent 路由仅覆盖特定模型指向 |
| 检测信号 | `__SUBAGENT_MARKER__` + `metadata.user_id._agent_` | 与 cc-switch PR #2621 一致，双重保障 |
| Agent fallback | 无 | 精确覆盖语义，无匹配则回退主路由 |
| DB 方案 | 新建 `agent_routes` 表 | 解耦主/agent 路由，迁移风险最低 |
| UI 布局 | 并列双表格 | 主路由表 + Agent 路由表上下排列，与三卡片联动 |

## 1. 数据库 & 迁移

### 新增表 `agent_routes`

```sql
CREATE TABLE IF NOT EXISTS agent_routes (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  source          TEXT NOT NULL CHECK(length(source) > 0),
  target_model_id INTEGER NOT NULL REFERENCES target_models(id) ON DELETE RESTRICT,
  request_type    TEXT NOT NULL DEFAULT 'chat_completions'
                  CHECK(request_type IN ('responses','messages','chat_completions')),
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(source, request_type)
);
```

与 `model_routes` 的区别：
- 语义上无 `*` fallback（UNIQUE 约束允许 `*` 存在但不做 fallback 查找）
- 独立自增 ID，不与 model_routes 冲突

### 迁移

`Migrations` 新增 v5→v6：仅 `CREATE TABLE agent_routes` + `UPDATE schema_version SET version = 6`。无破坏性变更。

### ConfigDB 新增方法

- `list_agent_routes(request_type)` — 联表查询 agent_routes + target_models + upstreams
- `get_agent_route(route_id)` — 单条查询
- `add_agent_route(data)` — 新增（校验 target_model_id 存在 + 上游活跃）
- `update_agent_route(route_id, data)` — 编辑
- `delete_agent_route(route_id)` — 删除（无 `*` fallback 保护）
- `resolve_agent(source, request_type)` — 精确匹配一条 agent 路由，无 fallback

## 2. Agent 检测逻辑

### 新增模块 `proxy/agent_detector.py`

层级：第 0 层，零内部依赖。

```python
def detect_subagent(body: dict) -> bool:
    """检测请求是否来自 Claude Code 子 agent。"""
    # 信号 1: __SUBAGENT_MARKER__ 文本标记
    if _contains_marker(body, "__SUBAGENT_MARKER__"):
        return True

    # 信号 2: metadata.user_id 含 _agent_ 后缀
    user_id = body.get("metadata", {}).get("user_id", "")
    if user_id and "_agent_" in user_id:
        return True

    return False
```

信号 1 扫描位置：system 消息文本 + 所有 user 消息文本（context 压缩可能重排消息）。

### handler.py 集成

```
请求进入 → 解析 request_type + model
    ↓
detect_subagent(body) ?
    ├─ 是 → config_cache.resolve_agent(model, request_type)
    │         ├─ 命中 → 使用 agent 路由
    │         └─ 未命中 → 回退 config_cache.resolve(model, request_type)
    └─ 否 → config_cache.resolve(model, request_type)  ← 现有逻辑不变
```

## 3. ConfigCache 变更

### 新增 `_agent_cache` dict

与 `_cache` 独立。**永不过期**，仅通过 `reload()` 或 `POST /admin/reload` 清空。

### 新增方法 `resolve_agent(source, request_type)`

```python
def resolve_agent(self, source_name, request_type):
    """子 agent 专用路由查找 — 精确匹配，无 fallback。"""
    with self._lock:
        key = (source_name, request_type)
        if key in self._agent_cache:
            return self._agent_cache[key]
    data = self._db.resolve_agent(source_name, request_type)
    with self._lock:
        self._agent_cache[key] = data
    return data
```

### 新增方法 `get_all_agent_routes(request_type)`

供前端列表展示，走缓存。

### `reload()` 变更

同时清空 `_cache` 和 `_agent_cache`。

### `ConfigDB.resolve_agent()` SQL

```sql
SELECT tm.name as target_name, tm.multimodal, u.format,
       u.id as upstream_id, u.base_url, u.api_key,
       u.timeout, u.connect_timeout, u.ssl_verify, u.retry
FROM agent_routes ar
JOIN target_models tm ON ar.target_model_id = tm.id
JOIN upstreams u ON tm.upstream_id = u.id
WHERE ar.source = ? AND ar.request_type = ? AND u.is_active = 1
```

## 4. API 端点

`config_api.py` 新增：

| 方法 | 路径 | 用途 |
|------|------|------|
| GET | `/api/agent-routes?request_type=xxx` | 列表（按 request_type 筛选） |
| GET | `/api/agent-routes/:id` | 单条查询 |
| POST | `/api/agent-routes` | 新增 |
| PUT | `/api/agent-routes/:id` | 编辑 |
| DELETE | `/api/agent-routes/:id` | 删除 |

### 新增/编辑校验

- `source` 不能为空
- `request_type` 必须是 `responses`/`messages`/`chat_completions` 之一
- `target_model_id` 必须存在且所属上游 `is_active=1`
- 无 `*` fallback 保护逻辑

### `server/handler.py` 分发表变更

新增 `/api/agent-routes` 系列路径匹配，委托 `config_api.py` 处理。

## 5. UI 设计

### 布局：并列双表格

页面结构：

```
┌─────────────────────────────────────────┐
│  路由映射              + 回退路由 + 新增路由 │
├─────────────────────────────────────────┤
│  [🔌 Responses] [✉️ Messages] [🔗 Chat]  │  ← 三卡片切换，联动两个表格
├─────────────────────────────────────────┤
│  🔀 主路由               3              │  ← 现有表格，不变
│  ┌──────┬──────┬──────┬──────┬────┐     │
│  │源模型│目标模型│上游  │状态  │操作│     │
│  └──────┴──────┴──────┴──────┴────┘     │
├─────────────────────────────────────────┤
│  🤖 Agent 路由    覆盖层 · 1  + 新增Agent路由│  ← 琥珀色边框
│  ┌──────┬──────┬──────┬──────┬────┐     │
│  │源模型│覆盖目标│上游  │状态  │操作│     │
│  └──────┴──────┴──────┴──────┴────┘     │
└─────────────────────────────────────────┘
```

### 前端变更

**`routes.js`**：
- `loadRoutePage()` 在主路由 `.table-card` 下方追加 Agent 路由卡片 HTML
- `switchRequestType(rt)` 同时刷新：`loadRouteTable(rt)` + `loadAgentRouteTable(rt)`
- 新增 `loadAgentRouteTable(requestType)` — `GET /api/agent-routes?request_type=xxx`
- 新增 `showAgentRouteModal(editId)` — source 输入框 + 级联选择（上游→目标模型）
- 删除时无 `*` fallback 保护检查

**`routes.css` 新增**：
- `.agent-route-card` — 琥珀色边框变体
- `.agent-badge` — "覆盖层"标签样式
- `.route-override-hint` — "← 覆盖主路由"提示文字

### Agent 路由模态框

- 标题："新增 Agent 路由" / "编辑 Agent 路由 #id"
- 字段：源模型名（输入框）+ 上游（下拉）+ 目标模型（级联下拉）+ request_type（hidden，继承当前卡片）
- 无"回退路由"按钮
- 保存时 request_type 取自 `currentRequestType`

## 6. 完整数据流

```
客户端 POST /v1/responses (body 含 __SUBAGENT_MARKER__)
  ↓
handler.py → do_POST() → 解析 request_type + model
  ↓
agent_detector.detect_subagent(body) → True
  ↓
config_cache.resolve_agent(model, request_type)
  ├─ agent_routes 命中 → 使用 agent 路由的 upstream + target_model
  └─ agent_routes 未命中 → 回退 config_cache.resolve(model, request_type)
  ↓
后续流程不变（透传/转换/日志/token_stats）
```

### 日志增强

`debug_log` 的 `request_path` 阶段记录 `agent=true/false`，方便排查。

## 变更文件清单

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `proxy/agent_detector.py` | 新增 | 子 agent 检测模块 |
| `proxy/config_manager.py` | 修改 | 新增 `agent_routes` 表 + ConfigDB 方法 + ConfigCache 方法 + v6 迁移 |
| `proxy/handler.py` | 修改 | 集成 agent 检测 + agent 路由查找 |
| `proxy/__init__.py` | 修改 | re-export `detect_subagent` |
| `proxy/request_logger.py` | 修改 | 日志记录 agent 标记 |
| `server/config_api.py` | 修改 | agent-routes CRUD 端点 |
| `server/handler.py` | 修改 | 分发表新增 /api/agent-routes |
| `static/js/pages/routes.js` | 修改 | Agent 路由表格 + 模态框 + 联动 |
| `static/css/routes.css` | 修改 | Agent 路由卡片样式 |
| `test/test_config_manager.py` | 修改 | agent_routes CRUD + resolve_agent 测试 |
| `test/test_agent_detector.py` | 新增 | 检测信号测试 |
