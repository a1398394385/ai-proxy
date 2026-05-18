import { api, escHtml, showModal, closeModal, bus, on, FORMAT_LABELS, FORMAT_COLORS, customSelectHtml, wireCustomSelect, updateCustomSelect } from '../core.js';

// ===== 路由管理 =====

let currentRequestType = 'chat_completions';

const RT_CONFIG = {
  responses: { icon: '🔌', label: 'Responses' },
  messages: { icon: '✉️', label: 'Messages' },
  chat_completions: { icon: '🔗', label: 'Chat Completions' }
};

// ─── 页面逻辑 ───

function switchRequestType(rt) {
  currentRequestType = rt;
  document.querySelectorAll('.route-type-card').forEach(b => b.classList.toggle('active', b.dataset.pt === rt));
  loadRouteTable(rt);
  loadAgentRouteTable(rt);
}

async function loadRouteTable(requestType) {
  let url = '/api/routes';
  if (requestType) url += '?request_type=' + encodeURIComponent(requestType);
  const data = await api(url);
  const tbody = document.querySelector('#route-table tbody');
  if (tbody) tbody.innerHTML = data.routes.map(r => {
    const isFallback = r.source === '*';
    const isDisabled = !r.upstream_active;
    const rowClass = [isFallback ? 'route-fallback' : '', isDisabled ? 'route-disabled' : ''].filter(Boolean).join(' ');
    return `<tr class="${rowClass}">
      <td>${isFallback
        ? '<span class="badge badge-purple">★ fallback</span>'
        : '<span class="badge badge-purple">' + escHtml(r.source) + '</span>'}</td>
      <td><span class="badge badge-green">${escHtml(r.target_name)}</span></td>
      <td><span class="badge" style="background:hsl(var(--muted) / 0.7);color:hsl(var(--muted-foreground))">${escHtml(r.upstream_name || r.upstream_id)}</span></td>
      <td><span class="badge ${FORMAT_COLORS[r.upstream_format] || ''}">${FORMAT_LABELS[r.upstream_format] || r.upstream_format || '-'}</span></td>
      <td><span class="route-status"><span class="route-status-dot ${r.upstream_active ? 'active' : 'inactive'}"></span>${r.upstream_active ? '活跃' : '已禁用'}</span></td>
      <td>
        <div class="route-actions">
          <button class="btn btn-secondary btn-sm" data-action="showRouteModal" data-id="${r.id}">编辑</button>
          <button class="btn btn-danger btn-sm" data-action="confirmDeleteRoute" data-id="${r.id}" data-source="${escHtml(r.source)}">删除</button>
        </div>
      </td>
    </tr>`;
  }).join('') || '<tr><td colspan="6" class="empty-state"><div class="empty-state-icon">🔀</div>暂无路由配置</td></tr>';
}

async function loadAgentRouteTable(requestType) {
  let url = '/api/agent-routes';
  if (requestType) url += '?request_type=' + encodeURIComponent(requestType);
  const tbody = document.querySelector('#agent-route-table tbody');
  const countEl = document.getElementById('agent-route-count');
  try {
    const data = await api(url);
    if (countEl) countEl.textContent = '覆盖层 · ' + (data.routes ? data.routes.length : 0);
    if (tbody) tbody.innerHTML = (data.routes || []).map(r => {
    const isDisabled = !r.upstream_active;
    const rowClass = isDisabled ? 'route-disabled' : '';
    return `<tr class="${rowClass}">
      <td><span class="badge badge-amber">${escHtml(r.source)}</span></td>
      <td><span class="badge badge-green">${escHtml(r.target_name)}</span>
          <span class="route-override-hint">← 覆盖主路由</span></td>
      <td><span class="badge" style="background:hsl(var(--muted) / 0.7);color:hsl(var(--muted-foreground))">${escHtml(r.upstream_name || r.upstream_id)}</span></td>
      <td><span class="badge ${FORMAT_COLORS[r.upstream_format] || ''}">${FORMAT_LABELS[r.upstream_format] || r.upstream_format || '-'}</span></td>
      <td><span class="route-status"><span class="route-status-dot ${r.upstream_active ? 'active' : 'inactive'}"></span>${r.upstream_active ? '活跃' : '已禁用'}</span></td>
      <td>
        <div class="route-actions">
          <button class="btn btn-secondary btn-sm" data-action="showAgentRouteModal" data-id="${r.id}">编辑</button>
          <button class="btn btn-danger btn-sm" data-action="confirmDeleteAgentRoute" data-id="${r.id}" data-source="${escHtml(r.source)}">删除</button>
        </div>
      </td>
    </tr>`;
  }).join('') || '<tr><td colspan="6" class="empty-state"><div class="empty-state-icon">🤖</div>暂无 Agent 路由配置<br><span style="font-size:11px">子 agent 请求将使用主路由表</span></td></tr>';
  } catch (e) {
    if (countEl) countEl.textContent = '覆盖层 · ?';
    if (tbody) tbody.innerHTML = '<tr><td colspan="6" class="empty-state">加载失败</td></tr>';
  }
}

