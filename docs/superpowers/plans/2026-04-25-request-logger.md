# Request Logger 实施计划

> 基于 `docs/superpowers/specs/2026-04-25-request-logger-design.md` 设计文稿生成。

## Goal

在 fact-store-browser proxy 中新增 `request_logger.py` 模块，记录四个关键阶段的请求/响应数据到独立 SQLite 数据库，并添加 token 统计表。

## Approach

TDD 循环：写测试 → 验证失败 → 实现 → 验证通过 → Commit

每个 task 开始/结束时更新 `plan_tracking.md`，完成后通知用户审阅。

## Tasks

### Task 0: request_logger.py 骨架 + DB 初始化

- [ ] Step 1: 创建 `data/` 目录
- [ ] Step 2: 创建 `request_logger.py` 骨架（import + `RequestLogger` class + `_get_conn` + `init_logger`/`get_logger` 全局函数）
- [ ] Step 3: 创建 `test_request_logger.py` 测试骨架（DB 初始化 + 表创建验证）
- [ ] Step 4: 运行测试验证骨架失败
- [ ] Step 5: 实现 `__init__`（创建 data/ 目录、建两张表、WAL 模式）
- [ ] Step 6: 运行测试验证通过
- [ ] Step 7: Commit

### Task 1: 5 个写入函数实现

- [ ] Step 1: 写出测试（5 个写入函数各一条记录验证：`log_raw_request`、`log_converted_request`、`log_upstream_response`、`log_converted_response`、`log_token_stats`）
- [ ] Step 2: 运行测试验证失败
- [ ] Step 3: 实现 5 个写入函数（短连接方案，try/except 静默降级）
- [ ] Step 4: 运行测试验证通过
- [ ] Step 5: Commit

### Task 2: 清理策略 + 辅助函数

- [ ] Step 1: 写出测试（`_cleanup_expired` 清理 debug_log，token_stats 不受影响；`_extract_agent` User-Agent 提取）
- [ ] Step 2: 运行测试验证失败
- [ ] Step 3: 实现 `_cleanup_expired`（datetime('now', 'localtime', '-N days')）
- [ ] Step 4: 实现 `_extract_agent` 和 `_generate_request_id`
- [ ] Step 5: 运行测试验证通过
- [ ] Step 6: Commit

### Task 3: proxy.py 集成点

- [ ] Step 1: 修改 `proxy.py` — `_handle_responses` 生成 request_id、记录 raw_request + converted_request
- [ ] Step 2: 修改 `proxy.py` — `_forward_non_streaming` 接收 request_id 参数、记录 upstream_response + converted_response + token_stats
- [ ] Step 3: 修改 `proxy.py` — `_forward_streaming` 接收 request_id 参数、记录 upstream_response + converted_response 跳过标记 + token_stats
- [ ] Step 4: 添加 `request_logger` import + 启动时 `init_logger` 调用
- [ ] Step 5: 冒烟测试验证 proxy 启动正常
- [ ] Step 6: Commit

### Task 4: 集成冒烟测试

- [ ] Step 1: 创建 `test_proxy_logger_integration.py`（使用 importlib 动态加载 proxy 模块）
- [ ] Step 2: 模拟完整请求流程 → 检查 DB 中有 4 条 debug_log + 1 条 token_stats
- [ ] Step 3: JSON 解析失败场景 → 仍有一条 raw_request 错误记录
- [ ] Step 4: 上游 500 错误场景 → 有 raw_request + converted_request + upstream_response，无 converted_response
- [ ] Step 5: 运行所有测试验证通过
- [ ] Step 6: Commit

### Task 5: proxy_config.yaml 配置更新 + 端对端验证

- [ ] Step 1: 更新 `proxy_config.yaml` 添加 `logging` 块
- [ ] Step 2: 端对端冒烟测试（启动 proxy → 发请求 → 检查 access_log.db 中有记录）
- [ ] Step 3: 验证旧配置向后兼容（无 logging 块也能启动）
- [ ] Step 4: Commit
