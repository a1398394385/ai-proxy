# Token 统计页面 Sub-tab 布局重构

**日期**: 2026-05-12
**状态**: 已批准

## 背景

当前 Token 统计页面的三联按钮（按模型统计 / 请求日志 / 按上游统计）切换时，整个页面内容（含 KPI 卡片和趋势图表）都会被替换。用户希望在切换表格视图时，KPI 和图表始终可见。

## 目标

将 sub-tab 的切换范围从"整个页面"缩小到"仅表格区域"，KPI 卡片和趋势图表始终可见。

## 设计

### HTML 结构变更

**当前结构** (`index.html`):

```
page-tokens
├── toolbar (周期 + 搜索模型 + 刷新)
├── subtab-nav
├── #subtab-models       ← 包含 KPI + 图表 + 模型表格
├── #subtab-requests     ← 隐藏（请求日志）
└── #subtab-upstream     ← 隐藏（上游统计）
```

**改动后**:

```
page-tokens
├── toolbar (周期 + 🔍搜索模型* + 刷新)  ← *仅"按模型统计"tab 时显示
├── #kpi-container                       ← 提取到顶层，始终可见
├── .chart-card                          ← 提取到顶层，始终可见
├── .subtab-nav                          ← 移到图表下方
├── #subtab-models (仅含模型表格)        ← 切换区域
├── #subtab-requests                     ← 切换区域
└── #subtab-upstream                     ← 切换区域
```

具体操作：
1. 将 `#kpi-container` 和 `.chart-card`（含 SVG 图表、图例、tooltip）从 `#subtab-models` 内移出到 `#page-tokens` 顶层
2. `#subtab-models` 只保留 `.table-card`（模型统计表格）
3. `.subtab-nav` 从 toolbar 下方移到 `.chart-card` 下方
4. 给搜索框的 `.search-box` 容器加 `id="model-search-box"`，方便 JS 控制显隐

### JS 逻辑变更

#### `initSubTabs()` 修改

切换逻辑基本不变（仍通过 `display:none` 切换三个 subtab div），新增搜索框显隐控制：
- `models` tab → 显示 `#model-search-box`
- `requests` / `upstream` tab → 隐藏 `#model-search-box`

#### 周期切换回调

`initTokenPage()` 中 period-btn 点击回调不变。`loadTokenStats()` 刷新 KPI + 图表 + 模型表格，各 sub-tab 的数据加载各自独立。

#### 请求日志 / 上游统计渲染

`renderRequestTable()` 和 `renderUpstreamTable()` 的目标容器 `#subtab-requests` / `#subtab-upstream` 仍存在，无需改动。

### CSS 变更

- `.subtab-nav` 增加 `margin-top: 24px`，与图表区域拉开间距
- 无新增 CSS 类

### 不改动的部分

- 后端 API — 无变更
- KPI 渲染、图表渲染、模型表格渲染逻辑 — 无变更
- 请求日志和上游统计的数据加载/渲染 — 无变更
- `stats_service.py` — 无变更

## 改动范围

| 文件 | 改动 |
|------|------|
| `static/index.html` | 重组 `#page-tokens` 内元素顺序，给搜索框容器加 id |
| `static/js/pages/tokens.js` | 修改 `initSubTabs()` 增加搜索框显隐逻辑 |
| `static/css/tokens.css` | `.subtab-nav` 加 `margin-top` |

## 验证方式

1. 切换周期 → KPI + 图表 + 当前活跃表格均刷新
2. 切换 sub-tab → 仅表格区域切换，KPI 和图表不变
3. "按模型统计" tab 下搜索模型 → 表格筛选正常
4. 切到"请求日志"/"按上游统计" → 搜索框消失
5. 切回"按模型统计" → 搜索框恢复
6. 点击模型行展开详情 → 正常展开/收起
7. 请求日志筛选和分页 → 正常工作
8. 运行 `python3 -m pytest test/ -q` → 全部通过