// ─── 级联选择：上游 → 模型 ───
function buildCascadingSelect(upstreams, models, selectedUpstreamId, selectedModelId) {
  const fmtLabel = { responses: 'Resp', messages: 'Msg', chat_completions: 'Chat' };
  const fmtStyle = {
    responses: 'background:hsl(var(--purple) / 0.15);color:hsl(var(--purple))',
    messages: 'background:hsl(var(--blue) / 0.15);color:hsl(var(--blue))',
    chat_completions: 'background:hsl(var(--green) / 0.15);color:hsl(var(--green))'
  };

  const upOpts = upstreams.upstreams.map(u => ({
    value: u.id,
    label: u.name,
    hint: fmtLabel[u.format] || u.format,
    hintStyle: fmtStyle[u.format] || '',
    selected: u.id === selectedUpstreamId,
    disabled: !u.is_active
  }));

  let modelOpts;
  if (!selectedUpstreamId) {
    modelOpts = [{ value: '', label: '-- 请先选择上游 --', selected: true }];
  } else if (!upstreams.upstreams.find(u => u.id === selectedUpstreamId)) {
    modelOpts = [{ value: '', label: '上游不存在或已变更', selected: true }];
  } else {
    const mlist = models.models.filter(m => m.upstream_id === selectedUpstreamId);
    modelOpts = mlist.length === 0
      ? [{ value: '', label: '暂无模型', selected: true }]
      : mlist.map(m => ({ value: m.id, label: m.name, selected: m.id === selectedModelId }));
  }

  return `<div class="form-group"><label class="form-label">上游</label>${customSelectHtml('r-upstream', upOpts, '-- 选择上游 --')}</div>
    <div class="form-group"><label class="form-label">目标模型</label>${customSelectHtml('r-target', modelOpts, '-- 请先选择上游 --')}</div>`;
}

