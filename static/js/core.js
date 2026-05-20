// ===== 全局状态 =====
export let currentPeriod = 'week';

export const catLabels = { general: '通用', project: '项目', tool: '工具', user_pref: '偏好' };
export const catIcons = { general: '📝', project: '📁', tool: '🔧', user_pref: '✨' };
export const FORMAT_LABELS = { responses: 'Responses', messages: 'Messages', chat_completions: 'Chat' };
export const FORMAT_COLORS = { responses: 'badge-blue', messages: 'badge-purple', chat_completions: 'badge-green' };

// ===== 主题切换 =====
export function initTheme() {
  const savedTheme = localStorage.getItem('theme') || 'dark';
  document.documentElement.setAttribute('data-theme', savedTheme);
  updateThemeButton(savedTheme);
}

export function toggleTheme() {
  const currentTheme = document.documentElement.getAttribute('data-theme') || 'dark';
  const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', newTheme);
  localStorage.setItem('theme', newTheme);
  updateThemeButton(newTheme);
}

export function updateThemeButton(theme) {
  const btn = document.getElementById('theme-toggle');
  if (btn) {
    btn.textContent = theme === 'dark' ? '🌙' : '🌕';
    btn.title = theme === 'dark' ? '切换到亮色模式' : '切换到暗色模式';
  }
}

// ===== 设置功能 =====
const VALID_PAGES = ['facts', 'tokens', 'models', 'routes', 'pricing'];

export function switchPage(page) {
  if (!page || !VALID_PAGES.includes(page)) page = 'facts';
  document.querySelectorAll('.nav-tab').forEach(t => t.classList.toggle('active', t.dataset.page === page));
  VALID_PAGES.forEach(p => document.getElementById(`page-${p}`).classList.toggle('hidden', p !== page));
}

export function initSettings() {
  const savedDefaultPeriod = localStorage.getItem('defaultPeriod') || 'week';
  currentPeriod = savedDefaultPeriod;
  window.currentPeriod = currentPeriod;
  document.querySelectorAll('.period-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.period === savedDefaultPeriod);
  });
}

export function showSettings() {
  const defaultPage = localStorage.getItem('defaultPage') || 'facts';
  const defaultPeriod = localStorage.getItem('defaultPeriod') || 'week';

  const body = `
    <div style="display:flex;flex-direction:column;gap:20px;">
      <div style="display:flex;align-items:center;justify-content:space-between;">
        <div>
          <div style="font-weight:600;margin-bottom:4px;">默认页面</div>
          <div style="font-size:13px;color:var(--muted);">打开网页时默认显示的页面</div>
        </div>
        <div style="min-width:150px">${customSelectHtml('modal-default-page-select', [
          { value: 'facts', label: '📋 Fact Store', selected: defaultPage === 'facts' },
          { value: 'tokens', label: '📊 Token 统计', selected: defaultPage === 'tokens' },
          { value: 'models', label: '🔌 模型管理', selected: defaultPage === 'models' },
          { value: 'routes', label: '🔀 路由映射', selected: defaultPage === 'routes' },
        ], '选择页面')}</div>
      </div>
      <div style="display:flex;align-items:center;justify-content:space-between;">
        <div>
          <div style="font-weight:600;margin-bottom:4px;">默认 Token 周期</div>
          <div style="font-size:13px;color:var(--muted);">进入 Token 统计时默认显示的时间范围</div>
        </div>
        <div style="min-width:150px">${customSelectHtml('modal-default-period-select', [
          { value: 'day', label: '24小时', selected: defaultPeriod === 'day' },
          { value: 'week', label: '7天', selected: defaultPeriod === 'week' },
          { value: 'month', label: '30天', selected: defaultPeriod === 'month' },
        ], '选择周期')}</div>
      </div>
    </div>`;

  showModal('⚙ 通用设置', body, '');

  setTimeout(() => {
    wireCustomSelect('modal-default-page-select');
    wireCustomSelect('modal-default-period-select');
    document.getElementById('modal-default-page-select').addEventListener('change', (e) => saveDefaultPage(e.target.value));
    document.getElementById('modal-default-period-select').addEventListener('change', (e) => {
      localStorage.setItem('defaultPeriod', e.target.value);
      currentPeriod = e.target.value;
      window.currentPeriod = e.target.value;
      document.querySelectorAll('.period-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.period === e.target.value);
      });
    });
  }, 0);
}

export function saveDefaultPage(page) {
  localStorage.setItem('defaultPage', page);
}

document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  const overlay = document.getElementById('modal-overlay');
  if (overlay && overlay.classList.contains('show')) {
    closeModal();
  }
});

