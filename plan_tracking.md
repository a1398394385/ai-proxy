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
- [ ] Step 1: 从 transform.py 复制全部内容到 transform_responses.py
- [ ] Step 2: 修改 proxy.py 的 import（从 transform_responses 导入）
- [ ] Step 3: 修改 test/test_transform.py 的 import
- [ ] Step 4: 重写 transform.py 为选择器（re-export）
- [ ] Step 5: 运行全量测试确认（139 passed）
- [ ] Step 6: Commit
- **Status:** pending

### Task 3: proxy.py 转发函数参数化
- [ ] Step 1: _forward_non_streaming 增加 response_converter 参数
- [ ] Step 2: _forward_streaming 增加 response_converter + sse_stream_factory 参数
- [ ] Step 3: 更新 _handle_responses 中的两处调用
- [ ] Step 4: 更新集成测试中的 mock 调用
- [ ] Step 5: 运行全量测试确认（139 passed）
- [ ] Step 6: Commit
- **Status:** pending

### Task 4: anthropic_to_chat — 请求转换 TDD
- [ ] Step 1-28: 23 个测试用例，逐个 TDD 循环
- [ ] Step 29: 运行全量测试确认
- [ ] Step 30: Commit
- **Status:** pending

### Task 5: chat_to_anthropic — 响应转换 TDD
- [ ] Step 1-11: 11 个测试用例，逐个 TDD 循环
- [ ] Step 12: 运行全量测试确认
- [ ] Step 13: Commit
- **Status:** pending

### Task 6: create_anthropic_sse_stream — 流式转换 TDD
- [ ] Step 1-14: 14 个测试用例，逐个 TDD 循环
- [ ] Step 15: 运行全量测试确认
- [ ] Step 16: Commit
- **Status:** pending

### Task 7: proxy.py /v1/messages 路由集成
- [ ] Step 1: 新增 _handle_messages 方法
- [ ] Step 2: do_POST 添加 /v1/messages 路由
- [ ] Step 3: _extract_agent 增加 claude 检测
- [ ] Step 4: 新增集成测试
- [ ] Step 5: 运行全量测试确认
- [ ] Step 6: Commit
- **Status:** pending

### Task 8: 最终验证
- [ ] Step 1: 运行全量测试
- [ ] Step 2: 重启 proxy（./server.sh restart）
- [ ] Step 3: 冒烟测试
- [ ] Step 4: Commit（如有修改）
- **Status:** pending

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
| | | |

## Notes

- 设计文稿：`docs/superpowers/specs/2026-04-27-anthropic-messages-conversion-design.md`
- 实施计划：`docs/superpowers/plans/2026-04-27-anthropic-messages-conversion.md`
- 参考实现：`/Users/xys/Github/cc-switch/src-tauri/src/proxy/providers/transform.rs` + `streaming.rs`
- Claude Code 源码：`/Users/xys/Github/Claude-Code/src/services/api/claude.ts`
- 阶段 1（Task 1-3）不改任何转换逻辑，纯移动代码
- 阶段 2（Task 4-8）严格 TDD，每个测试先失败再实现
- 每个 Task 完成后更新本文件 + 通知用户审阅
- 基线测试数：130 passed（非计划初稿的 123）
