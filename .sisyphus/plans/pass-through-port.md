# 透传独立端口 + 共享模块 + 配置化

## TL;DR

> **快速摘要**：将透传从 proxy.py 的 catch-all 路由中拆出为独立的 `pass_through.py` 服务（port 48744）；抽取共享代码到 `common.py`；把所有端口写入 `proxy_config.yaml` 的 `[ports]` 段统一管理。
> 
> **可交付物**：
> - `common.py` — 新建共享模块（ConfigCache、resolve_model、_create_upstream_conn 等）
> - `pass_through.py` — 新建独立透传代理（port 48744）
> - `proxy.py` — 删除所有透传代码，导入 common.py
> - `proxy_config.yaml` — 新增 `ports:` 段，迁移 `proxy.host/port`
> - `server.py` / `server.sh` — 端口配置化 + 三服务管理
> - 测试更新 — 所有引用旧端口/配置的测试
> 
> **预估工作量**：Large
> **并行执行**：YES — 4 waves
> **关键路径**：Task 1 → Task 2 → Task 6 → Task 9

---

## Context

### 原始需求

1. 透传不应该在 48743 端口上作为 catch-all — 需要独立端口 48744
2. 删除 proxy.py 中所有透传代码（catch-all 路由 + handler + forwarding methods）
3. 所有端口号放入配置文件 `proxy_config.yaml` 的 `[ports]` 段

### 关键决策

- **路径语义**：独立端口后客户端直接发目标路径，**不剥离 `/v1` 前缀**（与 catch-all 行为不同）
- **共享代码**：抽取到新建 `common.py`（避免循环导入）
- **日志数据库**：pass_through 和 proxy 共享同一 `data/access_log.db`（SQLite WAL 并发安全）
- **配置结构**：扁平 `ports:` 段，含 `data_browser`、`codex_proxy`、`pass_through`

### Metis 审查

**已识别的风险**（已处理）：
- **路径语义变更**：catch-all 剥 `/v1`，独立端口不剥 → `_normalize_forward_path()` 仅保留 `..` 拒绝和 `//` 规范化
- **测试破碎**：4 个测试文件硬编码端口/配置 → 集中在 Wave 3 修复
- **server.py 硬编码**：L86-87 端口 + L638/L826 连接 proxy → 读配置
- **循环导入**：pass_through.py 不能 import proxy.py → 抽取到 common.py
- **config 向后兼容**：`proxy.port` → `ports.codex_proxy` → 保留 fallback 读取

---

## Work Objectives

### 核心目标

将透传从 proxy.py 中分离为独立服务（port 48744），抽取共享基础设施到 common.py，实现所有端口配置化。

### 具体可交付物

- **`common.py`**：ConfigCache 实例、resolve_model、_create_upstream_conn、load_config、_normalize_forward_path、_extract_model_for_pass_through
- **`pass_through.py`**：独立透传服务器（ThreadedHTTPServer，port 48744）
- **`proxy.py`**：删除透传代码，导入 common.py，读取 ports 配置
- **`proxy_config.yaml`**：新增 `ports:` 段
- **`server.py`**：读取 ports.data_browser 配置
- **`server.sh`**：管理 3 个服务
- **测试更新**：test_proxy_pass_through.py、test_proxy_config.py、test_e2e_smoke.py、test_proxy_logger_integration.py

### 完成定义

- [ ] `curl -X POST http://127.0.0.1:48744/chat/completions` 原样透传到上游
- [ ] `curl -X POST http://127.0.0.1:48743/v1/chat/completions` 返回 404（不再被 catch-all 捕获）
- [ ] `curl http://127.0.0.1:48743/health` 正常返回
- [ ] `curl http://127.0.0.1:18742/api/config/status` 仍能检测 proxy 状态
- [ ] `./server.sh status` 显示 3 个服务状态
- [ ] `python3 -m pytest test/ -q` ≥ 348 passed

### 必须包含

- common.py 抽取共享代码
- pass_through.py 独立透传服务
- 删除 proxy.py 中所有透传代码
- ports 段配置化（3 个服务端口）
- server.py 和 server.sh 更新
- 所有测试文件更新

### 必须不包含

- 修改透传业务逻辑（模型提取、错误处理、SSE 转发、token_stats）
- 拆分 Responses/Anthropic 为独立端口
- 新增透传功能（速率限制、自定义 header 等）
- 修改上游配置逻辑（model_map、config.db）

---

## Execution Strategy

### 并行执行波次

