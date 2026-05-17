# 按模型统计 — 成本明细 + 对比计费设计

**日期:** 2026-05-17  
**状态:** 待实现

---

## 背景

按模型统计页面（tokens.js）的模型行展开后，目前只显示最近 50 条请求记录表格。用户需要在请求记录上方看到当前模型的 4 项成本分项（Input / Output / Cache Read / Cache Write），并能选择计费表中的其他模型，将当前模型的 token 数据套用到对比模型的计费规则上，直观比较两套定价的成本差异。

---

## 功能描述

### 1. 成本明细条（默认显示）

模型行展开后，在请求记录表格上方显示一条紧凑横排，包含 4 项成本分项及合计：

```
成本明细 — 最近 7 天         合计 ¥0.079540
In ¥0.012450  Out ¥0.062100  Cache Rd ¥0.000310  Cache Wr ¥0.004680
```

- 数据范围：当前 period（day / week / month）内该模型的全量汇总，与模型行 `estimated_cost_cny` 一致
- token 数量来自 `allModels` 数组（`fetch_by_model` 已返回），4 项成本前端计算

### 2. 对比计费行（选择后出现）

成本明细条下方有一个"套用计费"下拉框，默认为空（不显示对比行）。选择对比模型后，在明细条正下方出现第二条（橙色边框），显示相同 token 数套用对比模型单价的成本，及与当前模型的差额百分比：

```
套用计费: [claude-sonnet-4-6 ▼]      合计 ¥0.026513  ↓67%
In ¥0.004150  Out ¥0.020700  Cache Rd ¥0.000103  Cache Wr ¥0.001560
```

- 下拉框选项来自 `model_pricing` 表所有模型（含当前模型，方便验算）
- 差额 = `(对比成本 - 当前成本) / 当前成本 × 100%`，正值红色，负值绿色
- 选择"无"或清空时对比行隐藏

---

## 数据流

```
页面初始化
  ├─ /api/token_stats/by_model?period=X  →  allModels（已有，含 4 项 token 数）
  └─ /api/pricing                        →  allPricings（新增，一次性拉取）

模型行展开时
  ├─ 从 allModels 取该模型 4 项 token 数
  ├─ 从 allPricings 取该模型单价 → 计算 4 项成本 → 渲染明细条
  └─ 用 allPricings 填充下拉框选项

对比模型切换时（纯前端，无网络请求）
  └─ 从 allPricings 取对比模型单价 → 用当前模型 token 数重算 → 渲染对比行
```

**0 后端改动，0 新增 API。**

---

## 成本计算规则

```
rate = (currency === "USD") ? 7 : 1

input_cost    = input_tokens    / 1_000_000 × input_cost_per_million    × rate × multiplier
output_cost   = output_tokens   / 1_000_000 × output_cost_per_million   × rate × multiplier
cache_rd_cost = cache_read_tokens  / 1_000_000 × cache_read_cost_per_million  × rate × multiplier
cache_wr_cost = cache_write_tokens / 1_000_000 × cache_creation_cost_per_million × rate × multiplier
```

注意：`input_includes_cache_read` 字段已在后端 `_fetch_unified_records` 阶段修正过 `input_tokens`，前端无需再处理。

---

## 实现范围

仅修改 `static/js/pages/tokens.js`，改动集中在：

1. **`loadTokenStats()`** — 并发拉取 `allPricings`（与 `allModels` 同步加载）
2. **新增 `calcCost(tokens, pricing)`** — 纯函数，返回 4 项成本及合计
3. **新增 `renderCostBar(model, container)`** — 渲染明细条 + 对比下拉框 + 对比行
4. **`expandModelRow()`** — 在插入请求记录表格前，先调用 `renderCostBar()`

不涉及后端、CSS 文件（沿用现有 detail-content 样式）、其他页面。

---

## 边界情况

| 情况 | 处理 |
|------|------|
| 当前模型不在 `model_pricing` 表 | 明细条显示"未配置计费，成本按 ¥0 计算" |
| 对比模型不在 `model_pricing` 表 | 不会出现，下拉框只列出计费表中的模型 |
| `/api/pricing` 请求失败 | `allPricings = []`，下拉框不显示，明细条退化为 token 数展示 |
| 所有成本均为 0 | 正常显示，不特殊处理 |

---

## 不在范围内

- 请求记录表格本身不改动
- 不添加"按对比模型排序"等高级功能
- 不做持久化（刷新后对比模型选择不保留）

---

## 审阅记录

**审阅日期:** 2026-05-17

### 必须修复

1. **成本公式遗漏 `multiplier`** — 四项成本计算均未乘以 `model_pricing.multiplier`（默认 `1.0`，部分模型为非 1 值）。遗漏会导致前端计算与后端 `estimated_cost_cny` 不一致。已修正上方公式。

2. **模型名大小写匹配** — 后端 `_CostCalculator.get_pricing()` 对 `model_id` 做 `.lower()` 后存缓存，查询时也 `.lower()`。前端从 `allModels[i].model` 与 `allPricings[j].model_id` 做匹配时必须 `.toLowerCase()`，否则可能匹配不上。`calcCost` 实现中需明确此要求。

3. **下拉框显示字段** — `/api/pricing` 返回每条记录同时有 `model_id` 和 `display_name`。下拉框应优先显示 `display_name`，回退到 `model_id`，与后端 `get_display_name()` 行为一致。

### 已知限制（接受）

4. **跨模型对比计费为近似值** — 当前模型的 `input_tokens` 已按其自身的 `input_includes_cache_read` 修正。套用到对比模型定价时，若两模型的 `input_includes_cache_read` 设置不同，input 成本会有偏差。接受为近似对比。

5. **汇率硬编码 `7`** — 前后端分别硬编码 `rate = 7` / `EXCHANGE_RATE = 7`，需保持同步。已知设计决策。

6. **前端独立计算可能与后端 `estimated_cost_cny` 有细微差异** — 接受。

### 已验证

- `GET /api/pricing` 已存在，返回全量定价，确认 0 新增 API
- 实现范围只改 `tokens.js` 一个文件，4 个改动点明确
- 边界情况覆盖合理；补充：period 切换后 `allPricings` 不需要重新拉取（定价与时间无关）
