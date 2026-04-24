# Hermes Data Browser — AGENTS.md

本地 Web 工具，浏览 Hermes 内部数据（Fact Store + Token 使用统计）。

## 项目路径

```
~/.hermes/fact-store-browser/
├── server.py          # 后端（753行），纯 Python 标准库
├── static/index.html  # 前端（2190行），vanilla JS + CSS + SVG 面积图
├── server.sh          # 服务管理：./server.sh {start|stop|status}
├── restart_server.sh  # 强制重启（杀进程 → 清缓存 → 重启 → 验证 HTTP）
├── auto_restart.py    # Python 版重启工具
├── quick_test.py      # API 冒烟测试
└── .server.pid        # PID 文件
```

## 启动/停止

```bash
# 启动
cd ~/.hermes/fact-store-browser && ./server.sh start

# 查看状态
./server.sh status

# 停止
./server.sh stop

# 修改代码后重启
./restart_server.sh   # 或 python3 auto_restart.py
```

服务运行在 `http://127.0.0.1:18742`

## 数据源（3 个 SQLite 数据库）

| 数据库 | 路径 | 用途 |
|--------|------|------|
| memory_store.db | `~/.hermes/memory_store.db` | Fact Store 数据 |
| state.db | `~/.hermes/state.db` | Session/Token 统计 |
| cc-switch.db | `~/.cc-switch/cc-switch.db` | 模型计费规则（model_pricing 表） |

memory_store.db 核心表：`facts`、`facts_fts`（FTS5）、`entities`、`fact_entities`（多对多关联）

state.db 核心表：`sessions`（model/input_tokens/output_tokens/cache_read_tokens/cache_write_tokens/message_count/started_at）、`messages`

cc-switch.db 核心表：`model_pricing`（model_id/input_cost_per_million/output_cost_per_million/cache_read_cost_per_million/cache_creation_cost_per_million）

## API 端点

### Fact Store

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/facts` | 全部 facts，可选 `?q=搜索` + `?category=分类` |
| GET | `/api/facts/:id` | 单条 fact |
| POST | `/api/facts` | 创建 fact，body: `{content, category, tags, trust_score, entities[]}` |
| PUT | `/api/facts/:id` | 更新 fact |
| DELETE | `/api/facts/:id` | 删除 fact |
| POST | `/api/facts/:id/feedback` | feedback，body: `{action: "helpful"\|"unhelpful"}` |
| GET | `/api/categories` | 分类列表及计数 |
| GET | `/api/stats` | 汇总统计（total facts、categories、top 20 entities） |

### Token 统计

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/token_stats?period=day\|week\|month` | 汇总（总 token + 成本） |
| GET | `/api/token_stats/by_model?period=...` | 按模型分组 |
| GET | `/api/token_stats/trend?period=...` | 时间序列趋势（24点/7点/30点，无数据补0） |
| GET | `/api/token_stats/summary` | 三个周期一次性返回 |

## server.py 核心函数

| 函数 | 作用 |
|------|------|
| `get_model_pricing()` | 从 cc-switch.db 读取计费规则，缓存 5 分钟 |
| `calculate_cost(model, input, output, cache_read, cache_write)` | 按模型计费规则计算 USD 成本 |
| `get_all_facts()` / `search_facts(query)` | Fact Store 查询 |
| `get_token_stats(period)` | 汇总统计，LEFT JOIN messages 补偿 session token=0 的情况 |
| `get_token_stats_by_model(period)` | 按模型分组 |
| `get_daily_token_trend(period)` | 时间序列，固定点数（day=24h, week=7d, month=30d） |

## 前端技术栈

- 无框架：vanilla JS + CSS 变量（暗色/亮色主题切换）
- SVG 手绘面积图（无图表库），渐变填充、平滑曲线、图例可点击切换
- 玻璃拟态风格（glass-card），CSS 变量 HSL 色彩系统
- 三个 Tab：Facts / Tokens / Settings

## 开发注意事项

1. **零外部依赖** — 只用 Python 标准库，不要引入 pip 包
2. **单文件后端** — server.py 同时负责 API 路由和静态文件服务
3. **静态文件服务** — `/` 和 `/static/*` 走 `SimpleHTTPRequestHandler` 的文件服务逻辑
4. **CORS** — API 返回 `Access-Control-Allow-Origin: *`
5. **修改后必须重启** — 标准库 HTTP server 不支持热重载，改代码后跑 `./restart_server.sh`
6. **Token 数据有延迟** — 运行中 session 的 token 显示为 0，结束后才更新，这是 Hermes 设计机制
7. **计费规则缺失时成本为 0** — 如果 model 不在 cc-switch.db 中，`calculate_cost` 返回 0
