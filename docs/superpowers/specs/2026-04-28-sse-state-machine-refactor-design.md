# 设计文稿：SSE 状态机全面重构 + MCP 支持

**日期**: 2026-04-28
**状态**: 已审阅（第五轮，根据第二轮审查报告修复全部 17 个问题 + 补充 1 个已知缺口说明）
**范围**: `transform_responses.py` SSE 流转换器 + `proxy.py` 多轮循环层 + response store

---

## 1. 背景与动机

### 1.1 当前问题

我们的 SSE 流转换器 `create_codex_sse_stream()` 是一个**单向翻译器**——一次请求进，一次流式响应出。Codex CLI 实际使用时频繁报错，根因有三：

1. **事件类型不完整**：缺少 `response.in_progress`、`response.content_part.added/done`、`response.function_call_arguments.delta/done`、`response.refusal.delta/done`、`response.failed`/`response.incomplete`、`data: [DONE]` 终止标记
2. **错误处理不足**：流中异常导致连接直接断开，Codex 收不到 `response.failed` 事件
3. **无法支持 MCP 多轮循环**：当前架构不支持工具执行后重新调用上游

### 1.2 参考项目对比

| 能力 | 当前项目 | codex-proxy | openai-responses-adapter |
|---|---|---|---|
| SSE 事件完整性 | 部分缺失 | 完整 | 完整 |
| content_part 生命周期 | 无 | 有 | 无（直接 output_text.delta） |
| function_call_arguments.delta | 无 | 有 | 有 |
| response.in_progress | 无 | 有 | 有 |
| refusal 处理 | 无 | 有 | 无 |
| 客户端断开检测 | 无 | 有（async disconnect） | 无 |
| MCP 工具自动执行 | 无 | 无 | 有（非流式 8 轮循环） |
| response store | 无 | 无 | 有（LRU+TTL） |
| previous_response_id | 无 | 无 | 有 |

### 1.3 目标

- **Phase 1**: 全面重构 SSE 状态机，补齐所有缺失事件，对齐 Codex 期望的事件序列
- **Phase 2**: 新增 response store + previous_response_id 链，支持多轮对话
- **Phase 3**: 新增 MCP 工具自动执行循环（非流式模式）

---

## 2. 架构设计

### 2.1 整体数据流

```
┌─────────────┐    POST /v1/responses     ┌────────────────────┐    POST /v1/chat/completions    ┌──────────┐
│  Codex CLI  │ ───────────────────────► │  fact-store-proxy  │ ─────────────────────────────► │ Upstream │
│             │ ◄─────────────────────── │  (Threaded HTTP)   │ ◄───────────────────────────── │  LLM     │
│             │   SSE Events Stream      │                    │    SSE / JSON Response          │  Server  │
└─────────────┘                          └────────────────────┘                                └──────────┘
                                                 │
                                        ┌────────┴────────┐
                                        │  Response Store  │
                                        │  (in-memory)     │
                                        │  store=true 时   │
                                        │  缓存 response    │
                                        └───────────────────┘
                                                 │
                                        ┌────────┴────────┐
                                        │  MCP Manager     │
                                        │  非流式模式下    │
                                        │  自动执行工具     │
                                        └───────────────────┘
```

### 2.2 核心模块职责

| 模块 | 职责 | 对应文件 |
|---|---|---|
| **HTTP Handler** | 路由、请求解析、响应头设置、流/非流分支 | `proxy.py` |
| **Request Converter** | Responses API → Chat Completions | `transform_responses.py: responses_to_chat()` |
| **Response Converter** | Chat Completions → Responses API | `transform_responses.py: chat_to_responses()` |
| **SSE Stream Converter** | Chat Completions SSE → Responses SSE | `transform_responses.py: create_codex_sse_stream()` → 新 `CodexStreamConverter` |
| **Response Store** | 缓存 response output，支持 previous_response_id 链 | 新 `response_store.py` |
| **MCP Manager** | 非流式模式下自动执行 MCP 工具 | 新 `mcp_manager.py` |
| **Stream Loop** | 非流式模式下多轮工具调用循环 | `proxy.py` 中新增 |

---

## 3. SSE 状态机重构（Phase 1）

### 3.1 事件类型完整列表

以下是 Codex CLI 期望的完整 SSE 事件序列，以及每个事件的触发条件和数据格式。

#### 3.1.1 流开始

| 顺序 | 事件 | 触发条件 | data JSON 结构 |
|---|---|---|---|
| 1 | `response.created` | 收到上游第一个有效 chunk | `{"type":"response.created", "response":{response对象}}` |
| 2 | `response.in_progress` | 紧跟 created 后 | `{"type":"response.in_progress", "response":{response对象}}` |
| 3 | `response.metadata` | 紧跟 in_progress 后 | `{"type":"response.metadata", "headers":{"model":"..."}}` |

**当前状态**：只有 1 和 3，缺失 2（in_progress）。

#### 3.1.2 文本内容

| 顺序 | 事件 | 触发条件 | data JSON 结构 |
|---|---|---|---|
| 1 | `response.output_item.added` | 首次收到文本 delta | `{"type":"response.output_item.added", "output_index":N, "item":{"type":"message","id":"<msg_id>","status":"in_progress","role":"assistant","content":[]}}` |
| 2 | `response.content_part.added` | 紧跟 added 后 | `{"type":"response.content_part.added", "output_index":N, "content_index":0, "part":{"type":"output_text","text":"","annotations":[]}}` |
| 3 | `response.output_text.delta` | 每个文本 token | `{"type":"response.output_text.delta", "output_index":N, "content_index":0, "delta":"..."}` |
| 4 | `response.output_text.done` | 文本结束 | `{"type":"response.output_text.done", "output_index":N, "content_index":0, "text":"完整文本"}` |
| 5 | `response.content_part.done` | 紧跟 done 后 | `{"type":"response.content_part.done", "output_index":N, "content_index":0, "part":{output_text}}` |
| 6 | `response.output_item.done` | message item 关闭 | `{"type":"response.output_item.done", "output_index":N, "item":{完整 message}}` |

**当前状态**：有 1、3、4、6（但 4 和 6 的顺序/格式可能不对）。缺失 2 和 5（content_part 生命周期）。

#### 3.1.3 推理内容

