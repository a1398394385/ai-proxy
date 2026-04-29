# Codex Proxy 透传端点

## TL;DR

> **快速摘要**：在 Codex Proxy 中新增一个 catch-all 透传路由，匹配 `/v1/*` 路径，剥离 `/v1` 前缀后原样转发请求和响应到上游，同时保留 debug_log 和 token_stats 记录，不做任何协议转换。
> 
> **可交付物**：
> - `proxy.py`：新增 `_handle_pass_through()` handler + `_forward_pass_through_non_streaming()` 和 `_forward_pass_through_streaming()` 两个专用透传方法
> - 路径规范化和模型提取两个新工具函数
> - 透传端点的单元测试和集成测试
> 
> **预估工作量**：Medium
> **并行执行**：YES — 3 waves
> **关键路径**：Task 1 → Task 3 → Task 5 → Task 6 → Task 8

---

## Context

### 原始需求

在现有 OpenAI Responses 协议路由（`/v1/responses`）和 Anthropic Messages 协议路由（`/v1/messages`）之外，新增一个不做协议转换的透传接口。该接口需要：
1. 不做请求/响应格式转换，原样转发
2. 和现有接口一样记录 debug_log 和 token_stats
3. 和现有接口一样拼接请求路径到上游

### 访谈摘要

**关键讨论**：
- **路径格式**：匹配 `/v1/*`（除已注册路径），剥离 `/v1` 前缀后拼接上游路径。例如 `POST /v1/chat/completions` → upstream `POST /chat/completions`
- **模型路由**：通过 ConfigCache 解析模型名选择上游（base_url + api_key），但**不修改**请求体中的模型名
- **HTTP 方法**：支持 POST + GET
- **流式支持**：支持 SSE streaming 透传
- **日志记录**：debug_log 至少记录 raw_request 和 upstream_response 两个阶段；token_stats 尽力而为

**研究结果**：
- 现有路由使用精确字符串匹配（`self.path == "/v1/responses"`），catch-all 需要前缀匹配（`startswith("/v1/")`）
- 日志有 5 个阶段：raw_request → converted_request → upstream_response → converted_response → token_stats
- 现有 `_forward_streaming()` 硬编码了 Codex 格式的 SSE 事件注入，不适配透传
- 测试框架：unittest + pytest runner，333 tests passing，proxy.py 有 3 个测试文件

### Metis 审查

**已识别的缺口**（已处理）：
- **模型提取冲突**：需解析 JSON 提取 `model` 字段，同时保留原始 `body_raw` 字节用于转发 → 读取一次，分别处理
- **`_forward_streaming()` 不兼容透传**：硬编码 Codex SSE 错误事件注入 → **创建全新透传专用方法**，不复用现有实现
- **Token 统计不可靠**：响应格式未知时静默失败 → 提取失败时记录 warning 日志
- **路径遍历风险**：客户端可能构造 `../../../etc/passwd` → 实现路径规范化，拒绝 `..` 段
- **Header 安全**：客户端 Authorization 不应透传 → 始终替换为上游 API key

**Metis 推荐默认值**（已采纳）：
- 模型提取：POST 从 JSON body 提取，保留 raw bytes；GET 从 query string 提取；失败回退 `*` fallback
- SSE 语义：逐 chunk 原样中继，不注入任何代理级别事件
- Token 统计：尽力而为解析，失败时记录 warning
- debug_log 阶段：跳过 converted_request 和 converted_response（无转换含义）

---

## Work Objectives

### 核心目标

在 Codex Proxy 中新增一个透传路径，将 `/v1/*` 的请求原样（不做协议转换）转发到上游，同时保留完整的日志和 Token 统计记录。

### 具体可交付物

- **`proxy.py`**：新增 `_handle_pass_through()` handler、`_forward_pass_through_non_streaming()`、`_forward_pass_through_streaming()`
- **`proxy.py`**：新增 `_normalize_forward_path()` 路径规范化工具
- **`proxy.py`**：新增 `_extract_model_for_pass_through()` 模型提取工具
- **`proxy.py`**：在 `do_GET` 和 `do_POST` 最后一个 `elif` 注册 catch-all 路由
- **测试文件**：`test/test_proxy_pass_through.py` — 路径规范化、模型提取、端点行为、集成测试

