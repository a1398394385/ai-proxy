# 前端单文件拆分为 ES Modules

## TL;DR

> **快速摘要**：将 2622 行的单体 `static/index.html` 拆分为 ES Modules + 按页面 CSS，零服务器改动，零构建工具。
>
> **可交付物**：
> - 4 个 CSS 文件（base, facts, tokens, models）
> - 5 个 JS 模块（core, facts, tokens, models, app）
> - 1 个精简的 index.html 骨架（~80 行）
> - Playwright E2E 测试（至少 10 个场景）
>
> **预估工作量**：中等
> **并行执行**：YES — 3 波
> **关键路径**：Task 1 → Task 3 → Task 4/5/6 → Task 7 → Task 8

---

## 上下文

### 原始需求
用户要求将目前 ~61KB、2622 行的单体前端文件 `static/index.html` 拆分为独立的模块，每个页面作为单独的模块。

### 访谈摘要
**关键讨论**：
- 拆分策略：ES Modules（原生 `<script type="module">`），不引入构建工具
- CSS 策略：按页面拆分（base.css + facts.css + tokens.css + models.css）
- 测试策略：TDD（先写 Playwright E2E 测试，再拆分，确保不破坏现有功能）
- 方案 B（多页面 HTML）被否决，因为会引入服务器改动 + 页面刷新

**研究结果**：
- `server.py` 第 738-762 行：静态文件服务已支持 `.css` 和 `.js` MIME 类型 — 零服务器改动
- 前端结构：CSS (~1138行) + HTML body (~270行) + JS (~1208行)，3 个 Tab 页面 + 设置模态框
- 38 个 JavaScript 函数，26 个 API 端点调用

### Metis 审查
**发现的差距**（已解决）：
- **关键 — `onclick` 处理程序**：HTML 中有 `onclick="closeModal()"` 等内联事件。ES 模块不会将函数暴露到全局作用域。必须在 `core.js` 中将函数显式附加到 `window` 上（`window.closeModal = closeModal`）
- **关键 — 执行顺序**：`initTheme()` 当前在脚本解析时运行。在模块设置中，必须在 `DOMContentLoaded` 处理器中调用，以避免在 DOM 准备好之前查询 DOM
- **跨页面状态**：可变全局变量（`currentPage`、`currentPeriod`、`allFacts` 等）必须驻留在 `core.js` 中并通过具名导出暴露
- **设置代码**：设置是模态框而非完整页面，其样式放入 `base.css`，逻辑放入 `core.js`

---

## 工作目标

### 核心目标
将单体 `static/index.html` 拆分为 ES 模块化结构，同时保持 100% 的行为兼容性。

### 具体可交付物

```
static/
├── index.html              # 骨架（仅 HTML 结构 + <link> + <script type="module">）
├── css/
│   ├── base.css            # 主题变量 + 全局重置 + 布局 + 共享组件 + 设置 + 模态框
│   ├── facts.css           # Fact Store 页面样式
│   ├── tokens.css          # Token 统计页面样式（含图表）
│   └── models.css          # 模型管理页面样式（含表格、表单）
└── js/
    ├── core.js             # 工具函数 + 主题 + 设置 + 事件总线 + 全局状态 + window 挂载
    ├── pages/
    │   ├── facts.js        # Fact Store：loadFacts, renderFacts, CRUD
    │   ├── tokens.js       # Token 统计：loadTokenStats, renderKPI, renderChart, renderModelTable
    │   └── models.js       # 模型管理：event bus, 3个表渲染器, CRUD, applyConfig
    └── app.js              # 入口：DOMContentLoaded 初始化, 标签页路由
```

### 完成定义
- [ ] `./server.sh restart && python3 -m pytest test/ -q` → 333 passed, 0 failed
- [ ] `index.html` 在浏览器中加载零控制台错误
- [ ] 三个标签页（Facts/Tokens/Models）+ 设置模态框功能完全正常
- [ ] 所有 Playwright E2E 场景通过

### 必须做
- HTML 结构逐字节保留（仅替换 `<style>` 和 `<script>` 标签）
- 所有函数签名、参数名、返回值保持不变
- 所有 CSS 选择器、特异性、顺序保持不变
- 所有在 `onclick="..."` 中引用的函数挂载到 `window`

### 绝对不能做（防护栏）
- **不得**更改任何 HTML `id`、`class` 或 `data-*` 属性
- **不得**将任何函数转换为箭头函数（保留 `this` 绑定）
- **不得**添加或删除 `console.log` 语句
- **不得**更改 `server.py` 的静态文件服务代码
- **不得**引入任何构建工具（webpack/vite/esbuild 等）
- **不得**页面模块之间相互导入（facts.js 不导入 tokens.js）
- **不得**引入 Chart.js 或其他图表库（图表代码仍是纯 SVG）

---

## 验证策略

> **零人工干预** — 所有验证由代理执行。

### 测试决策
- **基础设施已存在**：YES（pytest 用于后端，Playwright 用于前端 E2E）
- **自动化测试**：TDD — 先写 Playwright E2E 测试，再拆分
- **框架**：Playwright（前端 E2E）+ pytest（后端回归）
- **TDD 流程**：每个任务遵循 RED（编写失败测试）→ GREEN（最小化实现）→ REFACTOR

### QA 策略
每项任务都包含代理执行的 QA 场景（见下方 TODO 模板）。
证据保存至 `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`。

