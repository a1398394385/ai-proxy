# Fallback 路由设计

**日期**: 2026-05-23
**状态**: 待实现

## 背景

当前 proxy 的重试机制在 4 个 `_forward_*` 方法中各自实现，只在**同一个上游**上重试（5xx / 连接错误）。重试耗尽后直接返回错误给客户端。

需要增加真正的 fallback 路由：主路由重试耗尽后，自动切换到备用路由（不同上游、不同模型）再试一次。

## 设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 触发条件 | 重试耗尽后 | 配合现有重试机制 |
| 配置粒度 | 路由级（`model_routes` 字段） | 每条路由可独立指定 fallback 目标 |
| 流式支持 | 支持，仅限 headers 未发送时 | 上游返回非 200 或连接失败时可 fallback；已开始流式传输则不可 |
| Fallback 重试 | 走 fallback 上游自己的 retry 配置 | 复用现有重试机制 |

## 数据模型

`model_routes` 表新增列：

```sql
ALTER TABLE model_routes ADD COLUMN fallback_target_model_id INTEGER DEFAULT NULL;
```

- 指向 `target_models(id)`，该 target_model 已有 `upstream_id`
- 可为 NULL（无 fallback）
- Fallback 目标可以是不同上游、不同协议格式

Schema 版本：v7 → v8。

## ConfigCache 变更

1. `resolve_one()` 返回 dict 中增加 `fallback_target_model_id` 字段
2. 新增 `resolve_by_target_model_id(target_model_id, request_type)` 方法：根据 target_model_id 反查完整路由配置（target_name + upstream 全部信息），供 fallback 解析时使用

## Proxy Handler 重构

### 核心变更

将 `do_POST` 中的路由分发逻辑提取为 `_try_route()` 方法：

```python
def do_POST(self):
    # ... 解析请求、路由解析 ...
    model_cfg = resolve(model_name, request_type)  # 含 fallback_target_model_id

    error = self._try_route(model_cfg, ...)
    if error and model_cfg.get("fallback_target_model_id"):
        fallback_cfg = config_cache.resolve_by_target_id(
            model_cfg["fallback_target_model_id"], request_type
        )
        if fallback_cfg:
            logging.info(f"Fallback 触发: ...")
            error = self._try_route(fallback_cfg, ..., is_fallback=True)

    if error:
        self._send_json(error["status"], error["body"])
```

### `_try_route()` 方法

封装当前 do_POST 中的 passthrough/convert 判定 + 执行：

- `request_type == upstream_format` → passthrough 路径
- 否则 → convert 路径
- 返回 `None`（成功，已写 wfile）或 `{"status": int, "body": dict}`（失败）

### `_forward_*` 方法改造

4 个方法的错误出口从"直接写 wfile"改为"返回错误 dict"：

| 场景 | 当前行为 | 改后行为 |
|------|---------|---------|
| 重试耗尽 + 连接错误 | `self._send_json(502, ...)` | `return {"status": 502, "body": {...}}` |
| 上游返回 5xx（重试耗尽） | 转发上游错误响应 | `return {"status": resp.status, "body": resp_body}` |
| 流式上游返回非 200 | 直接转发错误 | `return {"status": resp.status, "body": resp_body}` |
| 成功 | 写 wfile | 写 wfile，`return None` |

流式场景：上游返回 200 并开始流式传输后即"提交"，不可再 fallback。

## 日志

```
INFO  Fallback 触发: model=claude-sonnet-4-6, primary=deepseek-v4(上游A), fallback=gpt-4o(上游B), request_id=xxx
INFO  Fallback 成功: model=claude-sonnet-4-6, fallback_target=gpt-4o(上游B), status=200
ERROR Fallback 也失败: model=claude-sonnet-4-6, fallback_target=gpt-4o(上游B), status=502
```

## UI 变更

- **路由编辑弹窗**：新增"Fallback 目标模型"下拉框（可选），列出所有已注册的 target_models
- **路由表格行**：如有 fallback 配置，在目标模型 badge 旁显示 fallback 模型名（灰色小字）
- **默认路由（`*`）也支持配置 fallback**
- **Agent 路由不支持 fallback**（与当前无默认路由回退行为一致）

## 测试策略

1. **ConfigDB 单元测试**：新增列的 CRUD、`resolve_by_target_model_id` 方法
2. **Handler 测试**：mock 上游失败 → 验证 fallback 触发 → 验证 fallback 成功/失败
3. **流式测试**：验证上游非 200 触发 fallback、上游 200 后不触发 fallback
4. **Migration 测试**：v7 → v8 迁移正确性
