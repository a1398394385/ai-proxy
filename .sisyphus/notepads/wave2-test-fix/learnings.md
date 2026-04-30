# Wave 2 Test Fix — Learnings

## Critical Bug Found & Fixed

**Stale CONFIG reference in proxy.py due to `from common import CONFIG`**

- Python's `from X import Y` creates a local binding that captures the reference AT IMPORT TIME
- When `common.load_config()` reassigns `CONFIG = _parse_yaml(...)`, it creates a NEW dict object
- proxy.py's local `CONFIG` still points to the initial empty dict `{}`
- This means proxy.py NEVER reads values from proxy_config.yaml — it always uses defaults
- The proxy still works because:
  - Port defaults to 48743 (same as config)
  - Upstream config comes from config.db via config_cache (not from CONFIG)
  - Logging uses defaults

**Fix**: Changed `load_config` to use `CONFIG.clear(); CONFIG.update(new_config)` instead of `CONFIG = new_config` to preserve object identity.

## Test Patterns

- `_load_proxy()` and `_load_pass_through()` use `importlib.util` for dynamic module loading
- `_configure(mod)` sets `mod.CONFIG` which rebinds the module's CONFIG attribute
- The `_make_handler` function creates MagicMock handlers with lambda-bound real methods
- Always mock `http.client.HTTPConnection` for integration tests

## _normalize_forward_path behavior change

- OLD: Stripped `/v1` prefix from paths
- NEW: Only normalizes double slashes and prevents path traversal; `/v1` prefix is preserved
- The `/v1` stripping is now handled by the calling code (e.g., pass_through.py's upstream forwarding)
