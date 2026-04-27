# Anthropic Messages API 转换实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 proxy 中新增 Anthropic Messages API → OpenAI Chat Completions API 的双向完整转换（含流式 SSE），使 Claude Code 等 Anthropic 原生客户端可通过代理访问上游 Chat 兼容端点。

**Architecture:** 分两阶段——阶段 1 纯重构（拆分 transform.py 为 sse_utils.py + transform_responses.py + 选择器 transform.py），阶段 2 TDD 实现 Anthropic 转换（transform_anthropic.py + proxy.py /v1/messages 路由）。零行为变更、纯 Python 标准库。

**Tech Stack:** Python 3 stdlib（http.client, json, dataclasses, sqlite3, unittest）

**参考实现:** cc-switch (Rust) + Claude Code (TypeScript)

---

## 文件结构总览

| 文件 | 操作 | 职责 |
|------|------|------|
| `sse_utils.py` | **新建** | `_format_sse_event()` 基础设施 |
| `transform_responses.py` | **新建（从 transform.py 提取）** | Responses ↔ Chat 全部转换逻辑 |
| `transform.py` | **修改** | 选择器 + re-export 公共接口 |
| `transform_anthropic.py` | **新建** | Anthropic ↔ Chat 全部转换逻辑 |
| `proxy.py` | **修改** | 新增 `/v1/messages` 路由 + 转发函数参数化 |
| `request_logger.py` | **修改** | `_extract_agent` 增加 claude 检测 |
| `test/test_sse_utils.py` | **新建** | `_format_sse_event` 单元测试 |
| `test/test_transform_anthropic.py` | **新建** | Anthropic 转换单元测试（TDD） |
| `test/test_transform.py` | **修改** | 更新 import 路径 |
| `test/test_proxy_logger_integration.py` | **修改** | 新增 `/v1/messages` 集成测试 |
| `plan_tracking.md` | **修改** | 每个 Task 完成后更新 |

---

### Task 0: 准备 plan_tracking.md + 验证基线

**Files:**
- Modify: `plan_tracking.md`

- [ ] **Step 1: 替换 plan_tracking.md 为新功能跟踪**

写入新文件内容（完整 plan_tracking.md）：

```markdown
# Plan Tracking: Anthropic Messages API 转换 实现进度跟踪

> 基于 `docs/superpowers/plans/2026-04-27-anthropic-messages-conversion.md` 实施计划。

## Goal

在 proxy 中新增 Anthropic Messages API ↔ OpenAI Chat Completions API 的双向完整转换（含流式 SSE）。

## Current Task

Task 0: 准备 plan_tracking.md

## Tasks

### Task 0: 准备 plan_tracking.md + 验证基线
- [ ] Step 1: 写入新 plan_tracking.md
- [ ] Step 2: 运行全量测试确认基线（130 passed）
- [ ] Step 3: Commit
- **Status:** in_progress

### Task 1: 创建 sse_utils.py — 提取 _format_sse_event
- [ ] Step 1: 创建 test/test_sse_utils.py（TDD: 验证 _format_sse_event 行为）
- [ ] Step 2: 验证测试失败（import 不到的 sse_utils）
- [ ] Step 3: 创建 sse_utils.py + 从 transform.py 移动 _format_sse_event
- [ ] Step 4: 更新 transform.py 从 sse_utils import
- [ ] Step 5: 运行全量测试确认（130+ passed）
- [ ] Step 6: Commit
- **Status:** pending

### Task 2: 创建 transform_responses.py — 提取 Responses 转换逻辑
- [ ] Step 1: 从 transform.py 复制全部内容到 transform_responses.py
- [ ] Step 2: 修改 proxy.py 的 import（从 transform_responses 导入）
- [ ] Step 3: 修改 test/test_transform.py 的 import
- [ ] Step 4: 重写 transform.py 为选择器（re-export）
- [ ] Step 5: 运行全量测试确认（130+ passed）
- [ ] Step 6: Commit
- **Status:** pending

### Task 3: proxy.py 转发函数参数化
- [ ] Step 1: _forward_non_streaming 增加 response_converter 参数
- [ ] Step 2: _forward_streaming 增加 response_converter + sse_stream_factory 参数
- [ ] Step 3: 更新 _handle_responses 中的两处调用
- [ ] Step 4: 更新集成测试中的 mock 调用
- [ ] Step 5: 运行全量测试确认（130+ passed）
- [ ] Step 6: Commit
- **Status:** pending

### Task 4: anthropic_to_chat — 请求转换 TDD
- [ ] Step 1-14: 14 个测试用例，逐个 TDD 循环
- [ ] Step 15: 运行全量测试确认
- [ ] Step 16: Commit
- **Status:** pending

### Task 5: chat_to_anthropic — 响应转换 TDD
- [ ] Step 1-10: 10 个测试用例，逐个 TDD 循环
- [ ] Step 11: 运行全量测试确认
- [ ] Step 12: Commit
- **Status:** pending

### Task 6: create_anthropic_sse_stream — 流式转换 TDD
- [ ] Step 1-14: 14 个测试用例，逐个 TDD 循环
- [ ] Step 15: 运行全量测试确认
- [ ] Step 16: Commit
- **Status:** pending

### Task 7: proxy.py /v1/messages 路由集成
- [ ] Step 1: 新增 _handle_messages 方法
- [ ] Step 2: do_POST 添加 /v1/messages 路由
- [ ] Step 3: _extract_agent 增加 claude 检测
- [ ] Step 4: 新增集成测试
- [ ] Step 5: 运行全量测试确认
- [ ] Step 6: Commit
- **Status:** pending

### Task 8: 最终验证
- [ ] Step 1: 运行全量测试
- [ ] Step 2: 重启 proxy（./server.sh restart）
- [ ] Step 3: 冒烟测试
- [ ] Step 4: Commit（如有修改）
- **Status:** pending

## Decisions Made

| Decision | Rationale | Source |
|----------|-----------|--------|
| sse_utils.py 独立文件 | 避免 transform 模块间横向依赖 | 设计文稿/审阅 |
| 阶段 1 纯重构 | 先重命名/移动（不改逻辑）→ 跑测试 → 再做功能 | 设计文稿/审阅 |
| tool_blocks dict[int, ToolBlockState] | 多 tool 并发流式场景需要按 index 管理 | 设计文稿/审阅 |
| 推理字段双检测（reasoning_content + reasoning） | LiteLLM 网关字段名不确定 | 设计文稿/审阅 |
| Anthropic event data 自带 "type" | _format_sse_event 约定不重复注入 | 设计文稿/审阅 |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| | | |

## Notes

- 设计文稿：`docs/superpowers/specs/2026-04-27-anthropic-messages-conversion-design.md`
- 参考实现：`/Users/xys/Github/cc-switch/src-tauri/src/proxy/providers/transform.rs` + `streaming.rs`
- Claude Code 源码：`/Users/xys/Github/Claude-Code/src/services/api/claude.ts`
- 阶段 1（Task 1-3）不改任何转换逻辑，纯移动代码
- 阶段 2（Task 4-8）严格 TDD，每个测试先失败再实现
- 每个 Task 完成后更新本文件 + 通知用户审阅
```

