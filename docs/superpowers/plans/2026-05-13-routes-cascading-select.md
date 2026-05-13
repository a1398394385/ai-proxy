# 路由映射页级联选择实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将路由映射页的目标模型选择从 optgroup 分组下拉框改为先选上游再选模型的级联选择

**架构:** 仅修改 `static/js/pages/routes.js`，抽取 `buildCascadingModelSelect()` 返回 HTML 字符串，`showModal()` 返回后绑定事件。不修改后端。

**技术栈:** ES Module (Vanilla JS)，无构建工具

---

### Task 1: 新增 `buildCascadingModelSelect()` 辅助函数

**文件:**
- 修改: `static/js/pages/routes.js`

在 `showRouteModal` 定义之前（约第 33 行）插入以下函数。该函数仅生成 HTML 字符串，不含事件绑定。

- [ ] **Step 1: 插入辅助函数**

```javascript
// ─── 级联选择：上游 → 模型 ───
function buildCascadingModelSelect(upstreams, models, selectedUpstreamId, selectedModelId) {
  const fmtLabel = { responses: 'Resp', messages: 'Msg', chat_completions: 'Chat' };
  let upOpts = '<option value="">-- 选择上游 --</option>';
  upstreams.upstreams.forEach(u => {
    const fmt = fmtLabel[u.format] || u.format;
    const label = escHtml(u.name) + (fmt ? ' (' + fmt + ')' : '');
    upOpts += `<option value="${u.id}"${u.id === selectedUpstreamId ? ' selected' : ''}${u.is_active ? '' : ' disabled'}>${label}</option>`;
  });
  let modelOpts, modelDisabled = 'disabled';
  if (!selectedUpstreamId) {
    modelOpts = '<option value="">-- 请先选择上游 --</option>';
  } else if (!upstreams.upstreams.find(u => u.id === selectedUpstreamId)) {
    modelOpts = '<option value="">上游不存在或已变更</option>';
  } else {
    const mlist = models.models.filter(m => m.upstream_id === selectedUpstreamId);
    if (mlist.length === 0) {
      modelOpts = '<option value="">暂无模型</option>';
    } else {
      modelDisabled = '';
      modelOpts = mlist.map(m => `<option value="${m.id}"${m.id === selectedModelId ? ' selected' : ''}>${escHtml(m.name)}</option>`).join('');
    }
  }
  return `
    <div class="form-group"><label class="form-label">上游</label><select class="form-input" id="r-upstream">${upOpts}</select></div>
    <div class="form-group"><label class="form-label">目标模型</label><select class="form-input" id="r-target" ${modelDisabled}>${modelOpts}</select></div>`;
}
```

---

### Task 2: 重构 `showRouteModal()`

**文件:**
- 修改: `static/js/pages/routes.js` (第 33-68 行)

- [ ] **Step 1: 替换整个函数体**

新函数逻辑：
1. `Promise.all` 加载 routes + models + upstreams
2. 如果是编辑，从 models 中通过 `target_model_id` 找到模型的 `upstream_id`
3. 调用 `buildCascadingModelSelect()` 生成 HTML
4. `showModal()` 注入 DOM
5. 立即绑定 `#r-upstream` 的 change 事件

```javascript
async function showRouteModal(editId) {
  let data = { source: '', target_model_id: '', request_type: currentRequestType };
  let title = '新增路由';
  let routeUpstreamId = null;
  let routeModelId = null;
  try {
    if (editId) {
      title = '编辑路由 #' + editId;
      const routes = await api('/api/routes');
      const found = routes.routes.find(r => r.id === editId);
      if (found) data = found;
    }
    const [models, upstreams] = await Promise.all([api('/api/models'), api('/api/upstreams')]);
    if (editId && data.target_model_id) {
      routeModelId = data.target_model_id;
      const tm = models.models.find(m => m.id === data.target_model_id);
      if (tm) routeUpstreamId = tm.upstream_id;
    }
  } catch (_) {
    alert('加载数据失败，请检查服务是否正常运行');
    return;
  }
  const cascadingHtml = buildCascadingModelSelect(upstreams, models, routeUpstreamId, routeModelId);
  const requestTypeOptions = ['responses', 'messages', 'chat_completions']
    .map(rt => `<option value="${rt}" ${data.request_type === rt ? 'selected' : ''}>${rt}</option>`)
    .join('');
  const sourceField = data.source === '*'
    ? `<input type="text" class="form-input" value="* (fallback)" readonly style="background:hsl(var(--muted));color:hsl(var(--muted-foreground));cursor:not-allowed"><input type="hidden" id="r-source" value="*">`
    : `<input type="text" class="form-input" id="r-source" value="${escHtml(data.source)}" placeholder="如 gpt-4o">`;
  showModal(title,
    `<div class="form-group"><label class="form-label">源模型名</label>${sourceField}</div>
     ${cascadingHtml}
     <input type="hidden" id="r-proxy" value="${escHtml(data.request_type)}">`,
    `<button class="btn btn-secondary" onclick="closeModal()">取消</button><button class="btn btn-primary" onclick="saveRoute(${editId || 0})">保存</button>`);
  bindCascadeModelSelect();
}
```

