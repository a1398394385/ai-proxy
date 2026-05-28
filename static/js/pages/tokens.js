import { api, formatNumber, escHtml, customSelectHtml, wireCustomSelect, updateCustomSelect } from '../core.js';

// ===== 套用计费专用下拉框（独立实现，避免与模态框层叠冲突） =====
function costCompareSelectHtml(id, opts, placeholder) {
  const sel = opts.find(o => o.selected && o.value) || opts.find(o => o.value) || null;
  const display = sel ? escHtml(sel.label) : escHtml(placeholder);
  const allDisabled = opts.every(o => !o.value || o.disabled);
  const optionsHtml = opts.map(o => {
    const cls = ['cs-option'];
    if (o.selected) cls.push('selected');
    if (o.disabled) cls.push('disabled');
    if (!o.value) cls.push('cs-empty');
    return `<div class="${cls.join(' ')}" data-value="${escHtml(o.value)}" ${o.disabled ? 'data-disabled="1"' : ''}>
      <span class="cs-option-text">${escHtml(o.label)}</span>
    </div>`;
  }).join('');
  return `<input type="hidden" id="${id}" value="${sel ? escHtml(sel.value) : ''}">
    <div class="custom-select${allDisabled ? ' disabled' : ''}" data-ccs="${id}">
      <button type="button" class="cs-trigger"><span class="cs-text">${display}</span><svg class="cs-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg></button>
      <div class="cs-dropdown">${optionsHtml}</div>
    </div>`;
}

function wireCostCompareSelect(id, onChange) {
  const cs = document.querySelector(`[data-ccs="${id}"]`);
  if (!cs || cs.dataset.wired) return;
  cs.dataset.wired = '1';
  const trigger = cs.querySelector('.cs-trigger');
  const dropdown = cs.querySelector('.cs-dropdown');
  const hidden = document.getElementById(id);

  trigger.addEventListener('click', (e) => {
    e.stopPropagation();
    if (cs.classList.contains('disabled')) return;
    if (cs.classList.contains('open')) {
      cs.classList.remove('open');
      dropdown.style.display = 'none';
      dropdown.style.opacity = '';
      dropdown.style.visibility = '';
      dropdown.style.transform = '';
    } else {
      // 关闭其他成本对比下拉框
      document.querySelectorAll('[data-ccs] .cs-dropdown').forEach(dd => { dd.style.display = 'none'; });
      document.querySelectorAll('[data-ccs]').forEach(el => el.classList.remove('open'));
      const rect = trigger.getBoundingClientRect();
      dropdown.style.display = 'block';
      dropdown.style.position = 'fixed';
      dropdown.style.top = (rect.bottom + 4) + 'px';
      dropdown.style.left = rect.left + 'px';
      dropdown.style.width = rect.width + 'px';
      dropdown.style.opacity = '1';
      dropdown.style.visibility = 'visible';
      dropdown.style.transform = 'translateY(0)';
      cs.classList.add('open');
    }
  });

  dropdown.addEventListener('click', (e) => {
    const opt = e.target.closest('.cs-option');
    if (!opt || opt.dataset.disabled || opt.classList.contains('cs-empty')) return;
    e.stopPropagation();
    const val = opt.dataset.value;
    hidden.value = val;
    trigger.querySelector('.cs-text').textContent = opt.querySelector('.cs-option-text').textContent;
    dropdown.querySelectorAll('.cs-option').forEach(o => o.classList.toggle('selected', o === opt));
    cs.classList.remove('open');
    dropdown.style.display = 'none';
    if (onChange) onChange(val);
  });
}

function closeAllCostCompareSelects() {
  document.querySelectorAll('[data-ccs].open').forEach(el => {
    el.classList.remove('open');
    const dd = el.querySelector('.cs-dropdown');
    if (dd) {
      dd.style.display = 'none';
      dd.style.opacity = '';
      dd.style.visibility = '';
      dd.style.transform = '';
    }
  });
}

// 点击页面其他地方关闭成本对比下拉框（排除触发按钮，避免与 trigger click 冲突）
document.addEventListener('click', (e) => {
  if (e.target.closest('[data-ccs]')) return;
  closeAllCostCompareSelects();
});

// ===== Module-local state =====
let allModels = [];
let chartData = [];
let hiddenSeries = new Set();
// Request log state
let requestFilters = { model: '', requestType: '', period: 'week' };
let requestPagination = { limit: 50, offset: 0, total: 0 };
let debounceTimer = null;

// Upstream stats state
let upstreamStatsData = [];
let allPricings = [];
function tk(n, cls) { cls = cls || 'cell-number'; return '<td class="' + cls + '">' + (n || 0).toLocaleString() + '</td>'; }
function typeBadge(upstreamId) {
  return upstreamId === 'opencode'
    ? '<span class="type-badge-term opencode">[opencode]</span>'
    : '<span class="type-badge-term proxy">>_proxy</span>';
}


// ===== Token 统计 =====
async function loadTokenStats(modelFilter) {
  const period = window.currentPeriod || 'week';
  // modelFilter 为 null/undefined 时从搜索框读取，确保 period 切换后过滤不丢
  if (modelFilter === undefined || modelFilter === null) {
    modelFilter = document.getElementById('model-search')?.value?.trim() || null;
  }
  const modelParam = modelFilter ? `&model=${encodeURIComponent(modelFilter)}` : '';
  const [stats, byModel, trend, pricingRes] = await Promise.all([
    api(`/api/token_stats?period=${period}${modelParam}`),
    api(`/api/token_stats/by_model?period=${period}`),
    api(`/api/token_stats/trend?period=${period}${modelParam}`),
    api(`/api/pricing`).catch(() => ({ pricings: [] })),
  ]);

  allModels = byModel.models || [];
  allPricings = pricingRes.pricings || [];

  const periodLabels = { day: '24小时', week: '7天', month: '30天' };
  document.getElementById('chart-period-label').textContent = periodLabels[period] || '7天';

  renderKPI(stats);
  renderTrendChart(trend.trends);
  renderModelTable(allModels, modelFilter || undefined);
}

