# MCP 工具自动执行 Phase 3 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 MCP 工具自动执行能力：非流式模式下，当上游返回 tool_calls 且所有工具均可在 MCP 服务器中找到时，代理自动执行工具并重新请求上游，最多循环 8 轮，最终返回无工具调用的响应。

**Architecture:** 新建 `mcp_manager.py`（MCPManager 类 + execute_mcp_loop 函数）；`proxy.py` 新增 `_forward_mcp()` 方法，`_handle_responses()` 在非流式路径检测 `mcp_manager` 并路由；`main()` 按 `proxy_config.yaml` 的 `mcp` 节初始化 MCPManager；流式路径不变（MCP 不支持流式）。

**Tech Stack:** Python 3.10+, `mcp`（Python SDK，`pip install mcp`）, `asyncio`, `http.client`, `unittest.mock`, `pytest`

> **依赖前置条件**：Phase 1 和 Phase 2 已完成（`_output_items_to_messages`、`_store_response`、`ResponseStore` 均已实现）。

---

## 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| Create | `mcp_manager.py` | MCPManager（工具注册表 + MCP SDK 调用）+ execute_mcp_loop + _call_upstream_non_streaming |
| Modify | `proxy.py` | main() 初始化 MCPManager；_handle_responses() 路由到 _forward_mcp()；新增 _forward_mcp() 方法 |
| Modify | `proxy_config.yaml` | 新增 mcp 配置节 |
| Create | `test/test_mcp_manager.py` | MCPManager + execute_mcp_loop 单元测试 |
| Modify | `test/test_response_store.py` | 补充 proxy 路由检查测试 |

---

### Task 1：MCPManager 实现

**Files:**
- Create: `mcp_manager.py`（MCPManager 类，含 lazy 工具注册表 + async call_tool）
- Create: `test/test_mcp_manager.py`

- [ ] **Step 1: 写失败测试**

```python
# 新建 test/test_mcp_manager.py
import sys, unittest
from unittest.mock import patch, MagicMock, AsyncMock
sys.path.insert(0, "/Users/xys/.hermes/fact-store-browser")


class TestMCPManagerBasics(unittest.TestCase):
    def _make_manager(self, registry=None, servers_config=None):
        """创建 MCPManager，跳过真实 MCP 初始化，直接设置 _tool_registry。"""
        from mcp_manager import MCPManager
        m = MCPManager(servers_config or {}, max_auto_rounds=3)
        m._tool_registry = registry or {}    # 绕过 lazy init
        return m

    def test_has_tool_true(self):
        m = self._make_manager({"bash": "server1"})
        self.assertTrue(m.has_tool("bash"))

    def test_has_tool_false(self):
        m = self._make_manager({})
        self.assertFalse(m.has_tool("unknown"))

    def test_max_auto_rounds(self):
        m = self._make_manager()
        self.assertEqual(m.max_auto_rounds(), 3)

    def test_default_max_auto_rounds_is_8(self):
        from mcp_manager import MCPManager
        m = MCPManager({})
        m._tool_registry = {}
        self.assertEqual(m.max_auto_rounds(), 8)

    def test_tool_registry_lazy_init(self):
        """_tool_registry=None 时 has_tool() 应触发 lazy init（调用 _init_tool_registry）。

        Mock 链条说明：has_tool() 内部调用 asyncio.run(self._init_tool_registry())，
        mock_init.side_effect=_set_registry 使得 mock_init() 返回 _set_registry() 的结果
        （一个 coroutine），asyncio.run 运行该 coroutine 设置 m._tool_registry = {"bash": "s1"}。
        """
        from mcp_manager import MCPManager
        m = MCPManager({"s1": {"command": "echo", "args": []}}, max_auto_rounds=3)
        # _tool_registry 初始为 None
        self.assertIsNone(m._tool_registry)
        # mock _init_tool_registry 避免真实进程
        with patch.object(m, "_init_tool_registry") as mock_init:
            async def _set_registry():
                m._tool_registry = {"bash": "s1"}
            mock_init.side_effect = _set_registry
            result = m.has_tool("bash")
        self.assertTrue(result)
        mock_init.assert_called_once()

    def test_call_tool_returns_string(self):
        """call_tool() 应返回字符串结果。"""
        from mcp_manager import MCPManager
        m = MCPManager({"s1": {"command": "echo", "args": []}})
        m._tool_registry = {"bash": "s1"}
        # mock asyncio.run 直接返回结果
        with patch("asyncio.run", return_value="command output"):
            result = m.call_tool("bash", '{"cmd":"ls"}')
        self.assertEqual(result, "command output")

    def test_call_tool_unknown_raises(self):
        """调用不存在的工具应抛出 ValueError。"""
        from mcp_manager import MCPManager
        m = MCPManager({})
        m._tool_registry = {}
        with self.assertRaises(Exception):
            m.call_tool("unknown_tool", "{}")


class TestMCPManagerAsyncCallTool(unittest.TestCase):
    def test_async_call_tool_parses_json_args(self):
        """_async_call_tool 应将 JSON 字符串 arguments 解析为 dict 再传给 MCP SDK。"""
        from mcp_manager import MCPManager
        import asyncio

        m = MCPManager({"s1": {"command": "npx", "args": ["-y", "test-server"]}})
        m._tool_registry = {"bash": "s1"}

        # Mock MCP SDK 层
        mock_result = MagicMock()
        mock_result.content = [MagicMock(text="ls output")]

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=mock_result)
        mock_session.initialize = AsyncMock()

        mock_cm_session = MagicMock()
        mock_cm_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm_session.__aexit__ = AsyncMock(return_value=None)

        mock_cm_stdio = MagicMock()
        mock_cm_stdio.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock()))
        mock_cm_stdio.__aexit__ = AsyncMock(return_value=None)

        with patch("mcp_manager.stdio_client", return_value=mock_cm_stdio), \
             patch("mcp_manager.ClientSession", return_value=mock_cm_session):
            result = asyncio.run(m._async_call_tool("bash", '{"cmd":"ls"}'))

        # call_tool 收到的应是 dict，不是 JSON 字符串
        mock_session.call_tool.assert_called_once_with("bash", {"cmd": "ls"})
        self.assertEqual(result, "ls output")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_mcp_manager.py -v
```
期望：`ModuleNotFoundError: No module named 'mcp_manager'`