// ===== API 调用 =====
export async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opts });
  return res.json();
}

export function formatNumber(n) {
  if (n == null || n === undefined) return '0';
  if (n >= 1000000) return (n / 1000000).toFixed(2) + 'M';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
  return n.toLocaleString();
}

export function formatTokens(n) {
  return n.toLocaleString();
}

export function escHtml(s) {
  const d = document.createElement('div'); d.textContent = s; return d.innerHTML;
}

// ===== 模态框 =====
export function showModal(title, content, footer) {
  document.getElementById('modal-title').textContent = title;
  document.getElementById('modal-body').innerHTML = content;
  document.getElementById('modal-footer').innerHTML = footer;
  document.getElementById('modal-overlay').classList.add('show');
}

export function closeModal() {
  document.getElementById('modal-overlay').classList.remove('show');
}

// ===== Event Bus =====
export const bus = {
  emit(name, detail) { document.dispatchEvent(new CustomEvent(name, { detail })); },
  on(name, fn) { document.addEventListener(name, fn); }
};

// ===== 事件委托 =====
const __actions = {};
export function on(action, fn) { __actions[action] = fn; }

// 注册全局动作
on('closeModal', closeModal);

export function delegate(root = document) {
  const handler = e => {
    const el = e.target.closest('[data-action]');
    if (el) {
      const fn = __actions[el.dataset.action];
      if (fn) fn(e, el);
    }
  };
  root.addEventListener('click', handler);
  root.addEventListener('change', handler);
}

// ===== Custom Select 统一组件 =====
const CS_CHEVRON = '<svg class="cs-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>';

function csBuildDisplay(opt) {
  return escHtml(opt.label) + (opt.hint ? `<span class="cs-hint-inline" style="${opt.hintStyle || ''}">${opt.hint}</span>` : '');
}

export function customSelectHtml(id, opts, placeholder) {
  const sel = opts.find(o => o.selected && o.value) || opts.find(o => o.value) || null;
  const display = sel ? csBuildDisplay(sel) : escHtml(placeholder);
  const allDisabled = opts.every(o => !o.value || o.disabled);
  const optionsHtml = opts.map(o => {
    const cls = ['cs-option'];
    if (o.selected) cls.push('selected');
    if (o.disabled) cls.push('disabled');
    if (!o.value) cls.push('cs-empty');
    return `<div class="${cls.join(' ')}" data-value="${escHtml(o.value)}" ${o.disabled ? 'data-disabled="1"' : ''}>
      <span class="cs-option-text">${escHtml(o.label)}</span>${o.hint ? `<span class="cs-option-hint" style="${o.hintStyle || ''}">${o.hint}</span>` : ''}
    </div>`;
  }).join('');
  return `<input type="hidden" id="${id}" value="${sel ? escHtml(sel.value) : ''}">
    <div class="custom-select${allDisabled ? ' disabled' : ''}" data-cs="${id}">
      <button type="button" class="cs-trigger"><span class="cs-text">${display}</span>${CS_CHEVRON}</button>
      <div class="cs-dropdown">${optionsHtml}</div>
    </div>`;
}

export function wireCustomSelect(id) {
  const cs = document.querySelector(`[data-cs="${id}"]`);
  if (!cs || cs.dataset.wired) return;
  cs.dataset.wired = '1';
  const trigger = cs.querySelector('.cs-trigger');
  const dropdown = cs.querySelector('.cs-dropdown');
  const hidden = document.getElementById(id);

  trigger.addEventListener('click', (e) => {
    e.stopPropagation();
    if (cs.classList.contains('disabled')) return;
    closeAllCustomSelects();
    cs.classList.toggle('open');
  });

  dropdown.addEventListener('click', (e) => {
    const opt = e.target.closest('.cs-option');
    if (!opt || opt.dataset.disabled || opt.classList.contains('cs-empty')) return;
    e.stopPropagation();
    const val = opt.dataset.value;
    hidden.value = val;
    const optText = opt.querySelector('.cs-option-text').textContent;
    const optHint = opt.querySelector('.cs-option-hint');
    trigger.querySelector('.cs-text').innerHTML = escHtml(optText) + (optHint ? `<span class="cs-hint-inline" style="${optHint.style.cssText}">${optHint.textContent}</span>` : '');
    dropdown.querySelectorAll('.cs-option').forEach(o => o.classList.toggle('selected', o === opt));
    cs.classList.remove('open');
    hidden.dispatchEvent(new Event('change'));
  });
}