function formatTokenDisplay(value) {
  if (value >= 1_000_000_000) return (value / 1_000_000_000).toFixed(2) + 'B';
  if (value >= 1_000_000) return (value / 1_000_000).toFixed(2) + 'M';
  return value.toLocaleString();
}

function renderKPI(stats) {
  document.getElementById('kpi-container').innerHTML = `
    <div class="kpi-card">
      <div class="kpi-header">
        <span class="kpi-label">API 请求次数</span>
        <div class="kpi-icon blue">🔄</div>
      </div>
      <div class="kpi-value">${(stats.request_count || 0).toLocaleString()}</div>
      <div class="kpi-sub">模型调用次数</div>
    </div>

    <div class="kpi-card kpi-card-merged">
      <div class="kpi-header">
        <span class="kpi-label">总 Tokens</span>
        <div class="kpi-icon" style="background:hsl(var(--primary)/0.15);color:hsl(var(--primary))">Σ</div>
      </div>
      <div class="kpi-value kpi-value-total">${formatTokenDisplay(stats.total_tokens || 0)}</div>
      <div class="kpi-merged-grid">
        <div class="kpi-merged-item">
          <span class="kpi-merged-label">
            <span class="kpi-dot" style="background:hsl(var(--blue))"></span>
            Input
          </span>
          <span class="kpi-merged-value blue">${formatNumber(stats.input_tokens)}</span>
        </div>
        <div class="kpi-merged-item">
          <span class="kpi-merged-label">
            <span class="kpi-dot" style="background:hsl(160 60% 45%)"></span>
            Output
          </span>
          <span class="kpi-merged-value green">${formatNumber(stats.output_tokens)}</span>
        </div>
        <div class="kpi-merged-item">
          <span class="kpi-merged-label">
            <span class="kpi-dot" style="background:hsl(var(--purple))"></span>
            Cache Read
          </span>
          <span class="kpi-merged-value purple">${formatNumber(stats.cache_read_tokens)}</span>
        </div>
        <div class="kpi-merged-item">
          <span class="kpi-merged-label">
            <span class="kpi-dot" style="background:hsl(var(--orange))"></span>
            Cache Create
          </span>
          <span class="kpi-merged-value orange">${formatNumber(stats.cache_write_tokens)}</span>
        </div>
      </div>
    </div>

    <div class="kpi-card">
      <div class="kpi-header">
        <span class="kpi-label">估算成本</span>
        <div class="kpi-icon red">¥</div>
      </div>
      <div class="kpi-value red">¥${(stats.estimated_cost_cny || 0).toFixed(6)}</div>
      <div class="kpi-sub">CNY</div>
    </div>
  `;
}

// ===== SVG 面积图实现 =====

