import { api, escHtml, showModal, closeModal, bus } from '../core.js';

// ===== 模型管理 =====

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

  const formatLabels = { responses: 'Responses', messages: 'Messages', chat_completions: 'Chat Comp.' };

  const formatColors = { responses: 'badge-blue', messages: 'badge-purple', chat_completions: 'badge-green' };

  tbody.innerHTML = data.upstreams.map(u =>

    `<tr style="${u.is_active ? '' : 'opacity:0.5'}">

      <td><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${u.is_active ? 'hsl(var(--green))' : 'hsl(var(--red))'}};"></span> ${u.is_active ? '活跃' : '已禁用'}</td>

      <td><span class="badge badge-blue">${escHtml(u.id)}</span></td>

      <td style="font-family:monospace;font-size:12px">${escHtml(u.base_url)}</td>

      <td><span class="badge ${formatColors[u.format] || ''}">${formatLabels[u.format] || u.format || '-'}</span></td>

      <td>${u.timeout}s</td>

      <td>

        <button class="btn btn-secondary btn-sm" onclick="showUpstreamModal('${escHtml(u.id)}')">编辑</button>

        <button class="btn btn-secondary btn-sm" onclick="testUpstream('${escHtml(u.id)}')">测试</button>

        ${u.is_active ? '<button class="btn btn-danger btn-sm" onclick="confirmDisableUpstream(\'' + escHtml(u.id) + '\')">禁用</button>' : ''}

      </td>

    </tr>`

  ).join('');

}


