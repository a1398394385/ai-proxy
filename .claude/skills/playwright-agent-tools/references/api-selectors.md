# Hermes Data Browser — API 与 DOM 选择器参考

## DOM 选择器速查

| 目标 | CSS 选择器 |
|------|-----------|
| 导航按钮 | `.nav-tab` / `[data-page="xxx"]` |
| 当前激活页面 | `.nav-tab.active` |
| Fact 搜索框 | `#search` |
| Token 搜索框 | `#model-search` |
| Fact 容器 | `#facts-container` |
| 类别筛选按钮 | `.filter-pill` |
| 事实卡片 | `.fact-card` |
| KPI 卡片 | `.kpi-card` |
| KPI 值 | `.kpi-value` |
| 趋势图表 | `#trend-chart` |
| 图例项 | `.legend-item` |
| 模型表格 | `#model-table` |
| 上游表格 | `#upstream-table` |
| Drawer 行 | `tr.drawer-row` |
| 路由表格 | `#route-table` |
| 请求类型 Tab | `.proxy-tab` |
| SQL 编辑器 | `#sql-editor` |
| 执行查询按钮 | `#execute-sql` |
| 查询结果 | `#query-result` / `.dbquery-table` |
| 模态框遮罩 | `#modal-overlay` |
| 模态框标题 | `#modal-title` |
| 模态框内容 | `#modal-body` |
| 模态框底部 | `#modal-footer` |
| 配置状态栏 | `#config-status` |
| 主题按钮 | `#theme-toggle` |
| 设置按钮 | `#settings-btn` |
| 周期按钮 | `.period-btn` |

## 全局变量

通过 `browser_evaluate({ function: '() => window.xxx' })` 读取。

| 变量 | 类型 | 含义 |
|------|------|------|
| `window.currentPage` | string | 当前页面（facts/tokens/models/routes/dbquery） |
| `window.currentPeriod` | string | 当前周期（day/week/month） |
| `window.allFacts` | Array | 所有事实数据 |
| `window.allModels` | Array | 所有模型数据 |
| `window.editingId` | number\|null | 当前编辑的 ID |

## API 端点

### Fact Store

| 端点 | 方法 | 用途 |
|------|------|------|
| `/api/facts` | GET | 获取所有事实 |
| `/api/facts?q=xxx` | GET | 搜索事实 |
| `/api/facts?category=xxx` | GET | 按类别筛选 |
| `/api/categories` | GET | 获取类别列表 |
| `/api/facts` | POST | 新增事实 `{ content, category, tags, trust_score }` |
| `/api/facts/:id` | PUT | 编辑事实 |
| `/api/facts/:id` | DELETE | 删除事实 |

### Token 统计

| 端点 | 方法 | 用途 |
|------|------|------|
| `/api/token_stats?period=week` | GET | 汇总统计 |
| `/api/token_stats/by_model?period=week` | GET | 按模型统计 |
| `/api/token_stats/trend?period=week` | GET | 趋势数据 |

period 可选值：`day` / `week` / `month`

### 模型管理

| 端点 | 方法 | 用途 |
|------|------|------|
| `/api/upstreams` | GET | 获取所有上游 |
| `/api/upstreams` | POST | 新增上游 `{ id, base_url, api_key, timeout, ... }` |
| `/api/upstreams/:id` | PUT | 编辑上游 |
| `/api/upstreams/:id` | DELETE | 禁用上游 |
| `/api/upstreams/:id/test` | POST | 测试连通性 |
| `/api/upstreams/:id/detect-models` | POST | 检测模型 |
| `/api/upstreams/:id/models/bulk` | POST | 批量添加模型 `{ models: [{ name, multimodal }] }` |
| `/api/models` | GET | 获取所有模型 |
| `/api/models?upstream_id=xxx` | GET | 按上游筛选 |
| `/api/models` | POST | 新增模型 `{ name, upstream_id, multimodal }` |
| `/api/models/:id` | PUT | 编辑模型 |
| `/api/models/:id` | DELETE | 删除模型 |

