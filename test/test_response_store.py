import sys, time, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


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


class TestConversationChain(unittest.TestCase):
    """验证 previous_response_id 多轮对话链核心逻辑（不启动真实代理）。"""

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

    def test_round1_stores_and_round2_can_inject(self):
        """
        轮次1: user→"Hi", assistant→"Hello" → 存入 store
        轮次2: 从 store 取 conversation → 注入到新 messages → system 在首位、历史在中间、新 user 在末尾
        """
        import json
        from transform_responses import create_codex_sse_stream
        from response_store import ResponseStore

        store = ResponseStore()

        # 轮次 1
        round1_chunks = [
            b'data: {"id":"c1","model":"test","choices":[{"delta":{"content":"Hello"},"index":0}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":2,"completion_tokens":1,"total_tokens":3}}\n\n',
            b'data: [DONE]\n\n',
        ]
        round1_messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
        ]
        events1 = list(create_codex_sse_stream(
            self._make_mock_stream(round1_chunks),
            request_messages=round1_messages,
            response_store=store,
        ))

        # 从流事件提取 response_id
        resp_id = None
        for e in events1:
            for line in e.split("\n"):
                if line.startswith("data: "):
                    try:
                        data = json.loads(line[6:])
                        if data.get("type") == "response.created":
                            resp_id = data["response"]["id"]
                    except json.JSONDecodeError:
                        pass
        self.assertIsNotNone(resp_id, "轮次 1 应生成 response_id")

        # 验证 store 中有该 record
        record = store.get(resp_id)
        self.assertIsNotNone(record)
        self.assertEqual(record.status, "completed")

        # conversation 不含 system，含 user + assistant
        conv_roles = [m["role"] for m in record.conversation]
        self.assertNotIn("system", conv_roles, "conversation 不应含 system 消息")
        self.assertIn("user", conv_roles)
        self.assertIn("assistant", conv_roles)

        # 轮次 2：模拟 _handle_responses() 中的注入逻辑
        previous_conv = store.get_conversation(resp_id)
        round2_messages = [
            {"role": "system", "content": "You are helpful."},   # 新 system
            {"role": "user", "content": "What?"},
        ]
        system_msgs = [m for m in round2_messages if m.get("role") == "system"]
        non_system_msgs = [m for m in round2_messages if m.get("role") != "system"]
        injected = system_msgs + previous_conv + non_system_msgs

        # 顺序验证
        self.assertEqual(injected[0]["role"], "system", "system 消息必须在首位")
        self.assertEqual(injected[-1]["content"], "What?", "新 user 消息应在末尾")
        # 历史 "Hi" 在中间
        all_contents = [m.get("content") for m in injected]
        self.assertIn("Hi", all_contents, "历史 user 消息应在中间")
        # 消息角色顺序验证：system → user(历史) → assistant(历史) → user(新)
        roles = [m["role"] for m in injected]
        self.assertEqual(roles, ["system", "user", "assistant", "user"],
                         f"消息顺序错误，实际: {roles}")
        # assistant 消息不重复
        assistant_count = sum(1 for r in roles if r == "assistant")
        self.assertEqual(assistant_count, 1, "conversation 中 assistant 消息不应重复")

    def test_pure_refusal_non_streaming_store_uses_empty_string(self):
        """非流式路径：chat_to_responses 纯拒绝输出存入 store 后，content 为空字符串。"""
        from transform import chat_to_responses, output_items_to_messages
        from response_store import ResponseStore, ResponseRecord

        store = ResponseStore()
        chat_resp = {
            "id": "chatcmpl-refonly",
            "model": "test",
            "choices": [{
                "message": {"content": None, "refusal": "I cannot help with that."},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        }
        responses_resp = chat_to_responses(chat_resp)
        output = responses_resp.get("output", [])
        assistant_msgs = output_items_to_messages(output)

        messages_for_conv = [{"role": "user", "content": "Bad request"}] + assistant_msgs
        record = ResponseRecord(
            response_id=responses_resp.get("id", "ref_test"),
            model="test", output=output, conversation=messages_for_conv,
            usage={}, status="completed", created_at=time.time(),
            expires_at=time.time() + 3600,
        )
        store.put(record.response_id, record)

        conv = store.get_conversation(record.response_id)
        assistant_convs = [m for m in conv if m["role"] == "assistant"]
        self.assertEqual(len(assistant_convs), 1)
        self.assertIsNotNone(assistant_convs[0]["content"])
        self.assertEqual(assistant_convs[0]["content"], "")

    def test_pure_refusal_streaming_conversation_uses_empty_string(self):
        """流式路径：纯拒绝响应存入 store 后，conversation 的 assistant content 为空字符串（不是 None）。"""
        from transform_responses import create_codex_sse_stream
        from response_store import ResponseStore

        store = ResponseStore()
        refusal_chunks = [
            b'data: {"id":"c1","model":"test","choices":[{"delta":{"refusal":"I cannot help"},"index":0}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":2,"total_tokens":3}}\n\n',
            b'data: [DONE]\n\n',
        ]
        list(create_codex_sse_stream(
            self._make_mock_stream(refusal_chunks),
            request_messages=[{"role": "user", "content": "Do bad thing"}],
            response_store=store,
        ))

        record = list(store._store.values())[0]
        assistant_msgs = [m for m in record.conversation if m["role"] == "assistant"]
        self.assertEqual(len(assistant_msgs), 1)
        self.assertIsNotNone(assistant_msgs[0]["content"],
                             "assistant content 不能为 None（上游会报 400）")
        self.assertEqual(assistant_msgs[0]["content"], "",
                         "纯拒绝时 content 应为空字符串")


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
        self.assertIn("output_items_to_messages", src)


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