### 完成定义

- [ ] `curl -X POST http://127.0.0.1:48743/v1/chat/completions -H "Content-Type: application/json" -d '{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}'` 返回上游原始响应
- [ ] `curl -X GET http://127.0.0.1:48743/v1/models` 返回上游模型列表（透传，不同于现有的 `/v1/models` 端点）
- [ ] `sqlite3 data/access_log.db "SELECT COUNT(*) FROM debug_log WHERE stage='raw_request'"` 透传请求后有新增记录
- [ ] `sqlite3 data/access_log.db "SELECT COUNT(*) FROM token_stats"` 透传请求后有 token 统计记录
- [ ] `python3 -m pytest test/test_proxy_pass_through.py -q` → 全部通过
- [ ] `python3 -m pytest test/ -q` → 333 + 新增 = 全部通过

### 必须包含

- Catch-all 路由匹配 `/v1/*`（放在所有精确路由之后）
- 原样转发请求体和响应体，不做任何格式修改
- debug_log 至少记录 raw_request 和 upstream_response 阶段
- token_stats 尽力而为记录
- 路径规范化（拒绝 `..`、规范化重复斜杠）
- 流式 SSE 透传（逐 chunk 中继，不注入代理事件）
- 支持 POST 和 GET 方法

### 必须不包含（护栏）

- **不修改**现有路由处理逻辑（`_handle_responses`、`_handle_messages`）
- **不修改** `_forward_non_streaming()` 和 `_forward_streaming()` 签名或行为
- **不修改**请求体或响应体内容
- **不注入** Codex 格式的 SSE 事件（`response.failed`、`response.completed`、`data: [DONE]`）
- **不透传**客户端 `Authorization` 头到上游
- **不支持** POST/GET 以外的 HTTP 方法
- **不添加**速率限制、请求体大小限制、非 JSON 请求体解析
- **不**改动 `server.py`

---

## Verification Strategy（强制性）

> **零人工干预** — 所有验证由代理执行。禁止需要「用户手动测试/确认」的验收标准。

### 测试决策

- **基础设施存在**：YES
- **自动化测试**：Tests-after（实现完成后编写测试）
- **框架**：unittest + pytest runner
- **测试顺序**：实现 → 编写测试 → 验证通过

### QA 策略

每个任务必须包含代理可执行的 QA 场景（见下方 TODO 模板）。
证据保存到 `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`。

- **API/后端**：使用 Bash (curl) — 发送请求，断言状态码 + 响应字段
- **数据库**：使用 Bash (sqlite3) — 查询 debug_log 和 token_stats 表
- **单元测试**：使用 Bash (python3 -m pytest) — 运行测试套件

---

## Execution Strategy

### 并行执行波次

> 通过将独立任务分组到并行波次中来最大化吞吐量。
> 每个波次完成后才开始下一个波次。
> 目标：每波 3-5 个任务。少于 2 个任务（最后一波除外）= 拆分不足。

```
Wave 1（立即开始 — 工具函数 + 路由注册）：
├── Task 1: 路径规范化工具 _normalize_forward_path() [quick]
├── Task 2: 模型提取工具 _extract_model_for_pass_through() [quick]
├── Task 3: 注册 catch-all 路由 [quick]
└── Task 4: _handle_pass_through() handler 骨架 [quick]

Wave 2（在 Wave 1 之后 — 核心转发实现，最大并行）：
├── Task 5: _forward_pass_through_non_streaming() [deep]
└── Task 6: _forward_pass_through_streaming() [deep]

Wave 3（在 Wave 2 之后 — 测试 + 文档）：
├── Task 7: 单元测试 + 集成测试 [unspecified-high]
└── Task 8: 端到端验证 + 文档更新 [quick]

Wave FINAL（在 ALL 任务之后 — 4 个并行审查，然后用户确认）：
├── Task F1: 计划合规审计 (oracle)
├── Task F2: 代码质量审查 (unspecified-high)
├── Task F3: 实际手动 QA (unspecified-high)
└── Task F4: 范围保真度检查 (deep)
→ 呈现结果 → 获取用户明确确认
```