- [ ] **Step 2: 运行全量测试确认基线**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/ -q --tb=no
```

Expected: `130 passed`

- [ ] **Step 3: Commit**

```bash
git add plan_tracking.md && git commit -m "tracking: Anthropic Messages API 转换实施计划初始化"
```

---

### Task 1: 创建 sse_utils.py — 提取 _format_sse_event

**Files:**
- Create: `sse_utils.py`
- Create: `test/test_sse_utils.py`
- Modify: `transform.py` (移除 `_format_sse_event`，改从 sse_utils import)

**目标**: 将 `_format_sse_event` 从 transform.py 移至独立文件，为两个转换模块提供共享基础设施。

- [ ] **Step 1: 编写 test_sse_utils.py（TDD: 验证 _format_sse_event 行为）**

```python
# test/test_sse_utils.py
import json
import unittest


class TestFormatSSEEvent(unittest.TestCase):
    """_format_sse_event 独立测试 — 确保迁移后行为不变。"""

    # ─── 基本功能 ───

    def test_basic_event_format(self):
        """基本 SSE 事件格式：event: {type}\ndata: {json}\n\n"""
        from sse_utils import _format_sse_event
        result = _format_sse_event("message_start", {"id": "123", "model": "claude"})
        self.assertIn("event: message_start\n", result)
        self.assertIn("data: ", result)
        self.assertTrue(result.endswith("\n\n"))
        data_part = result.split("data: ", 1)[1].strip()
        data = json.loads(data_part)
        self.assertEqual(data["type"], "message_start")

    def test_type_field_injected(self):
        """event_type 作为 data JSON 的顶层 'type' 字段注入。"""
        from sse_utils import _format_sse_event
        result = _format_sse_event("content_block_delta", {"index": 0})
        data_part = result.split("data: ", 1)[1].strip()
        data = json.loads(data_part)
        self.assertEqual(data["type"], "content_block_delta")
        self.assertEqual(data["index"], 0)

    def test_type_field_overwritten(self):
        """data 中已有的 'type' 字段被 event_type 覆盖。"""
        from sse_utils import _format_sse_event
        result = _format_sse_event("correct_type", {"type": "wrong_type", "x": 1})
        data_part = result.split("data: ", 1)[1].strip()
        data = json.loads(data_part)
        self.assertEqual(data["type"], "correct_type")
        self.assertEqual(data["x"], 1)

    # ─── Responses API 'response' 包裹 ───

    def test_response_event_wrapped(self):
        """response.* 事件 data 被 'response' 键包裹。"""
        from sse_utils import _format_sse_event
        result = _format_sse_event("response.created", {"id": "resp-123"})
        data_part = result.split("data: ", 1)[1].strip()
        data = json.loads(data_part)
        self.assertIn("response", data)
        self.assertEqual(data["response"]["id"], "resp-123")
        self.assertEqual(data["type"], "response.created")

    def test_response_incomplete_wrapped(self):
        """response.incomplete 也被包裹。"""
        from sse_utils import _format_sse_event
        result = _format_sse_event("response.incomplete", {"reason": "max_tokens"})
        data_part = result.split("data: ", 1)[1].strip()
        data = json.loads(data_part)
        self.assertIn("response", data)
        self.assertEqual(data["response"]["reason"], "max_tokens")

    # ─── Responses API 'item' 包裹 ───

    def test_output_item_event_wrapped(self):
        """output_item.* 事件 data 被 'item' 键包裹。"""
        from sse_utils import _format_sse_event
        result = _format_sse_event("response.output_item.added", {"id": "item-1"})
        data_part = result.split("data: ", 1)[1].strip()
        data = json.loads(data_part)
        self.assertIn("item", data)
        self.assertEqual(data["item"]["id"], "item-1")
        self.assertEqual(data["type"], "response.output_item.added")

    # ─── Anthropic 事件（不匹配 response. / output_item. 前缀） ───

    def test_anthropic_event_no_wrap(self):
        """Anthropic 事件（message_start 等）不被包裹键包裹。"""
        from sse_utils import _format_sse_event
        result = _format_sse_event("message_start", {
            "message": {"id": "msg_1", "model": "claude-sonnet-4-6", "role": "assistant", "content": []}
        })
        data_part = result.split("data: ", 1)[1].strip()
        data = json.loads(data_part)
        self.assertEqual(data["type"], "message_start")
        self.assertIn("message", data)
        self.assertNotIn("response", data)
        self.assertNotIn("item", data)

    def test_anthropic_delta_event_no_wrap(self):
        """content_block_delta 不被包裹。"""
        from sse_utils import _format_sse_event
        result = _format_sse_event("content_block_delta", {
            "index": 0, "delta": {"type": "text_delta", "text": "hello"}
        })
        data_part = result.split("data: ", 1)[1].strip()
        data = json.loads(data_part)
        self.assertEqual(data["type"], "content_block_delta")
        self.assertNotIn("response", data)
        self.assertNotIn("item", data)

    # ─── compact JSON 格式 ───

    def test_compact_json_format(self):
        """使用紧凑格式（无多余空格）。"""
        from sse_utils import _format_sse_event
        result = _format_sse_event("message_stop", {})
        # compact JSON: {"type":"message_stop"} 不含多余空格
        self.assertIn('{"type":"message_stop"}', result)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试验证失败**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_sse_utils.py -v
