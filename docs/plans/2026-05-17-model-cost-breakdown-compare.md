# 按模型成本明细 + 对比计费 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在模型行展开区域的请求记录上方，添加 4 项成本分项横条，并支持选择对比模型即时对比成本。

**Architecture:** 纯前端实现，0 后端改动。页面初始化时并发拉取 `/api/pricing` 缓存到 `allPricings`，展开行时用已有 token 数 × 单价前端计算 4 项成本并渲染。对比模型切换即时计算，无网络请求。

**Tech Stack:** Vanilla JavaScript ES Module。唯一改动文件：`static/js/pages/tokens.js`。

**Spec:** `docs/specs/2026-05-17-model-cost-breakdown-compare-design.md`

---

## File Structure

| 文件 | 变更 | 说明 |
|------|------|------|
| `static/js/pages/tokens.js` | Modify | 添加 `allPricings` 状态、`findPricing`、`calcCost`、`renderCostBar` 函数，修改 `loadTokenStats` 和 `expandModelRow` |

---

### Task 1：添加 allPricings 状态 + 并发加载

**Files:**
- Modify: `static/js/pages/tokens.js`（模块状态区 + `loadTokenStats`）

- [ ] **Step 1：在模块变量区添加 allPricings**

在第 13 行（`let upstreamStatsData = [];` 之后）插入：

```javascript
let allPricings = [];
```

- [ ] **Step 2：修改 loadTokenStats 并发拉取 /api/pricing**

将 `loadTokenStats` 的 `Promise.all` 从三项改为四项，失败时优雅降级：

```javascript
async function loadTokenStats() {
  const period = window.currentPeriod || 'week';
  const [stats, byModel, trend, pricingRes] = await Promise.all([
    api(`/api/token_stats?period=${period}`),
    api(`/api/token_stats/by_model?period=${period}`),
    api(`/api/token_stats/trend?period=${period}`),
    api(`/api/pricing`).catch(() => ({ pricings: [] })),
  ]);

  allModels = byModel.models || [];
  allPricings = pricingRes.pricings || [];   // 新增：缓存全量计费单价
  // 以下原有赋值和渲染调用保持不变（periodLabels、renderKPI、renderTrendChart、renderModelTable）
```

注意：`api()` 对非 200 响应会 throw，`.catch()` 能捕获并返回 resolved Promise `{ pricings: [] }`，降级有效。

- [ ] **Step 3：重启服务，Network 面板确认 /api/pricing 已请求**

```bash
./server.sh restart
```

打开 http://localhost:18742 → Tokens 页 → DevTools Network 面板，确认：
- `/api/pricing` 有 200 响应
- Response 格式：`{"pricings": [{"model_id": "...", "display_name": "...", "input_cost_per_million": ..., "currency": "USD", "multiplier": "1.0", ...}]}`

- [ ] **Step 4：Commit**

```bash
git add static/js/pages/tokens.js
git commit -m "feat(tokens): 并发拉取 /api/pricing 到 allPricings 模块变量"
```

---

### Task 2：实现 findPricing 和 calcCost 纯函数

**Files:**
- Modify: `static/js/pages/tokens.js`（在 `renderModelTable` 函数定义之前插入）

- [ ] **Step 1：插入 findPricing 和 calcCost**

在 `function renderModelTable(models) {` 之前插入：

```javascript
// ─── 成本计算 ───

function findPricing(modelName) {
  const key = (modelName || '').toLowerCase();
  return allPricings.find(p => (p.model_id || '').toLowerCase() === key) || null;
}

function calcCost(modelData, pricingEntry) {
  const rate = pricingEntry.currency === 'USD' ? 7 : 1;
  const mult = parseFloat(pricingEntry.multiplier || '1.0');
  const M = 1_000_000;
  const input   = (modelData.input_tokens        || 0) / M * pricingEntry.input_cost_per_million          * rate * mult;
  const output  = (modelData.output_tokens       || 0) / M * pricingEntry.output_cost_per_million         * rate * mult;
  const cacheRd = (modelData.cache_read_tokens   || 0) / M * pricingEntry.cache_read_cost_per_million     * rate * mult;
  const cacheWr = (modelData.cache_write_tokens  || 0) / M * pricingEntry.cache_creation_cost_per_million * rate * mult;
  const r = v => Math.round(v * 1e6) / 1e6;
  return { input: r(input), output: r(output), cacheRead: r(cacheRd), cacheWrite: r(cacheWr), total: r(input + output + cacheRd + cacheWr) };
}
```