```
Wave 1（共享模块抽取 — 基础层）：
├── Task 1: 创建 common.py（ConfigCache + resolve_model + _create_upstream_conn）
├── Task 2: 添加 load_config + _normalize_forward_path + _extract_model_for_pass_through 到 common.py
├── Task 3: 更新 proxy.py 导入 common.py
└── Task 4: proxy.py 删除透传代码（catch-all + handler + forwarding）

Wave 2（配置文件 + pass_through.py）：
├── Task 5: 更新 proxy_config.yaml（新增 ports 段）
├── Task 6: 创建 pass_through.py（独立透传服务器）
├── Task 7: 更新 server.py 读取 ports 配置
└── Task 8: 更新 server.sh 管理 3 个服务

Wave 3（测试修复 — 最大并行）：
├── Task 9: 更新 test/test_proxy_pass_through.py 导入
├── Task 10: 更新 test_proxy_config.py + test_e2e_smoke.py + test_proxy_logger_integration.py
└── Task 11: 端到端验证（curl 测试 3 个端口）

Wave FINAL：
├── Task F1: 计划合规审计 (oracle)
├── Task F2: 代码质量审查 (oracle)
├── Task F3: 手动 QA (general)
└── Task F4: 范围保真度 (oracle)
```

**关键路径**：Task 1 → Task 2 → Task 6 → Task 9 → Task 11
**并行加速**：~60%
**最大并发**：4（Wave 1）

---

## TODOs

- [x] 1. 创建 `common.py` — ConfigCache + resolve_model + _create_upstream_conn

  **做什么**：
  - 新建 `/Users/xys/Github/ai-agent-tools/common.py`
  - 从 `proxy.py` 移动以下代码到 common.py：
    - `CONFIG_DB_PATH` 常量和 `config_cache = ConfigCache(...)` 实例（行 46-47）
    - `resolve_model()` 函数（行 82-83）
    - `_create_upstream_conn()` 函数（行 157-178）
    - 相关 imports：`config_manager.ConfigCache`、`http.client`、`ssl`、`urllib.parse`、`Path`
  - 修复 CONFIG_DB_PATH bug：当前指向 `data/access_log.db`，应指向 `~/.hermes/config.db`

  **不得做**：不要移动 proxy.py 的 CONFIG、load_config、任何 handler 代码

  **类别**：`quick` | **阻塞**：Task 3, Task 4, Task 6

  **QA 场景**：
  ```
  场景：proxy.py 能正常 import common
    工具：Bash (python3)
    步骤：
       1. python3 -c "from common import config_cache, resolve_model, _create_upstream_conn; print('OK')"
    预期结果：输出 OK，无异常
    证据：.sisyphus/evidence/task-1-import.txt
  ```

  **提交**：`refactor: 抽取 common.py — ConfigCache + resolve_model + _create_upstream_conn`

- [x] 2. 添加 load_config + 路径/模型工具到 common.py

  **做什么**：
  - 将 `proxy.py` 的 `CONFIG`、`load_config()`、`_parse_yaml()`、`_yaml_scalar()` 移到 common.py
  - 将 `_normalize_forward_path()` 从 proxy.py 移到 common.py，**移除 /v1 剥离逻辑**（只保留 `..` 拒绝和 `//` 规范化）
  - 将 `_extract_model_for_pass_through()` 从 proxy.py 移到 common.py

  **不得做**：不要改变任何函数的业务逻辑（除了移除 /v1 剥离）

  **类别**：`quick` | **被阻塞**：Task 1

  **QA 场景**：
  ```
  场景：路径规范化不剥 /v1
    工具：Bash (python3)
    步骤：
       1. python3 -c "from common import _normalize_forward_path; print(_normalize_forward_path('/chat/completions'))"
    预期结果：/chat/completions（不变）
    证据：.sisyphus/evidence/task-2-norm.txt
  场景：.. 仍然被拒绝
    工具：Bash (python3)
    步骤：
       1. python3 -c "from common import _normalize_forward_path; print(_normalize_forward_path('/../../../etc/passwd'))"
    预期结果：None
    证据：.sisyphus/evidence/task-2-traversal.txt
  ```

  **提交**：`refactor: 将 load_config + 路径/模型工具移到 common.py`

- [x] 3. 更新 proxy.py 导入 common.py

  **做什么**：
  - 在 proxy.py 中，将已移到 common.py 的代码替换为 import：
    `from common import config_cache, resolve_model, _create_upstream_conn, CONFIG, load_config`
  - 删除 proxy.py 中已被移动的代码段
  - 保持 `proxy.py` 中所有 handler 方法不变

  **不得做**：不要改动 handler 方法、不要删除任何仍在 proxy.py 中使用的代码

  **类别**：`quick` | **被阻塞**：Task 1, Task 2

  **QA 场景**：
  ```
  场景：proxy.py 导入无误 + 测试通过
    工具：Bash (pytest)
    步骤：
       1. python3 -m pytest test/ -q --tb=short -k "not pass_through"
    预期结果：所有非透传测试通过
    证据：.sisyphus/evidence/task-3-tests.txt
  ```

  **提交**：`refactor(proxy): 导入 common.py`