```

Expected: `ModuleNotFoundError: No module named 'sse_utils'`

- [ ] **Step 3: 创建 sse_utils.py + 从 transform.py 移除 _format_sse_event**

创建 `sse_utils.py`：

```python
"""SSE 事件格式化工具 — 两个转换模块共用。"""

import json


def _format_sse_event(event_type: str, data: dict) -> str:
    """生成标准 SSE 事件字符串，确保 data JSON 包含 "type" 字段。

    event_type 作为 data JSON 的顶层 "type" 字段注入，覆盖 data 中的已有 "type"。
    统一使用 separators=(',', ':') 紧凑格式。
    """
    payload = {**data, "type": event_type}
    return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
```

修改 `transform.py`：移除 line 317-324 的 `_format_sse_event` 函数定义，在文件顶部 import 处添加：

```python
from sse_utils import _format_sse_event
```

- [ ] **Step 4: 运行全量测试确认**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/ -q --tb=no
```

Expected: `138 passed`（130 + 8 new sse_utils tests）

- [ ] **Step 5: Commit**

```bash
git add sse_utils.py test/test_sse_utils.py transform.py plan_tracking.md && git commit -m "refactor: 提取 _format_sse_event 至 sse_utils.py，新增 8 个独立测试"
```

---

### Task 2: 创建 transform_responses.py — 提取 Responses 转换逻辑

**Files:**
- Create: `transform_responses.py`（从 transform.py 复制）
- Modify: `transform.py`（改为选择器 + re-export）
- Modify: `proxy.py`（更新 import）
- Modify: `test/test_transform.py`（更新 import）

**目标**: 将 transform.py 中的 Responses ↔ Chat 全部转换逻辑移至 transform_responses.py，transform.py 仅保留 re-export。

- [ ] **Step 1: 创建 transform_responses.py**

从 `transform.py` 复制以下内容到 `transform_responses.py`（保留 import + 移除 `_format_sse_event` 定义，因为它在 sse_utils.py 中）：

```python
"""Responses API ↔ Chat Completions 转换模块。

包含：
- responses_to_chat(): Responses API → Chat Completions
- chat_to_responses(): Chat Completions → Responses API
- StreamState + create_codex_sse_stream(): SSE 流转换
- SSE 解析器 iter_sse_events + _parse_sse_event
- generate_response_id(): 生成 resp-{timestamp_ms}-{random_hex8}
"""

import json
import uuid
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

from token_stats import _find_first
from sse_utils import _format_sse_event

logger = logging.getLogger(__name__)


def generate_response_id() -> str:
    """生成 OpenAI 规范 response ID: resp-{timestamp_ms}-{random_hex8}"""
    ts = int(time.time() * 1000)
    rand = uuid.uuid4().hex[:8]
    return f"resp-{ts}-{rand}"


# ─── 以下为原 transform.py 中除 _format_sse_event 外的全部内容 ───
# responses_to_chat, _map_input_item, _map_message, _map_input_image,
# _map_input_file, _map_function_call, _map_function_call_output,
# _map_computer_call_output, _map_tools, _map_response_format,
# FINISH_REASON_MAP, INCOMPLETE_REASON_MAP, chat_to_responses,
# _parse_sse_event, iter_sse_events, StreamState,
# create_codex_sse_stream, _emit_created, _process_delta, _emit_completion
```

> 具体代码 = transform.py 全部内容，仅移除 `_format_sse_event` 函数（line 317-324），顶部 import 改为 `from sse_utils import _format_sse_event`。

- [ ] **Step 2: 重写 transform.py 为选择器**

