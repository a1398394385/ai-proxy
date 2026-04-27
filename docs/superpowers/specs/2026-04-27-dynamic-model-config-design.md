# 动态模型配置 — 设计文稿

**日期**: 2026-04-27
**状态**: 已审阅（修订版）

---

## 目标

将 Codex Proxy 的模型路由配置从静态 `proxy_config.yaml` 改为动态、可在 Web 页面配置的系统。支持多上游、多模型、多上游间模型可重名。

---

## 一、整体架构

新增独立模块 `config_manager.py`，负责动态配置的读写。

```
Web UI → server.py API → config_manager.py → config.db (读写)
                            ↓
                POST /admin/reload (触发 proxy 重载)
                            ↓
proxy.py → config_manager.py → config.db (读取+缓存)
```

**职责划分**：

| 模块 | 职责 |
|------|------|
| `config_manager.py` | 纯数据层：读写 `config.db`，提供 CRUD 和查询接口，处理缓存。不依赖 server.py 或 proxy.py |
| `server.py` | 新增 `/api/config/*` API 路由，调用 config_manager 完成 CRUD |
| `proxy.py` | 通过 config_manager 读取配置（带缓存），暴露 `/admin/reload` 端点刷新缓存 |

**种子导入**：`proxy_config.yaml` 降级为初始种子。首次启动时若 `config.db` 为空，`config_manager` 自动从 yaml 导入。之后数据库是权威来源，yaml 不再读取。

---

## 二、数据库表设计（config.db）

数据库路径：`~/.hermes/config.db`，与其他数据库（memory_store.db, state.db）统一管理。

```sql
-- schema 版本管理表
CREATE TABLE schema_version (
    version INTEGER NOT NULL
);

-- 上游配置表
CREATE TABLE upstreams (
    id              TEXT PRIMARY KEY,
    base_url        TEXT NOT NULL,
    api_key         TEXT NOT NULL DEFAULT '',
    timeout         INTEGER NOT NULL DEFAULT 120  CHECK(timeout > 0),
    connect_timeout INTEGER NOT NULL DEFAULT 10   CHECK(connect_timeout > 0),
    ssl_verify      INTEGER NOT NULL DEFAULT 1    CHECK(ssl_verify IN (0, 1)),
    retry           INTEGER NOT NULL DEFAULT 1    CHECK(retry >= 0),
    is_active       INTEGER NOT NULL DEFAULT 1    CHECK(is_active IN (0, 1)),
    is_default      INTEGER NOT NULL DEFAULT 0    CHECK(is_default IN (0, 1)),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 目标模型表
CREATE TABLE target_models (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL CHECK(length(name) > 0),
    upstream_id TEXT NOT NULL REFERENCES upstreams(id) ON DELETE RESTRICT,
    multimodal  INTEGER NOT NULL DEFAULT 1    CHECK(multimodal IN (0, 1)),
    format      TEXT NOT NULL DEFAULT 'openai_chat',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(name, upstream_id)
);

-- 源模型路由映射表
CREATE TABLE model_routes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL UNIQUE CHECK(length(source) > 0),
    target_model_id INTEGER NOT NULL REFERENCES target_models(id) ON DELETE RESTRICT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
```

**字段说明**：

- `upstreams.id`：上游名称/标识，如 `"litellm-prod"`
- `upstreams.is_active`：0=禁用（软删除），禁用后的上游不会出现在路由查询结果中，现有关联数据保留
- `upstreams.is_default`：新建模型时默认选中的上游
- `schema_version`：用于未来 schema 迁移（`_ensure_tables()` 读取版本号判断是否需要 ALTER TABLE）
- `target_models.format`：支持 `'openai_chat'` / `'openai_responses'` / `'anthropic'`，多个格式用逗号分隔。当前阶段先记录，后续用于动态判断是否跳过转换
- `model_routes.source`：源模型名，`"*"` 为 fallback 兜底路由
- 所有外键使用 `ON DELETE RESTRICT`，不允许直接删除被引用的记录。需先删除引用方或标记禁用

