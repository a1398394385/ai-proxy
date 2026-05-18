# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 概述

`test/` 包含 18 个测试文件，约 9400 行。全部使用 `unittest.TestCase`，无 pytest fixtures。

## 测试运行

```bash
python3 -m pytest test/ -q                              # 全量 530+ tests
python3 -m pytest test/test_transform.py -q             # 单文件
python3 -m pytest test/test_handler.py -q -k "test名"   # 按名称过滤
```

## 文件速查

| 文件 | 行数 | 测试内容 |
|------|------|---------|
| `test_transform.py` | 2349 | Responses ↔ Chat 转换（最大文件，138 tests） |
| `test_transform_anthropic.py` | 796 | Anthropic Messages ↔ Chat 转换 |
| `test_transform_router.py` | 59 | TransformRouter 注册表/分发 |
| `test_sse_utils.py` | 121 | SSE 事件格式化 |
| `test_handler.py` | 838 | ProxyHandler 路由/透传/转换/日志 |
| `test_proxy_pass_through.py` | 307 | 透传路径规范化/日志/路由优先级 |
| `test_proxy_logger_integration.py` | 475 | Proxy + Logger 四阶段日志集成 |
| `test_config_manager.py` | 778 | ConfigDB CRUD/迁移/代理路由 |
| `test_config_integration.py` | 99 | ConfigDB → ConfigCache 全流程 |
| `test_proxy_config.py` | 116 | proxy_config.yaml 加载校验 |
| `test_request_logger.py` | 483 | RequestLogger DB 初始化/四阶段/清理 |
| `test_token_stats.py` | 256 | Token 提取（三种 API 格式） |
| `test_stats_service.py` | 1638 | StatsService 查询/成本/多源聚合 |
| `test_pricing_manager.py` | 211 | PricingDB 建表/种子数据/CRUD |
| `test_response_store.py` | 397 | ResponseStore LRU/TTL/对话链 |
| `test_agent_detector.py` | 157 | 子代理检测（标记符/Codex/OMC hooks） |
| `test_e2e_smoke.py` | 221 | 端对端冒烟（默认跳过） |
| `mock_server.py` | 101 | Chat Completions SSE mock 服务器 |
| `quick_test.py` | 24 | Token 统计 API 冒烟（需服务运行） |

## 测试模式

### DB 隔离
每个测试创建独立的 SQLite 数据库：
```python
def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.db_path = Path(self.tmp.name) / "test.db"

def tearDown(self):
    self.tmp.cleanup()
```
无共享测试基类，无 conftest.py。

### HTTP 层 Mock
```python
mock_resp = MagicMock()
mock_resp.status = 200
mock_resp.read.return_value = json.dumps(chat_resp).encode()
mock_conn = MagicMock()
mock_conn.getresponse.return_value = mock_resp

with patch("http.client.HTTPConnection", return_value=mock_conn):
    # 测试 handler
```

### Handler Mock（跳过 __init__）
```python
class _TestHandler(ProxyHandler):
    def __init__(self):
        self.rfile = io.BytesIO(body_bytes)
        self.wfile = io.BytesIO()
        self.headers = MagicMock()
        self.send_response = MagicMock()
```

### 动态模块加载
部分测试用 `importlib.util.spec_from_file_location` 重载 `proxy.py`，以绕过模块级 `load_config()` 副作用。

### 单例 Patch
```python
import proxy.request_logger
proxy.request_logger._logger = RequestLogger(self.db_path)
# ... 测试 ...
proxy.request_logger._logger = None
```

### 内联 JSON Fixture
转换测试使用大量内联 dict 作为 API 请求/响应体，配合 SSE chunk 字节串模拟流式响应。

## 测试分类

| 类型 | 文件 | 特点 |
|------|------|------|
| **纯函数单元测试** | transform, sse_utils, agent_detector | 无 DB，无网络，纯数据转换 |
| **DB 单元测试** | config_manager, pricing_manager, token_stats, response_store | 独立 temp DB |
| **集成测试** | handler, proxy_logger_integration, proxy_pass_through, config_integration | 多组件串联 |
| **E2E 测试** | e2e_smoke | 启动真实 proxy 子进程 |

## 注意事项
- 中文 docstring 描述测试场景
- `test_e2e_smoke.TestEndToEndSmoke` 默认跳过（需真实 API 后端）
- 转换测试对格式变更敏感，修改 `responses.py`/`anthropic.py` 转换逻辑后必须跑全量转换测试
- `test_response_store.py` 的 TTL 测试有时钟依赖，极端情况下可能不稳定
- `mock_server.py` 只被 `test_handler.py` 的 `TestConvertOutputConsistency` 使用