- [ ] **Step 2: 添加 `bindCascadeModelSelect()` 事件绑定函数**

在 `buildCascadingModelSelect` 后/前（总之在 Task 1 的函数附近）插入：

```javascript
function bindCascadeModelSelect() {
  const upSelect = document.getElementById('r-upstream');
  const modelSelect = document.getElementById('r-target');
  if (!upSelect || !modelSelect) return;
  upSelect.addEventListener('change', async () => {
    const upstreamId = upSelect.value;
    if (!upstreamId) {
      modelSelect.innerHTML = '<option value="">-- 请先选择上游 --</option>';
      modelSelect.disabled = true;
      return;
    }
    modelSelect.disabled = true;
    modelSelect.innerHTML = '<option value="">加载中…</option>';
    try {
      const data = await api('/api/models?upstream_id=' + encodeURIComponent(upstreamId));
      const mlist = data.models || [];
      modelSelect.innerHTML = mlist.length === 0
        ? '<option value="">暂无模型</option>'
        : mlist.map(m => `<option value="${m.id}">${escHtml(m.name)}</option>`).join('');
      modelSelect.disabled = false;
    } catch (_) {
      modelSelect.innerHTML = '<option value="">加载失败</option>';
    }
  });
}
```

---

### Task 3: 重构 `showFallbackModal()`

**文件:**
- 修改: `static/js/pages/routes.js` (第 70-94 行)

- [ ] **Step 1: 替换整个函数体**

```javascript
async function showFallbackModal() {
  const data = { source: '*', target_model_id: '', request_type: currentRequestType };
  let models, upstreams;
  try {
    [models, upstreams] = await Promise.all([api('/api/models'), api('/api/upstreams')]);
  } catch (_) {
    alert('加载数据失败，请检查服务是否正常运行');
    return;
  }
  const cascadingHtml = buildCascadingModelSelect(upstreams, models, null, null);
  showModal('新增回退路由',
    `<div class="form-group"><label class="form-label">源模型名</label><input type="text" class="form-input" value="* (fallback)" readonly style="background:hsl(var(--muted));color:hsl(var(--muted-foreground));cursor:not-allowed"></div>
     <input type="hidden" id="r-source" value="*">
     ${cascadingHtml}
     <input type="hidden" id="r-proxy" value="${escHtml(data.request_type)}">`,
    `<button class="btn btn-secondary" onclick="closeModal()">取消</button><button class="btn btn-primary" onclick="saveRoute(0, true)">保存</button>`);
  bindCascadeModelSelect();
}
```

---

### Task 4: 清理旧代码 & 语法验证

- [ ] **Step 1: 确认移除的旧代码**

确认 `showRouteModal` 和 `showFallbackModal` 中不再包含以下被替换的代码：
- `const fmtLabel = { responses: 'Resp', messages: 'Msg', chat_completions: 'Chat' };`
- `const upFmt = Object.fromEntries(upstreams.upstreams.map(u => [u.id, fmtLabel[u.format] || u.format]));`
- `const byUpstream = {}; models.models.forEach(...)` 和 `for (const [upstream, mlist] of Object.entries(byUpstream))` 循环
- `let modelOpts = '';` 及其构建逻辑
- `<div class="form-group"><label class="form-label">目标模型</label><select class="form-input" id="r-target">${modelOpts}</select></div>`

- [ ] **Step 2: 运行现有测试确认无回归**

Run: `python3 -m pytest test/ -q`
Expected: 所有 531+ 测试通过

---

### Task 5: 功能验证（Playwright 或手动）

- [ ] **Step 1: 重启服务**

```bash
./server.sh restart
```

- [ ] **Step 2: 新增路由验证**

路由映射 Tab → 点击「+ 新增路由」：
- 两个选择框「上游」和「目标模型」
- 上游框列出所有上游（带 `(Chat)` `(Resp)` `(Msg)` 标签）
- 未选上游时，模型框 disabled + 「请先选择上游」
- 选择上游 → 模型框加载该上游模型，变为可选
- 切换上游 → 模型框清空并重新加载

- [ ] **Step 3: 编辑路由验证**

点击某路由「编辑」：
- 上游框自动选中
- 模型框自动加载并选中当前模型
- 修改上游 → 模型框切换
- 保存正常

- [ ] **Step 4: 回退路由验证**

「+ 新增回退路由」同上，级联选择和保存正常。

- [ ] **Step 5: 最终验证**

Run: `python3 -m pytest test/ -q`
Expected: 全部通过
