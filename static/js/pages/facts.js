import { api, escHtml, showModal, closeModal, catLabels, catIcons } from '../core.js';

// ===== Mutable State =====
let allFacts = [];
let activeCategory = null;
let editingId = null;

// ===== Fact Store =====
async function loadFacts(q) {
  let url = q ? `/api/facts?q=${encodeURIComponent(q)}` : '/api/facts';
  if (activeCategory && !q) url += `?category=${activeCategory}`;
  const data = await api(url);
  allFacts = data.facts || [];
  renderFacts(allFacts);
  if (!q && !activeCategory) loadCategories();
}

async function loadCategories() {
  const data = await api('/api/categories');
  const wrap = document.getElementById('cat-filters');
  wrap.innerHTML = data.categories.map(c => {
    const label = catLabels[c.category] || c.category;
    const icon = catIcons[c.category] || '📌';
    return `<button class="filter-pill ${activeCategory === c.category ? 'active' : ''}" data-cat="${c.category}">${icon} ${label} (${c.count})</button>`;
  }).join('');
  wrap.querySelectorAll('.filter-pill').forEach(btn => {
    btn.addEventListener('click', () => {
      const cat = btn.dataset.cat;
      if (activeCategory === cat) { activeCategory = null; }
      else { activeCategory = cat; }
      loadCategories();
      loadFacts();
    });
  });
}

function renderFacts(facts) {
  const c = document.getElementById('facts-container');
  if (!facts.length) { 
    c.innerHTML = '<div class="empty-state"><div class="empty-state-icon">📭</div><p>没有找到事实</p></div>'; 
    return; 
  }
  c.innerHTML = facts.map(f => {
    const catLabel = catLabels[f.category] || f.category;
    const catIcon = catIcons[f.category] || '📌';
    const trustPct = Math.round(f.trust_score * 100);
    const trustColor = trustPct >= 70 ? 'var(--green)' : trustPct >= 40 ? 'var(--orange)' : 'var(--red)';
    const entitiesHtml = (f.entities || []).map(e => `<span class="badge badge-blue">${e}</span>`).join(' ');
    const tagsHtml = f.tags ? f.tags.split(',').filter(Boolean).map(t => `<span class="badge" style="background:hsl(var(--muted));color:hsl(var(--muted-foreground))">${t.trim()}</span>`).join(' ') : '';
    
    // 检测内容是否需要折叠（超过 5 行或 300 字符，与 CSS max-height: 120px 匹配）
    const content = f.content || '';
    const lineCount = content.split('\n').length;
    const charCount = content.length;
    // 按 1.6 行高、13px 字体计算，120px 约 5-6 行
    const needsExpand = lineCount > 5 || charCount > 300;
    const contentClass = needsExpand ? 'fact-content collapsed' : 'fact-content';
    const expandBtn = needsExpand ? `<button class="fact-expand-btn" onclick="toggleFactExpand(this)">展开 ▼</button>` : '';
    
    return `<div class="fact-card">
      <div class="fact-header">
        <span class="fact-id">#${f.fact_id}</span>
        <div style="flex:1;display:flex;flex-direction:column;min-width:0">
          <div class="${contentClass}" data-expanded="false">${escHtml(content)}</div>
          ${expandBtn}
        </div>
        <div class="fact-actions">
          <button class="btn btn-secondary btn-sm" onclick="editFact(${f.fact_id})">编辑</button>
          <button class="btn btn-danger btn-sm" onclick="deleteFact(${f.fact_id})">删除</button>
        </div>
      </div>
      <div class="fact-meta">
        <span class="badge badge-purple">${catIcon} ${catLabel}</span>
        ${tagsHtml}
        ${entitiesHtml}
        <span style="margin-left:auto;font-size:12px;color:hsl(var(--muted-foreground));display:flex;align-items:center;gap:6px">
          信任度
          <span style="display:inline-block;width:60px;height:4px;background:hsl(var(--muted));border-radius:2px;overflow:hidden">
            <span style="display:block;width:${trustPct}%;height:100%;background:hsl(${trustColor})"></span>
          </span>
          ${trustPct}%
        </span>
      </div>
    </div>`;
  }).join('');
}

// 展开/折叠 Fact 内容
function toggleFactExpand(btn) {
  const content = btn.previousElementSibling;
  const isExpanded = content.getAttribute('data-expanded') === 'true';
  
  if (isExpanded) {
    content.classList.remove('expanded');
    content.classList.add('collapsed');
    content.setAttribute('data-expanded', 'false');
    btn.textContent = '展开 ▼';
  } else {
    content.classList.remove('collapsed');
    content.classList.add('expanded');
    content.setAttribute('data-expanded', 'true');
    btn.textContent = '收起 ▲';
  }
}

