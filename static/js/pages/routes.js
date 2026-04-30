import { api, escHtml, showModal, closeModal, bus } from '../core.js';

// ===== 路由管理 (独立页面) =====

let currentProxyType = 'codex';

function switchProxyType(pt) {
  currentProxyType = pt;
  document.querySelectorAll('.proxy-tab').forEach(b => b.classList.toggle('active', b.dataset.pt === pt));
  loadRouteTable(pt);
}

async function loadRouteTable(proxyType) {
  let url = '/api/routes';
  if (proxyType) url += '?proxy_type=' + encodeURIComponent(proxyType);
  const data = await api(url);
  document.querySelector('#route-table tbody').innerHTML = data.routes.map(r =>
    `<tr style="${r.source === '*' ? 'background:hsl(var(--primary) / 0.05);' : ''} ${r.upstream_active ? '' : 'opacity:0.5'}">
      <td><span class="badge badge-purple">${escHtml(r.source)}${r.source === '*' ? ' (★ fallback)' : ''}</span></td>
      <td>→ <span class="badge badge-green">${escHtml(r.target_name)}</span></td>
      <td><span class="badge" style="background:hsl(var(--muted));color:hsl(var(--muted-foreground))">${escHtml(r.upstream_id)}</span></td>
      <td><span class="badge badge-blue">${escHtml(r.proxy_type || 'codex')}</span></td>
      <td>${r.upstream_active ? '<span style="color:hsl(var(--green))">活跃</span>' : '<span style="color:hsl(var(--red))">上游已禁用</span>'}</td>
      <td>
        <button class="btn btn-secondary btn-sm" onclick="showRouteModal(${r.id})">编辑</button>
        <button class="btn btn-danger btn-sm" onclick="confirmDeleteRoute(${r.id}, '${escHtml(r.source)}')">删除</button>
      </td>
    </tr>`
  ).join('') || '<tr><td colspan="6" class="empty-state">暂无路由</td></tr>';
}

// ─── 路由模态框 ───
async function showRouteModal(editId) {
  let data = { source: '', target_model_id: '', proxy_type: currentProxyType };
  let title = '新增路由';
  if (editId) {
    title = '编辑路由 #' + editId;
    const routes = await api('/api/routes');
    const found = routes.routes.find(r => r.id === editId);
    if (found) data = found;
  }
  const models = await api('/api/models');
  const byUpstream = {};
  models.models.forEach(m => {
    if (!byUpstream[m.upstream_name]) byUpstream[m.upstream_name] = [];
    byUpstream[m.upstream_name].push(m);
  });
  let modelOpts = '';
  for (const [upstream, mlist] of Object.entries(byUpstream)) {
    modelOpts += '<optgroup label="' + escHtml(upstream) + '">';
    mlist.forEach(m => { modelOpts += '<option value="' + m.id + '" ' + (data.target_model_id === m.id ? 'selected' : '') + '>' + escHtml(m.name) + '</option>'; });
    modelOpts += '</optgroup>';
  }
  const proxyTypeOptions = ['codex', 'claude', 'pass_through']
    .map(pt => `<option value="${pt}" ${data.proxy_type === pt ? 'selected' : ''}>${pt}</option>`)
    .join('');
  showModal(title,
    `<div class="form-group"><label class="form-label">源模型名</label><input type="text" class="form-input" id="r-source" value="${escHtml(data.source)}" placeholder="如 gpt-4o 或 * (fallback)"></div>
     <div class="form-group"><label class="form-label">目标模型</label><select class="form-input" id="r-target">${modelOpts}</select></div>
     <div class="form-group"><label class="form-label">Proxy 类型</label><select class="form-input" id="r-proxy">${proxyTypeOptions}</select></div>`,
    `<button class="btn btn-secondary" onclick="closeModal()">取消</button><button class="btn btn-primary" onclick="saveRoute(${editId || 0})">保存</button>`);
}

async function saveRoute(editId) {
  const data = {
    source: document.getElementById('r-source').value.trim(),
    target_model_id: parseInt(document.getElementById('r-target').value),
    proxy_type: document.getElementById('r-proxy').value,
  };
  if (!data.source) { alert('源模型名不能为空'); return; }
  if (editId) {
    await api('/api/routes/' + editId, { method: 'PUT', body: JSON.stringify(data) });
  } else {
    await api('/api/routes', { method: 'POST', body: JSON.stringify(data) });
  }
  closeModal();
  bus.emit('config:route-changed', {});
  bus.emit('config:dirty', { source: 'route' });
  loadRouteTable(currentProxyType);
}

async function confirmDeleteRoute(id, source) {
  if (source === '*') {
    const routes = await api('/api/routes');
    const starCount = routes.routes.filter(r => r.source === '*').length;
    if (starCount <= 1) { alert('❌ 不能删除最后一条 * fallback 路由'); return; }
  }
  if (!confirm('确认删除路由 "' + source + '"？')) return;
  const result = await api('/api/routes/' + id, { method: 'DELETE' });
  if (result.error) { alert('❌ ' + result.error); }
  else { bus.emit('config:route-changed', {}); bus.emit('config:dirty', { source: 'route' }); loadRouteTable(currentProxyType); }
}

// ===== Page Loader =====
async function loadRoutePage() {
  document.getElementById('page-routes').innerHTML = `
    <div class="proxy-tabs" style="display:flex;gap:8px;margin-bottom:16px;">
      <button class="proxy-tab btn btn-sm active" data-pt="codex" onclick="switchProxyType('codex')">🔌 Codex</button>
      <button class="proxy-tab btn btn-sm" data-pt="claude" onclick="switchProxyType('claude')">🤖 Claude</button>
      <button class="proxy-tab btn btn-sm" data-pt="pass_through" onclick="switchProxyType('pass_through')">↗️ Pass-through</button>
    </div>
    <div class="table-card" style="margin-bottom:20px">
      <div class="table-header">
        <span class="table-title">🔀 路由映射</span>
        <button class="btn btn-primary btn-sm" onclick="showRouteModal()">+ 新增路由</button>
      </div>
      <div class="table-scroll">
        <table id="route-table">
          <thead><tr><th>源模型</th><th>→ 目标模型</th><th>上游</th><th>Proxy</th><th>状态</th><th>操作</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>`;
  loadRouteTable('codex');
}

function initRoutePage() {
  // No-op — HTML injected dynamically
}

// ===== Exports =====
export { loadRoutePage, initRoutePage, loadRouteTable, showRouteModal, saveRoute, confirmDeleteRoute, switchProxyType };

// ===== Global Scope Mounting =====
window.switchProxyType = switchProxyType;
window.showRouteModal = showRouteModal;
window.saveRoute = saveRoute;
window.confirmDeleteRoute = confirmDeleteRoute;
window.loadRoutePage = loadRoutePage;
window.initRoutePage = initRoutePage;