| 顺序 | 事件 | 触发条件 | data JSON 结构 |
|---|---|---|---|
| 1 | `response.output_item.added` | 首次收到推理 delta | `{"type":"response.output_item.added", "output_index":N, "item":{"type":"reasoning","id":"<rs_id>","summary":[]}}` |
| 2 | `response.reasoning.delta` | 每个推理 token | `{"type":"response.reasoning.delta", "output_index":N, "delta":"..."}` |
| 3 | `response.reasoning.done` | 推理结束 | `{"type":"response.reasoning.done", "output_index":N, "text":"完整推理"}` |
| 4 | `response.output_item.done` | reasoning item 关闭 | `{"type":"response.output_item.done", "output_index":N, "item":{"type":"reasoning","id":"<rs_id>","summary":[{"type":"summary_text","text":"完整推理文本"}]}}` |

**当前状态**：事件序列结构存在，但**事件名错误且含多余字段**。当前实现使用 `response.reasoning_summary_text.delta/done`，而 codex-proxy 参考实现（`Constants.py` line 50-51）明确定义为 `response.reasoning.delta` / `response.reasoning.done`。事件名不匹配导致 Codex CLI 无法解析推理内容。

此外，当前代码中 delta 事件包含错误的 `summary_index` 字段（`{"output_index": 0, "summary_index": 0, "delta": ...}`）；codex-proxy 的 reasoning 事件格式不含此字段（`{"output_index": N, "delta": "..."}`），实现时需删除。同时 `output_index` 不能硬编码为 `0`，需改为动态 `next_output_index` 分配。

#### 3.1.4 工具调用（关键差异）

| 顺序 | 事件 | 触发条件 | data JSON 结构 |
|---|---|---|---|
| 1 | `response.output_item.added` | id+name 都到后触发 | `{"type":"response.output_item.added", "output_index":N, "item":{"type":"function_call","id":"<item_id>","call_id":"<call_id>","name":"<name>","arguments":"","status":"in_progress"}}` |
| 2 | `response.function_call_arguments.delta` | 每个参数 token | `{"type":"response.function_call_arguments.delta", "output_index":N, "call_id":"...", "delta":"..."}` |
| 3 | `response.function_call_arguments.done` | 参数结束 | `{"type":"response.function_call_arguments.done", "output_index":N, "call_id":"...", "arguments":"完整JSON"}` |
| 4 | `response.output_item.done` | function_call 关闭 | `{"type":"response.output_item.done", "output_index":N, "item":{"type":"function_call","id":"<item_id>","call_id":"<call_id>","name":"<name>","arguments":"<完整JSON>","status":"completed"}}` |

> **注意**：`output_item.added` 时 item 的 `arguments` 必须为空字符串（`""`），`status` 必须为 `"in_progress"`；缺少这两个字段会导致 Codex 客户端侧 item 对象不完整。`id`（item_id）在 added 时生成并保存，done 时复用同一 ID（设计决策 1）。

**当前状态**：**只有 4**。工具调用在 `_process_delta` 阶段仅被积累进 `state.tool_calls` dict，不发任何事件；在 `_emit_completion` 中直接跳到 `output_item.done`，连 `output_item.added`（事件1）也没有发送。缺失 1、2、3。这是 Codex 报错的核心原因——Codex 的 state machine 收不到 `output_item.added`，无法建立对应的工具调用 item，后续 done 事件无法匹配。

#### 3.1.5 拒绝内容

| 顺序 | 事件 | 触发条件 |
|---|---|---|
| 1 | `response.output_item.added` | 首次收到拒绝 delta **且 `text_message_opened=False`** 时（若为 True 则跳过此步，直接复用已开启的 message item） |
| 2 | `response.content_part.added` | content_index=1（如果有文本）或 0 |
| 3 | `response.refusal.delta` | 每个拒绝 token |
| 4 | `response.refusal.done` | 拒绝结束 |
| 5 | `response.content_part.done` | 关闭拒绝 content part |
| 6 | `response.output_item.done` | 关闭 message（包含 text + refusal） |

**当前状态**：完全没有。

#### 3.1.6 流结束

| 事件 | 触发条件 |
|---|---|
| `response.incomplete` | finish_reason == "length" 或 "content_filter" |
| `response.completed` | 正常结束 |
| `response.failed` | 上游 HTTP 错误或 Content-Type 不对 |
| `data: [DONE]` | 所有事件发送完毕后 |

**当前状态**：有 incomplete 和 completed，有 failed（在 proxy.py 中）。缺失 `[DONE]` 终止标记。

**`[DONE]` 的依据**：openai-responses-adapter（Go 参考实现）在 `response.completed` 之后显式发送 `data: [DONE]\n\n`，与 Chat Completions SSE 协议保持一致。codex-proxy（Python 参考实现）未发送 `[DONE]`，依赖 HTTP 连接关闭感知流结束。本项目选择对齐 Go 实现，原因：`[DONE]` 是 OpenAI SSE 协议的标准终止信号，更明确，客户端无需等待 TCP 连接关闭即可确认流已结束。

### 3.2 新状态机设计

```python
@dataclass
class ToolBlockState:
    """工具调用块的状态。"""
    output_index: int = -1
    call_id: str = ""
    name: str = ""
    accumulated_args: str = ""
    started: bool = False      # 延迟启动：等 id+name 都就绪
    item_id: str = ""          # 首次生成，added/done 必须复用同一 ID


@dataclass
class CodexStreamConverter:
    """完整的 Codex SSE 流转换器。替代 create_codex_sse_stream()。"""

    response_id: str = ""
    model: str = ""
    next_output_index: int = 0

    # 文本消息状态
    text_message_id: str = ""        # 首次生成，added/done 复用
    text_output_index: int = -1
    text_message_opened: bool = False     # 实际语义："message output item 已打开"，
    # 不仅限于文本（纯 refusal 场景也会设为 True）。命名为 text_ 是沿用旧代码习惯。
    text_content_part_opened: bool = False
    accumulated_text: str = ""

    # 推理状态
    reasoning_id: str = ""           # 首次生成，added/done 复用
    reasoning_output_index: int = -1
    reasoning_opened: bool = False
    accumulated_reasoning: str = ""

    # 拒绝状态
    refusal_opened: bool = False
    refusal_content_index: int = 0    # 在 _handle_refusal_delta 首次打开时保存，避免 _close_refusal_block 时序竞态
    accumulated_refusal: str = ""

    # 工具调用状态（key: tool_calls index → ToolBlockState）
    tool_blocks: dict = field(default_factory=dict)

    # 完成状态
    finish_reason: str = ""
    final_usage: Optional[dict] = None   # None 表示还未收到 usage chunk；空 dict 与"token 全为 0"语义不同

    # 完整 output 数组，每项为 (output_index, item) 元组，
    # finish() 中按 output_index 升序排序后提取 item 列表写入 response.completed
    output_items: list = field(default_factory=list)
    created_sent: bool = False


# 向后兼容别名（旧代码和测试通过 StreamState 引用）
StreamState = CodexStreamConverter
```

