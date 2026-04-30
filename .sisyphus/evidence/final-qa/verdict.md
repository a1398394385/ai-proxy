# F3: Real Manual QA — Final Verdict

## Summary
| Category | Results |
|----------|---------|
| Backend Scenarios | **6/6 pass** |
| Frontend Scenarios | **4/4 pass** |
| Edge Cases | **1 found & fixed** |
| Test Suite | **355/355 pass** |

## Backend Scenarios (6/6)
1. ✅ QA1: POST /api/migrate → `{"status": "already_migrated"}` (200)
2. ✅ QA2: POST /api/routes with proxy_type='claude' → 201 Created
3. ✅ QA3: POST duplicate (same source+same proxy_type) → 409 IntegrityError
4. ✅ QA4: GET /api/routes?proxy_type=codex → only codex routes returned (isolation verified)
5. ✅ QA5: GET /api/config/status → both `proxy_reachable` + `pass_through_reachable` fields exist
6. ✅ QA6: POST /api/config/reload → has both `proxy` and `pass_through` sub-keys

## Frontend Scenarios (4/4)
7. ✅ QA7: 4 nav tabs visible (Fact Store / Token 统计 / 模型管理 / 路由映射)
8. ✅ QA8: "路由映射" tab → switches to routes page with proxy type filter tabs
9. ✅ QA9: "模型管理" tab → shows upstream table (no route table)
10. ✅ QA10: `#apply-config-btn` ("✅ 应用配置") visible in nav bar

## Edge Cases (1 found & fixed)
- **Critical Bug Found**: `server.py:22` CONFIG_DB_PATH pointed to `data/access_log.db` instead of `~/.hermes/config.db`. This caused ALL POST /api/routes requests to return "Empty reply from server" because access_log.db's model_routes table lacked the proxy_type column (migration only applied to ~/.hermes/config.db). Fixed by correcting the path.
- Console error: only favicon.ico 404 (harmless, expected)

## Evidence
- `.sisyphus/evidence/final-qa/backend-results.md`
- `.sisyphus/evidence/final-qa/frontend-results.md`
- `.sisyphus/evidence/final-qa/qa7-nav-tabs.png`
- `.sisyphus/evidence/final-qa/qa8-routes-page.png`

## VERDICT: APPROVE (with 1 critical bug fixed)
Cross-task integration works: migration → routes CRUD → proxy_type isolation → frontend tab switching → dual reload.