function renderTrendChart(trends) {
  chartData = trends || [];
  if (!chartData.length) {
    document.getElementById('chart-areas').innerHTML = '<text x="50%" y="50%" text-anchor="middle" fill="hsl(var(--muted-foreground))">暂无数据</text>';
    return;
  }

  const svg = document.getElementById('trend-chart');
  const wrapper = document.getElementById('chart-wrapper');
  const width = wrapper.clientWidth;
  const height = wrapper.clientHeight;

  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);

  const margin = { top: 10, right: 60, bottom: 30, left: 50 };
  const chartWidth = width - margin.left - margin.right;
  const chartHeight = height - margin.top - margin.bottom;

  function niceMax(max) {
    if (max === 0) return 1;
    if (max < 10) return 10;
    const e = Math.floor(Math.log10(max)), f = max / 10 ** e;
    const nf = f <= 1.2 ? 1.2 : f <= 1.5 ? 1.5 : f <= 2 ? 2 : f <= 3 ? 3 : f <= 5 ? 5 : f <= 7 ? 7 : 10;
    return nf * 10 ** e;
  }

  const maxIndividual = Math.max(
    ...chartData.map(d => Math.max(
      hiddenSeries.has('inputTokens') ? 0 : (d.input_tokens || 0),
      hiddenSeries.has('outputTokens') ? 0 : (d.output_tokens || 0),
      hiddenSeries.has('cacheReadTokens') ? 0 : (d.cache_read_tokens || 0),
      hiddenSeries.has('cacheWriteTokens') ? 0 : (d.cache_write_tokens || 0)
    )),
    1
  );
  const yMax = niceMax(maxIndividual);
  const yTicks = 5;

  const gridGroup = document.getElementById('chart-grid');
  const axesGroup = document.getElementById('chart-axes');
  let gridHtml = '', axesHtml = '';
  for (let i = 0; i <= yTicks; i++) {
    const y = margin.top + (chartHeight * i / yTicks);
    gridHtml += `<line x1="${margin.left}" y1="${y}" x2="${width - margin.right}" y2="${y}" class="area-chart-grid"/>`;
  }
  gridGroup.innerHTML = gridHtml;

  function formatAxisValue(v) {
    return v >= 1e6 ? (v / 1e6).toFixed(1) + 'M' : v >= 1e3 ? (v / 1e3).toFixed(0) + 'k' : String(v);
  }

  for (let i = 0; i <= yTicks; i++) {
    const y = margin.top + (chartHeight * i / yTicks);
    const value = Math.round(yMax * (1 - i / yTicks));
    axesHtml += `<text x="${margin.left - 10}" y="${y + 4}" text-anchor="end" class="area-chart-tick">${formatAxisValue(value)}</text>`;
  }

  const costYMax = niceMax(Math.max(...chartData.map(d => d.estimated_cost_cny || 0), 0.0001));

  function formatCostAxis(v) {
    if (v === 0) return '¥0';
    return '¥' + (v >= 10 ? v.toFixed(1) : v >= 1 ? v.toFixed(2) : v >= 0.1 ? v.toFixed(3) : v.toFixed(6));
  }

  axesHtml += `<line x1="${width - margin.right}" y1="${margin.top}" x2="${width - margin.right}" y2="${margin.top + chartHeight}" class="area-chart-axis"/>`;
  for (let i = 0; i <= yTicks; i++) {
    const y = margin.top + (chartHeight * i / yTicks);
    const value = costYMax * (1 - i / yTicks);
    axesHtml += `<text x="${width - margin.right + 8}" y="${y + 4}" text-anchor="start" class="area-chart-tick" fill="#f43f5e">${formatCostAxis(value)}</text>`;
  }

  const xStep = chartWidth / (chartData.length - 1 || 1);
  const dataCount = chartData.length;

  const hourData = dataCount === 24;
  const labelInterval = hourData ? 1 : (dataCount === 30 ? 5 : Math.ceil(dataCount / 7));
  const labelFormatter = hourData
    ? (d) => { const p = d.date.split(' '); return p.length === 2 ? p[1] : d.date; }
    : (d) => d.date.slice(5);

  chartData.forEach((d, i) => {
    if (i % labelInterval !== 0 && i !== dataCount - 1) return;
    const x = margin.left + i * xStep;
    let anchor = 'middle', lx = x;
    if (i === dataCount - 1) { lx = Math.min(x, width - margin.right - 5); anchor = 'end'; }
    else if (i === 0) { anchor = 'start'; lx = Math.max(x, margin.left + 5); }
    const label = labelFormatter(d, i);
    if (label) axesHtml += `<text x="${lx}" y="${height - 10}" text-anchor="${anchor}" class="area-chart-tick">${label}</text>`;
  });

  axesGroup.innerHTML = axesHtml;

  const areasGroup = document.getElementById('chart-areas');
  areasGroup.innerHTML = '';

  const series = [
    { key: 'inputTokens', gradient: 'url(#gradientInput)', class: 'area-path-input', rawKey: 'input_tokens' },
    { key: 'outputTokens', gradient: 'url(#gradientOutput)', class: 'area-path-output', rawKey: 'output_tokens' },
    { key: 'cacheReadTokens', gradient: 'url(#gradientCacheRead)', class: 'area-path-cache-read', rawKey: 'cache_read_tokens' },
    { key: 'cacheWriteTokens', gradient: 'url(#gradientCacheWrite)', class: 'area-path-cache-write', rawKey: 'cache_write_tokens' }
  ];

  const chartBottom = margin.top + chartHeight;

  series.forEach(s => {
    if (hiddenSeries.has(s.key)) return;
    const pts = chartData.map((d, i) => ({ x: margin.left + i * xStep, y: margin.top + chartHeight * (1 - (d[s.rawKey] || 0) / yMax) }));
    let d = 'M' + pts[0].x + ' ' + pts[0].y;
    for (let i = 1; i < pts.length; i++) {
      d += ' C' + ((pts[i - 1].x + pts[i].x) / 2) + ' ' + pts[i - 1].y + ',' + ((pts[i - 1].x + pts[i].x) / 2) + ' ' + pts[i].y + ',' + pts[i].x + ' ' + pts[i].y;
    }
    d += ' L' + pts[pts.length - 1].x + ' ' + chartBottom + ' L' + pts[0].x + ' ' + chartBottom + ' Z';
    const el = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    el.setAttribute('d', d); el.setAttribute('class', s.class); el.setAttribute('fill', s.gradient);
    areasGroup.appendChild(el);
  });

  if (!hiddenSeries.has('costLine')) {
    const pts = chartData.map((d, i) => ({ x: margin.left + i * xStep, y: margin.top + chartHeight * (1 - (d.estimated_cost_cny || 0) / costYMax) }));
    let d = 'M' + pts[0].x + ' ' + pts[0].y;
    for (let i = 1; i < pts.length; i++) {
      d += ' C' + ((pts[i - 1].x + pts[i].x) / 2) + ' ' + pts[i - 1].y + ',' + ((pts[i - 1].x + pts[i].x) / 2) + ' ' + pts[i].y + ',' + pts[i].x + ' ' + pts[i].y;
    }
    const el = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    el.setAttribute('d', d); el.setAttribute('class', 'area-path-cost');
    areasGroup.appendChild(el);
  }

  document.querySelectorAll('.legend-item').forEach(item => item.classList.toggle('off', hiddenSeries.has(item.dataset.series)));

  const overlay = document.getElementById('chart-overlay');
  const tooltip = document.getElementById('chart-tooltip');
  const cursorLine = document.getElementById('chart-cursor-line');

  overlay.onmousemove = e => {
    const rect = svg.getBoundingClientRect();
    const idx = Math.round((e.clientX - rect.left - margin.left) / xStep);
    if (idx >= 0 && idx < chartData.length) {
      const px = margin.left + idx * xStep;
      cursorLine.setAttribute('x1', px); cursorLine.setAttribute('x2', px);
      cursorLine.style.display = 'block';
      showTooltip(e.clientX, e.clientY, chartData[idx]);
    }
  };
  overlay.onmouseleave = () => { tooltip.classList.remove('show'); cursorLine.style.display = 'none'; };
}

