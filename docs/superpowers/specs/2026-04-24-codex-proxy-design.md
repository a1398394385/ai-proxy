# Codex Responses API → Chat Completions 转换代理设计

## 概述

为 `fact-store-browser` 项目新增一个本地 HTTP 代理，将 Codex CLI 发出的 OpenAI Responses API 请求双向转换为 Chat Completions 格式，使公司内部仅支持 Chat Completions 的 LiteLLM 网关能够为 Codex 提供服务。

**访问方式**：Codex 配置 `OPENAI_BASE_URL=http://127.0.0.1:48743/v1`，`OPENAI_API_KEY` 填任意占位符。

---

## 架构

```
Codex CLI
  │  POST /v1/responses  (Responses API + SSE)
  ▼
proxy.py  :48743
  │  responses_to_chat()     → POST /v1/chat/completions
  │                          ← Chat Completions (JSON / SSE)
  │  chat_to_responses()
  │  create_codex_sse_stream()
  ▼
Codex CLI
  (Responses API SSE)
```

### 新增文件

```
fact-store-browser/
├── proxy.py              # HTTP server 主程序 + 请求路由
├── transform.py          # 纯转换逻辑（无 IO，可独立测试）
├── proxy_config.yaml     # 配置文件
└── .proxy.pid            # PID 文件（运行时生成）
```

`server.sh` 扩展：`start`/`stop`/`status` 同时管理 `server.py` 和 `proxy.py` 两个进程。

日志：同时写 `proxy.log`（文件）和 stdout，遵循 `log_level` 配置。
日志轮转：启动时检查 `proxy.log` 大小，超过 101 MB 时将当前日志重命名为 `proxy.log.YYYYMMDD.gz`（gzip 压缩）后新建空日志。每次启动最多触发一次轮转。

---

## 配置格式 `proxy_config.yaml`

```yaml
proxy:
  host: "127.0.0.1"
  port: 48743
  log_level: "INFO"          # DEBUG / INFO / WARNING

upstream:
  base_url: "https://llm-open-api.cargoware.com/v1"
  api_key: "sk-xxx"
  timeout: 120               # SSE 流总超时（秒）
  connect_timeout: 10
  ssl_verify: true           # 内网自签证书时可设为 false
  retry: 1                   # 上游 5xx/超时时自动重试次数（0 = 不重试）

model_map:
  "codex-mini-latest":
    target: "claude-sonnet-4-6"
    multimodal: false          # 不支持图片/文件时，将 input_image 转为占位文本 + WARNING 日志
  "o4-mini":
    target: "claude-sonnet-4-6"
    multimodal: false
  "gpt-4o":
    target: "claude-sonnet-4-6"
    multimodal: false
  "*":
    target: "claude-sonnet-4-6"   # 未命中时的 fallback，继承 multimodal: false
```

**启动校验**：`model_map` 必须含 `"*"` 键，否则进程启动失败并打印明确错误。

---

## 端点覆盖

| 端点 | 方法 | 处理逻辑 |
|------|------|---------|
| `POST /v1/responses` | POST | 核心转换：Responses → Chat → Responses |
| `POST /v1/responses/compact` | POST | 与 `/v1/responses` 完全相同的转换逻辑 |
| `GET /v1/responses` | GET | 返回 `426 Upgrade Required`（触发 Codex 回退 HTTP SSE） |
| `GET /v1/models` | GET | 返回合成模型列表（model_map 中所有非 `*` 的 key） |
| `GET /health` | GET | 健康检查，返回 `{"status":"ok","pid":<pid>}` |
| 其他路径 | 任意 | 返回 `404` |

---

## 请求转换 `responses_to_chat(body: dict, model_cfg: dict) -> dict`

`model_cfg` 为 model_map 中命中的条目，包含 `target` 和 `multimodal` 字段，由调用方从配置中查找后传入。

### 字段映射