**关键路径**：Task 1 → Task 5 → Task 7 → Task 8 → F1-F4 → 用户确认
**并行加速**：~50% 比顺序执行快
**最大并发**：4（Wave 1）

### 代理分发摘要

- **1**：**4** — T1-T4 → `quick`
- **2**：**2** — T5 → `deep`, T6 → `deep`
- **3**：**2** — T7 → `unspecified-high`, T8 → `quick`
- **FINAL**：**4** — F1 → `oracle`, F2 → `unspecified-high`, F3 → `unspecified-high`, F4 → `deep`

---

## TODOs

> 实现 + 测试 = 一个任务。绝不分开。
> 每个任务必须包含：推荐的代理配置文件 + 并行化信息 + QA 场景。
> **不含 QA 场景的任务是不完整的。没有例外。**

- [x] 1. 路径规范化工具 `_normalize_forward_path()`

  **做什么**：
  - 在 `proxy.py` 中新增 `_normalize_forward_path(path: str) -> str` 函数
  - 从请求路径中剥离 `/v1` 前缀
  - 拒绝包含 `..` 的路径（拒绝路径遍历攻击，返回 `None`）
  - 规范化重复斜杠（`//` → `/`）
  - 确保结果以 `/` 开头

  **不得做**：
  - 不要 URL 解码路径段（保留原始编码）
  - 不要修改 query string（仅处理 path 部分）

  **推荐代理配置文件**：
  > 根据任务领域选择类别 + 技能。为每个选择说明理由。
  - **类别**：`quick`
    - 原因：单一工具函数，逻辑简单直接
  - **技能**：`[]`
    - 无需特定领域技能，纯 Python 字符串处理

  **并行化**：
  - **可并行运行**：YES
  - **并行组**：Wave 1（与 Task 2、3、4）
  - **阻塞**：Task 5, Task 6
  - **被阻塞**：无（可立即开始）

  **引用**（关键 — 详尽列出）：

  > 执行者没有你访谈的上下文。引用是他们唯一的指南。
  > 每个引用必须回答：「我应该看什么，为什么？」

  **模式引用**（要遵循的现有代码）：
  - `proxy.py:384-387` — 上游 URL 拼接模式：`parsed.path.rstrip("/") + "/chat/completions"`，了解现有路径构建方式

  **API/类型引用**（要实现的合同）：
  - `urllib.parse.urlparse` — 标准库 URL 解析，用于拆分 path 和 query

  **外部引用**（库和框架）：
  - 无外部依赖

  **为什么每个引用都很重要**（解释相关性）：
  - `proxy.py:384-387`：展示现有代码如何构建上游路径，新函数需要产生兼容的输出

  **验收标准**：

  > **仅限代理可执行的验证** — 不允许人工操作。
  > 每个标准必须通过运行命令或使用工具来验证。

  **QA 场景（强制性 — 没有则任务不完整）**：

  > **这不是可选的。没有 QA 场景的任务将被拒绝。**
  >
  > 编写测试实际行为的情景测试。
  > 最少：每个任务 1 个正常路径 + 1 个失败/边缘情况。
  > 每个情景 = 确切工具 + 确切步骤 + 确切断言 + 证据路径。
  >
  > **执行代理必须在实现后运行这些场景。**
  > **编排者将在任务标记为完成前验证证据文件存在。**

  ```
  场景：正常路径 — 剥离 /v1 前缀
    工具：Bash (python3)
    前提条件：函数已导入
    步骤：
       1. python3 -c "from proxy import _normalize_forward_path; print(_normalize_forward_path('/v1/chat/completions'))"
       2. 断言输出为 "/chat/completions"
    预期结果：返回 "/chat/completions"
    失败指示器：输出不是 "/chat/completions" 或抛出异常
    证据：.sisyphus/evidence/task-1-normalize-ok.txt

  场景：路径遍历攻击 — 拒绝 ../
    工具：Bash (python3)
    前提条件：函数已导入
    步骤：
       1. python3 -c "from proxy import _normalize_forward_path; print(_normalize_forward_path('/v1/../../../etc/passwd'))"
       2. 断言输出为 "None"
    预期结果：返回 None（拒绝）
    失败指示器：返回了非 None 值
    证据：.sisyphus/evidence/task-1-traversal-rejected.txt

  场景：重复斜杠规范化
    工具：Bash (python3)
    前提条件：函数已导入
    步骤：
       1. python3 -c "from proxy import _normalize_forward_path; print(_normalize_forward_path('/v1//api//test'))"
       2. 断言输出为 "/api/test"
    预期结果：返回 "/api/test"
    失败指示器：返回带双斜杠的路径
    证据：.sisyphus/evidence/task-1-double-slash.txt
  ```

  **要捕获的证据**：
  - [ ] 每个证据文件命名为：task-{N}-{scenario-slug}.{ext}
  - [ ] 终端输出捕获

  **提交**：YES（与 Task 2, 3, 4 分组）
  - 消息：`feat(proxy): 新增透传路径规范化工具`
  - 文件：`proxy.py`
  - 预提交：`python3 -m pytest test/ -q`

