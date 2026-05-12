# 计费表迁移设计

**日期**: 2026-05-12
**状态**: 待实施

## 概述

将 CC-switch 的 `model_pricing` 表迁移到本项目 `config.db`，实现自建的计费管理功能。支持人民币/美元双币种（汇率 7），页面统一显示人民币，单价和费用支持 6 位小数精度。

## 需求

- 将 `model_pricing` 从 `~/.cc-switch/cc-switch.db` 迁入 `~/.hermes/config.db`，不再依赖 cc-switch
- 支持 USD/RMB 双币种，每条定价记录自带 `currency` 字段
- 汇率固定为 7（USD × 7 = RMB）
- 页面统一显示人民币，`¥` 前缀
- 后端 `_CostCalculator` 统一输出人民币金额
- 单价和费用均支持 6 位小数
- 完整 CRUD：查看/新增/编辑/删除定价记录
- 搜索：按模型名/显示名模糊匹配
- 前端新增独立 Tab

## 数据库 Schema

在 `~/.hermes/config.db` 新增表：

```sql
CREATE TABLE model_pricing (
    model_id                        TEXT PRIMARY KEY,
    display_name                    TEXT NOT NULL,
    input_cost_per_million          TEXT NOT NULL,
    output_cost_per_million         TEXT NOT NULL,
    cache_read_cost_per_million     TEXT NOT NULL DEFAULT '0',
    cache_creation_cost_per_million TEXT NOT NULL DEFAULT '0',
    currency                        TEXT NOT NULL DEFAULT 'USD' CHECK(currency IN ('USD', 'RMB'))
);
```

- 价格列用 `TEXT` 存储（避免浮点精度问题），计算时转 `Decimal`/`float`
- `currency` 只允许 `USD` 或 `RMB`，默认 `USD`（兼容 cc-switch 导入数据）
- 无 `created_at`/`updated_at`，定价是配置数据

种子数据从 cc-switch 的 `model_pricing_export.sql` 提取 INSERT 语句，内嵌为 Python 常量列表，首次建表且为空时批量插入。

## PricingManager 模块

新建 `proxy/pricing_manager.py`：

```python
class PricingDB:
    """model_pricing 表的 CRUD 操作。"""

    def __init__(self, db_path: Path):
        self.db_path = db_path

    def _ensure_table(self): ...       # 幂等建表，表空时导入种子数据
    def list_pricings(self, search=None) -> list[dict]: ...
    def get_pricing(self, model_id) -> dict | None: ...
    def add_pricing(self, data: dict) -> str: ...
    def update_pricing(self, model_id, data: dict) -> bool: ...
    def delete_pricing(self, model_id) -> bool: ...
```

关键点：
- 不依赖 `Migrations`，`_ensure_table()` 幂等建表
- `db_path` 传入 config.db 路径，与 upstreams/target_models 共库不同表
- `search` 参数对 `model_id` 和 `display_name` 做 LIKE 匹配
- 每次查询新建连接，用完关闭（与项目约定一致）

## _CostCalculator 改造

改造 `stats_service.py` 中的 `_CostCalculator`：

- 数据源：从 `cc-switch.db` 改为读 `config.db`（通过 `PricingDB`）
- 币种换算：加载定价时，USD 价格 × 7 转为人民币，RMB 价格原样使用
- 输出：`calculate()` 统一返回人民币金额（float，6 位小数精度）
- 缓存：保留现有 TTL 缓存机制（300s）

```python
class _CostCalculator:
    EXCHANGE_RATE = 7  # USD → RMB

    def __init__(self, config_db_path):
        self._pricing_db = PricingDB(config_db_path)

    def get_pricing(self) -> dict:
        rows = self._pricing_db.list_pricings()
        pricing = {}
        for r in rows:
            rate = 1 if r['currency'] == 'RMB' else self.EXCHANGE_RATE
            pricing[r['model_id']] = {
                'input_cost': float(r['input_cost_per_million']) * rate,
                'output_cost': float(r['output_cost_per_million']) * rate,
                'cache_read_cost': float(r['cache_read_cost_per_million']) * rate,
                'cache_creation_cost': float(r['cache_creation_cost_per_million']) * rate,
            }
        return pricing
```

`StatsService` 构造函数移除 `cc_switch_db_path` 参数，改为通过 `config_db_path` 使用 `PricingDB`。API 返回字段 `estimated_cost_usd` 改为 `estimated_cost_cny`。

## API 路由

在 `server.py` 新增 `/api/pricing/*` 路由：

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/api/pricing` | 列出所有定价，支持 `?search=xxx` |
| GET | `/api/pricing/{model_id}` | 获取单个模型定价 |
| POST | `/api/pricing` | 新增定价记录 |
| PUT | `/api/pricing/{model_id}` | 更新定价记录 |
| DELETE | `/api/pricing/{model_id}` | 删除定价记录 |

- `GET /api/pricing` 返回原始数据（含 currency 字段），前端负责人民币换算显示
- `POST`/`PUT` 校验：currency 必须为 USD/RMB，价格必须为合法数字字符串，model_id 不能为空
- `DELETE` 无外键约束，直接删除
- 与现有 `/api/upstreams`、`/api/models` 风格一致

## 前端页面

新建 `static/js/pages/pricing.js` + `static/css/pricing.css`，在 `index.html` 新增 Tab "💰 计费表"。

### 页面布局

```
┌───────────────────────────────────────────────────┐
│  [搜索框: 模型名/显示名]              [＋ 新增定价]  │
├───────────────────────────────────────────────────┤
│ 模型ID │ 显示名 │ 输入¥ │ 输出¥ │ 缓存读¥ │ 缓存写¥ │ 币种 │ 操作 │
├────────┼────────┼───────┼───────┼──────────┼──────────┼──────┼──────┤
│ claude-│ Claude │¥21.00 │¥105.00│ ¥3.5000  │ ¥43.7500 │ USD  │编辑删│
│ sonnet │Sonnet  │       │       │          │          │      │除   │
└────────┴────────┴───────┴───────┴──────────┴──────────┴──────┴──────┘
```

### 交互细节

- 表格列：模型ID、显示名、输入/输出/缓存读/缓存写单价（已换算为人民币显示）、币种、操作
- 币种列显示为 badge（USD 蓝色、RMB 绿色）
- 人民币换算：前端按 `currency × 7(USD)` 换算显示，编辑时还原为原始币种值
- 新增/编辑用 modal 弹窗：model_id、display_name、4 个价格输入框、currency 下拉（USD/RMB）
- 价格输入框限制 6 位小数
- 删除需确认弹窗（与上游删除一致）
- 搜索框实时过滤（keyup 事件，模糊匹配 model_id + display_name）

### 注册方式

- `app.js` 页面加载器新增 `pricing` → `pages/pricing.js` 映射
- `index.html` 新增 `<link>` 引用 `pricing.css`，新增 Tab 按钮，新增 `#page-pricing` 容器

## Token 统计页适配

`estimated_cost_usd` → `estimated_cost_cny` 变更影响现有 tokens.js 页面，需同步修改：

- `tokens.js` 中所有 `estimated_cost_usd` 替换为 `estimated_cost_cny`
- 所有 `$` 前缀替换为 `¥`
- `toFixed(4)` 替换为 `toFixed(6)`（6 位小数精度）
- tooltip/图例中"成本"标签的 `$` 前缀改为 `¥`
