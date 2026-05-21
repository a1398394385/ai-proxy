import { api, escHtml, showModal, closeModal, bus, on, FORMAT_LABELS, FORMAT_COLORS, customSelectHtml, wireCustomSelect, updateCustomSelect } from '../core.js';

// ===== 路由管理 =====

let currentRequestType = localStorage.getItem('defaultRouteType') || 'messages';

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
  loadTemplateSidebar(rt);
}

async function loadRouteTable(requestType) {
  let url = '/api/routes';
  if (requestType) url += '?request_type=' + encodeURIComponent(requestType);
  const data = await api(url);
  const tbody = document.querySelector('#route-table tbody');
  if (tbody) {
    const sorted = data.routes.slice().sort((a, b) => {
      if (a.source === '*') return -1;
      if (b.source === '*') return 1;
      return (a.source || '').localeCompare(b.source || '');
    });
    tbody.innerHTML = sorted.map(r => {
    const isFallback = r.source === '*';
    const isDisabled = !r.upstream_active;
    const rowClass = [isFallback ? 'route-fallback' : '', isDisabled ? 'route-disabled' : ''].filter(Boolean).join(' ');
    return `<tr class="${rowClass}">
      <td>${isFallback
        ? '<span class="badge badge-purple">★ fallback</span>'
        : '<span class="badge badge-purple">' + escHtml(r.source) + '</span>'}</td>
      <td>${r.target_name !== null && r.target_name !== undefined
        ? '<span class="badge badge-green">' + escHtml(r.target_name) + '</span>'
        : '<span class="badge badge-red" style="background:hsl(0 100% 50% / 0.15) !important;color:hsl(0 100% 50%) !important">失效</span>'}</td>
      <td><span class="badge" style="background:hsl(var(--muted) / 0.7);color:hsl(var(--muted-foreground))">${r.upstream_name || '(已删除)'}</span></td>
      <td><span class="badge ${FORMAT_COLORS[r.upstream_format] || ''}">${FORMAT_LABELS[r.upstream_format] || r.upstream_format || '-'}</span></td>
      <td>${r.target_name === null || r.target_name === undefined
        ? '<span class="route-status"><span class="route-status-dot invalid"></span>失效</span>'
        : '<span class="route-status"><span class="route-status-dot ' + (r.upstream_active ? 'active' : 'inactive') + '"></span>' + (r.upstream_active ? '活跃' : '已禁用') + '</span>'}</td>
      <td>
        <div class="route-actions">
          <button class="btn btn-secondary btn-sm" data-action="showRouteModal" data-id="${r.id}">编辑</button>
          <button class="btn btn-danger btn-sm" data-action="confirmDeleteRoute" data-id="${r.id}" data-source="${escHtml(r.source)}">删除</button>
        </div>
      </td>
    </tr>`;
  }).join('') || '<tr><td colspan="6" class="empty-state"><div class="empty-state-icon">🔀</div>暂无路由配置</td></tr>';
  }
}

