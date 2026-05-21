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

['upstream-changed', 'model-changed', 'route-changed'].forEach(evt =>
  bus.on('config:' + evt, () => refreshConfigStatus())
);

document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  delegate();

  const themeBtn = document.getElementById('theme-toggle');
  if (themeBtn) themeBtn.addEventListener('click', toggleTheme);

  const settingsBtn = document.getElementById('settings-btn');
  if (settingsBtn) settingsBtn.addEventListener('click', () => {
    settingsBtn.classList.add('spin');
    setTimeout(() => settingsBtn.classList.remove('spin'), 600);
    showSettings();
  });

  document.querySelectorAll('.nav-tab').forEach(tab =>
    tab.addEventListener('click', () => { switchPage(tab.dataset.page); loaders[tab.dataset.page](); })
  );

  document.getElementById('modal-overlay').addEventListener('click', e => {
    if (e.target.id === 'modal-overlay') e.target.classList.remove('show');
  });

  initSettings();
  const defaultPage = localStorage.getItem('defaultPage') || 'facts';
  switchPage(defaultPage);
  loaders[defaultPage]();
});
