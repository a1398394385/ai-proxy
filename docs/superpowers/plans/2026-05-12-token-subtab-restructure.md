# Token 统计页面 Sub-tab 布局重构 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Token 统计页面的 sub-tab 切换范围从整个页面缩小到仅表格区域，KPI 卡片和趋势图表始终可见。

**Architecture:** HTML 重组 — 将 KPI 和图表从 `#subtab-models` 内提取到 `#page-tokens` 顶层，sub-tab 导航移到图表下方，JS 增加搜索框显隐控制，CSS 调整 margin。

**Tech Stack:** 纯前端（HTML + JS + CSS），无后端改动

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `static/index.html:64-199` | 修改 | 重组 `#page-tokens` 内元素顺序 |
| `static/js/pages/tokens.js:595-616` | 修改 | `initSubTabs()` 增加搜索框显隐 |
| `static/css/tokens.css:611-620` | 修改 | `.subtab-nav` margin 调整 |

---

### Task 1: HTML 结构重组

**Files:**
- Modify: `static/index.html:64-199`

- [ ] **Step 1: 给搜索框容器加 id，并将 KPI、图表、subtab-nav、表格重组**

将 `index.html` 第 64-199 行的 `#page-tokens` 整体替换为以下结构。核心变化：
1. `.search-box` 加 `id="model-search-box"`
2. `.subtab-nav` 从 toolbar 下方移到图表下方
3. `#kpi-container` 和 `.chart-card` 从 `#subtab-models` 内提取到 `#page-tokens` 顶层
4. `#subtab-models` 只包含模型统计表格

```html
  <div id="page-tokens" class="main-content hidden">
    <!-- 周期切换 -->
    <div class="toolbar">
      <div class="toolbar-group">
        <button class="toolbar-btn period-btn" data-period="day">24小时</button>
        <button class="toolbar-btn period-btn active" data-period="week">7天</button>
        <button class="toolbar-btn period-btn" data-period="month">30天</button>
      </div>
      <div class="search-box" id="model-search-box">
        <input type="text" id="model-search" placeholder="搜索模型...">
      </div>
      <button class="btn btn-secondary" id="refresh-token" style="margin-left:auto">
        <span>🔄</span> 刷新
      </button>
    </div>

    <!-- KPI 卡片 -->
    <div class="kpi-grid" id="kpi-container">
      <!-- 动态填充 -->
    </div>

    <!-- 图表 -->
    <div class="chart-card">
      <div class="chart-header">
        <span class="chart-title">📈 使用趋势</span>
        <span style="font-size:12px;color:hsl(var(--muted-foreground))" id="chart-period-label">过去 7 天</span>
      </div>
      <div class="chart-container" id="chart-wrapper">
        <svg class="area-chart" id="trend-chart" preserveAspectRatio="none">
          <defs>
            <linearGradient id="gradientInput" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stop-color="#3b82f6" stop-opacity="0.3"/>
              <stop offset="95%" stop-color="#3b82f6" stop-opacity="0"/>
            </linearGradient>
            <linearGradient id="gradientOutput" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stop-color="#22c55e" stop-opacity="0.3"/>
              <stop offset="95%" stop-color="#22c55e" stop-opacity="0"/>
            </linearGradient>
            <linearGradient id="gradientCacheRead" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stop-color="#a855f7" stop-opacity="0.3"/>
              <stop offset="95%" stop-color="#a855f7" stop-opacity="0"/>
            </linearGradient>
            <linearGradient id="gradientCacheWrite" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stop-color="#f97316" stop-opacity="0.3"/>
              <stop offset="95%" stop-color="#f97316" stop-opacity="0"/>
            </linearGradient>
          </defs>
          <!-- 网格线 -->
          <g id="chart-grid"></g>
          <!-- 路径 -->
          <g id="chart-areas"></g>
          <!-- 坐标轴 -->
          <g id="chart-axes"></g>
          <!-- 鼠标移动竖线 -->
          <line id="chart-cursor-line" x1="0" y1="0" x2="0" y2="100%" stroke="hsl(var(--muted-foreground) / 0.5)" stroke-width="1" stroke-dasharray="4 4" style="display:none; pointer-events:none;"/>
          <!-- 交互区域 -->
          <rect id="chart-overlay" width="100%" height="100%" fill="transparent"/>
        </svg>
        <!-- Tooltip -->
        <div class="chart-tooltip" id="chart-tooltip">
          <div class="tooltip-title" id="tooltip-title"></div>
          <div id="tooltip-content"></div>
        </div>
      </div>
      <!-- 图例 -->
      <div class="chart-legend" id="chart-legend">
        <div class="legend-item" data-series="inputTokens">
          <div class="legend-dot input"></div>
          <span>输入 Tokens</span>
        </div>
        <div class="legend-item" data-series="outputTokens">
          <div class="legend-dot output"></div>
          <span>输出 Tokens</span>
        </div>
        <div class="legend-item" data-series="cacheReadTokens">
          <div class="legend-dot cache-read"></div>
          <span>缓存读取</span>
        </div>
        <div class="legend-item" data-series="cacheWriteTokens">
          <div class="legend-dot cache-write"></div>
          <span>缓存写入</span>
        </div>
        <div class="legend-item" data-series="costLine">
          <div class="legend-dot" style="background:#f43f5e"></div>
          <span>估算成本 (右轴 ¥)</span>
        </div>
      </div>
    </div>

    <!-- Sub-tab 导航 -->
    <div class="subtab-nav">
      <button class="sub-tab-btn active" data-subtab="models">📊 按模型统计</button>
      <button class="sub-tab-btn" data-subtab="requests">📋 请求日志</button>
      <button class="sub-tab-btn" data-subtab="upstream">🏢 按上游统计</button>
    </div>

    <!-- sub-tab: 按模型统计（默认激活） -->
    <div id="subtab-models">
      <!-- 模型统计表格 -->
      <div class="table-card">
        <div class="table-header">
          <span class="table-title">🧮 按模型统计</span>
          <span style="font-size:12px;color:hsl(var(--muted-foreground))" id="model-count"></span>
        </div>
        <div class="table-scroll">
          <table id="model-table">
            <thead>
              <tr>
                <th style="width:200px">模型</th>
                <th style="width:70px">请求</th>
                <th style="width:90px"><span style="display:inline-flex;align-items:center;gap:4px"><span class="color-dot" style="width:6px;height:6px;border-radius:50%;background:hsl(var(--blue));display:inline-block"></span>Input</span></th>
                <th style="width:80px"><span style="display:inline-flex;align-items:center;gap:4px"><span class="color-dot" style="width:6px;height:6px;border-radius:50%;background:hsl(var(--green));display:inline-block"></span>Output</span></th>
                <th style="width:90px"><span style="display:inline-flex;align-items:center;gap:4px"><span class="color-dot" style="width:6px;height:6px;border-radius:50%;background:hsl(var(--purple));display:inline-block"></span>Cache Read</span></th>
                <th style="width:90px"><span style="display:inline-flex;align-items:center;gap:4px"><span class="color-dot" style="width:6px;height:6px;border-radius:50%;background:hsl(var(--orange));display:inline-block"></span>Cache Create</span></th>
                <th style="width:90px">总计</th>
                <th style="width:150px">占比</th>
                <th style="width:80px">成本</th>
              </tr>
            </thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- sub-tab: 请求日志 -->
    <div id="subtab-requests" style="display:none">
      <!-- 筛选器栏 + 请求日志表格 + 分页器容器 -->
    </div>

    <!-- sub-tab: 按上游统计 -->
    <div id="subtab-upstream" style="display:none">
      <!-- 上游统计表格容器 -->
    </div>
  </div>
```

