# Anthropic Messages API ↔ OpenAI Chat Completions API 转换设计文稿

**日期**: 2026-04-27
**目标**: 在 proxy 中新增 Anthropic Messages API 格式支持，实现 Anthropic ↔ OpenAI Chat Completions 的双向完整转换（含流式 SSE），使 Claude Code 等 Anthropic 原生客户端可以通过代理访问上游 OpenAI Chat 兼容端点。

---

## 参考实现

- **cc-switch** (`/Users/xys/Github/cc-switch`): Rust 实现，`src-tauri/src/proxy/providers/transform.rs`（请求+响应转换）+ `streaming.rs`（SSE 流式转换）
- **Claude Code** (`/Users/xys/Github/Claude-Code`): Anthropic Messages API 原生客户端，`src/services/api/claude.ts` 中 `paramsFromContext()` 构建完整请求体

---

## 文件架构

```
sse_utils.py              → _format_sse_event()（基础设施，两个转换模块共用）
transform.py              → 选择器（re-export 各模块的公共接口）
transform_responses.py    → Responses ↔ Chat（从现有 transform.py 提取）
transform_anthropic.py    → Anthropic ↔ Chat（新建）
proxy.py                  → 新增 POST /v1/messages 路由
```

`_format_sse_event` 从 `transform.py` 移至独立的 `sse_utils.py`，避免 transform 模块间的横向依赖。

### proxy.py 路由

```
POST /v1/responses          → _handle_responses()  → transform_responses.py
POST /v1/responses/compact  → _handle_responses()  → transform_responses.py
POST /v1/messages           → _handle_messages()   → transform_anthropic.py  [新增]
```

### proxy.py 转发函数参数化

将 `_forward_non_streaming` 和 `_forward_streaming` 中硬编码的转换函数改为参数：

```python
def _forward_non_streaming(self, chat_body, request_id, model_cfg,
                           response_converter):
    # response_converter: chat_to_responses 或 chat_to_anthropic

def _forward_streaming(self, chat_body, request_id, model_cfg,
                       response_converter, sse_stream_factory):
    # sse_stream_factory: create_codex_sse_stream 或 create_anthropic_sse_stream
```

---

## 请求转换: Anthropic Messages → OpenAI Chat Completions

函数签名: `anthropic_to_chat(body: dict, model_cfg: dict) -> dict`

### 顶层字段映射

| Anthropic Messages | OpenAI Chat Completions | 处理 |
|---|---|---|
| `model` | `model` | 直接透传，model_map 解析在 proxy 层统一处理 |
| `system` (string) | `messages[0]: {role:"system", content}` | 单条 system 消息 |
| `system` (array of text blocks) | `messages[0..N]: {role:"system", content: text}` | 每个块一条 system 消息，保留 `cache_control` |
| `messages[]` | `messages[]` | 逐消息递归转换（见下文） |
| `max_tokens` | `max_tokens` 或 `max_completion_tokens` | o-series 模型用 `max_completion_tokens`（以 `o` + 数字开头的模型名，如 o1/o3/o4-mini/o5 等，参考 cc-switch `is_openai_o_series`） |
| `temperature` | `temperature` | 直接透传 |
| `top_p` | `top_p` | 直接透传 |
| `stop_sequences` | `stop` | 数组直接透传（Chat API 同时接受 string 和 array），不做长度截断，由上游端自行处理限制 |
| `stream` | `stream` + `stream_options: {include_usage: true}` | 追加 usage 收集 |
| `tool_choice` | `tool_choice` | 格式适配（见下文 tool_choice 映射） |
| `thinking` + `output_config.effort` | `reasoning_effort` | 见下方映射表 |
| `metadata` | 丢弃 | Chat API 不支持 |
| `betas` | 丢弃 | Anthropic 特有 beta 标记 |
| `context_management` | 丢弃 | Anthropic 特有，Chat API 无对应 |
| `speed` | 丢弃 | Anthropic 特有 fast mode，Chat API 无对应 |

### 消息内容块映射

逐消息调用 `_convert_message_to_chat(role, content) -> list[dict]`，可能产生多条 Chat 消息：

| Anthropic 内容块 | OpenAI Chat 表示 | 备注 |
|---|---|---|
| `text` | `{type:"text", text}` | 保留 `cache_control` |
| `image` (source base64) | `{type:"image_url", image_url:{url:"data:{media_type};base64,{data}"}}` | 内联 data URI |
| `tool_use` (`id, name, input`) | 同一消息中的 `tool_calls[]`：`{id, type:"function", function:{name, arguments: JSON.stringify(input)}}` | 参数 dict → JSON 字符串 |
| `tool_result` (`tool_use_id, content`) | **独立** `{role:"tool", tool_call_id, content}` 消息 | 从原消息中分离；content 为数组时 `json.dumps` 序列化为字符串 |
| `thinking` / `redacted_thinking` | **丢弃**（请求侧） | 历史消息中的 thinking 块在转换成 Chat 请求时丢弃，因为 Chat API 不支持。但上游响应中的 reasoning delta 不会被丢弃——它走的是独立的 SSE 流式响应转换路径 |
| 简单字符串 content | 直接作为 `content` 标量 | 单块优化 |

