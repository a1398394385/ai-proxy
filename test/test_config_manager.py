import unittest
import tempfile
import sqlite3
from pathlib import Path


class TestConfigDBInit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "config.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_init_creates_database_file(self):
        from proxy.config_manager import ConfigDB
        db = ConfigDB(self.db_path)
        self.assertTrue(self.db_path.exists())
        db.close()

    def test_init_creates_all_tables(self):
        from proxy.config_manager import ConfigDB
        db = ConfigDB(self.db_path)
        conn = sqlite3.connect(str(self.db_path))
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()]
        conn.close()
        db.close()
        self.assertIn("schema_version", tables)
        self.assertIn("upstreams", tables)
        self.assertIn("target_models", tables)
        self.assertIn("model_routes", tables)

    def test_pragma_foreign_keys_enabled(self):
        from proxy.config_manager import ConfigDB
        db = ConfigDB(self.db_path)
        conn = db._connect()
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        conn.close()
        db.close()
        self.assertEqual(fk, 1)

    def test_pragma_wal_mode(self):
        from proxy.config_manager import ConfigDB
        db = ConfigDB(self.db_path)
        conn = db._connect()
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        db.close()
        self.assertEqual(journal, "wal")

    def test_pragma_busy_timeout(self):
        from proxy.config_manager import ConfigDB
        db = ConfigDB(self.db_path)
        conn = db._connect()
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        conn.close()
        db.close()
        self.assertEqual(timeout, 3000)

    def test_init_idempotent(self):
        from proxy.config_manager import ConfigDB
        db1 = ConfigDB(self.db_path)
        db1.close()
        db2 = ConfigDB(self.db_path)
        db2.close()
        self.assertTrue(self.db_path.exists())


class TestUpstreamCRUD(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "config.db"
        from proxy.config_manager import ConfigDB
        self.db = ConfigDB(self.db_path)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_add_and_list_upstreams(self):
        self.db.add_upstream({
            "id": "litellm-prod",
            "base_url": "https://llm.cargoware.com/v1",
            "api_key": "sk-test123",
            "timeout": 120,
            "connect_timeout": 10,
            "ssl_verify": 1,
            "retry": 2,
        })
        upstreams = self.db.list_upstreams()
        self.assertEqual(len(upstreams), 1)
        self.assertEqual(upstreams[0]["id"], "litellm-prod")
        self.assertEqual(upstreams[0]["api_key"], "sk-test123")

    def test_get_upstream(self):
        self.db.add_upstream({"id": "test-up", "base_url": "http://x"})
        u = self.db.get_upstream("test-up")
        self.assertEqual(u["base_url"], "http://x")

    def test_get_upstream_not_found(self):
        self.assertIsNone(self.db.get_upstream("nonexistent"))

    def test_update_upstream(self):
        self.db.add_upstream({"id": "test-up", "base_url": "http://x"})
        self.db.update_upstream("test-up", {"base_url": "http://y", "timeout": 60})
        u = self.db.get_upstream("test-up")
        self.assertEqual(u["base_url"], "http://y")
        self.assertEqual(u["timeout"], 60)

    def test_disable_upstream(self):
        self.db.add_upstream({"id": "test-up", "base_url": "http://x"})
        self.db.disable_upstream("test-up")
        u = self.db.get_upstream("test-up")
        self.assertEqual(u["is_active"], 0)

    def test_list_upstreams_active_only(self):
        self.db.add_upstream({"id": "up-a", "base_url": "http://a"})
        self.db.add_upstream({"id": "up-b", "base_url": "http://b"})
        self.db.disable_upstream("up-b")
        active = self.db.list_upstreams(active_only=True)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["id"], "up-a")