| Responses 字段 | Chat Completions 映射 | 备注 |
|---|---|---|
| `instructions` | `messages[0] {role:"system", content:...}` | 为空则不插入 |
| `input[]` | `messages[]` | 见下方详细规则 |
| `model` | `model` | 经 model_map 映射 |
| `max_output_tokens` | `max_tokens` | |
| `tools` | `tools` | 透传 |
| `tool_choice` | `tool_choice` | 透传 |
| `parallel_tool_calls` | `parallel_tool_calls` | 透传 |
| `stream` | `stream` | 透传 |
| `reasoning.effort` | `reasoning.effort` | 透传（供应商可忽略） |
| `text.format` | `response_format` | 见结构化输出映射 |
| `previous_response_id` | 丢弃 | HTTP SSE 模式靠完整 input 维持上下文 |
| `include` | 丢弃 | |
| `store` | 丢弃 | |
| `text`（其余字段） | 丢弃 | |
| `client_metadata` | 丢弃 | |
| `service_tier` | 丢弃 | 避免部分供应商报错 |
| `reasoning.summary` | 丢弃 | Chat 不支持 |

### `input` 条目映射

```
type == "message"
  content: string                    → content 直接透传
  content: [{type:"input_text"}]     → [{type:"text", text:...}]
  content: [{type:"input_image",     → multimodal:true  → [{type:"image_url",
             image_url, detail}]                              image_url:{url, detail}}]
                                     → multimodal:false → [{type:"text",
                                                            text:"[image: unsupported]"}] + WARNING 日志
  content: [{type:"input_file",      → [{type:"text",
             file_id, filename}]        text:"[file: {filename}]"}]  + DEBUG 日志

type == "function_call"             → {role:"assistant",
                                        tool_calls:[{id, type:"function",
                                        function:{name, arguments}}]}

type == "function_call_output"      → {role:"tool",
                                        tool_call_id, content:output}

type == "computer_call_output"      → {role:"tool",
                                        tool_call_id, content:output}

type == "reasoning"                 → 丢弃
type == "web_search_call"           → 丢弃 + WARNING 日志
type == "code_interpreter_call"     → 丢弃 + WARNING 日志
type == "mcp_call"                  → 丢弃 + WARNING 日志
其他未知类型                         → 丢弃 + WARNING 日志
```

**工具名 namespace**：tool name 中的 `.`（如 `mcp.server__tool`）原样透传，供应商侧报错时记日志但不阻断。

### 结构化输出映射

```python
# Responses API
"text": {"format": {"type": "json_schema", "name": "...", "schema": {...}, "strict": true}}

# → Chat Completions
"response_format": {"type": "json_schema",
                    "json_schema": {"name": "...", "schema": {...}, "strict": true}}
```

`text.format.type` 为 `"text"` 或 `"json_object"` 时直接映射 `response_format.type`。

---

## 非流式响应转换 `chat_to_responses(response: dict) -> dict`

### 字段映射

| Chat Completions 字段 | Responses 映射 |
|---|---|
| `id` (`chatcmpl-xxx`) | `id` → `resp-xxx`（替换前缀；若非 `chatcmpl-` 开头则生成 `resp-{uuid4()[:8]}`） |
| `model` | `model` |
| `choices[0].message.content` | `output[{type:"message", role:"assistant", content:[{type:"output_text",text}], status:"completed"}]` |
| `choices[0].message.tool_calls[]` | `output[{type:"function_call", id, call_id:id, name, arguments}]` |
| `choices[0].message.refusal` | `output[{type:"message", content:[{type:"refusal",refusal}]}]` |
| `choices[0].finish_reason=="stop"` | `status:"completed"` |
| `choices[0].finish_reason=="length"` | `status:"incomplete"`, `incomplete_details:{reason:"max_tokens"}` |
| `choices[0].finish_reason=="tool_calls"` | `status:"completed"` |
| `choices[0].finish_reason=="content_filter"` | `status:"incomplete"`, `incomplete_details:{reason:"content_filter"}` |
| `usage.prompt_tokens` | `usage.input_tokens` |
| `usage.completion_tokens` | `usage.output_tokens` |
| `usage.total_tokens` | `usage.total_tokens` |
| `usage.prompt_tokens_details.cached_tokens` | `usage.input_tokens_details.cached_tokens`（缺失则 `0`） |
| `usage.completion_tokens_details.reasoning_tokens` | `usage.output_tokens_details.reasoning_tokens`（缺失则 `0`） |

