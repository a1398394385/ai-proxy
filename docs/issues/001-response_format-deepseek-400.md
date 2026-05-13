# Issue 001: DeepSeek 返回 "This response_format type is unavailable now"

**创建日期:** 2026-05-12
**来源:** trace `f95f9d366a4a4a2c`
**状态:** 待修复

---

## 问题描述

Codex 通过 AI Proxy 调用 DeepSeek 时，返回 400 错误：

```json
{
    "error": {
        "message": "Error from provider (DeepSeek): This response_format type is unavailable now",
        "type": "invalid_request_error",
        "param": null,
        "code": "invalid_request_error"
    }
}
```

## 请求链路

```
Codex
  → POST /v1/responses (model=gpt-5.5)
  → Proxy ProxyHandler.do_POST()
  → 无精确路由,走 * fallback (request_type=responses)
  → deepseek-v4-pro → OpenCodeGo (https://opencode.ai/zen/go/v1)
  → 转换路径: Responses API → Chat Completions
  → OpenCodeGo 转发 → DeepSeek API
  ← 400 "This response_format type is unavailable now"
```

## 路由详情

| 层级 | 值 |
|------|-----|
| 原始请求 model | `gpt-5.5` |
| 精确路由 | 无 |
| `*` fallback (request_type=responses) | `deepseek-v4-pro` (target_model_id=48) |
| 转发上游 | OpenCodeGo (`https://opencode.ai/zen/go/v1`) |
| 上游 format | `chat_completions` |
| 最终 Provider | DeepSeek |

## 数据流分析

### 原始请求 (`debug_log` stage=raw_request)

Codex 发送的是 **Responses API** 格式，关键字段：

```json
{
    "model": "gpt-5.5",
    "instructions": "You are Codex...",
    "input": [{"type": "message", "role": "developer", ...}],
    "text": {
        "format": {
            "type": "json_schema",
            "strict": true,
            "name": "codex_output_schema",
            "schema": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "required": ["verdict", "summary", "findings", "next_steps"],
                "properties": {
                    "verdict": {"type": "string", "enum": ["approve", "needs-attention"]},
                    "summary": {"type": "string"},
                    "findings": { "...": "审校项数组" },
                    "next_steps": { "...": "下一步操作数组" }
                }
            }
        }
    },
    "stream": true
}
```

### 转换后请求 (`debug_log` stage=converted_request)

`responses_to_chat()` 在 `proxy/transform_responses.py:98-101` 将 `text.format` 映射为 `response_format`：

```json
{
    "model": "deepseek-v4-pro",
    "messages": [...],
    "response_format": {
        "type": "json_schema",
        "json_schema": {
            "name": "codex_output_schema",
            "schema": { "大量 JSON Schema 定义" },
            "strict": true
        }
    },
    "stream": true,
    "stream_options": {"include_usage": true}
}
```

### 上游响应 (`debug_log` stage=upstream_response)

```json
{
    "error": {
        "message": "Error from provider (DeepSeek): This response_format type is unavailable now",
        "type": "invalid_request_error",
        "code": "invalid_request_error"
    }
}
```

## 根因

### 直接原因

`_map_response_format()` 正确地完成了**协议格式转换**（Responses API → Chat Completions），但转换后的 `response_format: {type: "json_schema", ...}` 被原封不动地发送给了 DeepSeek，而 **DeepSeek 的 Chat Completions API 不支持 `json_schema` 格式的 `response_format`**。

### 深层问题

> Proxy 的"转换"只改了协议格式（Responses ↔ Chat Completions），但没有处理各个 Provider 的能力差异。

| Provider | 协议格式 | 支持 json_schema？ | 支持 json_object？ |
|----------|---------|-------------------|-------------------|
| OpenAI | chat_completions | ✅ | ✅ |
| DeepSeek | chat_completions | ❌ | ❌ |
| Anthropic | messages | N/A | N/A |
| GLM | chat_completions | ❌（部分版本） | ❌ |
| 其他大部分 | chat_completions | ❌ | ❌ |

### 涉及代码

