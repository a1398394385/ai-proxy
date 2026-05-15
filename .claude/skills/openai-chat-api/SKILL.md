---
name: openai-chat-api
description: OpenAI Chat Completions API 请求体完整参考文档。需要在编写或审查 Chat Completions 请求格式、messages 字段定义、role 类型、tool_calls 结构、tool_choice、tools 定义、推理模型参数时使用此 skill。触发场景：构建 Chat Completions 请求、转换协议到/从 Chat 格式、调试 400 错误、验证 messages 结构、查询参数约束、编写转换逻辑。
metadata:
  source:
    - https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create
    - https://api-docs.deepseek.com/guides/thinking_mode
  fetched: 2026-05-15
---

# OpenAI Chat Completions API 请求体参考

> 来源: developers.openai.com — Chat Completions API Reference
> 抓取日期: 2026-05-15

## Endpoint

```
POST https://api.openai.com/v1/chat/completions
```

---

## 一、Messages 类型定义

`messages`: `array of ChatCompletionMessageParam` — 对话历史，按时间顺序排列。

共 6 种消息类型（one of）：

---

### 1.1 ChatCompletionDeveloperMessageParam

```
role: "developer"
content: string | array of ChatCompletionContentPartText
name: string (optional)
```

**content 两种格式：**

| 格式 | 类型 | 说明 |
|------|------|------|
| `TextContent` | `string` | 纯文本 |
| `ArrayOfContentParts` | `array` | 仅支持 `type: "text"` |

**text part 结构：**
```json
{"type": "text", "text": "内容文本"}
```

**用途：** o1 及更新模型替代 system。developer 指令优先级高于 user 消息。

---

### 1.2 ChatCompletionSystemMessageParam

```
role: "system"
content: string | array of ChatCompletionContentPartText
name: string (optional)
```

与 developer 结构完全一致。对于 o1 及更新的模型，应使用 `developer` 替代。

---

### 1.3 ChatCompletionUserMessageParam

```
role: "user"
content: string | array of ChatCompletionContentPart
name: string (optional)
```

**content 两种格式：**

| 格式 | 类型 | 说明 |
|------|------|------|
| `TextContent` | `string` | 纯文本 |
| `ArrayOfContentParts` | `array` | 可混搭 text / image / audio / file |

**array 元素类型（one of）：**

**① ChatCompletionContentPartText**
```json
{"type": "text", "text": "文本内容"}
```

**② ChatCompletionContentPartImage**
```json
{
  "type": "image_url",
  "image_url": {
    "url": "https://... 或 data:image/png;base64,...",
    "detail": "auto" | "low" | "high"   // optional
  }
}
```

**③ ChatCompletionContentPartInputAudio**
```json
{
  "type": "input_audio",
  "input_audio": {
    "data": "<base64 音频数据>",
    "format": "wav" | "mp3"
  }
}
```

**④ FileContentPart**
```json
{
  "type": "file",
  "file": {
    "file_data": "<base64 文件内容>",   // optional
    "file_id": "file-xxx",              // optional (已上传文件)
    "filename": "example.py"            // optional
  }
}
```

---

### 1.4 ChatCompletionAssistantMessageParam

```
role: "assistant"
content: string | array of (...) | null (optional — tool_calls/function_call 存在时可为 null)
tool_calls: array of ChatCompletionMessageToolCall (optional)
function_call: {arguments, name} (deprecated, optional)
refusal: string (optional)
audio: {id} (optional)
name: string (optional)
```

**content 三种格式（one of）：**

| 格式 | 类型 | 说明 |
|------|------|------|
| `TextContent` | `string` | 纯文本 |
| `ArrayOfContentParts` | `array` | text 或 refusal |
| `null` | — | tool_calls 存在时 |

**array 元素类型（one of）：**
- `ChatCompletionContentPartText` → `{"type": "text", "text": "..."}`
- `ChatCompletionContentPartRefusal` → `{"type": "refusal", "refusal": "..."}`