function showTooltip(mouseX, mouseY, data) {
  const tooltip = document.getElementById('chart-tooltip');
  const title = document.getElementById('tooltip-title');
  const content = document.getElementById('tooltip-content');

  title.textContent = data.date;

  const items = [
    { label: '输入 Tokens', value: data.input_tokens, color: '#3b82f6', key: 'inputTokens' },
    { label: '输出 Tokens', value: data.output_tokens, color: '#22c55e', key: 'outputTokens' },
    { label: '缓存读取', value: data.cache_read_tokens, color: '#a855f7', key: 'cacheReadTokens' },
    { label: '缓存写入', value: data.cache_write_tokens, color: '#f97316', key: 'cacheWriteTokens' },
    { label: '成本', value: '¥' + data.estimated_cost_cny.toFixed(6), color: '#f43f5e', bold: true }
  ];

  content.innerHTML = items.filter(item => !item.key || !hiddenSeries.has(item.key)).map(item => `
    <div class="tooltip-row">
      <div class="tooltip-label">
        <div class="tooltip-dot" style="background:${item.color}"></div>
        <span>${item.label}</span>
      </div>
      <span class="tooltip-value" style="color:${item.bold ? 'hsl(var(--foreground))' : ''}">${typeof item.value === 'number' ? item.value.toLocaleString() : item.value}</span>
    </div>
  `).join('');

  const rect = document.getElementById('chart-wrapper').getBoundingClientRect();
  let left = mouseX - rect.left + 15;
  let top = mouseY - rect.top - 10;

  tooltip.style.left = left + 'px';
  tooltip.style.top = top + 'px';
  tooltip.classList.add('show');

  const tipW = tooltip.offsetWidth;
  if (left + tipW > rect.width) left = mouseX - rect.left - tipW - 15;
  const tipH = tooltip.offsetHeight;
  if (top + tipH > rect.height) top = mouseY - rect.top - tipH - 10;
  if (top < 0) top = 4;

  tooltip.style.left = left + 'px';
  tooltip.style.top = top + 'px';
}

// ─── 成本计算 ───

function findPricing(modelName) {
  const key = (modelName || '').toLowerCase();
  return allPricings.find(p => (p.model_id || '').toLowerCase() === key) || null;
}

function calcCost(modelData, pricingEntry) {
  const rate = pricingEntry.currency === 'USD' ? 7 : 1;
  const mult = parseFloat(pricingEntry.multiplier || '1.0');
  const M = 1_000_000;
  const hasCacheRd = parseFloat(pricingEntry.cache_read_cost_per_million || 0) > 0;
  const hasCacheWr = parseFloat(pricingEntry.cache_creation_cost_per_million || 0) > 0;
  const inputToks = (modelData.input_tokens || 0)
    + (hasCacheRd ? 0 : (modelData.cache_read_tokens || 0))
    + (hasCacheWr ? 0 : (modelData.cache_write_tokens || 0));
  const input   = inputToks / M * pricingEntry.input_cost_per_million * rate * mult;
  const output  = (modelData.output_tokens       || 0) / M * pricingEntry.output_cost_per_million         * rate * mult;
  const cacheRd = hasCacheRd ? (modelData.cache_read_tokens  || 0) / M * pricingEntry.cache_read_cost_per_million     * rate * mult : 0;
  const cacheWr = hasCacheWr ? (modelData.cache_write_tokens || 0) / M * pricingEntry.cache_creation_cost_per_million * rate * mult : 0;
  const r = v => Math.round(v * 1e6) / 1e6;
  return { input: r(input), output: r(output), cacheRead: r(cacheRd), cacheWrite: r(cacheWr), total: r(input + output + cacheRd + cacheWr) };
}

function renderModelTable(models, filterOverride) {
  const filter = filterOverride !== undefined ? filterOverride.toLowerCase() : document.getElementById('model-search').value.toLowerCase();
  const filtered = filter ? models.filter(m => m.model.toLowerCase().includes(filter)) : models;

  document.getElementById('model-count').textContent = `${filtered.length} 个模型`;

  const tbody = document.querySelector('#model-table tbody');

  if (!filtered.length) {
    tbody.innerHTML = '<tr><td colspan="9" class="empty-state">没有找到模型</td></tr>';
    return;
  }

  // 按 total_tokens 排序以分配排名
  const sorted = [...filtered].sort((a, b) => b.total_tokens - a.total_tokens);
  const rankMap = {};
  sorted.forEach((m, i) => { rankMap[m.model] = i + 1; });

  // 颜色池 — 给每个模型分配一个独特的点颜色
  const dotColors = [
    '#3b82f6', '#22c55e', '#a855f7', '#f97316', '#f43f5e',
    '#06b6d4', '#eab308', '#84cc16', '#ec4899', '#6366f1',
    '#14b8a6', '#f59e0b', '#8b5cf6', '#ef4444', '#0ea5e9',
  ];

  tbody.innerHTML = filtered.map((m, idx) => {
    const rank = rankMap[m.model] || 99;
    const rankClass = rank === 1 ? 'model-rank-1' : rank === 2 ? 'model-rank-2' : rank === 3 ? 'model-rank-3' : 'model-rank-default';
    const dotColor = dotColors[idx % dotColors.length];

    const modelLabel = m.display_name || m.model;
    const cacheBase = m.input_tokens + m.cache_read_tokens + m.cache_write_tokens;
    const cacheRate = cacheBase > 0 ? (m.cache_read_tokens / cacheBase * 100) : 0;

    return `<tr data-model="${escHtml(m.model)}" class="model-row">
      <td>
        <div class="model-name-cell">
          <span class="model-rank ${rankClass}">${rank}</span>
          <span class="model-dot" style="background:${dotColor}"></span>
          <span class="model-name-text">${escHtml(modelLabel)}</span>
        </div>
      </td>
      <td class="cell-requests">${(m.request_count || 0).toLocaleString()}</td>
      ${tk(m.input_tokens)}
      ${tk(m.output_tokens)}
      ${tk(m.cache_read_tokens)}
      ${tk(m.cache_write_tokens)}
      <td class="cell-total">${((m.total_tokens)/1e6).toFixed(2) + "M"}</td>
      <td>
        <div class="pct-cell">
          <div class="pct-bar-track">
            <div class="pct-bar-segment cache-read" style="width:${cacheRate}%"></div>
          </div>
          <span class="pct-value">${cacheRate.toFixed(1)}%</span>
        </div>
      </td>
      <td class="cell-cost">¥${m.estimated_cost_cny.toFixed(6)}</td>
    </tr>`;
  }).join('');

  // 添加展开/收起事件绑定
  tbody.querySelectorAll('.model-row').forEach(row => {
    row.style.cursor = 'pointer';
    row.addEventListener('click', () => {
      const model = row.dataset.model;
      if (row.classList.contains('expanded')) {
        collapseModelRow(row);
      } else {
        tbody.querySelectorAll('.model-row.expanded').forEach(expandedRow => {
          if (expandedRow !== row) collapseModelRow(expandedRow);
        });
        expandModelRow(model, row);
      }
    });
  });
}

