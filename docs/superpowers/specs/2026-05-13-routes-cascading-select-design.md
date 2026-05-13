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

## 设计方案

替换为两个联动的 `<select>`：

```
[上游选择框: openai ▼]  →  [模型选择框: gpt-4o ▼]
```

### 交互

1. 用户先在上游框选择一个上游
2. 模型框调用 `GET /api/models?upstream_id=X` 加载该上游的模型
3. 用户再在模型框选择具体模型
4. 切换上游时模型框重置

### 编辑已有路由

根据 `target_model` 的 `upstream_name` 自动选中对应上游，模型框加载该上游的模型并选中当前模型。

### 保存

仍然提交 `target_model_id`，与现有 `saveRoute()` 逻辑完全兼容，不修改后端 API。

## 涉及修改

仅一个文件：`static/js/pages/routes.js`

- `showRouteModal()` — 替换 modelOpts optgroup 逻辑为双 select
- `showFallbackModal()` — 同上

两个函数改动一致。

## 边界情况

| 场景 | 处理 |
|------|------|
| 上游全部禁用 | 上游框空，模型框空 |
| 编辑时上游已禁用 | 直接按 upstream_name 匹配（API 返回含禁用的上游） |
| 上游无模型 | 模型框显示「暂无模型」 |
| 未选上游时 | 模型框 disabled + 提示文字 |
