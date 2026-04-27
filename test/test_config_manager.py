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
        from config_manager import ConfigDB
        db = ConfigDB(self.db_path)
        self.assertTrue(self.db_path.exists())
        db.close()

    def test_init_creates_all_tables(self):
        from config_manager import ConfigDB
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
        from config_manager import ConfigDB
        db = ConfigDB(self.db_path)
        conn = db._connect()
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        conn.close()
        db.close()
        self.assertEqual(fk, 1)

    def test_pragma_wal_mode(self):
        from config_manager import ConfigDB
        db = ConfigDB(self.db_path)
        conn = db._connect()
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        db.close()
        self.assertEqual(journal, "wal")

    def test_pragma_busy_timeout(self):
        from config_manager import ConfigDB
        db = ConfigDB(self.db_path)
        conn = db._connect()
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        conn.close()
        db.close()
        self.assertEqual(timeout, 3000)

    def test_init_idempotent(self):
        from config_manager import ConfigDB
        db1 = ConfigDB(self.db_path)
        db1.close()
        db2 = ConfigDB(self.db_path)
        db2.close()
        self.assertTrue(self.db_path.exists())


class TestUpstreamCRUD(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "config.db"
        from config_manager import ConfigDB
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

    def test_is_default_clears_others_on_add(self):
        self.db.add_upstream({"id": "up-a", "base_url": "http://a", "is_default": 1})
        self.db.add_upstream({"id": "up-b", "base_url": "http://b", "is_default": 1})
        a = self.db.get_upstream("up-a")
        b = self.db.get_upstream("up-b")
        self.assertEqual(a["is_default"], 0)
        self.assertEqual(b["is_default"], 1)

    def test_is_default_clears_others_on_update(self):
        self.db.add_upstream({"id": "up-a", "base_url": "http://a", "is_default": 1})
        self.db.add_upstream({"id": "up-b", "base_url": "http://b"})
        self.db.update_upstream("up-b", {"is_default": 1})
        a = self.db.get_upstream("up-a")
        b = self.db.get_upstream("up-b")
        self.assertEqual(a["is_default"], 0)
        self.assertEqual(b["is_default"], 1)


class TestModelCRUD(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "config.db"
        from config_manager import ConfigDB
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


class TestRouteCRUD(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "config.db"
        from config_manager import ConfigDB
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

    def test_delete_route(self):
        rid = self.db.add_route({"source": "z-model", "target_model_id": self.mid})
        self.db.delete_route(rid)
        self.assertIsNone(self.db.get_route(rid))

    def test_fk_restrict_upstream_delete(self):
        with self.assertRaises(sqlite3.IntegrityError):
            conn = self.db._connect()
            conn.execute("DELETE FROM upstreams WHERE id = ?", ("up-a",))
            conn.close()
