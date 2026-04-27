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