async function loadAgentRouteTable(requestType) {
  let url = '/api/agent-routes';
  if (requestType) url += '?request_type=' + encodeURIComponent(requestType);
  const tbody = document.querySelector('#agent-route-table tbody');
  const countEl = document.getElementById('agent-route-count');
  try {
    const data = await api(url);
    if (countEl) countEl.textContent = '覆盖层 · ' + (data.routes ? data.routes.length : 0);
    if (tbody) {
      const sortedAgents = (data.routes || []).slice().sort((a, b) => (a.source || '').localeCompare(b.source || ''));
      tbody.innerHTML = sortedAgents.map(r => {
    const isInvalid = r.target_name === null || r.target_name === undefined;
    const isDisabled = !r.upstream_active && !isInvalid;
    const rowClass = isInvalid ? 'route-invalid' : (isDisabled ? 'route-disabled' : '');
    return `<tr class="${rowClass}">
      <td><span class="badge badge-amber">${escHtml(r.source)}</span></td>
      <td>${isInvalid
        ? '<span class="badge badge-red" style="background:hsl(0 100% 50% / 0.15) !important;color:hsl(0 100% 50%) !important">失效</span>'
        : '<span class="badge badge-green">' + escHtml(r.target_name) + '</span>'}
          ${isInvalid ? '' : '<span class="route-override-hint">← 覆盖主路由</span>'}</td>
      <td><span class="badge" style="background:hsl(var(--muted) / 0.7);color:hsl(var(--muted-foreground))">${r.upstream_name || '(已删除)'}</span></td>
      <td><span class="badge ${FORMAT_COLORS[r.upstream_format] || ''}">${FORMAT_LABELS[r.upstream_format] || r.upstream_format || '-'}</span></td>
      <td>${isInvalid
        ? '<span class="route-status"><span class="route-status-dot invalid"></span>失效</span>'
        : '<span class="route-status"><span class="route-status-dot ' + (r.upstream_active ? 'active' : 'inactive') + '"></span>' + (r.upstream_active ? '活跃' : '已禁用') + '</span>'}</td>
      <td>
        <div class="route-actions">
          <button class="btn btn-secondary btn-sm" data-action="showAgentRouteModal" data-id="${r.id}">编辑</button>
          <button class="btn btn-danger btn-sm" data-action="confirmDeleteAgentRoute" data-id="${r.id}" data-source="${escHtml(r.source)}">删除</button>
        </div>
      </td>
    </tr>`;
  }).join('') || '<tr><td colspan="6" class="empty-state"><div class="empty-state-icon">🤖</div>暂无 Agent 路由配置<br><span style="font-size:11px">子 agent 请求将使用主路由表</span></td></tr>';
  }
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
    `<button class="route-type-card${key === currentRequestType ? ' active' : ''}" data-action="switchRequestType" data-pt="${key}">
      <span class="rtc-icon">${cfg.icon}</span>
      <span class="rtc-label">${cfg.label}</span>
    </button>`
  ).join('');

  document.getElementById('page-routes').innerHTML = `
    <div class="routes-layout">
      <div class="routes-main">
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
        </div>
      </div>
      <aside class="template-sidebar" id="template-sidebar">
        <div class="template-sidebar-header">
          <span class="template-sidebar-title">📋 路由模板</span>
          <button class="btn btn-primary btn-sm" data-action="saveTemplate" title="保存当前路由为模板">+ 保存</button>
        </div>
        <div class="template-list" id="template-list">
          <div class="template-empty">暂无模板<br><span style="font-size:11px;color:hsl(var(--muted-foreground))">点击「+ 保存」创建</span></div>
        </div>
      </aside>
    </div>`;
  loadRouteTable(currentRequestType);
  loadAgentRouteTable(currentRequestType);
  loadTemplateSidebar(currentRequestType);

  // hover 预览事件（在 innerHTML 渲染后绑定，确保元素存在）
  const sidebar = document.getElementById('template-sidebar');
  if (sidebar) {
    sidebar.addEventListener('mouseover', (e) => {
      const item = e.target.closest('.template-item');
      const from = e.relatedTarget ? e.relatedTarget.closest('.template-item') : null;
      if (item && from !== item && !item.classList.contains('previewing') && !item.classList.contains('active')) {
        if (previewTimer) clearTimeout(previewTimer);
        previewTimer = setTimeout(() => templateHover(item), 300);
      }
    });
    sidebar.addEventListener('mouseout', (e) => {
      const item = e.target.closest('.template-item');
      const related = e.relatedTarget ? e.relatedTarget.closest('.template-item') : null;
      if (item && related !== item) {
        if (previewTimer) clearTimeout(previewTimer);
        previewTimer = null;
        templateLeave(item);
      }
    });
  }
}


// ─── 模板边栏 ─────────────────────────────────────────
let cachedTemplates = {};
let activeTemplateId = parseInt(localStorage.getItem('activeTemplateId') || '0') || null;
let previewTimer = null;

async function loadTemplateSidebar(requestType) {
  const listEl = document.getElementById('template-list');
  if (!listEl) return;
  try {
    const data = await api('/api/templates?request_type=' + encodeURIComponent(requestType));
    cachedTemplates[requestType] = data.templates || [];
  } catch (_) {
    cachedTemplates[requestType] = [];
  }
  renderTemplateSidebar(requestType);
}

function renderTemplateSidebar(requestType) {
  const listEl = document.getElementById('template-list');
  if (!listEl) return;
  const templates = cachedTemplates[requestType] || [];
  if (templates.length === 0) {
    listEl.innerHTML = '<div class="template-empty">暂无模板<br><span style="font-size:11px;color:hsl(var(--muted-foreground))">点击「+ 保存」创建</span></div>';
    return;
  }
  listEl.innerHTML = templates.map(t => `
    <div class="template-item${t.id === activeTemplateId ? ' active' : ''}" data-id="${t.id}" data-name="${escHtml(t.name)}"
         data-action="templateHover" data-leave-action="templateLeave">
      <div class="template-item-name">${escHtml(t.name)}</div>
      <div class="template-item-meta">
        ${t.last_applied_at ? '上次应用: ' + t.last_applied_at : '未应用'}
      </div>
      <div class="template-item-actions">
        <button class="btn btn-primary btn-xs" data-action="applyTemplate" data-id="${t.id}">应用</button>
        <button class="btn btn-danger btn-xs" data-action="deleteTemplate" data-id="${t.id}" data-name="${escHtml(t.name)}">删除</button>
      </div>
    </div>`).join('');
}