> **注意**：旧的 `StreamState` dataclass（含 `has_text`、`has_reasoning`、`message_output_index` 属性）完全由 `CodexStreamConverter` 取代。`message_output_index` 的静态计算逻辑（`return 1 if has_reasoning else 0`）需要彻底删除，改用 `next_output_index` 递增分配，防止推理/文本顺序颠倒时索引出错。

#### 3.2.1 关键设计决策

1. **ID 一致性**：`response.output_item.added` 和 `response.output_item.done` 中的 item.id 必须完全相同。在 added 时生成 ID 并保存，done 时复用。
2. **延迟启动工具调用**：等 id 和 name 都到了才发 `output_item.added`。在此之前暂存的 arguments 在启动后一次性通过 `function_call_arguments.delta` 发送。
3. **content_part 生命周期**：每个内容块（text/refusal）都有 added → delta × N → done 的完整生命周期。
4. **message 合并 text + refusal**：如果既有文本又有拒绝，共用同一个 message output item，refusal 的 content_index = 1。
5. **output_index 自增**：每个 output item（message、reasoning、function_call）独立分配 output_index，按出现顺序递增。
6. **`[DONE]` 终止标记**：所有事件发送完毕后，必须发送 `data: [DONE]\n\n`。
7. **延迟启动优先于 Go 实现的即时启动**：Go 参考实现（`stream.go` line 159-168）在第一个工具 delta 到达时立即发射 `output_item.added`，此时 `call_id` 可能为空字符串，导致 `added`/`delta` 两个事件的 `call_id` 不一致。codex-proxy 延迟到 `id` 和 `name` 都就绪后再发，是更可靠的方案；本项目采用此方式。

### 3.3 方法拆分

> ⚠️ **重要**：以下代码块是 §3.2 `@dataclass class CodexStreamConverter:` 定义的**方法部分**，**不是一个新的类定义**。Python 中两个同名 `class` 后者会直接覆盖前者（不是"扩展"）。实现时必须将 §3.2 的字段定义和本节的方法定义合并为同一个 `@dataclass class CodexStreamConverter:` 类体内。