**查询链路**：源模型 → `model_routes` → `target_models`（跳过 `is_active=0` 的上游）→ `upstreams`

**保留的特性**：
- `multimodal` 标志跟着每个目标模型
- `*` fallback 路由，要求必须存在（启动校验）
- 目标模型 `(name, upstream_id)` 联合唯一，不同上游可有同名模型
- `updated_at` 在应用层显式更新（SQLite DEFAULT 仅在 INSERT 时生效），`config_manager.py` 的 update 方法负责维护

---

## 三、API 设计

server.py（18742 端口）新增 `/api/upstreams`、`/api/models`、`/api/routes`、`/api/config` 四组 REST API。

### 上游管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/upstreams` | 列出所有上游 |
| GET | `/api/upstreams/:id` | 获取单个上游详情 |
| POST | `/api/upstreams` | 新增上游 |
| PUT | `/api/upstreams/:id` | 修改上游 |
| DELETE | `/api/upstreams/:id` | 禁用上游（设 `is_active=0`）。若存在活跃路由（有 model_routes 引用该上游下的模型），拒绝并返回被引用的路由列表。前端弹二次确认对话框，列出将被影响的模型和路由数量 |
| POST | `/api/upstreams/:id/test` | 测试上游连通性（发 GET 到 `{base_url}/models`，超时 5 秒，返回可达性 + 延迟 + HTTP 状态码。401 视为可达，只作警告） |

### 目标模型管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/models` | 列出所有模型，支持 `?upstream_id=` 过滤 |
| GET | `/api/models/:id` | 获取单个模型详情 |
| POST | `/api/models` | 新增模型 |
| PUT | `/api/models/:id` | 修改模型 |
| DELETE | `/api/models/:id` | 删除模型 |

### 路由映射管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/routes` | 列出所有路由映射 |
| GET | `/api/routes/:id` | 获取单条路由 |
| POST | `/api/routes` | 新增路由 |
| PUT | `/api/routes/:id` | 修改路由 |
| DELETE | `/api/routes/:id` | 删除路由 |