- [x] 2. 模型提取工具 `_extract_model_for_pass_through()`

  **做什么**：
  - 在 `proxy.py` 中新增 `_extract_model_for_pass_through(method: str, path: str, body_raw: bytes) -> str` 函数
  - POST：尝试解析 `body_raw` 为 JSON，提取 `model` 字段；失败则回退到 `"*"`
  - GET：从 URL query string 提取 `?model=xxx`；无则回退到 `"*"`
  - 返回提取的模型名或 `"*"`

  **不得做**：
  - 不要修改请求体
  - 不要抛出异常（总是回退到 `"*"`）

  **推荐代理配置文件**：
  - **类别**：`quick`
    - 原因：简单解析逻辑
  - **技能**：`[]`

  **并行化**：
  - **可并行运行**：YES
  - **并行组**：Wave 1（与 Task 1, 3, 4）
  - **阻塞**：Task 5, Task 6
  - **被阻塞**：无

  **引用**：
  - `proxy.py:256` — 现有模型提取模式：`body.get("model", "*")`，使用相同的回退逻辑
  - `urllib.parse.parse_qs` — GET 请求查询字符串解析

  **验收标准**：

  **QA 场景**：

  ```
  场景：POST JSON — 提取 model 字段
    工具：Bash (python3)
    前提条件：函数已导入
    步骤：
       1. python3 -c "from proxy import _extract_model_for_pass_through; print(_extract_model_for_pass_through('POST', '/v1/chat/completions', b'{\"model\":\"gpt-4o\",\"messages\":[]}'))"
       2. 断言输出为 "gpt-4o"
    预期结果：返回 "gpt-4o"
    证据：.sisyphus/evidence/task-2-extract-post.txt

  场景：POST 无效 JSON — 回退到 *
    工具：Bash (python3)
    步骤：
       1. python3 -c "from proxy import _extract_model_for_pass_through; print(_extract_model_for_pass_through('POST', '/v1/test', b'not-json'))"
       2. 断言输出为 "*"
    预期结果：返回 "*"
    证据：.sisyphus/evidence/task-2-fallback-post.txt

  场景：GET query string — 提取 model
    工具：Bash (python3)
    步骤：
       1. python3 -c "from proxy import _extract_model_for_pass_through; print(_extract_model_for_pass_through('GET', '/v1/chat?model=claude-4', b''))"
       2. 断言输出为 "claude-4"
    预期结果：返回 "claude-4"
    证据：.sisyphus/evidence/task-2-extract-get.txt
  ```

  **提交**：YES（与 Task 1, 3, 4 分组）
  - 消息：`feat(proxy): 新增透传模型提取工具`