```python
# ——以下为 §3.2 CodexStreamConverter 类的方法部分，合并到同一 @dataclass 类定义中——
class CodexStreamConverter:
    def process_chunk(self, chunk: dict) -> list[str]:
        """处理单个上游 Chat Completions chunk，返回 SSE 事件字符串列表。

        **首个 chunk 处理（在任何 delta 处理之前）**：
        若 `created_sent=False`，先执行以下步骤（对齐 codex-proxy line 216-223）：
        1. 从 `chunk.get("model")` 更新 `self.model`（上游返回的实际模型名可能与请求的不同，
           必须在发 created 事件前更新，确保 response.created 中的 model 字段正确）
        2. 调用 `_emit_created()` 发送 created + in_progress + metadata 三个事件
        （_emit_created() 内部会将 created_sent 置为 True，防止重复触发）

        各 delta 类型处理顺序（与 codex-proxy 保持一致）：
        content → refusal → reasoning/reasoning_content → tool_calls
        当同一 chunk 同时包含多个字段时，此顺序决定事件发射顺序，
        进而影响 content_index 的计算（refusal 的 content_index 依赖 text_content_part_opened 状态）。
        """

    def finish(self) -> list[str]:
        """流结束时调用，返回关闭所有块 + completed + [DONE] 的事件列表。
        
        调用顺序：
        1. 若 created_sent=False（空流），先调用 _emit_created() 补发创建事件
        2. _close_text_block()（若 text_content_part_opened）
        3. _close_refusal_block()（若 refusal_opened）
        4. _emit_message_item_done()（若 text_message_opened）
        5. _close_reasoning_block()（若 reasoning_opened）
        6. _close_tool_blocks()（关闭所有工具块）
        7. 将 output_items 按 output_index 升序排序后，放入 response.completed 的 response.output
        8. 调用 _build_response_obj(status=..., output=output_items, usage=final_usage) 构建
           response 对象，发送 response.completed（或 response.incomplete）
           response 对象必须包含 id/object/created_at/status/model/output/usage 七个字段
        9. 发送 data: [DONE]
        
        注意：步骤 7 的排序是必要的。output_items 按关闭顺序（text→reasoning→tools）收集，
        但 output_index 按流中出现顺序分配，两者可能不同（如 reasoning 先出现 index=0，
        text 后出现 index=1，但关闭时 text 先关闭）。response.completed 的 output 数组
        必须与 SSE 中各 output_item.added 的 output_index 严格对应，参考 codex-proxy
        的 output_items.sort(key=lambda x: _get_item_output_index(x, state)) 实现。
        
        注意：若 created_sent=False（空流，从未收到任何 delta），
        finish() 必须先调用 _emit_created() 补发创建事件，再发 completed + [DONE]。
        """

    def _emit_created(self) -> list[str]:
        """发送 response.created + response.in_progress + response.metadata。

        三个事件连续发送：
        1. response.created     — {"response": _build_response_obj(status="in_progress")}
        2. response.in_progress — 与 created 相同的 response 对象
        3. response.metadata    — {"headers": {"model": "..."}}（**非标准扩展**：两个参考实现均无此事件。
                                   保留原因：Codex CLI 对未知事件类型静默忽略。风险：若未来 Codex 版本
                                   对未知事件报错，需移除。已有测试断言其存在，确保后续可追踪）

        response 对象结构（由 `_build_response_obj()` 统一构建，对齐 codex-proxy
        `_build_response_object_dict` line 864-873）：
        {
            "id":         response_id,
            "object":     "response",            # 必需字段
            "created_at": int(time.time()),       # 必需字段
            "status":     "in_progress",
            "model":      model,                 # 必需字段
            "output":     [],
            "usage":      {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            # 占位 usage：避免 Codex CLI 在收到 response.created 时访问
            # response.usage.input_tokens 引发运行时错误
        }

        调用结束前须将 `self.created_sent = True`，防止 process_chunk() 对后续每个 chunk
        重复调用此方法，导致流中出现大量重复的 response.created 和 response.in_progress 事件。
        """

    def _handle_text_delta(self, text: str) -> list[str]:
        """处理文本 delta。
        
        首次调用时依次发送：
        - response.output_item.added（item 完整结构：
            {"type":"message","id":"<text_message_id>","status":"in_progress","role":"assistant","content":[]}
          output_index = text_output_index，分配逻辑：若 text_message_opened=False，
          先 text_output_index=next_output_index++、生成 text_message_id、置 text_message_opened=True）
        - response.content_part.added（content_index=0，part 完整结构：
            {"type":"output_text","text":"","annotations":[]}）
          置 text_content_part_opened=True
        后续每次发送：
        - response.output_text.delta（output_index=text_output_index，content_index=0，delta=text）
        同时将 text 追加到 accumulated_text。
        """

    def _handle_refusal_delta(self, refusal: str) -> list[str]:
        """处理拒绝 delta。
        
        若 message item 尚未打开（text_message_opened=False），则新建 message item：
        - 分配 text_output_index（= next_output_index++），生成 text_message_id
        - 发 output_item.added（使用 text_message_id）
        - **将 text_message_opened 置为 True**（确保 finish() 步骤 4 的条件命中，_emit_message_item_done 能发送）
        若 message item 已打开（text_message_opened=True），则复用已有 item，不再发 added。
        若 refusal_opened=False：
        - 计算 refusal_content_index = 1 if text_content_part_opened else 0
        - **将此值存入 self.refusal_content_index**（避免 _close_refusal_block() 在
          text_content_part_opened 已被置 False 后计算出错误的时序竞态问题）
        - 发 content_part.added（part 完整结构：{"type":"refusal","refusal":""}，
          content_index=self.refusal_content_index）
        - 置 refusal_opened=True
        后续每次发送：
        - response.refusal.delta（含 output_index、content_index=self.refusal_content_index、delta）
        同时将 refusal 追加到 accumulated_refusal。
        
        注意：text_message_opened=False 时是**新建** message item，而非复用已有的。
        当只有 refusal 没有 text 时，message item 也用 text_message_id/text_output_index，
        保证 text+refusal 始终共享同一个 message output item。
        """

    def _handle_reasoning_delta(self, reasoning: str) -> list[str]:
        """处理推理 delta。
        
        首次调用时发送 output_item.added（item.id=reasoning_id）。
        后续每次发送：response.reasoning.delta
        """

    def _handle_tool_call_delta(self, tc_delta: dict) -> list[str]:
        """处理工具调用 delta（含延迟启动逻辑）。

        tc_delta 字段结构（来自 Chat Completions SSE tool_calls delta chunk）：
        {"index": int, "id": str, "type": "function",
         "function": {"name": str, "arguments": str}}   # name/arguments 可能分片到达

        延迟启动：等 call_id 和 name 都就绪才发 output_item.added（item.id=block.item_id）。
        启动前积累的 arguments 在启动后一次性通过单个 function_call_arguments.delta 发出，
        优先保证事件序列简洁。若 arguments 在 id/name 就绪前到达，按到达顺序积累。
        """

    def _close_text_block(self) -> list[str]:
        """关闭文本块：response.output_text.done → response.content_part.done"""

    def _close_refusal_block(self) -> list[str]:
        """关闭拒绝块：response.refusal.done → response.content_part.done
        
        使用 self.refusal_content_index（在 _handle_refusal_delta 首次打开时已保存），
        **不得**实时计算 `1 if text_content_part_opened else 0`——此时 _close_text_block()
        可能已将 text_content_part_opened 置为 False，实时计算会得到错误的 0。
        """

    def _emit_message_item_done(self) -> list[str]:
        """发送 message output_item.done（含 text + refusal 合并的完整 content 数组，复用 text_message_id）。
        
        item 完整结构：
        {
            "type": "message",
            "id":   text_message_id,
            "status": "completed",          # 必须为 "completed"，Codex CLI 依此判断 item 已就绪
            "role": "assistant",
            "content": [<text_block>, <refusal_block>]   # 视实际情况含 0~2 个 block
        }
        
        content 数组构建规则：
        - 若 accumulated_text 非空：添加 {"type":"output_text","text":"<accumulated_text>","annotations":[]}
        - 若 accumulated_refusal 非空：添加 {"type":"refusal","refusal":"<accumulated_refusal>"}
        - 若 content 数组为空（极端边界：message 被打开但既无文本也无拒绝）：
          兜底补一个空 output_text 块 {"type":"output_text","text":"","annotations":[]}，
          防止 Codex 收到 content=[] 的格式非法 message（对齐 codex-proxy line 909-915）
        """

    def _close_reasoning_block(self) -> list[str]:
        """关闭推理块：response.reasoning.done → response.output_item.done（复用 reasoning_id）
        
        reasoning.done 的 text 字段为 accumulated_reasoning（完整推理文本）。
        output_item.done 的 item 完整结构（对齐 codex-proxy line 718-726）：
        {
            "type": "reasoning",
            "id":   reasoning_id,
            "summary": [{"type": "summary_text", "text": "<accumulated_reasoning>"}]
        }
        Codex CLI 读取 item.summary[0].text 渲染推理内容，summary 结构错误会导致推理静默不显示。
        """

    def _close_tool_blocks(self) -> list[str]:
        """关闭所有工具块（含强制启动未就绪的）。
        
        每个块依次发送：
        - （若未就绪）先对未就绪字段应用 fallback（对齐 codex-proxy line 754-757）：
            block.call_id = block.call_id or f"tool_call_{tc_index}"
            block.name    = block.name    or "unknown_tool"
          再生成 block.item_id；
          再发 output_item.added + function_call_arguments.delta（积累的 args，如有）
        - response.function_call_arguments.done
        - response.output_item.done（item 含 status:"completed"，复用 block.item_id）
        
        排序：按 tc_index 升序处理已就绪的块（保持与上游 tool_calls 数组顺序一致）；未就绪的块按其 tc_index 追加到末尾。output_index 仅作 SSE 事件标识，不决定输出顺序。
        """

    def _build_response_obj(
        self,
        status: str,
        usage: dict = None,
        output: list = None,
        incomplete_details: dict = None,  # finish_reason=length/content_filter 时传入
    ) -> dict:
        """统一构建 response 对象，供 _emit_created() 和 finish() 共用。

        始终包含七个必需字段（对齐 codex-proxy _build_response_object_dict）：
        id / object / created_at / status / model / output / usage
        缺少任何一个会导致 Codex CLI 运行时错误。

        - _emit_created() 调用时：status="in_progress", output=[], usage 全为 0 的占位值
        - finish() 调用时：status="completed"/"incomplete"/"failed", output=output_items, usage=最终 usage

        usage=None 时 fallback（对齐 codex-proxy line 871）：
            {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        （上游未开启 stream_options: {include_usage: true} 时会出现此情况）

        incomplete_details：仅在 finish_reason 为 "length" 或 "content_filter" 时传入并写入
        response 对象（对齐 codex-proxy line 874-875）；其他情况省略此字段。
        """

    def _convert_usage(self, raw: dict) -> dict:
        """将上游 Chat Completions usage chunk 转换为 Responses API usage 格式。
        
        上游字段（prompt_tokens/completion_tokens）→ Responses 字段（input_tokens/output_tokens），
        同时展开 details 子字段（input_tokens_details/output_tokens_details）。
        raw=None 时返回 None（与 usage 占位 fallback 区分）。
        """

    def _format_sse(self, event_type: str, data: dict) -> str:
        """格式化 SSE 事件（复用 sse_utils._format_sse_event）
        
        输出格式：`event: {type}\ndata: {json}\n\n`（含 event: 行），与 codex-proxy（line 295）一致。
        _format_sse_event 同时在 SSE `event:` 行和 data JSON 的 `type` 字段注入事件类型。
        存量测试均通过 `assertIn("event: response.created", events_text)` 等方式断言 event: 行存在，
        实现时不得改为纯 data: 格式。
        """
```

