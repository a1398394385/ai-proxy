import { api, escHtml, showModal, closeModal, bus, on, FORMAT_LABELS, FORMAT_COLORS, customSelectHtml, wireCustomSelect } from '../core.js';

// ===== 上游管理 =====

async function refreshConfigStatus() {
  try {
    const status = await api('/api/config/status');
    const ind = document.getElementById('status-proxy-indicator');
    const txt = document.getElementById('status-proxy-text');
    const cnt = document.getElementById('status-counts');
    if (status.proxy_reachable) {
      ind.style.background = 'hsl(var(--green))'; txt.textContent = 'proxy 在线';
    } else {
      ind.style.background = 'hsl(var(--orange))'; txt.textContent = 'proxy 离线';
    }
    cnt.textContent = status.config_db.upstreams + ' 上游 · ' + status.config_db.models + ' 模型 · ' + status.config_db.routes + ' 路由';
  } catch (e) {}
}

async function loadUpstreamTable() {
  const data = await api('/api/upstreams');
  upstreamDataMap = {};
  data.upstreams.forEach(u => { upstreamDataMap[String(u.id)] = u; });
  document.getElementById('upstream-count').textContent = data.upstreams.length + ' 个上游';
  const tbody = document.querySelector('#upstream-table tbody');

  tbody.innerHTML = data.upstreams.map(u =>
    `<tr data-action="toggleModelDrawer" data-id="${u.id}" style="${u.is_active ? '' : 'opacity:0.5'};cursor:pointer">
      <td><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${u.is_active ? 'hsl(var(--green))' : 'hsl(var(--red))'};"></span> ${u.is_active ? '活跃' : '已禁用'}</td>
      <td><span class="badge badge-blue">${escHtml(u.name)}</span></td>
      <td style="font-family:monospace;font-size:12px">${escHtml(u.base_url)}</td>
      <td><span class="badge ${FORMAT_COLORS[u.format] || ''}">${FORMAT_LABELS[u.format] || u.format || '-'}</span></td>
      <td>${u.timeout}s</td>
      <td>
        <button class="btn btn-secondary btn-sm" data-action="showUpstreamModal" data-id="${u.id}">编辑</button>
        <button class="btn btn-secondary btn-sm" data-action="testUpstream" data-id="${u.id}">测试</button>
        <button class="btn btn-danger btn-sm" data-action="showDeleteUpstreamModal" data-id="${u.id}">删除</button>
      </td>
    </tr>`
  ).join('');
}

// ===== Drawer (inline accordion) =====

let openDrawerUpstreamId = null;
let upstreamDataMap = {};

function toggleModelDrawer(el, upstreamId) {
  if (openDrawerUpstreamId === upstreamId) {
    closeDrawerRow();
    return;
  }
  closeDrawerRow();

  const clickedRow = el.closest('tr');
  if (!clickedRow) return;

  const tbody = clickedRow.parentElement;
  const drawerRow = document.createElement('tr');
  drawerRow.className = 'drawer-row';
  drawerRow.id = 'drawer-' + upstreamId;
  const detectBtn = '<button class="btn btn-detect" data-action="detectUpstreamModels" data-id="' + escHtml(upstreamId) + '" id="detect-btn-' + escHtml(upstreamId) + '">🔍 检测模型</button>';
  drawerRow.innerHTML =
    '<td colspan="7">' +
      '<div class="drawer-content">' +
        '<div class="drawer-header">🤖 模型列表 — 上游: ' + escHtml(upstreamDataMap[upstreamId]?.name || upstreamId) +
          '<button class="btn btn-primary btn-sm" data-action="showModelModalForUpstream" data-id="' + escHtml(upstreamId) + '" style="margin-left:auto;">＋ 新增模型</button>' + detectBtn + '</div>' +
        '<table class="drawer-model-table">' +
          '<thead><tr><th>模型名</th><th>所属上游</th><th>Multimodal</th><th>最大上下文</th><th>最大输入</th><th>最大输出</th><th>RPM</th><th>操作</th></tr></thead>' +
          '<tbody class="drawer-model-tbody"></tbody>' +
        '</table>' +
      '</div>' +
    '</td>';

  const nextRow = clickedRow.nextElementSibling;
  if (nextRow) {
    tbody.insertBefore(drawerRow, nextRow);
  } else {
    tbody.appendChild(drawerRow);
  }

  openDrawerUpstreamId = upstreamId;

  requestAnimationFrame(() => {
    const content = drawerRow.querySelector('.drawer-content');
    if (content) content.classList.add('slide-in');
  });

  loadModelTable(upstreamId);
}