- **UI/浏览器**：使用 Playwright — 导航、交互、断言 DOM、截图
- **后端**：使用 Bash — `pytest`、`server.sh status`
- **API**：使用 Bash（curl）— 发送请求、断言状态码 + 响应字段

---

## 执行策略

### 并行执行波次

> 通过将独立任务分组为并行波次来最大化吞吐量。
> 每波完成后才开始下一波。
> 目标：每波 3-5 个任务。

```
Wave 1（立即开始 — 基础层 + 测试框架）：
├── Task 1: 提取 base.css [quick]
├── Task 2: 提取 core.js [quick]
└── Task 3: Playwright 基础冒烟测试 [quick]

Wave 2（Wave 1 之后 — 页面模块，最大并行度）：
├── Task 4: 提取 facts.css + facts.js [quick]
├── Task 5: 提取 tokens.css + tokens.js [deep]
└── Task 6: 提取 models.css + models.js [quick]

Wave 3（Wave 2 之后 — 入口 + 整合）：
├── Task 7: 创建 app.js + 精简 index.html [quick]
└── Task 8: window 全局挂载 + onclick 适配 [quick]

Wave FINAL（所有任务之后 — 4 项并行审查）：
├── Task F1: 计划合规审计 (oracle)
├── Task F2: 代码质量审查
├── Task F3: 全量 Playwright E2E 验证
└── Task F4: 范围忠实度检查
→ 呈现结果 → 等待用户明确 "okay"
```

**关键路径**：Task 1/2 → Task 3 → Task 4/5/6 → Task 7 → Task 8 → F1-F4 → 用户确认
**并行加速**：约 50% 比顺序执行更快
**最大并发**：3（Wave 1 和 Wave 2）

---

## TODOs

> 实现 + 测试 = 一个任务。绝不分离。
> 每个任务都必须包含：推荐的代理配置 + 并行化信息 + QA 场景。
> **没有 QA 场景的任务是不完整的。无一例外。**

- [x] 1. 提取 `static/css/base.css` — 共享样式

  **做什么**：
  - 将以下 CSS 从 `index.html` 的 `<style>` 块逐字提取到 `static/css/base.css`：
    - `@import` Google Fonts（第 8 行）
    - `:root` 暗色主题变量（第 10–34 行）
    - `[data-theme="light"]` 亮色主题变量（第 37–59 行）
    - 通用重置 `*`（第 61–65 行）
    - `body` 基础样式（第 67–74 行）
    - `.glass-card`（第 77–87 行）
    - `.app` 布局（第 89–94 行）
    - `.top-nav`（第 97–106 行）
    - `.nav-brand`、`.nav-brand-icon`（第 108–126 行）
    - `.nav-tabs`、`.nav-tab`、`.nav-tab:hover`、`.nav-tab.active`（第 128–156 行）
    - `.nav-actions`（第 158–162 行）
    - `.main-content`（第 164–171 行）
    - `.toolbar`、`.toolbar-group`、`.toolbar-btn`、`.toolbar-btn:hover`、`.toolbar-btn.active`（第 173–213 行）
    - `#theme-toggle` 及其 hover（第 215–235 行）
    - `.search-box`、`.search-box input`、`.search-box .search-icon`（约第 237–270 行）
    - `.modal-overlay`、`.modal`、`.modal-header`、`.modal-body`、`.modal-footer`、`.btn` 等模态框相关样式
    - `.settings-container`、`.settings-card`、`.settings-section` 等设置页面样式
    - 页面切换相关的 `.hidden` 类
  - 在 `index.html` 中，用 `<link rel="stylesheet" href="css/base.css">` 替换 `<style>` 标签
  - 当其他 CSS 文件（facts/tokens/models.css）已就位时，逐步从 `index.html` 的 `<style>` 中移除提取的样式

  **绝对不能做**：
  - 不得改变任何选择器、属性值或顺序
  - 不得合并或重构 CSS 规则
  - 不得移除任何仍被页面特定样式依赖的样式

  **推荐代理配置**：
  - **类别**：`quick` — 直接的 CSS 提取，需要精确性但逻辑简单
  - **技能**：[]

  **并行化**：
  - **可并行运行**：YES（与 Task 2）
  - **并行组**：Wave 1（与 Task 2, Task 3）
  - **阻塞**：Task 4, Task 5, Task 6
  - **被阻塞**：无

  **参考资料**：
  - `static/index.html:7-1145` — 完整的 `<style>` 块，所有 CSS 来源
  - `server.py:738-762` — 静态文件服务，验证 `.css` MIME 类型支持

  **验收标准**：
  - [ ] `static/css/base.css` 文件存在且内容非空
  - [ ] `index.html` 的 `<head>` 中包含 `<link rel="stylesheet" href="css/base.css">`
  - [ ] `curl -s http://127.0.0.1:18742/css/base.css | head -1` → 返回 CSS 内容

  **QA 场景**：

  ```
  场景：base.css 加载且样式生效（正常路径）
    工具：Playwright
    前置条件：./server.sh start 运行中
    步骤：
       1. 导航至 http://127.0.0.1:18742
       2. 等待 load 事件（timeout: 10s）
       3. 验证 document.querySelector('.top-nav') !== null
       4. 验证 getComputedStyle(document.documentElement).getPropertyValue('--primary').trim() !== ''
       5. 检查浏览器控制台无 404 错误（filter: "base.css"）
    预期结果：导航栏可见，CSS 变量已解析，无 404
    失败指标：.top-nav 为 null、CSS 变量为空、控制台有 base.css 404
    证据：.sisyphus/evidence/task-1-base-loaded.png

  场景：主题切换仍有效（正常路径）
    工具：Playwright
    前置条件：页面已加载
    步骤：
       1. 点击 #theme-toggle
       2. 验证 document.documentElement.getAttribute('data-theme') === 'light'
       3. 再次点击 #theme-toggle
       4. 验证 data-theme === 'dark'
    预期结果：暗/亮主题正确切换
    证据：.sisyphus/evidence/task-1-theme-toggle.png
  ```

  **证据**：
  - [ ] `task-1-base-loaded.png`
  - [ ] `task-1-theme-toggle.png`

  **提交**：YES
  - 消息：`feat(frontend): 提取 base.css — 主题变量、布局、共享组件样式`
  - 文件：`static/css/base.css`, `static/index.html`
  - 预提交：`python3 -m pytest test/ -q`

