import { api, formatNumber, formatTokens } from '../core.js';

// ===== Module-local state =====
let allModels = [];
let chartData = [];
let hiddenSeries = new Set();

// ===== Token 统计 =====
async function loadTokenStats() {
  const period = window.currentPeriod || 'week';
  const [stats, byModel, trend] = await Promise.all([
    api(`/api/token_stats?period=${period}`),
    api(`/api/token_stats/by_model?period=${period}`),
    api(`/api/token_stats/trend?period=${period}`)
  ]);
  
  allModels = byModel.models || [];
  
  const periodLabels = { day: '24小时', week: '7天', month: '30天' };
  document.getElementById('chart-period-label').textContent = periodLabels[period] || '7天';
  
  renderKPI(stats);
  renderTrendChart(trend.trends);
  renderModelTable(allModels);
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
    
    <div class="kpi-card">
      <div class="kpi-header">
        <span class="kpi-label">总 Tokens</span>
        <div class="kpi-icon" style="background:hsl(var(--primary)/0.15);color:hsl(var(--primary))">Σ</div>
      </div>
      <div class="kpi-value" style="font-size: 32px; margin-bottom: 8px;">${(stats.total_tokens || 0).toLocaleString()}</div>
      <div class="kpi-breakdown">
        <div class="kpi-breakdown-item">
          <span class="kpi-breakdown-dot blue"></span>
          <span class="kpi-breakdown-label">Input</span>
          <span class="kpi-breakdown-value blue">${formatNumber(stats.input_tokens)}</span>
        </div>
        <div class="kpi-breakdown-item">
          <span class="kpi-breakdown-dot green"></span>
          <span class="kpi-breakdown-label">Output</span>
          <span class="kpi-breakdown-value green">${formatNumber(stats.output_tokens)}</span>
        </div>
      </div>
    </div>
    
    <div class="kpi-card">
      <div class="kpi-header">
        <span class="kpi-label">Cache</span>
        <div class="kpi-icon orange">⚡</div>
      </div>
      <div class="kpi-cache-list">
        <div class="kpi-cache-item">
          <span class="kpi-cache-label">Read</span>
          <span class="kpi-cache-value orange">${formatNumber(stats.cache_read_tokens)}</span>
        </div>
        <div class="kpi-cache-item">
          <span class="kpi-cache-label">Create</span>
          <span class="kpi-cache-value purple">${formatNumber(stats.cache_write_tokens)}</span>
        </div>
      </div>
    </div>
    
    <div class="kpi-card">
      <div class="kpi-header">
        <span class="kpi-label">估算成本</span>
        <div class="kpi-icon red">$</div>
      </div>
      <div class="kpi-value red">$${(stats.estimated_cost_usd || 0).toFixed(4)}</div>
      <div class="kpi-sub">USD</div>
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
  
  function niceMax(max, ticks = 5) {
    if (max === 0) return 1;
    if (max < 10) return 10;
    const exponent = Math.floor(Math.log10(max));
    const fraction = max / Math.pow(10, exponent);
    let niceFraction;
    if (fraction <= 1.2) niceFraction = 1.2;
    else if (fraction <= 1.5) niceFraction = 1.5;
    else if (fraction <= 2) niceFraction = 2;
    else if (fraction <= 3) niceFraction = 3;
    else if (fraction <= 5) niceFraction = 5;
    else if (fraction <= 7) niceFraction = 7;
    else niceFraction = 10;
    return niceFraction * Math.pow(10, exponent);
  }

  const maxIndividual = Math.max(
    ...chartData.map(d => Math.max(
      hiddenSeries.has('inputTokens')    ? 0 : (d.input_tokens      || 0),
      hiddenSeries.has('outputTokens')   ? 0 : (d.output_tokens     || 0),
      hiddenSeries.has('cacheReadTokens')  ? 0 : (d.cache_read_tokens  || 0),
      hiddenSeries.has('cacheWriteTokens') ? 0 : (d.cache_write_tokens || 0)
    )),
    1
  );
  const yMax = niceMax(maxIndividual);
  const yTicks = 5;
  
  const gridGroup = document.getElementById('chart-grid');
  gridGroup.innerHTML = '';
  for (let i = 0; i <= yTicks; i++) {
    const y = margin.top + (chartHeight * i / yTicks);
    const value = Math.round(yMax * (1 - i / yTicks));
    
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    line.setAttribute('x1', margin.left);
    line.setAttribute('y1', y);
    line.setAttribute('x2', width - margin.right);
    line.setAttribute('y2', y);
    line.setAttribute('class', 'area-chart-grid');
    gridGroup.appendChild(line);
  }
  
  const axesGroup = document.getElementById('chart-axes');
  axesGroup.innerHTML = '';
  
  function formatAxisValue(value) {
    if (value >= 1000000) return (value / 1000000).toFixed(1) + 'M';
    if (value >= 1000) return (value / 1000).toFixed(0) + 'k';
    return value.toString();
  }
  
  for (let i = 0; i <= yTicks; i++) {
    const y = margin.top + (chartHeight * i / yTicks);
    const value = Math.round(yMax * (1 - i / yTicks));
    
    const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    text.setAttribute('x', margin.left - 10);
    text.setAttribute('y', y + 4);
    text.setAttribute('text-anchor', 'end');
    text.setAttribute('class', 'area-chart-tick');
    text.textContent = formatAxisValue(value);
    axesGroup.appendChild(text);
  }

  const costValues = chartData.map(d => d.estimated_cost_usd || 0);
  const costYMax = niceMax(Math.max(...costValues, 0.0001));

  function formatCostAxis(v) {
    if (v === 0) return '$0';
    if (v >= 10)  return '$' + v.toFixed(1);
    if (v >= 1)   return '$' + v.toFixed(2);
    if (v >= 0.1) return '$' + v.toFixed(3);
    return '$' + v.toFixed(4);
  }

  const rightLine = document.createElementNS('http://www.w3.org/2000/svg', 'line');
  rightLine.setAttribute('x1', width - margin.right);
  rightLine.setAttribute('y1', margin.top);
  rightLine.setAttribute('x2', width - margin.right);
  rightLine.setAttribute('y2', margin.top + chartHeight);
  rightLine.setAttribute('class', 'area-chart-axis');
  axesGroup.appendChild(rightLine);

  for (let i = 0; i <= yTicks; i++) {
    const y = margin.top + (chartHeight * i / yTicks);
    const value = costYMax * (1 - i / yTicks);
    const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    t.setAttribute('x', width - margin.right + 8);
    t.setAttribute('y', y + 4);
    t.setAttribute('text-anchor', 'start');
    t.setAttribute('class', 'area-chart-tick');
    t.setAttribute('fill', '#f43f5e');
    t.textContent = formatCostAxis(value);
    axesGroup.appendChild(t);
  }

  const xStep = chartWidth / (chartData.length - 1 || 1);
  const dataCount = chartData.length;
  
  let labelInterval;
  let labelFormatter;
  
  if (dataCount === 24) {
    labelInterval = 1;
    labelFormatter = (d, i) => {
      const parts = d.date.split(' ');
      if (parts.length === 2) {
        return parts[1];
      }
      return d.date;
    };
  } else if (dataCount === 7) {
    labelInterval = 1;
    labelFormatter = (d) => d.date.slice(5);
  } else if (dataCount === 30) {
    labelInterval = 5;
    labelFormatter = (d) => d.date.slice(5);
  } else {
    labelInterval = Math.ceil(dataCount / 7);
    labelFormatter = (d) => d.date.slice(5);
  }
  
  chartData.forEach((d, i) => {
    if (dataCount === 24) {
      if (i % labelInterval !== 0 && i !== dataCount - 1) {
        return;
      }
    }
    
    if (dataCount !== 24 && i % labelInterval !== 0 && i !== dataCount - 1) {
      return;
    }
    
    const x = margin.left + i * xStep;
    const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    
    let textAnchor = 'middle';
    let labelX = x;
    if (i === dataCount - 1) {
      labelX = Math.min(x, width - margin.right - 5);
      textAnchor = 'end';
    } else if (i === 0) {
      textAnchor = 'start';
      labelX = Math.max(x, margin.left + 5);
    }
    
    text.setAttribute('x', labelX);
    text.setAttribute('y', height - 10);
    text.setAttribute('text-anchor', textAnchor);
    text.setAttribute('class', 'area-chart-tick');
    const label = labelFormatter(d, i);
    if (label) {
      text.textContent = label;
      axesGroup.appendChild(text);
    }
  });
  
  const areasGroup = document.getElementById('chart-areas');
  areasGroup.innerHTML = '';

  const series = [
    { key: 'inputTokens',    label: '输入 Tokens', color: '#3b82f6', gradient: 'url(#gradientInput)',      class: 'area-path-input',       rawKey: 'input_tokens'       },
    { key: 'outputTokens',   label: '输出 Tokens', color: '#22c55e', gradient: 'url(#gradientOutput)',     class: 'area-path-output',      rawKey: 'output_tokens'      },
    { key: 'cacheReadTokens',  label: '缓存读取',  color: '#a855f7', gradient: 'url(#gradientCacheRead)',  class: 'area-path-cache-read',  rawKey: 'cache_read_tokens'  },
    { key: 'cacheWriteTokens', label: '缓存写入',  color: '#f97316', gradient: 'url(#gradientCacheWrite)', class: 'area-path-cache-write', rawKey: 'cache_write_tokens' }
  ];

  const chartBottom = margin.top + chartHeight;

  series.forEach((s) => {
    if (hiddenSeries.has(s.key)) return;

    const points = chartData.map((d, i) => {
      const x = margin.left + i * xStep;
      const value = d[s.rawKey] || 0;
      const y = margin.top + chartHeight * (1 - value / yMax);
      return { x, y };
    });

    let pathD = '';
    points.forEach((p, i) => {
      if (i === 0) {
        pathD += `M ${p.x} ${p.y}`;
      } else {
        const prev = points[i - 1];
        const cpX = (prev.x + p.x) / 2;
        pathD += ` C ${cpX} ${prev.y}, ${cpX} ${p.y}, ${p.x} ${p.y}`;
      }
    });

    const last = points[points.length - 1];
    pathD += ` L ${last.x} ${chartBottom} L ${points[0].x} ${chartBottom} Z`;

    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', pathD);
    path.setAttribute('class', s.class);
    path.setAttribute('fill', s.gradient);
    areasGroup.appendChild(path);
  });

  if (!hiddenSeries.has('costLine')) {
    const costPoints = chartData.map((d, i) => ({
      x: margin.left + i * xStep,
      y: margin.top + chartHeight * (1 - (d.estimated_cost_usd || 0) / costYMax)
    }));
    let costD = '';
    costPoints.forEach((p, i) => {
      if (i === 0) {
        costD += `M ${p.x} ${p.y}`;
      } else {
        const prev = costPoints[i - 1];
        const cpX = (prev.x + p.x) / 2;
        costD += ` C ${cpX} ${prev.y}, ${cpX} ${p.y}, ${p.x} ${p.y}`;
      }
    });
    const costPath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    costPath.setAttribute('d', costD);
    costPath.setAttribute('class', 'area-path-cost');
    areasGroup.appendChild(costPath);
  }

  document.querySelectorAll('.legend-item').forEach(item => {
    item.classList.toggle('hidden', hiddenSeries.has(item.dataset.series));
  });
  
  const overlay = document.getElementById('chart-overlay');
  const tooltip = document.getElementById('chart-tooltip');
  const cursorLine = document.getElementById('chart-cursor-line');
  
  overlay.onmousemove = (e) => {
    const rect = svg.getBoundingClientRect();
    const x = e.clientX - rect.left - margin.left;
    const index = Math.round(x / xStep);
    
    if (index >= 0 && index < chartData.length) {
      const d = chartData[index];
      const pointX = margin.left + index * xStep;
      cursorLine.setAttribute('x1', pointX);
      cursorLine.setAttribute('x2', pointX);
      cursorLine.style.display = 'block';
      showTooltip(e.clientX, e.clientY, d);
    }
  };
  
  overlay.onmouseleave = () => {
    tooltip.classList.remove('show');
    cursorLine.style.display = 'none';
  };
}