---

## SSE 流转换 `create_codex_sse_stream(upstream_response)`

### 流状态机

```python
@dataclass
class StreamState:
    response_id: str = ""
    model: str = ""
    # 推理 item（出现则为 output_index=0，message 顺移至 1）
    reasoning_buffer: str = ""
    has_reasoning: bool = False
    reasoning_item_announced: bool = False   # 是否已发 output_item.added(reasoning)
    # 文本 message item
    text_buffer: str = ""
    has_text: bool = False
    message_item_announced: bool = False     # 是否已发 output_item.added(message)
    # 工具调用积累：index → {id, name, arguments_buffer}
    tool_calls: dict = field(default_factory=dict)
    # 完成状态
    finish_reason: str = ""
    usage: dict = field(default_factory=dict)
    # 完整 output 数组（用于 response.completed）
    output_items: list = field(default_factory=list)
    # 是否已发送 response.created + response.metadata
    created_sent: bool = False

    @property
    def message_output_index(self) -> int:
        """message item 的 output_index：推理存在时为 1，否则为 0"""
        return 1 if self.has_reasoning else 0
```

### 完整 SSE 事件序列

**阶段 1 — 首个 chunk 到达时**（`created_sent == False`）：

仅发送 `response.created` 和 `response.metadata`，**暂不发送 `output_item.added`**（因为不知道第一个内容是推理还是文本）：

```
event: response.created
data: {"id":"resp-xxx","object":"response","model":"...","status":"in_progress","output":[]}

event: response.metadata
data: {"model":"claude-sonnet-4-6","previous_response_id":null}
```

**阶段 2 — 文本 delta**（`choices[0].delta.content` 非空）：

若 `message_item_announced == False`，先发 `output_item.added`：
```
event: response.output_item.added
data: {"output_index":{message_output_index},
       "item":{"type":"message","role":"assistant","content":[],"status":"in_progress"}}
```

然后发文本 delta：
```
event: response.output_text.delta
data: {"output_index":{message_output_index},"content_index":0,"delta":"Hello"}
# 同时 text_buffer += "Hello"，has_text = True
```

**阶段 3 — 推理 delta**（检测顺序：`delta.reasoning_content` → `delta.thinking` → `delta.reasoning`）：

推理总是在文本之前到达。若 `reasoning_item_announced == False`，先发 `output_item.added`（output_index 始终为 0）：
```
event: response.output_item.added
data: {"output_index":0,"item":{"type":"reasoning","id":"rs_xxx","summary":[],"status":"in_progress"}}
```

然后发推理 delta：
```
event: response.reasoning_summary_text.delta
data: {"output_index":0,"summary_index":0,"delta":"..."}
# 同时 reasoning_buffer += delta，has_reasoning = True
```

**阶段 4 — 工具调用 delta**（不发送事件，仅积累）：

```python
# choices[0].delta.tool_calls[{index, id, type:"function", function:{name, arguments}}]
# → tool_calls[index] = {id, name, arguments_buffer}（新建或追加 arguments_buffer）
```

**阶段 5 — finish_reason 出现**：

若有推理（`has_reasoning`）：
```
event: response.reasoning_summary_text.done
data: {"output_index":0,"summary_index":0,"text":"<full_reasoning>"}

event: response.output_item.done
data: {"output_index":0,"item":{"type":"reasoning","id":"rs_xxx",
       "summary":[{"type":"summary_text","text":"<full_reasoning>"}],"status":"completed"}}
```
→ 追加到 `output_items[0]`

若有文本（`has_text`）：
```
event: response.output_text.done
data: {"output_index":{message_output_index},"content_index":0,"text":"<full_text>"}

event: response.output_item.done
data: {"output_index":{message_output_index},"item":{"type":"message","role":"assistant",
       "content":[{"type":"output_text","text":"<full_text>"}],"status":"completed"}}
```
→ 追加到 `output_items`