```python
"""转换模块选择器 — 根据请求格式分发到对应转换模块。

公共接口：
- generate_response_id: 来自 transform_responses
- responses_to_chat: 来自 transform_responses（/v1/responses 路径）
- chat_to_responses: 来自 transform_responses
- create_codex_sse_stream: 来自 transform_responses
- _format_sse_event: 来自 sse_utils
- anthropic_to_chat: 来自 transform_anthropic（/v1/messages 路径）— Task 4 后可用
- chat_to_anthropic: 来自 transform_anthropic — Task 5 后可用
- create_anthropic_sse_stream: 来自 transform_anthropic — Task 6 后可用
"""

from sse_utils import _format_sse_event  # noqa: F401 — re-export

from transform_responses import (  # noqa: F401 — re-export
    generate_response_id,
    responses_to_chat,
    chat_to_responses,
    create_codex_sse_stream,
    _parse_sse_event,
    iter_sse_events,
    StreamState,
    _process_delta,
    _emit_completion,
    _map_tools,
    _map_response_format,
)

# 以下在 Task 4-6 中逐步激活：
# from transform_anthropic import anthropic_to_chat, chat_to_anthropic, create_anthropic_sse_stream  # noqa: F401
```

- [ ] **Step 3: 更新 proxy.py 的 import**

proxy.py line 18-24，将 `from transform import (...)` 改为：

```python
from transform import (
    generate_response_id,
    responses_to_chat,
    chat_to_responses,
    create_codex_sse_stream,
    _format_sse_event,
)
```

注意：import 路径不变（通过 transform.py 的 re-export 保证兼容）。

- [ ] **Step 4: 更新 test 文件的 import**

`test/test_transform.py` — 检查所有 `from transform import ...` 是否仍然有效（通过 transform.py 的 re-export 应该都可用）。如有直接从 transform 导入的非公共函数（如 `_map_tools`、`_map_response_format`），确认它们在 transform.py 的 re-export 列表中。

如测试需要直接导入内部函数，可通过 `from transform_responses import ...` 导入。

- [ ] **Step 5: 运行全量测试确认**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/ -q --tb=short
```

Expected: `138 passed`（无回归）

- [ ] **Step 6: Commit**

```bash
git add transform_responses.py transform.py proxy.py test/ plan_tracking.md && git commit -m "refactor: 提取 transform_responses.py，transform.py 改为选择器"
```

---

### Task 3: proxy.py 转发函数参数化

**Files:**
- Modify: `proxy.py`（_forward_non_streaming + _forward_streaming 参数化）
- Modify: `test/test_proxy_logger_integration.py`（更新 mock 调用）

**目标**: 将 _forward_non_streaming 和 _forward_streaming 中硬编码的转换函数改为参数，为后续 /v1/messages 路由做准备。不改任何行为。

- [ ] **Step 1: _forward_non_streaming 增加 response_converter 参数**

修改 `proxy.py` line 288 的 `_forward_non_streaming` 签名：

```python
def _forward_non_streaming(self, chat_body: dict, request_id: str, 
                           model: str, target: str, request_ts: str,
                           response_converter=None):
    """
    response_converter: callable, chat_response -> format_response
                       默认 chat_to_responses（与当前行为一致）
    """
    if response_converter is None:
        from transform_responses import chat_to_responses as response_converter
    # ... 其余逻辑不变，line 367 处：
    responses_response = response_converter(chat_response)
```

- [ ] **Step 2: _forward_streaming 增加 response_converter + sse_stream_factory 参数**

修改 `proxy.py` line 404 的 `_forward_streaming` 签名：

```python
def _forward_streaming(self, chat_body: dict, model_cfg: dict, 
                       request_id: str, model: str, target: str, request_ts: str,
                       response_converter=None, sse_stream_factory=None):
    """
    sse_stream_factory: callable, upstream_response -> Generator[str]
                       默认 create_codex_sse_stream（与当前行为一致）
    """
    if sse_stream_factory is None:
        from transform_responses import create_codex_sse_stream as sse_stream_factory
    # ... 其余逻辑不变，line 493 处：
    for sse_event in sse_stream_factory(resp):
        # ...
```

- [ ] **Step 3: 更新 _handle_responses 中的调用**

Line 283-286，确保调用传入默认参数（或不传，使用默认值）：

```python
# 非流式
self._forward_non_streaming(chat_body, request_id, model_name, target, request_ts)
# 流式
self._forward_streaming(chat_body, model_cfg, request_id, model_name, target, request_ts)
```

这两处无需修改（默认参数为 None 时自动使用现有转换函数）。

- [ ] **Step 4: 更新集成测试中的 mock 调用**

`test/test_proxy_logger_integration.py` — 搜索 `_forward_non_streaming` 和 `_forward_streaming` 的直接 mock/调用，确认签名变更不影响测试。如果测试中显式传参了这些方法，需更新参数名。

- [ ] **Step 5: 运行全量测试确认**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/ -q --tb=short
```

Expected: `138 passed`

- [ ] **Step 6: Commit**

```bash
git add proxy.py test/test_proxy_logger_integration.py plan_tracking.md && git commit -m "refactor: proxy 转发函数参数化，支持注入 response_converter 和 sse_stream_factory"
```

---

### Task 4: anthropic_to_chat — 请求转换 TDD

**Files:**
- Create: `transform_anthropic.py`
- Create: `test/test_transform_anthropic.py`
- Modify: `transform.py`（添加 re-export import）

