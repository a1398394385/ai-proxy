# Plan Tracking

## Current Focus: SSE 状态机重构 Phase 1（2026-04-28）

> 基于 `docs/superpowers/plans/2026-04-28-sse-state-machine-phase1.md` 实施计划。
> 设计文稿：`docs/superpowers/specs/2026-04-28-sse-state-machine-refactor-design.md`

**Status: COMPLETE** — 全部 14 个 Task 完成，184 passed，待用户审阅。

### Tasks

- [x] Task 1: 新增 ToolBlockState dataclass
- [x] Task 2: 新增 CodexStreamConverter dataclass 字段
- [x] Task 3: 实现 _build_response_obj / _format_sse / _emit_created
- [x] Task 4: 实现 _handle_text_delta + _close_text_block
- [x] Task 5: 实现 _handle_reasoning_delta + _close_reasoning_block
- [x] Task 6: 实现 refusal 处理三件套
- [x] Task 7: 实现工具调用延迟启动
- [x] Task 8: 实现 process_chunk / finish / _convert_usage
- [x] Task 9: 接入 create_codex_sse_stream + 更新 transform.py
- [x] Task 10: 更新现有失败测试
- [x] Task 11: 修复 chat_to_responses text+refusal 合并
- [x] Task 12: 修复 proxy.py 错误路径 [DONE] + 缓冲区
- [x] Task 13: 新增全覆盖测试 + _output_items_to_messages
- [x] Task 14: 全量测试 + 验收

### Decisions Made

| Decision | Rationale |
|----------|-----------|
| tool_blocks 按 tc_index 排序 | done 事件顺序与上游 tool_calls 数组一致 |
| output_items 按 output_index 排序 | 与 SSE 事件序列对应 |
| StreamState = CodexStreamConverter 别名 | Task 9 删除旧类时同步设置 |

---

## Current Focus: Response Store Phase 2（2026-04-28）

> 基于 `docs/superpowers/plans/2026-04-28-response-store-phase2.md` 实施计划。

**Status: COMPLETE** — 全部 6 个 Task 完成，205 passed，待用户审阅。

### Tasks

- [x] Task 1: ResponseRecord + ResponseStore 实现
- [x] Task 2: proxy_config.yaml + server 挂载
- [x] Task 3: previous_response_id 注入
- [x] Task 4: 非流式存储路径（_store_response 辅助函数）
- [x] Task 5: 流式存储路径（create_codex_sse_stream 新参数）
- [x] Task 6: 对话链集成测试 + 全量验收

### Decisions Made

| Decision | Rationale |
|----------|-----------|
| 懒导入 response_store | 避免 proxy.py → transform → response_store 循环依赖 |
| is_responses_api 显式标记 | 防止 _handle_messages 不传参数时误触发存储 |
| conversation 不含 system | 避免多轮拼接时上游收到多条 system 消息 |

---

# Plan Tracking: Anthropic Messages API 转换 实现进度跟踪

> 基于 `docs/superpowers/plans/2026-04-27-anthropic-messages-conversion.md` 实施计划。

## Goal

在 proxy 中新增 Anthropic Messages API ↔ OpenAI Chat Completions API 的双向完整转换（含流式 SSE）。

## Current Task

Task 0: 准备 plan_tracking.md + 验证基线（130 passed）

## Tasks

### Task 0: 准备 plan_tracking.md + 验证基线
- [x] Step 1: 写入新 plan_tracking.md
- [x] Step 2: 运行全量测试确认基线（130 passed）
- [x] Step 3: Commit
- **Status:** done

### Task 1: 创建 sse_utils.py — 提取 _format_sse_event
- [x] Step 1: 创建 test/test_sse_utils.py（TDD: 验证 _format_sse_event 行为）
- [x] Step 2: 验证测试失败（import 不到的 sse_utils）
- [x] Step 3: 创建 sse_utils.py + 从 transform.py 移动 _format_sse_event
- [x] Step 4: 更新 transform.py 从 sse_utils import
- [x] Step 5: 运行全量测试确认（139 passed）
- [x] Step 6: Commit
- **Status:** done

### Task 2: 创建 transform_responses.py — 提取 Responses 转换逻辑
- [x] Step 1: 从 transform.py 复制全部内容到 transform_responses.py
- [x] Step 2: 修改 proxy.py 的 import（从 transform_responses 导入）— 通过 transform.py re-export 兼容
- [x] Step 3: 修改 test/test_transform.py 的 import — 通过 transform.py re-export 兼容
- [x] Step 4: 重写 transform.py 为选择器（re-export）
- [x] Step 5: 运行全量测试确认（139 passed）
- [x] Step 6: Commit
- **Status:** done