// ===== Fact CRUD =====
async function editFact(id) {
  const f = allFacts.find(x => x.fact_id === id) || await api(`/api/facts/${id}`);
  editingId = id;
  showModal(`编辑事实 #${id}`, `
    <div class="form-group">
      <label class="form-label">内容</label>
      <textarea class="form-textarea" id="m-content">${escHtml(f.content)}</textarea>
    </div>
    <div class="form-group">
      <label class="form-label">类别</label>
      <select class="form-select" id="m-category">
        <option value="general" ${f.category==='general'?'selected':''}>通用</option>
        <option value="project" ${f.category==='project'?'selected':''}>项目</option>
        <option value="tool" ${f.category==='tool'?'selected':''}>工具</option>
        <option value="user_pref" ${f.category==='user_pref'?'selected':''}>偏好</option>
      </select>
    </div>
    <div class="form-group">
      <label class="form-label">标签 (逗号分隔)</label>
      <input type="text" class="form-input" id="m-tags" value="${escHtml(f.tags||'')}">
    </div>
    <div class="form-group">
      <label class="form-label">信任度 (0-1)</label>
      <input type="number" class="form-input" id="m-trust" min="0" max="1" step="0.1" value="${f.trust_score}">
    </div>
  `, `
    <button class="btn btn-secondary" onclick="closeModal()">取消</button>
    <button class="btn btn-primary" onclick="saveFact()">保存</button>
  `);
}

async function saveFact() {
  const data = {
    content: document.getElementById('m-content').value,
    category: document.getElementById('m-category').value,
    tags: document.getElementById('m-tags').value,
    trust_score: parseFloat(document.getElementById('m-trust').value) || 0.5,
  };
  if (!data.content.trim()) { alert('内容不能为空'); return; }
  const entitiesInput = document.getElementById('m-entities');
  if (entitiesInput) data.entities = entitiesInput.value.split(',').map(s=>s.trim()).filter(Boolean);
  
  if (editingId) {
    await api(`/api/facts/${editingId}`, { method: 'PUT', body: JSON.stringify(data) });
  } else {
    await api('/api/facts', { method: 'POST', body: JSON.stringify(data) });
  }
  closeModal();
  loadFacts(); loadCategories();
}

async function deleteFact(id) {
  if (!confirm('确认删除事实 #' + id + '?')) return;
  await api(`/api/facts/${id}`, { method: 'DELETE' });
  loadFacts(); loadCategories();
}

// ===== Init Fact Page Events =====
function initFactPage() {
  // Search input
  const searchInput = document.getElementById('search');
  if (searchInput) {
    searchInput.addEventListener('input', (e) => {
      loadFacts(e.target.value.trim());
    });
  }

  // Add fact button
  const addBtn = document.getElementById('add-btn');
  if (addBtn) {
    addBtn.addEventListener('click', () => {
      editingId = null;
      showModal('新增事实', `
        <div class="form-group">
          <label class="form-label">内容</label>
          <textarea class="form-textarea" id="m-content" placeholder="输入事实内容..."></textarea>
        </div>
        <div class="form-group">
          <label class="form-label">类别</label>
          <select class="form-select" id="m-category">
            <option value="general">通用</option>
            <option value="project">项目</option>
            <option value="tool">工具</option>
            <option value="user_pref">偏好</option>
          </select>
        </div>
        <div class="form-group">
          <label class="form-label">标签 (逗号分隔)</label>
          <input type="text" class="form-input" id="m-tags" placeholder="tag1, tag2">
        </div>
        <div class="form-group">
          <label class="form-label">信任度 (0-1)</label>
          <input type="number" class="form-input" id="m-trust" min="0" max="1" step="0.1" value="0.5">
        </div>
        <div class="form-group">
          <label class="form-label">实体 (逗号分隔)</label>
          <input type="text" class="form-input" id="m-entities" placeholder="entity1, entity2">
        </div>
      `, `
        <button class="btn btn-secondary" onclick="closeModal()">取消</button>
        <button class="btn btn-primary" onclick="saveFact()">保存</button>
      `);
    });
  }
}

// Initialize
initFactPage();

// ===== Exports =====
export { loadFacts, loadCategories, renderFacts, toggleFactExpand, editFact, saveFact, deleteFact, allFacts, activeCategory, editingId };

// ===== Global Scope Mounting =====
window.loadFacts = loadFacts;
window.loadCategories = loadCategories;
window.renderFacts = renderFacts;
window.toggleFactExpand = toggleFactExpand;
window.editFact = editFact;
window.saveFact = saveFact;
window.deleteFact = deleteFact;
window.allFacts = allFacts;
window.activeCategory = activeCategory;
window.editingId = editingId;
