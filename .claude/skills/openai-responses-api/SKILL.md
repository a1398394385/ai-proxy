---
name: openai-responses-api
description: OpenAI Responses API 请求体完整参考文档。需要在编写或审查 Responses API 请求格式、input 字段定义（替代 Chat 的 messages）、output item 类型、内置工具（web_search/file_search/code_interpreter/computer/local_shell/image_generation）、reasoning 配置、conversation/previous_response_id 多轮管理时使用此 skill。触发场景：构建 Responses API 请求、Responses ↔ Chat Completions 协议转换、调试 Responses 格式错误、理解 output 数组结构、处理内置工具调用。
metadata:
  source: https://developers.openai.com/api/reference/resources/responses/methods/create
  fetched: 2026-05-15
---

# OpenAI Responses API 请求体参考

> 来源: developers.openai.com — Responses API Reference
> 抓取日期: 2026-05-15

## Endpoint

```
POST https://api.openai.com/v1/responses
```

## 与 Chat Completions 的核心差异

| 特性 | Chat Completions | Responses |
|------|-----------------|-----------|
| 输入字段 | `messages` (role+content) | `input` (items 数组，每个 item 有 type+role) |
| 输出格式 | `choices[0].message` | `output` (items 数组，混合 message/tool_call/reasoning) |
| 多轮管理 | 客户端拼接 messages | `previous_response_id` 或 `conversation` |
| 工具 | function/custom tools | 内置工具 + function + MCP |
| 推理 | `reasoning_effort` | `reasoning: {effort, generate_summary, summary}` |
| 状态 | 无 | status: queued/in_progress/completed/... |

---

## 一、input 字段

`input`: `string | array` — 模型的文本、图片、音频或文件输入。

**四种输入格式（one of）：**

### 1.1 TextInput（字符串简写）

```
"Tell me a story about a unicorn."
```

等价于一个 `role: "user"` 的 EasyInputMessage。

### 1.2 InputItemList（数组）

每个 item **one of：**

#### EasyInputMessage — 输入消息

```
type: "message"
role: "developer" | "system" | "user" | "assistant"
content: string | array of ResponseInputContent
phase: "input" | "tool" (optional, 标识消息阶段)
```

**ResponseInputContent 类型（one of）：**

**① ResponseInputText**
```json
{"type": "input_text", "text": "文本内容"}
```

**② ResponseInputImage**
```json
{
  "type": "input_image",
  "detail": "auto" | "low" | "high" | "original",
  "file_id": "file-xxx",
  "image_url": "https://..."
}
```

**③ ResponseInputAudio** (Beta)
```json
{"type": "input_audio", "audio": "base64..."}
```

**④ ResponseInputFile**
```json
{
  "type": "input_file",
  "file_data": "<base64>",
  "file_id": "file-xxx",
  "filename": "example.py"
}
```

**role 含义：**
- `developer` / `system` — 系统指令，优先级高于 user
- `user` — 最终用户发送的消息
- `assistant` — 模型历史回复

#### ResponseOutputMessage — 历史输出消息

在后续请求中回传历史消息时使用，字段与 output 中的消息一致：
```
type: "message"
id: string
role: "assistant"
status: "completed" | "incomplete" | "in_progress"
content: array of ResponseOutputText | ResponseOutputRefusal
```

#### 其他 input item 类型

| 类型 | 用途 |
|------|------|
| `Reasoning` | 推理内容（跨轮回传） |
| `ResponseFunctionToolCall` | 函数工具调用 |
| `ResponseFunctionCallOutput` | 函数工具调用结果 |
| `ComputerCallOutput` | 计算机工具输出 |
| `WebSearchToolCall` | 网络搜索工具调用 |
| `FileSearchToolCall` | 文件搜索工具调用 |
| `CodeInterpreterToolCall` | 代码解释器工具调用 |
| `ImageGenerationToolCall` | 图像生成工具调用 |
| `LocalShellToolCall` | 本地 Shell 工具调用 |

---

## 二、output 格式

`output`: `array` — 模型生成的输出项列表，按生成顺序排列。

### 2.1 ResponseOutputMessage

```json
{
  "type": "message",
  "id": "msg_xxx",
  "role": "assistant",
  "status": "completed",
  "content": [
    {
      "type": "output_text",
      "text": "回答文本",
      "annotations": [],
      "logprobs": null
    }
  ]
}
```

**content 两种元素类型：**
- `ResponseOutputText` — `{type: "output_text", text, annotations, logprobs}`
- `ResponseOutputRefusal` — `{type: "refusal", refusal}`

**annotations 类型：**
- `FileCitation` — `{type: "file_citation", file_id, filename, index}`
- `URLCitation` — `{type: "url_citation", url, title, start_index, end_index}`
- `ContainerFileCitation` — `{type, container_id, file_id, filename, start_index, end_index}`

### 2.2 Reasoning

```json
{
  "type": "reasoning",
  "id": "rsn_xxx",
  "status": "completed",
  "summary": [{"type": "summary_text", "text": "推理摘要"}],
  "content": [{"type": "reasoning_text", "text": "完整推理过程"}],
  "encrypted_content": ""
}
```