function showTooltip(mouseX, mouseY, data) {
  const tooltip = document.getElementById('chart-tooltip');
  const title = document.getElementById('tooltip-title');
  const content = document.getElementById('tooltip-content');
  
  const dataCount = chartData.length;
  if (dataCount === 24) {
    title.textContent = data.date;
  } else if (dataCount === 7 || dataCount === 30) {
    title.textContent = data.date;
  } else {
    title.textContent = data.date;
  }
  
  const items = [
    { label: '输入 Tokens', value: data.input_tokens, color: '#3b82f6', key: 'inputTokens' },
    { label: '输出 Tokens', value: data.output_tokens, color: '#22c55e', key: 'outputTokens' },
    { label: '缓存读取', value: data.cache_read_tokens, color: '#a855f7', key: 'cacheReadTokens' },
    { label: '缓存写入', value: data.cache_write_tokens, color: '#f97316', key: 'cacheWriteTokens' },
    { label: '成本', value: '$' + data.estimated_cost_usd.toFixed(4), color: '#f43f5e', bold: true }
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

function renderModelTable(models) {
  const filter = document.getElementById('model-search').value.toLowerCase();
  const filtered = filter ? models.filter(m => m.model.toLowerCase().includes(filter)) : models;
  
  document.getElementById('model-count').textContent = `${filtered.length} 个模型`;
  
  const totalTokens = filtered.reduce((sum, m) => sum + m.total_tokens, 0) || 1;
  const tbody = document.querySelector('#model-table tbody');
  
  if (!filtered.length) {
    tbody.innerHTML = '<tr><td colspan="9" class="empty-state">没有找到模型</td></tr>';
    return;
  }
  
  tbody.innerHTML = filtered.map(m => {
    const pct = ((m.total_tokens / totalTokens) * 100).toFixed(1);
    return `<tr>
      <td><span class="badge badge-blue">${m.model}</span></td>
      <td>${(m.request_count || 0).toLocaleString()}</td>
      <td>${formatTokens(m.input_tokens)}</td>
      <td>${formatTokens(m.output_tokens)}</td>
      <td>${formatTokens(m.cache_read_tokens)}</td>
      <td>${formatTokens(m.cache_write_tokens)}</td>
      <td><b>${formatTokens(m.total_tokens)}</b></td>
      <td style="min-width:120px">
        ${pct}%
        <div class="progress-bar">
          <div class="progress-segment input" style="width:${(m.input_tokens/totalTokens*100)}%"></div>
          <div class="progress-segment output" style="width:${(m.output_tokens/totalTokens*100)}%"></div>
          <div class="progress-segment cache-read" style="width:${(m.cache_read_tokens/totalTokens*100)}%"></div>
          <div class="progress-segment cache-write" style="width:${(m.cache_write_tokens/totalTokens*100)}%"></div>
        </div>
      </td>
      <td style="color:hsl(var(--red));font-weight:500">$${m.estimated_cost_usd.toFixed(4)}</td>
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
    });
  });
  
  // Refresh button
  const refreshBtn = document.getElementById('refresh-token');
  if (refreshBtn) refreshBtn.addEventListener('click', loadTokenStats);
  
  // Model search
  const modelSearch = document.getElementById('model-search');
  if (modelSearch) {
    modelSearch.addEventListener('input', () => {
      renderModelTable(allModels);
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
      item.classList.toggle('hidden', hiddenSeries.has(series));
      renderTrendChart(chartData);
    });
  });
  
  // Window resize for chart
  window.addEventListener('resize', () => {
    if (document.getElementById('page-tokens') && !document.getElementById('page-tokens').classList.contains('hidden')) {
      renderTrendChart(chartData);
    }
  });
}

// ===== Exports =====
export { loadTokenStats, renderKPI, renderTrendChart, renderModelTable, allModels, chartData, hiddenSeries };

// ===== Global Scope Mounting =====
window.loadTokenStats = loadTokenStats;
window.renderModelTable = renderModelTable;
