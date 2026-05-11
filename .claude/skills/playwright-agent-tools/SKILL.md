---
name: playwright-agent-tools
description: 使用 Playwright MCP 工具操控 Hermes Data Browser (localhost:18742) 的前端 UI。当需要测试、验证、截图、交互本项目的 Web 页面时触发此 skill。触发场景包括：前端测试、UI验证、playwright、浏览器操作、截图验证、页面交互、验证改动、检查前端、测试页面。即使用户没有明确提到 playwright，只要涉及本项目 Web UI 的操作或验证，也应使用此 skill。
---

# Playwright MCP — Hermes Data Browser 操作指南

本项目的 Hermes Data Browser 是一个运行在 `http://localhost:18742` 的 SPA，5 个页面通过 Tab 按钮切换，URL 不变。

## 操作前检查

```
1. 确认服务运行: ./server.sh status
2. 如果未运行: ./server.sh start
```

## 核心机制：ref → target

Playwright MCP 的所有交互工具（click、type、fill_form、select_option）**必须通过 `target` 参数传入 ref 值**，ref 来自 `browser_snapshot` 的输出。

```yaml
# snapshot 输出示例
- button "📊 Token 统计" [ref=e10] [cursor=pointer]
#                              ^^^^ 这就是 ref
```

### 操作模式

```
navigate → snapshot → 用 ref 操作 → snapshot → 用新 ref 操作 → ...
```

**关键：每次页面状态变化后（点击、导航、数据加载），ref 会重新分配，必须重新 snapshot。**

## 常见失败与修复

| 失败错误 | 原因 | 修复 |
|---------|------|------|
| `expected string, received undefined` | click 缺少 `target` | 加 `target: "eNNN"` |
| `Invalid option: expected "textbox"\|"checkbox"\|...` | fill_form 的 type 值非法 | type 只能是 textbox/checkbox/radio/combobox/slider |
| 点击无效果 | ref 过期 | 重新 snapshot 获取新 ref |
| 找不到模态框元素 | 模态框未渲染完成 | snapshot 前 wait_for 500ms |
| alert/confirm 阻断操作 | 删除/测试触发弹窗 | 用 evaluate 预拦截：`window.confirm = () => true` |

## 工具参数格式速查

### browser_click（点击元素）

```json
{ "element": "按钮描述", "target": "e10" }
```

`target` 是必需参数。只传 `element` 不传 `target` 会报错。

### browser_type（输入文本）

```json
{ "element": "输入框描述", "target": "e24", "text": "要输入的内容" }
```

会先清空再输入。

### browser_fill_form（批量填表）

```json
{
  "fields": [
    { "name": "字段名", "type": "textbox", "target": "e769", "value": "值" },
    { "name": "下拉框", "type": "combobox", "target": "e792", "value": "messages" }
  ]
}
```

`type` 只接受：`textbox` | `checkbox` | `radio` | `combobox` | `slider`

**禁止使用** `input`、`text`、`number`、`select` 等——会报错。

### browser_select_option（下拉框选择）

```json
{ "element": "描述", "target": "e792", "values": ["messages"] }
```

`values` 是数组，支持多选。

### browser_evaluate（执行 JS 读取状态）

```json
{ "function": "() => window.currentPage" }
```

本项目暴露的全局变量：`window.currentPage`、`window.currentPeriod`、`window.allFacts`、`window.editingId`

### 其他工具

| 工具 | 参数 | 用途 |
|------|------|------|
| `browser_navigate` | `{ url }` | 打开页面 |
| `browser_snapshot` | 无 | 获取可访问性树 + ref |
| `browser_take_screenshot` | `{ filename }` | 截图保存到 CWD |
| `browser_console_messages` | 无 | 查看控制台日志 |
| `browser_wait_for` | `{ time: 2000 }` 或 `{ text: "关键词" }` | 等待条件 |
| `browser_press_key` | `{ key: "Escape" }` | 模拟按键（关闭模态框用 Escape） |
| `browser_close` | 无 | 关闭页面 |

## 页面导航

5 个 Tab 按钮，共享 `http://localhost:18742`，点击切换：

| 页面 | 按钮文本 | data-page |
|------|---------|-----------|
| Fact Store | 📋 Fact Store | facts |
| Token 统计 | 📊 Token 统计 | tokens |
| 模型管理 | 🔌 模型管理 | models |
| 路由映射 | 🔀 路由映射 | routes |
| 数据库查询 | 🗼️ 数据库查询 | dbquery |

导航步骤：snapshot → 找到 Tab 按钮 ref → click → snapshot 确认 `[active]`

快捷方式（evaluate 直接切换）：
```javascript
browser_evaluate({ function: '() => document.querySelector(\'[data-page="tokens"]\').click()' })
```

## 各页面操控要点

### Fact Store

- **搜索**: type 到 `#search` 输入框
- **类别筛选**: click 类别按钮（如 "📝 通用 (9)"）
- **新增事实**: click "+ 新增事实" → 填写模态框（内容 textarea、类别 combobox、标签 input、信任度 input）→ click 保存
- **编辑/删除**: click 事实卡片上的 "编辑"/"删除" 按钮
- **展开长内容**: click "展开 ▼" 按钮

### Token 统计

- **切换周期**: click "24小时"/"7天"/"30天" 按钮
- **搜索模型**: type 到 `#model-search`
- **刷新**: click "🔄 刷新"
- **图例切换**: click 图例项（输入 Tokens/输出 Tokens 等）隐藏/显示数据系列
- **读取数据**: evaluate `document.querySelectorAll("#model-table tbody tr").length`

### 模型管理

- **新增上游**: click "+ 新增上游" → 填写模态框（名称/URL/Key/超时/SSL/重试/格式）→ click 保存
- **测试连通**: click 上游行 "测试" 按钮 → 处理 alert 弹窗
- **展开 Drawer**: click 上游行（整行可点击）→ 展开模型列表
- **检测模型**: Drawer 内 click "🔍 检测模型" → wait_for 3s → 模态框出现
- **禁用上游**: click "禁用" → 处理 confirm 弹窗

### 路由映射

- **切换请求类型**: click "Responses"/"Messages"/"Chat Completions" Tab
- **新增路由**: click "+ 新增路由" → 填写源模型名 → 选择目标模型 → 保存
- **新增回退路由**: click "+ 新增回退路由"（源模型自动为 `*`，只读）

### 数据库查询

- **快捷查询**: click 快捷按钮 → SQL 自动填入（不自动执行）→ click "▶ 执行查询"
- **手动输入**: type 到 `#sql-editor` → click 执行
- **读取结果**: snapshot 或 evaluate `document.querySelectorAll(".dbquery-table tr")`

## 全局操作

- **设置**: click "⚙" 按钮 → 模态框含默认页面/周期选择
- **主题切换**: click "🌙"/"🌕" 按钮
- **关闭模态框**: click "×" 或 "取消" 或 press_key Escape

## 验证前端改动的标准流程

```
1. browser_take_screenshot({ filename: "before.png" })        // 改动前
2. 修改代码 → ./server.sh restart                              // 改动
3. browser_navigate({ url: "http://localhost:18742" })         // 刷新
4. 导航到目标页面 → browser_take_screenshot({ filename: "after.png" })  // 改动后
5. 用 evaluate 检查数据是否正确渲染
6. browser_console_messages() 排查 JS 错误
```

## 详细 API 与选择器参考

DOM 选择器、API 端点、全局变量等详细信息见 `references/api-selectors.md`。