### 3.4 与 `create_codex_sse_stream()` 的兼容过渡

- `create_codex_sse_stream()` 保留为入口函数，内部实例化 `CodexStreamConverter` 并调用其方法
- 文件末尾增加 `StreamState = CodexStreamConverter`，保持向后兼容导入
- 删除已成为类方法的顶层函数：`_emit_created`、`_process_delta`、`_emit_completion`；同时从 `transform.py` 的 re-export 列表中移除这三项
- 现有测试需同步更新：
  - `TestStreamState`：改为测试 `CodexStreamConverter` 的 `next_output_index` 递增行为，删除 `has_text`/`has_reasoning`/`message_output_index` 相关断言
  - `test_reasoning_plus_text_stream`：推理事件名从 `response.reasoning_summary_text.delta/done` 改为 `response.reasoning.delta/done`
  - `test_text_only_stream`：补充 `response.in_progress`、`response.content_part.added/done`、`data: [DONE]` 的断言
  - `test_tool_calls_accumulation`：补充 `output_item.added`、`function_call_arguments.delta/done`、`data: [DONE]` 的断言
- 新增测试覆盖见 §7.2

---

## 4. Response Store + previous_response_id（Phase 2）

### 4.1 需求

openai-responses-adapter 支持 `previous_response_id` 参数和 `store` 参数，实现多轮对话链。Codex 在多轮对话中也依赖此机制。

### 4.2 设计

```python
@dataclass
class ResponseRecord:
    response_id: str
    model: str
    output: list          # Responses API output items（返回给客户端用）
    conversation: list    # Chat Messages 格式（previous_response_id 重建历史用）
    usage: dict
    status: str
    created_at: float
    expires_at: float     # TTL 过期时间

class ResponseStore:
    """内存 response store，LRU + TTL 淘汰。"""

    def __init__(self, max_entries: int = 1000, ttl_seconds: int = 3600):
        # 使用 collections.OrderedDict 实现 LRU：
        # - move_to_end(key) O(1) 将访问项移到尾部（最新）
        # - popitem(last=False) O(1) 淘汰头部（最旧）
        # 不使用 deque，deque 只支持 O(1) 的两端操作，将中间元素移到端部是 O(n)
        self._store: OrderedDict = OrderedDict()  # response_id -> ResponseRecord

    def put(self, response_id: str, record: ResponseRecord):
        """存储 response，淘汰过期条目"""

    def get(self, response_id: str) -> Optional[ResponseRecord]:
        """获取 response，更新 LRU 顺序"""

    def get_conversation(self, response_id: str) -> list:
        """获取对应的 Chat Messages 历史（直接从 record.conversation 读取）"""
```

> **说明**：参考 Go 实现（`state/store.go`），`ResponseRecord` 同时存储两份数据：
> - `output`：Responses API 格式，供 `response.completed` 事件使用
> - `conversation`：已完成转换的 Chat Messages 列表，供 `previous_response_id` 直接拼接，避免二次转换

### 4.3 存储内容

每个 response 同时存储两份数据：
- **`output`**：完整的 Responses API output items 数组（message、function_call、reasoning 等），用于 `response.completed` 事件和返回给客户端
- **`conversation`**：已转换好的 Chat Messages 列表（与上游通信格式相同），用于 `previous_response_id` 历史拼接，直接追加到下一轮请求的 messages 前，无需二次转换

存储时机：非流式响应在 `chat_to_responses()` 之后，由调用方（`proxy.py`）存入；流式响应在 `create_codex_sse_stream()` 调用 `converter.finish()` 完成后，由 `create_codex_sse_stream()` 读取 `converter.output_items` 并存入。

**`conversation` 的构建**：需要一个辅助函数 `_output_items_to_messages(output_items: list) -> list`，将 Responses API output items 反转为 Chat Messages 格式：
- `type=message` → `{"role": "assistant", "content": <text>}`，其中 text 取法：
  取第一个 `type=output_text` 的 content block 的 `text` 字段；
  若无 output_text 块（纯拒绝响应），fallback 为空字符串 `""`（**不得**直接访问 `content[0].text`，
  纯拒绝时 `content[0].type=="refusal"`，该对象无 `text` 字段，导致 KeyError 或 `content=None`，
  多轮对话时上游会报 400）；refusal 块**有意丢弃**，不参与对话历史：
  ```python
  text = next(
      (b["text"] for b in item.get("content", []) if b.get("type") == "output_text"),
      "",
  )
  chat_msgs.append({"role": "assistant", "content": text})
  ```
- `type=function_call` → **全部收集后合并**为一条 `{"role": "assistant", "content": null, "tool_calls": [tc1, tc2, ...]}`（Chat Completions 要求并发工具调用必须在同一条 assistant 消息中；每条单独的 assistant message 都有 `tool_calls` 是格式错误，会导致上游报 400）
- `type=reasoning` → 跳过（reasoning 不参与 Chat Completions 历史）

此函数在 Phase 1 Task 13 中实现，由 transform_responses.py 导出，transform.py re-export。
Phase 2 和 Phase 3 直接导入使用（不需要二次实现）。

**`record.conversation` 完整性**：`conversation` 必须包含完整的上下文交换，而不只是 assistant 的输出。构建方式为：

```
conversation = [m for m in chat_body["messages"] if m.get("role") != "system"] \
               + _output_items_to_messages(output_items)
```

即过滤掉 system 消息后的请求 messages（含 user/tool 历史）加上本轮 assistant 的输出。过滤 system 消息的原因：每轮新请求都带有自己的 `instructions`（→ system 消息），若 `conversation` 中保留历史 system，拼接后上游将收到两条 system 消息，导致行为不确定；去掉 system 只保留 user+assistant 历史，让每轮的 system 独立生效。

