# 路由映射页 — 目标模型改为级联选择

**日期:** 2026-05-13
**范围:** 仅前端，不修改后端

## 目标

将路由映射页（新增/编辑路由和回退路由模态框）中目标模型的单一 `<select>`（按上游 optgroup 分组）改为二级联选择：先选上游，再选模型。

## 当前状态

`static/js/pages/routes.js` → `showRouteModal()` / `showFallbackModal()`:

- 一次性加载全部模型 + 上游
- 目标模型是带 `<optgroup>` 的单一 `<select>`
- 用户从所有模型中直接挑选

### 相关后端结构与 API

- `upstreams` 表（v4→v5 迁移后）：`id INTEGER PRIMARY KEY AUTOINCREMENT`，`name TEXT`（曾为旧版 id TEXT），`format`，`is_active` 等
- `target_models` 表：`id INTEGER PRIMARY KEY`，`upstream_id INTEGER FK → upstreams.id`，`name TEXT`
- 路由查询 `list_routes()` / `get_route()` 返回 `tm.upstream_id`（整数），**不包含上游名称**
- `GET /api/upstreams` → `list_upstreams()` 返回 `SELECT * FROM upstreams`，含整数 `id` 和文本 `name`
- `GET /api/models?upstream_id=X` → `list_models(upstream_id)` 按整数 upstream_id 过滤

## 设计方案

替换为两个联动的 `<select>`：

```
[上游选择框: openai ▼]  →  [模型选择框: gpt-4o ▼]
```

### 上游选择框

- 显示 `upstream.name`（人类可读的名称），value 为 `upstream.id`（整数）
- 选项保留 format 后缀：`upstream.name (format_short)`，如 `openai (Chat)`
- 所有上游（含禁用的）均列出，禁用上游显示为灰色

### 模型选择框

- 初始状态为 `disabled`，显示「请先选择上游」
- 上游选择框 `change` 事件触发异步调用 `GET /api/models?upstream_id=X`
- 加载期间显示「加载中…」，模型框 `disabled`
- 加载完成后模型框 `enabled`，显示该上游的模型列表
- 切换上游时：清空并 disable 模型框，重新加载

### 编辑已有路由

编辑模态框打开时涉及一个异步链：

1. `Promise.all([api('/api/routes'), api('/api/models'), api('/api/upstreams')])` → 路由数据 + 模型 + 上游
2. 从路由中找到编辑对象，拿到 `upstream_id`（整数）
3. 在上游选择框中通过 value 匹配选中对应上游
4. 手动触发上游 `change` 事件 → 加载该上游模型
5. 加载完成后，从已加载的模型列表中找到对应 `target_model_id` 的模型，选中它

**关键**：编辑场景不能依赖模型列表的预加载结果来匹配上游（模型列表很大且按上游分组），而是直接根据路由的 `upstream_id` 匹配上游列表中的 `id` 字段——上游列表小且已全部加载。

### 保存

仍然提交 `target_model_id`，与现有 `saveRoute()` 逻辑完全兼容，不修改后端 API。

## 实现方式

建议抽取一个公共函数 `buildCascadingModelSelect(selectedUpstreamId, selectedModelId)`，返回上游框 + 模型框的 HTML 字符串，供 `showRouteModal()` 和 `showFallbackModal()` 共用。两个模态框的级联选择逻辑完全一致，不应重复。

## 涉及修改

仅一个文件：`static/js/pages/routes.js`

- 新增 `buildCascadingModelSelect()` 辅助函数
- `showRouteModal()` — 替换 modelOpts optgroup 逻辑为级联选择
- `showFallbackModal()` — 同上

## 边界情况

| 场景 | 处理 |
|------|------|
| 上游全部禁用 | 上游框空，模型框 disabled +「请先选择上游」 |
| 编辑时上游已禁用 | 上游仍在下拉列表中出现，可按 upstream_id 匹配选中 |
| 上游无模型 | 模型框显示「暂无模型」 |
| 未选上游时 | 模型框 disabled + 提示文字 |
| 加载模型中 | 模型框 disabled +「加载中…」 |
| 编辑时模型数据尚未加载 | 上游选中后自动触发异步加载，加载完成后自动选中模型 |

## 后续可优化项（当前不处理）

- 路由表格第 22 行显示的是 `r.upstream_id`（整数，不可读），若改为显示 `upstream_name` 需要后端额外返回字段，不在本次范围内