---

### 1.5 ChatCompletionToolMessageParam

```
role: "tool"
content: string | array of ChatCompletionContentPartText
tool_call_id: string  ← 必须！对应 tool_calls[i].id
```

**示例：**
```json
{
  "role": "tool",
  "tool_call_id": "call_abc123",
  "content": "查询结果：72°F, 晴天"
}
```

---

### 1.6 ChatCompletionFunctionMessageParam (deprecated)

```
role: "function"
content: string
name: string  ← 必须！对应 function_call 中的函数名
```

已被 `tool` + `tool_call_id` 替代。

---

## 二、tool_calls 结构（assistant 消息内）

`tool_calls`: `array of ChatCompletionMessageToolCall` — **one of：**

### 2.1 ChatCompletionMessageFunctionToolCall

```json
{
  "id": "call_abc123",
  "type": "function",
  "function": {
    "name": "get_current_weather",
    "arguments": "{\"location\": \"Boston, MA\"}"
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `string` | 工具调用 ID，tool 消息通过 `tool_call_id` 引用 |
| `type` | `"function"` | 固定值 |
| `function.name` | `string` | 函数名 |
| `function.arguments` | `string` | JSON 字符串（需 `json.loads` 解析）|

### 2.2 ChatCompletionMessageCustomToolCall

```json
{
  "id": "call_xyz",
  "type": "custom",
  "custom": {
    "name": "my_custom_tool",
    "input": "用户输入"
  }
}
```

---

## 三、tool_choice 选项

`tool_choice`: `string | object` — 控制模型使用工具的策略。

**字符串模式（ToolChoiceMode）：**
- `"none"` — 不调用任何工具
- `"auto"` — 自动选择（默认值，当 tools 存在时）
- `"required"` — 强制调用至少一个工具

**对象模式：**

**ChatCompletionAllowedToolChoice** — 限定可选工具集合：
```json
{
  "type": "allowed_tools",
  "allowed_tools": {
    "mode": "auto" | "required",
    "tools": [
      {"type": "function", "function": {"name": "get_weather"}},
      {"type": "function", "function": {"name": "get_time"}}
    ]
  }
}
```

**ChatCompletionNamedToolChoice** — 强制调用指定函数：
```json
{"type": "function", "function": {"name": "get_weather"}}
```

**ChatCompletionNamedToolChoiceCustom** — 强制调用指定自定义工具：
```json
{"type": "custom", "custom": {"name": "my_tool"}}
```

---

## 四、tools 定义

`tools`: `array of ChatCompletionTool` — **one of：**

### 4.1 ChatCompletionFunctionTool

```json
{
  "type": "function",
  "function": {
    "name": "get_current_weather",
    "description": "获取指定位置的当前天气",
    "parameters": {
      "type": "object",
      "properties": {
        "location": {
          "type": "string",
          "description": "城市和州，例如 San Francisco, CA"
        },
        "unit": {
          "type": "string",
          "enum": ["celsius", "fahrenheit"]
        }
      },
      "required": ["location"]
    },
    "strict": true   // optional: 是否严格遵循 schema
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | `"function"` | 固定值 |
| `function.name` | `string` | 函数名，max 64 字符，a-z/A-Z/0-9/_/- |
| `function.description` | `string` (optional) | 功能描述 |
| `function.parameters` | `object` (optional) | JSON Schema 格式的参数定义 |
| `function.strict` | `boolean` (optional) | 是否启用严格 Structured Outputs |

### 4.2 ChatCompletionCustomTool

```json
{
  "type": "custom",
  "custom": {
    "name": "grammar_tool",
    "description": "语法检查工具",
    "format": {
      "type": "text"   // 或 "grammar"
    }
  }
}
```

**format 类型：**
- `TextFormat` — `{"type": "text"}` 无约束自由文本
- `GrammarFormat` — `{"type": "grammar", "grammar": {"definition": "...", "syntax": "lark" | "regex"}}`

---

## 五、finish_reason 枚举

choices[i].finish_reason 的可能值：

| 值 | 含义 |
|----|------|
| `"stop"` | 自然停止点或命中 stop 序列 |
| `"length"` | 达到 max_tokens / max_completion_tokens 上限 |
| `"tool_calls"` | 模型决定调用工具 |
| `"content_filter"` | 内容被安全过滤 |
| `"function_call"` | deprecated — 旧版函数调用 |

---

## 六、关键请求参数速查

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `model` | `string` | **必填** | 模型 ID，如 gpt-4o, o3, gpt-5.4 |
| `messages` | `array` | **必填** | 对话消息列表 |
| `max_completion_tokens` | `int` | — | 完成的最大 token 数（含推理 token） |
| `max_tokens` | `int` (deprecated) | — | 旧版，已弃用，不兼容 o-series |
| `temperature` | `float` (0-2) | — | 采样温度 |
| `top_p` | `float` (0-1) | — | Nucleus 采样 |
| `n` | `int` (1-128) | 1 | 生成几个候选回答 |
| `stream` | `boolean` | — | 是否流式返回 |
| `stream_options` | `object` | — | `{include_usage, include_obfuscation}` |
| `stop` | `string | array` | — | 最多 4 个停止序列 |
| `logprobs` | `boolean` | — | 是否返回 log 概率 |
| `top_logprobs` | `int` (0-20) | — | 需要 `logprobs: true` |
| `tool_choice` | — | — | 见第三章 |
| `tools` | `array` | — | 见第四章 |
| `parallel_tool_calls` | `boolean` | — | 是否启用并行工具调用 |
| `reasoning_effort` | `string` | 因模型而异 | none/minimal/low/medium/high/xhigh |
| `modalities` | `array` | `["text"]` | 输出类型：text, audio |
| `audio` | `object` | — | 音频输出配置 `{format, voice}` |
| `response_format` | `object` | — | `{type: "text"/"json_object"/"json_schema"}` |
| `seed` | `int` (deprecated) | — | 确定性采样（Beta） |
| `service_tier` | `string` | `"auto"` | auto/default/flex/priority |
| `frequency_penalty` | `float` (-2~2) | — | 频率惩罚 |
| `presence_penalty` | `float` (-2~2) | — | 存在惩罚 |
| `logit_bias` | `map[token→bias]` | — | 指定 token 的 logit 偏置 |
| `metadata` | `object` | — | 最多 16 个 key-value 对 |
| `prediction` | `object` | — | Predicted Outputs 内容 |
| `safety_identifier` | `string` | — | 用户唯一标识（代替旧 user 字段）|
| `prompt_cache_key` | `string` | — | 缓存优化 key |
| `prompt_cache_retention` | `string` | — | `"in_memory"` 或 `"24h"` |
| `verbosity` | `string` | — | low/medium/high |
| `web_search_options` | `object` | — | 网络搜索配置 |

---

## 七、reasoning_effort 详细规则

仅推理模型支持。取值：`none`, `minimal`, `low`, `medium`, `high`, `xhigh`。

| 模型 | 默认值 | 支持值 |
|------|--------|--------|
| gpt-5.1 | `none` | none, low, medium, high |
| gpt-5 之前模型 | `medium` | 不含 none |
| gpt-5-pro | `high` | 仅 high |
| gpt-5.1-codex-max 及之后 | — | 含 xhigh |

**注意：** gpt-5.1 的默认是 `none`（不执行推理），与其他推理模型不同。

---

## 八、消息顺序规则

Chat Completions 对消息排列有严格约束：

### 基本规则
```
user → assistant → user → assistant → ...
```

- 相邻消息的 role 不能相同（tool 消息除外）
- 首条消息通常为 `user`（或 `system`/`developer`）

### 涉及 tool 的规则
```
user → assistant+tool_calls → tool → tool → ... → assistant → user → ...
```

| 序列 | 是否允许 |
|------|----------|
| `user → assistant` | ✓ |
| `assistant → user` | ✓ |
| `user → user` | ✗ 禁止 |
| `assistant → assistant` | ✗ 禁止 |
| `tool → tool` | ✓ 允许多个工具调用结果连续 |
| `assistant+tool_calls → tool` | ✓ 必须（ID 必须匹配）|
| `assistant+tool_calls → user` | ✗ 禁止（必须先有 tool）|
| `tool → user` | ✗ 禁止（tool 之后必须是 assistant）|

### 关键约束
1. `assistant` 包含 `tool_calls` 时，后续消息必须是以相同 `tool_call_id` 响应的 `tool` 消息
2. 连续多个 `tool` 消息是允许的（一次执行多个工具调用）
3. 所有 tool 消息结束后，下一消息必须是 `assistant`（工具调用结果汇总）

---

## 九、响应格式摘要

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1702685778,
  "model": "gpt-4o",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "content": "回答文本",
      "tool_calls": [],   // optional
      "refusal": null,
      "annotations": []
    },
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 20,
    "total_tokens": 30,
    "prompt_tokens_details": {"cached_tokens": 0, "audio_tokens": 0},
    "completion_tokens_details": {
      "reasoning_tokens": 0,
      "audio_tokens": 0,
      "accepted_prediction_tokens": 0,
      "rejected_prediction_tokens": 0
    }
  },
  "service_tier": "default",
  "system_fingerprint": "fp_xxx"
}
```

---

## 十、常见用法示例

### 最小文本请求
```json
{
  "model": "gpt-4o",
  "messages": [
    {"role": "user", "content": "Hello!"}
  ]
}
```

### 带图片
```json
{
  "model": "gpt-4o",
  "messages": [{
    "role": "user",
    "content": [
      {"type": "text", "text": "这张图片里有什么？"},
      {"type": "image_url", "image_url": {"url": "https://example.com/photo.jpg"}}
    ]
  }]
}
```

### 带工具调用
```json
{
  "model": "gpt-4o",
  "messages": [
    {"role": "user", "content": "波士顿今天天气怎么样？"}
  ],
  "tools": [{
    "type": "function",
    "function": {
      "name": "get_weather",
      "description": "获取指定位置的天气",
      "parameters": {
        "type": "object",
        "properties": {
          "location": {"type": "string"}
        },
        "required": ["location"]
      }
    }
  }],
  "tool_choice": "auto"
}
```

### 多轮工具调用
```json
{
  "model": "gpt-4o",
  "messages": [
    {"role": "user", "content": "搜一下猫的图片。"},
    {
      "role": "assistant",
      "content": null,
      "tool_calls": [{
        "id": "call_1",
        "type": "function",
        "function": {"name": "search", "arguments": "{\"query\":\"猫\"}"}
      }]
    },
    {
      "role": "tool",
      "tool_call_id": "call_1",
      "content": "找到 3 张猫的图片。"
    },
    {"role": "assistant", "content": "我找到了 3 张猫的图片！"}
  ]
}
```

### 流式请求
```json
{
  "model": "gpt-4o",
  "messages": [{"role": "user", "content": "Hello!"}],
  "stream": true,
  "stream_options": {"include_usage": true}
}
```

### 推理模型
```json
{
  "model": "gpt-5.1",
  "messages": [{"role": "user", "content": "帮我分析一下..."}],
  "reasoning_effort": "high",
  "max_completion_tokens": 16000
}
```

---

## 注意事项

1. **`developer` vs `system`**：o1 及更新模型推荐使用 `developer`，但 `system` 仍被广泛支持
2. **`function_call` 已弃用**，应使用 `tool_calls` + `tool` 替代
3. **`max_tokens` 已弃用**，应使用 `max_completion_tokens`
4. **`seed` 已弃用**，使用 `safety_identifier` 和 `prompt_cache_key` 替代
5. **`user` 参数已弃用**，使用 `safety_identifier` 和 `prompt_cache_key` 替代

---

# DeepSeek 思考模式 (Thinking Mode)

> 来源: api-docs.deepseek.com/guides/thinking_mode
> 抓取日期: 2026-05-15

DeepSeek 模型支持思考模式：在输出最终回答之前，模型先输出思维链（Chain-of-Thought）推理过程，以提高最终响应的准确性。

---

## 一、开关与强度控制

### 1.1 思考开关

| 格式 | 参数 |
|------|------|
| OpenAI Chat Completions | `extra_body: {"thinking": {"type": "enabled"}}` 或 `"disabled"` |
| Anthropic Messages | `{"thinking": {"type": "enabled"}}` 或 `"disabled"` |

- **默认值**：`enabled`（开启）
- 使用 OpenAI SDK 时必须通过 `extra_body` 传递，不是标准参数

### 1.2 思考强度

| 格式 | 参数 |
|------|------|
| OpenAI | `reasoning_effort: "high"` 或 `"max"` |
| Anthropic | `output_config: {"effort": "high"}` 或 `"max"` |

| 规则 | 详情 |
|------|------|
| 默认强度 | 普通请求 `high`；Claude Code/OpenCode 等复杂 Agent 请求自动 `max` |
| 兼容映射 | `low`/`medium` → `high`；`xhigh` → `max` |

### 1.3 不支持的参数

思考模式下 **禁用** 以下参数（设置后不报错但不生效）：
- `temperature`
- `top_p`
- `presence_penalty`
- `frequency_penalty`

---

## 二、reasoning_content 字段

### 2.1 位置

`reasoning_content` 在响应中与 `content` 同级：

**非流式：**
```python
response.choices[0].message.reasoning_content  # 推理过程
response.choices[0].message.content            # 最终回答
response.choices[0].message.tool_calls         # 工具调用
```

**流式：**
```python
# 逐个 chunk 累积 reasoning_content
for chunk in response:
    if chunk.choices[0].delta.reasoning_content:
        reasoning_content += chunk.choices[0].delta.reasoning_content
    else:
        content += chunk.choices[0].delta.content