function closeDrawerRow() {
  const existing = document.querySelector('tr.drawer-row');
  if (existing) existing.remove();
  openDrawerUpstreamId = null;
}

async function loadModelTable(upstreamId) {
  const url = '/api/models?upstream_id=' + encodeURIComponent(upstreamId);
  const data = await api(url);
  const tbody = document.querySelector('#drawer-' + CSS.escape(upstreamId) + ' .drawer-model-tbody');
  if (!tbody) return;
  // 格式化数字：null→'-'，大数以 K/M 显示
  function fmtNum(v) {
    if (v === null || v === undefined || v === '') return '-';
    const n = Number(v);
    if (isNaN(n)) return '-';
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
    if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
    return String(n);
  }
  tbody.innerHTML = data.models.map(m =>
    `<tr>
      <td><span class="badge badge-green">${escHtml(m.name)}</span></td>
      <td><span class="badge" style="background:hsl(var(--muted));color:hsl(var(--muted-foreground))">${escHtml(m.upstream_name)}</span></td>
      <td>${m.multimodal ? '✅' : '❌'}</td>
      <td style="font-family:monospace;font-size:12px">${fmtNum(m.max_context)}</td>
      <td style="font-family:monospace;font-size:12px">${fmtNum(m.max_input)}</td>
      <td style="font-family:monospace;font-size:12px">${fmtNum(m.max_output)}</td>
      <td style="font-family:monospace;font-size:12px">${fmtNum(m.rpm)}</td>
      <td>
        <button class="btn btn-secondary btn-sm" data-action="showModelModal" data-id="${m.id}">编辑</button>
        <button class="btn btn-danger btn-sm" data-action="confirmDeleteModel" data-id="${m.id}" data-name="${escHtml(m.name)}">删除</button>
      </td>
    </tr>`
  ).join('') || '<tr><td colspan="8" class="empty-state">暂无模型</td></tr>';
}

function loadAllModelConfigTables() {
  const wasOpen = openDrawerUpstreamId;
  closeDrawerRow();
  loadUpstreamTable();
  if (wasOpen) {
    requestAnimationFrame(() => {
      const row = document.querySelector(`#upstream-table tbody tr[data-id="${CSS.escape(wasOpen)}"]`);
      if (row) {
        toggleModelDrawer({ closest: () => row }, wasOpen);
      }
    });
  }
}

// ─── 上游模态框 ───
async function showUpstreamModal(editId) {
  let data = { name: '', base_url: '', api_key: '', timeout: 600, connect_timeout: 30, ssl_verify: 1, retry: 1, format: 'chat_completions' };
  let title = '新增上游';
  if (editId) {
    const upstreams = await api('/api/upstreams');
    const found = upstreams.upstreams.find(u => String(u.id) === editId);
    if (found) data = found;
    title = '编辑上游: ' + escHtml(data.name || editId);
  }
  showModal(title,
    `<div class="form-group"><label class="form-label">名称</label><input type="text" class="form-input" id="up-name" value="${escHtml(data.name || data.id)}"></div>
     <div class="form-group"><label class="form-label">Base URL</label><input type="text" class="form-input" id="up-url" value="${escHtml(data.base_url)}"></div>
     <div class="form-group"><label class="form-label">API Key</label><input type="text" class="form-input" id="up-key" value="${escHtml(data.api_key)}"></div>
     <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
       <div class="form-group"><label class="form-label">响应超时 (s)</label><input type="number" class="form-input" id="up-timeout" value="${data.timeout}" min="1"></div>
       <div class="form-group"><label class="form-label">连接超时 (s)</label><input type="number" class="form-input" id="up-conn-timeout" value="${data.connect_timeout}" min="1"></div>
     </div>
     <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
       <div class="form-group"><label class="form-label">SSL</label>${customSelectHtml('up-ssl', [{ value: '1', label: '开启', selected: data.ssl_verify }, { value: '0', label: '关闭', selected: !data.ssl_verify }], '选择')}</div>
       <div class="form-group"><label class="form-label">重试</label><input type="number" class="form-input" id="up-retry" value="${data.retry}" min="0"></div>
     </div>
     <div class="form-group"><label class="form-label">请求格式</label>${customSelectHtml('up-format', [{ value: 'chat_completions', label: 'Chat', selected: data.format === 'chat_completions' }, { value: 'responses', label: 'Responses', selected: data.format === 'responses' }, { value: 'messages', label: 'Messages', selected: data.format === 'messages' }], '选择格式')}</div>`,
    `<button class="btn btn-secondary" data-action="closeModal">取消</button><button class="btn btn-primary" data-action="saveUpstream" data-edit-id="${editId || ''}">保存</button>`);
  setTimeout(() => { wireCustomSelect('up-ssl'); wireCustomSelect('up-format'); }, 0);
}

