"""proxy.py 配置加载校验测试。"""
import os
import sys
import unittest
import tempfile
import importlib.util
from pathlib import Path


def load_proxy_module(config_path_override: Path = None):
    """使用 importlib 动态加载 proxy.py，避免模块级 load_config() 执行。"""
    proxy_py = Path(__file__).parent.parent / "proxy.py"
    spec = importlib.util.spec_from_file_location("proxy_test", str(proxy_py))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if config_path_override:
        mod.CONFIG_PATH = config_path_override
    return mod


class TestConfigValidation(unittest.TestCase):
    def test_valid_config_loads(self):
        """有效配置正常加载，resolve_model 从动态缓存读取。"""
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
""")
            tmp_path = f.name

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "config.db"
            from config_manager import ConfigDB, ConfigCache
            db = ConfigDB(db_path)
            db.add_upstream({"id": "test-up", "base_url": "https://example.com/v1", "api_key": "sk-test"})
            m1 = db.add_model({"name": "claude-sonnet-4-6", "upstream_id": "test-up", "multimodal": 0})
            db.add_route({"source": "gpt-4o", "target_model_id": m1})
            db.add_route({"source": "*", "target_model_id": m1})
            db.close()

            mod = load_proxy_module(Path(tmp_path))
            # 直接设置 common 模块的变量，因为 load_config/resolve_model 在 common.py 中
            import common
            common.config_cache = ConfigCache(db_path)
            common.CONFIG_PATH = Path(tmp_path)
            mod.load_config()
            cfg = mod.resolve_model("gpt-4o")
            self.assertEqual(cfg["target"], "claude-sonnet-4-6")
            self.assertFalse(cfg["multimodal"])
            cfg2 = mod.resolve_model("unknown-model")
            self.assertEqual(cfg2["target"], "claude-sonnet-4-6")

        os.unlink(tmp_path)

    def test_missing_config_file(self):
        """配置文件不存在应导致 sys.exit(1)。"""
        import common
        common.CONFIG_PATH = Path("/nonexistent/path.yaml")
        mod = load_proxy_module()
        with self.assertRaises(SystemExit):
            mod.load_config()


class TestYamlParser(unittest.TestCase):
    def test_parse_basic_types(self):
        from common import _parse_yaml
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
        from common import _parse_yaml
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
        from common import _parse_yaml
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