注意：`chat_body["messages"]` 来自 `responses_to_chat()` 已转换的结果，其中已包含 system（来自 `instructions`）和 user 消息；调用方（`proxy.py` 或 `create_codex_sse_stream`）在存储时必须同时传入 `chat_body["messages"]`，而不能只传 `converter.output_items`。

### 4.4 Proxy 集成

在 `_handle_responses()` 中：
1. 检查 `previous_response_id`，从 store 获取历史记录
2. 将 `record.conversation` 插入本轮 messages 中，并确保 system 消息始终排在首位：

```python
system_msgs = [m for m in chat_body["messages"] if m.get("role") == "system"]
non_system_msgs = [m for m in chat_body["messages"] if m.get("role") != "system"]
chat_body["messages"] = system_msgs + record.conversation + non_system_msgs
```

（**不能**直接 `record.conversation + chat_body["messages"]`，否则 system 消息夹在历史末尾，即 `[hist_user, hist_asst, system, new_user]`，绝大多数 LLM 会报 400 或行为不确定）
3. 响应完成后，如果 `store=true`（默认），用 `[m for m in chat_body["messages"] if m.get("role") != "system"] + _output_items_to_messages(output_items)` 构建 conversation 并存入 store（见 §4.3 过滤 system 消息的原因）

**previous_response_id 不存在时的行为决策**（2026-04-28）：当 `previous_response_id` 在 store 中不存在或已 TTL 过期时，采用 **silent fallback**——记录 warning 日志后继续请求，但不带历史上下文。不返回 400 错误。原因：长时间对话中 store 淘汰旧记录是正常行为，400 报错会导致 Codex 无法恢复。静默降级代价是损失对话连续性，但 Codex 仍能获得有效响应。

**系统提示重复处理**：`record.conversation` 包含历史 system 消息，拼接后上游可能收到两条 system 消息（历史 system1 + 本轮 system2）。处理策略：存储时**不保留** system 消息——`conversation` 中过滤掉 `role=system` 的条目，只保留 `user/assistant/tool` 消息。这样每轮请求只有本轮的 system 消息生效，避免歧义。过滤逻辑在 §4.3 的 `conversation` 构建处加入：

```python
conversation = [m for m in chat_body["messages"] if m.get("role") != "system"] \
               + _output_items_to_messages(output_items)
```

### 4.5 流式模式的存储集成

`finish()` 是 `CodexStreamConverter` 的方法，**不应**持有 store 引用。正确的存储调用链是：

```
# 函数签名（Phase 2 新增 request_messages 和 response_store 参数）：
def create_codex_sse_stream(
    upstream_response,
    request_messages: list = None,    # chat_body["messages"]，用于构建完整 conversation
    response_store=None,              # ResponseStore 实例，None 则不存储
):
    converter = CodexStreamConverter(...)
    for chunk in iter_sse_events(upstream):
        yield from converter.process_chunk(chunk)
    yield from converter.finish()          # finish() 只返回 SSE 事件字符串
    # finish() 返回后，output_items 已收集完整
    if response_store is not None:
        # conversation = 请求的完整 messages（去除 system）+ 本轮 assistant 输出
        # 过滤 system 消息，避免多轮对话时 system 消息重复叠加导致上游收到多条 system
        assistant_msgs = _output_items_to_messages(converter.output_items)
        conversation = [m for m in (request_messages or []) if m.get("role") != "system"] \
                       + assistant_msgs
        record = ResponseRecord(
            response_id=converter.response_id,
            output=converter.output_items,
            conversation=conversation,
            ...
        )
        response_store.put(converter.response_id, record)
```

`proxy.py` 的 `_forward_streaming` 调用处也需同步更新，将 `chat_body["messages"]` 和 `response_store` 实例传入。

客户端收到 `response.completed` 后，可用其 `response.id` 作为下一次请求的 `previous_response_id`。

> **已知缺口**：Go reference（handler.go line 150-168）实现了 `GET /v1/responses/{id}` 端点，
> 可通过 response_id 查询历史 response。本实现**只支持** POST /v1/responses（写入和链式读取），
> **不实现** GET /v1/responses/{id} 端点（按 ID 查询）。若 Codex CLI 通过 response_id 查询历史
> response，当前设计会返回 404。此端点为 out-of-scope，留待后续 Phase 按需补充。

---

## 5. MCP 工具自动执行（Phase 3）

### 5.1 需求

openai-responses-adapter 支持 MCP 工具自动执行：当上游返回 tool_calls 且所有工具都能在 MCP 服务器中找到时，代理自己执行工具并重新调用上游，最多 8 轮。

### 5.2 设计

```python
class MCPManager:
    """MCP 工具管理器。使用 mcp Python SDK（`pip install mcp`）实现 JSON-RPC over stdio。"""

    def __init__(self, servers_config: dict):
        """servers_config: {"server_name": {"command": "...", "args": [...]}}"""

    def has_tool(self, tool_name: str) -> bool:
        """检查工具是否存在"""

    def call_tool(self, tool_name: str, arguments: str) -> str:
        """执行 MCP 工具，返回结果文本。

        arguments 为原始 JSON 字符串（来自 Chat Completions tool_calls[].function.arguments）。
        内部负责调用 json.loads(arguments) 后再传给 MCP SDK——MCP SDK 底层 call_tool 接收 dict，
        而 Chat Completions 返回的是 JSON 字符串，两者不同，调用方无需自行解析。
        """

    def max_auto_rounds(self) -> int:
        """最大自动执行轮数，默认 8"""

def execute_mcp_loop(
    chat_body: dict,
    mcp_manager: MCPManager,
    upstream_cfg: dict,
    api_key: str,
) -> tuple[dict, list]:
    """非流式 MCP 工具自动执行循环。
    
    返回值：(final_response: dict, all_messages: list)
    - final_response：最后一轮上游响应（Chat Completions 格式，无 tool_calls）
    - all_messages：完整消息列表，含所有中间轮次的 tool_calls/tool_results
      （调用方用此列表构建 conversation，**不依赖**对 chat_body["messages"] 的副作用）
    
    执行步骤：
    1. 发送 chat_body 到上游
    2. 解析响应
    3. 如果有 tool_calls 且所有工具都能在 MCP 中找到：
       a. 执行每个 MCP 工具
       b. 将工具结果追加到工作消息列表
       c. 回到步骤 1
    4. 如果没有 tool_calls 或有不认识的 tool_calls，返回最终响应
    5. 最多循环 max_auto_rounds 次
    
    错误合约：
    - 若达到 max_auto_rounds 仍有 tool_calls，抛出 RuntimeError("too many tool-call rounds")
    - 若任一工具调用失败（call_tool 抛出异常），立即向上传播，调用方负责返回 500 给客户端
      （对齐 Go reference handler.go line 309-311）
    """
```