若有工具调用（按 index 排序，逐个发送）：
```
event: response.output_item.done
data: {"output_index":{n},"item":{"type":"function_call","id":"call_xxx",
       "call_id":"call_xxx","name":"bash","arguments":"{\"command\":\"ls\"}"}}
```
→ 逐个追加到 `output_items`

若 `finish_reason == "length"`，额外发：
```
event: response.incomplete
data: {"reason":"max_tokens"}
```

若 `finish_reason == "content_filter"`，额外发：
```
event: response.incomplete
data: {"reason":"content_filter"}
```

**阶段 6 — usage 捕获**：

usage 可能随 finish_reason 同包到达，也可能在其后独立 chunk 中到达（LiteLLM 行为不确定）。实现上**读完所有 chunk 后再发 `response.completed`**，届时 `output_items` 已包含所有 item：

```
event: response.completed
data: {"id":"resp-xxx",
       "status":"completed",
       "output":[
         {"type":"reasoning","id":"rs_xxx","summary":[...],"status":"completed"},
         {"type":"message","role":"assistant","content":[...],"status":"completed"}
       ],
       "usage":{
         "input_tokens":100,
         "output_tokens":50,
         "total_tokens":150,
         "input_tokens_details":{"cached_tokens":0},
         "output_tokens_details":{"reasoning_tokens":0}
       }}
```

`output` 数组由 `StreamState.output_items` 提供，按 output_index 顺序排列。

**阶段 7 — `data: [DONE]`**：停止读取，关闭连接。不发额外事件。

---

## SSE 解析器（标准库实现）

```python
def iter_sse_events(upstream_response):
    """逐 chunk 读取 HTTP 响应流，yield 解析后的 SSE 事件"""
    buf = b""
    while True:
        chunk = upstream_response.read(256)   # 小 size 减少延迟
        if not chunk:
            break
        buf += chunk
        while b"\n\n" in buf:
            raw, buf = buf.split(b"\n\n", 1)
            event = _parse_sse_event(raw.decode("utf-8", errors="replace"))
            if event:
                yield event

def _parse_sse_event(text: str) -> Optional[dict]:
    event_type, data_lines = "message", []
    for line in text.splitlines():
        if line.startswith("event: "):    event_type = line[7:]
        elif line.startswith("data: "):  data_lines.append(line[6:])
        # ": " 开头为注释行（keepalive），跳过
    raw = "\n".join(data_lines)
    if not raw:
        return None
    if raw == "[DONE]":
        return {"event": "[DONE]", "data": None}
    try:
        return {"event": event_type, "data": json.loads(raw)}
    except json.JSONDecodeError:
        return None
```

**关键实现注意**：使用 `http.client.HTTPSConnection` 直连替代 `urllib.request.urlopen`，避免 urllib 对 chunked transfer 的内部缓冲导致 SSE 延迟。非流式请求可继续用 `urllib`。

---

## HTTP 转发请求头

**发往上游**（替换/添加）：

```
Authorization:   Bearer {upstream.api_key}   # 替换 Codex 的 token
Content-Type:    application/json
Accept:          text/event-stream           # 流式时
                 application/json            # 非流式时
```

**丢弃**：Codex 发来的 `Host`、`Connection`、`Transfer-Encoding`、原 `Authorization`。不发 `Accept-Encoding`（避免 gzip 压缩干扰流式解析）。

**上游响应透传给 Codex**：

```
Content-Type:        text/event-stream / application/json
Cache-Control:       no-cache
X-Accel-Buffering:   no
openai-model:        （如有）
x-codex-turn-state:  （如有，供粘性路由）
```

**不透传**：`Transfer-Encoding`、`Content-Length`（SSE 为 chunked，由 Python HTTP server 重新计算）。

---

## 错误处理全景

