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
