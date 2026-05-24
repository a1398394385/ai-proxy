"""动态模型配置 — ConfigDB → ConfigCache → resolve 完整链路集成测试。"""
import unittest
import tempfile
from pathlib import Path

from proxy.config_manager import ConfigDB, ConfigCache, Migrations


class TestConfigIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "config.db"
        self.db = ConfigDB(self.db_path)
        self.id_a = self.db.add_upstream({"name": "up-a", "base_url": "http://a:4000", "api_key": "sk-a"})
        self.id_b = self.db.add_upstream({"name": "up-b", "base_url": "http://b:5000", "api_key": "sk-b"})
        self.m1 = self.db.add_model({"name": "qwen", "upstream_id": self.id_a, "multimodal": 1})
        self.m2 = self.db.add_model({"name": "claude", "upstream_id": self.id_b, "multimodal": 0})
        self.db.add_route({"source": "gpt-4", "target_model_id": self.m1})
        self.db.add_route({"source": "codex-mini", "target_model_id": self.m1})
        self.db.add_route({"source": "*", "target_model_id": self.m2})
        # 注：get_route_by_source() 和 validate_star_default() 是 ConfigDB 内部方法，在此仅用于测试

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_config_cache_resolve(self):
        cache = ConfigCache(self.db_path)
        cfg = cache.resolve("gpt-4")
        self.assertEqual(cfg["target_name"], "qwen")
        self.assertEqual(cfg["upstream"]["base_url"], "http://a:4000")

    def test_config_cache_default(self):
        cache = ConfigCache(self.db_path)
        cfg = cache.resolve("unknown-model")
        self.assertEqual(cfg["target_name"], "claude")

    def test_disable_upstream_affects_resolve(self):
        self.db.disable_upstream(self.id_a)
        cache = ConfigCache(self.db_path)
        cfg = cache.resolve("gpt-4")
        self.assertEqual(cfg["target_name"], "claude")

    def test_star_default_validation(self):
        self.db.disable_upstream(self.id_b)
        self.assertFalse(self.db.validate_star_default())

    def test_model_referenced_routes_precheck(self):
        refs = self.db.model_referenced_routes(self.m1)
        self.assertEqual(set(refs), {"gpt-4", "codex-mini"})
        self.assertGreater(len(refs), 0)

    def test_upstream_active_routes(self):
        refs = self.db.upstream_active_routes(self.id_a)
        self.assertIn("gpt-4", refs)

    def test_get_counts(self):
        counts = self.db.get_counts()
        self.assertEqual(counts["upstreams"], 2)
        self.assertEqual(counts["models"], 2)
        self.assertEqual(counts["routes"], 3)

    def test_cache_reload(self):
        cache = ConfigCache(self.db_path)
        cfg1 = cache.resolve("gpt-4")
        rid = self.db.get_route_by_source("gpt-4")["id"]
        self.db.update_route(rid, {"target_model_id": self.m2})
        cache.reload()
        cfg2 = cache.resolve("gpt-4")
        self.assertEqual(cfg2["target_name"], "claude")

    def test_cache_get_all(self):
        cache = ConfigCache(self.db_path)
        all_routes = cache.get_all()
        self.assertIn("gpt-4", all_routes)
        self.assertIn("codex-mini", all_routes)
        self.assertIn("*", all_routes)

    def test_delete_model_with_check_refs(self):
        """FK SET NULL：被引用的模型 delete 后路由 target_model_id 设为 NULL。"""
        result = self.db.delete_model(self.m1)
        self.assertNotIn("error", result)
        self.assertIn("affected_routes", result)
        self.assertEqual(result["affected_routes"], 2)
        # 验证路由 target_model_id 为 NULL
        routes = self.db.list_routes(request_type=None)
        for r in routes:
            if r["source"] in ("gpt-4", "claude-3"):
                self.assertIsNone(r["target_model_id"])
                self.assertEqual(r["upstream_name"], "(已删除)")

    def test_migration_idempotent(self):
        """迁移幂等性：连续两次调用 migrate()，第二次返回 already_migrated。"""
        mg = Migrations(self.db_path)
        mg.migrate()
        result = mg.migrate()
        self.assertEqual(result["status"], "already_migrated")

    def test_migration_data_preserved(self):
        """迁移后数据完整性：路由数不变，所有路由 request_type 均为 'responses'。"""
        mg = Migrations(self.db_path)
        mg.migrate()
        routes = self.db.list_routes()
        self.assertEqual(len(routes), 3)
        for route in routes:
            self.assertEqual(route["request_type"], "responses")



class TestConfigCacheKeyIntegration(unittest.TestCase):
    """ConfigCache reload 后 upstream_api_keys 变更验证。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "config.db"
        self.db = ConfigDB(self.db_path)
        self.up_id = self.db.add_upstream({
            "name": "test-up", "base_url": "http://test:4000", "api_key": "sk-test"
        })
        self.cache = ConfigCache(self.db_path)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_add_key_then_reload_makes_it_available(self):
        """添加 key → reload → pick_key 返回该 key"""
        kid = self.db.add_upstream_key(self.up_id, "sk-key1")
        self.cache.reload()
        key = self.cache.pick_key(self.up_id)
        self.assertEqual(key, "sk-key1")

    def test_disable_key_then_reload_removes_from_rotation(self):
        """禁用 key → reload → pick_key 只返回剩余的活跃 key"""
        k1 = self.db.add_upstream_key(self.up_id, "sk-key1")
        k2 = self.db.add_upstream_key(self.up_id, "sk-key2")
        self.db.update_upstream_key(k1, {"is_active": 0})
        self.cache.reload()
        key = self.cache.pick_key(self.up_id)
        self.assertEqual(key, "sk-key2")

    def test_delete_key_then_reload(self):
        """删除 key → reload → pick_key 返回空字符串"""
        kid = self.db.add_upstream_key(self.up_id, "sk-key1")
        self.db.delete_upstream_key(kid)
        self.cache.reload()
        key = self.cache.pick_key(self.up_id)
        self.assertEqual(key, "")