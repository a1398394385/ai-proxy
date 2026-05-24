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

    def test_upstream_has_key_cooldown_secs_column(self):
        from proxy.config_manager import ConfigDB
        db = ConfigDB(self.db_path)
        conn = db._connect()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(upstreams)").fetchall()}
        conn.close()
        db.close()
        self.assertIn("key_cooldown_secs", cols)

    def test_upstream_api_keys_table_exists(self):
        from proxy.config_manager import ConfigDB
        db = ConfigDB(self.db_path)
        conn = db._connect()
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        cols = {r[1] for r in conn.execute("PRAGMA table_info(upstream_api_keys)").fetchall()}
        conn.close()
        db.close()
        self.assertIn("upstream_api_keys", tables)
        for col in ("id", "upstream_id", "api_key", "label", "is_active", "created_at"):
            self.assertIn(col, cols)

    def test_upstream_api_keys_foreign_key(self):
        from proxy.config_manager import ConfigDB
        db = ConfigDB(self.db_path)
        conn = db._connect()
        fks = conn.execute(
            "PRAGMA foreign_key_list('upstream_api_keys')"
        ).fetchall()
        conn.close()
        db.close()
        self.assertEqual(len(fks), 1)
        # pragma columns: id, seq, table, from, to, on_update, on_delete, match
        self.assertEqual(fks[0][2], "upstreams")    # table name
        self.assertEqual(fks[0][3], "upstream_id")   # local column
        self.assertEqual(fks[0][6], "CASCADE")       # on_delete


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

    def test_delete_model_sets_route_null(self):
        mid = self.db.add_model({"name": "qwen", "upstream_id": self.id_a})
        self.db.add_route({"source": "gpt-4", "target_model_id": mid})
        # FK SET NULL: 删除模型后路由的 target_model_id 应变为 NULL
        result = self.db.delete_model(mid, check_refs=False)
        self.assertIn("affected_routes", result)
        self.assertEqual(result["affected_routes"], 1)
        # 验证路由 target_model_id 为 NULL
        routes = self.db.list_routes(request_type="responses")
        route = next((r for r in routes if r["source"] == "gpt-4"), None)
        self.assertIsNotNone(route)
        self.assertIsNone(route["target_model_id"])
        self.assertIsNone(route["target_name"])
        self.assertEqual(route["upstream_name"], "(已删除)")

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

    def test_update_route_rejects_default_source_change(self):
        """更新默认路由 (source=*) 时不允许修改 source。"""
        rid = self.db.add_route({"source": "*", "target_model_id": self.mid})
        with self.assertRaises(ValueError):
            self.db.update_route(rid, {"source": "gpt-4o"})
        # 确认 source 保持不变
        r = self.db.get_route(rid)
        self.assertEqual(r["source"], "*")

    def test_update_route_allows_default_update_without_source(self):
        """编辑默认路由时不传 source 字段 → 正常更新（如只改目标模型）。"""
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

    def test_resolve_default_to_star(self):
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

    def test_validate_star_default(self):
        self.db.add_route({"source": "*", "target_model_id": self.m1})
        self.assertTrue(self.db.validate_star_default())

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
        self.assertEqual(result["version"], 10)

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