- [ ] **Step 3: 安装 MCP Python SDK**

```bash
cd /Users/xys/.hermes/fact-store-browser && pip install mcp
```

- [ ] **Step 4: 创建 `mcp_manager.py`**

```python
"""MCP 工具管理器：工具注册表 + 同步 call_tool 包装器。"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Optional

try:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False
    logging.warning("mcp 包未安装，MCPManager 不可用。运行 pip install mcp 以启用。")


class MCPManager:
    """MCP 工具管理器，使用 MCP Python SDK（JSON-RPC over stdio）。"""

    def __init__(self, servers_config: dict, max_auto_rounds: int = 8):
        """
        servers_config: {"server_name": {"command": "...", "args": [...]}}
        """
        self._servers_config = servers_config
        self._max_auto_rounds = max_auto_rounds
        self._tool_registry: Optional[dict] = None    # None = 未初始化（lazy init）
        self._init_lock = threading.Lock()             # 防止多线程双重初始化

    def has_tool(self, tool_name: str) -> bool:
        """检查工具是否存在于任一已注册的 MCP 服务器中。"""
        if self._tool_registry is None:
            with self._init_lock:
                if self._tool_registry is None:    # double-checked locking
                    try:
                        asyncio.run(self._init_tool_registry())
                    except Exception:
                        self._tool_registry = {}   # asyncio.run 本身失败兜底
        return tool_name in self._tool_registry

    def call_tool(self, tool_name: str, arguments: str, timeout: int = 30) -> str:
        """同步执行 MCP 工具，返回结果文本。

        arguments 为原始 JSON 字符串（来自 Chat Completions tool_calls[].function.arguments），
        内部负责 json.loads 后再传给 MCP SDK。

        timeout: 单个工具调用超时秒数（默认 30s，设计文稿 §10 要求的缓解措施）。

        已知限制：每次调用新建 stdio 子进程、初始化会话、调用、关闭，对 npx -y ... 等
        慢启动服务器性能极差。当前 Phase 3 可接受，留待后续优化（如进程池复用）。
        """
        try:
            return asyncio.run(
                asyncio.wait_for(self._async_call_tool(tool_name, arguments), timeout=timeout)
            )
        except asyncio.TimeoutError:
            raise TimeoutError(f"MCP 工具 {tool_name!r} 执行超时 ({timeout}s)")

    def max_auto_rounds(self) -> int:
        return self._max_auto_rounds

    async def _init_tool_registry(self):
        """向所有已配置的 MCP 服务器查询工具列表，构建 tool_name→server_name 映射。

        必须保证 self._tool_registry 最终不为 None，否则每次 has_tool() 都会
        重新尝试连接失败的 MCP 服务器，造成性能问题。
        """
        if not _MCP_AVAILABLE:
            self._tool_registry = {}
            return
        registry = {}
        for server_name, cfg in self._servers_config.items():
            params = StdioServerParameters(
                command=cfg["command"],
                args=cfg.get("args", []),
            )
            try:
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools_result = await session.list_tools()
                        for tool in tools_result.tools:
                            registry[tool.name] = server_name
            except Exception as e:
                logging.warning(f"MCPManager: 无法连接 server={server_name!r}: {e}")
        self._tool_registry = registry

    async def _async_call_tool(self, tool_name: str, arguments: str) -> str:
        """异步执行 MCP 工具，返回结果文本。"""
        if not _MCP_AVAILABLE:
            raise RuntimeError("mcp 包未安装，无法执行工具")
        if self._tool_registry is None:
            # async 路径无法用 with self._init_lock（会阻塞事件循环），
            # 但 MCP 工具调用在同步 call_tool→asyncio.run 内，不存在真正并发，重复 init 无害
            await self._init_tool_registry()
        server_name = self._tool_registry.get(tool_name)
        if not server_name:
            raise ValueError(f"工具 {tool_name!r} 不在 MCP 注册表中")
        cfg = self._servers_config[server_name]
        params = StdioServerParameters(
            command=cfg["command"],
            args=cfg.get("args", []),
        )
        args_dict = json.loads(arguments) if isinstance(arguments, str) else arguments
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, args_dict)
                if result.content:
                    return "\n".join(
                        c.text for c in result.content if hasattr(c, "text")
                    )
                return ""
```