### 路由映射

| 端点 | 方法 | 用途 |
|------|------|------|
| `/api/routes` | GET | 获取所有路由 |
| `/api/routes?request_type=xxx` | GET | 按请求类型筛选 |
| `/api/routes` | POST | 新增路由 `{ source, target_model_id, request_type }` |
| `/api/routes/:id` | PUT | 编辑路由 |
| `/api/routes/:id` | DELETE | 删除路由 |

request_type 可选值：`responses` / `messages` / `chat_completions`

### 其他

| 端点 | 方法 | 用途 |
|------|------|------|
| `/api/config/status` | GET | 配置状态（proxy 在线/离线、上游/模型/路由计数） |
| `/api/db/query` | POST | SQL 查询 `{ sql }` |
| `/admin/reload` | POST | 刷新配置缓存 |

## 常用 evaluate 代码片段

### 读取页面状态

```javascript
() => ({
  page: window.currentPage,
  period: window.currentPeriod,
  theme: document.documentElement.getAttribute('data-theme')
})
```

### 读取 KPI 数值

```javascript
() => Array.from(document.querySelectorAll('.kpi-card')).map(c => {
  const label = c.querySelector('.kpi-label')?.textContent;
  const value = c.querySelector('.kpi-value')?.textContent;
  return { label, value };
})
```

### 读取模型表格

```javascript
() => Array.from(document.querySelectorAll('#model-table tbody tr')).map(r => {
  const cells = r.querySelectorAll('td');
  return {
    model: cells[0]?.textContent.trim(),
    requests: cells[1]?.textContent.trim(),
    total: cells[6]?.textContent.trim()
  };
})
```

### 读取数据库查询结果

```javascript
() => {
  const table = document.querySelector('.dbquery-table');
  if (!table) return null;
  return Array.from(table.querySelectorAll('tr')).map(r =>
    Array.from(r.cells).map(c => c.textContent)
  );
}
```

### 拦截 confirm 弹窗

```javascript
() => { window.__originalConfirm = window.confirm; window.confirm = () => true; }
```

### 恢复 confirm 弹窗

```javascript
() => { if (window.__originalConfirm) window.confirm = window.__originalConfirm; }
```

## 上游表单字段 ID

新增/编辑上游模态框中的输入框 ID：

| 字段 | ID | 类型 |
|------|-----|------|
| 名称 (ID) | `#up-id` | text (编辑时 readonly) |
| Base URL | `#up-url` | text |
| API Key | `#up-key` | text |
| 响应超时 | `#up-timeout` | number |
| 连接超时 | `#up-conn-timeout` | number |
| SSL | `#up-ssl` | select (1=开启, 0=关闭) |
| 重试 | `#up-retry` | number |
| 请求格式 | `#up-format` | select (chat_completions/responses/messages) |

## 模型表单字段 ID

| 字段 | ID | 类型 |
|------|-----|------|
| 模型名 | `#m-name` | text |
| 所属上游 | `#m-upstream` | select / hidden |
| Multimodal | `#m-multimodal` | select (1=支持, 0=不支持) |

## 路由表单字段 ID

| 字段 | ID | 类型 |
|------|-----|------|
| 源模型名 | `#r-source` | text (fallback 时 hidden) |
| 目标模型 | `#r-target` | select |
| 请求类型 | `#r-proxy` | hidden |

## Fact 表单字段 ID

| 字段 | ID | 类型 |
|------|-----|------|
| 内容 | `#m-content` | textarea |
| 类别 | `#m-category` | select (general/project/tool/user_pref) |
| 标签 | `#m-tags` | text |
| 信任度 | `#m-trust` | number (0-1, step 0.1) |
| 实体 | `#m-entities` | text (仅新增时) |

## 设置模态框字段 ID

| 字段 | ID | 类型 |
|------|-----|------|
| 默认页面 | `#modal-default-page-select` | select |
| 默认周期 | `#modal-default-period-select` | select |