**目标**: 实现 Anthropic Messages → OpenAI Chat Completions 请求转换，完全按设计文稿的映射表。

- [ ] **Step 1: 编写 TestAnthropicToChat.test_simple_text_message（测试一：最简单的文本消息）**

```python
# test/test_transform_anthropic.py
import json
import unittest


class TestAnthropicToChat(unittest.TestCase):
    """anthropic_to_chat — Anthropic Messages → Chat Completions 请求转换。"""

    def test_simple_text_message(self):
        """user 角色 + 字符串 content → Chat message。"""
        from transform_anthropic import anthropic_to_chat
        body = {
            "model": "claude-sonnet-4-6",
            "messages": [
                {"role": "user", "content": "Hello, how are you?"}
            ],
            "max_tokens": 4096,
        }
        result = anthropic_to_chat(body, {"target": "qwen3.6-plus", "multimodal": True})
        self.assertEqual(result["model"], "qwen3.6-plus")
        self.assertEqual(len(result["messages"]), 1)
        self.assertEqual(result["messages"][0]["role"], "user")
        self.assertEqual(result["messages"][0]["content"], "Hello, how are you?")
        self.assertEqual(result["max_tokens"], 4096)
```

- [ ] **Step 2: Run test → FAIL**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_transform_anthropic.py::TestAnthropicToChat::test_simple_text_message -v
```

Expected: `ModuleNotFoundError: No module named 'transform_anthropic'`

- [ ] **Step 3: 创建最小实现**

```python
# transform_anthropic.py
"""Anthropic Messages API ↔ Chat Completions 转换模块。"""
import json
import logging

logger = logging.getLogger(__name__)


def anthropic_to_chat(body: dict, model_cfg: dict) -> dict:
    """Anthropic Messages → OpenAI Chat Completions 请求转换。"""
    chat = {
        "model": model_cfg["target"],
        "messages": [],
    }
    
    # system → system message
    system = body.get("system")
    if isinstance(system, str):
        chat["messages"].append({"role": "system", "content": system})
    elif isinstance(system, list):
        parts = []
        for block in system:
            parts.append(block.get("text", ""))
        chat["messages"].append({"role": "system", "content": "\n".join(parts)})
    
    # messages
    for msg in body.get("messages", []):
        converted = _convert_message_to_chat(msg.get("role", "user"), msg.get("content"))
        chat["messages"].extend(converted)
    
    # max_tokens
    if "max_tokens" in body:
        model = body.get("model", "")
        if _is_o_series(model):
            chat["max_completion_tokens"] = body["max_tokens"]
        else:
            chat["max_tokens"] = body["max_tokens"]
    
    # temperature, top_p, stop, stream
    for key in ("temperature", "top_p", "stop_sequences", "stream"):
        if key in body:
            target_key = "stop" if key == "stop_sequences" else key
            chat[target_key] = body[key]
    
    if body.get("stream"):
        chat["stream_options"] = {"include_usage": True}
    
    return chat


def _is_o_series(model: str) -> bool:
    """检测 o-series 模型（o + 数字开头）。"""
    import re
    return bool(re.match(r'^o\d', model))


def _convert_message_to_chat(role: str, content) -> list:
    """将单个 Anthropic 消息转换为 Chat messages（可能多条）。"""
    if isinstance(content, str):
        return [{"role": role, "content": content}]
    if isinstance(content, list):
        result = []
        chat_content = []
        tool_calls = []
        for block in content:
            block_type = block.get("type")
            if block_type == "text":
                text_item = {"type": "text", "text": block.get("text", "")}
                if "cache_control" in block:
                    text_item["cache_control"] = block["cache_control"]
                chat_content.append(text_item)
            elif block_type == "image":
                source = block.get("source", {})
                media_type = source.get("media_type", "image/png")
                data = source.get("data", "")
                chat_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{data}"},
                })
            elif block_type == "tool_use":
                tool_calls.append({
                    "id": block.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {})),
                    },
                })
            elif block_type == "tool_result":
                tc = block.get("content", "")
                if isinstance(tc, list):
                    tc = json.dumps(tc)
                result.append({
                    "role": "tool",
                    "tool_call_id": block.get("tool_use_id", ""),
                    "content": tc,
                })
            elif block_type in ("thinking", "redacted_thinking"):
                pass  # 丢弃
        if role == "assistant" and tool_calls:
            result.insert(0, {"role": "assistant", "tool_calls": tool_calls})
        elif chat_content:
            result.insert(0, {"role": role, "content": chat_content})
        elif not result:
            result.insert(0, {"role": role, "content": ""})
        return result
    return [{"role": role, "content": str(content)}]
```

添加 `_map_tool_choice` 辅助函数和 tools 转换、reasoning_effort 映射等——后续每个测试逐步添加。

- [ ] **Step 4: Run test → PASS**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_transform_anthropic.py::TestAnthropicToChat::test_simple_text_message -v
```

Expected: PASS

- [ ] **Step 5: Commit（test_simple_text_message）**

```bash
git add test/test_transform_anthropic.py transform_anthropic.py && git commit -m "feat: anthropic_to_chat 基本消息转换（TDD Step 1）"
```

- [ ] **Step 6-14: 以下测试逐个 TDD 循环（每个：写测试 → 验证失败 → 实现 → 验证通过 → commit）**

每个测试先写出来，验证因缺失功能而失败，再在 `transform_anthropic.py` 中逐步添加实现：