```

### 2.2 request 中的位置

`reasoning_content` 放在 assistant 消息的顶层（与 `content` 同级）：

```json
{
  "role": "assistant",
  "content": "最终回答文本",
  "reasoning_content": "思维链推理过程...",
  "tool_calls": []
}
```

### 2.3 便捷附加方式

DeepSeek 响应中的 `message` 对象已包含所有必要字段，可以直接附加：

```python
messages.append(response.choices[0].message)
# 等价于：
messages.append({
    "role": "assistant",
    "content": response.choices[0].message.content,
    "reasoning_content": response.choices[0].message.reasoning_content,
    "tool_calls": response.choices[0].message.tool_calls,
})
```

---

## 三、多轮对话规则（核心！）

### 3.1 无工具调用的回合

```
user → assistant(reasoning + content) → user → assistant(reasoning + content) → ...
```

**规则：** 中间 assistant 的 `reasoning_content` **无需**参与后续上下文拼接，即使传回也会被忽略。

```
正确做法：
  messages 列表直接 append(response.choices[0].message)
  → message 对象含 reasoning_content，后续回合 API 自动忽略它
```

### 3.2 有工具调用的回合 ⚠️

```
user → assistant(reasoning + tool_calls) → tool → tool → assistant(reasoning + content) → user → ...
```

**规则（极其重要）：**

> 如果模型执行了工具调用，该回合的 `reasoning_content` **必须参与上下文拼接**，且在**所有后续**用户交互回合中都必须传回 API。
>
> **如果未正确回传 `reasoning_content`，API 将返回 400 错误：**
> `"The reasoning_content in the thinking mode must be passed back to the API."`

### 3.3 规则总结表

| 场景 | reasoning_content 是否必须回传 |
|------|-------------------------------|
| 纯文本回答（无 tool call）| 否（传回也会被忽略）|
| 含工具调用 | **是，且在所有后续回合都要传** |
| 跨 turn 的工具调用（Turn 1 有 tool call，Turn 2 是新问题）| **是，Turn 1 的 reasoning_content 在 Turn 2 也要传** |

### 3.4 典型错误排查

当遇到 `"The reasoning_content in the thinking mode must be passed back to the API"` 错误：
1. **检查 assistant 消息是否缺少 `reasoning_content`** — 所有含 tool_calls 的 assistant 消息必须带此字段
2. **检查跨回合传递** — 即使新回合没有新的 tool call，历史有 tool call 的 reasoning_content 也要保留
3. **检查 SSE 流转换** — 流式处理时确保正确累积并回传 `reasoning_content`
4. **检查非同轮的一致性** — 同一会话中，如果某些 assistant 消息有 `reasoning_content` 而有些没有，DeepSeek 会拒绝

### 3.5 构造 assistant 消息时 reasoning_content 必须保留

构建 Chat Completions 请求的 messages 数组时，如果请求中涉及 reasoning_content，必须遵守以下规则：

**如果 assistant 消息包含 tool_calls，则必须同时携带 `reasoning_content`**。
即使 tool_calls 和 reasoning_content 来自不同来源（例如从其他格式转换而来），
最终拼装出的 assistant 消息也必须同时包含这两个字段。

关键约束：
- 构建 messages 时，任何含有 tool_calls 的 assistant 消息都必须检查是否有对应的 reasoning_content 需要注入
- reasoning 可能在 tool_calls 之前或之后出现，无论哪种顺序，最终 tool_calls 消息都必须带上 reasoning_content
- 已通过其他 assistant 消息消费过的 reasoning_content，不再重复注入

## 四、完整工具调用示例

```python
import json
from openai import OpenAI

