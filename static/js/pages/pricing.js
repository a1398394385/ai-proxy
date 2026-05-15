import { api, escHtml, showModal, closeModal, on } from '../core.js';

const EXCHANGE_RATE = 7;

function formatCny(value, currency) {
  const rmb = currency === 'RMB' ? Number(value) : Number(value) * EXCHANGE_RATE;
  let s = rmb.toFixed(6);
  const dot = s.indexOf('.');
  let dec = s.slice(dot + 1).replace(/0+$/, '');
  if (dec.length < 2) dec = dec.padEnd(2, '0');
  s = s.slice(0, dot) + '.' + dec;
  return '¥' + s;
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
      <div class="pricing-stat-icon" style="background:hsla(220,80%,60%,0.12);border-color:hsla(220,80%,60%,0.18)">📈</div>
      <div class="pricing-stat-label">平均输入（¥/1M）</div>
      <div class="pricing-stat-value">¥${summary.avgInput.toFixed(6)}</div>
      <div class="pricing-stat-sub">每百万 Tokens 均价</div>
    </div>
    <div class="pricing-stat-card">
      <div class="pricing-stat-icon" style="background:hsla(160,60%,45%,0.12);border-color:hsla(160,60%,45%,0.18)">📊</div>
      <div class="pricing-stat-label">平均输出（¥/1M）</div>
      <div class="pricing-stat-value">¥${summary.avgOutput.toFixed(6)}</div>
      <div class="pricing-stat-sub">每百万 Tokens 均价</div>
    </div>
    <div class="pricing-stat-card">
      <div class="pricing-stat-icon" style="background:hsla(35,90%,55%,0.12);border-color:hsla(35,90%,55%,0.18)">🏆</div>
      <div class="pricing-stat-label">最贵模型</div>
      <div class="pricing-stat-value" style="font-size:18px;font-weight:500">${summary.maxModel ? escHtml(summary.maxModel) : '—'}</div>
      <div class="pricing-stat-sub">${summary.maxTotal > 0 ? '¥' + summary.maxTotal.toFixed(6) + ' / 1M' : '每百万 Tokens 成本'}</div>
    </div>`;
}

/* ─── 渲染卡片网格 ─── */
function renderCards(items) {
  const grid = document.getElementById('pricing-grid');
  const countEl = document.getElementById('pricing-count');
  countEl.textContent = items.length;

  if (items.length === 0) {
    grid.innerHTML = `<div class="pricing-empty">
      <div class="pricing-empty-icon">💰</div>
      <div class="pricing-empty-text">暂无定价数据</div>
      <div class="pricing-empty-sub">点击上方"新增定价"按钮添加</div>
    </div>`;
    return;
  }

  grid.innerHTML = items.map((p, idx) => {
    const mid = escHtml(p.model_id).replace(/'/g, "\\'");
    return `<div class="pricing-card-item" style="animation-delay:${(idx * 0.04).toFixed(3)}s">
      <div class="card-top">
        <div class="card-title-area">
          <span class="card-display-name">${escHtml(p.display_name)}</span>
          <span class="card-model-id">${escHtml(p.model_id)}</span>
        </div>
        <div class="card-actions">
          <button class="card-btn card-btn-edit" title="编辑" data-action="editPricing" data-id="${mid}">
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M2 10.5V12h1.5l6.5-6.5-1.5-1.5L2 10.5zM11.5 4.5c.3-.3.3-.8 0-1.1l-.4-.4c-.3-.3-.8-.3-1.1 0L9 3.5l1.5 1.5 1-1z" fill="currentColor"/></svg>
          </button>
          <button class="card-btn card-btn-delete" title="删除" data-action="deletePricing" data-id="${mid}">
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M4 3V1.5c0-.3.2-.5.5-.5h5c.3 0 .5.2.5.5V3h3v1h-1v8c0 .6-.4 1-1 1H3c-.6 0-1-.4-1-1V4H1V3h3zm1-1v1h4V2H5zM3 4v8h8V4H3z" fill="currentColor"/></svg>
          </button>
        </div>
      </div>
      <div class="card-costs">
        <div class="cost-row">
          <div class="cost-cell" data-type="input">
            <span class="cost-label">Input</span>
            <span class="cost-value">${formatCny(p.input_cost_per_million, p.currency)}</span>
            <span class="cost-unit">/1M</span>
          </div>
          <div class="cost-cell" data-type="output">
            <span class="cost-label">Output</span>
            <span class="cost-value">${formatCny(p.output_cost_per_million, p.currency)}</span>
            <span class="cost-unit">/1M</span>
          </div>
        </div>
        <div class="cost-row">
          <div class="cost-cell" data-type="cache-read">
            <span class="cost-label">Cache Read</span>
            <span class="cost-value">${formatCny(p.cache_read_cost_per_million, p.currency)}</span>
            <span class="cost-unit">/1M</span>
          </div>
          <div class="cost-cell" data-type="cache-write">
            <span class="cost-label">Cache Create</span>
            <span class="cost-value">${formatCny(p.cache_creation_cost_per_million, p.currency)}</span>
            <span class="cost-unit">/1M</span>
          </div>
        </div>
      </div>
      <div class="card-footer">
        <span class="card-meta"><span class="meta-label">倍率</span> ${Number(p.multiplier || 1).toFixed(2)}×</span>
        <span class="card-currency">
          <span class="currency-badge ${p.currency === 'RMB' ? 'curr-rmb' : 'curr-usd'}">${p.currency}</span>
        </span>
        ${p.input_includes_cache_read ? '<span class="meta-badge badge-cache" title="查询时从 input 扣除 cache_read">⚡含缓存</span>' : ''}
      </div>
    </div>`;
  }).join('');
}

/* ─── 页面加载 ─── */
export async function loadPricingPage() {
  const container = document.getElementById('page-pricing');
  if (!container) return;
  if (container.dataset.loaded) { await loadPricingData(); return; }
  container.dataset.loaded = '1';

  container.innerHTML = `
    <div id="pricing-summary"></div>
    <div class="pricing-card">
      <div class="pricing-card-header">
        <div class="pricing-header-left">
          <h2 class="pricing-header-title">💰 定价配置</h2>
          <span class="pricing-header-count" id="pricing-count"></span>
          <span class="pricing-header-subtitle">每百万 Tokens</span>
        </div>
        <div class="pricing-header-right">
          <div class="search-box" style="max-width:220px">
            <input type="text" id="pricing-search" placeholder="搜索模型...">
          </div>
          <button class="btn btn-primary" data-action="showPricingModal">＋ 新增定价</button>
        </div>
      </div>
      <div class="pricing-grid" id="pricing-grid"></div>
    </div>`;

  document.getElementById('pricing-search').addEventListener('keyup', loadPricingData);
  await loadPricingData();
}

export function initPricingPage() {
  on('showPricingModal', () => showPricingModal());
  on('editPricing', (e, el) => editPricing(el.dataset.id));
  on('deletePricing', (e, el) => deletePricing(el.dataset.id));
  on('savePricing', (e, el) => savePricing(el.dataset.editId || null));
}

/* ─── 加载数据 ─── */
async function loadPricingData() {
  const search = document.getElementById('pricing-search')?.value?.trim() || '';
  const url = search ? `/api/pricing?search=${encodeURIComponent(search)}` : '/api/pricing';
  const data = await api(url);
  const items = data.pricings || [];

  renderSummary(computeSummary(items));
  renderCards(items);
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
  await loadPricingData();
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

      <div class="pricing-modal-section">统计扣除</div>
      <div class="form-group full">
        <label class="form-label">
          <input type="checkbox" id="pm-input-includes-cache-read" ${existing?.input_includes_cache_read ? 'checked' : ''} style="margin-right:8px">
          查询统计数据时从 input_tokens 扣除 cache_read
        </label>
        <span class="form-hint">适用于 input_tokens 已包含 cache_read 的模型（如 kimi-k2.6），避免重复计费</span>
      </div>
    </div>`;

  const footer = `
    <button class="btn btn-secondary" data-action="closeModal">取消</button>
    <button class="btn btn-primary" data-action="savePricing" data-edit-id="${escHtml(existing ? existing.model_id : '')}">保存</button>`;

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
    input_includes_cache_read: document.getElementById('pm-input-includes-cache-read').checked ? 1 : 0,
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
  await loadPricingData();
}

