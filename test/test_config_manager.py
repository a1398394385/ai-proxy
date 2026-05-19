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
        uid = self.db.add_upstream({
            "name": "litellm-prod",
            "base_url": "https://llm.cargoware.com/v1",
            "api_key": "sk-test123",
            "timeout": 120,
            "connect_timeout": 10,
            "ssl_verify": 1,
            "retry": 2,
        })
        self.assertIsInstance(uid, int)
        upstreams = self.db.list_upstreams()
        self.assertEqual(len(upstreams), 1)
        self.assertEqual(upstreams[0]["name"], "litellm-prod")
        self.assertEqual(upstreams[0]["api_key"], "sk-test123")

    def test_get_upstream(self):
        uid = self.db.add_upstream({"name": "test-up", "base_url": "http://x"})
        u = self.db.get_upstream(uid)
        self.assertEqual(u["base_url"], "http://x")

    def test_get_upstream_not_found(self):
        self.assertIsNone(self.db.get_upstream(999))

    def test_update_upstream(self):
        uid = self.db.add_upstream({"name": "test-up", "base_url": "http://x"})
        self.db.update_upstream(uid, {"base_url": "http://y", "timeout": 60})
        u = self.db.get_upstream(uid)
        self.assertEqual(u["base_url"], "http://y")
        self.assertEqual(u["timeout"], 60)

    def test_disable_upstream(self):
        uid = self.db.add_upstream({"name": "test-up", "base_url": "http://x"})
        self.db.disable_upstream(uid)
        u = self.db.get_upstream(uid)
        self.assertEqual(u["is_active"], 0)

    def test_list_upstreams_active_only(self):
        uid_a = self.db.add_upstream({"name": "up-a", "base_url": "http://a"})
        uid_b = self.db.add_upstream({"name": "up-b", "base_url": "http://b"})
        self.db.disable_upstream(uid_b)
        active = self.db.list_upstreams(active_only=True)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["name"], "up-a")