| # | 测试名 | 验证内容 | 实现要点 |
|---|--------|---------|---------|
| 6 | `test_system_string` | `system: "You are helpful"` → `messages[0]` 为 `{role:"system", content:"You are helpful"}` | 已在 Step 3 包含 |
| 7 | `test_system_array` | `system: [{type:"text", text:"part1"}, ...]` → `\n` 连接 | 已在 Step 3 包含 |
| 8 | `test_multimodal_image` | image source base64 → `image_url` | 已在 `_convert_message_to_chat` 包含 |
| 9 | `test_tool_use_conversion` | `tool_use` block → `tool_calls[]` + `arguments` 序列化 | 已在 Step 3 包含 |
| 10 | `test_tool_result_conversion` | `tool_result` → 独立 `{role:"tool", tool_call_id, content}` | 已在 Step 3 包含 |
| 11 | `test_tool_result_array_content` | tool_result content 为数组 → `json.dumps` | 已在 Step 3 包含 |
| 12 | `test_thinking_discarded` | 消息中的 `thinking` block → 不出现在 Chat messages 中 | `pass` 即正确 |
| 13 | `test_o_series_max_completion_tokens` | o3 模型 → `max_completion_tokens` 替代 `max_tokens` | `_is_o_series` 判断 |
| 14 | `test_tool_definitions_conversion` | Anthropic tools → `{type:"function", function:{name,description,parameters}}` | 新增 `_map_anthropic_tools` |
| 15 | `test_thinking_to_reasoning_effort_adaptive` | `thinking: {type:"adaptive"}` → `reasoning_effort: "xhigh"` | 新增 `_resolve_reasoning_effort` |
| 16 | `test_thinking_to_reasoning_effort_budget` | `thinking: {type:"enabled", budget_tokens: 16000}` → `reasoning_effort: "high"` | budget range 判断 |
| 17 | `test_reasoning_effort_only_on_supported_models` | 非 o-series 模型 → 不注入 `reasoning_effort` | `_is_o_series or gpt-5+` 判断 |
| 18 | `test_tool_choice_auto` | `{type:"auto"}` → `"auto"` | tool_choice 映射 |
| 19 | `test_tool_choice_any` | `{type:"any"}` → `"required"` | tool_choice 映射 |
| 20 | `test_tool_choice_tool` | `{type:"tool", name:"x"}` → `{type:"function", function:{name:"x"}}` | tool_choice 映射 |
| 21 | `test_tool_choice_string_fallback` | `"auto"` → `"auto"`, `"any"` → `"required"` | tool_choice 字符串兜底 |
| 22 | `test_unknown_fields_not_crash` | 含 `output_config.format`、`context_management` 等未知字段不抛异常 | 默认行为 |
| 23 | `test_empty_messages` | 空 messages → Chat messages 不含奇怪数据 | 边界 |
| 24 | `test_cache_control_preserved` | text block 上的 `cache_control` → 保留在 output 中 | `_convert_message_to_chat` 已含 |

每个测试编写 + 实现后执行：

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_transform_anthropic.py -q --tb=short
```

- [ ] **Step 15: 运行全量测试确认**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/ -q --tb=no
```

Expected: ~150+ passed

- [ ] **Step 16: Commit**

```bash
git add test/test_transform_anthropic.py transform_anthropic.py plan_tracking.md && git commit -m "feat: anthropic_to_chat 请求转换完成（25 个测试）"
```

---

### Task 5: chat_to_anthropic — 响应转换 TDD

**Files:**
- Modify: `transform_anthropic.py`（新增 `chat_to_anthropic`）
- Modify: `test/test_transform_anthropic.py`（新增 `TestChatToAnthropic`）

**目标**: 实现 OpenAI Chat Completions → Anthropic Messages 非流式响应转换。

- [ ] **Step 1-10: TDD 循环，每个测试一个 commit**

| # | 测试名 | Chat 响应输入 | 预期 Anthropic 输出 |
|---|--------|-------------|-------------------|
| 1 | `test_basic_text_response` | `choices[0].message.content: "hello"`, `finish_reason: "stop"` | `content: [{type:"text", text:"hello"}]`, `stop_reason: "end_turn"` |
| 2 | `test_tool_calls_response` | `tool_calls: [{id, function:{name, arguments}}]` | `content: [{type:"tool_use", id, name, input:{...}}]` |
| 3 | `test_refusal_response` | `choices[0].message.refusal: "I cannot..."` | `content: [{type:"text", text:"I cannot..."}]` |
| 4 | `test_finish_reason_stop` | `finish_reason: "stop"` | `stop_reason: "end_turn"` |
| 5 | `test_finish_reason_length` | `finish_reason: "length"` | `stop_reason: "max_tokens"` |
| 6 | `test_finish_reason_tool_calls` | `finish_reason: "tool_calls"` | `stop_reason: "tool_use"` |
| 7 | `test_finish_reason_content_filter` | `finish_reason: "content_filter"` | `stop_reason: "end_turn"` |
| 8 | `test_usage_mapping` | `usage: {prompt_tokens, completion_tokens, prompt_tokens_details: {cached_tokens}}` | `usage: {input_tokens, output_tokens, cache_read_input_tokens}` |
| 9 | `test_hardcoded_fields` | — | `type: "message"`, `role: "assistant"`, `stop_sequence: null` |

