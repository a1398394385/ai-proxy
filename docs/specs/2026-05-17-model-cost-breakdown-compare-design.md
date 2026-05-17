# 按模型统计 — 成本明细 + 对比计费设计

**日期:** 2026-05-17  
**状态:** 已审阅

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

input_cost    = input_tokens    / 1_000_000 × input_cost_per_million    × rate
output_cost   = output_tokens   / 1_000_000 × output_cost_per_million   × rate
cache_rd_cost = cache_read_tokens  / 1_000_000 × cache_read_cost_per_million  × rate
cache_wr_cost = cache_write_tokens / 1_000_000 × cache_creation_cost_per_million × rate
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