### Proxy 重载

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/config/reload` | 通知 proxy 重新加载配置 |
| GET | `/api/config/status` | 返回 proxy 重载时间 + 当前上游/模型/路由数量 |

### 约束校验

**数据库层面**（CHECK 约束，SQLite 在写入时拒绝无效数据）：
- `upstreams.timeout > 0`, `connect_timeout > 0`, `retry >= 0`
- `upstreams.ssl_verify IN (0, 1)`, `is_active IN (0, 1)`, `is_default IN (0, 1)`
- `target_models.name` 非空字符串, `multimodal IN (0, 1)`
- `model_routes.source` 非空字符串
- 外键 `ON DELETE RESTRICT`：被引用的记录无法直接删除

**应用层面**：
- 新增路由时校验：`source` 唯一；`target_model_id` 必须存在且其所属上游 `is_active=1`
- 删除路由时校验：不能删除最后一条 `source="*"` 的路由
- 删除上游时设为 `is_active=0`（软删除），不物理删除。若存在活跃路由引用该上游下的模型，返回 409 + 引用列表
- 删除模型时校验：不能删除被任何 model_route 引用的模型

### 种子导入

`config_manager.py` 初始化时执行 `_seed_from_yaml()`：

1. 检查 `schema_version` 表是否有记录 → 无记录 = 首次启动，执行导入
2. 从 `proxy_config.yaml` 解析 `upstream` 段 → 写入 `upstreams` 表
3. 从 `proxy_config.yaml` 解析 `model_map` 段 → 写入 `target_models` + `model_routes` 表
4. 写入 `schema_version = 1`
5. 若数据库已有数据（`schema_version` 存在）→ 跳过，不做任何修改
6. 提供 `POST /api/config/import-from-yaml` 手动重新导入（覆盖同名 upstream/model/route，不删除数据库中独有数据）

---

## 四、前端设计

导航栏新增第四个 Tab：**模型管理**，与 Facts / Tokens 并列。

```
[ Fact Store ]  [ Token 统计 ]  [ 模型管理 ]        [⚙️] [🌙]
```

### 4.1 页面布局

页面顶部：**配置状态栏**（显示 proxy 重载时间、上游/模型/路由数量、是否已生效）

三个分区卡片：

1. **上游列表** — 表格展示所有上游（状态、名称、地址、超时）。操作：新增/编辑/连通性测试。禁用按钮而非物理删除
2. **模型列表** — 表格展示所有目标模型（按上游筛选），列：模型名、所属上游、format、multimodal。操作：新增/编辑/删除
3. **路由映射** — 表格展示 `源模型 → 目标模型（@上游）` 映射关系。`*` fallback 行高亮

底部：**应用配置按钮**（调用 POST /api/config/reload，成功后更新状态栏）

### 4.2 三表格联动策略

用户体验优先原则：每个表格是独立的操作区，操作后局部刷新 + 被依赖方提示数据变更。

**新增/编辑上游**：
- 保存成功后：上游表刷新
- 模型表中 `upstream_id` dropdown 选项自动纳入新上游
- 路由表无需操作（路由引用的是模型，不受上游直接影响）

**新增/编辑模型**：
- 表单中「所属上游」dropdown 从上游表实时获取（仅显示 `is_active=1` 的）
- 保存成功后：模型表刷新
- 路由表中 `target_model` dropdown 自动纳入新模型

**新增/编辑路由**：
- 表单中「目标模型」dropdown 显示 `模型名 (@上游名)` 格式，按上游分组
- 仅列出 `is_active=1` 的上游下的模型
- 保存成功后：路由表刷新

**禁用上游**：
- 弹二次确认对话框，列出：上游名称、其下模型数、引用了这些模型的路由数
- 用户确认后：该上游行变灰（`is_active=0`），模型和路由 dropdown 中移除相关选项
- 若存在活跃路由引用 → 返回 409，前端提示"请先删除或重定向以下路由：..."

**"应用配置"按钮**：
- 用户修改了任何配置后，按钮高亮（橙色脉冲）提醒
- 点击后调 POST /api/config/reload，成功后更新状态栏时间戳

交互风格与现有 Facts/Token 页面一致。

Settings 页面保持不变。

---

## 五、config_manager.py 模块设计

**文件职责**：纯数据层，不依赖 server.py 或 proxy.py。

### ConfigDB — 数据库操作

```python
class ConfigDB:
    __init__(db_path)          # 连接/创建 config.db，自动建表
    _ensure_tables()           # CREATE TABLE IF NOT EXISTS
    _seed_from_yaml(yaml_path) # 首次启动从 proxy_config.yaml 导入种子数据
    close()                    # 关闭连接

    # 上游 CRUD
    list_upstreams() -> list
    get_upstream(id) -> dict|None
    add_upstream(data) -> str
    update_upstream(id, data)
    delete_upstream(id)

    # 目标模型 CRUD
    list_models(upstream_id=None) -> list
    get_model(id) -> dict|None
    add_model(data) -> int
    update_model(id, data)
    delete_model(id)

    # 路由映射 CRUD
    list_routes() -> list
    get_route(id) -> dict|None
    add_route(data) -> int
    update_route(id, data)
    delete_route(id)

    # 配置查询（供 proxy 使用）
    resolve_model(source_name) -> dict
        # 返回 {target_name, multimodal, format, upstream: {base_url, api_key, ...}}
        # 找不到 → 走 "*" fallback
    get_all_routes() -> dict
```

### ConfigCache — 内存缓存（供 proxy.py 使用）

```python
class ConfigCache:
    __init__(db_path, ttl=5)   # 默认 5 秒 TTL
    reload()                    # 强制重新读取数据库
    resolve(source_name) -> dict  # 带缓存的 resolve
    get_all() -> dict           # 带缓存的批量查询