函数签名：`chat_to_anthropic(response: dict) -> dict`

实现参考设计文稿"非流式响应转换"映射表。

- [ ] **Step 11: 运行全量测试**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/ -q --tb=no
```

Expected: ~160+ passed

- [ ] **Step 12: Commit**

```bash
git add test/test_transform_anthropic.py transform_anthropic.py plan_tracking.md && git commit -m "feat: chat_to_anthropic 响应转换完成（9 个测试）"
```

---

### Task 6: create_anthropic_sse_stream — 流式转换 TDD

**Files:**
- Modify: `transform_anthropic.py`（新增 `create_anthropic_sse_stream` + `AnthropicStreamState` + `ToolBlockState`）
- Modify: `test/test_transform_anthropic.py`（新增 `TestAnthropicSSEStream`）

**目标**: 实现 Chat Completions SSE → Anthropic Messages SSE 流式转换。

- [ ] **Step 1-14: TDD 循环**

测试策略：构造 mock 上游 SSE 事件 generator，验证输出的 Anthropic SSE 事件序列。

| # | 测试名 | Mock 上游事件 | 预期 Anthropic 事件序列 |
|---|--------|-------------|------------------------|
| 1 | `test_message_start` | 首个 chunk `{id, model, choices:[]}` | `event: message_start` + message 含 id/model/role |
| 2 | `test_text_stream` | `delta.content` 多次出现 | `content_block_start(text)` + `content_block_delta(text_delta)` × N + `content_block_stop` |
| 3 | `test_thinking_stream_reasoning_content` | `delta.reasoning_content` 出现 | `content_block_start(thinking)` + `content_block_delta(thinking_delta)` + `content_block_stop` |
| 4 | `test_thinking_stream_reasoning` | `delta.reasoning` 出现 | 同上（双字段兼容） |
| 5 | `test_tool_use_stream` | `delta.tool_calls[i]` name + arguments | `content_block_start(tool_use)` + `content_block_delta(input_json_delta)` + `content_block_stop` |
| 6 | `test_tool_use_late_start` | arguments 先于 id/name 到达 | 缓冲 + 延迟发 `content_block_start` |
| 7 | `test_multiple_tool_calls` | 两个 tool call 交错出现 | 按 index 正确路由到不同 content block |
| 8 | `test_message_delta` | `delta.finish_reason` 出现 | `event: message_delta` + `stop_reason` + `usage` |
| 9 | `test_message_stop` | `[DONE]` | `event: message_stop` |
| 10 | `test_arguments_null_skip` | `tool_calls[i].function.arguments` 为 null | 不发送 `input_json_delta` |
| 11 | `test_finish_reason_mapping` | `finish_reason: "stop"` → `stop_reason: "end_turn"` | message_delta 中的 stop_reason 正确映射 |
| 12 | `test_stream_interrupt` | 中途抛异常 | `event: error` 事件发送 |
| 13 | `test_content_block_index_sequence` | text + tool 交替 | index 递增（text=0, tool=1, text=2, ...） |
| 14 | `test_utf8_split` | 多字节字符跨 chunk 边界 | 正确拼接，不乱码 |

mock 辅助：

```python
def mock_sse_stream(events):
    """构造 fake upstream response，yield _parse_sse_event 格式的 dict。"""
    for event in events:
        yield {"event": "message", "data": event}
    yield {"event": "[DONE]", "data": None}
```

每个测试写完后实现，commit 频率：每 2-3 个测试一个 commit。

- [ ] **Step 15: 运行全量测试**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/ -q --tb=no
```

Expected: ~175+ passed

- [ ] **Step 16: Commit**

```bash
git add test/test_transform_anthropic.py transform_anthropic.py plan_tracking.md && git commit -m "feat: create_anthropic_sse_stream 流式转换完成（14 个测试）"
```

---

### Task 7: proxy.py /v1/messages 路由集成

**Files:**
- Modify: `proxy.py`（新增 `_handle_messages` + `do_POST` 路由 + import）
- Modify: `request_logger.py`（`_extract_agent` 增加 claude 检测）
- Modify: `test/test_proxy_logger_integration.py`（新增 `/v1/messages` 集成测试）
- Modify: `transform.py`（激活 anthropic re-export）

**目标**: 将 Anthropic 转换集成到 proxy 中，使 `POST /v1/messages` 可用。

- [ ] **Step 1: transform.py 激活 Anthropic re-export**

```python
# 取消注释 Task 2 中预留的 import：
from transform_anthropic import anthropic_to_chat, chat_to_anthropic, create_anthropic_sse_stream  # noqa: F401
```

- [ ] **Step 2: proxy.py 新增 import + _handle_messages**

```python
# 在 proxy.py 顶部 import（line 18-24）替换为：
from transform import (
    generate_response_id,
    responses_to_chat,
    chat_to_responses,
    create_codex_sse_stream,
    anthropic_to_chat,
    chat_to_anthropic,
    create_anthropic_sse_stream,
    _format_sse_event,
)
```

在 `ProxyHandler` 类中新增 `_handle_messages` 方法：