### thinking → reasoning_effort 映射

```
Priority 1: output_config.effort:
  "low"    → "low"
  "medium" → "medium"
  "high"   → "high"
  "max"    → "xhigh"

Priority 2: thinking.type:
  "adaptive"              → "xhigh"
  "enabled", budget < 4k   → "low"
  "enabled", budget < 16k  → "medium"
  "enabled", budget >= 16k → "high"
  "enabled", 无 budget     → "high"
```

仅在模型支持 reasoning_effort 时注入（o-series: o1/o3/o4-mini，gpt-5+）。

### System 消息处理

多条 system 消息合并为一条（用 `\n` 连接文本内容，与 cc-switch 一致）。`cache_control` 冲突时丢弃（如果各块的 cache_control 不同则不做缓存标记）。始终放在 messages 数组最前面。

若同时存在顶层 `system` 和 messages 中的 `role:"system"` 消息，优先使用顶层 `system`，丢弃 messages 中的 system 消息。

> 注意: 本设计仅处理顶层 `system` 字段（string 或数组），不处理 messages 数组中 `role:"system"` 且 content 为 array 的情况（cc-switch 的 `normalize_openai_system_messages` 会处理这种场景，但 Claude Code 实测不发送这种格式）。

### tool_choice 映射

Anthropic 的 `tool_choice` 有三种格式，需转换为 Chat Completions 格式：

| Anthropic | Chat Completions | 备注 |
|---|---|---|
| `{"type": "auto"}` | `"auto"` | 简单字符串 |
| `{"type": "any"}` | `"required"` | Anthropic "any" → OpenAI "required" |
| `{"type": "tool", "name": "xxx"}` | `{"type": "function", "function": {"name": "xxx"}}` | 指定工具 |

未提供 `tool_choice` 时，默认不传（Chat API 默认为 auto）。

> **与 cc-switch 行为不同**: cc-switch 的 `transform.rs` 中 tool_choice 是直接透传的（line 172-173），那是因为它的目标端是 Responses API 格式（接受 `"auto"`/`"required"` 字符串）。本设计的目标端是 Chat Completions API，需要做上述映射适配。

### 工具定义转换

```python
# Anthropic: {name, description?, input_schema}
# → Chat: {type:"function", function:{name, description, parameters: input_schema}}
```

递归清理 schema：移除 `"format": "uri"` 字段（cc-switch 兼容处理）。

### 丢弃的字段

请求侧的以下字段在转换时丢弃（不出现在 Chat 请求体中）：

- `metadata` — Chat API 不支持
- `betas` — Anthropic 特有 beta 标记
- `context_management` — Anthropic 上下文管理参数，Chat API 无对应
- `speed` — Anthropic fast mode 标记，Chat API 无对应
- 历史消息中的 `thinking` / `redacted_thinking` 块 — Chat API 无对应概念（仅请求侧丢弃，响应侧 reasoning delta 走独立的流式转换路径）

---

## 非流式响应转换: OpenAI Chat Completions → Anthropic Messages

函数签名: `chat_to_anthropic(response: dict) -> dict`

### 字段映射

| OpenAI Chat Completions | Anthropic Messages | 处理 |
|---|---|---|
| `id` | `id` | 直接透传 |
| *(hardcoded)* | `type: "message"` | 静态注入 |
| *(hardcoded)* | `role: "assistant"` | 静态注入 |
| `model` | `model` | 直接透传 |
| *(hardcoded)* | `stop_sequence: null` | 始终 null |
| `choices[0].message.content` (string) | `content: [{type:"text", text}]` | 字符串包裹 |
| `choices[0].message.content` (array) | `content[]` | 逐块转换（output_text → text） |
| `choices[0].message.tool_calls[]` | `content[]: {type:"tool_use", id, name, input}` | `function.arguments` JSON 字符串反序列化为 `input` dict |
| `choices[0].message.refusal` | `content[]: {type:"text", text: refusal}` | refusal 内容作为 text 块 |
| `usage.prompt_tokens` | `usage.input_tokens` | 重命名 |
| `usage.completion_tokens` | `usage.output_tokens` | 重命名 |
| `usage.prompt_tokens_details.cached_tokens` | `usage.cache_read_input_tokens` | 映射嵌套字段 |
| `usage.cache_creation_input_tokens` (直接字段) | `usage.cache_creation_input_tokens` | 直接透传 |

### finish_reason → stop_reason