- [x] 3. 注册 catch-all 路由

  **做什么**：
  - 在 `proxy.py` 的 `do_GET` 方法中，在最后一个 `elif`（`else: 404` 之前）添加：
    `elif self.path.startswith("/v1/"): self._handle_pass_through()`
  - 在 `proxy.py` 的 `do_POST` 方法中，在最后一个 `elif`（`else: 404` 之前）添加：
    `elif self.path.startswith("/v1/"): self._handle_pass_through()`
  - **关键**：放在 `/v1/responses`、`/v1/messages`、`/v1/models`、`/admin/reload` 的精确匹配**之后**

  **不得做**：
  - 不要改动现有路由的顺序
  - 不要在 `/v1/responses` 之前放置 catch-all

  **推荐代理配置文件**：
  - **类别**：`quick`
    - 原因：两行路由注册
  - **技能**：`[]`

  **并行化**：
  - **可并行运行**：YES
  - **并行组**：Wave 1（与 Task 1, 2, 4）
  - **阻塞**：Task 4, Task 5, Task 6
  - **被阻塞**：无

  **引用**：
  - `proxy.py:186-199` — do_GET 现有路由结构
  - `proxy.py:201-209` — do_POST 现有路由结构

  **验收标准**：

  **QA 场景**：

  ```
  场景：catch-all 不拦截 /v1/responses
    工具：Bash (curl)
    前提条件：proxy 运行中（端口 48743）
    步骤：
       1. curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:48743/v1/responses
       2. 断言返回 426（现有行为），不是 404
    预期结果：HTTP 426
    证据：.sisyphus/evidence/task-3-route-priority.txt

  场景：catch-all 捕获未知路径
    工具：Bash (curl)
    步骤：
       1. curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:48743/v1/test-unknown
       2. 断言返回非 404（可能是 500 如果 handler 未完成，但至少路由命中）
    预期结果：HTTP 非 404
    证据：.sisyphus/evidence/task-3-catch-all.txt
  ```

  **提交**：YES（与 Task 1, 2, 4 分组）
  - 消息：`feat(proxy): 注册 /v1/* catch-all 透传路由`

- [x] 4. `_handle_pass_through()` handler 骨架

  **做什么**：
  - 在 `proxy.py` 中新增 `_handle_pass_through(self)` 方法
  - 读取请求体（POST）或使用空 bytes（GET）
  - 调用 `_extract_model_for_pass_through()` 提取模型名
  - 调用 `resolve_model()` 获取上游配置
  - 生成 `request_id` 和 `request_ts`
  - 记录 `raw_request` 阶段（debug_log）
  - 根据 `is_stream`（从 body 或 query 判断）分发到非流式或流式透传方法
  - 处理上游配置不可用的情况（返回 500）

  **不得做**：
  - 不要在这个方法中实现转发逻辑（留给 Task 5, 6）
  - 不要尝试转换请求体

  **推荐代理配置文件**：
  - **类别**：`quick`
    - 原因：handler 骨架，主要是编排逻辑
  - **技能**：`[]`

  **并行化**：
  - **可并行运行**：NO（依赖 Task 1, 2, 3 中的函数定义和路由）
  - **阻塞**：Task 5, Task 6
  - **被阻塞**：Task 1, Task 2, Task 3

  **引用**：
  - `proxy.py:235-285` — `_handle_responses()` 结构作为模板
  - `proxy.py:312-360` — `_handle_messages()` 结构作为模板
  - `proxy.py:256-260` — `resolve_model()` 调用和 upstream_cfg 提取的模式

  **验收标准**：

  **QA 场景**：

  ```
  场景：handler 调用 resolve_model 并记录 log
    工具：Bash (curl + sqlite3)
    前提条件：proxy 运行中
    步骤：
       1. curl -s -X POST http://127.0.0.1:48743/v1/echo -H "Content-Type: application/json" -d '{"model":"gpt-4o","test":true}'
       2. sqlite3 data/access_log.db "SELECT COUNT(*) FROM debug_log WHERE stage='raw_request' AND json_extract(data, '$.model')='gpt-4o'"
       3. 断言 count > 0
    预期结果：debug_log 中有 raw_request 记录
    证据：.sisyphus/evidence/task-4-skeleton-log.txt

  场景：无上游配置 — 返回错误
    工具：Bash (curl)
    前提条件：proxy 运行中
    步骤：
       1. curl -s -X POST http://127.0.0.1:48743/v1/test -H "Content-Type: application/json" -d '{"model":"nonexistent-model-xyz"}'
       2. 断言返回 500
    预期结果：HTTP 500
    证据：.sisyphus/evidence/task-4-no-upstream.txt
  ```

  **提交**：YES（与 Task 1, 2, 3 分组）
  - 消息：`feat(proxy): 新增 _handle_pass_through 骨架`