- [ ] **Step 2：DevTools Console 手工验证 calcCost**

页面加载后，在 DevTools Sources 对 tokens.js 打断点，或在 expandModelRow 入口临时插入 `console.log`，验证：

若模型 `input_tokens=1000, output_tokens=1000`，pricing `input_cost_per_million=15, output_cost_per_million=75, currency="USD", multiplier="1.0"`：

```
期望：input=0.000105, output=0.000525, cacheRead=0, cacheWrite=0, total=0.00063
计算：1000/1_000_000 × 15 × 7 × 1 = 0.000105 ✓
```

- [ ] **Step 3：Commit**

```bash
git add static/js/pages/tokens.js
git commit -m "feat(tokens): 添加 findPricing + calcCost 纯函数（含 multiplier 和大小写匹配）"
```

---

### Task 3：实现 renderCostBar

**Files:**
- Modify: `static/js/pages/tokens.js`（在 `expandModelRow` 定义之前插入）

- [ ] **Step 1：插入 renderCostBar 函数**

在 `// ===== 展开/收起模型行 =====` 注释之前插入：

```javascript
// ─── 成本明细条 ───

function renderCostBar(modelName, detailContent) {
  const modelData = allModels.find(m => m.model === modelName);
  if (!modelData) return;

  const period = window.currentPeriod || 'week';
  const periodLabel = { day: '最近 24 小时', week: '最近 7 天', month: '最近 30 天' }[period] || '最近 7 天';

  const wrap = document.createElement('div');
  const pricing = findPricing(modelName);

  if (!pricing) {
    wrap.innerHTML = `
      <div style="padding:8px 12px;border-bottom:1px solid hsl(var(--border));font-family:monospace;font-size:11px;color:hsl(var(--muted-foreground))">
        成本明细 — ${periodLabel} · 未配置计费，成本按 ¥0 计算
      </div>`;
    detailContent.insertBefore(wrap, detailContent.firstChild);
    return;
  }

  const c = calcCost(modelData, pricing);

  wrap.innerHTML = `
    <div style="padding:8px 12px;border-bottom:1px solid hsl(var(--border));font-family:monospace;font-size:11px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
        <span style="color:hsl(var(--muted-foreground))">成本明细 — ${periodLabel}</span>
        <span>合计 <span style="color:#f43f5e">¥${c.total.toFixed(6)}</span></span>
      </div>
      <div style="display:flex;gap:16px">
        <span><span style="color:#3b82f6">In</span> ¥${c.input.toFixed(6)}</span>
        <span><span style="color:#22c55e">Out</span> ¥${c.output.toFixed(6)}</span>
        <span><span style="color:#a855f7">Cache Rd</span> ¥${c.cacheRead.toFixed(6)}</span>
        <span><span style="color:#f97316">Cache Wr</span> ¥${c.cacheWrite.toFixed(6)}</span>
      </div>
    </div>
    <div style="padding:4px 12px;border-bottom:1px solid hsl(var(--border));display:flex;align-items:center;gap:8px;font-size:11px">
      <span style="color:hsl(var(--muted-foreground))">套用计费:</span>
      <select class="cost-bar-compare-select" style="background:hsl(var(--background));color:hsl(var(--foreground));border:1px solid hsl(var(--border));border-radius:4px;font-size:10px;padding:1px 6px">
        <option value="">— 不对比 —</option>
        ${allPricings.map(p => `<option value="${escHtml(p.model_id)}">${escHtml(p.display_name || p.model_id)}</option>`).join('')}
      </select>
    </div>
    <div class="cost-bar-compare" style="display:none;padding:8px 12px;border-bottom:1px solid hsl(var(--border));border-left:2px solid #f97316;font-family:monospace;font-size:11px"></div>`;

  const sel = wrap.querySelector('.cost-bar-compare-select');
  const compareDiv = wrap.querySelector('.cost-bar-compare');

  sel.addEventListener('change', () => {
    const compareId = sel.value;
    if (!compareId) { compareDiv.style.display = 'none'; return; }
    const cp = allPricings.find(p => p.model_id === compareId);
    if (!cp) return;

    const cc = calcCost(modelData, cp);
    const delta = c.total > 0 ? ((cc.total - c.total) / c.total * 100) : 0;
    const deltaHtml = delta >= 0
      ? `<span style="color:#f43f5e">+${delta.toFixed(0)}%</span>`
      : `<span style="color:#22c55e">${delta.toFixed(0)}%</span>`;

    compareDiv.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
        <span style="color:hsl(var(--muted-foreground))">套用：${escHtml(cp.display_name || cp.model_id)}</span>
        <span>合计 <span style="color:#f97316">¥${cc.total.toFixed(6)}</span> ${deltaHtml}</span>
      </div>
      <div style="display:flex;gap:16px">
        <span><span style="color:#3b82f6">In</span> ¥${cc.input.toFixed(6)}</span>
        <span><span style="color:#22c55e">Out</span> ¥${cc.output.toFixed(6)}</span>
        <span><span style="color:#a855f7">Cache Rd</span> ¥${cc.cacheRead.toFixed(6)}</span>
        <span><span style="color:#f97316">Cache Wr</span> ¥${cc.cacheWrite.toFixed(6)}</span>
      </div>`;
    compareDiv.style.display = 'block';
  });

  detailContent.insertBefore(wrap, detailContent.firstChild);
}
```

- [ ] **Step 2：Commit（函数已定义但未接入，不影响现有功能）**

```bash
git add static/js/pages/tokens.js
git commit -m "feat(tokens): 添加 renderCostBar 成本明细+对比渲染函数"
```

---

### Task 4：接入 expandModelRow

**Files:**
- Modify: `static/js/pages/tokens.js`（`expandModelRow` 内正常数据路径）

- [ ] **Step 1：将 expandModelRow 的三个分支统一改为 detail-content 包装结构**

当前 `expandModelRow` 有三条路径（空数据 early return、成功、catch 错误），成本条来自 `allModels`（页面加载时已有），与请求列表无关，必须在三条路径都调用 `renderCostBar`。

**修改方案**：将空数据分支和错误分支也改用 `.detail-content` 包装，然后在公共位置调用 `renderCostBar`，再 append 各自内容。

将 `expandModelRow` 整个函数替换为：

```javascript
function expandModelRow(model, rowElement) {
  if (rowElement.nextSibling && rowElement.nextSibling.classList && rowElement.nextSibling.classList.contains('model-detail-row')) {
    collapseModelRow(rowElement);
    return;
  }

  const period = window.currentPeriod || 'week';
  const encodedModel = encodeURIComponent(model);
  api(`/api/token_stats/by_model/${encodedModel}/requests?period=${period}&limit=50`)
    .then(data => {
      const requests = data.requests || [];
      const limit = Math.min(requests.length, 50);

      const tr = document.createElement('tr');
      tr.className = 'model-detail-row';
      tr.innerHTML = `<td colspan="10" class="detail-content"></td>`;
      rowElement.after(tr);
      const detailContent = tr.querySelector('.detail-content');
      renderCostBar(model, detailContent);
      rowElement.classList.add('expanded');

      if (!requests.length) {
        detailContent.insertAdjacentHTML('beforeend',
          `<div class="empty-state" style="padding:12px">暂无详细请求记录</div>`);
        return;
      }

      const rows = requests.slice(0, limit).map(r => {
        const typeBadge = r.upstream_id === 'hermes'
          ? '<span class="type-badge-term hermes">[hermes]</span>'
          : r.upstream_id === 'opencode'
            ? '<span class="type-badge-term opencode">[opencode]</span>'
            : '<span class="type-badge-term proxy">>_proxy</span>';
        const timeStr = r.created_at || r.request_ts || r.timestamp || '-';
        const costStr = ((r.input_cost_cny || 0) + (r.output_cost_cny || 0) + (r.cache_read_cost_cny || 0) + (r.cache_write_cost_cny || 0)).toFixed(6);

        const tokenCells = `<td class="cell-number">${formatTokens(r.input_tokens || 0)}</td>
             <td class="cell-number">${formatTokens(r.output_tokens || 0)}</td>
             <td class="cell-number">${formatTokens(r.cache_read_tokens || 0)}</td>
             <td class="cell-number">${formatTokens(r.cache_write_tokens || 0)}</td>
             <td class="cell-total">${formatTokens((r.input_tokens || 0) + (r.output_tokens || 0) + (r.cache_read_tokens || 0) + (r.cache_write_tokens || 0))}</td>
             <td class="cell-number">${r.duration_ms ? (r.duration_ms / 1000).toFixed(2) + 's' : '-'}</td>
             <td class="cell-cost"><span class="cost-badge">¥${costStr}</span></td>`;

        return `<tr class="detail-row">
          <td class="cell-detail-id">${escHtml(r.request_id || r.id || '-')}</td>
          <td class="cell-number">${escHtml(timeStr)}</td>
          <td>${typeBadge}</td>
          ${tokenCells}
        </tr>`;
      }).join('');

      detailContent.insertAdjacentHTML('beforeend', `
        <div class="detail-header">
          <span class="detail-model">${escHtml(model)}</span>
          <span class="detail-count">最近 ${requests.length} 条记录</span>
        </div>
        <table class="detail-table">
          <thead>
            <tr>
              <th>请求ID</th><th>时间</th><th>类型</th><th>Input</th><th>Output</th>
              <th>Cache Read</th><th>Cache Create</th><th>总Token</th><th>耗时</th><th>成本</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>`);
    })
    .catch(err => {
      const tr = document.createElement('tr');
      tr.className = 'model-detail-row';
      tr.innerHTML = `<td colspan="10" class="detail-content"></td>`;
      rowElement.after(tr);
      const detailContent = tr.querySelector('.detail-content');
      renderCostBar(model, detailContent);
      rowElement.classList.add('expanded');
      detailContent.insertAdjacentHTML('beforeend',
        `<div class="empty-state" style="padding:12px">加载失败: ${escHtml(err.message)}</div>`);
    });
}
```

- [ ] **Step 2：重启并手动验证**

```bash
./server.sh restart
```

打开 http://localhost:18742 → Tokens 页，逐一验证：

1. 点击有数据的模型行 → 展开后可见"成本明细 — 最近 X 天"横条在请求记录表格上方
2. 下拉框选择一个对比模型 → 出现第二条（橙色左边框）显示对比成本和差额百分比
3. 下拉框切回"— 不对比 —" → 对比行消失
4. 收起再展开 → 对比行不保留（符合"不持久化"设计）
5. 切换 period（7天→30天）→ 收起再展开 → 成本条 period 标签更新，数值与新 period 对应
6. 若存在无定价的模型 → 成本条显示"未配置计费，成本按 ¥0 计算"
7. 点击当前 period 内无请求记录的模型行 → 仍显示成本条，下方显示"暂无详细请求记录"
8. 在 DevTools Network 中 block 某个请求接口（模拟加载失败）→ 仍显示成本条，下方显示"加载失败: ..."

- [ ] **Step 3：确认 allPricings 为空时下拉框不渲染**

在 DevTools Network 面板中 block `/api/pricing`（右键 → Block request URL），刷新页面，展开模型行 → 成本条仍显示（显示 ¥0），下拉框不出现（`allPricings.length === 0`，`map` 生成空 options 仅有"— 不对比 —"）。

- [ ] **Step 4：运行全量测试确认无回归**

```bash
python3 -m pytest test/ -q
```

预期：所有现有测试通过（本功能 0 后端改动）。

- [ ] **Step 5：Commit**

```bash
git add static/js/pages/tokens.js
git commit -m "feat(tokens): 接入 renderCostBar — 模型展开显示成本明细+对比计费"
```

---

## 自检

**Spec 覆盖：**
- ✅ 成本明细条（4项分项，period全量）→ Task 3
- ✅ 对比计费行（下拉框选择后出现，差额%）→ Task 3
- ✅ multiplier 在公式中 → Task 2
- ✅ 大小写匹配 → Task 2（`findPricing` 用 `.toLowerCase()`）
- ✅ display_name 优先 → Task 3（`p.display_name || p.model_id`）
- ✅ 0 后端改动 → 全计划无后端文件
- ✅ /api/pricing 失败降级 → Task 1（`.catch(() => ({pricings:[]}))`，api() throw 可被捕获）
- ✅ 无定价模型处理 → Task 3（`if (!pricing)` 分支）
- ✅ 空数据分支也显示成本条 → Task 4（三分支统一用 detail-content 包装）
- ✅ 错误分支也显示成本条 → Task 4（catch 分支同样调用 renderCostBar）
- ✅ 不持久化（刷新后不保留对比选择）→ 无 localStorage，符合设计

**类型一致性：**
- `calcCost` 返回 `{input, output, cacheRead, cacheWrite, total}` — Task 2 定义，Task 3 使用 `c.input / c.output / c.cacheRead / c.cacheWrite / c.total` ✅
- `findPricing(modelName)` 返回 pricing 对象或 null — Task 2 定义，Task 3 使用 ✅
- `allPricings` 模块变量 — Task 1 定义，Task 3 使用 ✅