- [x] 4. proxy.py 删除透传代码

  **做什么**：
  - 删除 do_GET 中的 `elif self.path.startswith("/v1/")` 行
  - 删除 do_POST 中的 `elif self.path.startswith("/v1/")` 行
  - 删除 `_handle_pass_through()` 方法
  - 删除 `_forward_pass_through_non_streaming()` 方法
  - 删除 `_forward_pass_through_streaming()` 方法
  - 验证 `/v1/chat/completions` 在 port 48743 上返回 404

  **不得做**：不要删除任何非透传的 handler 或方法

  **类别**：`quick` | **被阻塞**：Task 3

  **QA 场景**：
  ```
  场景：透传路由已移除
    工具：Bash (curl)
    步骤：
       1. curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:48743/v1/chat/completions -H "Content-Type: application/json" -d '{"model":"*"}'
    预期结果：404
    证据：.sisyphus/evidence/task-4-removed.txt
  场景：现有路由不受影响
    工具：Bash (curl)
    步骤：
       1. curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:48743/health
    预期结果：200
    证据：.sisyphus/evidence/task-4-health.txt
  ```

  **提交**：`refactor(proxy): 删除透传 catch-all 和 handler`

- [x] 5. 更新 `proxy_config.yaml` — 新增 `ports:` 段

  **做什么**：新增顶级 `ports:` 段，含 data_browser(18742)、codex_proxy(48743)、pass_through(48744) 的 host+port。保留旧 `proxy.host/port` 向后兼容。`common.py` 新增 `get_port(service)` 优先读 ports。

  **类别**：`quick` | **提交**：`feat(config): ports 段配置化`

- [x] 6. 创建 `pass_through.py` — 独立透传服务器（port 48744）

  **做什么**：新建 ThreadedHTTPServer，实现 PassThroughHandler（do_GET/do_POST 接受所有路径），import common.py 共享代码，复用已验证的透传转发逻辑。路径不再剥 /v1。

  **类别**：`deep` | **提交**：`feat: 独立透传服务器`

- [x] 7. 更新 `server.py` 读取 ports 配置

  **做什么**：HOST/PORT 常量、L638/L826 硬编码 48743 均改为从 `common.get_port()` 读取。

  **类别**：`quick` | **提交**：`refactor(server): 从配置读取端口`

- [x] 8. 更新 `server.sh` 管理 3 个服务

  **做什么**：新增 `start/stop/status_pass_through`，PID 文件 `.pass_through.pid`。

  **类别**：`quick` | **提交**：`feat(server.sh): 三服务管理`

- [ ] 9. 更新 `test/test_proxy_pass_through.py` 导入

  **做什么**：`from proxy` → `from common`/`from pass_through`，更新端到端端口 48743→48744。

  **类别**：`unspecified-high` | **提交**：`test: 更新透传测试`

- [ ] 10. 更新其他测试文件

  **做什么**：`test_proxy_config.py`、`test_e2e_smoke.py`、`test_proxy_logger_integration.py` — `proxy.port` → `ports.codex_proxy`。

  **类别**：`quick` | **提交**：`test: 配置引用更新`

- [ ] 11. 端到端验证 + 文档更新

  **做什么**：重启全服务，验证 proxy 返回 404 透传、pass_through 响应透传、server.py 连通。更新 AGENTS.md/CLAUDE.md。

  **类别**：`quick` | **提交**：`docs: 独立透传端口文档`

---

## Final Verification Wave

- [ ] F1-F4: oracle x3 + general x1 — 合规、质量、QA、保真度

---

## Commit Strategy

| Wave | 消息 |
|------|------|
| 1 | `refactor: 抽取 common.py + 清理 proxy.py 透传代码` |
| 2 | `feat: 独立透传端口 + 配置化 + 三服务管理` |
| 3 | `test+docs: 更新测试和文档` |

## Success Criteria

```bash
# proxy.py 不再响应透传 → 404
curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:48743/v1/chat/completions

# pass_through.py 响应透传 → 200
curl -s -X POST http://127.0.0.1:48744/chat/completions -H "Content-Type: application/json" -d '{"model":"*","messages":[{"role":"user","content":"hi"}],"max_tokens":5}'

# 三服务管理
./server.sh status

# 全量测试 ≥ 348
python3 -m pytest test/ -q