| 场景 | 处理 |
|------|------|
| 上游 4xx/5xx（非流式） | 透传上游状态码 + 原始错误 JSON |
| 上游 4xx/5xx（流式，在读取过程中） | 发 `response.failed` SSE 事件后关闭连接 |
| 上游连接超时 | 发 `response.failed {type:"server_error", message:"Upstream timeout"}` |
| 上游返回非 SSE 格式（如 502 HTML） | 检测 `Content-Type`，非 `text/event-stream` 时包装为 `response.failed` |
| `transform.py` 内部异常 | 发 `response.failed {type:"internal_error"}` + 记录完整异常栈 |
| SSL 证书错误 | 发 `response.failed {type:"server_error"}` + 记录日志 |
| 请求体 JSON 解析失败 | HTTP 400 + `{"error":{"type":"invalid_request_error","message":"..."}}` |
| model_map 无 `*` fallback（启动时） | 进程启动失败，stderr 输出明确错误 |
| 上游 5xx/超时（可重试错误） | 按 `upstream.retry` 配置重试，每次失败记 WARNING 日志，全部失败后发 `response.failed` |

`response.failed` 事件格式：
```
event: response.failed
data: {"type":"error","error":{"type":"server_error","message":"..."}}
```

---

## 并发模型

```python
from socketserver import ThreadingMixIn
from http.server import HTTPServer

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True   # 进程退出时强制终止工作线程
```

每个请求独立线程，SSE 长连接不阻塞其他请求。Codex 通常 1-2 个并发连接，无需线程池限制。
线程安全：`transform.py` 为纯函数无全局可变状态；`proxy.py` 中每个请求线程持有独立的 `StreamState` 和连接对象。配置文件仅在启动时读取一次，运行期间不变。

---

## `server.sh` 扩展

```bash
# start：同时启动两个进程
start() {
    start_data_browser   # 现有逻辑
    start_proxy          # 新增：python3 proxy.py &  echo $! > .proxy.pid
}

# stop：同时停止两个进程
stop() {
    stop_data_browser
    stop_proxy           # kill $(cat .proxy.pid)
}

# status：显示两个进程状态
status() {
    status_data_browser
    status_proxy
}
```

---

## 已知风险与处理方案

| # | 风险 | 等级 | 处理方案 |
|---|------|------|---------|
| 1 | 工具调用 delta 需积累后一次性发出 | 高 | StreamState 积累，finish_reason 时批量发送 |
| 2 | urllib chunked 缓冲导致 SSE 延迟 | 高 | 改用 `http.client.HTTPSConnection` 直连 |
| 3 | usage 字段出现时机不确定 | 中 | 读完所有 chunk 后再发 `response.completed` |
| 4 | 推理 delta 字段名不固定 | 中 | 按顺序检测 `reasoning_content`/`thinking`/`reasoning` |
| 5 | SSE 字节流不对齐事件边界 | 高 | buffer + `\n\n` 分割（已设计） |
| 6 | 上游返回非 SSE 格式（502 HTML 等） | 中 | Content-Type 检测 + 包装 error 事件 |
| 7 | 内网 SSL 证书自签 | 低 | 配置项 `ssl_verify: false` |
| 8 | 工具名含 `.` 供应商可能报错 | 低 | 透传，出错时记日志不阻断 |
| 9 | response_id 格式 Codex 是否验证 | 低 | 生成格式 `resp-{timestamp_ms}-{random_hex8}`（如 `resp-1714089600123-a3f1c2d4`），确保全局唯一且前缀符合 OpenAI 规范 |
| 10 | 多 tool_calls 顺序 | 低 | 按 `index` 字段排序后发送 |

---

## 实施顺序

1. **Step 1**：`transform.py` — 实现 `responses_to_chat` + `chat_to_responses` + 单元测试
2. **Step 2**：`transform.py` — 实现 SSE 状态机 `StreamState` + `iter_sse_events`
3. **Step 3**：`proxy.py` — ThreadedHTTPServer + 配置加载 + 路由骨架
4. **Step 4**：`proxy.py` — 流式请求处理（集成 StreamState）
5. **Step 5**：`proxy.py` — 非流式请求处理
6. **Step 6**：`proxy.py` — `/v1/models`、`/v1/responses/compact`、426 处理
7. **Step 7**：扩展 `server.sh`，添加 `proxy_config.yaml` 示例
8. **Step 8**：端对端验证（Codex CLI 实际请求测试）
