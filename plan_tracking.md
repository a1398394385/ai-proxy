# Plan Tracking: Token 统计抽取 实现进度跟踪

> 基于 `docs/superpowers/plans/2026-04-27-token-stats-extraction.md` 实施计划生成的进度跟踪文档。

## Goal

将 token 统计逻辑从 proxy.py 抽取为独立 `token_stats.py` 模块，统一处理 Anthropic / OpenAI Chat / OpenAI Responses 三种 usage 格式，包括 qwen 通过 LiteLLM 返回的 Anthropic 格式 cache 字段。

## Current Task

None (All Done)

## Tasks

### Task 1: 创建 `token_stats.py` — 核心函数

- [x] Step 1: 写 `_find_first` 辅助函数和 `record_token_stats` 函数（含幂等建表）
- [x] Step 2: 验证模块可导入
- [x] Step 3: Commit
- **Status:** done

### Task 2: 新增 `test/test_token_stats.py` — 16 个单元测试

- [x] Step 1: 写测试代码（_find_first × 5 + _extract_tokens × 6 + record_token_stats × 5）
- [x] Step 2: 运行测试，验证全部通过（16/16）
- [x] Step 3: Commit
- **Status:** done

### Task 3: 重构 `proxy.py` — 替换两处 token 统计调用点

- [x] Step 1: 添加 `from token_stats import record_token_stats` import
- [x] Step 2: 修改非流式路径（替换内联格式提取）
- [x] Step 3: 修改流式路径（替换内联格式提取）
- [x] Step 4: 运行全量测试（127/127）
- [x] Step 5: Commit
- **Status:** done

### Task 4: 重构 `transform.py` — `_emit_completion` usage 透传

- [x] Step 1: 简化 usage 构建为透传原始字段，移除 Anthropic cache 适配
- [x] Step 2: 运行全量测试（127/127）
- [x] Step 3: Commit
- **Status:** done

### Task 5: 确认集成测试兼容性

- [x] Step 1: 确认 mock SSE 路径 — 需新增 token_stats.DB_PATH patch
- [x] Step 2: 运行集成测试确认（127/127）
- [x] Step 3: Commit
- **Status:** done

### Task 6: 最终验证

- [x] Step 1: 运行全量测试（127/127）
- [x] Step 2: 重启 proxy（`./server.sh restart`）
- [x] Step 3: 冒烟测试 — proxy 启动正常
- [x] Step 4: 检查 token_stats 数据库写入正确（非零值）
- **Status:** done

## 之前的工作（SSE 格式对齐）

1. Task 1-4 全部完成，6 个 commit，108+ tests passing。
2. `_format_sse_event` 辅助函数 + 12 个 SSE 事件生成点统一 + 集成测试 + Codex CLI 验证通过。
3. 后续审阅修复：response.completed 统一、metadata headers 包裹、MockStream 重构、proxy.py 硬编码 SSE 修复。
4. 工具格式转换修复：`_map_tools` 将 Responses API 工具格式转为 Chat Completions 格式，丢弃非 function 类型工具。
5. stream_options 修复：流式请求添加 `include_usage: true` 使上游返回 token 统计。

## 之前的工作（Request Logger）

1. Request Logger 模块已完成全部 6 个 Task（Task 0-5），97 个测试通过。
2. 测试文件已统一移至 `test/` 目录。

## 之前的工作（Codex Proxy 基础）

1. Codex Proxy 已完成全部 10 个 Task，`proxy.py` 和 `transform.py` 均已上线运行。

## Decisions Made

| Decision | Rationale | Source |
|----------|-----------|--------|
| `_find_first` 用 `k in usage` 判断存在性，不用 `v > 0` | 0 是合法业务值（cache 未命中），不应被跳过 | 设计文稿/审阅 |
| `_extract_tokens` 中 cache_* 用 if/elif 链 | 嵌套路径需展开；if/elif 更清晰表达优先级 | 设计文稿/审阅 |
| `record_token_stats` 内建 `CREATE TABLE IF NOT EXISTS` | 不依赖 request_logger 初始化顺序 | 审阅 |
| `sqlite3.connect` 不加 `check_same_thread` | 每次调用新建连接，天然线程安全 | 审阅 |
| `request_logger.log_token_stats()` 保留但不再调用 | 向后兼容 | 设计文稿 |
| DB_PATH 硬编码到项目根目录 | 与 request_logger.py 同一路径约定 | 设计文稿 |
| context 缺字段用默认值，仅 request_id 缺失时跳过 | 统计容错性优先于完整性 | 设计文稿 |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| | | |

## Notes

- 设计文稿：`docs/superpowers/specs/2026-04-26-token-stats-extraction-design.md`
- 实施计划：`docs/superpowers/plans/2026-04-27-token-stats-extraction.md`
- 共 6 个 Task，每个 Task 包含 3-5 个 Step（TDD 循环）
- Task 1-2 为新文件创建，Task 3-4 为现有文件重构
- Task 5 检查集成测试兼容性（可能无需修改）
- Task 6 为最终验证，包含冒烟测试（依赖上游网络）
- 纯 Python 标准库实现，无外部依赖
- **注意**：`token_stats.py` 必须放在项目根目录（与 `data/` 同级），`DB_PATH` 基于 `__file__` 计算
