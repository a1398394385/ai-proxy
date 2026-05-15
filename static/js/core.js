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
        <select class="settings-select" id="modal-default-page-select">
          <option value="facts">📋 Fact Store</option>
          <option value="tokens">📊 Token 统计</option>
          <option value="models">🔌 模型管理</option>
          <option value="routes">🔀 路由映射</option>
        </select>
      </div>
      <div style="display:flex;align-items:center;justify-content:space-between;">
        <div>
          <div style="font-weight:600;margin-bottom:4px;">默认 Token 周期</div>
          <div style="font-size:13px;color:var(--muted);">进入 Token 统计时默认显示的时间范围</div>
        </div>
        <select class="settings-select" id="modal-default-period-select">
          <option value="day">24小时</option>
          <option value="week">7天</option>
          <option value="month">30天</option>
        </select>
      </div>
    </div>`;

  showModal('⚙ 通用设置', body, '');

  // 设置当前值（在 DOM 注入后）
  setTimeout(() => {
    const pageSelect = document.getElementById('modal-default-page-select');
    const periodSelect = document.getElementById('modal-default-period-select');
    if (pageSelect) {
      pageSelect.value = defaultPage;
      pageSelect.addEventListener('change', (e) => saveDefaultPage(e.target.value));
    }
    if (periodSelect) {
      periodSelect.value = defaultPeriod;
      periodSelect.addEventListener('change', (e) => {
        const val = e.target.value;
        localStorage.setItem('defaultPeriod', val);
        currentPeriod = val;
        window.currentPeriod = val;
        document.querySelectorAll('.period-btn').forEach(btn => {
          btn.classList.toggle('active', btn.dataset.period === val);
        });
      });
    }
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

// 仅保留跨模块同步必需的 window 挂载
window.currentPeriod = currentPeriod;
