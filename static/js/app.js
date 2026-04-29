// ============================================================
// app.js — Hermes Data Browser Entry Point
// ============================================================

import { initTheme, toggleTheme, initSettings, showSettings, saveDefaultPage, pageLoaders, bus } from './core.js';
import { loadFacts, initFactPage } from './pages/facts.js';
import { loadTokenStats, initTokenPage } from './pages/tokens.js';
import { loadModelConfig, initModelPage } from './pages/models.js';

pageLoaders.facts = loadFacts;
pageLoaders.tokens = loadTokenStats;
pageLoaders.models = loadModelConfig;

initFactPage();
initTokenPage();
initModelPage();

bus.on('config:dirty', () => {
  const btn = document.getElementById('apply-config-btn');
  if (btn) { btn.classList.add('pulse-orange'); btn.textContent = '⚠️ 应用配置'; }
});
bus.on('config:applied', () => {
  const btn = document.getElementById('apply-config-btn');
  if (btn) { btn.classList.remove('pulse-orange'); btn.textContent = '✅ 应用配置'; }
});

bus.on('config:upstream-changed', () => { window.refreshUpstreamDropdown?.(); window.refreshConfigStatus?.(); });
bus.on('config:model-changed', () => { window.refreshConfigStatus?.(); });
bus.on('config:route-changed', () => { window.refreshConfigStatus?.(); });

document.addEventListener('DOMContentLoaded', () => {
  initTheme();

  const themeBtn = document.getElementById('theme-toggle');
  if (themeBtn) themeBtn.addEventListener('click', toggleTheme);

  const settingsBtn = document.getElementById('settings-btn');
  if (settingsBtn) settingsBtn.addEventListener('click', showSettings);

  const pageSelect = document.getElementById('default-page-select');
  if (pageSelect) pageSelect.addEventListener('change', (e) => saveDefaultPage(e.target.value));

  const periodSelect = document.getElementById('default-period-select');
  if (periodSelect) periodSelect.addEventListener('change', (e) => localStorage.setItem('defaultPeriod', e.target.value));

  document.querySelectorAll('.nav-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      const page = tab.dataset.page;
      window.currentPage = page;

      document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');

      document.getElementById('page-facts').classList.toggle('hidden', page !== 'facts');
      document.getElementById('page-tokens').classList.toggle('hidden', page !== 'tokens');
      document.getElementById('page-models').classList.toggle('hidden', page !== 'models');
      document.getElementById('page-settings').classList.add('hidden');

      if (page === 'facts') loadFacts();
      if (page === 'tokens') loadTokenStats();
      if (page === 'models') loadModelConfig();
    });
  });

  document.getElementById('modal-overlay').addEventListener('click', e => {
    if (e.target.id === 'modal-overlay') {
      document.getElementById('modal-overlay').classList.remove('show');
    }
  });

  initSettings();
});