⚠️ 如果在 `include` 中指定了 `reasoning.encrypted_content`，多轮回传时必须包含整个 Reasoning item。

### 2.3 工具调用 output 类型

| 类型 | type 值 |
|------|---------|
| 函数工具调用 | `function_call` |
| 函数工具输出 | `function_call_output` |
| 网络搜索 | `web_search_call` |
| 文件搜索 | `file_search_call` |
| 代码解释器 | `code_interpreter_call` |
| 计算机使用 | `computer_call` + `computer_call_output` |
| 本地 Shell | `local_shell_call` + `local_shell_call_output` |
| 图像生成 | `image_generation_call` |

---

## 三、tools 定义

`tools`: `array` — three categories:

### 3.1 内置工具 (Built-in Tools)

**Web Search**
```json
{
  "type": "web_search",
  "search_context_size": "low" | "medium" | "high",
  "user_location": {
    "type": "approximate",
    "city": "San Francisco",
    "country": "US",
    "region": "California",
    "timezone": "America/Los_Angeles"
  }
}
```

**File Search**
```json
{
  "type": "file_search",
  "vector_store_ids": ["vs_xxx"],
  "ranking_options": {},
  "max_num_results": 10
}
```

**Code Interpreter**
```json
{"type": "code_interpreter"}
```

**Computer Use**
```json
{
  "type": "computer_use_preview",
  "environment": "browser" | "mac" | "windows" | "ubuntu",
  "display_width": 1024,
  "display_height": 768
}
```

**Local Shell**
```json
{"type": "local_shell"}
```

**Image Generation**
```json
{"type": "image_generation"}
```

### 3.2 Function Tool（自定义函数）

```json
{
  "type": "function",
  "name": "get_weather",
  "description": "获取指定位置的天气",
  "strict": true,
  "parameters": {
    "type": "object",
    "properties": {
      "location": {"type": "string", "description": "城市名"}
    },
    "required": ["location"]
  }
}
```

**与 Chat Completions 的区别：** Responses API 中 function tool 直接使用 `name`/`description`/`parameters` 顶层字段，不包裹在 `function` 对象中。

### 3.3 MCP Tools

```json
{
  "type": "mcp",
  "server_label": "deepwiki",
  "server_url": "https://mcp.example.com",
  "allowed_tools": ["search", "read"]
}
```

---

## 四、tool_choice 选项

`tool_choice` 与 Chat Completions 类似但适配 Responses：

**字符串模式：**
- `"none"` — 不调用工具
- `"auto"` — 自动选择（默认）
- `"required"` — 必须调用工具

**ToolChoiceAllowed（限定工具集）：**
```json
{
  "type": "allowed_tools",
  "mode": "auto" | "required",
  "tools": [
    {"type": "function", "name": "get_weather"},
    {"type": "mcp", "server_label": "deepwiki"},
    {"type": "image_generation"}
  ]
}
```

**ToolChoiceTypes（指定工具类型）：**
```json
{"type": "web_search"}
```
```json
{"type": "code_interpreter"}
```

---

## 五、multi-turn 方法

### 5.1 previous_response_id

```json
{
  "previous_response_id": "resp_xxx",
  "input": "follow-up question"
}
```

- 自动拼接之前 response 的 input 和 output
- 上一轮的 `instructions` **不会**自动带入下一轮
- 不能与 `conversation` 同时使用

### 5.2 conversation

```json
{
  "conversation": "conv_xxx",
  "input": "next prompt"
}
```

- 分配到一个持久对话中
- input/output 自动追加到 conversation
- 可以通过 Conversation API 管理

---

## 六、reasoning 配置

`reasoning`: `{effort, generate_summary, summary}` — gpt-5 和 o-series 模型专用。