class TestModelCRUD(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "config.db"
        from proxy.config_manager import ConfigDB
        self.db = ConfigDB(self.db_path)
        self.db.add_upstream({"id": "up-a", "base_url": "http://a"})
        self.db.add_upstream({"id": "up-b", "base_url": "http://b"})

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_add_and_list_models(self):
        mid = self.db.add_model({"name": "qwen-plus", "upstream_id": "up-a"})
        self.assertIsInstance(mid, int)
        models = self.db.list_models()
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]["name"], "qwen-plus")
        self.assertEqual(models[0]["upstream_name"], "up-a")

    def test_list_models_filter_by_upstream(self):
        self.db.add_model({"name": "qwen-a", "upstream_id": "up-a"})
        self.db.add_model({"name": "qwen-b", "upstream_id": "up-b"})
        models_a = self.db.list_models(upstream_id="up-a")
        self.assertEqual(len(models_a), 1)
        self.assertEqual(models_a[0]["name"], "qwen-a")

    def test_add_duplicate_model_same_upstream_raises(self):
        self.db.add_model({"name": "qwen", "upstream_id": "up-a"})
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.add_model({"name": "qwen", "upstream_id": "up-a"})

    def test_same_name_different_upstream_ok(self):
        self.db.add_model({"name": "qwen", "upstream_id": "up-a"})
        mid = self.db.add_model({"name": "qwen", "upstream_id": "up-b"})
        self.assertIsInstance(mid, int)

    def test_update_model(self):
        mid = self.db.add_model({"name": "qwen", "upstream_id": "up-a"})
        self.db.update_model(mid, {"name": "qwen-plus", "multimodal": 0})
        m = self.db.get_model(mid)
        self.assertEqual(m["name"], "qwen-plus")
        self.assertEqual(m["multimodal"], 0)

    def test_delete_model(self):
        mid = self.db.add_model({"name": "qwen", "upstream_id": "up-a"})
        result = self.db.delete_model(mid)
        self.assertIn("message", result)
        self.assertIsNone(self.db.get_model(mid))

    def test_delete_model_referenced_by_route_raises(self):
        mid = self.db.add_model({"name": "qwen", "upstream_id": "up-a"})
        self.db.add_route({"source": "gpt-4", "target_model_id": mid})
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.delete_model(mid, check_refs=False)
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM target_models WHERE id = ?", (mid,))
            conn.commit()
        conn.close()

    def test_model_referenced_routes(self):
        mid = self.db.add_model({"name": "qwen", "upstream_id": "up-a"})
        self.db.add_route({"source": "gpt-4", "target_model_id": mid})
        self.db.add_route({"source": "o4-mini", "target_model_id": mid})
        refs = self.db.model_referenced_routes(mid)
        self.assertEqual(set(refs), {"gpt-4", "o4-mini"})