// hover 预览：mouseenter → 获取预览 → 切换表格
async function templateHover(el) {
  const tid = el.dataset.id;
  if (!tid) return;
  try {
    const preview = await api('/api/templates/' + tid + '/preview');
    // 备份当前路由表内容
    const routeTbody = document.querySelector('#route-table tbody');
    const agentTbody = document.querySelector('#agent-route-table tbody');
    if (routeTbody) {
      if (!routeTbody.dataset.original) {
        routeTbody.dataset.original = routeTbody.innerHTML;
      }
      const sortedRoutes = (preview.model_routes || []).slice().sort((a, b) => {
        if (a.source === '*') return -1;
        if (b.source === '*') return 1;
        return (a.source || '').localeCompare(b.source || '');
      });
      routeTbody.innerHTML = sortedRoutes.map(r => {
        const isFallback = r.source === '*';
        const valid = r.valid !== false;
        return `<tr class="${isFallback ? 'route-fallback' : ''} ${valid ? '' : 'route-invalid'}">
          <td>${isFallback ? '<span class="badge badge-purple">\u2605 fallback</span>' : '<span class="badge badge-purple">' + escHtml(r.source) + '</span>'}</td>
          <td>${valid
            ? '<span class="badge badge-green">' + escHtml(r.target_name) + '</span>'
            : '<span class="badge badge-red" style="background:hsl(0 100% 50% / 0.15) !important;color:hsl(0 100% 50%) !important">失效</span>'}</td>
          <td><span class="badge" style="background:hsl(var(--muted) / 0.7);color:hsl(var(--muted-foreground))">${r.upstream_name || '(已删除)'}</span></td>
          <td><span class="badge">${r.upstream_format || '-'}</span></td>
          <td>${!valid
            ? '<span class="route-status"><span class="route-status-dot invalid"></span>失效</span>'
            : '<span class="route-status"><span class="route-status-dot ' + (r.upstream_active ? 'active' : 'inactive') + '"></span>' + (r.upstream_active ? '\u6d3b\u8dc3' : '\u5df2\u7981\u7528') + '</span>'}</td>
          <td><span class="preview-badge">预览</span></td>
        </tr>`;
      }).join('') || '<tr><td colspan="6" class="empty-state">预览：无路由</td></tr>';
    }
    if (agentTbody) {
      if (!agentTbody.dataset.original) {
        agentTbody.dataset.original = agentTbody.innerHTML;
      }
      const sortedAgentRoutes = (preview.agent_routes || []).slice().sort((a, b) => (a.source || '').localeCompare(b.source || ''));
      agentTbody.innerHTML = sortedAgentRoutes.map(r => {
        const valid = r.valid !== false;
        return `<tr class="${valid ? '' : 'route-invalid'}">
          <td><span class="badge badge-amber">${escHtml(r.source)}</span></td>
          <td>${valid
            ? '<span class="badge badge-green">' + escHtml(r.target_name) + '</span>'
            : '<span class="badge badge-red" style="background:hsl(0 100% 50% / 0.15) !important;color:hsl(0 100% 50%) !important">失效</span>'}</td>
          <td><span class="badge" style="background:hsl(var(--muted) / 0.7);color:hsl(var(--muted-foreground))">${r.upstream_name || '(已删除)'}</span></td>
          <td><span class="badge">${r.upstream_format || '-'}</span></td>
          <td>${!valid
            ? '<span class="route-status"><span class="route-status-dot invalid"></span>失效</span>'
            : '<span class="route-status"><span class="route-status-dot ' + (r.upstream_active ? 'active' : 'inactive') + '"></span>' + (r.upstream_active ? '\u6d3b\u8dc3' : '\u5df2\u7981\u7528') + '</span>'}</td>
          <td><span class="preview-badge">预览</span></td>
        </tr>`;
      }).join('') || '<tr><td colspan="6" class="empty-state">预览：无 Agent 路由</td></tr>';
    }
    // 高亮当前预览的模板
    document.querySelectorAll('.template-item').forEach(el => el.classList.remove('previewing'));
    el.classList.add('previewing');
  } catch (_) {
    // 预览失败时保持现有内容
  }
}

async function templateLeave(el) {
  el.classList.remove('previewing');
  const routeTbody = document.querySelector('#route-table tbody');
  const agentTbody = document.querySelector('#agent-route-table tbody');
  if (routeTbody && routeTbody.dataset.original) {
    routeTbody.innerHTML = routeTbody.dataset.original;
    delete routeTbody.dataset.original;
  }
  if (agentTbody && agentTbody.dataset.original) {
    agentTbody.innerHTML = agentTbody.dataset.original;
    delete agentTbody.dataset.original;
  }
}