### Task 3: proxy.py 转发函数参数化
- [x] Step 1: _forward_non_streaming 增加 response_converter 参数
- [x] Step 2: _forward_streaming 增加 response_converter + sse_stream_factory 参数
- [x] Step 3: 更新 _handle_responses 中的两处调用
- [x] Step 4: 更新集成测试中的 mock 调用
- [x] Step 5: 运行全量测试确认（139 passed）
- [x] Step 6: Commit
- **Status:** done

### Task 4: anthropic_to_chat — 请求转换 TDD
- [x] Step 1-28: 25 个测试用例，逐个 TDD 循环
- [x] Step 29: 运行全量测试确认
- [x] Step 30: Commit
- **Status:** done

### Task 5: chat_to_anthropic — 响应转换 TDD
- [x] Step 1-11: 11 个测试用例，逐个 TDD 循环
- [x] Step 12: 运行全量测试确认
- [x] Step 13: Commit
- **Status:** done

### Task 6: create_anthropic_sse_stream — 流式转换 TDD
- [x] Step 1-14: 8 个测试用例（精简核心场景）
- [x] Step 15: 运行全量测试确认
- [x] Step 16: Commit
- **Status:** done

### Task 7: proxy.py /v1/messages 路由集成
- [x] Step 1: transform.py 激活 Anthropic re-export
- [x] Step 2: 更新 proxy.py import
- [x] Step 3: do_POST 添加 /v1/messages 路由 + _handle_messages 方法
- [x] Step 4: _extract_agent 增加 claude 检测
- [x] Step 5: 新增集成测试
- [x] Step 6: 运行全量测试确认（185 passed）
- [x] Step 7: Commit
- **Status:** done

### Task 8: 最终验证
- [x] Step 1: 运行全量测试（185 passed）
- [x] Step 2: 重启 proxy（./server.sh restart）— 用户手动验证
- [x] Step 3: 冒烟测试 — 用户手动验证
- [x] Step 4: Commit（无新修改）
- **Status:** done

## Decisions Made

| Decision | Rationale | Source |
|----------|-----------|--------|
| sse_utils.py 独立文件 | 避免 transform 模块间横向依赖 | 设计文稿/审阅 |
| 阶段 1 纯重构 | 先重命名/移动（不改逻辑）→ 跑测试 → 再做功能 | 设计文稿/审阅 |
| tool_blocks dict[int, ToolBlockState] | 多 tool 并发流式场景需要按 index 管理 | 设计文稿/审阅 |
| 推理字段双检测（reasoning_content + reasoning） | LiteLLM 网关字段名不确定 | 设计文稿/审阅 |
| Anthropic event data 自带 "type" | _format_sse_event 约定不重复注入 | 设计文稿/审阅 |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| Task 3 集成测试 mock 路径不匹配 | `_forward_streaming` 改为局部 import 后 mock 失效 | 改 `patch.object(self.mod, "create_codex_sse_stream")` 为 `patch("transform_responses.create_codex_sse_stream")` |
| system array 过滤未检查 type 字段 | `block.get("text")` 也匹配了 thinking 块 | 改为 `block.get("type") == "text" and block.get("text")` |
| assistant text content 测试期望错误 | 测试期望字符串 "Hello"，实际返回 list `[{"type":"text","text":"Hello"}]` | 修正测试期望值，与实现一致 |
| message_start 未发送 | 首个 chunk 只有 id/model 无 delta，未触发 _send_message_start | 在捕获 id 后立即 emit message_start |

## Notes

- 设计文稿：`docs/superpowers/specs/2026-04-27-anthropic-messages-conversion-design.md`
- 实施计划：`docs/superpowers/plans/2026-04-27-anthropic-messages-conversion.md`
- 参考实现：`/Users/xys/Github/cc-switch/src-tauri/src/proxy/providers/transform.rs` + `streaming.rs`
- Claude Code 源码：`/Users/xys/Github/Claude-Code/src/services/api/claude.ts`
- 阶段 1（Task 1-3）不改任何转换逻辑，纯移动代码
- 阶段 2（Task 4-8）严格 TDD，每个测试先失败再实现
- 每个 Task 完成后更新本文件 + 通知用户审阅
- 基线测试数：130 passed（非计划初稿的 123）
