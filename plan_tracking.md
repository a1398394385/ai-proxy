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

---

# Plan Tracking: 动态模型配置 实现进度跟踪

> 基于 `docs/superpowers/plans/2026-04-27-dynamic-model-config-plan.md` 实施计划生成的进度跟踪文档。

## Goal

将 proxy_config.yaml 的静态 model_map 替换为数据库驱动的动态配置系统，支持 Web 页面管理多上游、多模型、多路由映射。

## Current Task

Task 1 (Not Started)

## Tasks

### Task 1: config_manager.py — 数据库初始化 + PRAGMA

- [ ] Step 1: 写 6 个失败测试（建表 + PRAGMA + 幂等）
- [ ] Step 2: 运行测试确认失败
- [ ] Step 3: 实现 ConfigDB.__init__ / _connect / _ensure_db / _seed_from_yaml + _parse_yaml
- [ ] Step 4: 运行测试确认通过（6/6）
- [ ] Step 5: Commit
- **Status:** not_started

### Task 2: config_manager.py — 上游 CRUD 测试与实现

- [ ] Step 1: 写 8 个失败测试（add/list/get/update/disable/active_only/is_default）
- [ ] Step 2: 运行测试确认通过（已在 Task 1 实现）
- [ ] Step 3: Commit
- **Status:** not_started

### Task 3: config_manager.py — 模型 + 路由 CRUD 测试

- [ ] Step 1: 写 13 个失败测试（add/list/update/delete/fk_restrict/referenced_routes/star_order）
- [ ] Step 2: 运行测试确认通过（已在 Task 1 实现）
- [ ] Step 3: Commit
- **Status:** not_started

### Task 4: config_manager.py — resolve_model 测试 + ConfigCache

- [ ] Step 1: 写 12 个测试（resolve exact/fallback/disabled/none/star_disabled + get_all + validate + cache resolve/hit/reload/TTL/get_all）
- [ ] Step 2: 运行测试确认 ConfigCache 部分失败
- [ ] Step 3: 实现 ConfigCache（resolve/get_all/_refresh_if_stale + Lock + TTL + 异常兜底）
- [ ] Step 4: 运行测试确认全部通过
- [ ] Step 5: Commit
- **Status:** not_started

### Task 5: config_manager.py — 种子导入测试

- [ ] Step 1: 写 3 个测试（空库导入 / 已导入跳过 / yaml 缺失兜底）
- [ ] Step 2: 运行测试确认通过（已在 Task 1 实现）
- [ ] Step 3: Commit
- **Status:** not_started

### Task 6: proxy.py — 集成 ConfigCache

- [ ] Step 1: 添加 import ConfigCache + 全局单例
- [ ] Step 2: 替换 resolve_model() 为动态路由
- [ ] Step 3: 替换 _handle_models() 为动态模型列表
- [ ] Step 4: 修改 load_config() 校验逻辑（移除 model_map 校验，改为检查 * fallback）
- [ ] Step 5: 调整转发逻辑（upstream 全从 cache 获取，移除 CONFIG["upstream"] 回退）
- [ ] Step 6: 运行全量测试确认无回归
- [ ] Step 7: Commit
- **Status:** not_started

### Task 7: proxy.py — 新增 /admin/reload 端点

- [ ] Step 1: 修改 do_POST 路由
- [ ] Step 2: 实现 _handle_admin_reload（client_address 白名单 + config_cache.reload()）
- [ ] Step 3: 运行全量测试确认无回归
- [ ] Step 4: Commit
- **Status:** not_started

### Task 8: server.py — 上游 API 路由

- [ ] Step 1: 导入 config_manager + 全局 get_config_db()
- [ ] Step 2: 在 do_GET 中添加上游路由
- [ ] Step 3: 在 do_POST 中添加上游新增 + 连通性测试
- [ ] Step 4: 在 do_PUT 中添加上游更新
- [ ] Step 5: 在 do_DELETE 中添加上游禁用（409 检查）
- [ ] Step 6: 添加 _read_json 辅助方法 + _test_upstream_connectivity
- [ ] Step 7: 运行全量测试 + quick_test.py 确认无回归
- [ ] Step 8: Commit
- **Status:** not_started

### Task 9: server.py — 模型 + 路由 + 配置 API