async function saveUpstream(editId) {
  const data = {
    base_url: document.getElementById('up-url').value,
    api_key: document.getElementById('up-key').value,
    timeout: parseInt(document.getElementById('up-timeout').value) || 600,
    connect_timeout: parseInt(document.getElementById('up-conn-timeout').value) || 30,
    ssl_verify: parseInt(document.getElementById('up-ssl').value),
    retry: parseInt(document.getElementById('up-retry').value) || 1,
    format: document.getElementById('up-format').value,
  };
  data.name = document.getElementById('up-name').value.trim();
  if (data.name.includes('/')) { alert('上游名不能包含 /'); return; }
  if (!data.base_url) { alert('Base URL 不能为空'); return; }
  if (editId) {
    await api('/api/upstreams/' + editId, { method: 'PUT', body: JSON.stringify(data) });
  } else {
    await api('/api/upstreams', { method: 'POST', body: JSON.stringify(data) });
  }
  closeModal();
  bus.emit('config:upstream-changed', {});
  loadAllModelConfigTables();
}

async function testUpstream(id) {
  const result = await api('/api/upstreams/' + id + '/test', { method: 'POST' });
  if (result.reachable) {
    alert('✅ 连通正常 (' + result.latency_ms + 'ms)' + (result.warning ? '\n⚠️ ' + result.warning : ''));
  } else {
    alert('❌ 不可达: ' + (result.error || '未知错误'));
  }
}

function _deleteUpstreamBody(uName, uId, modelNames, hasRoutes, routeList) {
  const body = '<div style="font-size:14px;line-height:1.8">' +
    '<div>上游: ' + escHtml(uName) + ' (ID: ' + escHtml(uId) + ')</div>' +
    '<div>关联模型: ' + escHtml(modelNames) + '</div>';
  if (hasRoutes) {
    return body +
      '<div style="color:hsl(var(--red));margin-top:12px">❌ 该上游被以下路由引用：</div>' +
      '<pre style="background:hsl(var(--muted));padding:8px;border-radius:4px;font-size:12px;margin:8px 0">' + escHtml(routeList) + '</pre>' +
      '<div style="color:hsl(var(--muted-foreground));font-size:13px">请先在路由管理中解绑这些路由。</div>' +
      '</div>';
  }
  return body +
    '<div style="color:hsl(var(--green));margin-top:12px">✅ 无路由引用，可以安全删除</div>' +
    '</div>';
}

function _deleteUpstreamFooter(hasRoutes, id) {
  if (hasRoutes) {
    return '<button class="btn btn-secondary" data-action="closeModal">关闭</button>' +
      '<button class="btn btn-danger" disabled>确认删除</button>';
  }
  return '<button class="btn btn-secondary" data-action="closeModal">取消</button>' +
    '<button class="btn btn-danger" data-action="confirmDeleteUpstream" data-id="' + escHtml(id) + '">确认删除</button>';
}

async function showDeleteUpstreamModal(id) {
  const [upData, models, routes] = await Promise.all([
    api('/api/upstreams'),
    api('/api/models?upstream_id=' + encodeURIComponent(id)),
    api('/api/routes')
  ]);
  const u = upData.upstreams.find(x => String(x.id) === id);
  const affected = routes.routes.filter(r => String(r.upstream_id) === id);
  const uName = u ? (u.name || id) : id;
  const modelNames = models.models.map(m => m.name).join(', ') || '无';
  const hasRoutes = affected.length > 0;
  const routeList = hasRoutes ? affected.map(r => '  • ' + escHtml(r.source)).join('\n') : '';

  showModal('🗑️  删除上游',
    _deleteUpstreamBody(uName, id, modelNames, hasRoutes, routeList),
    _deleteUpstreamFooter(hasRoutes, id));
}

