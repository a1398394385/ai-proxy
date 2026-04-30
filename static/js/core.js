// ===== 全局状态 =====
export let currentPage = 'facts';
export let currentPeriod = 'week';
export let allFacts = [];
export let allModels = [];
export let activeCategory = null;
export let editingId = null;

export const catLabels = { general: '通用', project: '项目', tool: '工具', user_pref: '偏好' };
export const catIcons = { general: '📝', project: '📁', tool: '🔧', user_pref: '✨' };

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
export function initSettings() {
  const savedDefaultPage = localStorage.getItem('defaultPage') || 'facts';
  const savedDefaultPeriod = localStorage.getItem('defaultPeriod') || 'week';

  // 同步到当前周期
  currentPeriod = savedDefaultPeriod;
  window.currentPeriod = currentPeriod;

  // 更新周期按钮的活动状态
  document.querySelectorAll('.period-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.period === savedDefaultPeriod);
  });

  // 应用默认页面
  applyDefaultPage(savedDefaultPage);
}

export function applyDefaultPage(page) {
  const validPages = ['facts', 'tokens', 'models', 'routes'];
  if (!page || !validPages.includes(page)) {
    page = 'facts';
  }

  currentPage = page;

  // 更新导航标签状态
  document.querySelectorAll('.nav-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.page === page);
  });

  // 显示/隐藏页面
  document.getElementById('page-facts').classList.toggle('hidden', page !== 'facts');
  document.getElementById('page-tokens').classList.toggle('hidden', page !== 'tokens');
  document.getElementById('page-models').classList.toggle('hidden', page !== 'models');
  document.getElementById('page-routes').classList.toggle('hidden', page !== 'routes');

  // 加载数据（通过回调注册表，避免跨模块导入）
  if (page === 'facts' && pageLoaders.facts) pageLoaders.facts();
  if (page === 'tokens' && pageLoaders.tokens) pageLoaders.tokens();
  if (page === 'models' && pageLoaders.models) pageLoaders.models();
  if (page === 'routes' && pageLoaders.routes) pageLoaders.routes();
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

// ===== SVG 面积图 =====
export let chartData = [];
export let hiddenSeries = new Set();

// ===== 模态框 =====
export function showModal(title, content, footer) {
  document.getElementById('modal-title').textContent = title;
  document.getElementById('modal-body').innerHTML = content;
  document.getElementById('modal-footer').innerHTML = footer;
  document.getElementById('modal-overlay').classList.add('show');
}

export function closeModal() {
  document.getElementById('modal-overlay').classList.remove('show');
  editingId = null;
}

// ===== Event Bus =====
export const bus = {
  emit(name, detail) { document.dispatchEvent(new CustomEvent(name, { detail })); },
  on(name, fn) { document.addEventListener(name, fn); }
};

// Callback registry for cross-page data loading (avoids circular imports)
export const pageLoaders = { facts: null, tokens: null, models: null, routes: null };

// Global scope mounting for onclick handlers (required by ES modules)
window.api = api;
window.formatNumber = formatNumber;
window.formatTokens = formatTokens;
window.escHtml = escHtml;
window.initTheme = initTheme;
window.toggleTheme = toggleTheme;
window.updateThemeButton = updateThemeButton;
window.initSettings = initSettings;
window.applyDefaultPage = applyDefaultPage;
window.showSettings = showSettings;
window.saveDefaultPage = saveDefaultPage;
window.showModal = showModal;
window.closeModal = closeModal;
window.bus = bus;
window.currentPage = currentPage;
window.currentPeriod = currentPeriod;
window.allFacts = allFacts;
window.allModels = allModels;
window.activeCategory = activeCategory;
window.editingId = editingId;
window.catLabels = catLabels;
window.catIcons = catIcons;
window.chartData = chartData;
window.hiddenSeries = hiddenSeries;