// ─── 成本明细条 ───


function costCard(label, value, type, baseValue) {
  let deltaHtml = '';
  if (baseValue !== undefined) {
    const delta = baseValue > 0 ? ((value - baseValue) / baseValue * 100) : 0;
    const cls = delta >= 0 ? 'up' : 'down';
    const sign = delta >= 0 ? '+' : '';
    deltaHtml = `<span class="cost-card-delta ${cls}">${sign}${delta.toFixed(0)}%</span>`;
  }
  return `<div class="cost-card ${type}">
    <div class="cost-card-label">${label}</div>
    <div class="cost-card-value">¥${value.toFixed(6)}</div>
    ${deltaHtml}
  </div>`;
}

function renderCostBar(modelName, detailContent) {
  const modelData = allModels.find(m => m.model === modelName);
  if (!modelData) return;

  const period = window.currentPeriod || 'week';
  const periodLabel = { day: '最近 24 小时', week: '最近 7 天', month: '最近 30 天' }[period] || '最近 7 天';

  const wrap = document.createElement('div');
  wrap.className = 'cost-panel';
  const pricing = findPricing(modelName);

  if (!pricing) {
    wrap.innerHTML = `<div class="cost-panel-none">
      <span class="cost-panel-period-dot"></span>
      成本明细 — ${periodLabel} · 未配置计费，成本按 ¥0 计算
    </div>`;
    detailContent.insertBefore(wrap, detailContent.firstChild);
    return;
  }

  const c = calcCost(modelData, pricing);
  const csId = 'cc-' + modelName.replace(/[^a-zA-Z0-9]/g, '-');

  const options = [
    { value: '', label: '— 不对比 —' },
    ...allPricings.map(p => ({ value: p.model_id, label: p.display_name || p.model_id })),
  ];

  wrap.innerHTML = `
    <div class="cost-panel-top">
      <span class="cost-panel-period">
        <span class="cost-panel-period-dot"></span>
        ${periodLabel}
      </span>
      <div class="cost-panel-total">
        <div class="cost-panel-total-value">¥${c.total.toFixed(6)}</div>
        <div class="cost-panel-total-label">合计成本</div>
      </div>
    </div>
    <div class="cost-cards">
      ${costCard('Input', c.input, 'input')}
      ${costCard('Output', c.output, 'output')}
      ${costCard('Cache Read', c.cacheRead, 'cache-rd')}
      ${costCard('Cache Write', c.cacheWrite, 'cache-wr')}
    </div>
    <div class="cost-compare-bar">
      <span class="cost-compare-label">套用计费</span>
      ${costCompareSelectHtml(csId, options, '— 不对比 —')}
    </div>
    <div class="cost-compare-result"><div class="cost-compare-result-inner"></div></div>`;

  detailContent.insertBefore(wrap, detailContent.firstChild);

  const compareResult = wrap.querySelector('.cost-compare-result');
  const compareInner = wrap.querySelector('.cost-compare-result-inner');
  const hidden = document.getElementById(csId);

  wireCostCompareSelect(csId, (selectedValue) => {
    if (!selectedValue) {
      compareResult.classList.remove('show');
      return;
    }
    const cp = allPricings.find(p => p.model_id === selectedValue);
    if (!cp) return;

    const cc = calcCost(modelData, cp);
    const delta = c.total > 0 ? ((cc.total - c.total) / c.total * 100) : 0;
    const deltaClass = delta >= 0 ? 'up' : 'down';
    const deltaSign = delta >= 0 ? '+' : '';
    const deltaPct = deltaSign + delta.toFixed(0) + '%';

    compareInner.innerHTML = `
      <div class="cost-compare-result-top">
        <span class="cost-compare-result-model">${escHtml(cp.display_name || cp.model_id)}</span>
        <div class="cost-compare-result-right">
          <span class="cost-compare-result-total">¥${cc.total.toFixed(6)}</span>
          <span class="cost-compare-delta ${deltaClass}">${deltaPct}</span>
        </div>
      </div>
      <div class="cost-cards">
        ${costCard('Input', cc.input, 'input', c.input)}
        ${costCard('Output', cc.output, 'output', c.output)}
        ${costCard('Cache Read', cc.cacheRead, 'cache-rd', c.cacheRead)}
        ${costCard('Cache Write', cc.cacheWrite, 'cache-wr', c.cacheWrite)}
      </div>`;
    compareResult.classList.add('show');
  });
}

// ===== 展开/收起模型行 =====

