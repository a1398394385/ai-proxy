## 2026-04-30T06:49:29Z Session Start

### Active Plan
- Plan: models-routes-refactor
- Tasks: 13 implementation + 4 verification
- Critical Path: Task 1 → Task 2 → Task 4 → Task 5 → Task 7 → F1-F4

### Key Decisions
- 3 independent proxy routes: codex / claude / pass_through
- Existing route data defaults to 'codex'
- Apply Config button promoted to nav bar
- SQLite rebuild-table approach for schema migration (no DROP CONSTRAINT support)
- PUT validation fix included in Task 2

### File Map
- Backend: config_manager.py (Migrations + ConfigDB + ConfigCache), common.py (resolve_model), server.py (API), proxy.py, pass_through.py
- Frontend: index.html, models.js → upstreams.js + routes.js, app.js, core.js
- Tests: test_config_manager.py, test_config_integration.py
- CSS: new routes.css, modified models.css

### Task 1 Complete - Migrations class (2026-04-30)
- Added `import shutil` + `from datetime import datetime` to config_manager.py
- Migrations class inserted between ConfigDB (line 455) and ConfigCache (line 458)
- 6-step migration: status check → backup (shutil.copy2) → CREATE new table → INSERT data → DROP+ RENAME → UPSERT version → verify count
- All steps wrapped in single transaction with rollback on failure
- Backup pattern: `config.db.bak.{YYYYMMDDHHmmss}` in same directory
- proxy_type defaults to 'codex'; UNIQUE(source, proxy_type) constraint added
- _ensure_db() migration check is non-blocking (logs warning, swallows exceptions)
- 348 tests passing, verified idempotent migrate()

### Task 2 Complete - ConfigDB + ConfigCache proxy_type support (2026-04-30)

**ConfigDB._ensure_db**: Updated CREATE TABLE model_routes to include proxy_type column
  (TEXT NOT NULL DEFAULT 'codex' CHECK(proxy_type IN ('codex','claude','pass_through')))
  with UNIQUE(source, proxy_type). For existing DBs, Migration handles the upgrade.

**ConfigDB.list_routes**: Added proxy_type: Optional[str] = None param. Builds WHERE mr.proxy_type = ? on demand.

**ConfigDB.add_route**: Extracts data["proxy_type"] (default 'codex'), validates against allowed set.
  INSERT now includes proxy_type column: (source, target_model_id, proxy_type).

**ConfigDB.update_route**: Added "proxy_type" to the mutable fields loop with validation.
  New: validates target_model_id points to active upstream (both new and existing values).
  This fixes the known gap where update silently allowed invalid target models.

**ConfigDB.resolve_model / resolve_one**: Added proxy_type: str = "codex" param.
  resolve_one adds AND mr.proxy_type = ? to WHERE clause.

**ConfigDB.get_all_routes**: Added proxy_type: Optional[str] = None param.
  Added mr.proxy_type to SELECT. WHERE clause filters by proxy_type when provided.
  Returns dict includes proxy_type in each value.

**ConfigDB.validate_star_fallback**: Added proxy_type: str = "codex", pass-through to resolve_model.

**ConfigCache.resolve**: Added proxy_type: str = "codex". Cache key changed to (source, proxy_type) tuple.
  Fallback lookup: ("*", proxy_type).

**ConfigCache.get_all**: Added proxy_type: Optional[str] = None. Filters _routes by proxy_type when specified.

**ConfigCache._refresh_if_stale**: Added proxy_type: Optional[str] = None.
  Calls db.get_all_routes(proxy_type). Keys routes by (source, pt) tuple using route info proxy_type.
  Resolves via db.resolve_one(source, pt).

**Verification**: 348 tests + 10 integration tests pass. All backward compatible (default params = 'codex').

### Task n - proxy_type isolation tests (2026-04-30)
- Added TestRouteProxyType class to test_config_manager.py with 5 tests:
  - list_routes filter by proxy_type
  - add_route with invalid proxy_type raises ValueError
  - duplicate source+same proxy_type raises IntegrityError (UNIQUE constraint)
  - same source+different proxy_type succeeds (no conflict)
  - resolve_one returns different results for same source with different proxy_types
- Added 2 migration tests to test_config_integration.py:
  - migrate() idempotent: second call returns status='already_migrated'
  - data preserved: route count unchanged, all proxy_type='codex' after migration
- All 348 existing tests preserved. Total: 355 tests passing.