class TestModelCRUD(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "config.db"
        from proxy.config_manager import ConfigDB
        self.db = ConfigDB(self.db_path)
        self.id_a = self.db.add_upstream({"name": "up-a", "base_url": "http://a"})
        self.id_b = self.db.add_upstream({"name": "up-b", "base_url": "http://b"})

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_add_and_list_models(self):
        mid = self.db.add_model({"name": "qwen-plus", "upstream_id": self.id_a})
        self.assertIsInstance(mid, int)
        models = self.db.list_models()
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]["name"], "qwen-plus")
        self.assertEqual(models[0]["upstream_name"], "up-a")

    def test_list_models_filter_by_upstream(self):
        self.db.add_model({"name": "qwen-a", "upstream_id": self.id_a})
        self.db.add_model({"name": "qwen-b", "upstream_id": self.id_b})
        models_a = self.db.list_models(upstream_id=self.id_a)
        self.assertEqual(len(models_a), 1)
        self.assertEqual(models_a[0]["name"], "qwen-a")

    def test_add_duplicate_model_same_upstream_raises(self):
        self.db.add_model({"name": "qwen", "upstream_id": self.id_a})
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.add_model({"name": "qwen", "upstream_id": self.id_a})

    def test_same_name_different_upstream_ok(self):
        self.db.add_model({"name": "qwen", "upstream_id": self.id_a})
        mid = self.db.add_model({"name": "qwen", "upstream_id": self.id_b})
        self.assertIsInstance(mid, int)

    def test_update_model(self):
        mid = self.db.add_model({"name": "qwen", "upstream_id": self.id_a})
        self.db.update_model(mid, {"name": "qwen-plus", "multimodal": 0})
        m = self.db.get_model(mid)
        self.assertEqual(m["name"], "qwen-plus")
        self.assertEqual(m["multimodal"], 0)

    def test_delete_model(self):
        mid = self.db.add_model({"name": "qwen", "upstream_id": self.id_a})
        result = self.db.delete_model(mid)
        self.assertIn("message", result)
        self.assertIsNone(self.db.get_model(mid))

    def test_delete_model_referenced_by_route_raises(self):
        mid = self.db.add_model({"name": "qwen", "upstream_id": self.id_a})
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
        mid = self.db.add_model({"name": "qwen", "upstream_id": self.id_a})
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
        self.id_a = self.db.add_upstream({"name": "up-a", "base_url": "http://a"})
        self.id_b = self.db.add_upstream({"name": "up-b", "base_url": "http://b"})
        self.m1 = self.db.add_model({"name": "gpt-4", "upstream_id": self.id_a})
        self.m2 = self.db.add_model({"name": "gpt-35", "upstream_id": self.id_a})
        self.m3 = self.db.add_model({"name": "claude", "upstream_id": self.id_b})

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_delete_upstream_with_models_no_refs(self):
        """无路由引用时，删除上游及其关联模型。"""
        self.db.delete_upstream_with_models(self.id_a)
        # 上游已删除
        self.assertIsNone(self.db.get_upstream(self.id_a))
        # 关联模型已删除
        models = self.db.list_models(upstream_id=self.id_a)
        self.assertEqual(len(models), 0)
        # 其他上游不受影响
        self.assertIsNotNone(self.db.get_upstream(self.id_b))
        self.assertEqual(len(self.db.list_models(upstream_id=self.id_b)), 1)

    def test_delete_upstream_cascades_to_models(self):
        """验证删除上游后，该上游下的模型全部被删除。"""
        # 确认 up-a 下有 2 个模型
        models_before = self.db.list_models(upstream_id=self.id_a)
        self.assertEqual(len(models_before), 2)

        self.db.delete_upstream_with_models(self.id_a)

        # 上游已不存在
        self.assertIsNone(self.db.get_upstream(self.id_a))
        # 模型也全部消失
        models_after = self.db.list_models(upstream_id=self.id_a)
        self.assertEqual(len(models_after), 0)

    def test_delete_upstream_rollback_on_error(self):
        """执行过程中抛出异常，事务应回滚，上游和模型保持不变。"""
        call_count = [0]

        class FailingConn:
            def __init__(self, real):
                self._real = real

            def execute(self, sql, params=None):
                call_count[0] += 1
                # 在第三次 execute（第一个 DELETE FROM target_models）时抛出异常
                if call_count[0] == 3 and "DELETE FROM target_models" in (sql or ""):
                    raise RuntimeError("Simulated failure")
                if params is None:
                    return self._real.execute(sql)
                return self._real.execute(sql, params)

            def __getattr__(self, name):
                return getattr(self._real, name)

        original_connect = self.db._connect

        def patched_connect():
            real = original_connect()
            return FailingConn(real)

        self.db._connect = patched_connect

        try:
            with self.assertRaises(RuntimeError):
                self.db.delete_upstream_with_models(self.id_a)
        finally:
            self.db._connect = original_connect

        # 验证回滚：上游和模型仍然存在
        self.assertIsNotNone(self.db.get_upstream(self.id_a))
        models_after = self.db.list_models(upstream_id=self.id_a)
        self.assertEqual(len(models_after), 2)