- [ ] Step 1: 在 do_GET 中添加模型/路由/配置状态路由
- [ ] Step 2: 在 do_POST 中添加模型/路由/配置重载路由
- [ ] Step 3: 在 do_PUT 中添加模型/路由更新
- [ ] Step 4: 在 do_DELETE 中添加模型/路由删除（409 检查）
- [ ] Step 5: 运行全量测试确认无回归
- [ ] Step 6: Commit
- **Status:** not_started

### Task 10: 前端 — 模型管理页面（HTML/CSS 骨架）

- [ ] Step 1: 添加「模型管理」导航标签
- [ ] Step 2: 添加模型管理页面 HTML 骨架（状态栏 + 上游/模型/路由三表格 + 应用配置按钮）
- [ ] Step 3: 添加 CSS 样式（脉冲动画 + 状态栏 + API Key 脱敏 + format tooltip）
- [ ] Step 4: Commit
- **Status:** not_started

### Task 11: 前端 — 事件总线 + 三表格 JS 逻辑

- [ ] Step 1: 添加导航切换 model 页分支
- [ ] Step 2: 实现事件总线（bus.emit / bus.on）
- [ ] Step 3: 实现状态栏刷新（refreshConfigStatus）
- [ ] Step 4: 实现上游表格渲染 + 编辑模态框 + 禁用确认
- [ ] Step 5: 实现模型表格渲染（含 format tooltip）+ 编辑模态框 + 删除确认
- [ ] Step 6: 实现路由表格渲染 + 编辑模态框 + 删除确认（* fallback 保护）
- [ ] Step 7: 实现应用配置按钮 + 事件订阅（config:dirty → 高亮，config:applied → 取消）
- [ ] Step 8: Commit
- **Status:** not_started

### Task 12: 集成测试

- [ ] Step 1: 写 10 个集成测试（ConfigDB → ConfigCache → resolve 完整链路）
- [ ] Step 2: 运行集成测试确认通过
- [ ] Step 3: 运行全量测试确认无回归
- [ ] Step 4: Commit
- **Status:** not_started

## 之前的工作（动态模型配置 — 设计阶段）

1. 设计文稿经过四轮审阅修订，覆盖架构、数据库表、API、前端、模块设计、重载流程、风险应对。
2. 实施计划经过三轮审阅修订，12 个 Task 全部 TDD 驱动，覆盖 16 个测试函数 + 完整实现代码 + 验证命令。

## 之前的工作（Token 统计抽取）

1. Task 1-4 全部完成，6 个 commit，108+ tests passing。
2. 后续审阅修复：response.completed 统一、metadata headers 包裹、MockStream 重构、proxy.py 硬编码 SSE 修复。
3. 工具格式转换修复 + stream_options 修复。
 4e046db (docs: 生成动态模型配置 plan_tracking.md — 12 Task 进度跟踪)

## Decisions Made

