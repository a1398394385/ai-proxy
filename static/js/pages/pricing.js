import { api, escHtml, showModal, closeModal } from '../core.js';

const EXCHANGE_RATE = 7;

function formatCny(value, currency) {
  const rmb = currency === 'RMB' ? Number(value) : Number(value) * EXCHANGE_RATE;
  return '¥' + rmb.toFixed(6);
}

/* ─── 统计计算 ─── */
function computeSummary(items) {
  const total = items.length;
  const rmbCount = items.filter(p => p.currency === 'RMB').length;
  const usdCount = total - rmbCount;

  let avgInput = 0, avgOutput = 0, maxTotal = 0, maxModel = '';
  if (total > 0) {
    items.forEach(p => {
      const cny = p.currency === 'RMB' ? 1 : EXCHANGE_RATE;
      const inp = Number(p.input_cost_per_million) * cny;
      const out = Number(p.output_cost_per_million) * cny;
      avgInput += inp;
      avgOutput += out;
      const sum = inp + out;
      if (sum > maxTotal) { maxTotal = sum; maxModel = p.model_id; }
    });
    avgInput /= total;
    avgOutput /= total;
  }
  return { total, rmbCount, usdCount, avgInput, avgOutput, maxModel, maxTotal };
}

/* ─── 渲染统计卡片 ─── */
function renderSummary(summary) {
  const container = document.getElementById('pricing-summary');
  container.innerHTML = `
    <div class="pricing-stat-card">
      <div class="pricing-stat-icon">📦</div>
      <div class="pricing-stat-label">定价模型</div>
      <div class="pricing-stat-value">${summary.total}</div>
      <div class="pricing-stat-split">
        <span class="split-item"><span class="split-dot rmb"></span> RMB ${summary.rmbCount}</span>
        <span class="split-item"><span class="split-dot usd"></span> USD ${summary.usdCount}</span>
      </div>
    </div>
    <div class="pricing-stat-card">
      <div class="pricing-stat-icon" style="background:hsl(160 60% 45% / 0.1);border-color:hsl(160 60% 45% / 0.15)">📈</div>
      <div class="pricing-stat-label">平均输入（¥/1M）</div>
      <div class="pricing-stat-value">¥${summary.avgInput.toFixed(6)}</div>
      <div class="pricing-stat-sub">每百万 Tokens 均价</div>
    </div>
    <div class="pricing-stat-card">
      <div class="pricing-stat-icon" style="background:hsl(var(--purple) / 0.1);border-color:hsl(var(--purple) / 0.15)">📊</div>
      <div class="pricing-stat-label">平均输出（¥/1M）</div>
      <div class="pricing-stat-value">¥${summary.avgOutput.toFixed(6)}</div>
      <div class="pricing-stat-sub">每百万 Tokens 均价</div>
    </div>
    <div class="pricing-stat-card">
      <div class="pricing-stat-icon" style="background:hsl(35 90% 55% / 0.1);border-color:hsl(35 90% 55% / 0.15)">🏆</div>
      <div class="pricing-stat-label">最贵模型</div>
      <div class="pricing-stat-value" style="font-size:18px;font-weight:500">${summary.maxModel ? escHtml(summary.maxModel) : '—'}</div>
      <div class="pricing-stat-sub">${summary.maxTotal > 0 ? '¥' + summary.maxTotal.toFixed(6) + ' / 1M' : '每百万 Tokens 成本'}</div>
    </div>`;
}

