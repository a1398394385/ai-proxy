---
name: anthropic-messages-api
description: Anthropic Messages API 请求体完整参考文档。需要在编写或审查 Anthropic Messages 请求格式、messages 字段定义、content block 类型（text/image/tool_use/tool_result/thinking/redacted_thinking）、tools 定义（input_schema）、thinking 扩展思考配置、system 提示词、Anthropic ↔ Chat Completions 协议转换时使用此 skill。触发场景：构建 Messages API 请求、调试 Anthropic 格式错误、理解 content blocks 结构、编写 Anthropic 协议转换逻辑。
metadata:
  source:
    - https://platform.claude.com/docs/en/api/messages (via Context7)
    - https://docs.anthropic.com/en/api/messages
  fetched: 2026-05-15
---

# Anthropic Messages API 请求体参考

> 来源: platform.claude.com/docs/en/api/messages
> 获取方式: Context7 (官方文档对中国 region block)
> 获取日期: 2026-05-15

## Endpoint

```
POST https://api.anthropic.com/v1/messages
```

**Headers:**
```
x-api-key: <API Key>
anthropic-version: 2023-06-01
Content-Type: application/json
```

---

## 一、messages 参数

`messages`: `array` — **必填**。对话历史，user 和 assistant 严格交替。

### 1.1 基本结构

每条消息：
```
role: "user" | "assistant"
content: string | array of content blocks
```

- 连续同 role 的消息会被合并为单轮
- 最多 100,000 条消息
- 字符串 content 等价于 `[{"type": "text", "text": "..."}]`

### 1.2 示例

**单一 user 消息:**
```json
{"role": "user", "content": "Hello, Claude"}
```

**多轮对话:**
```json
[
  {"role": "user", "content": "你好。"},
  {"role": "assistant", "content": "你好！有什么可以帮你的？"},
  {"role": "user", "content": "给我解释一下大语言模型。"}
]
```

**部分 assistant 响应（prefill）:**
```json
[
  {"role": "user", "content": "太阳的希腊名是？(A) Sol (B) Helios (C) Sun"},
  {"role": "assistant", "content": "最佳答案是 ("}
]
```

---

## 二、Content Block 类型

每个 `content` 数组可包含以下 6 种 block 类型：

### 2.1 text block

**出现方:** user, assistant

```json
{
  "type": "text",
  "text": "文本内容",
  "cache_control": {"type": "ephemeral"}  // optional
}
```

**citation 扩展（assistant text 内）:**
```json
{
  "type": "text",
  "text": "根据文档...",
  "citations": [
    {
      "type": "char_location",
      "cited_text": "引用文本",
      "document_index": 0,
      "document_title": "文档名",
      "start_char_index": 10,
      "end_char_index": 30
    }
  ]
}
```

### 2.2 image block

**出现方:** user

```json
{
  "type": "image",
  "source": {
    "type": "base64",
    "media_type": "image/png",
    "data": "<base64 图片数据>"
  }
}
```

支持格式: `image/jpeg`, `image/png`, `image/gif`, `image/webp`

### 2.3 tool_use block

**出现方:** assistant