function expandModelRow(model, rowElement) {
  if (rowElement.nextSibling && rowElement.nextSibling.classList && rowElement.nextSibling.classList.contains('model-detail-row')) {
    collapseModelRow(rowElement);
    return;
  }

  const period = window.currentPeriod || 'week';
  const encodedModel = encodeURIComponent(model);
  api(`/api/token_stats/by_model/${encodedModel}/requests?period=${period}&limit=50`)
    .then(data => {
      const requests = data.requests || [];
      const limit = Math.min(requests.length, 50);

      const rows = requests.slice(0, limit).map(r => {
        const badgeHtml = typeBadge(r.upstream_id);
        const timeStr = r.created_at || r.request_ts || r.timestamp || '-';
        const costStr = ((r.input_cost_cny || 0) + (r.output_cost_cny || 0) + (r.cache_read_cost_cny || 0) + (r.cache_write_cost_cny || 0)).toFixed(6);

        const tokenCells = `${tk(r.input_tokens)}
             ${tk(r.output_tokens)}
             ${tk(r.cache_read_tokens)}
             ${tk(r.cache_write_tokens)}
             ${tk((r.input_tokens||0)+(r.output_tokens||0)+(r.cache_read_tokens||0)+(r.cache_write_tokens||0), "cell-total")}
             <td class="cell-number">${r.duration_ms ? (r.duration_ms / 1000).toFixed(2) + 's' : '-'}</td>
             <td class="cell-cost"><span class="cost-badge">¥${costStr}</span></td>`;

        return `<tr class="detail-row">
          <td class="cell-detail-id">${escHtml(r.request_id || r.id || '-')}</td>
          <td class="cell-number">${escHtml(timeStr)}</td>
          <td>${badgeHtml}</td>
          ${tokenCells}
        </tr>`;
      }).join('');

      const tr = document.createElement('tr');
      tr.className = 'model-detail-row';
      tr.innerHTML = `<td colspan="10" class="detail-content"></td>`;
      rowElement.after(tr);
      const detailContent = tr.querySelector('.detail-content');
      renderCostBar(model, detailContent);
      rowElement.classList.add('expanded');

      if (!requests.length) {
        detailContent.insertAdjacentHTML('beforeend',
          `<div class="empty-state" style="padding:12px">暂无详细请求记录</div>`);
        return;
      }

      detailContent.insertAdjacentHTML('beforeend', `
          <div class="detail-requests-toggle" data-collapsed="true">
            <div class="detail-header">
              <span class="detail-model">${escHtml(model)}</span>
              <span class="detail-count">最近 ${requests.length} 条记录</span>
              <svg class="detail-chevron" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 6l4 4 4-4"/></svg>
            </div>
            <div class="detail-requests-body" style="display:none">
              <table class="detail-table">
                <thead>
                  <tr>
                    <th>请求ID</th><th>时间</th><th>类型</th><th>Input</th><th>Output</th>
                    <th>Cache Read</th><th>Cache Create</th><th>总Token</th><th>耗时</th><th>成本</th>
                  </tr>
                </thead>
                <tbody>${rows}</tbody>
              </table>
            </div>
          </div>`);

      const toggle = detailContent.querySelector('.detail-requests-toggle');
      const body = toggle.querySelector('.detail-requests-body');
      const chevron = toggle.querySelector('.detail-chevron');
      toggle.querySelector('.detail-header').addEventListener('click', () => {
        const collapsed = toggle.dataset.collapsed === 'true';
        toggle.dataset.collapsed = String(!collapsed);
        body.style.display = collapsed ? '' : 'none';
        chevron.style.transform = collapsed ? 'rotate(180deg)' : '';
      });
    })
    .catch(err => {
      const tr = document.createElement('tr');
      tr.className = 'model-detail-row';
      tr.innerHTML = `<td colspan="10" class="detail-content"></td>`;
      rowElement.after(tr);
      const detailContent = tr.querySelector('.detail-content');
      renderCostBar(model, detailContent);
      rowElement.classList.add('expanded');
      detailContent.insertAdjacentHTML('beforeend',
        `<div class="empty-state" style="padding:12px">加载失败: ${escHtml(err.message)}</div>`);
    });
}

function collapseModelRow(rowElement) {
  rowElement.classList.remove('expanded');
  const next = rowElement.nextSibling;
  if (next && next.classList && next.classList.contains('model-detail-row')) {
    next.remove();
  }
}
// ===== Sub-tab 切换 =====

function getActiveSubtab() {
  const activeBtn = document.querySelector('.sub-tab-btn.active');
  return activeBtn ? activeBtn.dataset.subtab : 'models';
}

function initSubTabs() {
  document.querySelectorAll('.sub-tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.sub-tab-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');

      const subtabName = btn.dataset.subtab;
      ['models', 'requests', 'upstream'].forEach(name => {
        const el = document.getElementById(`subtab-${name}`);
        if (el) el.style.display = name === subtabName ? '' : 'none';
      });

      const searchBox = document.getElementById('model-search-box');
      if (searchBox) searchBox.style.display = subtabName === 'models' ? '' : 'none';

      if (subtabName === 'requests') {
        requestFilters.period = window.currentPeriod || 'week';
        requestPagination.offset = 0;
        loadRequestLog();
      } else if (subtabName === 'upstream') {
        loadUpstreamStats();
      }
    });
  });
}

// ===== 请求日志 tab =====

async function loadRequestLog() {
  const period = requestFilters.period || 'week';
  const model = requestFilters.model || '';
  const requestType = requestFilters.requestType || '';
  const { limit, offset } = requestPagination;

  let url = `/api/token_stats/requests?period=${period}&limit=${limit}&offset=${offset}`;
  if (model) url += `&model=${encodeURIComponent(model)}`;
  if (requestType) url += `&source=${encodeURIComponent(requestType)}`;

  try {
    const data = await api(url);
    requestPagination.total = data.total || 0;
    renderRequestTable(data.requests || []);
    renderPagination(requestPagination.total, limit, offset);
  } catch (err) {
    const container = document.getElementById('request-log-container');
    if (container) {
      container.innerHTML = `<div class="empty-state">加载失败: ${escHtml(err.message)}</div>`;
    }
  }
}