/* ─── 渲染表格 ─── */
function renderTable(items) {
  const tbody = document.getElementById('pricing-tbody');
  const countEl = document.getElementById('pricing-count');
  countEl.textContent = items.length + ' 条';

  if (items.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8"><div class="pricing-empty">
      <div class="pricing-empty-icon">💰</div>
      <div class="pricing-empty-text">暂无定价数据</div>
      <div class="pricing-empty-sub">点击上方"新增定价"按钮添加</div>
    </div></td></tr>`;
    return;
  }

  /* 计算各列最大值（统一转人民币）用于条形图比例 */
  const maxIn = Math.max(...items.map(p =>
    Number(p.input_cost_per_million) * (p.currency === 'RMB' ? 1 : EXCHANGE_RATE)
  ), 0.000001);
  const maxOut = Math.max(...items.map(p =>
    Number(p.output_cost_per_million) * (p.currency === 'RMB' ? 1 : EXCHANGE_RATE)
  ), 0.000001);
  const maxCR = Math.max(...items.map(p =>
    Number(p.cache_read_cost_per_million) * (p.currency === 'RMB' ? 1 : EXCHANGE_RATE)
  ), 0.000001);
  const maxCW = Math.max(...items.map(p =>
    Number(p.cache_creation_cost_per_million) * (p.currency === 'RMB' ? 1 : EXCHANGE_RATE)
  ), 0.000001);

  tbody.innerHTML = items.map((p, idx) => {
    const mid = escHtml(p.model_id).replace(/'/g, "\\'");
    const cny = p.currency === 'RMB' ? 1 : EXCHANGE_RATE;
    const inCny = Number(p.input_cost_per_million) * cny;
    const outCny = Number(p.output_cost_per_million) * cny;
    const crCny = Number(p.cache_read_cost_per_million) * cny;
    const cwCny = Number(p.cache_creation_cost_per_million) * cny;

    const badge = p.currency === 'RMB'
      ? '<span class="badge-currency badge-rmb">CNY</span>'
      : '<span class="badge-currency badge-usd">USD</span>';

    return `<tr style="animation-delay:${(idx * 0.025).toFixed(3)}s">
      <td class="cell-model">
        <span class="model-id">${escHtml(p.model_id)}</span>
        <span class="model-name">${escHtml(p.display_name)}</span>
      </td>
      <td class="cell-price">
        <span class="price-bar-bg" style="width:${(inCny / maxIn * 100).toFixed(1)}%;background:hsl(var(--blue))"></span>
        <span class="price-value">${formatCny(p.input_cost_per_million, p.currency)}</span>
      </td>
      <td class="cell-price">
        <span class="price-bar-bg" style="width:${(outCny / maxOut * 100).toFixed(1)}%;background:hsl(160 60% 45%)"></span>
        <span class="price-value">${formatCny(p.output_cost_per_million, p.currency)}</span>
      </td>
      <td class="cell-price">
        <span class="price-bar-bg" style="width:${(crCny / maxCR * 100).toFixed(1)}%;background:hsl(var(--purple))"></span>
        <span class="price-value">${formatCny(p.cache_read_cost_per_million, p.currency)}</span>
      </td>
      <td class="cell-price">
        <span class="price-bar-bg" style="width:${(cwCny / maxCW * 100).toFixed(1)}%;background:hsl(var(--orange))"></span>
        <span class="price-value">${formatCny(p.cache_creation_cost_per_million, p.currency)}</span>
      </td>
      <td class="cell-multiplier">${Number(p.multiplier || 1).toFixed(2)}×</td>
      <td>${badge}</td>
      <td class="cell-actions">
        <button class="btn-icon btn-edit" title="编辑" onclick="editPricing('${mid}')">✎</button>
        <button class="btn-icon btn-delete" title="删除" onclick="deletePricing('${mid}')">✕</button>
      </td>
    </tr>`;
  }).join('');
}

/* ─── 页面加载 ─── */
export async function loadPricingPage() {
  const container = document.getElementById('page-pricing');
  if (!container) return;
  if (container.dataset.loaded) { await loadPricingTable(); return; }
  container.dataset.loaded = '1';

  container.innerHTML = `
    <div id="pricing-summary"></div>
    <div class="pricing-card">
      <div class="pricing-card-header">
        <div class="pricing-header-left">
          <h2 class="pricing-header-title">💰 定价配置</h2>
          <span class="pricing-header-count" id="pricing-count"></span>
        </div>
        <div class="pricing-header-right">
          <div class="search-box" style="max-width:220px">
            <input type="text" id="pricing-search" placeholder="搜索模型...">
          </div>
          <button class="btn btn-primary" onclick="showPricingModal()">＋ 新增定价</button>
        </div>
      </div>
      <div class="pricing-table-wrap">
        <table id="pricing-table">
          <thead>
            <tr>
              <th style="min-width:180px">模型</th>
              <th class="th-price">输入 <span class="price-unit">¥/1M</span></th>
              <th class="th-price">输出 <span class="price-unit">¥/1M</span></th>
              <th class="th-price">缓存读 <span class="price-unit">¥/1M</span></th>
              <th class="th-price">缓存写 <span class="price-unit">¥/1M</span></th>
              <th style="width:72px">倍率</th>
              <th style="width:72px">币种</th>
              <th style="width:80px">操作</th>
            </tr>
          </thead>
          <tbody id="pricing-tbody"></tbody>
        </table>
      </div>
    </div>`;

  document.getElementById('pricing-search').addEventListener('keyup', loadPricingTable);
  await loadPricingTable();
}

export function initPricingPage() {}

/* ─── 加载数据 ─── */
async function loadPricingTable() {
  const search = document.getElementById('pricing-search')?.value?.trim() || '';
  const url = search ? `/api/pricing?search=${encodeURIComponent(search)}` : '/api/pricing';
  const data = await api(url);
  const items = data.pricings || [];

  renderSummary(computeSummary(items));
  renderTable(items);
}

/* ─── 编辑 / 删除 / 新增 ─── */

async function editPricing(modelId) {
  const data = await api(`/api/pricing/${encodeURIComponent(modelId)}`);
  if (data.error) return alert(data.error);
  showPricingModal(data);
}

async function deletePricing(modelId) {
  if (!confirm(`确定删除模型 ${modelId} 的定价？`)) return;
  const result = await api(`/api/pricing/${encodeURIComponent(modelId)}`, { method: 'DELETE' });
  if (result.error) return alert(result.error);
  await loadPricingTable();
}

function showPricingModal(existing = null) {
  const isEdit = !!existing;
  const content = `
    <div class="pricing-modal-grid">
      <div class="pricing-modal-section">基本信息</div>
      <div class="form-group full">
        <label class="form-label">模型 ID</label>
        <input class="form-input" id="pm-model-id" value="${escHtml(existing?.model_id || '')}" ${isEdit ? 'disabled' : ''} placeholder="例: gpt-4o">
      </div>
      <div class="form-group full">
        <label class="form-label">显示名</label>
        <input class="form-input" id="pm-display-name" value="${escHtml(existing?.display_name || '')}" placeholder="例: GPT-4o">
      </div>

      <div class="pricing-modal-section">定价（每百万 Tokens）</div>
      <div class="form-group">
        <label class="form-label">输入价格</label>
        <input class="form-input" id="pm-input" type="number" step="0.000001" value="${existing?.input_cost_per_million || ''}" placeholder="0">
      </div>
      <div class="form-group">
        <label class="form-label">输出价格</label>
        <input class="form-input" id="pm-output" type="number" step="0.000001" value="${existing?.output_cost_per_million || ''}" placeholder="0">
      </div>

      <div class="pricing-modal-section">缓存定价（每百万 Tokens）</div>
      <div class="form-group">
        <label class="form-label">缓存读</label>
        <input class="form-input" id="pm-cache-read" type="number" step="0.000001" value="${existing?.cache_read_cost_per_million || '0'}" placeholder="0">
      </div>
      <div class="form-group">
        <label class="form-label">缓存写</label>
        <input class="form-input" id="pm-cache-write" type="number" step="0.000001" value="${existing?.cache_creation_cost_per_million || '0'}" placeholder="0">
      </div>

      <div class="pricing-modal-section">倍率</div>
      <div class="form-group full">
        <label class="form-label">计费倍率</label>
        <input class="form-input" id="pm-multiplier" type="number" step="0.01" min="0" value="${existing?.multiplier || '1.0'}" placeholder="1.0">
        <span class="form-hint">四个价格（输入/输出/缓存读/缓存写）均乘以该倍率</span>
      </div>

      <div class="pricing-modal-section">币种</div>
      <div class="form-group full">
        <label class="form-label">结算币种</label>
        <select class="form-input" id="pm-currency">
          <option value="USD" ${existing?.currency !== 'RMB' ? 'selected' : ''}>USD（美元）</option>
          <option value="RMB" ${existing?.currency === 'RMB' ? 'selected' : ''}>RMB（人民币）</option>
        </select>
      </div>
    </div>`;

  const editId = isEdit ? `'${escHtml(existing.model_id).replace(/'/g, "\\'")}'` : 'null';
  const footer = `
    <button class="btn btn-secondary" onclick="closeModal()">取消</button>
    <button class="btn btn-primary" onclick="savePricing(${editId})">保存</button>`;

  showModal(isEdit ? '编辑定价' : '新增定价', content, footer);
}