export function updateCustomSelect(id, opts, placeholder) {
  const cs = document.querySelector(`[data-cs="${id}"]`);
  if (!cs) return;
  const hidden = document.getElementById(id);
  const dropdown = cs.querySelector('.cs-dropdown');
  const textSpan = cs.querySelector('.cs-text');
  const sel = opts.find(o => o.selected && o.value) || opts.find(o => o.value);
  hidden.value = sel ? sel.value : '';
  textSpan.innerHTML = sel ? csBuildDisplay(sel) : escHtml(placeholder || '--');
  dropdown.innerHTML = opts.map(o => {
    const cls = ['cs-option'];
    if (o.selected) cls.push('selected');
    if (o.disabled) cls.push('disabled');
    if (!o.value) cls.push('cs-empty');
    return `<div class="${cls.join(' ')}" data-value="${escHtml(o.value)}" ${o.disabled ? 'data-disabled="1"' : ''}>
      <span class="cs-option-text">${escHtml(o.label)}</span>${o.hint ? `<span class="cs-option-hint" style="${o.hintStyle || ''}">${o.hint}</span>` : ''}
    </div>`;
  }).join('');
  cs.classList.toggle('disabled', opts.every(o => !o.value || o.disabled));
  cs.classList.remove('open');
}

export function closeAllCustomSelects() {
  document.querySelectorAll('.custom-select.open').forEach(el => el.classList.remove('open'));
}

export function buildCustomSelect(parentEl, options, onChange) {
  const container = document.createElement('div');
  container.className = 'custom-select';
  const trigger = document.createElement('button');
  trigger.type = 'button';
  trigger.className = 'cs-trigger';
  trigger.innerHTML = `<span class="cs-text">${escHtml(options[0]?.label || '')}</span>${CS_CHEVRON}`;

  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;inset:0;z-index:299;display:none;';

  const dropdown = document.createElement('div');
  dropdown.className = 'cs-dropdown';
  dropdown.style.cssText = 'position:fixed;display:none;z-index:300;background:hsl(var(--card));border:1px solid hsl(var(--border));border-radius:10px;padding:4px;';
  dropdown.innerHTML = options.map(opt => {
    const cls = opt.value ? 'cs-option' : 'cs-option cs-empty';
    return `<div class="${cls}" data-value="${escHtml(opt.value)}">${escHtml(opt.label)}</div>`;
  }).join('');

  let selectedValue = options[0]?.value || '';

  function close() {
    container.classList.remove('open');
    dropdown.style.display = 'none';
    overlay.style.display = 'none';
  }

  function positionDropdown() {
    const rect = trigger.getBoundingClientRect();
    dropdown.style.left = rect.left + 'px';
    dropdown.style.width = rect.width + 'px';
    dropdown.style.top = (rect.bottom + 4) + 'px';
    const spaceBelow = window.innerHeight - rect.bottom - 8;
    dropdown.style.maxHeight = Math.min(Math.max(spaceBelow, 100), 280) + 'px';
  }

  function open() {
    closeAllCustomSelects();
    positionDropdown();
    dropdown.style.display = 'block';
    overlay.style.display = 'block';
    container.classList.add('open');
  }

  trigger.addEventListener('click', (e) => {
    e.stopPropagation();
    container.classList.contains('open') ? close() : open();
  });

  overlay.addEventListener('click', close);

  dropdown.addEventListener('click', (e) => {
    const opt = e.target.closest('.cs-option');
    if (!opt || opt.classList.contains('cs-empty')) return;
    e.stopPropagation();
    selectedValue = opt.dataset.value;
    trigger.querySelector('.cs-text').textContent = opt.textContent;
    dropdown.querySelectorAll('.cs-option').forEach(o => o.classList.toggle('selected', o.dataset.value === selectedValue));
    close();
    onChange(selectedValue, options.find(o => o.value === selectedValue));
  });

  const onScroll = () => { if (container.classList.contains('open')) positionDropdown(); };
  window.addEventListener('scroll', onScroll, true);

  container.appendChild(trigger);
  parentEl.appendChild(container);
  document.body.appendChild(overlay);
  document.body.appendChild(dropdown);

  return {
    selectOption(v, label) { selectedValue = v; trigger.querySelector('.cs-text').textContent = label || v; close(); },
    getValue: () => selectedValue,
    container,
    destroy() { overlay.remove(); dropdown.remove(); window.removeEventListener('scroll', onScroll, true); },
  };
}

// 全局点击关闭下拉框
document.addEventListener('click', (e) => {
  if (!e.target.closest('.custom-select')) closeAllCustomSelects();
});

// 仅保留跨模块同步必需的 window 挂载
window.currentPeriod = currentPeriod;
