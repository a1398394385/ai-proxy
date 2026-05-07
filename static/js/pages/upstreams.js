import { api, escHtml, showModal, closeModal, bus } from '../core.js';

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
  document.getElementById('upstream-count').textContent = data.upstreams.length + ' 个上游';
  const tbody = document.querySelector('#upstream-table tbody');
  tbody.innerHTML = data.upstreams.map(u =>
    `<tr style="${u.is_active ? '' : 'opacity:0.5'};cursor:pointer" onclick="toggleModelDrawer(event, '${escHtml(u.id)}')">
      <td><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${u.is_active ? 'hsl(var(--green))' : 'hsl(var(--red))'};"></span> ${u.is_active ? '活跃' : '已禁用'}</td>
      <td><span class="badge badge-blue">${escHtml(u.id)}</span></td>
      <td style="font-family:monospace;font-size:12px">${escHtml(u.base_url)}</td>
      <td>${u.timeout}s</td>
      <td>${u.is_default ? '✅' : ''}</td>
      <td>
        <button class="btn btn-secondary btn-sm" onclick="event.stopPropagation(); showUpstreamModal('${escHtml(u.id)}')">编辑</button>
        <button class="btn btn-secondary btn-sm" onclick="event.stopPropagation(); testUpstream('${escHtml(u.id)}')">测试</button>
        ${u.is_active ? '<button class="btn btn-danger btn-sm" onclick="event.stopPropagation(); confirmDisableUpstream(\'' + escHtml(u.id) + '\')">禁用</button>' : ''}
      </td>
    </tr>`
  ).join('');
}

// ===== Drawer (inline accordion) =====

let openDrawerUpstreamId = null;

function toggleModelDrawer(event, upstreamId) {
  event.stopPropagation();

  // If same upstream clicked again → close
  if (openDrawerUpstreamId === upstreamId) {
    closeDrawerRow();
    return;
  }

  // Close existing drawer if open
  closeDrawerRow();

  // Find the clicked row and insert drawer row after it
  const clickedRow = event.currentTarget.closest('tr');
  if (!clickedRow) return;

  const tbody = clickedRow.parentElement;
  const drawerRow = document.createElement('tr');
  drawerRow.className = 'drawer-row';
  drawerRow.id = 'drawer-' + upstreamId;
  drawerRow.innerHTML =
    '<td colspan="6">' +
      '<div class="drawer-content">' +
        '<div class="drawer-header">🤖 模型列表 — 上游: ' + escHtml(upstreamId) +
          '<button class="btn btn-primary btn-sm" onclick="event.stopPropagation(); showModelModalForUpstream(\'' + escHtml(upstreamId) + '\')" style="margin-left:auto;">＋ 新增模型</button></div>' +
     '<table class="drawer-model-table">' +
           '<thead><tr><th>模型名</th><th>所属上游</th><th>Multimodal</th><th>操作</th></tr></thead>' +
           '<tbody class="drawer-model-tbody"></tbody>' +
         '</table>' +
      '</div>' +
    '</td>';

  // Insert after the clicked row
  const nextRow = clickedRow.nextElementSibling;
  if (nextRow) {
    tbody.insertBefore(drawerRow, nextRow);
  } else {
    tbody.appendChild(drawerRow);
  }

  openDrawerUpstreamId = upstreamId;

  // Trigger slide-in animation
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
  tbody.innerHTML = data.models.map(m =>
    `<tr>
      <td><span class="badge badge-green">${escHtml(m.name)}</span></td>
      <td><span class="badge" style="background:hsl(var(--muted));color:hsl(var(--muted-foreground))">${escHtml(m.upstream_name)}</span></td>
      <td>${m.multimodal ? '✅' : '❌'}</td>
      <td>
        <button class="btn btn-secondary btn-sm" onclick="showModelModal(${m.id})">编辑</button>
        <button class="btn btn-danger btn-sm" onclick="confirmDeleteModel(${m.id}, '${escHtml(m.name)}')">删除</button>
      </td>
    </tr>`
  ).join('') || '<tr><td colspan="4" class="empty-state">暂无模型</td></tr>';
}

