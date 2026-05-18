# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 概述

`proxy/` 是 AI Proxy 核心包 —— 统一的 LLM API 代理，支持 **Responses / Messages / Chat Completions** 三种协议格式之间的 NxM 互转，纯 Python 标准库。

## 架构分层

```
Layer 0 (零依赖)          Layer 1 (转换逻辑)       Layer 2 (调度)        Layer 3 (入口)
──────────────────────────────────────────────────────────────────────────────────
schema.py                 transform/request/       common.py             handler.py
paths.py                  transform/response/      transform/__init__.py  __init__.py
sse_utils.py              transform/registry.py
token_stats.py            transform/router.py
response_store.py
request_logger.py
pricing_manager.py
agent_detector.py
config_manager.py
```

- **Layer 0** 文件之间也零依赖，各自独立
- **Layer 1** 依赖 Layer 0 的部分模块（如 `sse_utils`）
- **Layer 2** 粘合层，组合下层
- **Layer 3** HTTP 请求入口 + 公共 API re-export

## 请求处理流程

```
客户端 POST /v1/{responses|messages|chat/completions}
  ↓
handler.do_POST()
  ├─ 路径解析 → request_type
  ├─ detect_subagent(body) → 是否子代理请求
  ├─ config_cache.resolve(model, request_type) → upstream_cfg
  │    └─ ConfigDB (SQLite: model_routes + target_models + upstreams)
  │        优先级: source 精确匹配 → * fallback（同 request_type）
  │        跳过 is_active=0 的 upstream
  └─ request_type == upstream_cfg.format ?
       ├─ 是 → _handle_passthrough()  原样转发
       └─ 否 → _handle_convert()      协议转换
```

### 透传路径
直接转发 body 到上游，记录 token_stats，提取 usage。

### 转换路径
```
TransformRouter.convert_request()
  → registry.lookup_request(client_fmt, upstream_fmt)
  → e.g. responses_to_chat() 或 anthropic_to_chat()
  → 发送到上游（Chat Completions 格式）
  → TransformRouter.convert_response() / stream_convert()
  → registry.lookup_response(upstream_fmt, client_fmt)
  → 返回给客户端
```

## 关键文件

### `handler.py` — 统一请求处理器
`ProxyHandler(BaseHTTPRequestHandler)` 约 1112 行。核心方法：
- `do_POST()` — 路径解析、子代理检测、模型解析、透传/转换分发
- `do_GET()` — `/health`、`/v1/models`、`/admin/reload`
- `_handle_passthrough()` — 非流式 + 流式透传
- `_handle_convert()` — 非流式 + 流式转换，含 `previous_response_id` 多轮支持

### `config_manager.py` — 动态配置管理
- **`ConfigDB`** — upstreams / target_models / model_routes / agent_routes 四表 CRUD
- **`ConfigCache`** — 线程安全内存缓存，按需加载，`reload()` 主动失效
- **`Migrations`** — v0→v7 数据库迁移，先备份再迁移
- 配置存储在 `~/.ai-agent-tools/data/access_log.db`（与 token_stats 共享）

### `transform/` 子包 — NxM 协议转换矩阵
```
transform/
  registry.py          # 注册表: (client_fmt, upstream_fmt) → 转换函数
  router.py            # TransformRouter 分发类
  request/
    anthropic.py       # Messages → Chat Completions
    responses.py       # Responses → Chat Completions
    _utils.py          # _fix_tool_message_order, _merge_consecutive_assistants
  response/
    anthropic.py       # Chat Completions → Messages（含流式 SSE）
    responses.py       # Chat Completions → Responses（含流式 SSE）
```

转换在模块导入时自注册：`register_request("messages", "chat_completions", anthropic_to_chat)`

### `common.py` — 共享工具
- `CONFIG` — proxy_config.yaml 运行时配置
- `config_cache` — 全局 ConfigCache 单例
- `resolve_model(model, request_type)` → `{target, multimodal, upstream}`
- `_create_upstream_conn(upstream_cfg, parsed, port)` — 支持 HTTP 代理隧道

### `request_logger.py` — 四阶段日志
- Stage 1: 客户端原始请求 → Stage 2: 代理转换后请求 → Stage 3: 上游原始响应 → Stage 4: 代理转换后响应
- 写入 `debug_log` 表，含 `request_path`（上下游 URL）、`session_id`
- 单例模式：`init_logger()` / `get_logger()`

### `token_stats.py` — Token 统计
- `_extract_tokens(usage)` — 统一三种 usage 格式（Anthropic/OpenAI Chat/OpenAI Responses）
- `record_token_stats(usage, context)` — 写入 `token_stats` 表

### `response_store.py` — 响应缓存
- `ResponseStore` — LRU + TTL 内存缓存（OrderedDict）
- 供 `previous_response_id` 多轮对话使用

### `agent_detector.py` — 子代理检测
- `detect_subagent(body)` — 检测 Claude Code/Codex 子代理标记
- 影响路由选择：子代理走 `agent_routes` 表，fallback 到 `model_routes`

### `schema.py` — 数据库表定义
所有 SQLite 表的单一真相来源。`ensure_table()` 幂等建表 + 索引。

### `sse_utils.py` — SSE 工具
`_format_sse_event()`, `_parse_sse_event()`, `iter_sse_events()`

## 导入约定
- proxy 包内：相对导入 `from .common import CONFIG`
- proxy 包外：绝对导入 `from proxy.handler import ProxyHandler`
- 协议转换统一走 `from proxy.transform import ...`

## 注意事项
- 无第三方依赖
- `config_cache.reload()` 由外部 `POST /admin/reload` 触发
- 透传 vs 转换判定取决于 `request_type == upstream_cfg.format`，不按路径固定
- agent_routes 表（v7 新增）用于子代理专项路由
