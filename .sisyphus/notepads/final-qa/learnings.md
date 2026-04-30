
## QA Test Results

### Test A: Route Priority
- GET /health: 200 ✅
- GET /v1/responses: 426 ✅ (correct per spec)
- GET /v1/models: 200 ✅

### Test B: Non-streaming Pass-through
- Pass-through to /v1/chat/completions works correctly
- Upstream returned model access denial for gpt-4o (expected - upstream restriction, not proxy bug)
- Proxy correctly forwarded and returned upstream response

### Test C: Logging
- debug_log has entries for raw_request and upstream_response ✅
- token_stats records input/output tokens with "completed" status ✅

### Test D: Test Suite
- 348/348 passed in 18.00s ✅

### Test E: Path Traversal
- Path traversal correctly rejected with 404 {"error":"not found"} ✅

### F3: Real Manual QA (2026-04-30)

**Backend QA (6/6 PASS)**:
- POST /api/migrate → "already_migrated" ✅
- POST /api/routes with proxy_type='claude' → 201 ✅
- POST duplicate source+proxy_type → 409 ✅
- GET /api/routes?proxy_type=codex/claude → proxy_type isolation verified ✅
- GET /api/config/status → proxy_reachable + pass_through_reachable fields ✅
- POST /api/config/reload → dual (proxy+pass_through) sub-keys ✅

**Frontend QA (4/4 PASS)**:
- 4 nav tabs visible ✅
- "路由映射" tab switches to routes page with proxy_type filter tabs ✅
- "模型管理" tab shows upstream table (no route table) ✅
- #apply-config-btn ("✅ 应用配置") in nav bar ✅

**Critical Bug Found & Fixed**:
- server.py:22 CONFIG_DB_PATH pointed to data/access_log.db instead of ~/.hermes/config.db
- This caused POST /api/routes to crash with "Empty reply from server"
- Root cause: access_log.db lacked proxy_type column (migration only applied to config.db)
- Fix: changed to Path(os.path.expanduser("~/.hermes/config.db"))

**Test Suite**: 355/355 passed