- [x] 2. 提取 `static/js/core.js` — 共享 JavaScript 模块

  **做什么**：
  - 从 `index.html` 的 `<script>` 块提取以下代码到 `static/js/core.js`，为每个函数/变量添加 `export`：
    - **API 工具**：`api(path, opts)`、`formatNumber(n)`、`formatTokens(n)`、`escHtml(s)`
    - **主题**：`initTheme()`、`toggleTheme()`、`updateThemeButton(theme)`
    - **模态框**：`showModal(title, content, footer)`、`closeModal()`
    - **设置**：`initSettings()`、`applyDefaultPage(page)`、`showSettings()`、`saveDefaultPage(page)`
    - **事件总线**：`const bus = { emit, on }` — 导出为 `export { bus }`
    - **全局可变状态**：`currentPage`、`currentPeriod`、`allFacts`、`allModels`、`activeCategory`、`editingId`、`chartData`、`hiddenSeries` — 使用 `export let`
    - **常量**：`catLabels`、`catIcons` — 使用 `export const`
  - **关键 — window 全局挂载**：在 `core.js` 末尾挂载 onclick 处理器所需的函数：
    ```js
    window.closeModal = closeModal;
    window.toggleTheme = toggleTheme;
    window.showSettings = showSettings;
    window.saveDefaultPage = saveDefaultPage;
    ```
  - 从 `index.html` 中移除被提取的函数

  **绝对不能做**：
  - 不得将任何函数转换为箭头函数（保留 `this` 绑定）
  - 不得更改任何函数签名、参数名或返回值
  - 不得添加或删除 console.log

  **推荐代理配置**：
  - **类别**：`quick` — 直接的函数提取 + 添加 export 关键字
  - **技能**：[]

  **并行化**：
  - **可并行运行**：YES（与 Task 1）
  - **并行组**：Wave 1
  - **阻塞**：Task 4-8
  - **被阻塞**：无

  **参考资料**：
  - `static/index.html:1412-1547` — 主题和设置函数
  - `static/index.html:1549-1694` — API 工具和格式化函数
  - `static/index.html:2195-2210` — 模态框函数
  - `static/index.html:2312-2315` — 事件总线

  **验收标准**：
  - [ ] `static/js/core.js` 文件存在，包含所有列出的函数
  - [ ] 所有导出函数在模块作用域中有 `export` 关键字
  - [ ] `window.closeModal`、`window.toggleTheme` 等在浏览器中可调用

  **QA 场景**：

  ```
  场景：core.js 模块加载无错误
    工具：Playwright
    步骤：
       1. 导航至 http://127.0.0.1:18742
       2. 检查控制台无 "Failed to load module" 或 "Uncaught SyntaxError"
       3. await import('./js/core.js') — 验证返回模块对象
    预期结果：零模块加载错误，模块可导入
    证据：.sisyphus/evidence/task-2-core-loaded.txt

  场景：api() 包装器仍正常工作
    工具：Playwright evaluate
    步骤：
       1. const m = await import('./js/core.js')
       2. const data = await m.api('/api/categories')
       3. 验证 Array.isArray(data) === true
    预期结果：api() 返回有效 JSON 数组
    证据：.sisyphus/evidence/task-2-api-works.txt
  ```

  **证据**：
  - [ ] `task-2-core-loaded.txt`
  - [ ] `task-2-api-works.txt`

  **提交**：YES
  - 消息：`feat(frontend): 提取 core.js — 共享工具、主题、设置、事件总线、全局状态`
  - 文件：`static/js/core.js`, `static/index.html`

