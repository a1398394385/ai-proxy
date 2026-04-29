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

// 初始化主题
initTheme();

// ===== 设置功能 =====
export function initSettings() {
  const savedDefaultPage = localStorage.getItem('defaultPage') || 'facts';
  const pageSelect = document.getElementById('default-page-select');
  if (pageSelect) {
    pageSelect.value = savedDefaultPage;
  }

  // 初始化默认周期设置
  const savedDefaultPeriod = localStorage.getItem('defaultPeriod') || 'week';
  const periodSelect = document.getElementById('default-period-select');
  if (periodSelect) {
    periodSelect.value = savedDefaultPeriod;
  }
  // 同步到当前周期
  currentPeriod = savedDefaultPeriod;

  // 更新周期按钮的活动状态
  document.querySelectorAll('.period-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.period === savedDefaultPeriod);
  });

  // 应用默认页面
  applyDefaultPage(savedDefaultPage);
}

export function applyDefaultPage(page) {
  if (!page || (page !== 'facts' && page !== 'tokens')) {
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
  document.getElementById('page-settings').classList.add('hidden');

  // 加载数据
  if (page === 'facts') window.loadFacts && window.loadFacts();
  if (page === 'tokens') loadTokenStats();
}

export function showSettings() {
  currentPage = 'settings';

  // 隐藏所有主页面
  document.getElementById('page-facts').classList.add('hidden');
  document.getElementById('page-tokens').classList.add('hidden');
  document.getElementById('page-settings').classList.remove('hidden');

  // 移除导航标签的活动状态
  document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
}

export function saveDefaultPage(page) {
  localStorage.setItem('defaultPage', page);
}

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