```json
{
  "type": "tool_use",
  "id": "toolu_01D7FLrfh4GYq7yT1ULFeyMV",
  "name": "get_stock_price",
  "input": {"ticker": "^GSPC"}
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `string` | 工具调用唯一 ID，后续 tool_result 引用 |
| `name` | `string` | 工具名 |
| `input` | `object` | 符合工具 `input_schema` 的参数 |

### 2.4 tool_result block

**出现方:** user（⚠️ 用 user 消息包装）

```json
{
  "type": "tool_result",
  "tool_use_id": "toolu_01D7FLrfh4GYq7yT1ULFeyMV",
  "content": "259.75 USD"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `tool_use_id` | `string` | 对应 tool_use 的 id |
| `content` | `string \| array` | 工具执行结果，支持嵌套 content blocks |
| `cache_control` | `object` (optional) | 缓存控制 |

### 2.5 thinking block

**出现方:** assistant（启用 extended thinking 时）

```json
{
  "type": "thinking",
  "thinking": "让我一步步分析这个问题..."
}
```

### 2.6 redacted_thinking block

**出现方:** assistant（`display: "omitted"` 时）

```json
{
  "type": "redacted_thinking",
  "data": "<加密签名>"
}
```

用于多轮连续性——即使思考内容被隐藏，签名仍需在后续请求中回传。

---

## 三、system 参数

`system`: `string | array` — **顶层参数**，不是 messages 数组内的消息。

### 3.1 字符串简写

```json
{"system": "You are a helpful assistant."}
```

### 3.2 数组格式（多个 text block）

```json
{
  "system": [
    {"type": "text", "text": "你是一个帮助用户的助手。", "cache_control": {"type": "ephemeral"}},
    {"type": "text", "text": "请用中文回答。"}
  ]
}
```

⚠️ system 中只支持 `type: "text"` 的 block。

---

## 四、tools 参数

`tools`: `array` — 工具定义列表。

### 4.1 结构

```json
{
  "name": "get_stock_price",
  "description": "获取指定股票代码的当前价格。",
  "input_schema": {
    "type": "object",
    "properties": {
      "ticker": {
        "type": "string",
        "description": "股票代码，例如 AAPL"
      }
    },
    "required": ["ticker"]
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | `string` | **必填**，工具名 |
| `description` | `string` | 强烈推荐，描述工具功能 |
| `input_schema` | `object` | **必填**，JSON Schema 格式的参数定义 |
| `cache_control` | `object` (optional) | `{type: "ephemeral"}` |
| `strict` | `boolean` (optional) | 严格 schema 遵循 |

### 4.2 高级选项

| 字段 | 说明 |
|------|------|
| `AllowedCallers` | `["direct"]` 或 `["code_execution_20250825"]`，限制调用来源 |
| `DeferLoading` | 延迟加载工具定义（用于 tool search） |
| `EagerInputStreaming` | 流式传输工具参数 |
| `InputExamples` | 有效输入示例列表 |

---

## 五、thinking 参数

`thinking`: `object` — 扩展思考配置。

### 5.1 enabled

```json
{
  "type": "enabled",
  "budget_tokens": 4096,
  "display": "summarized"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | `"enabled"` | 固定值 |
| `budget_tokens` | `number` | **必填**，≥1024 且 < max_tokens |
| `display` | `"summarized"\|"omitted"` | 思考内容展示方式，默认 `summarized` |

### 5.2 disabled

```json
{"type": "disabled"}
```

### 5.3 adaptive

```json
{
  "type": "adaptive",
  "display": "summarized"
}
```

Claude 自动决定是否使用扩展思考。

---

## 六、全部请求参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `model` | `string` | **是** | 模型 ID，如 `claude-opus-4-7` |
| `messages` | `array` | **是** | 对话消息列表 |
| `max_tokens` | `int` | **是** | 生成的最大 token 数 |
| `system` | `string\|array` | 否 | 系统提示词 |
| `tools` | `array` | 否 | 工具定义 |
| `tool_choice` | `object` | 否 | 工具选择策略 |
| `thinking` | `object` | 否 | 扩展思考配置 |
| `stream` | `boolean` | 否 | 流式响应 |
| `temperature` | `float` (0-1) | 否 | 采样温度 |
| `top_p` | `float` (0-1) | 否 | nucleus sampling |
| `top_k` | `int` | 否 | 仅保留 top-k 个 token |
| `stop_sequences` | `array` | 否 | 停止序列 |
| `metadata` | `object` | 否 | user_id 等元数据 |
| `output_config` | `object` | 否 | 输出格式配置 |

---

## 七、tool_choice 选项

`tool_choice`: `object` — 控制模型如何使用工具。

| 值 | 说明 |
|----|------|
| `{"type": "auto"}` | 模型自动决定（默认） |
| `{"type": "any"}` | 强制调用至少一个工具 |
| `{"type": "tool", "name": "get_stock_price"}` | 强制调用指定工具 |
| `{"type": "none"}` | 不调用任何工具 |

---

## 八、output_config 参数

```json
{
  "output_config": {
    "format": {"type": "text"},
    "effort": "high"
  }
}
```

`effort` 非 Anthropic 官方字段，来自 Claude Code 内部扩展（Beta API），用于控制输出强度。

---

## 九、Content Block 出现规则总结

| block type | user 消息 | assistant 消息 | 说明 |
|-----------|-----------|---------------|------|
| `text` | ✓ | ✓ | 文本内容 |
| `image` | ✓ | ✗ | 图片输入 |
| `tool_use` | ✗ | ✓ | 工具调用 |
| `tool_result` | ✓ | ✗ | 工具结果（用 user 包装） |
| `thinking` | ✗ | ✓ | 思考过程 |
| `redacted_thinking` | ✗ | ✓ | 加密思考内容 |

---

## 十、典型消息序列

### 纯文本对话

```
user:       [text]
assistant:  [text]
user:       [text]
assistant:  [text]
```

### 工具调用

```
user:       [text]
assistant:  [thinking, tool_use]
user:       [tool_result]
assistant:  [thinking, text]
```

### 多工具并行

```
user:       [text]
assistant:  [thinking, tool_use, tool_use, tool_use]
user:       [tool_result, tool_result, tool_result]
assistant:  [thinking, text]
```

---

## 十一、与 Chat Completions 的关键差异

| 特性 | Anthropic Messages | Chat Completions |
|------|-------------------|-----------------|
| 端点 | `POST /v1/messages` | `POST /v1/chat/completions` |
| system | 顶层 `system` 参数 | messages 数组内的 `role: "system"` |
| 消息交替 | user ↔ assistant（严格） | user ↔ assistant |
| 工具调用 | `tool_use` block (assistant 内) | `tool_calls` 数组 (assistant 顶层) |
| 工具结果 | `tool_result` block (user 内) | `role: "tool"` 独立消息 |
| 思考 | `thinking` block (assistant 内) | `reasoning_content` 字段 (非标准) |
| 图片 | `image` block 内 `source` 对象 | `image_url` content part |
| required 参数 | model, messages, max_tokens | model, messages |
| 版本标头 | `anthropic-version` | 无 |

---

## 十二、示例

### 最简请求
```json
{
  "model": "claude-sonnet-4-6",
  "max_tokens": 1024,
  "messages": [
    {"role": "user", "content": "Hello, Claude"}
  ]
}
```

### 带 system 提示词
```json
{
  "model": "claude-sonnet-4-6",
  "max_tokens": 1024,
  "system": "You are a helpful coding assistant. Use Chinese.",
  "messages": [
    {"role": "user", "content": "帮我写一个冒泡排序"}
  ]
}
```

### 带工具
```json
{
  "model": "claude-sonnet-4-6",
  "max_tokens": 1024,
  "tools": [
    {
      "name": "get_weather",
      "description": "获取指定城市的天气",
      "input_schema": {
        "type": "object",
        "properties": {
          "location": {"type": "string", "description": "城市名"}
        },
        "required": ["location"]
      }
    }
  ],
  "messages": [
    {"role": "user", "content": "北京今天天气怎么样？"}
  ]
}
```

### 带扩展思考
```json
{
  "model": "claude-opus-4-7",
  "max_tokens": 8192,
  "thinking": {
    "type": "enabled",
    "budget_tokens": 4096
  },
  "messages": [
    {"role": "user", "content": "这道数学证明题应该如何解？"}
  ]
}
```

### 带图片
```json
{
  "model": "claude-sonnet-4-6",
  "max_tokens": 1024,
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "这张图片里有什么？"},
        {
          "type": "image",
          "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": "iVBORw0KGgo..."
          }
        }
      ]
    }
  ]
}
```

### 多轮工具调用
```json
{
  "model": "claude-sonnet-4-6",
  "max_tokens": 1024,
  "tools": [{"name": "search", "description": "搜索网络", "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}],
  "messages": [
    {"role": "user", "content": "搜索一下猫的图片。"},
    {
      "role": "assistant",
      "content": [
        {"type": "tool_use", "id": "call_1", "name": "search", "input": {"query": "猫"}}
      ]
    },
    {
      "role": "user",
      "content": [
        {"type": "tool_result", "tool_use_id": "call_1", "content": "找到 3 张猫的图片。"}
      ]
    }
  ]
}
```

---

## 注意事项

1. **`max_tokens` 是必填的** — 与 Chat Completions 不同，Anthropic 要求必传
2. **system 是顶层参数** — 不在 messages 数组内
3. **tool_result 用 user 包装** — Anthropic 没有独立的 tool role
4. **消息严格交替** — user/assistant/user/assistant...
5. **`anthropic-version` header 必传** — 如 `2023-06-01`
6. **thinking 计入 max_tokens** — budget_tokens 必须 < max_tokens
7. **图片仅限 user 消息** — assistant 不能包含 image block