function renderRequestTable(requests) {
  const subtabEl = document.getElementById('subtab-requests');
  if (!subtabEl) return;

  if (!document.getElementById('request-log-container')) {
    subtabEl.innerHTML = `
      <div class="filter-terminal">
        <div class="search-wrap">
          <input type="text" class="search-input" id="req-filter-model" placeholder="model..." value="${escHtml(requestFilters.model || '')}" />
        </div>
        <label>Type</label>
        ${customSelectHtml('req-filter-type', [
          { value: '', label: 'All', selected: !requestFilters.requestType },
          { value: 'proxy', label: 'Proxy', selected: requestFilters.requestType === 'proxy' },
          { value: 'opencode', label: 'OpenCode', selected: requestFilters.requestType === 'opencode' },
        ], 'All')}
        <button class="btn btn-secondary btn-sm" id="req-filter-clear">Clear</button>
      </div>
      <div id="request-log-container">
        <div class="table-card">
          <div class="table-header">
            <span class="table-title">请求日志</span>
            <span id="request-count" style="font-size:11px;color:hsl(var(--muted-foreground))"></span>
          </div>
          <div class="table-scroll">
            <table class="req-table" id="request-log-table">
              <thead>
                <tr>
                  <th>模型</th><th>请求ID</th><th>时间</th><th>类型</th><th>Input</th><th>Output</th>
                  <th>Cache Rd</th><th>Cache Wr</th><th>总Token</th><th>耗时</th><th>成本</th>
                </tr>
              </thead>
              <tbody></tbody>
            </table>
          </div>
        </div>
        <div id="request-pagination"></div>
      </div>`;
    setupRequestFilters();
  }

  const tbody = document.querySelector('#request-log-table tbody');
  const countEl = document.getElementById('request-count');
  if (!tbody) return;

  if (countEl) countEl.textContent = `${requestPagination.total} 条记录`;

  if (!requests.length) {
    tbody.innerHTML = '<tr><td colspan="11" class="empty-state">没有找到请求记录</td></tr>';
    return;
  }

  tbody.innerHTML = requests.map(r => {
    const typeAttr = r.upstream_id === 'opencode' ? 'opencode' : 'proxy';
    const badgeHtml = typeBadge(r.upstream_id);
    const timeStr = r.created_at || r.request_ts || r.timestamp || '-';
    const costStr = ((r.input_cost_cny || 0) + (r.output_cost_cny || 0) + (r.cache_read_cost_cny || 0) + (r.cache_write_cost_cny || 0)).toFixed(6);

    const tokenCells = `${tk(r.input_tokens)}
         ${tk(r.output_tokens)}
         ${tk(r.cache_read_tokens)}
         ${tk(r.cache_write_tokens)}
         ${tk((r.input_tokens||0)+(r.output_tokens||0)+(r.cache_read_tokens||0)+(r.cache_write_tokens||0), "cell-total")}
         <td class="cell-number">${r.duration_ms ? (r.duration_ms / 1000).toFixed(2) + 's' : '-'}</td>
         <td class="cell-cost"><span class="cost-badge">¥${costStr}</span></td>`;

    return `<tr data-type="${typeAttr}">
      <td class="model-name-text">${escHtml(r.model || '-')}</td>
      <td class="cell-detail-id">${escHtml(r.request_id || r.id || '-')}</td>
      <td class="cell-number">${escHtml(timeStr)}</td>
      <td>${badgeHtml}</td>
      ${tokenCells}
    </tr>`;
  }).join('');
}

function setupRequestFilters() {
  wireCustomSelect('req-filter-type');
  const modelInput = document.getElementById('req-filter-model');
  const typeSelect = document.getElementById('req-filter-type');
  const clearBtn = document.getElementById('req-filter-clear');

  if (modelInput) {
    modelInput.addEventListener('input', () => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        requestFilters.model = modelInput.value.trim();
        requestPagination.offset = 0;
        loadRequestLog();
      }, 300);
    });
  }

  if (typeSelect) {
    typeSelect.addEventListener('change', () => {
      requestFilters.requestType = typeSelect.value;
      requestPagination.offset = 0;
      loadRequestLog();
    });
  }

  if (clearBtn) {
    clearBtn.addEventListener('click', () => {
      requestFilters.model = '';
      requestFilters.requestType = '';
      requestPagination.offset = 0;
      const mi = document.getElementById('req-filter-model');
      if (mi) mi.value = '';
      const ts = document.getElementById('req-filter-type');
      if (ts) ts.value = '';
      updateCustomSelect('req-filter-type', [
        { value: '', label: 'All', selected: true },
        { value: 'proxy', label: 'Proxy' },
        { value: 'opencode', label: 'OpenCode' },
      ], 'All');
      loadRequestLog();
    });
  }
}