class TestMigrationV8(unittest.TestCase):
    """v8 迁移测试：FK SET NULL + route_templates 表。"""

    def _create_v7_db(self, db_path):
        """创建模拟的 v7 数据库用于迁移测试。"""
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript("""
            CREATE TABLE schema_version (version INTEGER NOT NULL);
            INSERT INTO schema_version (version) VALUES (7);
            CREATE TABLE upstreams (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL,
                base_url TEXT NOT NULL, api_key TEXT NOT NULL DEFAULT '',
                timeout INTEGER NOT NULL DEFAULT 600, connect_timeout INTEGER NOT NULL DEFAULT 10,
                ssl_verify INTEGER NOT NULL DEFAULT 1, retry INTEGER NOT NULL DEFAULT 1,
                is_active INTEGER NOT NULL DEFAULT 1,
                format TEXT NOT NULL DEFAULT 'chat_completions',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')));
            CREATE TABLE target_models (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                upstream_id INTEGER NOT NULL REFERENCES upstreams(id) ON DELETE RESTRICT,
                multimodal INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(name, upstream_id));
            CREATE TABLE model_routes (
                id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL,
                target_model_id INTEGER NOT NULL REFERENCES target_models(id) ON DELETE RESTRICT,
                request_type TEXT NOT NULL DEFAULT 'responses',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')));
            CREATE TABLE agent_routes (
                id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL,
                target_model_id INTEGER NOT NULL REFERENCES target_models(id) ON DELETE RESTRICT,
                request_type TEXT NOT NULL DEFAULT 'chat_completions',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')));
            INSERT INTO upstreams (name, base_url) VALUES ('test-up', 'http://test');
            INSERT INTO target_models (name, upstream_id) VALUES ('test-model', 1);
            INSERT INTO model_routes (source, target_model_id) VALUES ('gpt-4', 1);
            INSERT INTO agent_routes (source, target_model_id) VALUES ('claude', 1);
        """)
        conn.commit()
        conn.close()

    def _verify_v9_schema(self, db_path):
        """验证 v9 迁移后的 schema 正确。"""
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
        self.assertEqual(cur.fetchone()[0], 10)
        # 验证 model_routes FK 为 SET NULL
        cur = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='model_routes'")
        sql = cur.fetchone()[0]
        self.assertIn("SET NULL", sql)
        self.assertNotIn("NOT NULL", sql.split("target_model_id")[1].split(",")[0])
        # 验证 agent_routes FK
        cur = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='agent_routes'")
        sql = cur.fetchone()[0]
        self.assertIn("SET NULL", sql)
        # 验证 route_templates 表存在
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='route_templates'")
        self.assertIsNotNone(cur.fetchone())
        # 验证 v9 新字段
        cur = conn.execute("PRAGMA table_info(target_models)")
        cols = {row[1] for row in cur.fetchall()}
        for col in ('max_context', 'max_input', 'max_output', 'rpm'):
            self.assertIn(col, cols, f'{col} 列应存在于 target_models')
        # 验证数据完整性
        cur = conn.execute("SELECT COUNT(*) FROM model_routes")
        self.assertEqual(cur.fetchone()[0], 1)
        conn.close()

    def test_migration_v7_to_v8(self):
        tmp = tempfile.TemporaryDirectory()
        db_path = Path(tmp.name) / "access_log.db"
        self._create_v7_db(db_path)
        from proxy.config_manager import Migrations
        mg = Migrations(db_path)
        s = mg.status()
        self.assertFalse(s["migrated"])
        result = mg.migrate()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["version"], 10)
        self._verify_v9_schema(db_path)
        # 幂等
        result2 = mg.migrate()
        self.assertEqual(result2["status"], "already_migrated")
        tmp.cleanup()

    def test_migration_v7_to_v8_is_idempotent(self):
        tmp = tempfile.TemporaryDirectory()
        db_path = Path(tmp.name) / "access_log.db"
        self._create_v7_db(db_path)
        from proxy.config_manager import Migrations
        mg = Migrations(db_path)
        result = mg.migrate()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["version"], 10)
        # 第二次调用应为幂等
        result2 = mg.migrate()
        self.assertEqual(result2["status"], "already_migrated")
        tmp.cleanup()

    def test_migration_v6_to_v8(self):
        """跨版本迁移 v6→v8。"""
        tmp = tempfile.TemporaryDirectory()
        db_path = Path(tmp.name) / "access_log.db"
        # 创建 v6 数据库（仅有 model_routes，无 agent_routes）
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript("""
            CREATE TABLE schema_version (version INTEGER NOT NULL);
            INSERT INTO schema_version (version) VALUES (6);
            CREATE TABLE upstreams (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL,
                base_url TEXT NOT NULL, api_key TEXT NOT NULL DEFAULT '',
                timeout INTEGER NOT NULL DEFAULT 600, connect_timeout INTEGER NOT NULL DEFAULT 10,
                ssl_verify INTEGER NOT NULL DEFAULT 1, retry INTEGER NOT NULL DEFAULT 1,
                is_active INTEGER NOT NULL DEFAULT 1,
                format TEXT NOT NULL DEFAULT 'chat_completions',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')));
            CREATE TABLE target_models (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                upstream_id INTEGER NOT NULL REFERENCES upstreams(id) ON DELETE RESTRICT,
                multimodal INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(name, upstream_id));
            CREATE TABLE model_routes (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL,
                target_model_id INTEGER NOT NULL REFERENCES target_models(id) ON DELETE RESTRICT,
                request_type TEXT NOT NULL DEFAULT 'responses',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')));
            INSERT INTO upstreams (name, base_url) VALUES ('test-up', 'http://test');
            INSERT INTO target_models (name, upstream_id) VALUES ('test-model', 1);
            INSERT INTO model_routes (source, target_model_id) VALUES ('gpt-4', 1);
        """)
        conn.commit()
        conn.close()
        from proxy.config_manager import Migrations
        mg = Migrations(db_path)
        result = mg.migrate()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["version"], 10)
        conn = sqlite3.connect(str(db_path))
        cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
        self.assertEqual(cur.fetchone()[0], 10)
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='route_templates'")
        self.assertIsNotNone(cur.fetchone())
        conn.close()
        tmp.cleanup()