async function confirmDeleteUpstream(id) {
  const result = await api('/api/upstreams/' + id, { method: 'DELETE' });
  if (result.error) {
    alert('❌ 无法删除: ' + result.error + '\n\n被引用的路由: ' + (result.referenced_routes || []).join(', '));
  } else {
    closeModal();
    bus.emit('config:upstream-changed', {});
    loadAllModelConfigTables();
  }
}

// ─── 模型模态框 ───
async function showModelModal(editId, defaultUpstreamId) {
  let data = { name: '', upstream_id: defaultUpstreamId || '', multimodal: 1 };
  let title = '新增模型';
  if (editId) {
    title = '编辑模型 #' + editId;
    const models = await api('/api/models');
    const found = models.models.find(m => m.id === editId);
    if (found) data = found;
  }
  const upstreams = await api('/api/upstreams');
  const activeUpstreams = upstreams.upstreams.filter(u => u.is_active);
  const upstreamOpts = activeUpstreams.map(u => ({ value: String(u.id), label: u.name, selected: String(data.upstream_id) === String(u.id) }));

  const upstreamField = defaultUpstreamId
    ? `<input type="hidden" id="m-upstream" value="${escHtml(defaultUpstreamId)}"><div class="form-group"><label class="form-label">所属上游</label><input type="text" class="form-input" value="${escHtml(upstreamDataMap[defaultUpstreamId]?.name || defaultUpstreamId)}" readonly style="background:hsl(var(--muted));color:hsl(var(--muted-foreground));cursor:not-allowed"></div>`
    : `<div class="form-group"><label class="form-label">所属上游</label>${customSelectHtml('m-upstream', upstreamOpts, '选择上游')}</div>`;

  const newFieldsHtml = `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
      <div class="form-group"><label class="form-label">最大上下文</label><input type="number" class="form-input" id="m-max-context" value="${data.max_context !== null && data.max_context !== undefined ? data.max_context : ''}" min="0" placeholder="如 128000"></div>
      <div class="form-group"><label class="form-label">最大输入 (tokens)</label><input type="number" class="form-input" id="m-max-input" value="${data.max_input !== null && data.max_input !== undefined ? data.max_input : ''}" min="0" placeholder="如 128000"></div>
      <div class="form-group"><label class="form-label">最大输出 (tokens)</label><input type="number" class="form-input" id="m-max-output" value="${data.max_output !== null && data.max_output !== undefined ? data.max_output : ''}" min="0" placeholder="如 16384"></div>
      <div class="form-group"><label class="form-label">RPM</label><input type="number" class="form-input" id="m-rpm" value="${data.rpm !== null && data.rpm !== undefined ? data.rpm : ''}" min="0" placeholder="如 10000"></div>
    </div>`;
  showModal(title,
    `<div class="form-group"><label class="form-label">模型名</label><input type="text" class="form-input" id="m-name" value="${escHtml(data.name)}"></div>
     ${upstreamField}
     <div class="form-group"><label class="form-label">Multimodal</label>${customSelectHtml('m-multimodal', [{ value: '1', label: '✅ 支持', selected: data.multimodal }, { value: '0', label: '❌ 不支持', selected: !data.multimodal }], '选择')}</div>
     ${newFieldsHtml}`,
    `<button class="btn btn-secondary" data-action="closeModal">取消</button><button class="btn btn-primary" data-action="saveModel" data-edit-id="${editId || 0}">保存</button>`);
  setTimeout(() => { if (!defaultUpstreamId) wireCustomSelect('m-upstream'); wireCustomSelect('m-multimodal'); }, 0);
}

function showModelModalForUpstream(upstreamId) {
  showModelModal(0, upstreamId);
}