- [x] 3. Playwright E2E 冒烟测试 + 基础框架搭建

  **做什么**：
  - 创建 `test/test_frontend_e2e.py`（Playwright + pytest）
  - 安装 Playwright：`pip install playwright && python3 -m playwright install chromium`
  - 编写 7 个基线冒烟测试，在**拆分前的原始 index.html** 上运行：
    1. `test_page_loads` — 标题为 "Hermes Data Browser"
    2. `test_all_tabs_present` — 3 个 nav-tab 可见
    3. `test_default_tab_facts` — `#page-facts` 可见，其他隐藏
    4. `test_no_console_errors` — 零 console.error
    5. `test_nav_tab_switching` — 点击标签页切换页面可见性
    6. `test_theme_toggle` — 暗/亮切换
    7. `test_settings_modal` — 设置页面可见
  - 使用 pytest fixture 启动/停止服务器

  **绝对不能做**：
  - 不得修改 index.html
  - 不得将测试写成仅通过修改后代码的形式

  **推荐代理配置**：
  - **类别**：`quick`
  - **技能**：[`web-access`] — Playwright 浏览器自动化核心能力

  **并行化**：
  - **可并行运行**：YES（与 Task 1, Task 2）
  - **并行组**：Wave 1
  - **阻塞**：Task 4-8（需要基线测试通过）
  - **被阻塞**：无

  **参考资料**：
  - `test/` 目录下现有测试文件 — 遵循 pytest 约定
  - `static/index.html:1155-1165` — 标签页 HTML 结构
  - Playwright 文档：`https://playwright.dev/python/docs/intro`

  **验收标准**：
  - [ ] `python3 -m pytest test/test_frontend_e2e.py -v` → 7 passed, 0 failed

  **QA 场景**：
  ```
  场景：全部冒烟测试通过基线
    工具：Bash
    步骤：
       1. ./server.sh restart
       2. python3 -m pytest test/test_frontend_e2e.py -v --timeout=30
       3. 验证输出包含 "7 passed, 0 failed"
    预期结果：全部通过
    证据：.sisyphus/evidence/task-3-smoke.txt
  ```

  **证据**：`task-3-smoke.txt`

   **提交**：YES
   - 消息：`test(frontend): 添加 Playwright E2E 冒烟测试基线（7 个测试）`
   - 文件：`test/test_frontend_e2e.py`

- [x] 4. 提取 `static/css/facts.css` + `static/js/pages/facts.js`

  **做什么**：
  - **CSS**：将 Fact Store 页面特定样式从 `<style>` 逐字提取到 `static/css/facts.css`：
    - `.fact-card`、`.fact-header`、`.fact-content`、`.fact-footer`
    - `.category-pill`、`.category-pill.active`
    - `.trust-bar`、`.trust-fill`
    - `.tag`、`.entity-link`
    - 其他以 `.fact-` 或 `#facts-` 为前缀的样式
    - 在 `index.html` 中添加 `<link rel="stylesheet" href="css/facts.css">`
  - **JS**：将 Fact Store 函数提取到 `static/js/pages/facts.js`：
    - `loadFacts(q)`（第 1584–1591 行）
    - `loadCategories()`（第 1593–1610 行）
    - `renderFacts(facts)`（第 1612–1661 行）
    - `toggleFactExpand(btn)`（第 1664–1678 行）
    - `editFact(id)`（第 2247–2276 行）
    - `saveFact()`（第 2278–2296 行）
    - `deleteFact(id)`（第 2298–2302 行）
    - `#add-btn` 的点击处理程序（第 2213–2245 行）— 包装为 `export function initFactCRUD()`
    - 搜索输入处理程序（第 2304–2307 行）— 包装为 `export function initFactSearch()`
  - 从 `core.js` 导入依赖：`import { api, escHtml, showModal, closeModal, allFacts, activeCategory, editingId, catLabels, catIcons } from '../core.js'`
  - 从 `index.html` 的 `<script>` 中移除被提取的函数

  **绝对不能做**：
  - 不得更改函数签名、行为或 DOM 选择器
  - 不得在模块顶层调用任何函数（仅定义 + 导出）

  **推荐代理配置**：
  - **类别**：`quick`
  - **技能**：[]

  **并行化**：
  - **可并行运行**：YES（与 Task 5, Task 6）
  - **并行组**：Wave 2
  - **阻塞**：Task 7
  - **被阻塞**：Task 1, Task 2, Task 3

  **参考资料**：
  - `static/index.html:1173-1184` — `#page-facts` HTML 结构
  - `static/index.html:1583-1683` — Fact 加载/渲染函数
  - `static/index.html:2212-2302` — Fact CRUD 函数
  - `static/index.html:2304-2307` — 搜索处理器

  **验收标准**：
  - [ ] `static/css/facts.css` 和 `static/js/pages/facts.js` 存在且非空
  - [ ] `facts.js` 正确从 `core.js` 导入
  - [ ] `index.html` 包含 `<link rel="stylesheet" href="css/facts.css">`

  **QA 场景**：

  ```
  场景：事实加载和渲染（正常路径）
    工具：Playwright
    步骤：
       1. 导航至 http://127.0.0.1:18742
       2. 等待 #facts-container 有子元素（timeout: 10s）
       3. 验证至少一个 .fact-card 存在
       4. 验证 #cat-filters 包含类别药丸按钮
    预期结果：事实卡片和类别筛选器已渲染，无控制台错误
    证据：.sisyphus/evidence/task-4-facts-loaded.png

  场景：事实搜索（正常路径）
    工具：Playwright
    步骤：
       1. 在 #search 输入框中输入 "test"（使用 fill）
       2. 等待网络请求完成
       3. 验证 fetch 请求 URL 包含 "?q=test"
    预期结果：搜索触发正确的 API 调用
    证据：.sisyphus/evidence/task-4-search.png

  场景：事实 CRUD — 创建（正常路径）
    工具：Playwright
    步骤：
       1. 点击 #add-btn
       2. 验证 .modal-overlay 可见
       3. 在 .modal-body 内填写表单字段
       4. 点击 .modal-footer 中的保存按钮
       5. 验证 POST /api/facts 请求已发出
    预期结果：模态框打开，保存触发 POST
    证据：.sisyphus/evidence/task-4-create-fact.png
  ```

  **证据**：
  - [ ] `task-4-facts-loaded.png`、`task-4-search.png`、`task-4-create-fact.png`

  **提交**：YES
  - 消息：`feat(frontend): 提取 facts 模块 — CSS + JS ES 模块`
  - 文件：`static/css/facts.css`, `static/js/pages/facts.js`, `static/index.html`