| OpenAI `finish_reason` | Anthropic `stop_reason` |
|---|---|
| `"stop"` | `"end_turn"` |
| `"length"` | `"max_tokens"` |
| `"tool_calls"` | `"tool_use"` |
| `"function_call"` | `"tool_use"` |
| `"content_filter"` | `"end_turn"` |
| 其他/缺失 | `"end_turn"` |

---

## 流式 SSE 转换: OpenAI Chat Completions SSE → Anthropic Messages SSE

函数签名: `create_anthropic_sse_stream(upstream_response) -> Generator[str]`

生成完整的 Anthropic Messages API SSE 格式文本字符串。Anthropic SSE 使用**命名事件**（`event: <name>\ndata: <json>\n\n`），与 OpenAI Chat SSE 的纯 `data:` 格式不同。

**推理字段兼容**: OpenAI 上游的推理 delta 字段因提供商而异——OpenAI 原生用 `delta.reasoning_content`，OpenRouter/兼容层用 `delta.reasoning`。考虑到当前 proxy 上游是 LiteLLM 网关（非 OpenRouter），实现时**两个字段都检测，任一非空即视为推理 delta**，不做优先级排序。参考 cc-switch `streaming.rs` 的做法（cc-switch 上游为 OpenRouter，仅检测 `reasoning` 字段）。

### 事件序列

| 触发条件 | Anthropic SSE 事件 |
|---|---|
| 首个 chunk（含 `id`, `model`） | `event: message_start` → `{type:"message_start", message:{id, model, role:"assistant", content:[]}}` |
| `delta.reasoning_content` 或 `delta.reasoning` 首次出现 | `event: content_block_start` → `{type:"content_block_start", index, content_block:{type:"thinking", thinking:""}}` |
| 推理 delta 持续 | `event: content_block_delta` → `{type:"content_block_delta", index, delta:{type:"thinking_delta", thinking:text}}` |
| 推理结束 | `event: content_block_stop` → `{type:"content_block_stop", index}` |
| `delta.content` 首次出现 | `event: content_block_start` → `{type:"content_block_start", index, content_block:{type:"text", text:""}}` |
| `delta.content` 持续 | `event: content_block_delta` → `{type:"content_block_delta", index, delta:{type:"text_delta", text}}` |
| 文本结束 | `event: content_block_stop` → `{type:"content_block_stop", index}` |
| `delta.tool_calls[i].function.name` 首次 | `event: content_block_start` → `{type:"content_block_start", index, content_block:{type:"tool_use", id, name, input:{}}}` |
| `delta.tool_calls[i].function.arguments` | `event: content_block_delta` → `{type:"content_block_delta", index, delta:{type:"input_json_delta", partial_json:chunk}}` |
| 工具调用结束 | `event: content_block_stop` → `{type:"content_block_stop", index}` |
| `delta.finish_reason` | `event: message_delta` → `{type:"message_delta", delta:{stop_reason, stop_sequence:null}, usage:{output_tokens}}` |
| `[DONE]` | `event: message_stop` → `{type:"message_stop"}` |

### AnthropicStreamState

```python
@dataclass
class ToolBlockState:
    """单个 tool_use content block 的缓冲状态。"""
    id: str = ""
    name: str = ""
    pending_args: str = ""     # id/name 未到齐时缓冲的 arguments 片段

@dataclass
class AnthropicStreamState:
    message_id: str = ""
    model: str = ""
    content_index: int = 0          # 当前 content block 索引
    current_block_type: str = ""    # "thinking" | "text" | "tool_use"
    # 多 tool 并发流式场景：以 OpenAI tool_call.index 为 key 管理每个 tool block 的状态
    tool_blocks: dict = field(default_factory=dict)  # int → ToolBlockState
    finish_reason: str = ""
    usage: dict = field(default_factory=dict)
    message_start_sent: bool = False
    open_blocks: set = field(default_factory=set)  # 未关闭的 content block 索引
```

**"late start" 处理**: 参考 cc-switch `streaming.rs` line 447-488，当 tool call 的参数 chunk 先于 `id` 和 `name` 到达时（early arguments），通过 OpenAI delta 中 `tool_calls[index]` 的 index 找到对应的 `ToolBlockState`，缓存到 `pending_args`，等 `id`/`name` 到齐后再发送 `content_block_start` + 缓冲的参数内容。

**多 tool 并发处理**: 使用 `dict[int, ToolBlockState]` 按 index 管理每个 tool call 的状态，而非只跟踪当前单个 tool。OpenAI Chat SSE 的 `delta.tool_calls[i]` 中 `i` 是 tool call 的索引，多个 tool 可能在不同 chunk 中交错出现。

### 关键差异（与 Responses API SSE 流）