class TestRouteCRUD(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "config.db"
        from proxy.config_manager import ConfigDB
        self.db = ConfigDB(self.db_path)
        self.id_a = self.db.add_upstream({"name": "up-a", "base_url": "http://a"})
        self.mid = self.db.add_model({"name": "qwen", "upstream_id": self.id_a})

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
        mid2 = self.db.add_model({"name": "claude", "upstream_id": self.id_a})
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
        mid2 = self.db.add_model({"name": "claude", "upstream_id": self.id_a})
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
            conn.execute("DELETE FROM upstreams WHERE id = ?", (self.id_a,))
            conn.close()


class TestResolveModel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "config.db"
        from proxy.config_manager import ConfigDB
        self.db = ConfigDB(self.db_path)
        self.id_a = self.db.add_upstream({"name": "up-a", "base_url": "http://a", "api_key": "sk-a"})
        self.id_b = self.db.add_upstream({"name": "up-b", "base_url": "http://b", "api_key": "sk-b"})
        self.m1 = self.db.add_model({"name": "qwen", "upstream_id": self.id_a})
        self.m2 = self.db.add_model({"name": "claude", "upstream_id": self.id_b})

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
        self.db.disable_upstream(self.id_a)
        cfg = self.db.resolve_model("gpt-4")
        self.assertEqual(cfg["target_name"], "claude")

    def test_resolve_none_when_no_match(self):
        cfg = self.db.resolve_model("no-route-anywhere")
        self.assertIsNone(cfg)

    def test_resolve_none_when_star_also_disabled(self):
        self.db.add_route({"source": "*", "target_model_id": self.m1})
        self.db.disable_upstream(self.id_a)
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
        self.db.disable_upstream(self.id_a)
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
        self.id_a = self.db.add_upstream({"name": "up-a", "base_url": "http://a"})
        self.m1 = self.db.add_model({"name": "qwen", "upstream_id": self.id_a})
        self.db.add_route({"source": "*", "target_model_id": self.m1})

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_cache_resolve_returns_config(self):
        from proxy.config_manager import ConfigCache
        cache = ConfigCache(self.db_path)
        cfg = cache.resolve("unknown")
        self.assertEqual(cfg["target_name"], "qwen")

    def test_cache_hit_avoids_db_read(self):
        from proxy.config_manager import ConfigCache
        cache = ConfigCache(self.db_path)
        cfg1 = cache.resolve("*")
        m2 = self.db.add_model({"name": "claude", "upstream_id": self.id_a})
        star_rid = self.db.get_route_by_source("*")["id"]
        self.db.update_route(star_rid, {"target_model_id": m2})
        cfg2 = cache.resolve("*")
        self.assertEqual(cfg1["target_name"], cfg2["target_name"])

    def test_reload_refreshes_cache(self):
        from proxy.config_manager import ConfigCache
        cache = ConfigCache(self.db_path)
        cfg1 = cache.resolve("*")
        m2 = self.db.add_model({"name": "claude", "upstream_id": self.id_a})
        star_rid = self.db.get_route_by_source("*")["id"]
        self.db.update_route(star_rid, {"target_model_id": m2})
        cache.reload()
        cfg2 = cache.resolve("*")
        self.assertEqual(cfg2["target_name"], "claude")

    def test_get_all(self):
        from proxy.config_manager import ConfigCache
        cache = ConfigCache(self.db_path)
        all_routes = cache.get_all()
        self.assertIn("*", all_routes)

    def test_resolve_direct_match(self):
        """直线路由：匹配已注册上游+模型 → 返回配置。"""
        from proxy.config_manager import ConfigCache
        cache = ConfigCache(self.db_path)
        cfg = cache.resolve_direct("up-a/qwen")
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg["target_name"], "qwen")
        self.assertEqual(cfg["upstream"]["id"], self.id_a)

    def test_resolve_direct_no_slash(self):
        """直线路由：model 不含 / → 不命中。"""
        from proxy.config_manager import ConfigCache
        cache = ConfigCache(self.db_path)
        self.assertIsNone(cache.resolve_direct("qwen"))

    def test_resolve_direct_model_not_registered(self):
        """直线路由：模型未注册 → 不命中。"""
        from proxy.config_manager import ConfigCache
        cache = ConfigCache(self.db_path)
        self.assertIsNone(cache.resolve_direct("up-a/unknown-model"))

    def test_resolve_direct_case_sensitive(self):
        """直线路由：上游名大小写敏感 → 不命中。"""
        from proxy.config_manager import ConfigCache
        cache = ConfigCache(self.db_path)
        self.assertIsNone(cache.resolve_direct("Up-A/qwen"))

    def test_resolve_direct_longest_prefix(self):
        """直线路由：长上游名优先匹配。"""
        from proxy.config_manager import ConfigCache
        id_b = self.db.add_upstream({"name": "up-a-longer", "base_url": "http://b"})
        self.db.add_model({"name": "qwen", "upstream_id": id_b})
        cache = ConfigCache(self.db_path)
        cfg = cache.resolve_direct("up-a-longer/qwen")
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg["upstream"]["id"], id_b)

    def test_resolve_direct_cache_reload(self):
        """直线路由：reload 后缓存重建。"""
        from proxy.config_manager import ConfigCache
        cache = ConfigCache(self.db_path)
        cfg1 = cache.resolve_direct("up-a/qwen")
        self.assertIsNotNone(cfg1)
        id_b = self.db.add_upstream({"name": "up-b", "base_url": "http://b"})
        self.db.add_model({"name": "claude", "upstream_id": id_b})
        cache.reload()
        cfg2 = cache.resolve_direct("up-b/claude")
        self.assertIsNotNone(cfg2)
        self.assertEqual(cfg2["target_name"], "claude")