```python
# proxy/transform_responses.py:98-101
text_format = body.get("text", {}).get("format")
if text_format:
    chat["response_format"] = _map_response_format(text_format)

# proxy/transform_responses.py:318-332
def _map_response_format(text_format: dict) -> dict:
    fmt_type = text_format.get("type", "text")
    if fmt_type == "json_schema":
        return {
            "type": "json_schema",
            "json_schema": {
                "name": text_format.get("name", ""),
                "schema": text_format.get("schema", {}),
                "strict": text_format.get("strict", False),
            },
        }
    else:
        return {"type": fmt_type}
```

转换路径入口：

```python
# proxy/handler.py:172-183
if request_type == upstream_format and upstream_format:
    # 透传路径：response_format 原样传送（不做任何检查）
    self._handle_passthrough(...)
else:
    # 转换路径：生成 response_format，同样不做 Provider 兼容性检查
    self._handle_convert(...)
```

## 修复方案

### 方案 A：直接剥离（最简单）

在 `_handle_convert()` 转换完成后，对非 OpenAI 上游剥离 `response_format`。

**好处：** 改动最小，错误消失
**坏处：** DeepSeek 输出的 JSON 不再受 Schema 约束，Codex 可能解析失败（静默失败 > 显式报错）

### 方案 B：转 Prompt 指令（推荐）

将 `json_schema` 转换为 system message 中的格式说明，引导 DeepSeek 按要求输出：

```python
# 示例逻辑
if unsupported and text_format.type == "json_schema":
    system_msg = chat_body["messages"][0]
    system_msg["content"] += (
        "\n\n你必须输出符合以下 JSON Schema 的 JSON 对象：\n"
        + json.dumps(text_format["schema"], indent=2, ensure_ascii=False)
    )
    del chat_body["response_format"]
```

**好处：** DeepSeek 知道要输出什么格式，大部分情况下能工作
**坏处：** 没有 `strict: true` 的强制约束，偶尔可能输出不合规的 JSON

### 方案 C：上游抽象字段（灵活但改动大）

在 `upstreams` 表加 `supports_structured_output INTEGER DEFAULT 0`，配合 migration：

1. 新增字段，上游是否为能处理 `json_schema` 的 Provider
2. `_handle_convert()` 和 `_handle_passthrough()` 根据此字段决定是否剥离/转换
3. 即使路由变化也不受影响

**好处：** 灵活，未来加 OpenAI / Claude 原生接口时直接开关
**坏处：** 改动较大，涉及 migration、YAML 配置

### 方案 D：路由到 OpenAI

当检测到 `response_format == json_schema` 且路由到不支持的上游时，自动将请求重新路由给 OpenAI 或另一个支持结构化的 Provider。

**好处：** 功能完整，结构化输出有保障
**坏处：** 复杂度高，涉及动态路由变更

---

## 配置参考

当前 `config.db` 相关路由数据：

```sql
-- upstreams
Deepseek Chat  | https://api.deepseek.com           | chat_completions
Deepseek Anthropic | https://api.deepseek.com/anthropic | messages
OpenCodeGo     | https://opencode.ai/zen/go/v1      | chat_completions

-- model_routes (source=*, request_type=responses)
* → target_model_id=48 (deepseek-v4-pro, OpenCodeGo, chat_completions)
```

`*` fallback 优先级：
- `request_type=responses` → deepseek-v4-pro (OpenCodeGo)
- `request_type=messages` → deepseek-v4-pro (Deepseek Anthropic)
- `request_type=chat_completions` → glm-5 (WallTech-Cargo)

---

## 复现

```python
curl -X POST http://localhost:48743/v1/responses \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-5.5",
    "instructions": "生成 JSON 输出",
    "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
    "text": {
      "format": {
        "type": "json_schema",
        "strict": true,
        "name": "test_schema",
        "schema": {
          "type": "object",
          "properties": {
            "result": {"type": "string"}
          },
          "required": ["result"]
        }
      }
    }
  }'
```

预期：返回 400 "This response_format type is unavailable now"