- [x] 5. `_forward_pass_through_non_streaming()` 非流式透传

  **做什么**：
  - 在 `proxy.py` 中新增 `_forward_pass_through_non_streaming(self, body_raw, request_id, model_name, target, request_ts, upstream_cfg, forward_path)` 方法
  - 使用 `http.client.HTTPConnection` 或 `HTTPSConnection` 连接上游
  - 构建上游路径：`parsed.path.rstrip("/") + forward_path`
  - 转发 HEADER：`Authorization: Bearer {api_key}`（不透传客户端 Auth）、`Content-Type` 复制自请求
  - 转发 BODY：`body_raw` 原样，不做任何修改
  - 读取上游完整响应，记录 `upstream_response` 阶段
  - 尽力提取 usage → `record_token_stats()`；提取失败时 log warning
  - 上游响应状态码和 body 原样返回客户端；支持重试

  **不得做**：
  - 不要解析/转换响应体、不注入 Codex 错误事件
  - 不记录 converted_request/converted_response、不存 ResponseStore

  **推荐代理配置文件**：
  - **类别**：`deep` — 核心转发逻辑，需处理连接/重试/错误/日志

  **并行化**：
  - **可并行运行**：YES（与 Task 6 并行）
  - **并行组**：Wave 2（与 Task 6）
  - **阻塞**：Task 7, Task 8
  - **被阻塞**：Task 1, 2, 3, 4

  **引用**：
  - `proxy.py:362-481` — `_forward_non_streaming()`：连接、重试、日志完整模式
  - `proxy.py:384-387` — 上游 URL 拼接（替换 `/chat/completions` 为 `forward_path`）
  - `proxy.py:453-456` — upstream_response 日志
  - `proxy.py:461-471` — token_stats context dict 结构

  **QA 场景**：

  ```
  场景：非流式透传 — 返回上游原始 JSON
    工具：Bash (curl)
    步骤：
       1. RESP=$(curl -s -X POST http://127.0.0.1:48743/v1/chat/completions -H "Content-Type: application/json" -d '{"model":"gpt-4o","messages":[{"role":"user","content":"say hi"}],"max_tokens":5}')
       2. echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'choices' in d or 'error' in d; print('OK')"
    预期结果：返回 choices 或 error
    证据：.sisyphus/evidence/task-5-non-streaming.txt

  场景：debug_log 记录
    工具：Bash (sqlite3)
    步骤：
       1. sqlite3 data/access_log.db "SELECT COUNT(*) FROM debug_log WHERE stage='upstream_response'"
       2. 断言 count > 0
    证据：.sisyphus/evidence/task-5-debug-log.txt

  场景：token_stats 记录
    工具：Bash (sqlite3)
    步骤：
       1. sqlite3 data/access_log.db "SELECT input_tokens,output_tokens FROM token_stats ORDER BY rowid DESC LIMIT 1"
    证据：.sisyphus/evidence/task-5-token-stats.txt
  ```

  **提交**：YES（与 Task 6 分组）
  - 消息：`feat(proxy): 实现非流式透传转发`