async function savePricing(editModelId) {
  const payload = {
    model_id: document.getElementById('pm-model-id').value.trim(),
    display_name: document.getElementById('pm-display-name').value.trim(),
    input_cost_per_million: document.getElementById('pm-input').value,
    output_cost_per_million: document.getElementById('pm-output').value,
    cache_read_cost_per_million: document.getElementById('pm-cache-read').value || '0',
    cache_creation_cost_per_million: document.getElementById('pm-cache-write').value || '0',
    multiplier: document.getElementById('pm-multiplier').value || '1.0',
    currency: document.getElementById('pm-currency').value,
  };
  if (!payload.model_id || !payload.display_name || !payload.input_cost_per_million || !payload.output_cost_per_million) {
    alert('模型 ID、显示名、输入/输出价格为必填');
    return;
  }
  const url = editModelId
    ? `/api/pricing/${encodeURIComponent(editModelId)}`
    : '/api/pricing';
  const method = editModelId ? 'PUT' : 'POST';
  const result = await api(url, { method, body: JSON.stringify(payload) });
  if (result.error) { alert(result.error); return; }
  closeModal();
  await loadPricingTable();
}

/* 挂载到 window（ES Module onclick 需要） */
window.showPricingModal = showPricingModal;
window.editPricing = editPricing;
window.deletePricing = deletePricing;
window.savePricing = savePricing;