function loadAllModelConfigTables() {
  const wasOpen = openDrawerUpstreamId;
  closeDrawerRow();
  loadUpstreamTable();
  if (wasOpen) {
    // Re-open the drawer by simulating a click on the matching row
    requestAnimationFrame(() => {
      const allRows = document.querySelectorAll('#upstream-table tbody tr');
      for (const r of allRows) {
        const badge = r.querySelector('.badge-blue');
        if (badge && badge.textContent === wasOpen) {
          const fakeEvent = { stopPropagation: () => {}, currentTarget: r };
          toggleModelDrawer(fakeEvent, wasOpen);
          break;
        }
      }
    });
  }
}

// ─── 上游模态框 ───
async function showUpstreamModal(editId) {
  let data = { id: '', base_url: '', api_key: '', timeout: 120, connect_timeout: 10, ssl_verify: 1, retry: 1, is_default: 0 };
  let title = '新增上游';
  if (editId) {
    title = '编辑上游: ' + editId;
    const upstreams = await api('/api/upstreams');
    const found = upstreams.upstreams.find(u => u.id === editId);
    if (found) data = found;
  }
  showModal(title,
    `<div class="form-group"><label class="form-label">名称 (ID)</label><input type="text" class="form-input" id="up-id" value="${escHtml(data.id)}" ${editId ? 'readonly' : ''}></div>
     <div class="form-group"><label class="form-label">Base URL</label><input type="text" class="form-input" id="up-url" value="${escHtml(data.base_url)}"></div>
     <div class="form-group"><label class="form-label">API Key</label><input type="text" class="form-input" id="up-key" value="${escHtml(data.api_key)}"></div>
     <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
       <div class="form-group"><label class="form-label">超时 (s)</label><input type="number" class="form-input" id="up-timeout" value="${data.timeout}" min="1"></div>
       <div class="form-group"><label class="form-label">连接超时 (s)</label><input type="number" class="form-input" id="up-conn-timeout" value="${data.connect_timeout}" min="1"></div>
     </div>
     <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;">
       <div class="form-group"><label class="form-label">SSL</label><select class="form-input" id="up-ssl"><option value="1" ${data.ssl_verify ? 'selected' : ''}>开启</option><option value="0" ${!data.ssl_verify ? 'selected' : ''}>关闭</option></select></div>
       <div class="form-group"><label class="form-label">重试</label><input type="number" class="form-input" id="up-retry" value="${data.retry}" min="0"></div>
       <div class="form-group"><label class="form-label">默认</label><select class="form-input" id="up-default"><option value="1" ${data.is_default ? 'selected' : ''}>是</option><option value="0" ${!data.is_default ? 'selected' : ''}>否</option></select></div>
     </div>
     <div class="form-group"><label class="form-label">Format</label><select class="form-input" id="up-format"><option value="openai_chat" ${data.format === 'openai_chat' ? 'selected' : ''}>openai_chat</option><option value="openai_responses" ${data.format === 'openai_responses' ? 'selected' : ''}>openai_responses</option><option value="anthropic" ${data.format === 'anthropic' ? 'selected' : ''}>anthropic</option></select></div>`,
    `<button class="btn btn-secondary" onclick="closeModal()">取消</button><button class="btn btn-primary" onclick="saveUpstream('${editId || ''}')">保存</button>`);
}