```

**现有代码替换**：
- `proxy.py` 的 `resolve_model()` → `ConfigCache.resolve()`
- `proxy.py` 的 `_handle_models()` → 从 `ConfigCache.get_all()` 取源模型列表
- `load_config()` 不再加载 model_map

---

## 六、配置重载与生效

### 两条互补路径

| 路径 | 触发方式 | 生效时间 | 用途 |
|------|---------|---------|------|
| 手动重载 | Web 页面点击"应用配置" → server.py POST proxy `/admin/reload` | 立即 | 用户主动确认后即时生效 |
| 自动过期 | `ConfigCache` TTL 到期（默认 5 秒），下次 `resolve()` 时重新读库 | ≤ 5 秒 | 兜底，防止手动重载失败 |

两条路径不冲突：用户保存配置后如果没点"应用配置"，最长 5 秒后自动生效；点了则立即生效。

### 手动重载流程

```
用户点击 "应用配置"
        │
        ▼
  server.py: POST /api/config/reload
        │
        ├──→ proxy.py: POST /admin/reload (HTTP 127.0.0.1)
        │         │
        │         ├── 连通：ConfigCache.reload() → 返回 {"status": "ok", "reloaded_at": "..."}
        │         │
        │         └── 不可达：返回 {"status": "error", "message": "proxy 未运行，配置将在 TTL 过期后自动生效"}
        │
        └── 将 proxy 响应透传给前端
```

### 重载状态查询

`GET /api/config/status` 返回：
```json
{
  "proxy_reachable": true,
  "last_reloaded_at": "2026-04-27 15:30:00",
  "config_db": {"upstreams": 3, "models": 8, "routes": 5}
}
```

前端在配置页面展示此状态（顶部状态栏），让用户知道当前配置是否已生效。

proxy.py 新增 `/admin/reload` 端点（仅监听 127.0.0.1），重载不重启进程。

---

---

## 七、format 字段与 transform 交互（设计预留）

`target_models.format` 字段记录目标模型支持的请求格式。当前阶段仅存储，不改变转发逻辑。

**后续使用时**的伪代码逻辑：

```python
def should_transform(source_format: str, target_format: str) -> bool:
    """是否需要做格式转换。"""
    # 如果目标模型直接支持请求格式 → 不转换
    if source_format in target_format.split(","):
        return False
    return True

# 在 proxy._handle_responses() 中：
model_cfg = cache.resolve(model_name)
if model_cfg["format"] and "openai_responses" in model_cfg["format"]:
    # 目标模型原生支持 Responses API，跳过转换
    forward_raw(body, model_cfg["upstream"])
else:
    # 走现有转换路径
    chat_body = responses_to_chat(body, model_cfg)
    forward_to_chat(chat_body, model_cfg["upstream"])
```

**当前阶段**：所有上游都是 `openai_chat` 格式，转换逻辑不变。format 字段作为数据预留存在于数据库和 UI 中。

---

## 九、风险点与应对

| 风险 | 应对 |
|------|------|
| 并发安全 — `reload()` 和 `resolve()` 竞争 | `threading.Lock` 保护缓存更新。配置读取频率低，锁竞争可忽略 |
| 配置错误 — 上游地址无效、误删正在使用的映射 | `resolve_model()` 返回 None 时返回 500 + 明确错误信息，不崩溃；外键 RESTRICT + 软删除防止误删 |
| 种子导入冲突 — 已有数据和 yaml 种子不一致 | `schema_version` 存在则跳过；手动 `/api/config/import-from-yaml` 覆盖同名记录 |
| 向后兼容 — 现有 proxy_config.yaml 如何处理 | 首次启动导入后不再读取，旧配置不丢失 |
| proxy 不可达 — manual reload 时 proxy 进程不在 | 返回友好错误提示给前端，配置将在 TTL 过期后自动生效 |
| API Key 安全 — 明文存储在 config.db | 当前 proxy_config.yaml 也是明文，暂无额外加密需求。后续可选 AES 加密 |
| API Key 前端暴露 — 网页展示时泄露 | 前端展示 API Key 时用 `sk-****abc` 脱敏格式，仅在编辑模态框中明文显示 |