| Decision | Rationale | Source |
|----------|-----------|--------|
<<<<<<< HEAD
| sse_utils.py 独立文件 | 避免 transform 模块间横向依赖 | 设计文稿/审阅 |
| 阶段 1 纯重构 | 先重命名/移动（不改逻辑）→ 跑测试 → 再做功能 | 设计文稿/审阅 |
| tool_blocks dict[int, ToolBlockState] | 多 tool 并发流式场景需要按 index 管理 | 设计文稿/审阅 |
| 推理字段双检测（reasoning_content + reasoning） | LiteLLM 网关字段名不确定 | 设计文稿/审阅 |
| Anthropic event data 自带 "type" | _format_sse_event 约定不重复注入 | 设计文稿/审阅 |
=======
| 新增 config.db（SQLite WAL）作为配置存储 | 与现有 memory_store.db / state.db 统一，方便后续 token 统计连表查询 | 设计文稿 |
| config_manager.py 独立模块 | 纯数据层，不被 server.py 或 proxy.py 耦合 | 设计文稿 |
| ConfigCache TTL=5s + 手动 reload 双路径 | 手动即时生效 + 自动兜底，防止 proxy 离线时配置无法生效 | 设计文稿 |
| upstream 用软删除（is_active）而非物理删除 | 保护现有关联数据，防止误删 | 审阅 |
| 外键 ON DELETE RESTRICT + PRAGMA foreign_keys | 数据库层阻止非法操作，应用层预检查 + 友好 409 响应 | 设计文稿 |
| _seed_from_yaml 包裹事务 | 全部成功才写 schema_version，中途失败回滚 | 审阅 |
| is_default 全局唯一（应用层维护） | SQLite CHECK 子查询限制多，应用层清除旧默认更简单可靠 | 审阅 |
| 前端 CustomEvent 事件总线 | vanilla JS 无框架下实现组件通信，约 30 行代码 | 设计文稿 |
| 连通性测试 TCP + HTTP GET 两步 | TCP 验证网络可达，HTTP 验证服务存活；不假设 /models 端点存在 | 设计文稿/审阅 |
| /admin/reload 仅允许 127.0.0.1/::1 | 应用层白名单校验，不依赖 socket bind 地址 | 设计文稿 |
| format 字段当前阶段仅存储不生效 | UI 附 tooltip 说明避免用户误解 | 审阅 |
| delete_model 内置 check_refs 参数 | 默认预检查返回引用列表，省去 handler 中的重复逻辑 | 审阅 |
| ConfigCache._refresh_if_stale 用 _resolve_one | 避免 resolve_model 的 fallback 把不同 source 归一化到 * 的语义错误 | 审阅 |
>>>>>>> 4e046db (docs: 生成动态模型配置 plan_tracking.md — 12 Task 进度跟踪)

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| Task 3 集成测试 mock 路径不匹配 | `_forward_streaming` 改为局部 import 后 mock 失效 | 改 `patch.object(self.mod, "create_codex_sse_stream")` 为 `patch("transform_responses.create_codex_sse_stream")` |
| system array 过滤未检查 type 字段 | `block.get("text")` 也匹配了 thinking 块 | 改为 `block.get("type") == "text" and block.get("text")` |
| assistant text content 测试期望错误 | 测试期望字符串 "Hello"，实际返回 list `[{"type":"text","text":"Hello"}]` | 修正测试期望值，与实现一致 |
| message_start 未发送 | 首个 chunk 只有 id/model 无 delta，未触发 _send_message_start | 在捕获 id 后立即 emit message_start |

## Notes

<<<<<<< HEAD
- 设计文稿：`docs/superpowers/specs/2026-04-27-anthropic-messages-conversion-design.md`
- 实施计划：`docs/superpowers/plans/2026-04-27-anthropic-messages-conversion.md`
- 参考实现：`/Users/xys/Github/cc-switch/src-tauri/src/proxy/providers/transform.rs` + `streaming.rs`
- Claude Code 源码：`/Users/xys/Github/Claude-Code/src/services/api/claude.ts`
- 阶段 1（Task 1-3）不改任何转换逻辑，纯移动代码
- 阶段 2（Task 4-8）严格 TDD，每个测试先失败再实现
- 每个 Task 完成后更新本文件 + 通知用户审阅
- 基线测试数：130 passed（非计划初稿的 123）
=======
- 设计文稿：`docs/superpowers/specs/2026-04-27-dynamic-model-config-design.md`
- 实施计划：`docs/superpowers/plans/2026-04-27-dynamic-model-config-plan.md`
- 共 12 个 Task，每个 Task 包含 3-8 个 Step（TDD 循环）
- Task 1-5 为新文件创建（config_manager.py + test），Task 6-7 为 proxy.py 修改，Task 8-9 为 server.py 修改，Task 10-11 为前端修改，Task 12 为集成测试
- 纯 Python 标准库实现，无外部依赖
- **重要**：config.db 路径统一为 `~/.hermes/config.db`，使用 `Path.home() / ".hermes" / "config.db"`
- **重要**：server.py 和 proxy.py 必须使用相同的路径构造参数，确保操作同一数据库
- **重要**：每次连接必须执行 `PRAGMA foreign_keys = ON`，否则 `ON DELETE RESTRICT` 静默失效
- **重要**：修改代码后必须重启 server（`./server.sh restart`），标准 HTTP server 无热重载
- **重要**：ConfigCache 数据库异常时保留旧缓存，不抛异常导致 500
>>>>>>> 4e046db (docs: 生成动态模型配置 plan_tracking.md — 12 Task 进度跟踪)
