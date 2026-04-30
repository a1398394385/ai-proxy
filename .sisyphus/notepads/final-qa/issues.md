
### F3 Critical Bug: Wrong CONFIG_DB_PATH in server.py
- **Severity**: Critical (blocks all POST /api/routes operations)
- **Location**: server.py line 22
- **Problem**: `CONFIG_DB_PATH = Path(__file__).resolve().parent / "data" / "access_log.db"` instead of `Path(os.path.expanduser("~/.hermes/config.db"))`
- **Impact**: POST /api/routes gets "Empty reply from server" because access_log.db's model_routes table lacks proxy_type column
- **Fix**: Changed to `Path(os.path.expanduser("~/.hermes/config.db"))`
- **Status**: Fixed during QA
