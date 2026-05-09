import { api, escHtml, showModal, closeModal, bus } from '../core.js';

// ===== 路由管理 (独立页面) =====

let currentRequestType = 'chat_completions';

function switchRequestType(rt) {
  currentRequestType = rt;
  document.querySelectorAll('.proxy-tab').forEach(b => b.classList.toggle('active', b.dataset.pt === rt));
  loadRouteTable(rt);
}

async function loadRouteTable(requestType) {
  let url = '/api/routes';
  if (requestType) url += '?request_type=' + encodeURIComponent(requestType);
  const data = await api(url);
  document.querySelector('#route-table tbody').innerHTML = data.routes.map(r =>
    `<tr style="${r.source === '*' ? 'background:hsl(var(--primary) / 0.05);' : ''} ${r.upstream_active ? '' : 'opacity:0.5'}">
      <td><span class="badge badge-purple">${escHtml(r.source)}${r.source === '*' ? ' (★ fallback)' : ''}</span></td>
      <td>→ <span class="badge badge-green">${escHtml(r.target_name)}</span></td>
      <td><span class="badge" style="background:hsl(var(--muted));color:hsl(var(--muted-foreground))">${escHtml(r.upstream_id)}</span></td>
      <td><span class="badge badge-blue">${escHtml(r.request_type || 'chat_completions')}</span></td>
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
  let data = { source: '', target_model_id: '', request_type: currentRequestType };
  let title = '新增路由';
  if (editId) {
    title = '编辑路由 #' + editId;
    const routes = await api('/api/routes');
    const found = routes.routes.find(r => r.id === editId);
    if (found) data = found;
  }
  const [models, upstreams] = await Promise.all([api('/api/models'), api('/api/upstreams')]);
  const fmtLabel = { responses: 'Resp', messages: 'Msg', chat_completions: 'Chat' };
  const upFmt = Object.fromEntries(upstreams.upstreams.map(u => [u.id, fmtLabel[u.format] || u.format]));
  const byUpstream = {};
  models.models.forEach(m => {
    if (!byUpstream[m.upstream_name]) byUpstream[m.upstream_name] = [];
    byUpstream[m.upstream_name].push(m);
  });
  let modelOpts = '';
  for (const [upstream, mlist] of Object.entries(byUpstream)) {
    const fmt = upFmt[upstream] || '';
    modelOpts += '<optgroup label="' + escHtml(upstream) + (fmt ? ' (' + fmt + ')' : '') + '">';
    mlist.forEach(m => { modelOpts += '<option value="' + m.id + '" ' + (data.target_model_id === m.id ? 'selected' : '') + '>' + escHtml(m.name) + '</option>'; });
    modelOpts += '</optgroup>';
  }
  const requestTypeOptions = ['responses', 'messages', 'chat_completions']
    .map(rt => `<option value="${rt}" ${data.request_type === rt ? 'selected' : ''}>${rt}</option>`)
    .join('');
  const sourceField = data.source === '*'
    ? `<input type="text" class="form-input" value="* (fallback)" readonly style="background:hsl(var(--muted));color:hsl(var(--muted-foreground));cursor:not-allowed"><input type="hidden" id="r-source" value="*">`
    : `<input type="text" class="form-input" id="r-source" value="${escHtml(data.source)}" placeholder="如 gpt-4o">`;
  showModal(title,
    `<div class="form-group"><label class="form-label">源模型名</label>${sourceField}</div>
     <div class="form-group"><label class="form-label">目标模型</label><select class="form-input" id="r-target">${modelOpts}</select></div>
     <input type="hidden" id="r-proxy" value="${escHtml(data.request_type)}">`,
    `<button class="btn btn-secondary" onclick="closeModal()">取消</button><button class="btn btn-primary" onclick="saveRoute(${editId || 0})">保存</button>`);
}

async function showFallbackModal() {
  const data = { source: '*', target_model_id: '', request_type: currentRequestType };
  const title = '新增回退路由';
  const [models, upstreams] = await Promise.all([api('/api/models'), api('/api/upstreams')]);
  const fmtLabel = { responses: 'Resp', messages: 'Msg', chat_completions: 'Chat' };
  const upFmt = Object.fromEntries(upstreams.upstreams.map(u => [u.id, fmtLabel[u.format] || u.format]));
  const byUpstream = {};
  models.models.forEach(m => {
    if (!byUpstream[m.upstream_name]) byUpstream[m.upstream_name] = [];
    byUpstream[m.upstream_name].push(m);
  });
  let modelOpts = '';
  for (const [upstream, mlist] of Object.entries(byUpstream)) {
    const fmt = upFmt[upstream] || '';
    modelOpts += '<optgroup label="' + escHtml(upstream) + (fmt ? ' (' + fmt + ')' : '') + '">';
    mlist.forEach(m => { modelOpts += '<option value="' + m.id + '">' + escHtml(m.name) + '</option>'; });
    modelOpts += '</optgroup>';
  }
  showModal(title,
    `<div class="form-group"><label class="form-label">源模型名</label><input type="text" class="form-input" value="* (fallback)" readonly style="background:hsl(var(--muted));color:hsl(var(--muted-foreground));cursor:not-allowed"></div>
     <input type="hidden" id="r-source" value="*">
     <div class="form-group"><label class="form-label">目标模型</label><select class="form-input" id="r-target">${modelOpts}</select></div>
     <input type="hidden" id="r-proxy" value="${escHtml(data.request_type)}">`,
    `<button class="btn btn-secondary" onclick="closeModal()">取消</button><button class="btn btn-primary" onclick="saveRoute(0, true)">保存</button>`);
}

async function saveRoute(editId, allowFallback = false) {
  const data = {
    source: document.getElementById('r-source').value.trim(),
    target_model_id: parseInt(document.getElementById('r-target').value),
    request_type: document.getElementById('r-proxy').value,
  };
  if (!data.source) { alert('源模型名不能为空'); return; }
  if (!editId && data.source === '*' && !allowFallback) { alert('❌ 不能通过此按钮添加回退路由，请使用「新增回退路由」按钮'); return; }
  if (editId) {
    await api('/api/routes/' + editId, { method: 'PUT', body: JSON.stringify(data) });
  } else {
    await api('/api/routes', { method: 'POST', body: JSON.stringify(data) });
  }
  closeModal();
  bus.emit('config:route-changed', {});
  loadRouteTable(currentRequestType);
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
  else { bus.emit('config:route-changed', {}); loadRouteTable(currentRequestType); }
}

// ===== Page Loader =====
async function loadRoutePage() {
  document.getElementById('page-routes').innerHTML = `
    <div class="proxy-tabs" style="display:flex;gap:8px;margin-bottom:16px;">
      <button class="proxy-tab btn btn-sm active" data-pt="responses" onclick="switchRequestType('responses')">🔌 Responses</button>
      <button class="proxy-tab btn btn-sm" data-pt="messages" onclick="switchRequestType('messages')">✉️ Messages</button>
      <button class="proxy-tab btn btn-sm" data-pt="chat_completions" onclick="switchRequestType('chat_completions')">🔗 Chat Completions</button>
    </div>
    <div class="table-card" style="margin-bottom:20px">
      <div class="table-header">
        <span class="table-title">🔀 路由映射</span>
        <div style="display:flex;gap:8px;">
          <button class="btn btn-primary btn-sm" onclick="showRouteModal()">+ 新增路由</button>
          <button class="btn btn-secondary btn-sm" onclick="showFallbackModal()">+ 新增回退路由</button>
        </div>
      </div>
      <div class="table-scroll">
        <table id="route-table">
          <thead><tr><th>源模型</th><th>→ 目标模型</th><th>上游</th><th>Request</th><th>状态</th><th>操作</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>`;
  loadRouteTable('chat_completions');
}

function initRoutePage() {
  // No-op — HTML injected dynamically
}

// ===== Exports =====
export { loadRoutePage, initRoutePage, loadRouteTable, showRouteModal, showFallbackModal, saveRoute, confirmDeleteRoute, switchRequestType };

// ===== Global Scope Mounting =====
window.switchRequestType = switchRequestType;
window.showRouteModal = showRouteModal;
window.showFallbackModal = showFallbackModal;
window.saveRoute = saveRoute;
window.confirmDeleteRoute = confirmDeleteRoute;
window.loadRoutePage = loadRoutePage;
window.initRoutePage = initRoutePage;
