# 动态模型配置 — 设计文稿

**日期**: 2026-04-27
**状态**: 待审阅

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

```sql
-- 上游配置表
CREATE TABLE upstreams (
    id              TEXT PRIMARY KEY,
    base_url        TEXT NOT NULL,
    api_key         TEXT NOT NULL DEFAULT '',
    timeout         INTEGER NOT NULL DEFAULT 120,
    connect_timeout INTEGER NOT NULL DEFAULT 10,
    ssl_verify      INTEGER NOT NULL DEFAULT 1,
    retry           INTEGER NOT NULL DEFAULT 1,
    is_default      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 目标模型表
CREATE TABLE target_models (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    upstream_id TEXT NOT NULL REFERENCES upstreams(id) ON DELETE CASCADE,
    multimodal  INTEGER NOT NULL DEFAULT 1,
    format      TEXT NOT NULL DEFAULT 'openai_chat',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(name, upstream_id)
);

-- 源模型路由映射表
CREATE TABLE model_routes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL UNIQUE,
    target_model_id INTEGER NOT NULL REFERENCES target_models(id) ON DELETE CASCADE,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
```

**字段说明**：

- `upstreams.id`：上游名称/标识，如 `"litellm-prod"`
- `target_models.format`：支持 `'openai_chat'` / `'openai_responses'` / `'anthropic'`，多个格式用逗号分隔。当前阶段先记录，后续用于动态判断是否跳过转换
- `model_routes.source`：源模型名，`"*"` 为 fallback 兜底路由

**查询链路**：源模型 → `model_routes` → `target_models` → `upstreams`

**保留的特性**：
- `multimodal` 标志跟着每个目标模型
- `*` fallback 路由，要求必须存在（启动校验）
- 目标模型 `(name, upstream_id)` 联合唯一，不同上游可有同名模型

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
| DELETE | `/api/upstreams/:id` | 删除上游（级联删除其下模型） |
| POST | `/api/upstreams/:id/test` | 测试上游连通性 |

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

- 新增路由时校验：`source` 唯一；`target_model_id` 必须存在；必须有一条 `source="*"` 的路由
- 删除路由时校验：不能删除最后一条 `source="*"` 的路由
- 删除上游时级联删除其下所有模型和关联路由

---

## 四、前端设计

导航栏新增第四个 Tab：**模型管理**，与 Facts / Tokens 并列。

```
[ Fact Store ]  [ Token 统计 ]  [ 模型管理 ]        [⚙️] [🌙]
```

模型管理页面包含三个分区卡片，从上到下：

1. **上游列表** — 表格展示所有上游（名称、地址、超时、默认标记）。操作：新增/编辑/删除/连通性测试
2. **模型列表** — 表格展示所有目标模型（按上游筛选），列：模型名、所属上游、format、multimodal。操作：新增/编辑/删除
3. **路由映射** — 表格展示 `源模型 → 目标模型（@上游）` 映射关系。`*` fallback 行高亮

交互风格：玻璃卡片、模态框编辑、badge 标签，与现有 Facts/Token 页面一致。

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

## 六、配置重载流程

```
用户点击 "应用配置"
        │
        ▼
  server.py: POST /api/config/reload
        │
        ├──→ proxy.py: POST /admin/reload (HTTP 127.0.0.1)
        │         │
        │         └── ConfigCache.reload() — 清空缓存，重新读取 config.db
        │
        └── 返回 {status: "ok", reloaded_at: "..."}
```

proxy.py 新增 `/admin/reload` 端点（仅监听 127.0.0.1），重载不重启进程。

---

## 七、风险点与应对

| 风险 | 应对 |
|------|------|
| 并发安全 — `reload()` 和 `resolve()` 竞争 | `threading.Lock` 保护缓存更新。配置读取频率低，锁竞争可忽略 |
| 配置错误 — 上游地址无效、误删正在使用的映射 | `resolve_model()` 返回 None 时返回 500 + 明确错误信息，不崩溃 |
| 种子导入冲突 — 已有数据和 yaml 种子不一致 | 仅空数据库时导入。数据已有则跳过 |
| 向后兼容 — 现有 proxy_config.yaml 如何处理 | 首次启动导入后不再读取，旧配置不丢失 |
| 上游连通性 — 不确定配的对不对 | `/api/upstreams/:id/test` 发送最小化请求验证可达性 |
| API Key 安全 — 明文存储在 config.db | 当前 proxy_config.yaml 也是明文，暂无额外加密需求。后续可选 AES 加密 |
