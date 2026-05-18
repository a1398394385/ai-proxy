# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 概述

`static/` 是 Hermes Data Browser 的前端 —— 纯原生 ES Module SPA，无框架、无构建步骤。

## JS 架构

```
index.html
  └── <script type="module" src="js/app.js">
        ├── core.js              # 共享工具层
        ├── pages/facts.js       # Fact Store 页面
        ├── pages/tokens.js      # Token 统计页面（最大模块 ~1090 行）
        ├── pages/upstreams.js   # 模型管理页面（上游 + 模型抽屉）
        ├── pages/routes.js      # 路由映射页面
        └── pages/pricing.js     # 计费表页面
```

所有 5 个页面模块都是活跃的，无死代码。`models.js` 不再存在于 `js/pages/`。

## 核心模块 `core.js` — 共享工具层

| 类别 | 导出 | 说明 |
|------|------|------|
| **API** | `api(path, opts)` | `fetch` 封装，自动 JSON 头 |
| **事件总线** | `bus.emit(name, detail)`, `bus.on(name, fn)` | document 级 CustomEvent |
| **动作委托** | `on(action, fn)`, `delegate(root)` | `data-action` 属性分发，注册到 `__actions` Map |
| **主题** | `initTheme()`, `toggleTheme()` | CSS 变量切换，localStorage 持久化 |
| **导航** | `switchPage(page)`, `saveDefaultPage()` | Tab 切换 + 记住上次页面 |
| **模态框** | `showModal(title, content, footer)`, `closeModal()` | 全局单例 `#modal-overlay` |
| **格式化** | `formatNumber()`, `formatTokens()`, `escHtml()` | 数字/Token 显示 + XSS 防护 |
| **自定义选择** | `customSelectHtml()`, `wireCustomSelect()`, `buildCustomSelect()` | 纯 JS/CSS 下拉组件 |
| **常量** | `FORMAT_LABELS`, `FORMAT_COLORS` | 格式名称/颜色映射 |

### 动作委托机制
```javascript
// 注册: on('saveFact', async (target) => { ... })
// HTML: <button data-action="saveFact">保存</button>
// 分发: delegate(root) 监听 click → e.target.closest('[data-action]')
```
所有 CRUD 操作都走此模式，无需手动 `addEventListener`。

### 事件总线
跨模块通信：`bus.emit('config:upstream-changed')` → `app.js` 监听到 → `refreshConfigStatus()`

## HTML 结构

5 个顶级 Tab（`data-page` 属性）：
| Tab | `data-page` | 默认 |
|-----|-------------|------|
| Fact Store | `facts` | 是 |
| Token 统计 | `tokens` | |
| 模型管理 | `models` | |
| 路由映射 | `routes` | |
| 计费表 | `pricing` | |

Token 页面内部有 3 个子 Tab：`models`（默认）、`requests`、`upstream`。
所有页面容器均为 `.app > div` 兄弟节点，通过 `.hidden` 类切换显示。
全局模态框 `#modal-overlay` 被所有页面复用。

## CSS 架构

```
base.css      # 设计 Token（CSS 变量）、布局、按钮、徽章、模态框、自定义选择、glass card
facts.css     # Fact 卡片展开/折叠
tokens.css    # KPI 网格、SVG 面积图、子 Tab、成本面板、分页、请求/上游表格
models.css    # 上游表格、抽屉手风琴、自动检测模型、checkbox/toggle
routes.css    # 路由类型卡片、路由表格、代理路由卡片、模态表单
pricing.css   # 统计卡片、计费网格、卡片项
```

### 主题系统
CSS 自定义属性，`:root`（暗色默认）/ `[data-theme="light"]`。切换持久化到 `localStorage`。

## 各页面 API 端点汇总

| 页面 | 端点前缀 | 方法 |
|------|---------|------|
| Facts | `/api/facts`, `/api/categories`, `/api/stats` | GET/POST/PUT/DELETE |
| Tokens | `/api/token_stats`, `/api/token_stats/by_model`, `/api/token_stats/trend`, `/api/token_stats/requests`, `/api/token_stats/by_upstream`, `/api/pricing` | GET |
| Upstreams | `/api/upstreams`, `/api/models`, `/api/routes`, `/api/config/status` | GET/POST/PUT/DELETE |
| Routes | `/api/routes`, `/api/agent-routes`, `/api/models`, `/api/upstreams` | GET/POST/PUT/DELETE |
| Pricing | `/api/pricing` | GET/POST/PUT/DELETE |

## 注意事项
- 无 React/Vue/打包工具，纯 ES Module 浏览器原生运行
- `escHtml()` 在所有用户数据注入 HTML 时必须使用
- Token 页面有自己的子 Tab 和独立数据加载系统（`initSubTabs()`）
- 自定义选择组件不是原生 `<select>`，需要通过 `core.js` 的函数操作
- 路由页面区分主路由和代理路由（`agent-routes`），代理路由是叠加表格
- 模型管理页面以「上游 → 展开抽屉 → 模型列表」的层级展示，没有独立的模型页面
