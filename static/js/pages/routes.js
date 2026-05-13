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

// ─── 级联选择：上游 → 模型 ───
function buildCascadingModelSelect(upstreams, models, selectedUpstreamId, selectedModelId) {
  const fmtLabel = { responses: 'Resp', messages: 'Msg', chat_completions: 'Chat' };
  let upOpts = '<option value="">-- 选择上游 --</option>';
  upstreams.upstreams.forEach(u => {
    const fmt = fmtLabel[u.format] || u.format;
    const label = escHtml(u.name) + (fmt ? ' (' + fmt + ')' : '');
    upOpts += `<option value="${u.id}"${u.id === selectedUpstreamId ? ' selected' : ''}${u.is_active ? '' : ' disabled'}>${label}</option>`;
  });
  let modelOpts, modelDisabled = 'disabled';
  if (!selectedUpstreamId) {
    modelOpts = '<option value="">-- 请先选择上游 --</option>';
  } else if (!upstreams.upstreams.find(u => u.id === selectedUpstreamId)) {
    modelOpts = '<option value="">上游不存在或已变更</option>';
  } else {
    const mlist = models.models.filter(m => m.upstream_id === selectedUpstreamId);
    if (mlist.length === 0) {
      modelOpts = '<option value="">暂无模型</option>';
    } else {
      modelDisabled = '';
      modelOpts = mlist.map(m => `<option value="${m.id}"${m.id === selectedModelId ? ' selected' : ''}>${escHtml(m.name)}</option>`).join('');
    }
  }
  return `
    <div class="form-group"><label class="form-label">上游</label><select class="form-input" id="r-upstream">${upOpts}</select></div>
    <div class="form-group"><label class="form-label">目标模型</label><select class="form-input" id="r-target" ${modelDisabled}>${modelOpts}</select></div>`;
}

function bindCascadeModelSelect() {
  const upSelect = document.getElementById('r-upstream');
  const modelSelect = document.getElementById('r-target');
  if (!upSelect || !modelSelect) return;
  upSelect.addEventListener('change', async () => {
    const upstreamId = upSelect.value;
    if (!upstreamId) {
      modelSelect.innerHTML = '<option value="">-- 请先选择上游 --</option>';
      modelSelect.disabled = true;
      return;
    }
    modelSelect.disabled = true;
    modelSelect.innerHTML = '<option value="">加载中…</option>';
    try {
      const data = await api('/api/models?upstream_id=' + encodeURIComponent(upstreamId));
      const mlist = data.models || [];
      modelSelect.innerHTML = mlist.length === 0
        ? '<option value="">暂无模型</option>'
        : mlist.map(m => `<option value="${m.id}">${escHtml(m.name)}</option>`).join('');
      modelSelect.disabled = false;
    } catch (_) {
      modelSelect.innerHTML = '<option value="">加载失败</option>';
    }
  });
}

// ─── 路由模态框 ───
async function showRouteModal(editId) {
  let data = { source: '', target_model_id: '', request_type: currentRequestType };
  let title = '新增路由';
  let routeUpstreamId = null;
  let routeModelId = null;
  let models, upstreams;
  try {
    if (editId) {
      title = '编辑路由 #' + editId;
      const routes = await api('/api/routes');
      const found = routes.routes.find(r => r.id === editId);
      if (found) data = found;
    }
    [models, upstreams] = await Promise.all([api('/api/models'), api('/api/upstreams')]);
    if (editId && data.target_model_id) {
      routeModelId = data.target_model_id;
      const tm = models.models.find(m => m.id === data.target_model_id);
      if (tm) routeUpstreamId = tm.upstream_id;
    }
  } catch (_) {
    alert('加载数据失败，请检查服务是否正常运行');
    return;
  }
  const cascadingHtml = buildCascadingModelSelect(upstreams, models, routeUpstreamId, routeModelId);
  const sourceField = data.source === '*'
    ? `<input type="text" class="form-input" value="* (fallback)" readonly style="background:hsl(var(--muted));color:hsl(var(--muted-foreground));cursor:not-allowed"><input type="hidden" id="r-source" value="*">`
    : `<input type="text" class="form-input" id="r-source" value="${escHtml(data.source)}" placeholder="如 gpt-4o">`;
  showModal(title,
    `<div class="form-group"><label class="form-label">源模型名</label>${sourceField}</div>
     ${cascadingHtml}
     <input type="hidden" id="r-proxy" value="${escHtml(data.request_type)}">`,
    `<button class="btn btn-secondary" onclick="closeModal()">取消</button><button class="btn btn-primary" onclick="saveRoute(${editId || 0})">保存</button>`);
  bindCascadeModelSelect();
}

async function showFallbackModal() {
  const data = { source: '*', target_model_id: '', request_type: currentRequestType };
  let models, upstreams;
  try {
    [models, upstreams] = await Promise.all([api('/api/models'), api('/api/upstreams')]);
  } catch (_) {
    alert('加载数据失败，请检查服务是否正常运行');
    return;
  }
  const cascadingHtml = buildCascadingModelSelect(upstreams, models, null, null);
  showModal('新增回退路由',
    `<div class="form-group"><label class="form-label">源模型名</label><input type="text" class="form-input" value="* (fallback)" readonly style="background:hsl(var(--muted));color:hsl(var(--muted-foreground));cursor:not-allowed"></div>
     <input type="hidden" id="r-source" value="*">
     ${cascadingHtml}
     <input type="hidden" id="r-proxy" value="${escHtml(data.request_type)}">`,
    `<button class="btn btn-secondary" onclick="closeModal()">取消</button><button class="btn btn-primary" onclick="saveRoute(0, true)">保存</button>`);
  bindCascadeModelSelect();
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