```json
{
  "reasoning": {
    "effort": "high",
    "summary": "auto"
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `effort` | `"none"\|"minimal"\|"low"\|"medium"\|"high"\|"xhigh"` | 推理强度 |
| `generate_summary` | `"auto"\|"concise"\|"detailed"` | **已弃用**，用 `summary` 替代 |
| `summary` | `"auto"\|"concise"\|"detailed"` | 推理摘要。`concise` 仅 computer-use-preview 和 gpt-5 后推理模型支持 |

---

## 七、include 参数

`include`: `array` — 指定输出中额外包含的数据。

| 值 | 说明 |
|----|------|
| `web_search_call.action.sources` | 网络搜索的引用源 |
| `code_interpreter_call.outputs` | Python 代码执行输出 |
| `computer_call_output.output.image_url` | 计算机截图 URL |
| `file_search_call.results` | 文件搜索结果 |
| `message.input_image.image_url` | 输入图片 URL |
| `message.output_text.logprobs` | assistant 消息的 logprobs |
| `reasoning.encrypted_content` | 推理的加密内容（用于零数据保留场景下的多轮） |

---

## 八、全部请求参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `model` | `string` | — | 模型 ID |
| `input` | `string \| array` | — | 见第一章 |
| `instructions` | `string \| array` | — | 系统/开发者指令 |
| `previous_response_id` | `string` | — | 前一次 response ID |
| `conversation` | `string \| object` | — | 对话 ID |
| `max_output_tokens` | `number` (≥16) | — | 输出上限 |
| `max_tool_calls` | `number` | — | 内置工具调用总次数上限 |
| `temperature` | `float` (0-2) | 1 | 采样温度 |
| `top_p` | `float` (0-1) | 1 | nucleus sampling |
| `stream` | `boolean` | false | 流式返回 |
| `stream_options` | `object` | — | `{include_obfuscation}` |
| `tools` | `array` | — | 见第三章 |
| `tool_choice` | — | `"auto"` | 见第四章 |
| `reasoning` | `object` | — | 见第六章 |
| `text` | `object` | `{format: {type: "text"}}` | 文本输出配置 |
| `include` | `array` | — | 见第七章 |
| `background` | `boolean` | false | 后台异步执行 |
| `parallel_tool_calls` | `boolean` | true | 并行工具调用 |
| `store` | `boolean` | true | 是否存储 response |
| `truncation` | `"auto"\|"disabled"` | `"disabled"` | 截断策略 |
| `metadata` | `object` | — | 16 个 key-value 对 |
| `safety_identifier` | `string` | — | 用户标识 |
| `prompt_cache_key` | `string` | — | 缓存 key |
| `prompt_cache_retention` | `string` | — | 缓存保留策略 |
| `service_tier` | `string` | `"auto"` | 服务等级 |
| `top_logprobs` | `number` (0-20) | — | 对数概率 token 数 |
| `context_management` | `array` | — | 上下文压缩配置 |
| `user` | `string` (deprecated) | — | 已弃用 |

---

## 九、Response 对象（返回值）

```json
{
  "id": "resp_xxx",
  "object": "response",
  "created_at": 1741476542,
  "status": "completed",
  "completed_at": 1741476543,
  "model": "gpt-5.4",
  "output": [],
  "usage": {
    "input_tokens": 36,
    "input_tokens_details": {"cached_tokens": 0},
    "output_tokens": 87,
    "output_tokens_details": {"reasoning_tokens": 0},
    "total_tokens": 123
  },
  "error": null,
  "incomplete_details": null,
  "instructions": null,
  "parallel_tool_calls": true,
  "temperature": 1.0,
  "text": {"format": {"type": "text"}},
  "tool_choice": "auto",
  "tools": [],
  "top_p": 1.0,
  "truncation": "disabled",
  "metadata": {}
}
```

**status 枚举：** `queued` | `in_progress` | `completed` | `incomplete` | `failed`

**error codes：** `server_error`, `rate_limit_exceeded`, `invalid_prompt`, `invalid_image`, `invalid_image_format`, `image_too_large`, `vector_store_timeout` 等

---

## 十、关键约束

1. **input 与 Chat 的 messages 完全不同** — input 是 type+role 的 item 列表
2. **history 通过 previous_response_id 管理** — API 自动拼接（不可手动拼接 messages）
3. **output 是混合数组** — 同时包含 message、reasoning、tool_call、tool_call_output
4. **reasoning 是独立的 output item** — 不是 message 的子字段
5. **内置工具直接在 tools 中声明类型** — 不需要 function 定义
6. **function tool 定义更扁平** — name/parameters 直接放在顶层
7. **不能同时使用 previous_response_id 和 conversation**
8. **truncation: "auto" 时** — 超出上下文窗口会自动从开头丢弃旧项
9. **status 区分异步** — 可通过 `background: true` 让请求异步执行

---

## 示例

### 最简请求
```json
{
  "model": "gpt-5.4",
  "input": "Tell me a three sentence bedtime story about a unicorn."
}
```

### 带系统指令
```json
{
  "model": "gpt-5.4",
  "instructions": "You are a helpful coding assistant. Use Chinese.",
  "input": "帮我写一个 Python 排序函数"
}
```

### 带网络搜索
```json
{
  "model": "gpt-5.4",
  "input": "今天有什么重大新闻？",
  "tools": [{"type": "web_search"}],
  "include": ["web_search_call.action.sources"]
}
```

### 带自定义函数
```json
{
  "model": "gpt-5.4",
  "input": "杭州明天天气怎么样？",
  "tools": [{
    "type": "function",
    "name": "get_weather",
    "description": "获取指定位置天气",
    "parameters": {
      "type": "object",
      "properties": {
        "location": {"type": "string"},
        "date": {"type": "string"}
      },
      "required": ["location"]
    }
  }]
}
```

### 多轮对话
```json
// 第一轮
{
  "model": "gpt-5.4",
  "input": "帮我分析一下这段代码",
  "store": true
}
// 响应: { "id": "resp_abc" }

// 第二轮
{
  "model": "gpt-5.4",
  "previous_response_id": "resp_abc",
  "input": "再帮我优化一下性能"
}
```

### 推理模型
```json
{
  "model": "o3",
  "input": "这道数学题怎么解？",
  "reasoning": {
    "effort": "max",
    "summary": "detailed"
  }
}
```
