## Task 1: Extract base.css

- Lines extracted: 633 lines (12693 bytes)
- Key selectors in base.css: :root (theme vars), [data-theme="light"], * (reset), body, .glass-card, .app, .top-nav, .nav-brand, .nav-brand-icon, .nav-tabs, .nav-tab, .nav-actions, .main-content, .toolbar, .toolbar-group, .toolbar-btn, #theme-toggle, .search-box, .badge, .badge-*, .progress-bar, .progress-segment, .btn, .btn-*, .modal-*, .form-*, .filter-pills, .filter-pill, .empty-state, .hidden, .settings-*, #settings-btn, .config-status-bar, @keyframes pulse-orange, #apply-config-btn.pulse-orange, .api-key-masked, .format-with-tooltip, @media (max-width: 768px)
- Page-specific CSS remaining in index.html: ~410 lines (KPI grid/cards/breakdown/cache, chart card/area chart/legend/tooltip, table card/table/scrollbar, fact store)
- Verification: `curl -s http://127.0.0.1:18742/css/base.css` returns 200 OK, 12693 bytes, Content-Type: text/css
- Server.py already handles .css extension mapping: `".css": "text/css"` at line 750
- No server restart needed for new CSS files (server reads from filesystem per request)

## Task 2: Extract core.js

- Functions extracted: 14 functions + 6 state variables + 2 constants + 1 event bus object = 23 exported symbols (24 export statements)
- Window mounts: 24 (all exported symbols mounted to window for inline script compatibility)
- Global state variables exported: currentPage, currentPeriod, allFacts, allModels, activeCategory, editingId, chartData, hiddenSeries
- Constants exported: catLabels, catIcons
- Functions exported: initTheme, toggleTheme, updateThemeButton, initSettings, applyDefaultPage, showSettings, saveDefaultPage, api, formatNumber, formatTokens, escHtml, showModal, closeModal
- Event bus exported: bus (with emit/on methods)
- core.js size: 171 lines, 5307 bytes
- index.html reduced from 1989 to 1868 lines (~121 lines removed)
- Inline script aliases (var) added for 8 state variables to prevent ReferenceError in remaining page-specific code
- Module script tag added: `<script type="module" src="js/core.js">` after base.css link
- All 333 tests still passing
- Key insight: `window.xxx = xxx` in ES modules makes functions accessible as bare names from non-module inline scripts (window properties ARE in the global scope chain)
- Key concern: state variables exported as `let` are captured by value at module init time — `window.xxx = xxx` is NOT a live reference. Use Object.defineProperty getter/setter for live references if needed

## Task 3: Fix CRITICAL timing bug — module loads after inline script

- **Problem**: `<script type="module">` is deferred → executes AFTER inline `<script>`. So `loadFacts()` at parse time crashes (ReferenceError: api is not defined). Same for `bus.on()` — stub bus registrations are no-ops.
- **Fix 1**: Removed `<script type="module" src="js/core.js">` from `<head>`. core.js module will be loaded by app.js in Task 7.
- **Fix 2**: Added fallback function stubs for 7 critical exports (api, formatNumber, formatTokens, escHtml, showModal, closeModal, bus) using `var fn = window.fn || function() { stub }` pattern at top of inline script.
- **Fix 3**: Wrapped all `bus.on()` registrations in `registerModelEvents()` polling function (retries every 50ms until `window.bus` with `.on()` is available).
- **Fix 4**: Changed `loadFacts()` from synchronous call to deferred: checks `window.api`, if available calls directly, otherwise waits for DOMContentLoaded.
- **Key insight**: In classic (non-module) scripts, `window.foo = val` IS accessible as bare `foo` through the global Object Environment Record. So `toggleTheme`, `showSettings`, `initSettings` inside DOMContentLoaded handler work without var aliases.
- **Key insight**: The bus stub's `emit()` dispatches CustomEvents on document. The real bus's `on()` uses `document.addEventListener`. So even with stub bus, `bus.emit()` in functions (saveUpstream, etc.) triggers handlers registered via `window.bus.on()`.

## Task 4: Fix two remaining runtime errors

- **Bug 1 — toggleTheme is not defined**: DOMContentLoaded callback references `toggleTheme`, `showSettings`, `saveDefaultPage`, `initSettings` as bare names. Added `var fn = window.fn || function(){}` stubs for all 4.
- **Bug 2 — api stub called at parse time**: The `if (window.api) { loadFacts() }` check was fooled by `var api = window.api || stub` (stub is truthy). So `loadFacts()` ran immediately with the stub, rejecting with "Module not loaded".
- **Fix for Bug 2**: Replaced the `if (window.api)` check with `setTimeout` polling every 20ms until `typeof window.api === 'function'`, then calls `loadFacts()`. This ensures the module has set `window.api` to the real function.
- **Key insight**: `var api = window.api || stub` creates `window.api = stub` (same property). The module later does `window.api = realFunc`, updating same property. So `api` as bare name resolves to real function after module loads — BUT only if code executes AFTER module loads (setTimeout ensures this).

## Task 5: Fix api stub poll loop never completes

- **Root cause**: `var api = window.api || function() { return Promise.reject(...) }` creates `window.api = stub` (a function). So `typeof window.api === 'function'` is ALWAYS true, even before core.js loads. The boot poll immediately calls `loadFacts()` using the stub → "Module not loaded" error.
- **Fix**: Removed fallback stubs from 7 module function aliases (`api`, `formatNumber`, `formatTokens`, `escHtml`, `showModal`, `closeModal`, `bus`). Changed from `var x = window.x || stub` to `var x = window.x` (undefined until module loads).
- **Why safe**: These vars are only accessed inside functions that execute AFTER module load (user clicks, fetch responses, DOMContentLoaded). At parse time they're `undefined`, but no synchronous code dereferences them. Module sets `window.x = realFunc` before any code reads them.
- **Key insight**: `var x = window.x` at global scope IS `window.x` (Object Environment Record binding). Module's `window.x = realFunc` updates the same property, so all future `x` references resolve to the real function.
## Task 7 Complete — app.js Integration

### Changes Made
1. **core.js**: 
   - Removed `initTheme()` module-level call (line 36 → removed)
   - Added `pageLoaders` export (callback registry pattern)
   - Updated `applyDefaultPage()` to use `pageLoaders` instead of `window.loadFacts`
   
2. **facts.js**: Removed module-level `initFactPage()` call; added `initFactPage` to exports
3. **tokens.js**: Removed module-level `initTokenPage()` call; added `initTokenPage` to exports  
4. **models.js**: Removed module-level `initModelPage()` call
5. **app.js** (new): Single entry module — registers pageLoaders, inits pages, sets up bus events, and runs DOMContentLoaded
6. **index.html**: Stripped all 4 head script tags + entire inline `<script>` block (120 lines removed), replaced with single `<script type="module" src="js/app.js">`

### Architecture Decisions
- **pageLoaders registry**: core.js's `applyDefaultPage()` needs to call `loadFacts()`/`loadTokenStats()`, but can't import from page modules (cross-page import rule). Solution: callback registry pattern where app.js registers the loaders.
- **`window.currentPage`**: Used for nav tab handler reassignment since ES module imports are live read-only views that can't be reassigned from app.js.
- **Preserved bus events**: `config:upstream-changed`, `config:model-changed`, `config:route-changed` listeners preserved in app.js.

### Verification
- 333 tests pass (unchanged)
- LSP: 0 errors, 3 pre-existing hints (tokens.js unused vars)
- Module graph: app.js → core.js + facts/tokens/models → no circular imports