- [x] 5. 提取 `static/css/tokens.css` + `static/js/pages/tokens.js`

  **做什么**：
  - **CSS**：将 Token 统计页面特定样式提取到 `static/css/tokens.css`：
    - `.kpi-grid`、`.kpi-card`、`.kpi-label`、`.kpi-value`、`.kpi-sub`
    - `.chart-wrapper`、`.area-chart`、`.chart-grid`、`.chart-axis`
    - `.chart-tooltip`、`.chart-cursor`
    - `.legend`、`.legend-item`
    - `.model-table` 相关样式
    - `.period-btn` 样式
    - 在 `index.html` 中添加 `<link rel="stylesheet" href="css/tokens.css">`
  - **JS**：将 Token 统计函数提取到 `static/js/pages/tokens.js`：
    - `loadTokenStats()`（第 1697–1713 行）
    - `renderKPI(stats)`（第 1715–1772 行）
    - `renderTrendChart(trends)`（第 1778–2073 行）— 包含子函数：`niceMax()`、`formatAxisValue()`、`formatCostAxis()`、`labelFormatter`
    - `showTooltip(mouseX, mouseY, data)`（第 2075–2131 行）
    - `renderModelTable(models)`（第 2153–2189 行）
    - 周期按钮和图表交互的事件处理程序 — 包装为 `export function initTokenInteractions()`
    - 窗口 resize 监听器

  **绝对不能做**：
  - 不得修改 SVG 图表逻辑（复杂的计算逻辑）
  - 不得更改 `formatAxisValue` 或 `niceMax` 算法

  **推荐代理配置**：
  - **类别**：`deep` — 图表渲染代码是最复杂的单个组件（~300 行纯 SVG），需要精确处理
  - **技能**：[]

  **并行化**：
  - **可并行运行**：YES（与 Task 4, Task 6）
  - **并行组**：Wave 2
  - **阻塞**：Task 7
  - **被阻塞**：Task 1, Task 2, Task 3

  **参考资料**：
  - `static/index.html:1187-1301` — `#page-tokens` HTML 结构（含 SVG 模板）
  - `static/index.html:1685-2193` — Token 统计函数（最大函数块）
  - `static/index.html:1778-2073` — SVG 图表渲染（关键复杂代码）

  **验收标准**：
  - [ ] `static/css/tokens.css` 和 `static/js/pages/tokens.js` 存在且非空
  - [ ] 图表 SVG 在标签页切换时正确渲染
  - [ ] KPI 卡片显示数字（非 NaN/undefined）

  **QA 场景**：

  ```
  场景：Token 统计页面加载（正常路径）
    工具：Playwright
    步骤：
       1. 导航至 http://127.0.0.1:18742
       2. 点击 [data-page="tokens"] 标签页
       3. 等待 #kpi-container 有子元素（timeout: 10s）
       4. 验证至少 4 个 .kpi-card 存在
       5. 验证 .area-chart SVG 包含 <path> 元素
       6. 验证 #model-table tbody 包含 <tr> 行
    预期结果：KPI、图表和模型表格全部渲染
    证据：.sisyphus/evidence/task-5-tokens-loaded.png

  场景：周期切换（正常路径）
    工具：Playwright
    步骤：
       1. 在 Token 标签页，点击 [data-period="day"] 按钮
       2. 验证 GET /api/token_stats?period=day 请求已发出
       3. 点击 [data-period="month"]
       4. 验证 GET /api/token_stats?period=month 请求已发出
    预期结果：每个周期按钮触发正确的 API 调用
    证据：.sisyphus/evidence/task-5-period-switch.txt

  场景：图表图例切换（正常路径）
    工具：Playwright
    步骤：
       1. 在 Token 标签页，等待图表渲染
       2. 点击包含 "Input" 文本的 .legend-item
       3. 验证图例项获得 "hidden" 类
       4. 再次点击以恢复
    预期结果：图例项切换图表系列的可见性
    证据：.sisyphus/evidence/task-5-legend-toggle.png
  ```

  **证据**：
  - [ ] `task-5-tokens-loaded.png`、`task-5-period-switch.txt`、`task-5-legend-toggle.png`

  **提交**：YES
  - 消息：`feat(frontend): 提取 tokens 模块 — CSS + JS（含 SVG 图表）`
  - 文件：`static/css/tokens.css`, `static/js/pages/tokens.js`, `static/index.html`