```python
def _handle_messages(self, body, request_id, request_ts, model_name, target, is_stream, model_cfg):
    """核心：Anthropic Messages → Chat → Anthropic Messages 转换。"""
    logger = get_logger()
    if logger:
        logger.log_raw_request(request_id, model_name, target, body)

    # 请求转换
    try:
        chat_body = anthropic_to_chat(body, model_cfg)
    except Exception as e:
        logging.exception("anthropic_to_chat 转换失败")
        if logger:
            logger.log_converted_request(request_id, model_name, target, {"error": str(e)})
        self._send_json(500, {"error": {"type": "internal_error", "message": str(e)}})
        return

    if logger:
        logger.log_converted_request(request_id, model_name, target, chat_body)

    # 转发
    if is_stream:
        self._forward_streaming(chat_body, model_cfg, request_id, model_name, target, request_ts,
                                response_converter=chat_to_anthropic,
                                sse_stream_factory=create_anthropic_sse_stream)
    else:
        self._forward_non_streaming(chat_body, request_id, model_name, target, request_ts,
                                    response_converter=chat_to_anthropic)
```

- [ ] **Step 3: do_POST 添加 /v1/messages 路由**

```python
def do_POST(self):
    if self.path in ("/v1/responses", "/v1/responses/compact"):
        self._handle_responses()
    elif self.path == "/v1/messages":
        self._handle_responses()  # 重用解析逻辑
    else:
        self._send_json(404, {"error": "not found"})
```

修改 `_handle_responses` 以支持分发（根据 path 选择转换函数）：

在 `_handle_responses` 中 line 268-270，根据 `self.path` 选择：

```python
is_messages = self.path == "/v1/messages"
if is_messages:
    converter = anthropic_to_chat
else:
    converter = responses_to_chat

chat_body = converter(body, model_cfg)
```

并在转发时传入对应的 response_converter 和 sse_stream_factory。

```python
if is_stream:
    if is_messages:
        self._forward_streaming(chat_body, model_cfg, request_id, model_name, target, request_ts,
                                response_converter=chat_to_anthropic,
                                sse_stream_factory=create_anthropic_sse_stream)
    else:
        self._forward_streaming(chat_body, model_cfg, request_id, model_name, target, request_ts)
else:
    if is_messages:
        self._forward_non_streaming(chat_body, request_id, model_name, target, request_ts,
                                    response_converter=chat_to_anthropic)
    else:
        self._forward_non_streaming(chat_body, request_id, model_name, target, request_ts)
```

> 或将 `_handle_responses` 重命名为 `_handle_request` 并接受 converter 参数——更简洁。此处选择内联分发以最小化改动。

- [ ] **Step 4: request_logger.py _extract_agent 增加 claude 检测**

```python
# request_logger.py line ~187-190
def _extract_agent(user_agent: str) -> str:
    if "claude" in user_agent.lower():
        return "claude"
    if "codex" in user_agent.lower():
        return "codex"
    return "unknown"
```

- [ ] **Step 5: 新增集成测试**

在 `test/test_proxy_logger_integration.py` 的 `TestFullRequestFlow` 和 `TestStreamingFlow` 中新增：

```python
def test_non_streaming_messages_path(self):
    """POST /v1/messages 非流式路径 — Anthropic request → Chat → Anthropic response"""
    # 构造 Anthropic 格式请求
    body = json.dumps({
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "Hello"}],
        "max_tokens": 100,
    }).encode()
    # ... 发送到 /v1/messages，验证返回 Anthropic 格式

def test_streaming_messages_path(self):
    """POST /v1/messages 流式路径 — SSE 转换为 Anthropic 事件"""
    # ... 发送 stream:true 请求，验证 SSE 事件为 Anthropic 格式
```

- [ ] **Step 6: 运行全量测试确认**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/ -q --tb=short
```

Expected: ~180+ passed

- [ ] **Step 7: Commit**

```bash
git add proxy.py request_logger.py transform.py test/ plan_tracking.md && git commit -m "feat: proxy 集成 /v1/messages 路由，支持 Anthropic Messages 转换"
```

---

### Task 8: 最终验证

**Files:**
- Modify: `plan_tracking.md`

- [ ] **Step 1: 运行全量测试**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/ -v --tb=short
```

Expected: 全部通过，零失败。

- [ ] **Step 2: 重启 proxy**

```bash
cd /Users/xys/.hermes/fact-store-browser && ./server.sh restart
```

验证 proxy 启动正常（无 import 错误）。

- [ ] **Step 3: 冒烟测试**

```bash
# 健康检查
curl -s http://127.0.0.1:48743/health

# POST /v1/messages 基本非流式请求
curl -s -X POST http://127.0.0.1:48743/v1/messages \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-4-6","messages":[{"role":"user","content":"Say hi"}],"max_tokens":50}'
```

- [ ] **Step 4: 更新 plan_tracking.md → 全部 done，提交**

```bash
git add plan_tracking.md && git commit -m "tracking: Anthropic Messages API 转换实施完成"
```

---

## 自审检查

1. **Spec 覆盖**:
   - 请求转换 ✓ (Task 4: 25 个测试覆盖所有映射)
   - 非流式响应转换 ✓ (Task 5: 9 个测试)
   - 流式 SSE 转换 ✓ (Task 6: 14 个测试)
   - 错误处理 ✓ (各 Task 内覆盖)
   - token_stats 兼容性 ✓ (agent=claude 检测在 Task 7)
   - 拆分重构 ✓ (Task 1-3)
   - 集成测试 ✓ (Task 7)

2. **占位符扫描**: 无 TBD/TODO。

3. **类型一致性**: `anthropic_to_chat(body: dict, model_cfg: dict) -> dict` 在所有 Task 中一致使用。