class TestRouteProxyType(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "config.db"
        from proxy.config_manager import ConfigDB
        self.db = ConfigDB(self.db_path)
        self.id_a = self.db.add_upstream({"name": "up-a", "base_url": "http://a", "api_key": "sk-a"})
        self.id_b = self.db.add_upstream({"name": "up-b", "base_url": "http://b", "api_key": "sk-b"})
        self.m1 = self.db.add_model({"name": "qwen", "upstream_id": self.id_a})
        self.m2 = self.db.add_model({"name": "claude", "upstream_id": self.id_b})

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


class TestMigrations(unittest.TestCase):
    """Migrations 测试。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "config.db"

    def tearDown(self):
        self.tmp.cleanup()

    def _create_v4_db(self):
        """创建 v4 schema 的数据库。"""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript("""
            CREATE TABLE schema_version (version INTEGER NOT NULL);
            INSERT INTO schema_version (version) VALUES (4);

            CREATE TABLE upstreams (
                id              TEXT PRIMARY KEY,
                base_url        TEXT NOT NULL,
                api_key         TEXT NOT NULL DEFAULT '',
                timeout         INTEGER NOT NULL DEFAULT 600,
                connect_timeout INTEGER NOT NULL DEFAULT 10,
                ssl_verify      INTEGER NOT NULL DEFAULT 1,
                retry           INTEGER NOT NULL DEFAULT 1,
                is_active       INTEGER NOT NULL DEFAULT 1,
                format          TEXT NOT NULL DEFAULT 'chat_completions',
                created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
            );
            INSERT INTO upstreams (id, base_url) VALUES ('openai', 'https://api.openai.com');
            INSERT INTO upstreams (id, base_url) VALUES ('anthropic', 'https://api.anthropic.com');

            CREATE TABLE target_models (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                upstream_id TEXT NOT NULL REFERENCES upstreams(id) ON DELETE RESTRICT,
                multimodal  INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(name, upstream_id)
            );
            INSERT INTO target_models (name, upstream_id) VALUES ('gpt-4', 'openai');
            INSERT INTO target_models (name, upstream_id) VALUES ('claude-3', 'anthropic');

            CREATE TABLE model_routes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                source          TEXT NOT NULL,
                target_model_id INTEGER NOT NULL REFERENCES target_models(id) ON DELETE RESTRICT,
                request_type    TEXT NOT NULL DEFAULT 'responses',
                created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(source, request_type)
            );
        """)
        conn.commit()
        conn.close()

    def test_v5_migration_adds_name_column(self):
        """v4 → v5 迁移应添加 name 列并将旧 id 复制为 name。"""
        from proxy.config_manager import Migrations
        self._create_v4_db()

        m = Migrations(self.db_path)
        result = m.migrate()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["version"], 7)

        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            # 检查 name 列存在
            cols = {r["name"] for r in conn.execute("PRAGMA table_info('upstreams')").fetchall()}
            self.assertIn("name", cols)
            # 检查 id 是 INTEGER
            id_type = None
            for r in conn.execute("PRAGMA table_info('upstreams')").fetchall():
                if r["name"] == "id":
                    id_type = r["type"]
                    break
            self.assertEqual(id_type.upper(), "INTEGER")
            # 检查数据正确
            rows = conn.execute("SELECT id, name, base_url FROM upstreams ORDER BY id").fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["name"], "openai")  # 旧 id → name
            self.assertEqual(rows[0]["base_url"], "https://api.openai.com")
            self.assertIsInstance(rows[0]["id"], int)
            # 检查 target_models 的 upstream_id 已映射为 INTEGER
            models = conn.execute("SELECT name, upstream_id FROM target_models ORDER BY id").fetchall()
            self.assertEqual(models[0]["upstream_id"], rows[0]["id"])
        finally:
            conn.close()

    def test_v5_migration_is_idempotent_when_already_done(self):
        """已迁移到 v5 的数据库再次调用 migrate() 应返回 already_migrated。"""
        from proxy.config_manager import Migrations
        self._create_v4_db()

        m = Migrations(self.db_path)
        m.migrate()  # 第一次迁移
        result = m.migrate()  # 第二次迁移
        self.assertEqual(result["status"], "already_migrated")



class TestAgentRouteCRUD(unittest.TestCase):
    def setUp(self):
        from proxy.config_manager import ConfigDB
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "config.db"
        self.db = ConfigDB(self.db_path)
        # 创建上游 + 模型供路由引用
        self.upstream_id = self.db.add_upstream({
            "name": "test-upstream", "base_url": "http://localhost:8000",
            "api_key": "sk-test", "format": "chat_completions"
        })
        self.model_id = self.db.add_model({
            "name": "test-model", "upstream_id": self.upstream_id
        })

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_add_and_list_agent_routes(self):
        rid = self.db.add_agent_route({
            "source": "claude-sonnet-4-6",
            "target_model_id": self.model_id,
            "request_type": "chat_completions"
        })
        self.assertIsNotNone(rid)
        routes = self.db.list_agent_routes()
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["source"], "claude-sonnet-4-6")

    def test_list_agent_routes_filter_by_request_type(self):
        self.db.add_agent_route({
            "source": "m1", "target_model_id": self.model_id,
            "request_type": "chat_completions"
        })
        self.db.add_agent_route({
            "source": "m2", "target_model_id": self.model_id,
            "request_type": "responses"
        })
        self.assertEqual(len(self.db.list_agent_routes(request_type="chat_completions")), 1)
        self.assertEqual(len(self.db.list_agent_routes(request_type="responses")), 1)

    def test_add_agent_route_source_star_rejected(self):
        with self.assertRaises(ValueError):
            self.db.add_agent_route({
                "source": "*", "target_model_id": self.model_id,
                "request_type": "chat_completions"
            })

    def test_add_agent_route_duplicate_raises(self):
        self.db.add_agent_route({
            "source": "m1", "target_model_id": self.model_id,
            "request_type": "chat_completions"
        })
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.add_agent_route({
                "source": "m1", "target_model_id": self.model_id,
                "request_type": "chat_completions"
            })

    def test_add_agent_route_inactive_upstream_rejected(self):
        self.db.update_upstream(self.upstream_id, {"is_active": 0})
        with self.assertRaises(ValueError):
            self.db.add_agent_route({
                "source": "m1", "target_model_id": self.model_id,
                "request_type": "chat_completions"
            })

    def test_get_agent_route(self):
        rid = self.db.add_agent_route({
            "source": "m1", "target_model_id": self.model_id,
            "request_type": "chat_completions"
        })
        route = self.db.get_agent_route(rid)
        self.assertIsNotNone(route)
        self.assertEqual(route["source"], "m1")

    def test_get_agent_route_not_found(self):
        self.assertIsNone(self.db.get_agent_route(999))

    def test_update_agent_route(self):
        rid = self.db.add_agent_route({
            "source": "m1", "target_model_id": self.model_id,
            "request_type": "chat_completions"
        })
        self.db.update_agent_route(rid, {"source": "m1-updated"})
        route = self.db.get_agent_route(rid)
        self.assertEqual(route["source"], "m1-updated")

    def test_update_agent_route_star_rejected(self):
        rid = self.db.add_agent_route({
            "source": "m1", "target_model_id": self.model_id,
            "request_type": "chat_completions"
        })
        with self.assertRaises(ValueError):
            self.db.update_agent_route(rid, {"source": "*"})

    def test_delete_agent_route(self):
        rid = self.db.add_agent_route({
            "source": "m1", "target_model_id": self.model_id,
            "request_type": "chat_completions"
        })
        self.db.delete_agent_route(rid)
        self.assertIsNone(self.db.get_agent_route(rid))

    def test_resolve_agent_found(self):
        self.db.add_agent_route({
            "source": "claude-sonnet-4-6", "target_model_id": self.model_id,
            "request_type": "chat_completions"
        })
        result = self.db.resolve_agent("claude-sonnet-4-6", "chat_completions")
        self.assertIsNotNone(result)
        self.assertEqual(result["target_name"], "test-model")

    def test_resolve_agent_not_found(self):
        result = self.db.resolve_agent("nonexistent", "chat_completions")
        self.assertIsNone(result)

    def test_resolve_agent_inactive_upstream_returns_none(self):
        self.db.add_agent_route({
            "source": "m1", "target_model_id": self.model_id,
            "request_type": "chat_completions"
        })
        self.db.update_upstream(self.upstream_id, {"is_active": 0})
        result = self.db.resolve_agent("m1", "chat_completions")
        self.assertIsNone(result)

    def test_get_all_agent_routes(self):
        self.db.add_agent_route({
            "source": "m1", "target_model_id": self.model_id,
            "request_type": "chat_completions"
        })
        self.db.add_agent_route({
            "source": "m2", "target_model_id": self.model_id,
            "request_type": "responses"
        })
        result = self.db.get_all_agent_routes()
        self.assertIn("m1", result)
        self.assertIn("m2", result)

    def test_get_all_agent_routes_filtered(self):
        self.db.add_agent_route({
            "source": "m1", "target_model_id": self.model_id,
            "request_type": "chat_completions"
        })
        self.db.add_agent_route({
            "source": "m2", "target_model_id": self.model_id,
            "request_type": "responses"
        })
        result = self.db.get_all_agent_routes(request_type="chat_completions")
        self.assertIn("m1", result)
        self.assertNotIn("m2", result)