class TestTemplateCRUD(unittest.TestCase):
    """路由模板 CRUD 测试。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "config.db"
        from proxy.config_manager import ConfigDB
        self.db = ConfigDB(self.db_path)
        self.up_id = self.db.add_upstream({"name": "up-a", "base_url": "http://a"})
        self.model_id = self.db.add_model({"name": "gpt-4", "upstream_id": self.up_id})
        self.model_id2 = self.db.add_model({"name": "claude", "upstream_id": self.up_id})
        self.db.add_route({"source": "gpt-4o", "target_model_id": self.model_id, "request_type": "chat_completions"})
        self.db.add_route({"source": "claude-sonnet", "target_model_id": self.model_id2, "request_type": "chat_completions"})

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_save_and_list_templates(self):
        tid = self.db.save_template({"name": "default", "request_type": "chat_completions"})
        self.assertIsInstance(tid, int)
        templates = self.db.list_templates(request_type="chat_completions")
        self.assertEqual(len(templates), 1)
        self.assertEqual(templates[0]["name"], "default")
        # 不含 items
        self.assertNotIn("items", templates[0])

    def test_list_templates_filter_by_request_type(self):
        self.db.save_template({"name": "t1", "request_type": "chat_completions"})
        self.db.save_template({"name": "t2", "request_type": "responses"})
        all_t = self.db.list_templates()
        self.assertEqual(len(all_t), 2)
        chat_t = self.db.list_templates(request_type="chat_completions")
        self.assertEqual(len(chat_t), 1)
        self.assertEqual(chat_t[0]["name"], "t1")

    def test_get_template_preview(self):
        tid = self.db.save_template({"name": "preview-test", "request_type": "chat_completions"})
        preview = self.db.get_template_preview(tid)
        self.assertIsNotNone(preview)
        self.assertEqual(preview["name"], "preview-test")
        # 预览应展开 model_routes
        self.assertEqual(len(preview["model_routes"]), 2)
        self.assertTrue(all(r["valid"] for r in preview["model_routes"]))

    def test_get_template_preview_with_deleted_model(self):
        tid = self.db.save_template({"name": "del-test", "request_type": "chat_completions"})
        # 删除模型后模板预览中应显示失效
        self.db.delete_model(self.model_id)
        preview = self.db.get_template_preview(tid)
        self.assertIsNotNone(preview)
        invalid = [r for r in preview["model_routes"] if not r["valid"]]
        self.assertGreaterEqual(len(invalid), 1)

    def test_apply_template(self):
        tid = self.db.save_template({"name": "apply-test", "request_type": "chat_completions"})
        # 应用前清空路由
        for r in self.db.list_routes(request_type="chat_completions"):
            self.db.delete_route(r["id"])
        result = self.db.apply_template(tid)
        self.assertEqual(result["applied"], 2)
        routes = self.db.list_routes(request_type="chat_completions")
        self.assertEqual(len(routes), 2)

    def test_apply_template_atomic_replace(self):
        tid = self.db.save_template({"name": "atomic-test", "request_type": "chat_completions"})
        result = self.db.apply_template(tid)
        self.assertEqual(result["applied"], 2)
        self.assertEqual(result["invalid_count"], 0)
        routes = self.db.list_routes(request_type="chat_completions")
        self.assertEqual(len(routes), 2)

    def test_delete_template_no_effect_on_routes(self):
        tid = self.db.save_template({"name": "del-tmpl", "request_type": "chat_completions"})
        route_count = len(self.db.list_routes(request_type="chat_completions"))
        self.db.delete_template(tid)
        # 路由不受影响
        self.assertEqual(len(self.db.list_routes(request_type="chat_completions")), route_count)
        # 模板已被删除
        self.assertIsNone(self.db.get_template(tid))

    def test_template_unique_name_per_request_type(self):
        self.db.save_template({"name": "unique-test", "request_type": "chat_completions"})
        with self.assertRaises(ValueError):
            self.db.save_template({"name": "unique-test", "request_type": "chat_completions"})
        # 不同 request_type 可同名
        tid = self.db.save_template({"name": "unique-test", "request_type": "responses"})
        self.assertIsInstance(tid, int)

    def test_template_name_validation(self):
        with self.assertRaises(ValueError):
            self.db.save_template({"name": "", "request_type": "chat_completions"})
        with self.assertRaises(ValueError):
            self.db.save_template({"name": "a/b", "request_type": "chat_completions"})
        # 超长名
        long_name = "x" * 101
        with self.assertRaises(ValueError):
            self.db.save_template({"name": long_name, "request_type": "chat_completions"})

    def test_apply_template_with_deleted_model(self):
        tid = self.db.save_template({"name": "deleted-ref", "request_type": "chat_completions"})
        # 删除模板引用的模型
        self.db.delete_model(self.model_id)
        # 应用模板时失效路由的 target_model_id 应设为 NULL
        result = self.db.apply_template(tid)
        self.assertGreaterEqual(result["invalid_count"], 1)
        routes = self.db.list_routes(request_type="chat_completions")
        invalid = [r for r in routes if r["target_model_id"] is None]
        self.assertGreaterEqual(len(invalid), 1)

    def test_list_routes_with_null_target(self):
        """LEFT JOIN 应返回 target_model_id=NULL 的路由。"""
        self.db.delete_model(self.model_id)
        routes = self.db.list_routes(request_type="chat_completions")
        null_target = [r for r in routes if r["target_model_id"] is None]
        self.assertGreaterEqual(len(null_target), 1)

    def test_apply_template_nonexistent(self):
        with self.assertRaises(ValueError):
            self.db.apply_template(9999)

    def test_apply_template_invalid_json_items(self):
        tid = self.db.save_template({"name": "bad-items", "request_type": "chat_completions"})
        # 直接修改 items 为损坏 JSON
        conn = self.db._connect()
        conn.execute("UPDATE route_templates SET items = 'not-json' WHERE id = ?", (tid,))
        conn.commit()
        conn.close()
        # 刷新 cached template
        with self.assertRaises(ValueError):
            self.db.apply_template(tid)


class TestUpstreamApiKeysCRUD(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / 'config.db'
        from proxy.config_manager import ConfigDB
        self.db = ConfigDB(self.db_path)
        self.up_id = self.db.add_upstream({'name': 'test-upstream', 'base_url': 'http://test'})

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_add_and_list_keys(self):
        kid = self.db.add_upstream_key(self.up_id, 'sk-test-key-1234abc', 'primary')
        self.assertIsInstance(kid, int)
        keys = self.db.list_upstream_keys(self.up_id)
        self.assertEqual(len(keys), 1)
        self.assertEqual(keys[0]['id'], kid)
        self.assertEqual(keys[0]['upstream_id'], self.up_id)
        self.assertEqual(keys[0]['label'], 'primary')
        self.assertEqual(keys[0]['is_active'], 1)
        self.assertEqual(keys[0]['masked_key'], '****4abc')
        self.assertNotIn('api_key', keys[0])

    def test_masked_key_short(self):
        self.db.add_upstream_key(self.up_id, 'abc', 'short')
        keys = self.db.list_upstream_keys(self.up_id)
        self.assertEqual(keys[0]['masked_key'], 'abc')

    def test_duplicate_key_raises(self):
        self.db.add_upstream_key(self.up_id, 'sk-dup', 'first')
        with self.assertRaises(ValueError) as ctx:
            self.db.add_upstream_key(self.up_id, 'sk-dup', 'second')
        self.assertIn('已存在', str(ctx.exception))

    def test_limit_20_keys(self):
        for i in range(20):
            self.db.add_upstream_key(self.up_id, f'sk-key-{i:02d}')
        keys = self.db.list_upstream_keys(self.up_id)
        self.assertEqual(len(keys), 20)
        with self.assertRaises(ValueError) as ctx:
            self.db.add_upstream_key(self.up_id, 'sk-key-overflow')
        self.assertIn('最多配置 20 个', str(ctx.exception))

    def test_update_key_label_and_active(self):
        kid = self.db.add_upstream_key(self.up_id, 'sk-update', 'old-label')
        self.db.update_upstream_key(kid, {'label': 'new-label', 'is_active': 0})
        keys = self.db.list_upstream_keys(self.up_id)
        self.assertEqual(keys[0]['label'], 'new-label')
        self.assertEqual(keys[0]['is_active'], 0)

    def test_delete_key(self):
        kid = self.db.add_upstream_key(self.up_id, 'sk-delete')
        self.db.delete_upstream_key(kid)
        keys = self.db.list_upstream_keys(self.up_id)
        self.assertEqual(len(keys), 0)

    def test_get_first_active_key(self):
        self.db.add_upstream_key(self.up_id, 'sk-active-first')
        self.db.add_upstream_key(self.up_id, 'sk-active-second')
        first = self.db.get_first_active_key(self.up_id)
        self.assertEqual(first, 'sk-active-first')

    def test_get_first_active_key_skips_disabled(self):
        kid1 = self.db.add_upstream_key(self.up_id, 'sk-disabled')
        self.db.update_upstream_key(kid1, {'is_active': 0})
        self.db.add_upstream_key(self.up_id, 'sk-active-next')
        first = self.db.get_first_active_key(self.up_id)
        self.assertEqual(first, 'sk-active-next')

    def test_get_first_active_key_no_keys(self):
        first = self.db.get_first_active_key(self.up_id)
        self.assertEqual(first, '')

    def test_cascade_delete_on_upstream_removal(self):
        self.db.add_upstream_key(self.up_id, 'sk-cascade')
        self.assertEqual(len(self.db.list_upstream_keys(self.up_id)), 1)
        self.db.delete_upstream_with_models(self.up_id)
        keys = self.db.list_upstream_keys(self.up_id)
        self.assertEqual(len(keys), 0)

class TestMigrationV9ToV10(unittest.TestCase):
    """v9 → v10 迁移测试：upstream_api_keys 表 + key_cooldown_secs 列 + api_key 迁移。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "config.db"

    def tearDown(self):
        self.tmp.cleanup()

    def _make_v9_db(self, api_keys=None):
        """创建模拟的 v9 数据库。

        api_keys: [(upstream_name, api_key), ...]
        """
        if api_keys is None:
            api_keys = []

        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript("""
            CREATE TABLE schema_version (version INTEGER NOT NULL);
            INSERT INTO schema_version (version) VALUES (9);
            CREATE TABLE upstreams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                base_url TEXT NOT NULL,
                api_key TEXT NOT NULL DEFAULT '',
                timeout INTEGER NOT NULL DEFAULT 600,
                connect_timeout INTEGER NOT NULL DEFAULT 10,
                ssl_verify INTEGER NOT NULL DEFAULT 1,
                retry INTEGER NOT NULL DEFAULT 1,
                is_active INTEGER NOT NULL DEFAULT 1,
                format TEXT NOT NULL DEFAULT 'chat_completions',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE target_models (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                upstream_id INTEGER NOT NULL REFERENCES upstreams(id) ON DELETE SET NULL,
                multimodal INTEGER NOT NULL DEFAULT 1,
                max_context INTEGER DEFAULT NULL,
                max_input INTEGER DEFAULT NULL,
                max_output INTEGER DEFAULT NULL,
                rpm INTEGER DEFAULT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(name, upstream_id)
            );
            CREATE TABLE model_routes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                target_model_id INTEGER REFERENCES target_models(id) ON DELETE SET NULL,
                request_type TEXT NOT NULL DEFAULT 'responses',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')));
            CREATE TABLE agent_routes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                target_model_id INTEGER REFERENCES target_models(id) ON DELETE SET NULL,
                request_type TEXT NOT NULL DEFAULT 'chat_completions',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')));
            CREATE TABLE route_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                request_type TEXT NOT NULL DEFAULT 'chat_completions',
                items TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                last_applied_at TEXT);
        """)

        for i, (up_name, api_key) in enumerate(api_keys, 1):
            conn.execute(
                "INSERT INTO upstreams (name, base_url, api_key) VALUES (?, ?, ?)",
                (up_name, f'http://{up_name}', api_key),
            )
            conn.execute(
                "INSERT INTO target_models (name, upstream_id) VALUES (?, ?)",
                (f'model-{i}', i),
            )

        if not api_keys:
            conn.execute(
                "INSERT INTO upstreams (name, base_url) VALUES (?, ?)",
                ('no-key-upstream', 'http://no-key'),
            )
            conn.execute(
                "INSERT INTO target_models (name, upstream_id) VALUES (?, ?)",
                ('no-key-model', 1),
            )

        conn.commit()
        conn.close()

    def _verify_v10_schema(self):
        """验证 v10 迁移后的 schema 正确。"""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
        self.assertEqual(cur.fetchone()[0], 10)
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='upstream_api_keys'"
        )
        self.assertIsNotNone(cur.fetchone())
        cur = conn.execute("PRAGMA table_info(upstreams)")
        cols = {row[1] for row in cur.fetchall()}
        self.assertIn('key_cooldown_secs', cols)
        cur = conn.execute("SELECT name, api_key FROM upstreams")
        for name, api_key in cur.fetchall():
            self.assertEqual(api_key, '', f'upstream "{name}" api_key 应为空')
        conn.close()

    def _verify_api_keys_migrated(self, expected_count):
        """验证 api_key 迁移结果。"""
        conn = sqlite3.connect(str(self.db_path))
        cur = conn.execute(
            "SELECT COUNT(*) FROM upstream_api_keys WHERE label = '迁移自旧字段'"
        )
        self.assertEqual(cur.fetchone()[0], expected_count)
        conn.close()

    def test_migration_v9_to_v10_basic(self):
        """基本迁移：有 api_key 的 v9 数据库 → v10。"""
        self._make_v9_db(api_keys=[
            ('up-a', 'sk-key-aaaa'),
            ('up-b', 'sk-key-bbbb'),
        ])
        from proxy.config_manager import Migrations
        mg = Migrations(self.db_path)
        s = mg.status()
        self.assertFalse(s["migrated"])
        self.assertEqual(s["version"], 9)
        result = mg.migrate()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["version"], 10)
        self._verify_v10_schema()
        self._verify_api_keys_migrated(expected_count=2)

    def test_migration_v9_to_v10_idempotent(self):
        """二次迁移应为幂等（already_migrated）。"""
        self._make_v9_db(api_keys=[('up-a', 'sk-key-aaaa')])
        from proxy.config_manager import Migrations
        mg = Migrations(self.db_path)
        result = mg.migrate()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["version"], 10)
        result2 = mg.migrate()
        self.assertEqual(result2["status"], "already_migrated")

    def test_migration_v9_to_v10_no_api_keys(self):
        """无 api_key 的 v9 数据库迁移。"""
        self._make_v9_db(api_keys=[])
        from proxy.config_manager import Migrations
        mg = Migrations(self.db_path)
        result = mg.migrate()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["version"], 10)
        conn = sqlite3.connect(str(self.db_path))
        count = conn.execute("SELECT COUNT(*) FROM upstream_api_keys").fetchone()[0]
        self.assertEqual(count, 0)
        conn.close()

    def test_migration_v9_to_v10_preserves_api_keys(self):
        """api_key 值正确迁移到 upstream_api_keys。"""
        self._make_v9_db(api_keys=[
            ('up1', 'sk-key-one'),
            ('up2', 'sk-key-two'),
            ('up3', 'sk-key-three'),
        ])
        from proxy.config_manager import Migrations
        mg = Migrations(self.db_path)
        mg.migrate()

        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT uk.api_key, u.name
                   FROM upstream_api_keys uk
                   JOIN upstreams u ON uk.upstream_id = u.id
                  ORDER BY u.name"""
        ).fetchall()
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["api_key"], "sk-key-one")
        self.assertEqual(rows[0]["name"], "up1")
        self.assertEqual(rows[1]["api_key"], "sk-key-two")
        self.assertEqual(rows[1]["name"], "up2")
        self.assertEqual(rows[2]["api_key"], "sk-key-three")
        self.assertEqual(rows[2]["name"], "up3")
        conn.close()

    def test_migration_v9_to_v10_clears_upstreams_api_key(self):
        """迁移后 upstreams.api_key 应为空。"""
        self._make_v9_db(api_keys=[('up-a', 'sk-key-aaaa')])
        from proxy.config_manager import Migrations
        mg = Migrations(self.db_path)
        mg.migrate()

        conn = sqlite3.connect(str(self.db_path))
        rows = conn.execute("SELECT name, api_key FROM upstreams").fetchall()
        for name, api_key in rows:
            self.assertEqual(api_key, '', f'upstream "{name}" api_key 应为空')
        conn.close()

    def test_migration_v9_to_v10_key_cooldown_default(self):
        """key_cooldown_secs 默认值为 60。"""
        self._make_v9_db(api_keys=[('up-a', 'sk-key-aaaa')])
        from proxy.config_manager import Migrations
        mg = Migrations(self.db_path)
        mg.migrate()

        conn = sqlite3.connect(str(self.db_path))
        rows = conn.execute(
            "SELECT name, key_cooldown_secs FROM upstreams"
        ).fetchall()
        for name, cooldown in rows:
            self.assertEqual(cooldown, 60, f'upstream "{name}" key_cooldown_secs 应为 60')
        conn.close()

    def test_migration_v8_to_v10(self):
        """跨版本迁移 v8 → v10。"""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript("""
            CREATE TABLE schema_version (version INTEGER NOT NULL);
            INSERT INTO schema_version (version) VALUES (8);
            CREATE TABLE upstreams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                base_url TEXT NOT NULL,
                api_key TEXT NOT NULL DEFAULT '',
                timeout INTEGER NOT NULL DEFAULT 600,
                connect_timeout INTEGER NOT NULL DEFAULT 10,
                ssl_verify INTEGER NOT NULL DEFAULT 1,
                retry INTEGER NOT NULL DEFAULT 1,
                is_active INTEGER NOT NULL DEFAULT 1,
                format TEXT NOT NULL DEFAULT 'chat_completions',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE target_models (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                upstream_id INTEGER NOT NULL REFERENCES upstreams(id) ON DELETE RESTRICT,
                multimodal INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(name, upstream_id)
            );
            CREATE TABLE model_routes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                target_model_id INTEGER NOT NULL REFERENCES target_models(id) ON DELETE RESTRICT,
                request_type TEXT NOT NULL DEFAULT 'responses',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')));
            CREATE TABLE agent_routes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                target_model_id INTEGER NOT NULL REFERENCES target_models(id) ON DELETE RESTRICT,
                request_type TEXT NOT NULL DEFAULT 'chat_completions',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')));
            INSERT INTO upstreams (name, base_url, api_key) VALUES ('old-up', 'http://old', 'sk-old-key');
            INSERT INTO target_models (name, upstream_id) VALUES ('old-model', 1);
            INSERT INTO model_routes (source, target_model_id) VALUES ('gpt-4', 1);
            INSERT INTO agent_routes (source, target_model_id) VALUES ('claude', 1);
        """)
        conn.commit()
        conn.close()

        from proxy.config_manager import Migrations
        mg = Migrations(self.db_path)
        result = mg.migrate()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["version"], 10)

        conn = sqlite3.connect(str(self.db_path))
        cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
        self.assertEqual(cur.fetchone()[0], 10)
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='upstream_api_keys'"
        )
        self.assertIsNotNone(cur.fetchone())
        cur = conn.execute("SELECT key_cooldown_secs FROM upstreams WHERE name='old-up'")
        self.assertEqual(cur.fetchone()[0], 60)
        cur = conn.execute("SELECT api_key FROM upstream_api_keys WHERE upstream_id=1")
        self.assertEqual(cur.fetchone()[0], 'sk-old-key')
        cur = conn.execute("SELECT api_key FROM upstreams WHERE id=1")
        self.assertEqual(cur.fetchone()[0], '')
        conn.close()

    def test_migration_v9_to_v10_duplicate_keys_ignored(self):
        """INSERT OR IGNORE 防止重复插入。"""
        self._make_v9_db(api_keys=[('up-a', 'sk-key-aaaa')])
        from proxy.config_manager import Migrations
        mg = Migrations(self.db_path)
        mg.migrate()

        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            """INSERT OR IGNORE INTO upstream_api_keys
                    (upstream_id, api_key, label)
                    SELECT id, api_key, '迁移自旧字段' FROM upstreams WHERE api_key != ''"""
        )
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) FROM upstream_api_keys WHERE label = '迁移自旧字段'"
        ).fetchone()[0]
        self.assertEqual(count, 1, "重试不应产生重复记录")
        conn.close()