async function saveUpstream(editId) {
  const data = {
    base_url: document.getElementById('up-url').value,
    api_key: document.getElementById('up-key').value,
    timeout: parseInt(document.getElementById('up-timeout').value) || 120,
    connect_timeout: parseInt(document.getElementById('up-conn-timeout').value) || 10,
    ssl_verify: parseInt(document.getElementById('up-ssl').value),
    retry: parseInt(document.getElementById('up-retry').value) || 1,
    is_default: parseInt(document.getElementById('up-default').value),
    format: document.getElementById('up-format').value,
  };
  if (!editId) data.id = document.getElementById('up-id').value.trim();
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

async function confirmDisableUpstream(id) {
  const data = await api('/api/upstreams');
  const u = data.upstreams.find(x => x.id === id);
  if (!u) return;
  const models = await api('/api/models?upstream_id=' + encodeURIComponent(id));
  const routes = await api('/api/routes');
  const affected = routes.routes.filter(r => r.upstream_id === id);
  const msg = '确认禁用上游 "' + id + '"？\n\n关联模型: ' + models.models.length + ' 个\n活跃路由引用: ' + affected.length + ' 个\n\n禁用后相关路由将无法使用。';
  if (!confirm(msg)) return;
  const result = await api('/api/upstreams/' + id, { method: 'DELETE' });
  if (result.error) {
    alert('❌ 无法禁用: ' + result.error + '\n\n被引用的路由: ' + (result.referenced_routes || []).join(', '));
  } else {
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
  const upstreamOpts = activeUpstreams.map(u => '<option value="' + escHtml(u.id) + '" ' + (data.upstream_id === u.id ? 'selected' : '') + '>' + escHtml(u.id) + '</option>').join('');

  const upstreamField = defaultUpstreamId
    ? `<input type="hidden" id="m-upstream" value="${escHtml(defaultUpstreamId)}"><div class="form-group"><label class="form-label">所属上游</label><input type="text" class="form-input" value="${escHtml(defaultUpstreamId)}" readonly style="background:hsl(var(--muted));color:hsl(var(--muted-foreground));cursor:not-allowed"></div>`
    : `<div class="form-group"><label class="form-label">所属上游</label><select class="form-input" id="m-upstream">${upstreamOpts}</select></div>`;

  showModal(title,
    `<div class="form-group"><label class="form-label">模型名</label><input type="text" class="form-input" id="m-name" value="${escHtml(data.name)}"></div>
     ${upstreamField}
     <div class="form-group"><label class="form-label">Multimodal</label><select class="form-input" id="m-multimodal"><option value="1" ${data.multimodal ? 'selected' : ''}>✅ 支持</option><option value="0" ${!data.multimodal ? 'selected' : ''}>❌ 不支持</option></select></div>`,
    `<button class="btn btn-secondary" onclick="closeModal()">取消</button><button class="btn btn-primary" onclick="saveModel(${editId || 0})">保存</button>`);
}

function showModelModalForUpstream(upstreamId) {
  showModelModal(0, upstreamId);
}

async function saveModel(editId) {
  const data = {
    name: document.getElementById('m-name').value.trim(),
    upstream_id: document.getElementById('m-upstream').value,
    multimodal: parseInt(document.getElementById('m-multimodal').value),
  };
  if (!data.name) { alert('模型名不能为空'); return; }
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

// ─── 页面加载 ───

async function loadUpstreamPage() {
  await refreshConfigStatus();
  loadUpstreamTable();
}

function initUpstreamPage() {
  // No filter dropdown needed — removed
}

// ─── 全局绑定 ───

window.showUpstreamModal = showUpstreamModal;
window.saveUpstream = saveUpstream;
window.testUpstream = testUpstream;
window.confirmDisableUpstream = confirmDisableUpstream;
window.showModelModal = showModelModal;
window.showModelModalForUpstream = showModelModalForUpstream;
window.saveModel = saveModel;
window.confirmDeleteModel = confirmDeleteModel;
window.toggleModelDrawer = toggleModelDrawer;
window.loadUpstreamPage = loadUpstreamPage;
window.refreshConfigStatus = refreshConfigStatus;
export { loadUpstreamPage, initUpstreamPage, refreshConfigStatus };
