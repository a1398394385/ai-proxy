# Task 6 - Models Page Extraction 完成

## 完成内容
- Created `static/css/models.css` — table card/table/th/td styles (extracted from inline `<style>`)
- Created `static/js/pages/models.js` — all 19 model CRUD functions as ES module
- Updated `static/index.html` (763→399 行):
  - Added `<link>` for models.css + `<script>` for models.js
  - Removed inline `<style>` block
  - Removed all extracted JS functions
  - Nav click: `loadModelConfig()` → `window.loadModelConfig()`
  - `registerModelEvents()` uses `window.refreshUpstreamDropdown` / `window.refreshConfigStatus`
  - `model-filter-upstream` change listener moved to models.js `initModelPage()`
- 333 tests passing, zero diagnostics on new files

## 注意事项
- Config-specific CSS (`.config-status-bar`, `@keyframes pulse-orange`, `.api-key-masked`, `.format-with-tooltip`) 已在之前的 Task 中被移入 base.css，本次未重复提取
- `registerModelEvents()` 留在 inline script 中，Task 7 将处理
- `model-filter-upstream` change listener 移到了 models.js 的 `initModelPage()` 中