- [x] 6. 提取 `static/css/models.css` + `static/js/pages/models.js`

  **做什么**：
  - **CSS**：将模型管理页面特定样式提取到 `static/css/models.css`：
    - `.config-table`、`.config-section`、`.config-header`
    - `.status-dot`（在线/离线指示器）
    - 模型配置相关的表格、表单、按钮样式
    - 在 `index.html` 中添加 `<link rel="stylesheet" href="css/models.css">`
  - **JS**：将模型管理函数提取到 `static/js/pages/models.js`：
    - `loadModelConfig()`（第 2412–2417 行）
    - `loadAllModelConfigTables()`（第 2406–2410 行）
    - `refreshConfigStatus()`（第 2329–2342 行）
    - `refreshUpstreamDropdown()`（第 2399–2404 行）
    - `loadUpstreamTable()`（第 2344–2362 行）
    - `loadModelTable(upstreamId)`（第 2364–2379 行）
    - `loadRouteTable()`（第 2381–2395 行）
    - `applyConfig()`（第 2605–2616 行）
    - **上游 CRUD**：`showUpstreamModal()`、`saveUpstream()`、`testUpstream()`、`confirmDisableUpstream()`
    - **模型 CRUD**：`showModelModal()`、`saveModel()`、`confirmDeleteModel()`
    - **路由 CRUD**：`showRouteModal()`、`saveRoute()`、`confirmDeleteRoute()`
  - 从 `core.js` 导入：`import { api, showModal, closeModal, bus, formatNumber } from '../core.js'`
  - **CRITICAL — window 挂载**：在模块末尾挂载 onclick 使用的函数：
    ```js
    window.showUpstreamModal = showUpstreamModal;
    window.saveUpstream = saveUpstream;
    window.testUpstream = testUpstream;
    window.showModelModal = showModelModal;
    window.saveModel = saveModel;
    window.showRouteModal = showRouteModal;
    window.saveRoute = saveRoute;
    window.applyConfig = applyConfig;
    ```
  - 在 `core.js` 中保留事件总线监听器（`bus.on(...)`），因为在 `loadModelConfig()` 期间需要它们

  **绝对不能做**：
  - 不得更改事件总线协议（`bus.emit`/`bus.on` 签名）
  - 不得破坏配置脏跟踪（`config:dirty`/`config:applied` 事件）

  **推荐代理配置**：
  - **类别**：`quick`
  - **技能**：[]

  **并行化**：
  - **可并行运行**：YES（与 Task 4, Task 5）
  - **并行组**：Wave 2
  - **阻塞**：Task 7
  - **被阻塞**：Task 1, Task 2, Task 3

  **参考资料**：
  - `static/index.html:1304-1359` — `#page-models` HTML 结构
  - `static/index.html:2309-2616` — 所有模型管理函数（~308 行）
  - `static/index.html:2312-2327` — 事件总线定义和监听器

  **验收标准**：
  - [ ] `static/css/models.css` 和 `static/js/pages/models.js` 存在且非空
  - [ ] 所有 onclick 函数可通过 `window.xxx` 访问

  **QA 场景**：

  ```
  场景：模型管理页面加载（正常路径）
    工具：Playwright
    步骤：
       1. 导航至 http://127.0.0.1:18742
       2. 点击 [data-page="models"] 标签页
       3. 等待 #upstream-table tbody 有子元素（timeout: 10s）
       4. 验证 #config-status 显示代理状态
       5. 验证 3 个表格（upstream、model、route）均有行
    预期结果：3 个配置表格已渲染，代理状态可见
    证据：.sisyphus/evidence/task-6-models-loaded.png

  场景：上游创建（正常路径）
    工具：Playwright
    步骤：
       1. 点击 "添加上游" 按钮
       2. 验证 .modal-overlay 可见，包含上游表单
       3. 填写 base_url 字段
       4. 点击保存
       5. 验证 POST /api/upstreams 请求已发出
    预期结果：模态框打开，保存触发 POST
    证据：.sisyphus/evidence/task-6-create-upstream.png

  场景：路由删除 — 阻止最后一条 fallback 删除（边缘情况）
    工具：Playwright
    步骤：
       1. 在模型页面，尝试删除 source="*" 的路由
       2. 如果这是唯一一条，验证删除被阻止并显示警告
       3. 验证 alert 对话框包含阻止消息
    预期结果：阻止删除最后一条 fallback 路由
    证据：.sisyphus/evidence/task-6-route-guard.txt
  ```

  **证据**：
  - [ ] `task-6-models-loaded.png`、`task-6-create-upstream.png`、`task-6-route-guard.txt`

  **提交**：YES
  - 消息：`feat(frontend): 提取 models 模块 — CSS + JS（含上游/模型/路由 CRUD）`
  - 文件：`static/css/models.css`, `static/js/pages/models.js`, `static/index.html`

