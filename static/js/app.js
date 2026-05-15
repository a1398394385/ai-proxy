// ============================================================
// app.js — Hermes Data Browser Entry Point
// ============================================================

import { initTheme, toggleTheme, initSettings, showSettings, switchPage, bus, delegate } from './core.js';
import { loadFacts, initFactPage } from './pages/facts.js';
import { loadTokenStats, initTokenPage } from './pages/tokens.js';
import { loadUpstreamPage, initUpstreamPage, refreshConfigStatus } from './pages/upstreams.js';
import { loadRoutePage, initRoutePage } from './pages/routes.js';
import { loadPricingPage, initPricingPage } from './pages/pricing.js';

const loaders = { facts: loadFacts, tokens: loadTokenStats, models: loadUpstreamPage, routes: loadRoutePage, pricing: loadPricingPage };

initFactPage();
initTokenPage();
initUpstreamPage(); initRoutePage();
initPricingPage();

bus.on('config:upstream-changed', () => refreshConfigStatus());
bus.on('config:model-changed', () => refreshConfigStatus());
bus.on('config:route-changed', () => refreshConfigStatus());

document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  delegate();

  const themeBtn = document.getElementById('theme-toggle');
  if (themeBtn) themeBtn.addEventListener('click', toggleTheme);

  const settingsBtn = document.getElementById('settings-btn');
  if (settingsBtn) settingsBtn.addEventListener('click', showSettings);

  document.querySelectorAll('.nav-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      const page = tab.dataset.page;
      switchPage(page);
      loaders[page]();
    });
  });

  document.getElementById('modal-overlay').addEventListener('click', e => {
    if (e.target.id === 'modal-overlay') {
      document.getElementById('modal-overlay').classList.remove('show');
    }
  });

  initSettings();
  const defaultPage = localStorage.getItem('defaultPage') || 'facts';
  switchPage(defaultPage);
  loaders[defaultPage]();
});