- [ ] **Step 2: 运行后端测试确认无破坏**

Run: `python3 -m pytest test/ -q`
Expected: 全部通过（后端不涉及前端改动）

- [ ] **Step 3: 提交**

```bash
git add static/index.html
git commit -m "refactor: 重组 Token 页面 HTML 结构，KPI/图表提取到顶层"
```

---

### Task 2: CSS margin 调整

**Files:**
- Modify: `static/css/tokens.css:611-620`

- [ ] **Step 1: 修改 `.subtab-nav` margin**

将 `tokens.css` 第 615 行的 `margin-bottom: 16px;` 替换为：

```css
  margin: 24px 0 16px 0;
```

完整的 `.subtab-nav` 块变为：

```css
.subtab-nav {
  display: flex;
  gap: 4px;
  margin: 24px 0 16px 0;
  padding: 4px;
  background: hsl(var(--secondary) / 0.5);
  border-radius: var(--radius);
  width: fit-content;
}
```

- [ ] **Step 2: 提交**

```bash
git add static/css/tokens.css
git commit -m "style: 调整 subtab-nav margin 适配新位置"
```

---

### Task 3: JS sub-tab 切换逻辑修改

**Files:**
- Modify: `static/js/pages/tokens.js:595-616`

- [ ] **Step 1: 修改 `initSubTabs()` 增加搜索框显隐**

将 `tokens.js` 第 595-616 行的 `initSubTabs()` 函数替换为：

```javascript
function initSubTabs() {
  document.querySelectorAll('.sub-tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.sub-tab-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');

      const subtabName = btn.dataset.subtab;
      ['models', 'requests', 'upstream'].forEach(name => {
        const el = document.getElementById(`subtab-${name}`);
        if (el) el.style.display = name === subtabName ? '' : 'none';
      });

      const searchBox = document.getElementById('model-search-box');
      if (searchBox) searchBox.style.display = subtabName === 'models' ? '' : 'none';

      if (subtabName === 'requests') {
        requestFilters.period = window.currentPeriod || 'week';
        requestPagination.offset = 0;
        loadRequestLog();
      } else if (subtabName === 'upstream') {
        loadUpstreamStats();
      }
    });
  });
}
```

与原函数的区别：新增第 606-607 行（搜索框显隐控制），其余逻辑不变。

- [ ] **Step 2: 运行后端测试确认无破坏**

Run: `python3 -m pytest test/ -q`
Expected: 全部通过

- [ ] **Step 3: 提交**

```bash
git add static/js/pages/tokens.js
git commit -m "feat: sub-tab 切换时控制模型搜索框显隐"
```

---

### Task 4: UI 验证

**Files:** 无代码改动，纯验证

- [ ] **Step 1: 重启服务**

```bash
./server.sh restart
```

- [ ] **Step 2: 用 Playwright MCP 打开页面验证**

打开 `http://localhost:18742`，切换到 Token 统计页面，逐项验证：

1. 页面首次加载 → KPI 卡片 + 图表 + 模型表格均可见，搜索框可见
2. 切换周期（24小时/7天/30天）→ KPI + 图表 + 当前表格均刷新
3. 切换到"请求日志" → 仅表格区域变为请求日志，KPI 和图表不变，搜索框消失
4. 切换到"按上游统计" → 仅表格区域变为上游统计，KPI 和图表不变，搜索框消失
5. 切回"按模型统计" → 表格恢复，搜索框恢复，之前输入的搜索词保留
6. 在"按模型统计"输入搜索词 → 表格筛选正常
7. 点击模型行展开详情 → 正常展开/收起
8. 请求日志筛选和分页 → 正常工作

- [ ] **Step 3: 最终确认 — 运行全量测试**

Run: `python3 -m pytest test/ -q`
Expected: 全部通过