- [ ] **Step 5: 运行测试确认通过**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_mcp_manager.py::TestMCPManagerBasics test/test_mcp_manager.py::TestMCPManagerAsyncCallTool -v
```
期望：`8 passed`

- [ ] **Step 6: Commit**

```bash
cd /Users/xys/.hermes/fact-store-browser && git add mcp_manager.py test/test_mcp_manager.py && git commit -m "feat: 新增 MCPManager（lazy 工具注册表 + asyncio.run 包装 MCP SDK 调用）"
```

---

### Task 2：execute_mcp_loop + _call_upstream_non_streaming

**Files:**
- Modify: `mcp_manager.py`（追加两个函数）
- Modify: `test/test_mcp_manager.py`

- [ ] **Step 1: 写失败测试**

```python
# 追加到 test/test_mcp_manager.py

class TestExecuteMcpLoop(unittest.TestCase):
    def _make_manager(self, tools=None, results=None):
        from mcp_manager import MCPManager
        m = MCPManager({}, max_auto_rounds=3)
        m._tool_registry = {t: "s1" for t in (tools or [])}
        if results:
            m.call_tool = lambda name, args: results.get(name, "ok")
        else:
            m.call_tool = lambda name, args: "ok"
        return m

    def _make_response(self, content=None, tool_calls=None, finish_reason="stop"):
        msg = {"role": "assistant"}
        if content is not None:
            msg["content"] = content
        if tool_calls:
            msg["tool_calls"] = tool_calls
        return {
            "id": "chatcmpl-test",
            "choices": [{"message": msg, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }

    def test_no_tool_calls_returns_immediately(self):
        """无 tool_calls 时应直接返回，不执行 MCP。"""
        from mcp_manager import execute_mcp_loop
        manager = self._make_manager()
        response = self._make_response(content="Hello, world!")

        with patch("mcp_manager._call_upstream_non_streaming", return_value=response) as mock_call:
            final, all_msgs = execute_mcp_loop(
                {"messages": [{"role": "user", "content": "Hi"}], "model": "test"},
                manager,
                {"base_url": "http://test", "api_key": "k"},
                "k",
            )
        mock_call.assert_called_once()
        self.assertEqual(final["choices"][0]["message"]["content"], "Hello, world!")
        # all_msgs 应含 user 和最终 assistant
        roles = [m["role"] for m in all_msgs]
        self.assertIn("user", roles)
        self.assertIn("assistant", roles)

    def test_tool_calls_with_mcp_executes_and_loops(self):
        """有 MCP tool_calls 时应执行工具并重新请求上游。"""
        from mcp_manager import execute_mcp_loop

        tool_response = self._make_response(tool_calls=[{
            "id": "call_1", "type": "function",
            "function": {"name": "bash", "arguments": '{"cmd":"ls"}'},
        }], finish_reason="tool_calls")
        final_response = self._make_response(content="Files: a.txt b.txt")

        manager = self._make_manager(tools=["bash"], results={"bash": "a.txt b.txt"})

        with patch("mcp_manager._call_upstream_non_streaming",
                   side_effect=[tool_response, final_response]) as mock_call:
            final, all_msgs = execute_mcp_loop(
                {"messages": [{"role": "user", "content": "List files"}], "model": "test"},
                manager,
                {"base_url": "http://test", "api_key": "k"},
                "k",
            )

        self.assertEqual(mock_call.call_count, 2, "应请求上游两次（1次工具响应 + 1次最终响应）")
        self.assertEqual(final["choices"][0]["message"]["content"], "Files: a.txt b.txt")
        # all_msgs 应含 user, assistant(tool_calls), tool(结果), assistant(最终)
        roles = [m["role"] for m in all_msgs]
        self.assertIn("tool", roles)

    def test_unknown_tool_stops_loop(self):
        """tool_calls 中有 MCP 不认识的工具时，直接返回当前响应（不执行）。"""
        from mcp_manager import execute_mcp_loop

        response_with_unknown = self._make_response(tool_calls=[{
            "id": "call_1", "type": "function",
            "function": {"name": "unknown_tool", "arguments": "{}"},
        }], finish_reason="tool_calls")

        manager = self._make_manager(tools=["bash"])  # "unknown_tool" 不在注册表

        with patch("mcp_manager._call_upstream_non_streaming", return_value=response_with_unknown):
            final, all_msgs = execute_mcp_loop(
                {"messages": [{"role": "user", "content": "?"}], "model": "test"},
                manager,
                {"base_url": "http://test", "api_key": "k"},
                "k",
            )
        # 遇到未知工具应立即停止，返回含 tool_calls 的响应
        self.assertIsNotNone(final["choices"][0]["message"].get("tool_calls"),
                             "遇到未知工具时应停止，返回原始 tool_calls 响应")

    def test_exceeds_max_rounds_raises_runtime_error(self):
        """超过 max_auto_rounds 仍有 tool_calls 时，抛出 RuntimeError。"""
        from mcp_manager import execute_mcp_loop

        tool_response = self._make_response(tool_calls=[{
            "id": "call_1", "type": "function",
            "function": {"name": "bash", "arguments": "{}"},
        }], finish_reason="tool_calls")

        manager = self._make_manager(tools=["bash"])  # max_auto_rounds=3

        with patch("mcp_manager._call_upstream_non_streaming", return_value=tool_response):
            with self.assertRaises(RuntimeError) as ctx:
                execute_mcp_loop(
                    {"messages": [{"role": "user", "content": "loop"}], "model": "test"},
                    manager,
                    {"base_url": "http://test", "api_key": "k"},
                    "k",
                )
        self.assertIn("too many tool-call rounds", str(ctx.exception))

    def test_tool_failure_propagates_exception(self):
        """call_tool 抛出异常时，应立即向上传播（不吞掉）。"""
        from mcp_manager import execute_mcp_loop

        tool_response = self._make_response(tool_calls=[{
            "id": "call_1", "type": "function",
            "function": {"name": "bash", "arguments": "{}"},
        }], finish_reason="tool_calls")

        manager = self._make_manager(tools=["bash"])
        manager.call_tool = MagicMock(side_effect=RuntimeError("MCP server crashed"))

        with patch("mcp_manager._call_upstream_non_streaming", return_value=tool_response):
            with self.assertRaises(RuntimeError, msg="工具调用失败应向上传播"):
                execute_mcp_loop(
                    {"messages": [{"role": "user", "content": "run"}], "model": "test"},
                    manager,
                    {"base_url": "http://test", "api_key": "k"},
                    "k",
                )

    def test_all_messages_contains_intermediate_rounds(self):
        """all_messages 应包含所有中间轮次（tool_calls + tool_results）。"""
        from mcp_manager import execute_mcp_loop

        tool_response = self._make_response(tool_calls=[
            {"id": "call_1", "type": "function", "function": {"name": "bash", "arguments": "{}"}},
            {"id": "call_2", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
        ], finish_reason="tool_calls")
        final_response = self._make_response(content="Done")

        manager = self._make_manager(tools=["bash", "read_file"])

        with patch("mcp_manager._call_upstream_non_streaming",
                   side_effect=[tool_response, final_response]):
            final, all_msgs = execute_mcp_loop(
                {"messages": [{"role": "user", "content": "run both"}], "model": "test"},
                manager,
                {"base_url": "http://test", "api_key": "k"},
                "k",
            )

        tool_msgs = [m for m in all_msgs if m["role"] == "tool"]
        self.assertEqual(len(tool_msgs), 2, "两个工具调用应有两条 tool result 消息")
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_mcp_manager.py::TestExecuteMcpLoop -v
```
期望：`ImportError: cannot import name 'execute_mcp_loop' from 'mcp_manager'`

- [ ] **Step 3: 在 `mcp_manager.py` 末尾追加两个函数**

```python
# ─── 上游 HTTP 辅助 ────────────────────────────────────────────────

def _call_upstream_non_streaming(chat_body: dict, upstream_cfg: dict, api_key: str) -> dict:
    """向上游发一次非流式 POST /chat/completions 请求，返回 JSON 响应 dict。
    
    使用 urllib.request（而非 http.client），因为：
    - http.client 不读取 http_proxy/https_proxy 环境变量，env var 代理方案无效
    - urllib.request 原生支持 ProxyHandler，配置 enable_proxy 后自动路由

    已知设计取舍：MCP 中间轮次的上游请求绕过了 proxy.py 的中间件层
    （model_map 映射、request_logger 日志），每轮使用 execute_mcp_loop 拿到
    的原始 model 名直连上游。这是有意为之——MCP 循环发生在非流式路径，
    model_map 已在 _handle_responses 入口处完成映射并写入 chat_body["model"]。
    若需要为中间轮次增加日志记录，可在后续迭代中提取通用 upstream 请求工具函数。
    """
    import urllib.request
    import ssl

    base_url = upstream_cfg["base_url"]
    timeout = upstream_cfg.get("timeout", 120)
    ssl_verify = upstream_cfg.get("ssl_verify", True)

    url = base_url.rstrip("/") + "/chat/completions"
    body_bytes = json.dumps({**chat_body, "stream": False}).encode()

    # SSL context：通过 HTTPSHandler 注入 opener，不能在 open() 调用时传入
    # (OpenerDirector.open() 签名不接受 context 参数，会抛 TypeError)
    ssl_ctx = (
        ssl.create_default_context()
        if ssl_verify
        else ssl._create_unverified_context()
    )
    handlers = [urllib.request.HTTPSHandler(context=ssl_ctx)]

    # 代理：当 enable_proxy=True 时使用 ProxyHandler，否则直连
    proxy_url = upstream_cfg.get("proxy", "")
    enable_proxy = upstream_cfg.get("enable_proxy", False)
    if enable_proxy and proxy_url:
        handlers.append(urllib.request.ProxyHandler({
            "http": proxy_url,
            "https": proxy_url,
        }))
    opener = urllib.request.build_opener(*handlers)

    req = urllib.request.Request(
        url, data=body_bytes,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    resp = opener.open(req, timeout=timeout)
    data = json.loads(resp.read().decode())
    resp.close()
    return data


# ─── MCP 工具执行循环 ──────────────────────────────────────────────

def execute_mcp_loop(
    chat_body: dict,
    mcp_manager: "MCPManager",
    upstream_cfg: dict,
    api_key: str,
) -> tuple[dict, list]:
    """非流式 MCP 工具自动执行循环。

    返回 (final_response: dict, all_messages: list)
    - final_response：最后一轮上游响应（Chat Completions 格式，无 tool_calls）
    - all_messages：完整消息列表，含所有中间轮次的 tool_calls/tool_results + 末尾一条 final assistant 消息
      （调用方构建 conversation 时需用 all_messages[:-1] 排除最后一条，再拼接 assistant_msgs，避免重复）

    错误合约：
    - 达到 max_auto_rounds 仍有 tool_calls → 抛出 RuntimeError("too many tool-call rounds")
    - 任一工具调用失败（call_tool 抛异常）→ 立即向上传播，调用方返回 500 给客户端
    """
    working_messages = list(chat_body.get("messages", []))
    rounds = 0
    max_rounds = mcp_manager.max_auto_rounds()

    while rounds < max_rounds:
        request_body = {**chat_body, "messages": working_messages}
        response = _call_upstream_non_streaming(request_body, upstream_cfg, api_key)

        choice = response.get("choices", [{}])[0]
        message = choice.get("message", {})
        tool_calls = message.get("tool_calls") or []

        # 无 tool_calls 或存在 MCP 不认识的工具 → 返回
        if not tool_calls or not all(
            mcp_manager.has_tool(tc["function"]["name"]) for tc in tool_calls
        ):
            working_messages.append(message)
            return response, working_messages

        # 执行所有 MCP 工具（有任一失败则异常向上传播）
        working_messages.append(message)
        for tc in tool_calls:
            result_text = mcp_manager.call_tool(
                tc["function"]["name"],
                tc["function"].get("arguments", "{}"),
            )
            working_messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result_text,
            })

        rounds += 1

    raise RuntimeError("too many tool-call rounds")
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_mcp_manager.py -v
```
期望：`14 passed`（Task 1 + Task 2 所有测试）

- [ ] **Step 5: Commit**

```bash
cd /Users/xys/.hermes/fact-store-browser && git add mcp_manager.py test/test_mcp_manager.py && git commit -m "feat: 实现 execute_mcp_loop 和 _call_upstream_non_streaming（MCP 工具自动执行循环）"
```

---

### Task 3：proxy.py MCP 集成

**Files:**
- Modify: `proxy.py`（`_handle_responses()` 路由 + 新增 `_forward_mcp()` 方法）
- Modify: `test/test_mcp_manager.py`

- [ ] **Step 1: 写失败测试（源码检查）**

```python
# 追加到 test/test_mcp_manager.py

class TestProxyMCPIntegration(unittest.TestCase):
    def _get_proxy_src(self):
        import pathlib
        return pathlib.Path("/Users/xys/.hermes/fact-store-browser/proxy.py").read_text()

    def test_handle_responses_routes_to_forward_mcp(self):
        """_handle_responses() 检测到 mcp_manager 时应路由到 _forward_mcp()。"""
        body = self._get_proxy_src()
        self.assertIn("_forward_mcp(", body,
                      "proxy.py 应有 _forward_mcp 方法")
        self.assertIn("mcp_manager", body,
                      "proxy.py 应检查 server.mcp_manager")

    def test_forward_mcp_calls_execute_mcp_loop(self):
        body = self._get_proxy_src()
        start = body.index("def _forward_mcp(")
        end = body.index("\n    def ", start + 1)
        func_body = body[start:end]
        self.assertIn("execute_mcp_loop", func_body,
                      "_forward_mcp() 应调用 execute_mcp_loop")
        self.assertIn("chat_to_responses", func_body,
                      "_forward_mcp() 应调用 chat_to_responses 转换结果")

    def test_forward_mcp_handles_too_many_rounds_error(self):
        body = self._get_proxy_src()
        start = body.index("def _forward_mcp(")
        end = body.index("\n    def ", start + 1)
        func_body = body[start:end]
        self.assertIn("too many tool-call rounds", func_body,
                      "_forward_mcp() 应处理 RuntimeError('too many tool-call rounds')")
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_mcp_manager.py::TestProxyMCPIntegration -v
```
期望：`3 failed`

- [ ] **Step 3: 在 `proxy.py` 的 `_handle_responses()` 中添加 MCP 路由**

将 `_handle_responses()` 末尾（`store_enabled = body.get("store", True)` 及后续 if/else，约第 334-338 行）改为：

```python
        store_enabled = body.get("store", True)
        mcp_manager = getattr(self.server, "mcp_manager", None)
        if is_stream:
            self._forward_streaming(chat_body, model_cfg, request_id, model_name, target, request_ts,
                                    store_enabled=store_enabled)
        elif mcp_manager is not None and chat_body.get("tools"):
            self._forward_mcp(chat_body, request_id, model_name, target, request_ts,
                               mcp_manager=mcp_manager, store_enabled=store_enabled)
        else:
            self._forward_non_streaming(chat_body, request_id, model_name, target, request_ts,
                                        store_enabled=store_enabled)
```

- [ ] **Step 4: 在 `ProxyHandler` 类中添加 `_forward_mcp()` 方法**

在 `_forward_non_streaming()` 方法之前插入（仅在 `ProxyHandler` 类体内）：

```python
    def _forward_mcp(self, chat_body: dict, request_id: str, model: str, target: str, request_ts: str, mcp_manager, store_enabled: bool = True):
        """非流式 MCP 工具自动执行路径。"""
        from mcp_manager import execute_mcp_loop
        upstream_cfg = CONFIG.get("upstream", {})
        api_key = upstream_cfg.get("api_key", "")

        logger = logging.getLogger(__name__)     # 与 _forward_non_streaming 的日志路径一致
        try:
            final_chat_resp, all_messages = execute_mcp_loop(
                chat_body, mcp_manager, upstream_cfg, api_key
            )
        except RuntimeError as e:
            if "too many tool-call rounds" in str(e):
                logging.warning(f"MCP 循环超出最大轮次: {e}")
                self._send_json(500, {"error": {"type": "server_error", "message": str(e)}})
            else:
                logging.exception("MCP 循环 RuntimeError")
                self._send_json(500, {"error": {"type": "server_error", "message": str(e)}})
            return
        except Exception as e:
            logging.exception("MCP 循环异常")
            self._send_json(500, {"error": {"type": "server_error", "message": str(e)}})
            return

        try:
            responses_resp = chat_to_responses(final_chat_resp)
        except Exception as e:
            logging.exception("chat_to_responses 转换失败（MCP 路径）")
            self._send_json(500, {"error": {"type": "internal_error", "message": str(e)}})
            return

        logger.info("MCP 循环完成 request_id=%s model=%s target=%s", request_id, model, target)
        # 若项目有自定义 RequestLogger，替换为：
        # from request_logger import get_logger; get_logger().log_converted_response(...)

        # 存储（conversation = 所有中间轮次消息 + 最终 assistant 输出）
        # 注意：all_messages 末尾已含 execute_mcp_loop 追加的最终 assistant 消息，
        # 用 all_messages[:-1] 排除它，避免与 assistant_msgs 重复。
        if store_enabled:
            from transform_responses import _output_items_to_messages as _oitm
            assistant_msgs = _oitm(responses_resp.get("output", []))
            messages_for_conv = [m for m in all_messages[:-1] if m.get("role") != "system"] + assistant_msgs
            _store_response(self.server, responses_resp, messages_for_conv)

        self._send_json(200, responses_resp)
```

- [ ] **Step 5: 运行测试确认通过**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_mcp_manager.py::TestProxyMCPIntegration -v
```
期望：`3 passed`

- [ ] **Step 6: Commit**

```bash
cd /Users/xys/.hermes/fact-store-browser && git add proxy.py test/test_mcp_manager.py && git commit -m "feat: proxy.py 新增 _forward_mcp() 路由，_handle_responses() 检测 mcp_manager 时走 MCP 循环路径"
```

---

### Task 4：proxy_config.yaml mcp 节 + main() 初始化

**Files:**
- Modify: `proxy_config.yaml`
- Modify: `proxy.py`（`main()` 函数）
- Modify: `test/test_mcp_manager.py`

- [ ] **Step 1: 写失败测试（源码检查）**

```python
# 追加到 test/test_mcp_manager.py

class TestMCPServerMounting(unittest.TestCase):
    def test_proxy_config_has_mcp_section(self):
        import pathlib
        src = pathlib.Path("/Users/xys/.hermes/fact-store-browser/proxy_config.yaml").read_text()
        self.assertIn("mcp:", src,
                      "proxy_config.yaml 应包含 mcp 配置节")
        self.assertIn("max_auto_rounds", src)

    def test_main_creates_mcp_manager_when_configured(self):
        """proxy.py main() 应按 proxy_config.yaml 的 mcp 节初始化 MCPManager。"""
        import pathlib
        src = pathlib.Path("/Users/xys/.hermes/fact-store-browser/proxy.py").read_text()
        main_idx = src.index("def main():")
        main_body = src[main_idx:]
        self.assertIn("mcp_manager", main_body,
                      "main() 应初始化 server.mcp_manager")
        self.assertIn("MCPManager", main_body,
                      "main() 应使用 MCPManager 类")
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_mcp_manager.py::TestMCPServerMounting -v
```
期望：`2 failed`

- [ ] **Step 3: 在 `proxy_config.yaml` 末尾追加 mcp 节**

```yaml
mcp:
  max_auto_rounds: 8
  # servers:
  #   web_search:
  #     command: "npx"
  #     args: ["-y", "@modelcontextprotocol/server-web-search"]
  #   filesystem:
  #     command: "npx"
  #     args: ["-y", "@modelcontextprotocol/server-filesystem", "/Users/xys"]
```

（默认注释掉，避免在无 MCP 服务器的环境中启动失败。）

- [ ] **Step 4: 在 `proxy.py` 的 `main()` 中初始化 MCPManager**

在 `server.response_store = ...` 赋值之后插入：

```python
    # MCPManager（仅在 mcp.servers 配置非空时初始化）
    mcp_cfg = CONFIG.get("mcp", {})
    mcp_servers = mcp_cfg.get("servers", {})
    if mcp_servers:
        from mcp_manager import MCPManager
        server.mcp_manager = MCPManager(
            servers_config=mcp_servers,
            max_auto_rounds=mcp_cfg.get("max_auto_rounds", 8),
        )
        logging.info(f"MCPManager 已初始化，servers={list(mcp_servers.keys())}")
    else:
        server.mcp_manager = None
```

- [ ] **Step 5: 运行测试确认通过**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/test_mcp_manager.py::TestMCPServerMounting -v
```
期望：`2 passed`

- [ ] **Step 6: Commit**

```bash
cd /Users/xys/.hermes/fact-store-browser && git add proxy_config.yaml proxy.py test/test_mcp_manager.py && git commit -m "feat: proxy_config.yaml 新增 mcp 节；main() 按配置初始化 MCPManager 并挂载到 server"
```

---

### Task 5：集成测试 + 全量验收

**Files:**
- Modify: `test/test_mcp_manager.py`

- [ ] **Step 1: 新增端到端 MCP 循环集成测试**

```python
# 追加到 test/test_mcp_manager.py

class TestMCPLoopIntegration(unittest.TestCase):
    """验证 MCP 循环 + Response Store 联动的核心逻辑。"""

    def _make_manager(self, tools, tool_results=None):
        from mcp_manager import MCPManager
        m = MCPManager({}, max_auto_rounds=8)
        m._tool_registry = {t: "s1" for t in tools}
        results = tool_results or {t: f"result_{t}" for t in tools}
        m.call_tool = lambda name, args: results.get(name, "ok")
        return m

    def _make_chat_response(self, content=None, tool_calls=None):
        msg = {"role": "assistant"}
        if content is not None:
            msg["content"] = content
        if tool_calls:
            msg["tool_calls"] = tool_calls
        return {
            "id": "chatcmpl-test",
            "model": "test",
            "choices": [{"message": msg, "finish_reason": "tool_calls" if tool_calls else "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }

    def test_mcp_result_stored_in_response_store(self):
        """MCP 循环结束后，最终响应应存入 ResponseStore，对话链完整。"""
        from mcp_manager import execute_mcp_loop
        from response_store import ResponseStore
        from transform import chat_to_responses, _output_items_to_messages
        from response_store import ResponseRecord
        import time

        store = ResponseStore()
        tool_response = self._make_chat_response(tool_calls=[{
            "id": "call_1", "type": "function",
            "function": {"name": "bash", "arguments": '{"cmd":"ls"}'},
        }])
        final_response = self._make_chat_response(content="Listed files")

        manager = self._make_manager(["bash"], {"bash": "file1.txt\nfile2.txt"})
        original_messages = [{"role": "user", "content": "List my files"}]

        with patch("mcp_manager._call_upstream_non_streaming",
                   side_effect=[tool_response, final_response]):
            chat_resp, all_messages = execute_mcp_loop(
                {"messages": original_messages, "model": "test"},
                manager,
                {"base_url": "http://test", "api_key": "k"},
                "k",
            )

        responses_resp = chat_to_responses(chat_resp)

        # 模拟 proxy 存储逻辑（all_messages 末尾已含 final assistant，用 [:-1] 排除避免重复）
        from transform_responses import _output_items_to_messages as oitm
        output = responses_resp.get("output", [])
        assistant_msgs = oitm(output)
        conversation = [m for m in all_messages[:-1] if m.get("role") != "system"] + assistant_msgs
        record = ResponseRecord(
            response_id=responses_resp.get("id", "test_id"),
            model="test",
            output=output,
            conversation=conversation,
            usage={},
            status="completed",
            created_at=time.time(),
            expires_at=time.time() + 3600,
        )
        store.put(record.response_id, record)

        # 验证 conversation 包含完整的 MCP 轮次
        conv = store.get_conversation(record.response_id)
        self.assertIsNotNone(conv)
        # 应含 user 消息、assistant(tool_calls)、tool(result)、assistant(最终)
        roles = [m["role"] for m in conv]
        self.assertIn("user", roles)
        self.assertIn("tool", roles, "对话历史应包含 MCP 工具执行结果")
        self.assertIn("assistant", roles)

    def test_two_tools_both_get_executed(self):
        """并发两个工具调用都应被执行，all_messages 中有两条 tool 消息。"""
        from mcp_manager import execute_mcp_loop

        tool_response = self._make_chat_response(tool_calls=[
            {"id": "c1", "type": "function", "function": {"name": "bash", "arguments": "{}"}},
            {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
        ])
        final_response = self._make_chat_response(content="Both done")
        manager = self._make_manager(["bash", "read_file"])

        with patch("mcp_manager._call_upstream_non_streaming",
                   side_effect=[tool_response, final_response]):
            _, all_msgs = execute_mcp_loop(
                {"messages": [{"role": "user", "content": "run both"}], "model": "test"},
                manager,
                {"base_url": "http://test", "api_key": "k"},
                "k",
            )

        tool_msgs = [m for m in all_msgs if m["role"] == "tool"]
        self.assertEqual(len(tool_msgs), 2, "两个工具各产生一条 tool result 消息")
        tool_call_ids = {m["tool_call_id"] for m in tool_msgs}
        self.assertIn("c1", tool_call_ids)
        self.assertIn("c2", tool_call_ids)
```

- [ ] **Step 2: 运行全量测试确认全部通过**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -m pytest test/ -v --tb=short
```
期望：全部通过（Phase 1 + Phase 2 + Phase 3 所有测试）

- [ ] **Step 3: 验证关键符号均可导入**

```bash
cd /Users/xys/.hermes/fact-store-browser && python3 -c "
from mcp_manager import MCPManager, execute_mcp_loop, _call_upstream_non_streaming
from response_store import ResponseStore, ResponseRecord
from transform import create_codex_sse_stream, _output_items_to_messages
print('所有符号导入成功')
"
```
期望：`所有符号导入成功`

- [ ] **Step 4: 最终 Commit**

```bash
cd /Users/xys/.hermes/fact-store-browser && git add test/test_mcp_manager.py && git commit -m "test: Phase 3 MCP 循环集成测试——Response Store 联动、并发工具执行、对话链完整性"
```

---

*三个阶段全部完成后，代理具备：完整 SSE 事件序列（Phase 1）、多轮对话 previous_response_id 支持（Phase 2）、MCP 工具自动执行（Phase 3）。*
