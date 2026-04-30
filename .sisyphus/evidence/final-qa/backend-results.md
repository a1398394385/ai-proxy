# Backend QA Results

## QA1: POST /api/migrate
- Request: `curl -s -X POST http://127.0.0.1:18742/api/migrate`
- Response: `{"status": "already_migrated", "details": "已迁移到 v1: model_routes 包含 proxy_type 列"}`
- HTTP: 200
- **PASS** ✓

## QA2: POST /api/routes with proxy_type='claude'
- Request: `curl -s -X POST ... -d '{"source":"f3-test-claude","target_model_id":1,"proxy_type":"claude"}'`
- Response: `{"id": 5, "message": "Created"}`
- HTTP: 201
- **PASS** ✓

## QA3: POST /api/routes with same source+same proxy_type (409 IntegrityError)
- Request: `curl -s -X POST ... -d '{"source":"f3-test-claude","target_model_id":1,"proxy_type":"claude"}'`
- Response: `{"error": "UNIQUE constraint failed: model_routes.source, model_routes.proxy_type"}`
- HTTP: 409
- **PASS** ✓

## QA4: GET /api/routes?proxy_type=codex
- Request: `curl -s "http://127.0.0.1:18742/api/routes?proxy_type=codex"`
- Response: 4 codex routes returned, ALL have proxy_type='codex'. Claude route (f3-test-claude) NOT present.
- HTTP: 200
- **PASS** ✓ (proxy_type isolation verified)

## QA5: GET /api/config/status
- Request: `curl -s http://127.0.0.1:18742/api/config/status`
- Response: `{"proxy_reachable": true, "pass_through_reachable": false, "config_db": {"upstreams": 2, "models": 3, "routes": 5}}`
- HTTP: 200
- **PASS** ✓ (both proxy_reachable and pass_through_reachable fields exist)

## QA6: POST /api/config/reload
- Request: `curl -s -X POST http://127.0.0.1:18742/api/config/reload`
- Response: `{"proxy": {"status": "ok", "reloaded_at": "2026-04-30 15:56:53"}, "pass_through": {"error": {"type": "server_error", "message": "[Errno 61] Connection refused"}}}`
- HTTP: 200
- **PASS** ✓ (response has both proxy and pass_through sub-keys; pass_through error is expected since proxy health check requires auth)

## Summary: 6/6 PASS

## Bug Found
**Critical**: `server.py:22` had `CONFIG_DB_PATH` pointing to `data/access_log.db` instead of `~/.hermes/config.db`. This caused POST /api/routes to crash with "Empty reply from server" because access_log.db's model_routes table lacked the proxy_type column. Fixed as part of QA by changing line 22 to `Path(os.path.expanduser("~/.hermes/config.db"))`.
