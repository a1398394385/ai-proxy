# AI Proxy

统一的 LLM API 代理，支持 **OpenAI Responses / Anthropic Messages / OpenAI Chat Completions** 三种协议格式 NxM 互转，纯 Python 标准库，0 第三方依赖。

附带 **Hermes Data Browser** Web 管理面板 —— Token 统计、模型路由 CRUD、计费管理、Fact Store。

## 快速开始

**Linux / macOS**

```bash
./server.sh start          # 启动 Data Browser + AI Proxy
./server.sh stop           # 停止
./server.sh restart        # 重启（无热重载）
./server.sh status         # 查看状态
```

**Windows**

```powershell
# PowerShell（推荐）
.\server.ps1 start
.\server.ps1 stop
.\server.ps1 restart
.\server.ps1 status
```

或使用 `server.bat`（CMD 兼容）。

| 服务 | 端口 | 用途 |
|------|------|------|
| Hermes Data Browser | 18742 | Web UI 管理面板 |
| AI Proxy | 48743 | 统一代理 |

管理面板默认访问 `http://localhost:18742`。

## 代理能力

```
客户端 POST /v1/responses 或 /v1/messages 或 /v1/chat/completions
  ↓
AI Proxy (48743)
  ├─ 透传: 客户端格式 == 上游格式 → 原样转发
  └─ 转换: 客户端格式 ≠ 上游格式 → Chat Completions 中间格式互转
```

协议转换矩阵（当前已实现）:

| 客户端 | 上游 | 方向 |
|--------|------|------|
| Responses | Chat Completions | 请求 + 响应（含流式） |
| Messages | Chat Completions | 请求 + 响应（含流式） |
| Chat Completions | Chat Completions | 透传 |

## 测试

```bash
python3 -m pytest test/ -q         # 全量 529 tests
python3 test/quick_test.py         # Token 快速冒烟（需服务运行）
```

## 项目结构

```
proxy/      AI Proxy 核心包 — 协议转换 + 透传 + 日志 + 路由
server/     Data Browser 后端 — REST API (CRUD + 查询)
static/     Web 前端 — 纯 ES Module SPA
test/       18 测试文件 — unittest.TestCase
docs/       设计文档
```

## 文档

- [CLAUDE.md](CLAUDE.md) — 项目全景
- [proxy/CLAUDE.md](proxy/CLAUDE.md) — 协议转换管线
- [server/CLAUDE.md](server/CLAUDE.md) — Data Browser 后端
- [static/CLAUDE.md](static/CLAUDE.md) — 前端架构
- [test/CLAUDE.md](test/CLAUDE.md) — 测试约定