| 维度 | Anthropic SSE | Responses API SSE |
|---|---|---|
| 事件行 | `event:` + `data:` 两行 | `event:` + `data:` 两行 |
| content block 模型 | 索引式（`content_block_start/delta/stop`） | 命名式（`output_item.added/done`） |
| thinking 处理 | `thinking_delta` 块 | `reasoning_summary_text.delta` |
| 工具参数 | `input_json_delta`（增量 JSON） | `function_call_arguments.delta` |
| 结束信号 | `message_stop` | `[DONE]` 标记 |

---

## 错误处理

遵循与现有 Responses 路径一致的原则：转换失败不阻断正常流程。

| 场景 | 处理 |
|---|---|
| 请求 JSON 解析失败 | 返回 400 + `{error: "Invalid JSON"}` |
| 请求体格式无法识别 | 返回 400 + `{error: "Unsupported request format"}` |
| 转换过程字段缺失/类型错误 | `logging.warning` + 使用默认值继续，不抛异常 |
| 上游返回非 200 | 透传状态码和错误体 |
| 上游 SSE 流中断 | 发送 Anthropic 格式 `error` 事件后关闭连接（与 cc-switch 一致）：`{"type":"error","error":{"type":"stream_error","message":"Stream error: ..."}}` |
| DB 写入失败（token_stats） | `logging.warning`，不影响响应返回 |
| UTF-8 多字节字符跨块切割 | 参考 cc-switch 的 `append_utf8_safe` 逻辑，保留 incomplete bytes 到下一块 |
| 上游 SSE 解析失败（无效 JSON） | `logging.warning` + 跳过该 chunk，继续处理后续事件 |
| 流式 tool call "late start" | 见 AnthropicStreamState 中的 `pending_args` 处理 |

---

## token_stats 兼容性

`record_token_stats(usage, context)` 已经支持三种 usage 格式，包括 Anthropic。Chat → Anthropic 响应转换后的 `usage` 字段包含 `cache_read_input_tokens` 和 `cache_creation_input_tokens`，`_extract_tokens` 中的优先检测能正确提取。

唯一变化：context 中的 `agent` 字段从 `"codex"` 改为检测策略：
- User-Agent 含 `"claude"` → `"claude"`
- User-Agent 含 `"codex"` → `"codex"`
- 默认 → `"unknown"`

---

## 测试计划

1. **单元测试** — `test/test_transform_anthropic.py`
   - `anthropic_to_chat`: 基本消息转换、system 字符串/数组、多模态图片、工具定义、tool_use/tool_result、thinking → reasoning_effort、cache_control 保留、o-series max_completion_tokens、空 messages、错误格式、含未知额外字段（如 `output_config.format`、`context_management` 等）的请求体不抛异常
   - `chat_to_anthropic`: 文本响应、工具调用响应、refusal 处理、finish_reason 映射、usage 映射
   - `create_anthropic_sse_stream`: 完整事件序列、thinking 流、文本流、工具调用流、多 content block 交替、UTF-8 跨块、流中断

2. **集成测试** — 更新 `test/test_proxy_logger_integration.py`
   - `POST /v1/messages` 非流式路径
   - `POST /v1/messages` 流式路径
   - token_stats 正确写入（agent=claude）

3. **拆分重构测试** — 确保 `transform_responses.py` 提取后现有测试全部通过。分两步执行避免回归：
   - 阶段 1（纯重命名/移动）：从 `transform.py` 提取 Responses 逻辑至 `transform_responses.py`，不修改转换逻辑 → 跑全量测试确认通过 → commit
   - 阶段 2（Anthropic 功能）：新建 `transform_anthropic.py` + `sse_utils.py` + 更新 `proxy.py` 路由 → 跑全量测试确认通过 → commit

---

## 性能考虑

- 纯 dict/list 遍历，无外部 I/O
- `anthropic_to_chat` 最坏 O(N×M)（N 条消息，M 个内容块/消息）
- SSE 流式转换逐块处理，不缓存完整响应体
- 错误路径 < 1ms 返回（try/except 包裹）
- 与现有 Responses 路径无性能回归
- SSE 事件的 JSON 输出使用 f-string 拼接以最小化序列化开销（参考现有 `_format_sse_event` 使用 `separators=(',', ':')` 紧凑格式）

---

## Breaking Changes

1. **`transform.py` 拆分为选择器**：现有 `transform.py` 中的 Responses 转换逻辑移至 `transform_responses.py`。所有 `from transform import responses_to_chat, ...` 的引用需更新为 `from transform_responses import ...` 或通过 `transform.py` re-export。对 `proxy.py` 外部调用者无影响（仅 proxy.py 和 test 文件引用这些函数）。
2. **`_forward_non_streaming` / `_forward_streaming` 签名变更**：新增 `response_converter` 和 `sse_stream_factory` 参数。集成测试中的 mock 调用需更新。
