"""proxy.py 配置加载校验测试。"""
import os
import sys
import unittest
import tempfile
import importlib.util
from pathlib import Path


def load_proxy_module(config_path_override: Path = None):
    """使用 importlib 动态加载 proxy.py，避免模块级 load_config() 执行。

    config_path_override: 可选，传入自定义 CONFIG_PATH（在 exec_module 后设置以避免覆盖）。
    """
    proxy_py = Path(__file__).parent / "proxy.py"
    spec = importlib.util.spec_from_file_location("proxy_test", str(proxy_py))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if config_path_override:
        mod.CONFIG_PATH = config_path_override
    return mod


class TestConfigValidation(unittest.TestCase):
    def test_missing_star_fallback(self):
        """model_map 缺少 * 键应导致 sys.exit(1)。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("""
proxy:
  host: "127.0.0.1"
  port: 48743
  log_level: "INFO"

upstream:
  base_url: "https://example.com/v1"
  api_key: "sk-test"
  timeout: 120
  connect_timeout: 10
  ssl_verify: true
  retry: 0

model_map:
  "gpt-4o":
    target: "claude-sonnet-4-6"
    multimodal: false
""")
            tmp_path = f.name

        mod = load_proxy_module(Path(tmp_path))
        with self.assertRaises(SystemExit):
            mod.load_config()

        os.unlink(tmp_path)

    def test_valid_config_loads(self):
        """有效配置应正常加载，不触发 sys.exit。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("""
proxy:
  host: "127.0.0.1"
  port: 48743
  log_level: "WARNING"

upstream:
  base_url: "https://example.com/v1"
  api_key: "sk-test"
  timeout: 120
  connect_timeout: 10
  ssl_verify: true
  retry: 1

model_map:
  "gpt-4o":
    target: "claude-sonnet-4-6"
    multimodal: false
  "*":
    target: "claude-sonnet-4-6"
    multimodal: false
""")
            tmp_path = f.name

        mod = load_proxy_module(Path(tmp_path))
        mod.load_config()
        cfg = mod.resolve_model("gpt-4o")
        self.assertEqual(cfg["target"], "claude-sonnet-4-6")
        self.assertFalse(cfg["multimodal"])
        # fallback
        cfg2 = mod.resolve_model("unknown-model")
        self.assertEqual(cfg2["target"], "claude-sonnet-4-6")

        os.unlink(tmp_path)

    def test_missing_model_map(self):
        """配置文件缺少 model_map 应导致 sys.exit(1)。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("""
proxy:
  host: "127.0.0.1"
  port: 48743

upstream:
  base_url: "https://example.com/v1"
  api_key: "sk-test"
""")
            tmp_path = f.name

        mod = load_proxy_module(Path(tmp_path))
        with self.assertRaises(SystemExit):
            mod.load_config()

        os.unlink(tmp_path)

    def test_missing_config_file(self):
        """配置文件不存在应导致 sys.exit(1)。"""
        mod = load_proxy_module(Path("/nonexistent/path.yaml"))
        with self.assertRaises(SystemExit):
            mod.load_config()


class TestYamlParser(unittest.TestCase):
    def test_parse_basic_types(self):
        from proxy import _parse_yaml
        result = _parse_yaml("""
proxy:
  host: "127.0.0.1"
  port: 48743
  log_level: "INFO"
  enabled: true
""")
        self.assertEqual(result["proxy"]["host"], "127.0.0.1")
        self.assertEqual(result["proxy"]["port"], 48743)
        self.assertEqual(result["proxy"]["log_level"], "INFO")
        self.assertTrue(result["proxy"]["enabled"])

    def test_parse_nested_structure(self):
        from proxy import _parse_yaml
        result = _parse_yaml("""
upstream:
  base_url: "https://example.com/v1"
  ssl_verify: false
  retry: 0
""")
        self.assertEqual(result["upstream"]["base_url"], "https://example.com/v1")
        self.assertFalse(result["upstream"]["ssl_verify"])
        self.assertEqual(result["upstream"]["retry"], 0)

    def test_parse_comments_and_blanks(self):
        from proxy import _parse_yaml
        result = _parse_yaml("""
# This is a comment
proxy:
  port: 48743  # inline comment

  # Another comment
  host: "127.0.0.1"
""")
        self.assertEqual(result["proxy"]["port"], 48743)
        self.assertEqual(result["proxy"]["host"], "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