async function applyTemplate(el) {
  const tid = parseInt(el.dataset.id);
  if (!tid) return;
  try {
    const result = await api('/api/templates/' + tid + '/apply', { method: 'POST' });
    if (result.error) { alert(result.error); return; }
    activeTemplateId = tid;
    localStorage.setItem('activeTemplateId', tid);
    // 清除预览备份，避免 templateLeave 恢复旧数据覆盖新数据
    document.querySelectorAll('#route-table tbody, #agent-route-table tbody').forEach(tb => {
      delete tb.dataset.original;
    });
    if (result.invalid_count > 0) {
      alert('应用完成。' + result.applied + ' 条路由已加载，其中 ' + result.invalid_count + ' 条路由因模型已删除，target_model_id 设为 NULL。');
    }
    loadRouteTable(currentRequestType);
    loadAgentRouteTable(currentRequestType);
    loadTemplateSidebar(currentRequestType);
  } catch (e) {
    alert('应用失败: ' + (e.message || e));
  }
}

async function saveTemplate() {
  if (activeTemplateId) {
    const templates = cachedTemplates[currentRequestType] || [];
    const active = templates.find(t => t.id === activeTemplateId);
    const activeName = active ? escHtml(active.name) : '(未知)';
    showModal('保存模板',
      `<div style="text-align:center;padding:8px 0">
        <p style="margin-bottom:16px;color:hsl(var(--muted-foreground));font-size:13px">当前活动模板：<strong style="color:hsl(var(--foreground))">${activeName}</strong></p>
        <button class="btn btn-primary" data-action="updateActiveTemplate" style="width:100%;margin-bottom:10px">更新「${activeName}」</button>
        <button class="btn btn-secondary" data-action="saveAsNewTemplate" style="width:100%">另存为新模板</button>
      </div>`,
      `<button class="btn btn-secondary" data-action="closeModal">取消</button>`
    );
  } else {
    showSaveNewTemplateModal();
  }
}

function showSaveNewTemplateModal() {
  showModal('新建模板',
    `<div class="form-group">
      <label class="form-label">模板名称</label>
      <input type="text" class="form-input" id="template-name" placeholder="输入模板名称" maxlength="100">
    </div>`,
    `<button class="btn btn-secondary" data-action="closeModal">取消</button>
     <button class="btn btn-primary" data-action="confirmSaveNewTemplate">保存</button>`
  );
  const modal = document.querySelector('.modal');
  if (modal) modal.classList.add('route-modal');
}

async function deleteTemplate(el) {
  const tid = el.dataset.id;
  const name = el.dataset.name;
  if (!tid) return;
  if (!confirm('确认删除模板 "' + name + '"？这将不影响当前路由。')) return;
  try {
    const result = await api('/api/templates/' + tid, { method: 'DELETE' });
    if (result.error) { alert(result.error); return; }
    if (parseInt(tid) === activeTemplateId) {
      activeTemplateId = null;
      localStorage.removeItem('activeTemplateId');
    }
    loadTemplateSidebar(currentRequestType);
  } catch (e) {
    alert('删除失败: ' + (e.message || e));
  }
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
  // 模板边栏交互
  on('saveTemplate', saveTemplate);
  on('applyTemplate', (e, el) => applyTemplate(el));
  on('deleteTemplate', (e, el) => deleteTemplate(el));
  on('updateActiveTemplate', async () => {
    try {
      const result = await api('/api/templates/' + activeTemplateId, {
        method: 'PUT',
        body: JSON.stringify({ resnapshot: true, request_type: currentRequestType })
      });
      if (result.error) { alert(result.error); return; }
      closeModal();
      loadTemplateSidebar(currentRequestType);
    } catch (e) {
      alert('更新失败: ' + (e.message || e));
    }
  });
  on('saveAsNewTemplate', () => showSaveNewTemplateModal());
  on('confirmSaveNewTemplate', async () => {
    const nameInput = document.getElementById('template-name');
    const name = nameInput ? nameInput.value.trim() : '';
    if (!name) { alert('模板名称不能为空'); return; }
    if (name.length > 100) { alert('模板名称不能超过 100 字符'); return; }
    if (name.includes('/')) { alert('模板名称不能包含 /'); return; }
    try {
      const result = await api('/api/templates', {
        method: 'POST',
        body: JSON.stringify({ name, request_type: currentRequestType })
      });
      if (result.error) { alert(result.error); return; }
      activeTemplateId = result.id;
      localStorage.setItem('activeTemplateId', result.id);
      closeModal();
      loadTemplateSidebar(currentRequestType);
    } catch (e) {
      alert('保存失败: ' + (e.message || e));
    }
  });
}

export { loadRoutePage, initRoutePage, loadRouteTable, showRouteModal, showFallbackModal, saveRoute, confirmDeleteRoute, switchRequestType, loadAgentRouteTable, showAgentRouteModal, saveAgentRoute, confirmDeleteAgentRoute, loadTemplateSidebar, applyTemplate, saveTemplate, deleteTemplate };
