import { api } from '../core.js';

// ===== Page Init =====
export function loadDbQuery() {
  // 快捷查询按钮：点击只填入 SQL，不自动执行
  document.querySelectorAll('.quick-query-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.getElementById('sql-editor').value = btn.dataset.sql;
    });
  });

  // 执行查询按钮
  document.getElementById('execute-sql').addEventListener('click', executeQuery);
}

// ===== Execute Query =====
async function executeQuery() {
  const sql = document.getElementById('sql-editor').value.trim();
  if (!sql) return;

  const btn = document.getElementById('execute-sql');
  const resultDiv = document.getElementById('query-result');

  // 加载状态
  const originalText = btn.innerHTML;
  btn.innerHTML = '<span>⏳</span> 执行中...';
  btn.disabled = true;

  try {
    const data = await api('/api/db/query', {
      method: 'POST',
      body: JSON.stringify({ sql })
    });

    if (data.error) {
      renderError(resultDiv, data.error);
    } else {
      renderTable(resultDiv, data.columns, data.rows);
    }
  } catch (err) {
    renderError(resultDiv, '请求失败: ' + err.message);
  } finally {
    btn.innerHTML = originalText;
    btn.disabled = false;
  }
}

// ===== Render Results =====
function renderTable(container, columns, rows) {
  container.innerHTML = '';  // 清空

  if (!rows || rows.length === 0) {
    container.innerHTML = '<div class="empty-state"><div class="empty-state-icon">📭</div><p>无结果</p></div>';
    return;
  }

  const wrapper = document.createElement('div');
  wrapper.style.overflowX = 'auto';

  const table = document.createElement('table');
  table.className = 'dbquery-table';

  // 表头
  const thead = document.createElement('thead');
  const headerRow = document.createElement('tr');
  columns.forEach(col => {
    const th = document.createElement('th');
    th.textContent = col;
    headerRow.appendChild(th);
  });
  thead.appendChild(headerRow);
  table.appendChild(thead);

  // 表体
  const tbody = document.createElement('tbody');
  rows.forEach(row => {
    const tr = document.createElement('tr');
    row.forEach(cell => {
      const td = document.createElement('td');
      td.textContent = cell !== null && cell !== undefined ? String(cell) : '';
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);

  wrapper.appendChild(table);
  container.appendChild(wrapper);
}

// ===== Render Error =====
function renderError(container, message) {
  container.innerHTML = `<div class="dbquery-error">⚠ ${message}</div>`;
}

// ===== Global Scope Mounting =====
window.loadDbQuery = loadDbQuery;