### 5.3 流式模式的限制

流式模式下 **不支持 MCP 自动执行**。原因：
- Codex 流式模式下，tool_calls 通过 SSE 发送给客户端，由客户端决定如何处理
- 代理无法"拦截"流式输出并执行工具后再继续流
- 这是 openai-responses-adapter 的设计限制，也是业界标准做法

### 5.4 MCP 循环结果与 Response Store 的集成

`execute_mcp_loop()` 最终返回一个非流式 Chat Completions 响应（最后一轮，没有工具调用）。如果 `store=true`（默认），代理须将此结果存入 response store，以支持后续 `previous_response_id` 引用：

```
chat_resp, all_messages = execute_mcp_loop(chat_body, mcp_manager, upstream_cfg, api_key)
responses_resp = chat_to_responses(chat_resp)           # 转换为 Responses API 格式
if store_enabled:
    # conversation 必须包含完整上下文：原始请求 messages（含 system/user）
    # + MCP 循环内部所有轮次的 tool_calls/tool_results
    # + 最终 assistant 输出。
    # execute_mcp_loop 返回的 all_messages 末尾已含 execute_mcp_loop 追加的
    # 最终 assistant 消息，用 all_messages[:-1] 排除它，避免与 assistant_msgs 重复。
    assistant_msgs = _output_items_to_messages(responses_resp["output"])
    conversation = [m for m in all_messages[:-1] if m.get("role") != "system"] \
                   + assistant_msgs
    response_store.put(responses_resp["id"], ResponseRecord(
        response_id=responses_resp["id"],
        output=responses_resp["output"],
        conversation=conversation,
        ...
    ))
```

注意：MCP 循环内部的中间轮次（tool_calls + tool_results 的多轮 messages）**不存入** store，只存最终轮的响应。

---

## 6. 错误处理增强

### 6.1 流中错误

| 场景 | 当前行为 | 新行为 |
|---|---|---|
| 上游返回非 200 | 发 response.failed + completed | 同，但增加详细错误信息；补发 `data: [DONE]` |
| 上游返回非 SSE Content-Type | 发 response.failed + completed | 同；补发 `data: [DONE]` |
| 流解析异常 | 发 response.failed + completed | 同，但增加 traceback；补发 `data: [DONE]` |
| 上游返回空 choices | 静默处理 | 发空 output 的 completed；补发 `data: [DONE]` |
| idle timeout | 无 | 发 response.failed + completed + `data: [DONE]` |

> **[DONE] 在错误路径的必要性**：§3.1.6 规定 `data: [DONE]` 是流的终止信号，所有路径（正常/异常）都必须发送。当前 `proxy.py` 的三处错误处理（非 200、非 SSE Content-Type、流异常）均缺失 `[DONE]`，需在 Phase 1 步骤中一并修复（在 `_forward_streaming` 的每条错误返回路径末尾追加 `self.wfile.write(b"data: [DONE]\\n\\n")`）。

> **注意**：客户端已断开时 `self.wfile.write()` 会抛 `BrokenPipeError` / `ConnectionResetError`，
> 实现时应在 `wfile.write(b"data: [DONE]\\n\\n")` 外加 `try/except (BrokenPipeError, OSError)` 静默吞掉，
> 不影响日志输出。

### 6.2 客户端断开检测

Python 标准库 `BaseHTTPRequestHandler` 不直接支持 async disconnect 检测。实现方案：

```python
def _check_client_disconnected(self) -> bool:
    """检测客户端是否已断开连接。"""
    import select
    import socket
    import ssl
    try:
        sock = self.request
        if isinstance(sock, ssl.SSLSocket):
            # SSL socket 上 MSG_PEEK 行为依赖 OpenSSL 版本，不可靠
            # 退化为依赖写入异常检测断开（False = 假设仍连接）
            return False
        readable, _, _ = select.select([sock], [], [], 0)
        if readable:
            # 使用 MSG_PEEK 探测，不修改 socket 模式，不消耗数据
            data = sock.recv(1, socket.MSG_PEEK)
            return len(data) == 0
    except OSError:
        return True
    return False
```

> **注意**：使用 `socket.MSG_PEEK` 而非 `setblocking(False)`。`setblocking(False)` 会永久修改 socket 状态，导致后续的 `wfile.write()` 进入非阻塞模式，在写缓冲区满时抛出 `BlockingIOError` 并被静默吞掉，造成 SSE 数据丢失。`MSG_PEEK` 不消耗数据、不改变 socket 模式，是安全的探测方式。

此检测在 SSE 循环中每 N 个事件执行一次（N=10 左右）。如果检测到断开，关闭上游连接并记录日志。

> **实现状态（2026-04-28）**：`_check_client_disconnected` 已在此设计文稿中完成详细设计，
> 但**当前未纳入任何 Phase 的实施计划 Task**。原因：该功能属于连接可靠性增强，非 Codex CLI
> 兼容性的阻塞项。三份实施计划优先补齐事件序列、response store、MCP 循环三个核心能力。
> 建议在 Phase 1-3 全部完成后，以独立的小 Task（Phase 1 追加 Task 15）实施。

---

## 7. 测试策略

### 7.1 保留与更新现有测试

大部分 `test_transform.py` 测试保留不变作为回归保护。以下测试需同步更新（详见 §3.4）：

| 测试 | 变更原因 |
|---|---|
| `TestStreamState` | `has_text`/`has_reasoning`/`message_output_index` 已删除，改测 `next_output_index` 递增 |
| `test_reasoning_plus_text_stream` | 推理事件名更正为 `response.reasoning.delta/done` |
| `test_reasoning_plus_text_events_have_type_field` | 推理事件名更正为 `response.reasoning.delta/done`（与 `test_reasoning_plus_text_stream` 同原因；否则测试静默失效：检查旧名称是否存在，旧名称永远不会出现，`assertFalse(missing, ...)` 永远通过但已失去实际验证意义） |
| `test_text_only_stream` | 补充 `response.in_progress`、`content_part.added/done`、`[DONE]` 断言 |
| `test_tool_calls_accumulation` | 补充 `output_item.added`、`function_call_arguments.delta/done`、`[DONE]` 断言 |

### 7.2 新增测试覆盖