function bindCascadeModelSelect() {
  const upHidden = document.getElementById('r-upstream');
  if (!upHidden) return;

  wireCustomSelect('r-upstream');
  wireCustomSelect('r-target');

  upHidden.addEventListener('change', async () => {
    const upstreamId = upHidden.value;
    if (!upstreamId) {
      updateCustomSelect('r-target', [{ value: '', label: '-- 请先选择上游 --', selected: true }], '-- 请先选择上游 --');
      return;
    }
    updateCustomSelect('r-target', [{ value: '', label: '加载中…', selected: true }], '加载中…');
    try {
      const data = await api('/api/models?upstream_id=' + encodeURIComponent(upstreamId));
      const mlist = data.models || [];
      updateCustomSelect('r-target',
        mlist.length === 0
          ? [{ value: '', label: '暂无模型', selected: true }]
          : mlist.map(m => ({ value: m.id, label: m.name, selected: false })),
        mlist.length === 0 ? '暂无模型' : '选择模型');
    } catch (_) {
      updateCustomSelect('r-target', [{ value: '', label: '加载失败', selected: true }], '加载失败');
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
  const cascadingHtml = buildCascadingSelect(upstreams, models, routeUpstreamId, routeModelId);
  const sourceField = data.source === '*'
    ? `<input type="text" class="form-input" value="* (fallback)" readonly><input type="hidden" id="r-source" value="*">`
    : `<input type="text" class="form-input" id="r-source" value="${escHtml(data.source)}" placeholder="如 gpt-4o">
       <div class="form-hint">客户端请求的模型名称，路由会将其转发到目标模型</div>`;
  showModal(title,
    `<div class="form-group"><label class="form-label">源模型名</label>${sourceField}</div>
     <hr class="form-divider">
     ${cascadingHtml}
     <input type="hidden" id="r-proxy" value="${escHtml(data.request_type)}">`,
    `<button class="btn btn-secondary" data-action="closeModal">取消</button><button class="btn btn-primary" data-action="saveRoute" data-edit-id="${editId || 0}">保存路由</button>`);
  const modal = document.querySelector('.modal');
  if (modal) modal.classList.add('route-modal');
  bindCascadeModelSelect();
}

async function showAgentRouteModal(editId) {
  let data = { source: '', target_model_id: '', request_type: currentRequestType };
  let title = '新增 Agent 路由';
  let routeUpstreamId = null;
  let routeModelId = null;
  let models, upstreams;
  try {
    if (editId) {
      title = '编辑 Agent 路由 #' + editId;
      const routes = await api('/api/agent-routes');
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
  const cascadingHtml = buildCascadingSelect(upstreams, models, routeUpstreamId, routeModelId);
  showModal(title,
    `<div class="form-group"><label class="form-label">源模型名</label>
       <input type="text" class="form-input" id="r-source" value="${escHtml(data.source)}" placeholder="如 claude-sonnet-4-6">
       <div class="form-hint">子 agent 请求的模型名称，匹配时覆盖主路由指向</div>
     </div>
     <hr class="form-divider">
     ${cascadingHtml}
     <input type="hidden" id="r-proxy" value="${escHtml(data.request_type)}">`,
    `<button class="btn btn-secondary" data-action="closeModal">取消</button><button class="btn btn-primary" data-action="saveAgentRoute" data-edit-id="${editId || 0}">保存 Agent 路由</button>`);
  const modal = document.querySelector('.modal');
  if (modal) modal.classList.add('route-modal');
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
  const cascadingHtml = buildCascadingSelect(upstreams, models, null, null);
  showModal('新增回退路由',
    `<div class="form-group"><label class="form-label">源模型名</label><input type="text" class="form-input" value="* (fallback)" readonly><input type="hidden" id="r-source" value="*"></div>
     <hr class="form-divider">
     ${cascadingHtml}
     <input type="hidden" id="r-proxy" value="${escHtml(data.request_type)}">`,
    `<button class="btn btn-secondary" data-action="closeModal">取消</button><button class="btn btn-primary" data-action="saveRoute" data-edit-id="0" data-fallback="true">保存路由</button>`);
  const modal = document.querySelector('.modal');
  if (modal) modal.classList.add('route-modal');
  bindCascadeModelSelect();
}

async function saveRoute(editId, allowFallback = false) {
  const data = {
    source: document.getElementById('r-source').value.trim(),
    target_model_id: parseInt(document.getElementById('r-target').value),
    request_type: document.getElementById('r-proxy').value,
  };
  if (!data.source) { alert('源模型名不能为空'); return; }
  if (!editId && data.source === '*' && !allowFallback) { alert('不能通过此按钮添加回退路由，请使用「新增回退路由」按钮'); return; }
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
    if (starCount <= 1) { alert('不能删除最后一条 * fallback 路由'); return; }
  }
  if (!confirm('确认删除路由 "' + source + '"？')) return;
  const result = await api('/api/routes/' + id, { method: 'DELETE' });
  if (result.error) { alert(result.error); }
  else { bus.emit('config:route-changed', {}); loadRouteTable(currentRequestType); }
}

async function saveAgentRoute(editId) {
  const data = {
    source: document.getElementById('r-source').value.trim(),
    target_model_id: parseInt(document.getElementById('r-target').value),
    request_type: document.getElementById('r-proxy').value,
  };
  if (!data.source) { alert('源模型名不能为空'); return; }
  if (data.source === '*') { alert('Agent 路由不支持 * fallback'); return; }
  if (!data.target_model_id) { alert('请选择目标模型'); return; }
  if (editId) {
    await api('/api/agent-routes/' + editId, { method: 'PUT', body: JSON.stringify(data) });
  } else {
    await api('/api/agent-routes', { method: 'POST', body: JSON.stringify(data) });
  }
  closeModal();
  bus.emit('config:route-changed', {});
  loadAgentRouteTable(currentRequestType);
}

async function confirmDeleteAgentRoute(id, source) {
  if (!confirm('确认删除 Agent 路由 "' + source + '"？')) return;
  const result = await api('/api/agent-routes/' + id, { method: 'DELETE' });
  if (result.error) { alert(result.error); }
  else { bus.emit('config:route-changed', {}); loadAgentRouteTable(currentRequestType); }
}

// ===== Page Loader =====
async function loadRoutePage() {
  const typeCards = Object.entries(RT_CONFIG).map(([key, cfg]) =>
    `<button class="route-type-card${key === 'chat_completions' ? ' active' : ''}" data-action="switchRequestType" data-pt="${key}">
      <span class="rtc-icon">${cfg.icon}</span>
      <span class="rtc-label">${cfg.label}</span>
    </button>`
  ).join('');

  document.getElementById('page-routes').innerHTML = `
    <div class="page-header">
      <div class="page-title">
        <span class="page-title-icon">🔀</span>
        路由映射
      </div>
      <div class="page-actions">
        <button class="btn btn-secondary btn-sm" data-action="showFallbackModal">+ 回退路由</button>
        <button class="btn btn-primary btn-sm" data-action="showRouteModal">+ 新增路由</button>
      </div>
    </div>
    <div class="route-type-cards">${typeCards}</div>
    <div class="table-card">
      <div class="table-scroll">
        <table id="route-table">
          <thead><tr><th>源模型</th><th>目标模型</th><th>上游</th><th>请求格式</th><th>状态</th><th>操作</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>
    <div class="table-card agent-route-card">
      <div class="table-header">
        <div class="table-title">
          <span>🤖 Agent 路由</span>
          <span class="agent-badge" id="agent-route-count">覆盖层 · 0</span>
        </div>
        <div class="page-actions">
          <button class="btn btn-secondary btn-sm" data-action="showAgentRouteModal">+ 新增 Agent 路由</button>
        </div>
      </div>
      <div class="table-scroll">
        <table id="agent-route-table">
          <thead><tr><th>源模型</th><th>覆盖目标</th><th>上游</th><th>请求格式</th><th>状态</th><th>操作</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>`;
  loadRouteTable('chat_completions');
  loadAgentRouteTable('chat_completions');
}

function initRoutePage() {
  on('switchRequestType', (e, el) => switchRequestType(el.dataset.pt));
  on('showRouteModal', (e, el) => el.dataset.id ? showRouteModal(parseInt(el.dataset.id)) : showRouteModal());
  on('showAgentRouteModal', (e, el) => el.dataset.id ? showAgentRouteModal(parseInt(el.dataset.id)) : showAgentRouteModal());
  on('showFallbackModal', showFallbackModal);
  on('saveRoute', (e, el) => {
    const editId = parseInt(el.dataset.editId) || 0;
    const allowFallback = el.dataset.fallback === 'true';
    saveRoute(editId || 0, allowFallback);
  });
  on('saveAgentRoute', (e, el) => saveAgentRoute(parseInt(el.dataset.editId) || 0));
  on('confirmDeleteRoute', (e, el) => confirmDeleteRoute(parseInt(el.dataset.id), el.dataset.source));
  on('confirmDeleteAgentRoute', (e, el) => confirmDeleteAgentRoute(parseInt(el.dataset.id), el.dataset.source));
}

export { loadRoutePage, initRoutePage, loadRouteTable, showRouteModal, showFallbackModal, saveRoute, confirmDeleteRoute, switchRequestType, loadAgentRouteTable, showAgentRouteModal, saveAgentRoute, confirmDeleteAgentRoute };