function renderPagination(total, limit, offset) {
  const container = document.getElementById('request-pagination');
  if (!container) return;

  const currentPage = Math.floor(offset / limit) + 1;
  const totalPages = Math.max(1, Math.ceil(total / limit));
  const hasNext = currentPage < totalPages;
  const hasPrev = currentPage > 1;

  // 生成页码按钮（最多 7 个）
  const maxVisible = 7;
  let pageNumbers = [];
  if (totalPages <= maxVisible) {
    for (let i = 1; i <= totalPages; i++) pageNumbers.push(i);
  } else {
    const half = Math.floor(maxVisible / 2);
    let start = Math.max(1, currentPage - half);
    let end = Math.min(totalPages, currentPage + half);
    if (start === 1) end = maxVisible;
    if (end === totalPages) start = totalPages - maxVisible + 1;
    for (let i = start; i <= end; i++) pageNumbers.push(i);
  }

  const pageBtns = pageNumbers.map(p =>
    `<button class="${p === currentPage ? 'active' : ''}" data-page="${p}">${p}</button>`
  ).join('');

  container.innerHTML = `
    <div class="pagination-terminal">
      <span class="info">${total.toLocaleString()} records · pg ${currentPage}/${totalPages}</span>
      <div class="btns">
        <button id="pagination-prev" ${hasPrev ? '' : 'disabled'}>‹ Prev</button>
        ${pageBtns}
        <button id="pagination-next" ${hasNext ? '' : 'disabled'}>Next ›</button>
      </div>
    </div>`;

  const prevBtn = document.getElementById('pagination-prev');
  const nextBtn = document.getElementById('pagination-next');

  if (prevBtn && hasPrev) {
    prevBtn.addEventListener('click', () => {
      requestPagination.offset = Math.max(0, offset - limit);
      loadRequestLog();
    });
  }

  if (nextBtn && hasNext) {
    nextBtn.addEventListener('click', () => {
      requestPagination.offset = offset + limit;
      loadRequestLog();
    });
  }

  // 页码点击
  container.querySelectorAll('[data-page]').forEach(btn => {
    btn.addEventListener('click', () => {
      const page = parseInt(btn.dataset.page, 10);
      requestPagination.offset = (page - 1) * limit;
      loadRequestLog();
    });
  });
}

// ===== 上游统计 tab =====

async function loadUpstreamStats() {
  const period = window.currentPeriod || 'week';
  try {
    const data = await api(`/api/token_stats/by_upstream?period=${period}`);
    upstreamStatsData = data.upstreams || [];
    renderUpstreamTable(upstreamStatsData);
  } catch (err) {
    const subtabEl = document.getElementById('subtab-upstream');
    if (subtabEl) {
      subtabEl.innerHTML = `<div class="table-card"><div class="empty-state">加载失败: ${escHtml(err.message)}</div></div>`;
    }
  }
}

function renderUpstreamTable(data) {
  const subtabEl = document.getElementById('subtab-upstream');
  if (!subtabEl) return;

  const sorted = [...data].sort((a, b) => (b.estimated_cost_cny || 0) - (a.estimated_cost_cny || 0));

  const headerBar = `<div class="table-card">
    <div class="table-header">
      <span class="table-title">上游统计</span>
      <span style="font-size:11px;color:hsl(var(--muted-foreground))">${sorted.length} upstreams</span>
    </div>
    <div class="table-scroll">
      <table class="up-table" id="upstream-stats-table">
        <thead>
          <tr>
            <th>上游</th><th>请求数</th><th>Input</th><th>Output</th>
            <th>Cache Rd</th><th>Cache Wr</th><th>总Token</th><th>成本</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
  </div>`;

  if (!document.getElementById('upstream-stats-table')) {
    subtabEl.innerHTML = headerBar;
  }

  const tbody = document.querySelector('#upstream-stats-table tbody');
  if (!tbody) return;

  if (!sorted.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty-state">暂无上游统计数据</td></tr>';
    return;
  }

  tbody.innerHTML = sorted.map(u => {
    const upstreamId = u.upstream_id || '';
    const isSpecial = upstreamId === '__unknown__' || upstreamId === '__opencode__';
    const rowClass = isSpecial ? ' class="is-unknown"' : '';
    const displayName = u.upstream_name || u.upstream_id || 'Unknown';
    const costStr = (u.estimated_cost_cny || 0).toFixed(6);

    return `<tr${rowClass}>
      <td>${escHtml(displayName)}</td>
      <td class="cell-number">${(u.request_count || 0).toLocaleString()}</td>
      ${tk(u.input_tokens)}
      ${tk(u.output_tokens)}
      ${tk(u.cache_read_tokens)}
      ${tk(u.cache_write_tokens)}
      <td class="cell-number">${((u.total_tokens || 0)/1e6).toFixed(2) + "M"}</td>
      <td class="cell-cost">¥${costStr}</td>
    </tr>`;
  }).join('');
}


// ===== Init Token Page Events =====
export function initTokenPage() {
  // Period buttons
  document.querySelectorAll('.period-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.period-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      window.currentPeriod = btn.dataset.period;
      loadTokenStats();
      const activeSubtab = getActiveSubtab();
      if (activeSubtab === 'requests') {
        requestFilters.period = window.currentPeriod || 'week';
        requestPagination.offset = 0;
        loadRequestLog();
      } else if (activeSubtab === 'upstream') {
        loadUpstreamStats();
      }
    });
  });

  // Refresh button
  const refreshBtn = document.getElementById('refresh-token');
  if (refreshBtn) refreshBtn.addEventListener('click', loadTokenStats);

  // Model search — 带 debounce 的完整重载（含 KPI + 趋势图 + 模型表）
  const modelSearch = document.getElementById('model-search');
  if (modelSearch) {
    let searchDebounce = null;
    modelSearch.addEventListener('input', () => {
      clearTimeout(searchDebounce);
      searchDebounce = setTimeout(() => {
        loadTokenStats(modelSearch.value.trim() || null);
      }, 300);
    });
  }

  // Legend click toggle
  document.querySelectorAll('.legend-item').forEach(item => {
    item.addEventListener('click', () => {
      const series = item.dataset.series;
      if (hiddenSeries.has(series)) {
        hiddenSeries.delete(series);
      } else {
        hiddenSeries.add(series);
      }
      item.classList.toggle('off', hiddenSeries.has(series));
      renderTrendChart(chartData);
    });
  });

  // Window resize for chart (150ms debounce)
  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (document.getElementById('page-tokens') && !document.getElementById('page-tokens').classList.contains('hidden')) {
        renderTrendChart(chartData);
      }
    }, 150);
  });

  // Sub-tab 切换
  initSubTabs();
}

// ===== Exports =====
export { loadTokenStats };