async function saveModel(editId) {
  function _intVal(id) {
    const v = document.getElementById(id).value.trim();
    return v === '' ? null : parseInt(v);
  }
  const data = {
    name: document.getElementById('m-name').value.trim(),
    upstream_id: document.getElementById('m-upstream').value,
    multimodal: parseInt(document.getElementById('m-multimodal').value),
    max_context: _intVal('m-max-context'),
    max_input: _intVal('m-max-input'),
    max_output: _intVal('m-max-output'),
    rpm: _intVal('m-rpm'),
  };
  if (!data.name) { alert('模型名不能为空'); return; }
  if (data.name.includes('/')) { alert('模型名不能包含 /'); return; }
  if (editId) {
    await api('/api/models/' + editId, { method: 'PUT', body: JSON.stringify(data) });
  } else {
    await api('/api/models', { method: 'POST', body: JSON.stringify(data) });
  }
  closeModal();
  bus.emit('config:model-changed', {});
  if (openDrawerUpstreamId) {
    loadModelTable(openDrawerUpstreamId);
  } else {
    loadAllModelConfigTables();
  }
}

async function confirmDeleteModel(id, name) {
  if (!confirm('确认删除模型 "' + name + '"？')) return;
  const result = await api('/api/models/' + id, { method: 'DELETE' });
  if (result.error) {
    alert('❌ 无法删除: ' + result.error + '\n\n被引用的路由: ' + (result.referenced_routes || []).join(', '));
  } else {
    bus.emit('config:model-changed', {});
    if (openDrawerUpstreamId) {
      loadModelTable(openDrawerUpstreamId);
    } else {
      loadAllModelConfigTables();
    }
  }
}

// ─── 自动检测模型 ───

