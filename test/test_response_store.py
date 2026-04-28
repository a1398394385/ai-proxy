import sys, time, unittest
sys.path.insert(0, "/Users/xys/.hermes/fact-store-browser")


class TestResponseRecord(unittest.TestCase):
    def test_fields(self):
        from response_store import ResponseRecord
        now = time.time()
        r = ResponseRecord(
            response_id="resp_1", model="gpt-4o",
            output=[{"type": "message"}],
            conversation=[{"role": "user", "content": "Hi"}],
            usage={"input_tokens": 5, "output_tokens": 3, "total_tokens": 8},
            status="completed",
            created_at=now, expires_at=now + 3600,
        )
        self.assertEqual(r.response_id, "resp_1")
        self.assertEqual(r.model, "gpt-4o")
        self.assertEqual(r.status, "completed")


class TestResponseStore(unittest.TestCase):
    def _make_record(self, resp_id="r1", ttl=3600):
        from response_store import ResponseRecord
        now = time.time()
        return ResponseRecord(
            response_id=resp_id, model="test",
            output=[{"type": "message", "content": [{"type": "output_text", "text": "Hello"}]}],
            conversation=[
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello"},
            ],
            usage={"input_tokens": 5, "output_tokens": 3, "total_tokens": 8},
            status="completed",
            created_at=time.time(), expires_at=time.time() + ttl,
        )

    def test_put_and_get(self):
        from response_store import ResponseStore
        store = ResponseStore()
        store.put("resp_1", self._make_record("resp_1"))
        result = store.get("resp_1")
        self.assertIsNotNone(result)
        self.assertEqual(result.response_id, "resp_1")

    def test_get_missing_returns_none(self):
        from response_store import ResponseStore
        self.assertIsNone(ResponseStore().get("nonexistent"))

    def test_ttl_expiry(self):
        from response_store import ResponseStore, ResponseRecord
        store = ResponseStore()
        now = time.time()
        expired = ResponseRecord("r_exp", "t", [], [], {}, "c", now, now - 1)
        store._store["r_exp"] = expired   # bypass put() 直接注入已过期条目
        self.assertIsNone(store.get("r_exp"), "TTL 已过期应返回 None")

    def test_lru_eviction(self):
        from response_store import ResponseStore
        store = ResponseStore(max_entries=2)
        store.put("r1", self._make_record("r1"))
        store.put("r2", self._make_record("r2"))
        store.get("r1")                           # 标记 r1 为最近使用
        store.put("r3", self._make_record("r3"))  # 超出 max，淘汰最旧的 r2
        self.assertIsNotNone(store.get("r1"), "r1 应保留（最近访问）")
        self.assertIsNone(store.get("r2"),    "r2 应被淘汰（LRU）")
        self.assertIsNotNone(store.get("r3"), "r3 应保留（新加入）")

    def test_get_updates_lru_order(self):
        """get() 将条目移到最近端，防止连续 put 时被误淘汰。"""
        from response_store import ResponseStore
        store = ResponseStore(max_entries=3)
        store.put("r1", self._make_record("r1"))
        store.put("r2", self._make_record("r2"))
        store.put("r3", self._make_record("r3"))
        store.get("r1")                           # r1 刷新为最近使用
        store.put("r4", self._make_record("r4"))  # 淘汰最旧的 r2
        self.assertIsNotNone(store.get("r1"))
        self.assertIsNone(store.get("r2"))

    def test_get_conversation(self):
        from response_store import ResponseStore
        store = ResponseStore()
        store.put("r1", self._make_record("r1"))
        conv = store.get_conversation("r1")
        self.assertEqual(len(conv), 2)
        self.assertEqual(conv[0]["role"], "user")

    def test_get_conversation_missing_returns_empty(self):
        from response_store import ResponseStore
        self.assertEqual(ResponseStore().get_conversation("nonexistent"), [])

    def test_expired_evicted_on_put(self):
        """put() 先清理已过期条目，避免 max_entries 被占满后再淘汰有效条目。"""
        from response_store import ResponseStore, ResponseRecord
        store = ResponseStore(max_entries=2)
        now = time.time()
        r_exp = ResponseRecord("r_exp", "t", [], [], {}, "c", now, now - 1)
        store._store["r_exp"] = r_exp           # 注入过期条目（绕过 put）
        store.put("r2", self._make_record("r2"))
        # 此时 _store 有 2 个条目（含过期），put r3 时先 evict r_exp
        store.put("r3", self._make_record("r3"))
        self.assertIsNone(store.get("r_exp"))
        self.assertIsNotNone(store.get("r2"))
        self.assertIsNotNone(store.get("r3"))