- [x] 7. 创建 `static/js/app.js` 入口 + 精简 `index.html`

  **做什么**：
  - 创建 `static/js/app.js` 作为应用入口模块：
    ```js
    import { initTheme, initSettings, applyDefaultPage } from './core.js';
    import { loadFacts, initFactCRUD, initFactSearch } from './pages/facts.js';
    import { loadTokenStats, initTokenInteractions } from './pages/tokens.js';
    import { loadModelConfig } from './pages/models.js';

    // 关键：wait until DOM is fully parsed
    document.addEventListener('DOMContentLoaded', () => {
      initTheme();           // 在任何渲染之前应用主题
      initSettings();        // 加载已保存的偏好设置
      initFactSearch();      // 绑定搜索输入
      initFactCRUD();        // 绑定新增按钮点击
      initTokenInteractions(); // 绑定周期按钮 + 图表交互

      // 标签页点击处理程序
      document.querySelectorAll('.nav-tab').forEach(tab => {
        tab.addEventListener('click', () => {
          const page = tab.dataset.page;
          document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
          tab.classList.add('active');
          document.querySelectorAll('.main-content').forEach(p => p.classList.add('hidden'));
          const target = document.getElementById(`page-${page}`);
          if (target) target.classList.remove('hidden');
          if (page === 'facts') loadFacts();
          else if (page === 'tokens') loadTokenStats();
          else if (page === 'models') loadModelConfig();
        });
      });

      // 初始页面
      applyDefaultPage(localStorage.getItem('defaultPage') || 'facts');
    });
    ```
  - 精简 `index.html` 的 `<script>` 标签，替换为：
    ```html
    <script type="module" src="js/app.js"></script>
    ```
  - 从 `<style>` 中移除所有已提取到 CSS 文件的样式
  - 添加所有 `<link>` 标签（base.css、facts.css、tokens.css、models.css）
  - 确保此时 `index.html` 仅包含 HTML 骨架 + `<link>` 标签 + 模块脚本

  **绝对不能做**：
  - 不得更改 HTML 结构、ID 或类名
  - 不得在模块顶层调用依赖于 DOM 的函数

  **推荐代理配置**：
  - **类别**：`quick`
  - **技能**：[]

  **并行化**：
  - **可并行运行**：NO（需要 Task 4-6 先完成）
  - **并行组**：Wave 3（与 Task 8 顺序执行）
  - **阻塞**：Task F1-F4
  - **被阻塞**：Task 4, Task 5, Task 6

  **参考资料**：
  - `static/index.html:1516-1547` — 原始 DOMContentLoaded 初始化代码
  - `static/index.html:1567-1581` — 原始标签页点击处理程序

  **验收标准**：
  - [ ] `static/js/app.js` 存在，包含完整的初始化逻辑
  - [ ] `index.html` 中无内联 `<script>` 标签（仅 `<script type="module" src="js/app.js">`）
  - [ ] `index.html` 中无 `<style>` 标签（仅 `<link>` 标签）
  - [ ] `./server.sh restart` 后页面正常加载

  **QA 场景**：

  ```
  场景：完整拆分后页面加载无错误（正常路径）
    工具：Playwright
    步骤：
       1. 导航至 http://127.0.0.1:18742
       2. 检查控制台 — 零错误、零警告
       3. 验证所有 3 个标签页可见
       4. 验证默认显示 Facts 标签页
    预期结果：页面加载，零控制台错误，默认标签页正确
    证据：.sisyphus/evidence/task-7-full-load.png

  场景：拆分前后 DOM 快照对比
    工具：Playwright
    步骤：
       1. 加载拆分后的 index.html
       2. 捕获页面 HTML 快照
       3. 与拆分前基线进行比较（排除 <link>/<script> 标签）
       4. 验证可见 DOM 结构相同
    预期结果：DOM 结构不变
    证据：.sisyphus/evidence/task-7-dom-diff.txt
  ```

  **证据**：
  - [ ] `task-7-full-load.png`、`task-7-dom-diff.txt`

  **提交**：YES
  - 消息：`feat(frontend): 创建 app.js 入口 + 精简 index.html 为骨架`
  - 文件：`static/js/app.js`, `static/index.html`

- [x] 8. 最终集成验证 — onclick 处理器 + 跨页面回归

  **做什么**：
  - 验证所有 `onclick="..."` 处理器在拆分后仍可工作：
    - `closeModal()` — 模态框关闭按钮（所有页面）
    - `toggleTheme()` — 主题切换按钮
    - `showSettings()` — 设置齿轮按钮
    - `saveDefaultPage()` — 设置保存按钮
    - 模型页面：`showUpstreamModal()`、`saveUpstream()`、`testUpstream()`、`showModelModal()`、`saveModel()`、`showRouteModal()`、`saveRoute()`、`applyConfig()`
  - 如需修复：确保每个函数都正确挂载到 `window`
  - 运行完整回归：`python3 -m pytest test/test_frontend_e2e.py -v` — 必须全部通过
  - 运行后端回归：`python3 -m pytest test/ -q` — 必须保持 333 passed
  - 手动验证快速切换标签页（Facts → Tokens → Models → Facts）不产生竞态条件

  **绝对不能做**：
  - 不得在此时引入新功能或重构

  **推荐代理配置**：
  - **类别**：`quick`
  - **技能**：[]

  **并行化**：
  - **可并行运行**：NO（需要 Task 7 先完成）
  - **并行组**：Wave 3
  - **阻塞**：Task F1-F4
  - **被阻塞**：Task 7

  **参考资料**：
  - `static/js/core.js` — window.xxx 挂载
  - `static/js/pages/models.js` — 模型 CRUD 的 window 挂载
  - `test/test_frontend_e2e.py` — Playwright 测试套件

  **验收标准**：
  - [ ] `python3 -m pytest test/test_frontend_e2e.py -v` → 7 passed, 0 failed
  - [ ] `python3 -m pytest test/ -q` → 333 passed, 0 failed
  - [ ] 标签页快速切换无控制台错误
  - [ ] 所有 onclick 按钮功能正常（JavaScript 错误为零）

  **QA 场景**：

  ```
  场景：全量 E2E 测试通过（正常路径）
    工具：Bash
    步骤：
       1. ./server.sh restart
       2. python3 -m pytest test/test_frontend_e2e.py -v --timeout=30
       3. python3 -m pytest test/ -q
    预期结果：前端 7 passed + 后端 333 passed = 全部通过
    证据：.sisyphus/evidence/task-8-all-tests.txt

  场景：快速标签页切换无崩溃（边缘情况）
    工具：Playwright
    步骤：
       1. 快速连续点击 Facts → Tokens → Models → Facts（每次间隔 100ms）
       2. 等待最后一次切换完成（timeout: 5s）
       3. 检查控制台错误计数 === 0
       4. 验证 #page-facts 可见
    预期结果：无竞态条件，最终页面正确渲染
    证据：.sisyphus/evidence/task-8-rapid-switch.png
  ```

  **证据**：
  - [ ] `task-8-all-tests.txt`、`task-8-rapid-switch.png`

  **提交**：YES
  - 消息：`fix(frontend): 验证 onclick 处理器 + 跨页面回归测试通过`
  - 文件：无新文件（仅为验证任务）