- [x] 6. `_forward_pass_through_streaming()` 流式 SSE 透传

  **做什么**：
  - 新增 `_forward_pass_through_streaming(self, body_raw, request_id, model_name, target, request_ts, upstream_cfg, forward_path)` 方法
  - 响应头：`Content-Type: text/event-stream`、`Cache-Control: no-cache`
  - 请求头：`Accept: text/event-stream`
  - 逐 chunk 读取上游 SSE → `self.wfile.write(chunk); self.wfile.flush()`
  - 原样中继，不注入任何代理级别 SSE 事件
  - 流结束后记录 `upstream_response` → 尽力提取 usage → token_stats

  **不得做**：
  - **绝不注入** `response.failed`、`response.completed`、`data: [DONE]`

  **推荐代理配置文件**：
  - **类别**：`deep` — 流式 chunk 管理、SSE 边界、连接生命周期

  **并行化**：
  - **可并行运行**：YES（与 Task 5）
  - **并行组**：Wave 2

  **引用**：
  - `proxy.py:483-713` — `_forward_streaming()` 流式结构（参考，不做 Codex 注入）
  - `proxy.py:516-521` — 流式响应头
  - `proxy.py:534-574` — 反例：需避免的 Codex 事件注入

  **QA 场景**：

  ```
  场景：SSE 流式输出
    工具：Bash (curl)
    步骤：
       1. curl -s -N -X POST http://127.0.0.1:48743/v1/chat/completions -H "Content-Type: application/json" -d '{"model":"gpt-4o","messages":[{"role":"user","content":"say hi"}],"stream":true,"max_tokens":10}' > /tmp/sse-out.txt
       2. head -5 /tmp/sse-out.txt
    预期结果：包含 "data:" SSE 事件
    证据：.sisyphus/evidence/task-6-sse.txt

  场景：不注入 Codex 事件
    工具：Bash (grep)
    步骤：
       1. grep -c "response.failed" /tmp/sse-out.txt
       2. 断言 count = 0
    证据：.sisyphus/evidence/task-6-no-codex.txt
  ```

  **提交**：YES（与 Task 5）
  - 消息：`feat(proxy): 实现流式 SSE 透传转发`

- [ ] 7. 单元测试 + 集成测试

  **做什么**：
  - 创建 `test/test_proxy_pass_through.py`
  - 路径规范化测试（3 个用例：正常、遍历拒绝、双斜杠）
  - 模型提取测试（3 个用例：POST JSON、无效 JSON fallback、GET query）
  - 路由优先级测试（catch-all 不拦截现有路由）
  - 集成测试：启动 proxy，发送 POST 请求，验证响应和日志
  - 使用 `unittest.TestCase` + `unittest.mock`（匹配现有测试风格）
  - 使用 `subprocess` 启动/停止 proxy 进行端到端测试

  **不得做**：
  - 不要使用 pytest 特定特性（fixture、parametrize）

  **推荐代理配置文件**：
  - **类别**：`unspecified-high` — 测试编写，覆盖多个模块

  **并行化**：NO（依赖 Task 1-6 完成）

  **引用**：
  - `test/test_proxy_logger_integration.py` — proxy 集成测试模式
  - `test/test_e2e_smoke.py` — 端到端子进程模式
  - `test/test_sse_utils.py` — 单元测试风格

  **QA 场景**：

  ```
  场景：全部测试通过
    工具：Bash (pytest)
    步骤：
       1. python3 -m pytest test/test_proxy_pass_through.py -v
       2. 断言全部 PASSED
    证据：.sisyphus/evidence/task-7-tests.txt

  场景：不破坏现有测试
    工具：Bash (pytest)
    步骤：
       1. python3 -m pytest test/ -q
       2. 断言 333 + 新增全部通过
    证据：.sisyphus/evidence/task-7-full-suite.txt
  ```

  **提交**：YES
  - 消息：`test(proxy): 新增透传端点测试`
  - 文件：`test/test_proxy_pass_through.py`