| 测试类别 | 测试用例 |
|---|---|
| content_part 生命周期 | 验证 text 的 added→delta×N→done 序列 |
| function_call_arguments | 验证 delta→done 序列，含延迟启动场景 |
| refusal 处理（流式） | 验证 refusal delta→done 序列，含 text+refusal 共享 message item |
| refusal 处理（非流式） | 验证 `chat_to_responses` 将 text+refusal 合并到同一 message content 数组 |
| response.in_progress | 验证 created→in_progress→metadata 序列 |
| response.reasoning.delta/done | 验证推理事件名正确，output_index 早于文本 |
| [DONE] 终止 | 验证流末尾包含 `data: [DONE]\n\n` |
| 空响应 | 验证无 delta 时 finish() 仍发送 created + in_progress + completed + [DONE] |
| 多工具并发 | 验证 index 0 和 index 2 先于 index 1 到达的场景，按 index 顺序关闭 |
| response store | 验证 put/get/conversation 链，LRU 淘汰，TTL 过期 |
| previous_response_id | 验证多轮对话 conversation 直接拼接（无二次转换） |
| `_output_items_to_messages` 独立单元测试 | 纯文本消息、纯拒绝消息（content=None 保护）、text+refusal 混合、多工具合并为单条 tool_calls 消息、reasoning 跳过 |
| `execute_mcp_loop` 错误路径 | 超过 max_auto_rounds 抛出 RuntimeError；call_tool 失败异常向上传播 |
| `_check_client_disconnected` 分支 | SSL socket 返回 False（无法可靠探测）；普通 socket 正常/断开两个分支 |

---

## 8. 迁移计划

### Phase 1（SSE 状态机重构）

**预估工作量**：5-8 小时
**风险等级**：中（核心代码重写，但有测试保护）

> **与实施计划 Task 编号映射**：以下步骤 1-10 对应实施计划
> `docs/superpowers/plans/2026-04-28-sse-state-machine-phase1.md` 的 14 个 Task。
> 设计文稿给出高层次步骤，实施计划将其拆分为独立可测试的 Task/commit 单元。

步骤：
1. 新增 `ToolBlockState` dataclass（→ Task 1）
2. 实现 `CodexStreamConverter` 类（含所有方法）（→ Tasks 2-8）
3. 修改 `create_codex_sse_stream()` 使用新类（→ Task 9）
4. 末尾添加 `StreamState = CodexStreamConverter` 别名（→ Task 2）
5. 删除已废弃的顶层函数 `_emit_created`、`_process_delta`、`_emit_completion`，并从 `transform.py` re-export 列表中移除（→ Task 9）
6. 修复非流式 `chat_to_responses`：将 text + refusal 合并进同一个 message output item 的 content 数组（对齐 codex-proxy `_build_message_output_item_with_refusal` 实现）。注：`output_tokens_details` 无推理 token 时的默认值当前代码（line 310-316）已经产出 `{"reasoning_tokens": 0}`，**无需修改**，此步骤只涉及 text+refusal 合并。（→ Task 11）
7. 修复 `proxy.py` 三处错误路径（非 200、非 SSE Content-Type、流异常），在每处 `response.completed` 之后补发 `data: [DONE]\n\n`（见 §6.1）（→ Task 12）
8. 将 `iter_sse_events` 读缓冲区从 256 字节增大至 4096 字节（→ Task 12）
9. 更新 `test_transform.py`（详见 §3.4）（→ Task 10）
10. 运行全量测试，确保全部通过（→ Tasks 13-14）

### Phase 2（Response Store）

**预估工作量**：3-4 小时
**风险等级**：低（纯新增功能，不影响现有路径）

步骤：
1. 实现 `response_store.py`（`ResponseRecord` dataclass + `ResponseStore` 用 `OrderedDict` 实现 LRU+TTL）
2. 在 `transform_responses.py` 中实现 `_output_items_to_messages(output_items: list) -> list`（Responses API output items → Chat Messages 反转转换，见 §4.3）
3. 在 `ThreadedHTTPServer` 子类上挂载 `response_store` 属性（`server.response_store = ResponseStore(...)`），`ProxyHandler` 通过 `self.server.response_store` 访问；在 `main()` 中按 `proxy_config.yaml` 的 `response_store` 节初始化
4. 在 `proxy.py` 中集成 `previous_response_id` 处理：读取历史 conversation 并拼接到本轮 messages 前（见 §4.4）
5. 在 `proxy.py` 非流式路径中，`chat_to_responses()` 后用 `[m for m in chat_body["messages"] if m.get("role") != "system"] + _output_items_to_messages(output_items)` 构建 conversation 并存入 store
6. 在 `create_codex_sse_stream()` 中新增 `request_messages` 和 `response_store` 参数，`converter.finish()` 完成后存入 store（见 §4.5）；更新 `proxy.py` `_forward_streaming` 调用处传入两个新参数
7. 编写测试

### Phase 3（MCP 支持）

**预估工作量**：4-6 小时
**风险等级**：低（纯新增功能，仅影响非流式路径）

步骤：
1. 安装依赖：`pip install mcp`（MCP Python SDK，提供 JSON-RPC over stdio 客户端）
2. 实现 `mcp_manager.py`（使用 `mcp` SDK 的 `StdioClient`）
3. 在 `proxy.py` 中集成 MCP 循环：在非流式路径判断是否有 MCP 工具，如有则调用 `execute_mcp_loop()`，循环结果经 `chat_to_responses()` 转换后存入 response store（见 §5.4）
4. 配置更新（proxy_config.yaml 新增 mcp 节）
5. 编写测试

---

## 9. 配置更新

`proxy_config.yaml` 新增配置节：

```yaml
# Response Store 配置
response_store:
  max_entries: 1000
  ttl_seconds: 3600

# MCP 配置
mcp:
  servers:
    web_search:
      command: "npx"
      args: ["-y", "@modelcontextprotocol/server-web-search"]
    filesystem:
      command: "npx"
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/Users/xys"]
  max_auto_rounds: 8
```

---

## 10. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|---|---|---|
| SSE 事件序列变更导致 Codex 不兼容 | 高 | 对齐 codex-proxy + openai-responses-adapter 两个已验证实现 |
| response store 内存泄漏 | 中 | LRU + TTL 双重淘汰 |
| 长对话 conversation 线性增长（O(n²)） | 中 | 建议设 conversation 最大 token 数，超出截断最老的轮次 |
| MCP 工具执行超时阻塞代理 | 中 | 每个工具调用设置超时（默认 30s） |
| 状态机复杂度增加 | 低 | 拆分为独立方法，每个方法单一职责 |
| 现有测试无法覆盖新增事件 | 低 | 新增测试覆盖每个新增事件类型 |