async function detectUpstreamModels(upstreamId) {
  const btn = document.getElementById('detect-btn-' + upstreamId);
  if (!btn) return;

  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>检测中...';

  try {
    const result = await api('/api/upstreams/' + encodeURIComponent(upstreamId) + '/detect-models', { method: 'POST' });
    if (!result.reachable) {
      alert('⚠️ 上游不可达: ' + (result.error || '未知错误'));
      return;
    }
    showDetectModal(upstreamId, result);
  } catch (e) {
    alert('❌ 检测失败: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = '🔍 检测模型';
  }
}

function showDetectModal(upstreamId, result) {
  const discovered = result.discovered || [];
  const existingCount = (result.existing || []).length;

  if (discovered.length === 0) {
    showModal('检测模型 — ' + escHtml(upstreamId),
      '<div class="detect-all-existing">' +
        '<div class="detect-all-existing-icon">✅</div>' +
        '<div>所有模型已存在' + (existingCount > 0 ? '（' + existingCount + ' 个）' : '') + '</div>' +
      '</div>',
      '<button class="btn btn-secondary" data-action="closeModal">关闭</button>');
    return;
  }

  const summaryHtml =
    '<div class="detect-modal-summary">' +
      '<div class="detect-summary-badge detect-badge-new">' +
        '<span class="detect-badge-num">' + discovered.length + '</span>' +
        '<span class="detect-badge-label">新发现</span>' +
      '</div>' +
      '<div class="detect-summary-badge detect-badge-existing">' +
        '<span class="detect-badge-num">' + existingCount + '</span>' +
        '<span class="detect-badge-label">已存在</span>' +
      '</div>' +
    '</div>';

  const selectBarHtml =
    '<div class="detect-select-bar">' +
      '<span class="detect-select-count">已选 <strong id="detect-selected-count">' + discovered.length + '</strong> / ' + discovered.length + '</span>' +
      '<div class="detect-select-actions">' +
        '<a class="detect-select-link" data-action="toggleSelectAllModels" data-checked="true">全选</a>' +
        '<a class="detect-select-link" data-action="toggleSelectAllModels" data-checked="false">取消全选</a>' +
      '</div>' +
    '</div>';

  const tableRows = discovered.map((name, i) =>
    '<tr class="detect-row" style="animation-delay:' + (i * 30) + 'ms">' +
      '<td class="detect-cell-check">' +
        '<input type="checkbox" class="detect-model-cb" value="' + escHtml(name) + '" checked id="dm-cb-' + i + '" data-action="updateDetectSelectedCount">' +
      '</td>' +
      '<td class="detect-cell-name">' +
        '<span class="detect-model-name" title="' + escHtml(name) + '">' + escHtml(name) + '</span>' +
      '</td>' +
      '<td class="detect-cell-multi">' +
        '<label class="detect-toggle-wrap">' +
          '<input type="checkbox" class="dm-multimodal" checked>' +
          '<span class="detect-toggle-track"><span class="detect-toggle-thumb"></span></span>' +
        '</label>' +
      '</td>' +
    '</tr>'
  ).join('');

  const tableHtml =
    '<div class="detect-table-wrap">' +
      '<table class="detect-table">' +
        '<thead><tr>' +
          '<th class="detect-col-check"></th>' +
          '<th class="detect-col-name">模型名</th>' +
          '<th class="detect-col-multi">多模态</th>' +
        '</tr></thead>' +
        '<tbody>' + tableRows + '</tbody>' +
      '</table>' +
    '</div>';

  showModal('检测模型 — ' + escHtml(upstreamId),
    summaryHtml + selectBarHtml + tableHtml,
    '<button class="btn btn-secondary" data-action="closeModal">取消</button>' +
    '<button class="btn btn-primary" data-action="bulkAddDetectedModels" data-id="' + escHtml(upstreamId) + '">批量添加选中模型</button>');
}

function updateDetectSelectedCount() {
  const checked = document.querySelectorAll('.detect-model-cb:checked').length;
  const el = document.getElementById('detect-selected-count');
  if (el) el.textContent = checked;
}

function toggleSelectAllModels(checked) {
  document.querySelectorAll('.detect-model-cb').forEach(cb => { cb.checked = checked; });
  updateDetectSelectedCount();
}

async function bulkAddDetectedModels(upstreamId) {
  const checkboxes = document.querySelectorAll('.detect-model-cb:checked');
  if (checkboxes.length === 0) {
    alert('请至少选择一个模型');
    return;
  }

  const models = Array.from(checkboxes).map(cb => {
    const item = cb.closest('.detect-row');
    const multimodalCb = item ? item.querySelector('.dm-multimodal') : null;
    return { name: cb.value, multimodal: multimodalCb ? (multimodalCb.checked ? 1 : 0) : 1 };
  });

  try {
    const result = await api('/api/upstreams/' + encodeURIComponent(upstreamId) + '/models/bulk', {
      method: 'POST',
      body: JSON.stringify({ models })
    });
    closeModal();
    bus.emit('config:model-changed', {});
    if (openDrawerUpstreamId) loadModelTable(openDrawerUpstreamId);
    alert('✅ 添加完成: 新增 ' + result.added + ' 个，跳过 ' + result.skipped + ' 个');
  } catch (e) {
    alert('❌ 添加失败: ' + e.message);
  }
}


// ─── 页面加载 ───

async function loadUpstreamPage() {
  await refreshConfigStatus();
  loadUpstreamTable();
}

function initUpstreamPage() {
  on('toggleModelDrawer', (e, el) => toggleModelDrawer(el, el.dataset.id));
  on('showUpstreamModal', (e, el) => showUpstreamModal(el.dataset.id));
  on('testUpstream', (e, el) => testUpstream(el.dataset.id));
  on('showDeleteUpstreamModal', (e, el) => showDeleteUpstreamModal(el.dataset.id));
  on('saveUpstream', (e, el) => saveUpstream(el.dataset.editId));
  on('confirmDeleteUpstream', (e, el) => confirmDeleteUpstream(el.dataset.id));
  on('showModelModal', (e, el) => showModelModal(parseInt(el.dataset.id)));
  on('showModelModalForUpstream', (e, el) => showModelModalForUpstream(el.dataset.id));
  on('saveModel', (e, el) => saveModel(parseInt(el.dataset.editId)));
  on('confirmDeleteModel', (e, el) => confirmDeleteModel(parseInt(el.dataset.id), el.dataset.name));
  on('detectUpstreamModels', (e, el) => detectUpstreamModels(el.dataset.id));
  on('bulkAddDetectedModels', (e, el) => bulkAddDetectedModels(el.dataset.id));
  on('toggleSelectAllModels', (e, el) => toggleSelectAllModels(el.dataset.checked === 'true'));
  on('updateDetectSelectedCount', updateDetectSelectedCount);
}

export { loadUpstreamPage, initUpstreamPage, refreshConfigStatus };