async function loadModelTable(upstreamId) {
  let url = '/api/models'; if (upstreamId) url += '?upstream_id=' + encodeURIComponent(upstreamId);
  const data = await api(url);
  document.querySelector('#model-table tbody').innerHTML = data.models.map(m =>
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

async function loadRouteTable() {
  const data = await api('/api/routes');
  document.querySelector('#route-table tbody').innerHTML = data.routes.map(r =>
    `<tr style="${r.source === '*' ? 'background:hsl(var(--primary) / 0.05);' : ''} ${r.upstream_active ? '' : 'opacity:0.5'}">
      <td><span class="badge badge-purple">${escHtml(r.source)}${r.source === '*' ? ' (★ fallback)' : ''}</span></td>
      <td>→ <span class="badge badge-green">${escHtml(r.target_name)}</span></td>
      <td><span class="badge" style="background:hsl(var(--muted));color:hsl(var(--muted-foreground))">${escHtml(r.upstream_id)}</span></td>
      <td>${r.upstream_active ? '<span style="color:hsl(var(--green))">活跃</span>' : '<span style="color:hsl(var(--red))">上游已禁用</span>'}</td>
      <td>
        <button class="btn btn-secondary btn-sm" onclick="showRouteModal(${r.id})">编辑</button>
        <button class="btn btn-danger btn-sm" onclick="confirmDeleteRoute(${r.id}, '${escHtml(r.source)}')">删除</button>
      </td>
    </tr>`
  ).join('') || '<tr><td colspan="5" class="empty-state">暂无路由</td></tr>';
}

async function refreshUpstreamDropdown() {
  const data = await api('/api/upstreams');
  const active = data.upstreams.filter(u => u.is_active);
  document.getElementById('model-filter-upstream').innerHTML = '<option value="">全部上游</option>' +
    active.map(u => '<option value="' + escHtml(u.id) + '">' + escHtml(u.id) + '</option>').join('');
}

function loadAllModelConfigTables() {
  loadUpstreamTable();
  loadModelTable(document.getElementById('model-filter-upstream').value);
  loadRouteTable();
}

async function loadModelConfig() {
  await refreshConfigStatus();
  await refreshUpstreamDropdown();
  loadAllModelConfigTables();
}

// ─── 上游模态框 ───
async function showUpstreamModal(editId) {
  let data = { id: '', base_url: '', api_key: '', timeout: 600, connect_timeout: 30, ssl_verify: 1, retry: 1, format: 'chat_completions' };
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
     <div class="form-group"><label class="form-label">请求格式</label><select class="form-input" id="up-format"><option value="chat_completions" ${data.format === 'chat_completions' ? 'selected' : ''}>Chat Completions</option><option value="responses" ${data.format === 'responses' ? 'selected' : ''}>Responses</option><option value="messages" ${data.format === 'messages' ? 'selected' : ''}>Messages</option></select></div>
     <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
       <div class="form-group"><label class="form-label">响应超时 (s)</label><input type="number" class="form-input" id="up-timeout" value="${data.timeout}" min="1"></div>
       <div class="form-group"><label class="form-label">连接超时 (s)</label><input type="number" class="form-input" id="up-conn-timeout" value="${data.connect_timeout}" min="1"></div>
     </div>
     <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
       <div class="form-group"><label class="form-label">SSL</label><select class="form-input" id="up-ssl"><option value="1" ${data.ssl_verify ? 'selected' : ''}>开启</option><option value="0" ${!data.ssl_verify ? 'selected' : ''}>关闭</option></select></div>
       <div class="form-group"><label class="form-label">重试</label><input type="number" class="form-input" id="up-retry" value="${data.retry}" min="0"></div>
     </div>`,
    `<button class="btn btn-secondary" onclick="closeModal()">取消</button><button class="btn btn-primary" onclick="saveUpstream('${editId || ''}')">保存</button>`);
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
  ];
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
  bus.emit('config:dirty', { source: 'upstream' });
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
    bus.emit('config:dirty', { source: 'upstream' });
    loadAllModelConfigTables();
  }
}

// ─── 模型模态框 ───
async function showModelModal(editId) {
  let data = { name: '', upstream_id: '', multimodal: 1 };
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
  showModal(title,
    `<div class="form-group"><label class="form-label">模型名</label><input type="text" class="form-input" id="m-name" value="${escHtml(data.name)}"></div>
     <div class="form-group"><label class="form-label">所属上游</label><select class="form-input" id="m-upstream">${upstreamOpts}</select></div>
     <div class="form-group"><label class="form-label">Multimodal</label><select class="form-input" id="m-multimodal"><option value="1" ${data.multimodal ? 'selected' : ''}>✅ 支持</option><option value="0" ${!data.multimodal ? 'selected' : ''}>❌ 不支持</option></select></div>`,
    `<button class="btn btn-secondary" onclick="closeModal()">取消</button><button class="btn btn-primary" onclick="saveModel(${editId || 0})">保存</button>`);
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
  bus.emit('config:dirty', { source: 'model' });
  loadAllModelConfigTables();
}

async function confirmDeleteModel(id, name) {
  if (!confirm('确认删除模型 "' + name + '"？')) return;
  const result = await api('/api/models/' + id, { method: 'DELETE' });
  if (result.error) {
    alert('❌ 无法删除: ' + result.error + '\n\n被引用的路由: ' + (result.referenced_routes || []).join(', '));
  } else {
    bus.emit('config:model-changed', {});
    bus.emit('config:dirty', { source: 'model' });
    loadAllModelConfigTables();
  }
}

// ─── 路由模态框 ───
async function showRouteModal(editId) {
  let data = { source: '', target_model_id: '' };
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
  showModal(title,
    `<div class="form-group"><label class="form-label">源模型名</label><input type="text" class="form-input" id="r-source" value="${escHtml(data.source)}" placeholder="如 gpt-4o 或 * (fallback)"></div>
     <div class="form-group"><label class="form-label">目标模型</label><select class="form-input" id="r-target">${modelOpts}</select></div>`,
    `<button class="btn btn-secondary" onclick="closeModal()">取消</button><button class="btn btn-primary" onclick="saveRoute(${editId || 0})">保存</button>`);
}

async function saveRoute(editId) {
  const data = {
    source: document.getElementById('r-source').value.trim(),
    target_model_id: parseInt(document.getElementById('r-target').value),
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
  loadAllModelConfigTables();
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
  else { bus.emit('config:route-changed', {}); bus.emit('config:dirty', { source: 'route' }); loadAllModelConfigTables(); }
}

async function applyConfig() {
  const btn = document.getElementById('apply-config-btn');
  btn.textContent = '⏳ 应用中...'; btn.disabled = true;
  const result = await api('/api/config/reload', { method: 'POST' });
  if (result.status === 'ok') {
    bus.emit('config:applied', { reloaded_at: result.reloaded_at });
    btn.classList.remove('pulse-orange'); btn.textContent = '✅ 应用配置';
    refreshConfigStatus();
    alert('配置已生效 (' + result.reloaded_at + ')');
  } else { alert('⚠️ ' + (result.message || '重载失败')); btn.textContent = '🔄 重试'; }
  btn.disabled = false;
}

// ===== Init Model Page Events =====
export function initModelPage() {
  // Upstream filter dropdown
  const filterEl = document.getElementById('model-filter-upstream');
  if (filterEl) {
    filterEl.addEventListener('change', (e) => { loadModelTable(e.target.value); });
  }
}

// ===== Exports =====
export { loadModelConfig, refreshConfigStatus, refreshUpstreamDropdown, loadAllModelConfigTables, loadUpstreamTable, loadModelTable, loadRouteTable, applyConfig, showUpstreamModal, saveUpstream, testUpstream, confirmDisableUpstream, showModelModal, saveModel, confirmDeleteModel, showRouteModal, saveRoute, confirmDeleteRoute };

// ===== Global Scope Mounting =====
window.showUpstreamModal = showUpstreamModal;
window.saveUpstream = saveUpstream;
window.testUpstream = testUpstream;
window.showModelModal = showModelModal;
window.saveModel = saveModel;
window.showRouteModal = showRouteModal;
window.saveRoute = saveRoute;
window.applyConfig = applyConfig;
window.loadModelConfig = loadModelConfig;
window.refreshConfigStatus = refreshConfigStatus;
window.refreshUpstreamDropdown = refreshUpstreamDropdown;
window.confirmDisableUpstream = confirmDisableUpstream;
window.confirmDeleteModel = confirmDeleteModel;
window.confirmDeleteRoute = confirmDeleteRoute;
