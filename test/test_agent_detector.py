import unittest


class TestDetectSubagent(unittest.TestCase):
    def _make_body(self, messages=None, metadata=None):
        body = {}
        if messages is not None:
            body["messages"] = messages
        if metadata is not None:
            body["metadata"] = metadata
        return body

    def test_normal_user_message_not_subagent(self):
        from proxy.agent_detector import detect_subagent
        body = self._make_body(messages=[{"role": "user", "content": "hello"}])
        self.assertFalse(detect_subagent(body))

    def test_system_message_with_marker(self):
        from proxy.agent_detector import detect_subagent
        body = self._make_body(messages=[
            {"role": "system", "content": '<system-reminder>{"__SUBAGENT_MARKER__": {"session_id": "abc"}}</system-reminder>'}
        ])
        self.assertTrue(detect_subagent(body))

    def test_user_message_with_marker(self):
        from proxy.agent_detector import detect_subagent
        body = self._make_body(messages=[
            {"role": "user", "content": '<system-reminder>{"__SUBAGENT_MARKER__": {"agent_id": "123"}}</system-reminder>'}
        ])
        self.assertTrue(detect_subagent(body))

    def test_content_blocks_with_marker(self):
        from proxy.agent_detector import detect_subagent
        body = self._make_body(messages=[
            {"role": "user", "content": [
                {"type": "text", "text": "task description"},
                {"type": "text", "text": '<system-reminder>{"__SUBAGENT_MARKER__": {}}</system-reminder>'}
            ]}
        ])
        self.assertTrue(detect_subagent(body))

    def test_metadata_user_id_contains_agent(self):
        from proxy.agent_detector import detect_subagent
        body = self._make_body(
            messages=[{"role": "user", "content": "hello"}],
            metadata={"user_id": "sess123_agent_agent456"}
        )
        self.assertTrue(detect_subagent(body))

    def test_metadata_user_id_no_agent(self):
        from proxy.agent_detector import detect_subagent
        body = self._make_body(
            messages=[{"role": "user", "content": "hello"}],
            metadata={"user_id": "normal_user"}
        )
        self.assertFalse(detect_subagent(body))

    def test_empty_body(self):
        from proxy.agent_detector import detect_subagent
        self.assertFalse(detect_subagent({}))

    def test_no_messages_key(self):
        from proxy.agent_detector import detect_subagent
        self.assertFalse(detect_subagent({"metadata": {"user_id": "normal"}}))

    def test_content_blocks_without_text(self):
        from proxy.agent_detector import detect_subagent
        body = self._make_body(messages=[
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "http://example.com"}}
            ]}
        ])
        self.assertFalse(detect_subagent(body))

    # ─── Codex subagent 检测 ───

    def test_codex_main_session_not_subagent(self):
        from proxy.agent_detector import detect_subagent
        body = {
            "model": "gpt-5.4-mini",
            "client_metadata": {"x-codex-installation-id": "abc-123"},
            "tools": [
                {"type": "function", "name": "spawn_agent"},
                {"type": "function", "name": "exec_command"},
            ],
        }
        self.assertFalse(detect_subagent(body))

    def test_codex_subagent_no_spawn_agent(self):
        from proxy.agent_detector import detect_subagent
        body = {
            "model": "gpt-5.4-mini",
            "client_metadata": {"x-codex-installation-id": "abc-123"},
            "tools": [
                {"type": "function", "name": "exec_command"},
                {"type": "function", "name": "apply_patch"},
            ],
        }
        self.assertTrue(detect_subagent(body))

    def test_codex_subagent_with_mcp_tools(self):
        from proxy.agent_detector import detect_subagent
        body = {
            "model": "gpt-5.5",
            "client_metadata": {"x-codex-installation-id": "def-456"},
            "tools": [
                {"type": "function", "name": "exec_command"},
                {"type": "function", "name": "mcp__context7__"},
                {"type": "function", "name": "apply_patch"},
            ],
        }
        self.assertTrue(detect_subagent(body))

    def test_non_codex_without_spawn_agent_not_detected(self):
        from proxy.agent_detector import detect_subagent
        body = {
            "model": "gpt-5.4-mini",
            "tools": [{"type": "function", "name": "exec_command"}],
        }
        self.assertFalse(detect_subagent(body))

    def test_codex_empty_tools(self):
        from proxy.agent_detector import detect_subagent
        body = {
            "model": "gpt-5.4-mini",
            "client_metadata": {"x-codex-installation-id": "abc-123"},
            "tools": [],
        }
        self.assertTrue(detect_subagent(body))

    def test_codex_no_tools_key(self):
        from proxy.agent_detector import detect_subagent
        body = {
            "model": "gpt-5.4-mini",
            "client_metadata": {"x-codex-installation-id": "abc-123"},
        }
        self.assertTrue(detect_subagent(body))


if __name__ == "__main__":
    unittest.main()