class TestDeleteUpstream(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "config.db"
        from proxy.config_manager import ConfigDB
        self.db = ConfigDB(self.db_path)
        self.db.add_upstream({"id": "up-a", "base_url": "http://a"})
        self.db.add_upstream({"id": "up-b", "base_url": "http://b"})
        self.m1 = self.db.add_model({"name": "gpt-4", "upstream_id": "up-a"})
        self.m2 = self.db.add_model({"name": "gpt-35", "upstream_id": "up-a"})
        self.m3 = self.db.add_model({"name": "claude", "upstream_id": "up-b"})

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_delete_upstream_with_models_no_refs(self):
        """无路由引用时，删除上游及其关联模型。"""
        self.db.delete_upstream_with_models("up-a")
        # 上游已删除
        self.assertIsNone(self.db.get_upstream("up-a"))
        # 关联模型已删除
        models = self.db.list_models(upstream_id="up-a")
        self.assertEqual(len(models), 0)
        # 其他上游不受影响
        self.assertIsNotNone(self.db.get_upstream("up-b"))
        self.assertEqual(len(self.db.list_models(upstream_id="up-b")), 1)


class TestRouteCRUD(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "config.db"
        from proxy.config_manager import ConfigDB
        self.db = ConfigDB(self.db_path)
        self.db.add_upstream({"id": "up-a", "base_url": "http://a"})
        self.mid = self.db.add_model({"name": "qwen", "upstream_id": "up-a"})

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_add_and_list_routes(self):
        rid = self.db.add_route({"source": "gpt-4", "target_model_id": self.mid})
        self.assertIsInstance(rid, int)
        routes = self.db.list_routes()
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["source"], "gpt-4")
        self.assertEqual(routes[0]["target_name"], "qwen")

    def test_star_route_orders_first(self):
        self.db.add_route({"source": "z-model", "target_model_id": self.mid})
        self.db.add_route({"source": "*", "target_model_id": self.mid})
        routes = self.db.list_routes()
        self.assertEqual(routes[0]["source"], "*")

    def test_update_route(self):
        rid = self.db.add_route({"source": "gpt-4", "target_model_id": self.mid})
        mid2 = self.db.add_model({"name": "claude", "upstream_id": "up-a"})
        self.db.update_route(rid, {"source": "gpt-4o", "target_model_id": mid2})
        r = self.db.get_route(rid)
        self.assertEqual(r["source"], "gpt-4o")
        self.assertEqual(r["target_name"], "claude")

    def test_update_route_rejects_fallback_source_change(self):
        """更新回退路由 (source=*) 时不允许修改 source。"""
        rid = self.db.add_route({"source": "*", "target_model_id": self.mid})
        with self.assertRaises(ValueError):
            self.db.update_route(rid, {"source": "gpt-4o"})
        # 确认 source 保持不变
        r = self.db.get_route(rid)
        self.assertEqual(r["source"], "*")

    def test_update_route_allows_fallback_update_without_source(self):
        """编辑回退路由时不传 source 字段 → 正常更新（如只改目标模型）。"""
        rid = self.db.add_route({"source": "*", "target_model_id": self.mid})
        mid2 = self.db.add_model({"name": "claude", "upstream_id": "up-a"})
        # 不传 source，只改 target_model_id
        self.db.update_route(rid, {"target_model_id": mid2})
        r = self.db.get_route(rid)
        self.assertEqual(r["source"], "*")
        self.assertEqual(r["target_name"], "claude")
    def test_delete_route(self):
        rid = self.db.add_route({"source": "z-model", "target_model_id": self.mid})
        self.db.delete_route(rid)
        self.assertIsNone(self.db.get_route(rid))

    def test_fk_restrict_upstream_delete(self):
        with self.assertRaises(sqlite3.IntegrityError):
            conn = self.db._connect()
            conn.execute("DELETE FROM upstreams WHERE id = ?", ("up-a",))
            conn.close()


class TestResolveModel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "config.db"
        from proxy.config_manager import ConfigDB
        self.db = ConfigDB(self.db_path)
        self.db.add_upstream({"id": "up-a", "base_url": "http://a", "api_key": "sk-a"})
        self.db.add_upstream({"id": "up-b", "base_url": "http://b", "api_key": "sk-b"})
        self.m1 = self.db.add_model({"name": "qwen", "upstream_id": "up-a"})
        self.m2 = self.db.add_model({"name": "claude", "upstream_id": "up-b"})

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_resolve_exact_match(self):
        self.db.add_route({"source": "gpt-4", "target_model_id": self.m1})
        cfg = self.db.resolve_model("gpt-4")
        self.assertEqual(cfg["target_name"], "qwen")
        self.assertEqual(cfg["upstream"]["base_url"], "http://a")

    def test_resolve_fallback_to_star(self):
        self.db.add_route({"source": "*", "target_model_id": self.m2})
        cfg = self.db.resolve_model("unknown-model")
        self.assertEqual(cfg["target_name"], "claude")

    def test_resolve_skip_disabled_upstream(self):
        self.db.add_route({"source": "gpt-4", "target_model_id": self.m1})
        self.db.add_route({"source": "*", "target_model_id": self.m2})
        self.db.disable_upstream("up-a")
        cfg = self.db.resolve_model("gpt-4")
        self.assertEqual(cfg["target_name"], "claude")

    def test_resolve_none_when_no_match(self):
        cfg = self.db.resolve_model("no-route-anywhere")
        self.assertIsNone(cfg)

    def test_resolve_none_when_star_also_disabled(self):
        self.db.add_route({"source": "*", "target_model_id": self.m1})
        self.db.disable_upstream("up-a")
        cfg = self.db.resolve_model("anything")
        self.assertIsNone(cfg)

    def test_get_all_routes(self):
        self.db.add_route({"source": "gpt-4", "target_model_id": self.m1})
        self.db.add_route({"source": "*", "target_model_id": self.m2})
        all_routes = self.db.get_all_routes()
        self.assertIn("gpt-4", all_routes)
        self.assertIn("*", all_routes)
        self.assertEqual(all_routes["gpt-4"]["target_name"], "qwen")

    def test_get_all_routes_skips_disabled_upstream(self):
        self.db.add_route({"source": "gpt-4", "target_model_id": self.m1})
        self.db.add_route({"source": "*", "target_model_id": self.m2})
        self.db.disable_upstream("up-a")
        all_routes = self.db.get_all_routes()
        self.assertNotIn("gpt-4", all_routes)
        self.assertIn("*", all_routes)

    def test_validate_star_fallback(self):
        self.db.add_route({"source": "*", "target_model_id": self.m1})
        self.assertTrue(self.db.validate_star_fallback())

    def test_matched_source_field(self):
        self.db.add_route({"source": "gpt-4", "target_model_id": self.m1})
        self.db.add_route({"source": "*", "target_model_id": self.m2})
        cfg = self.db.resolve_model("gpt-4")
        self.assertEqual(cfg["matched_source"], "gpt-4")
        cfg2 = self.db.resolve_model("nonexistent")
        self.assertEqual(cfg2["matched_source"], "*")


class TestConfigCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "config.db"
        from proxy.config_manager import ConfigDB, ConfigCache
        self.db = ConfigDB(self.db_path)
        self.db.add_upstream({"id": "up-a", "base_url": "http://a"})
        self.m1 = self.db.add_model({"name": "qwen", "upstream_id": "up-a"})
        self.db.add_route({"source": "*", "target_model_id": self.m1})

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_cache_resolve_returns_config(self):
        from proxy.config_manager import ConfigCache
        cache = ConfigCache(self.db_path, ttl=5)
        cfg = cache.resolve("unknown")
        self.assertEqual(cfg["target_name"], "qwen")

    def test_cache_hit_avoids_db_read(self):
        from proxy.config_manager import ConfigCache
        cache = ConfigCache(self.db_path, ttl=99)
        cfg1 = cache.resolve("*")
        m2 = self.db.add_model({"name": "claude", "upstream_id": "up-a"})
        star_rid = self.db.get_route_by_source("*")["id"]
        self.db.update_route(star_rid, {"target_model_id": m2})
        cfg2 = cache.resolve("*")
        self.assertEqual(cfg1["target_name"], cfg2["target_name"])

    def test_reload_refreshes_cache(self):
        from proxy.config_manager import ConfigCache
        cache = ConfigCache(self.db_path, ttl=99)
        cfg1 = cache.resolve("*")
        m2 = self.db.add_model({"name": "claude", "upstream_id": "up-a"})
        star_rid = self.db.get_route_by_source("*")["id"]
        self.db.update_route(star_rid, {"target_model_id": m2})
        cache.reload()
        cfg2 = cache.resolve("*")
        self.assertEqual(cfg2["target_name"], "claude")

    def test_get_all(self):
        from proxy.config_manager import ConfigCache
        cache = ConfigCache(self.db_path, ttl=5)
        all_routes = cache.get_all()
        self.assertIn("*", all_routes)

    def test_ttl_expiry_refreshes(self):
        from proxy.config_manager import ConfigCache
        cache = ConfigCache(self.db_path, ttl=0)
        cfg1 = cache.resolve("*")
        m2 = self.db.add_model({"name": "claude", "upstream_id": "up-a"})
        star_rid = self.db.get_route_by_source("*")["id"]
        self.db.update_route(star_rid, {"target_model_id": m2})
        cfg2 = cache.resolve("*")
        self.assertEqual(cfg2["target_name"], "claude")


class TestRouteProxyType(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "config.db"
        from proxy.config_manager import ConfigDB
        self.db = ConfigDB(self.db_path)
        self.db.add_upstream({"id": "up-a", "base_url": "http://a", "api_key": "sk-a"})
        self.db.add_upstream({"id": "up-b", "base_url": "http://b", "api_key": "sk-b"})
        self.m1 = self.db.add_model({"name": "qwen", "upstream_id": "up-a"})
        self.m2 = self.db.add_model({"name": "claude", "upstream_id": "up-b"})

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_list_routes_filter_by_request_type(self):
        self.db.add_route({"source": "gpt-4", "target_model_id": self.m1, "request_type": "responses"})
        self.db.add_route({"source": "gpt-4", "target_model_id": self.m2, "request_type": "messages"})
        self.db.add_route({"source": "sonnet", "target_model_id": self.m1, "request_type": "messages"})

        responses_routes = self.db.list_routes(request_type="responses")
        messages_routes = self.db.list_routes(request_type="messages")
        all_routes = self.db.list_routes()

        self.assertEqual(len(responses_routes), 1)
        self.assertEqual(responses_routes[0]["request_type"], "responses")
        self.assertEqual(len(messages_routes), 2)
        self.assertEqual(len(all_routes), 3)

    def test_add_route_invalid_request_type(self):
        with self.assertRaises(ValueError):
            self.db.add_route({"source": "gpt-4", "target_model_id": self.m1, "request_type": "invalid"})

    def test_add_route_duplicate_source_same_request_type(self):
        self.db.add_route({"source": "gpt-4", "target_model_id": self.m1, "request_type": "responses"})
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.add_route({"source": "gpt-4", "target_model_id": self.m2, "request_type": "responses"})

    def test_add_route_same_source_different_request_type(self):
        self.db.add_route({"source": "gpt-4", "target_model_id": self.m1, "request_type": "responses"})
        rid = self.db.add_route({"source": "gpt-4", "target_model_id": self.m2, "request_type": "messages"})
        self.assertIsInstance(rid, int)

    def test_resolve_one_request_type_isolation(self):
        self.db.add_route({"source": "gpt-4o", "target_model_id": self.m1, "request_type": "responses"})
        self.db.add_route({"source": "gpt-4o", "target_model_id": self.m2, "request_type": "messages"})

        responses_result = self.db.resolve_one("gpt-4o", "responses")
        messages_result = self.db.resolve_one("gpt-4o", "messages")

        self.assertEqual(responses_result["target_name"], "qwen")
        self.assertEqual(messages_result["target_name"], "claude")
        self.assertNotEqual(responses_result["target_name"], messages_result["target_name"])

