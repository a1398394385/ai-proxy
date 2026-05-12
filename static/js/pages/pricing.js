import { api, escHtml, showModal, closeModal } from '../core.js';

const EXCHANGE_RATE = 7;

function formatCny(value, currency) {
  const rmb = currency === 'RMB' ? value : value * EXCHANGE_RATE;
  return '¥' + parseFloat(rmb).toFixed(6);
}

export async function loadPricingPage() {
  const container = document.getElementById('page-pricing');
  if (!container) return;
  if (container.dataset.loaded) { await loadPricingTable(); return; }
  container.dataset.loaded = '1';
  container.innerHTML = `
    <div class="pricing-toolbar">
      <div class="search-box">
        <input type="text" id="pricing-search" placeholder="搜索模型名 / 显示名...">
      </div>
      <span class="pricing-count" id="pricing-count"></span>
      <button class="btn btn-primary" style="margin-left:auto" onclick="showPricingModal()">＋ 新增定价</button>
    </div>
    <div style="overflow-x:auto">
      <table id="pricing-table">
        <thead>
          <tr>
            <th>模型 ID</th>
            <th>显示名</th>
            <th style="text-align:right">输入价格</th>
            <th style="text-align:right">输出价格</th>
            <th style="text-align:right">缓存读</th>
            <th style="text-align:right">缓存写</th>
            <th>币种</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody id="pricing-tbody"></tbody>
      </table>
    </div>
  `;

  document.getElementById('pricing-search').addEventListener('keyup', loadPricingTable);
  await loadPricingTable();
}

export function initPricingPage() {}

async function loadPricingTable() {
  const search = document.getElementById('pricing-search')?.value?.trim() || '';
  const url = search ? `/api/pricing?search=${encodeURIComponent(search)}` : '/api/pricing';
  const data = await api(url);
  const items = data.pricings || [];
  document.getElementById('pricing-count').textContent = items.length + ' 条定价';

  const tbody = document.getElementById('pricing-tbody');
  tbody.innerHTML = items.map(p => {
    const currencyBadge = p.currency === 'RMB'
      ? '<span class="badge badge-rmb">RMB</span>'
      : '<span class="badge badge-usd">USD</span>';
    const mid = escHtml(p.model_id).replace(/'/g, "\\'");
    return `<tr>
      <td style="font-family:monospace">${escHtml(p.model_id)}</td>
      <td>${escHtml(p.display_name)}</td>
      <td class="cell-price">${formatCny(p.input_cost_per_million, p.currency)}</td>
      <td class="cell-price">${formatCny(p.output_cost_per_million, p.currency)}</td>
      <td class="cell-price">${formatCny(p.cache_read_cost_per_million, p.currency)}</td>
      <td class="cell-price">${formatCny(p.cache_creation_cost_per_million, p.currency)}</td>
      <td>${currencyBadge}</td>
      <td>
        <button class="btn btn-secondary btn-sm" onclick="editPricing('${mid}')">编辑</button>
        <button class="btn btn-danger btn-sm" onclick="deletePricing('${mid}')">删除</button>
      </td>
    </tr>`;
  }).join('');
}

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
  const title = isEdit ? '编辑定价' : '新增定价';
  const content = `
    <div class="form-group"><label class="form-label">模型 ID</label>
      <input class="form-input" id="pm-model-id" value="${escHtml(existing?.model_id || '')}" ${isEdit ? 'disabled' : ''}></div>
    <div class="form-group"><label class="form-label">显示名</label>
      <input class="form-input" id="pm-display-name" value="${escHtml(existing?.display_name || '')}"></div>
    <div class="form-group"><label class="form-label">输入价格 / 1M tokens</label>
      <input class="form-input" id="pm-input" type="number" step="0.000001" value="${existing?.input_cost_per_million || ''}"></div>
    <div class="form-group"><label class="form-label">输出价格 / 1M tokens</label>
      <input class="form-input" id="pm-output" type="number" step="0.000001" value="${existing?.output_cost_per_million || ''}"></div>
    <div class="form-group"><label class="form-label">缓存读价格 / 1M</label>
      <input class="form-input" id="pm-cache-read" type="number" step="0.000001" value="${existing?.cache_read_cost_per_million || '0'}"></div>
    <div class="form-group"><label class="form-label">缓存写价格 / 1M</label>
      <input class="form-input" id="pm-cache-write" type="number" step="0.000001" value="${existing?.cache_creation_cost_per_million || '0'}"></div>
    <div class="form-group"><label class="form-label">币种</label>
      <select class="form-input" id="pm-currency">
        <option value="USD" ${existing?.currency !== 'RMB' ? 'selected' : ''}>USD (美元)</option>
        <option value="RMB" ${existing?.currency === 'RMB' ? 'selected' : ''}>RMB (人民币)</option>
      </select></div>
  `;
  const editId = isEdit ? `'${escHtml(existing.model_id).replace(/'/g, "\\'")}'` : 'null';
  const footer = `<button class="btn btn-secondary" onclick="closeModal()">取消</button><button class="btn btn-primary" onclick="savePricing(${editId})">保存</button>`;
  showModal(title, content, footer);
}

async function savePricing(editModelId) {
  const payload = {
    model_id: document.getElementById('pm-model-id').value.trim(),
    display_name: document.getElementById('pm-display-name').value.trim(),
    input_cost_per_million: document.getElementById('pm-input').value,
    output_cost_per_million: document.getElementById('pm-output').value,
    cache_read_cost_per_million: document.getElementById('pm-cache-read').value || '0',
    cache_creation_cost_per_million: document.getElementById('pm-cache-write').value || '0',
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

// 挂载到 window（ES Module onclick 需要）
window.showPricingModal = showPricingModal;
window.editPricing = editPricing;
window.deletePricing = deletePricing;
window.savePricing = savePricing;
