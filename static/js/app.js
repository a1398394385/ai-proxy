// ============================================================
// app.js — Hermes Data Browser Entry Point
// ============================================================

import { initTheme, toggleTheme, initSettings, showSettings, saveDefaultPage, pageLoaders, bus } from './core.js';
import { loadFacts, initFactPage } from './pages/facts.js';
import { loadTokenStats, initTokenPage } from './pages/tokens.js';
import { loadUpstreamPage, initUpstreamPage } from './pages/upstreams.js';
import { loadRoutePage, initRoutePage } from './pages/routes.js';
import { loadDbQuery } from './pages/dbquery.js';
import { loadPricingPage, initPricingPage } from './pages/pricing.js';

pageLoaders.facts = loadFacts;
pageLoaders.tokens = loadTokenStats;
pageLoaders.models = loadUpstreamPage;
pageLoaders.routes = loadRoutePage;
pageLoaders.dbquery = loadDbQuery;
pageLoaders.pricing = loadPricingPage;

initFactPage();
initTokenPage();
initUpstreamPage(); initRoutePage();
initPricingPage();

bus.on('config:upstream-changed', () => { window.refreshUpstreamDropdown?.(); window.refreshConfigStatus?.(); });
bus.on('config:model-changed', () => { window.refreshConfigStatus?.(); });
bus.on('config:route-changed', () => { window.refreshConfigStatus?.(); });

document.addEventListener('DOMContentLoaded', () => {
  initTheme();

  const themeBtn = document.getElementById('theme-toggle');
  if (themeBtn) themeBtn.addEventListener('click', toggleTheme);

  const settingsBtn = document.getElementById('settings-btn');
  if (settingsBtn) settingsBtn.addEventListener('click', showSettings);

  document.querySelectorAll('.nav-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      const page = tab.dataset.page;
      window.currentPage = page;

      document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');

      document.getElementById('page-facts').classList.toggle('hidden', page !== 'facts');
      document.getElementById('page-tokens').classList.toggle('hidden', page !== 'tokens');
      document.getElementById('page-models').classList.toggle('hidden', page !== 'models');
      document.getElementById('page-routes').classList.toggle('hidden', page !== 'routes');
      document.getElementById('page-dbquery').classList.toggle('hidden', page !== 'dbquery');
      document.getElementById('page-pricing').classList.toggle('hidden', page !== 'pricing');

      if (page === 'facts') loadFacts();
      if (page === 'tokens') loadTokenStats();
      if (page === 'models') loadUpstreamPage();
      if (page === 'routes') loadRoutePage();
      if (page === 'dbquery') loadDbQuery();
      if (page === 'pricing') loadPricingPage();
    });
  });

  document.getElementById('modal-overlay').addEventListener('click', e => {
    if (e.target.id === 'modal-overlay') {
      document.getElementById('modal-overlay').classList.remove('show');
    }
  });

  initSettings();
});