---

## 最终验证波（强制性 — 所有实现任务之后）

> 4 个审查代理并行运行。全部必须 APPROVE。向用户呈现综合结果，并在继续之前获得明确 "okay"。
>
> **在获得用户批准之前不得自动继续。**
> **在获得用户 okay 之前，切勿将 F1-F4 标记为已完成。** 拒绝或反馈 → 修复 → 重新运行 → 再次呈现 → 等待 okay。

- [x] F1. 计划合规审计 — MUST DO 4/4 | MUST NOT 7/7 | Tasks 8/8 | VERDICT: ✅ APPROVE
- [x] F2. 代码质量审查 — Files 10 clean | Imports VALID | VERDICT: ✅ APPROVE
- [x] F3. 全量 Playwright E2E 验证 — Scenarios 8/8 pass | Integration 3/3 | VERDICT: ✅ APPROVE
- [x] F4. 范围忠实度检查 — Tasks 8/8 compliant | Contamination CLEAN | VERDICT: ✅ APPROVE

---

## 提交策略

| 提交 | 任务 | 消息 | 文件 |
|------|------|------|------|
| 1 | Task 1 | `feat(frontend): 提取 base.css — 主题变量、布局、共享组件样式` | `static/css/base.css`, `static/index.html` |
| 2 | Task 2 | `feat(frontend): 提取 core.js — 共享工具、主题、设置、事件总线、全局状态` | `static/js/core.js`, `static/index.html` |
| 3 | Task 3 | `test(frontend): 添加 Playwright E2E 冒烟测试基线（7 个测试）` | `test/test_frontend_e2e.py` |
| 4 | Task 4 | `feat(frontend): 提取 facts 模块 — CSS + JS ES 模块` | `static/css/facts.css`, `static/js/pages/facts.js`, `static/index.html` |
| 5 | Task 5 | `feat(frontend): 提取 tokens 模块 — CSS + JS（含 SVG 图表）` | `static/css/tokens.css`, `static/js/pages/tokens.js`, `static/index.html` |
| 6 | Task 6 | `feat(frontend): 提取 models 模块 — CSS + JS（含上游/模型/路由 CRUD）` | `static/css/models.css`, `static/js/pages/models.js`, `static/index.html` |
| 7 | Task 7 | `feat(frontend): 创建 app.js 入口 + 精简 index.html 为骨架` | `static/js/app.js`, `static/index.html` |
| 8 | Task 8 | `fix(frontend): 验证 onclick 处理器 + 跨页面回归测试通过` | 无新文件 |

**每个提交前**：`python3 -m pytest test/ -q`（确保零后端回归）

---

## 成功标准

### 验证命令
```bash
# 服务健康检查
./server.sh status                              # 预期：两个服务运行中

# 后端回归
python3 -m pytest test/ -q                      # 预期：333 passed, 0 failed

# 前端 E2E
python3 -m pytest test/test_frontend_e2e.py -v   # 预期：7 passed, 0 failed

# CSS 文件可访问性
curl -s http://127.0.0.1:18742/css/base.css | head -1     # 预期：CSS 内容
curl -s http://127.0.0.1:18742/css/facts.css | head -1    # 预期：CSS 内容
curl -s http://127.0.0.1:18742/css/tokens.css | head -1   # 预期：CSS 内容
curl -s http://127.0.0.1:18742/css/models.css | head -1   # 预期：CSS 内容

# JS 模块可访问性
curl -s http://127.0.0.1:18742/js/core.js | head -1       # 预期：JS 内容
curl -s http://127.0.0.1:18742/js/pages/facts.js | head -1 # 预期：JS 内容
curl -s http://127.0.0.1:18742/js/app.js | head -1        # 预期：JS 内容
```

### 最终检查清单
- [ ] 所有"必须做"已实现（5/5）
- [ ] 所有"绝对不能做"未违反（7/7）
- [ ] 所有 8 个任务已完成
- [ ] 所有 7 个 Playwright E2E 测试通过
- [ ] 333 项后端测试通过（零回归）
- [ ] `index.html` 从 2622 行减少到 ~80 行
- [ ] 新文件结构：4 个 CSS + 5 个 JS + 1 个 HTML = 10 个文件
- [ ] 服务器零改动（`server.py` 未修改）
- [ ] 零构建工具引入
