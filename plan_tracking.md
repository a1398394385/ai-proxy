# Plan Tracking: Codex SSE 格式对齐 实现进度跟踪

> 基于 `docs/superpowers/plans/2026-04-26-codex-sse-format-alignment.md` 实施计划生成的进度跟踪文档。

## Goal

修复 fact-store-browser proxy 的 SSE 流式响应格式，使所有事件的 data JSON 都包含 `"type"` 字段，与 Codex CLI 的 `ResponsesStreamEvent` 解析完全兼容，解决 `stream closed before response.completed` 错误。

## Current Task

Task 3 (Not Started)

## Tasks

### Task 1: 新增 `_format_sse_event` 辅助函数 + 单元测试

- [x] Step 0: 补充 `import json` 到 test/test_transform.py
- [x] Step 1: 写 7 个 `_format_sse_event` 单元测试
- [x] Step 2: 运行测试，验证全部失败
- [x] Step 3: 实现 `_format_sse_event` 函数
- [x] Step 4: 运行测试，验证全部通过
- [x] Step 5: 运行全量测试，确认无回归
- [x] Step 6: Commit
- **Status:** done

### Task 2: 修改所有 SSE 事件生成点，使用 `_format_sse_event`

- [x] Step 1: `_emit_created` 元组拼接 (line ~435)
- [x] Step 2: `output_item.added` reasoning
- [x] Step 3: `reasoning_summary_text.delta`
- [x] Step 4: `output_item.added` message
- [x] Step 5: `output_text.delta`
- [x] Step 6: `reasoning_summary_text.done`
- [x] Step 7: `output_item.done` reasoning
- [x] Step 8: `output_text.done`
- [x] Step 9: `output_item.done` message
- [x] Step 10: `output_item.done` function_call
- [x] Step 11: `response.incomplete`（含 `"response"` 包裹）
- [x] Step 12: 运行全量测试
- [x] Step 13: Commit
- **Status:** done

### Task 3: 新增事件格式集成测试 + 快照测试

- [ ] Step 1: 集成测试 — 逐事件验证 `"type"` 字段（3 个测试）
- [ ] Step 2: 快照测试 — 完整 SSE 格式对照（2 个测试）
- [ ] Step 3: 运行所有新增测试
- [ ] Step 4: 运行全量测试
- [ ] Step 5: Commit
- **Status:** not_started

### Task 4: Codex CLI 端对端验证

- [ ] Step 1: 备份 codex 配置
- [ ] Step 2: 切换 codex 配置到 proxy
- [ ] Step 3: 启动 proxy
- [ ] Step 4: codex CLI 发送简单请求
- [ ] Step 5: 验证不再报错
- [ ] Step 6: 恢复原始配置
- [ ] Step 7: Commit (如有)
- **Status:** not_started

## 之前的工作（Request Logger）

1. Request Logger 模块已完成全部 6 个 Task（Task 0-5），97 个测试通过。
2. 测试文件已统一移至 `test/` 目录。
3. 测试文件：`test/test_request_logger.py`、`test/test_proxy_logger_integration.py`、`test/test_e2e_smoke.py`、`test/test_proxy_config.py`、`test/test_transform.py`。

## 之前的工作（Codex Proxy 基础）

1. Codex Proxy 已完成全部 10 个 Task（Task 0 环境准备 + Task 1-9 实施），`proxy.py` 和 `transform.py` 均已上线运行。
2. `_emit_created` 和 `_emit_completed` 的格式已修复，但 `_process_delta` 和 `_emit_completion` 中仍有 10+ 个事件缺失 `"type"` 字段。
3. 根因：Codex 的 `ResponsesStreamEvent` 要求所有事件 data JSON 必须有 `"type"` 字段。

## Decisions Made

| Decision | Rationale | Source |
|----------|-----------|--------|
| 新增 `_format_sse_event` 统一辅助函数 | 所有事件统一格式化，避免遗漏 | 设计文稿 |
| 使用 `{"type": event_type, **data}` 注入 | event_type 覆盖 data 中已有的 "type"，保证一致 | 设计文稿 |
| 统一 `separators=(',', ':')` 紧凑格式 | 与已有修复的事件格式一致 | 设计文稿 |
| `response.incomplete` 使用 `"response"` 包裹 | Codex 从 `response.incomplete_details.reason` 读取 | 设计文稿 |
| `chat_to_responses` 无需修改 | 非流式响应，Codex 直接解析 JSON 响应体 | 设计文稿 |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| | | |

## Notes

- 设计文稿：`docs/superpowers/specs/2026-04-26-codex-sse-format-alignment-design.md`
- 实施计划：`docs/superpowers/plans/2026-04-26-codex-sse-format-alignment.md`
- 共 4 个 Task，每个 Task 包含多个 Step（TDD 循环：写测试 → 验证失败 → 实现 → 验证通过 → Commit）
- Task 2 改动最敏感，需要确保不影响现有 proxy 转发逻辑
- Task 4 涉及外部工具（Codex CLI），需要在实现完成后手动执行
- 纯 Python 标准库实现，无外部依赖，与 Codex Proxy 项目约束一致
