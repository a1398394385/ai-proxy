# Learnings: proxy-pass-through

## Task 5 & 6: Pass-through forwarding implementation

### Patterns
- `_create_upstream_conn(upstream_cfg, parsed, port)` handles proxy/SSL transparently — used in both methods
- `self.command` for HTTP method (not hardcoded "POST")
- `body_raw` for raw request body (not json.dumps)
- `forward_path` for upstream path (not hardcoded "/chat/completions")
- Content-Type copied from `self.headers`
- `get_logger()` may return None — always guard with `if logger:`
- `record_token_stats(usage, context)` — context dict needs: request_id, agent, model, target_model, request_ts, duration_ms

### Decisions
- `import re` added at top of proxy.py (was not present before)
- Phase comments kept (match existing codebase convention in `_forward_non_streaming`)
- Streaming error path: writes simple `data: {"error":"..."}\n\n` instead of injecting Codex events (per MUST NOT DO rules)
- Usage extraction in streaming: regex scan of last SSE chunk for `"usage":{...}` pattern

### Issues
- None. All 333 tests pass, no new lsp_diagnostics.

## Fix 2: Code quality fixes (F2)

### Fix 1: Usage regex → brace-balanced JSON extraction
- Old: `re.search(r'"usage"\s*:\s*(\{[^}]+\})', last_chunk)` — fails on nested usage JSON (e.g. `input_tokens_details`)
- New: Find `"usage"` key, scan forward counting `{`/`}` depth to extract full nested object
- `import re` now unused but left in place per "keep other code unchanged" rule

### Fix 2: Retry loop for streaming pass-through
- Added `retries = upstream_cfg.get("retry", 0) + 1` and `for attempt in range(retries)` around connection establishment
- Retries only the conn creation + request, NOT the SSE header sending (can only send headers once)
- On last attempt failure: sends JSON 502 error (not SSE event — consistent with non-streaming pattern)

### Fix 3: Debug log for silent JSONDecodeError
- Replaced `except (json.JSONDecodeError, UnicodeDecodeError): pass` with `logging.debug(...)`
- Ensures visibility when pass-through body isn't parseable JSON (e.g. binary uploads)

### Test Results
- 347 passed, 1 pre-existing E2E flake (test_smoke_request_creates_db_records — requires running proxy, unrelated to changes)
