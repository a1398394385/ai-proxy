## 2026-04-30T06:49:29Z Decisions

### Architecture: Migration approach
- SQLite doesn't support ALTER TABLE DROP CONSTRAINT
- Chose: CREATE new table → INSERT data → DROP old → RENAME new
- This is the recommended approach for SQLite schema changes involving constraint removal

### Architecture: proxy_type default value
- All existing routes get proxy_type='codex'
- Reason: historical data was used primarily by Codex CLI
- New routes default to current proxy context (determined by frontend tab selection)

### Architecture: Dual proxy reload
- "/api/config/reload" sends POST to both proxy:48743 and pass_through:48744
- If pass_through is offline: graceful degradation (returns both results)
- Reason: both proxies have independent ConfigCache instances sharing config.db

### Frontend: Page naming convention
- Internal page id stays "models" for backward compatibility
- Visual nav tab label: "模型管理" (unchanged)
- New internal page id: "routes"
- New visual nav tab label: "🔀 路由映射"