- [ ] 8. 端到端验证 + 文档更新

  **做什么**：
  - 运行 `./server.sh restart` 启动代理
  - 执行端到端 curl 测试
  - 验证 debug_log 和 token_stats 写入
  - 更新 `AGENTS.md` 路由表文档
  - 更新 `CLAUDE.md`（如需要）

  **不得做**：
  - 不要修改 `proxy_config.yaml` 或 `config.db`

  **推荐代理配置文件**：
  - **类别**：`quick` — 验证和文档

  **QA 场景**：

  ```
  场景：端到端 — POST 透传成功
    工具：Bash (curl + sqlite3)
    步骤：
       1. curl -s -X POST http://127.0.0.1:48743/v1/chat/completions -H "Content-Type: application/json" -d '{"model":"*","messages":[{"role":"user","content":"hello"}],"max_tokens":5}'
       2. sqlite3 data/access_log.db "SELECT stage FROM debug_log ORDER BY rowid DESC LIMIT 2"
    预期结果：响应为非错误 JSON；日志有 raw_request + upstream_response
    证据：.sisyphus/evidence/task-8-e2e.txt
  ```

  **提交**：YES
  - 消息：`docs(proxy): 更新路由文档`

---

## Final Verification Wave（强制性 — 所有实现任务之后）

> 4 个审查代理并行运行。全部必须 APPROVE。向用户呈现综合结果并在完成前获得明确的「okay」。
>
> **验证后不要自动继续。等待用户明确批准后再标记工作完成。**
> **在获得用户确认前，绝不打勾 F1-F4。** 拒绝或用户反馈 → 修复 → 重新运行 → 再次呈现 → 等待确认。

- [ ] F1. **计划合规审计** — `oracle`
  逐项对照计划：「必须包含」全部实现、「必须不包含」全部避免。检查证据文件存在于 `.sisyphus/evidence/`。对比可交付物。
  输出：`Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [ ] F2. **代码质量审查** — `unspecified-high`
  运行 `python3 -m pytest test/ -q` + 检查 `proxy.py` 增量：空 catch、`as any`、console.log、注释掉的代码、重复代码。
  输出：`Tests [N pass/N fail] | proxy.py [N issues] | VERDICT`

- [ ] F3. **实际手动 QA** — `unspecified-high`
  从干净状态启动。执行所有任务的所有 QA 场景。测试集成。保存到 `.sisyphus/evidence/final-qa/`。
  输出：`Scenarios [N/N pass] | VERDICT`

- [ ] F4. **范围保真度检查** — `deep`
  逐任务对比「做什么」和实际 diff。验证 1:1 — 全部构建且无范围蔓延。检查「不得做」合规性。
  输出：`Tasks [N/N compliant] | VERDICT`

---

## Commit Strategy

- **Wave 1**：`feat(proxy): 新增透传基础设施（路径规范化 + 模型提取 + 路由 + handler 骨架）` — `proxy.py`
- **Wave 2**：`feat(proxy): 实现非流式和流式 SSE 透传转发` — `proxy.py`
- **Wave 3**：`test(proxy): 新增透传端点测试` — `test/test_proxy_pass_through.py`；`docs(proxy): 更新路由文档` — `AGENTS.md`

---

## Success Criteria

### 验证命令

```bash
# 非流式透传
curl -s -X POST http://127.0.0.1:48743/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"hello"}],"max_tokens":5}'
# 预期：返回包含 choices 的 JSON

# 流式透传
curl -s -N -X POST http://127.0.0.1:48743/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"hello"}],"stream":true,"max_tokens":5}'
# 预期：SSE 事件流，以 data: 开头

# 路由优先级（现有端点不受影响）
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:48743/v1/responses
# 预期：426

# 日志记录
sqlite3 data/access_log.db "SELECT stage FROM debug_log ORDER BY rowid DESC LIMIT 2"
# 预期：raw_request, upstream_response

# 全部测试
python3 -m pytest test/ -q
# 预期：ALL PASSED
```

### 最终清单

- [ ] Catch-all 路由命中 `/v1/*` 且不拦截现有精确路由
- [ ] POST/GET 请求原样透传，不做格式转换
- [ ] 路径被规范化（无遍历、无双斜杠）
- [ ] 流式 SSE 逐 chunk 中继，无 Codex 事件注入
- [ ] debug_log 记录 raw_request + upstream_response
- [ ] token_stats 尽力而为记录
- [ ] 全部测试通过
- [ ] proxy.py 现有逻辑未被修改

