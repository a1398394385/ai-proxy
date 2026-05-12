import unittest
import sqlite3
from tempfile import TemporaryDirectory
from pathlib import Path


class TestPricingDBEnsureTable(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "config.db"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_ensure_table_creates_model_pricing(self):
        from proxy.pricing_manager import PricingDB
        db = PricingDB(self.db_path)
        db._ensure_table()
        conn = sqlite3.connect(str(self.db_path))
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='model_pricing'"
        ).fetchall()]
        conn.close()
        self.assertIn("model_pricing", tables)

    def test_ensure_table_idempotent(self):
        from proxy.pricing_manager import PricingDB
        db = PricingDB(self.db_path)
        db._ensure_table()
        db._ensure_table()  # 第二次不应报错
        conn = sqlite3.connect(str(self.db_path))
        count = conn.execute("SELECT COUNT(*) FROM model_pricing").fetchone()[0]
        conn.close()
        self.assertGreater(count, 0)

    def test_seed_data_imported_on_empty_table(self):
        from proxy.pricing_manager import PricingDB
        db = PricingDB(self.db_path)
        db._ensure_table()
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM model_pricing WHERE model_id = ?", ("claude-sonnet-4-6-20260217",)
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["display_name"], "Claude Sonnet 4.6")
        self.assertEqual(row["input_cost_per_million"], "3")
        self.assertEqual(row["currency"], "USD")

    def test_seed_data_not_reimported(self):
        from proxy.pricing_manager import PricingDB
        db = PricingDB(self.db_path)
        db._ensure_table()
        # 删一条，再调用 _ensure_table，不应重新导入
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("DELETE FROM model_pricing WHERE model_id = 'claude-sonnet-4-6-20260217'")
        conn.commit()
        count_before = conn.execute("SELECT COUNT(*) FROM model_pricing").fetchone()[0]
        conn.close()
        db._ensure_table()
        conn = sqlite3.connect(str(self.db_path))
        count_after = conn.execute("SELECT COUNT(*) FROM model_pricing").fetchone()[0]
        conn.close()
        self.assertEqual(count_before, count_after)


class TestPricingDBListAndGet(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "config.db"
        from proxy.pricing_manager import PricingDB
        self.db = PricingDB(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_list_pricings_returns_all(self):
        result = self.db.list_pricings()
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)
        self.assertIn("model_id", result[0])
        self.assertIn("currency", result[0])

    def test_list_pricings_with_search(self):
        result = self.db.list_pricings(search="claude")
        self.assertGreater(len(result), 0)
        for r in result:
            self.assertIn("claude", r["model_id"].lower() + r["display_name"].lower())

    def test_list_pricings_search_no_match(self):
        result = self.db.list_pricings(search="nonexistent_model_xyz")
        self.assertEqual(len(result), 0)

    def test_get_pricing_existing(self):
        result = self.db.get_pricing("claude-sonnet-4-6-20260217")
        self.assertIsNotNone(result)
        self.assertEqual(result["model_id"], "claude-sonnet-4-6-20260217")
        self.assertEqual(result["input_cost_per_million"], "3")

    def test_get_pricing_nonexistent(self):
        result = self.db.get_pricing("nonexistent_model")
        self.assertIsNone(result)