client = OpenAI(
    api_key="<DeepSeek API Key>",
    base_url="https://api.deepseek.com"
)

tools = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get weather of a location",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "城市名"},
                "date": {"type": "string", "description": "日期 YYYY-mm-dd"}
            },
            "required": ["location", "date"]
        }
    }
}]

messages = [{"role": "user", "content": "杭州明天天气怎么样？"}]

while True:
    response = client.chat.completions.create(
        model="deepseek-v4-pro",
        messages=messages,
        tools=tools,
        reasoning_effort="high",
        extra_body={"thinking": {"type": "enabled"}}
    )
    messages.append(response.choices[0].message)  # 含 reasoning_content

    if response.choices[0].message.tool_calls is None:
        break

    for tool in response.choices[0].message.tool_calls:
        tool_result = execute_tool(tool.function.name, json.loads(tool.function.arguments))
        messages.append({
            "role": "tool",
            "tool_call_id": tool.id,
            "content": tool_result
        })
```

### 输出示例

```
Turn 1.1
reasoning_content="先查明天日期，再调用天气函数。"
content=""
tool_calls=[get_date()]

Turn 1.2
reasoning_content="明天是 2026-04-20，调用天气函数。"
content=""
tool_calls=[get_weather("Hangzhou", "2026-04-20")]

Turn 1.3
reasoning_content="天气结果已获取。"
content="杭州明天（4月20日）多云，7~13°C..."
tool_calls=None

Turn 2.1（新问题，但 Turn 1 的 reasoning_content 仍要回传）
reasoning_content="用户问广州天气..."
content=""
tool_calls=[get_weather("Guangzhou", "2026-04-20")]
```

---

## 五、与 OpenAI 的差异对照

| 特性 | OpenAI | DeepSeek |
|------|--------|----------|
| `reasoning_content` 字段 | 不存在 | 标准字段 |
| 思考开关 | `reasoning_effort`（部分模型）| `extra_body: {"thinking": {"type": "enabled"}}` |
| 无 tool call 时 reasoning 回传 | 不需要 | 不需要（传了也被忽略）|
| 有 tool call 时 reasoning 回传 | 不需要 | **必须，否则 400 错误** |
| temperature 等参数 | 支持 | 思考模式下禁用 |