class TestStreamingStorePath(unittest.TestCase):
    @staticmethod
    def _make_mock_stream(chunks):
        class MockStream:
            def __init__(self):
                self.data = b"".join(chunks)
                self.pos = 0
            def read(self, size):
                chunk = self.data[self.pos:self.pos + size]
                self.pos += size
                return chunk
        return MockStream()

    def test_streaming_stores_record_in_store(self):
        """耗尽 create_codex_sse_stream 生成器后，store 应有对应的 record。"""
        from transform_responses import create_codex_sse_stream
        from response_store import ResponseStore

        store = ResponseStore()
        chunks = [
            b'data: {"id":"c1","model":"test","choices":[{"delta":{"content":"Hi"},"index":0}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}\n\n',
            b'data: [DONE]\n\n',
        ]
        request_messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]

        events_text = "".join(create_codex_sse_stream(
            self._make_mock_stream(chunks),
            request_messages=request_messages,
            response_store=store,
        ))

        self.assertEqual(len(store._store), 1, "store 应有 1 条 record")
        record = list(store._store.values())[0]
        self.assertEqual(record.status, "completed")
        # usage 格式验证：应是 input_tokens/output_tokens（Responses API 格式），
        # 而非 raw prompt_tokens/completion_tokens（Chat Completions 格式）
        self.assertIn("input_tokens", record.usage)
        self.assertIn("output_tokens", record.usage)
        self.assertNotIn("prompt_tokens", record.usage)
        self.assertNotIn("completion_tokens", record.usage)
        # conversation 不含 system，但含 user 和 assistant
        roles = [m["role"] for m in record.conversation]
        self.assertNotIn("system", roles, "conversation 不应含 system 消息")
        self.assertIn("user", roles)
        self.assertIn("assistant", roles)

    def test_streaming_no_store_when_none(self):
        """response_store=None 时不报错，正常流式输出。"""
        from transform_responses import create_codex_sse_stream
        chunks = [
            b'data: {"id":"c1","model":"test","choices":[{"delta":{"content":"Hi"},"index":0}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}\n\n',
            b'data: [DONE]\n\n',
        ]
        result = list(create_codex_sse_stream(self._make_mock_stream(chunks)))
        self.assertTrue(any("response.completed" in e for e in result))

    def test_forward_streaming_passes_store_to_factory(self):
        """_forward_streaming 应传入 request_messages 和 response_store 参数。"""
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "proxy.py").read_text()
        start = src.index("def _forward_streaming(")
        end = src.index("\n    def _send_json(", start)
        func_body = src[start:end]
        self.assertIn("request_messages", func_body,
                      "_forward_streaming 应向 sse_stream_factory 传 request_messages")
        self.assertIn("response_store", func_body,
                      "_forward_streaming 应向 sse_stream_factory 传 response_store")


class TestNonStreamingStorePath(unittest.TestCase):
    def _get_non_streaming_body(self):
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "proxy.py").read_text()
        start = src.index("def _forward_non_streaming(")
        end = src.index("\n    def _forward_streaming(", start)
        return src[start:end]

    def test_stores_response_after_conversion(self):
        body = self._get_non_streaming_body()
        self.assertIn("_store_response(", body,
                      "_forward_non_streaming 应调用 _store_response 存储")

    def test_store_response_helper_exists(self):
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "proxy.py").read_text()
        self.assertIn("def _store_response(", src,
                      "proxy.py 应有 _store_response 辅助函数")
        self.assertIn("_output_items_to_messages", src)


class TestPreviousResponseIdInjection(unittest.TestCase):
    def _get_handle_responses_body(self):
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "proxy.py").read_text()
        start = src.index("def _handle_responses(")
        # 取到下一个 def（_handle_messages 之前）
        end = src.index("\n    def _handle_messages(", start)
        return src[start:end]

    def test_reads_previous_response_id(self):
        body = self._get_handle_responses_body()
        self.assertIn("previous_response_id", body,
                      "_handle_responses() 应读取 previous_response_id")
        self.assertIn("response_store.get(", body,
                      "_handle_responses() 应调用 response_store.get() 读取历史")

    def test_system_msg_stays_first(self):
        """注入历史时 system 消息必须保持在首位（不被历史 messages 插入其前）。"""
        body = self._get_handle_responses_body()
        self.assertIn("system_msgs", body,
                      "proxy.py 应将 system 消息和历史消息分开处理，确保 system 在首位")


class TestResponseStoreServerMount(unittest.TestCase):
    def test_main_mounts_response_store(self):
        """proxy.py main() 应在创建 server 后挂载 server.response_store。"""
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "proxy.py").read_text()
        self.assertIn("server.response_store", src,
                      "main() 应将 ResponseStore 挂载到 server.response_store")
        self.assertIn("ResponseStore", src,
                      "proxy.py 应导入并使用 ResponseStore")

    def test_proxy_config_has_response_store_section(self):
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "proxy_config.yaml").read_text()
        self.assertIn("response_store", src,
                      "proxy_config.yaml 应包含 response_store 配置节")
        self.assertIn("max_entries", src)
        self.assertIn("ttl_seconds", src)


if __name__ == "__main__":
    unittest.main()
