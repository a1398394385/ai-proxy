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


class TestPricingDBCRUD(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "config.db"
        from proxy.pricing_manager import PricingDB
        self.db = PricingDB(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_add_pricing(self):
        model_id = self.db.add_pricing({
            "model_id": "test-model-v1",
            "display_name": "Test Model V1",
            "input_cost_per_million": "1.5",
            "output_cost_per_million": "6",
            "cache_read_cost_per_million": "0.15",
            "cache_creation_cost_per_million": "1.5",
            "currency": "RMB",
        })
        self.assertEqual(model_id, "test-model-v1")
        result = self.db.get_pricing("test-model-v1")
        self.assertEqual(result["currency"], "RMB")
        self.assertEqual(result["input_cost_per_million"], "1.5")

    def test_add_pricing_default_currency(self):
        model_id = self.db.add_pricing({
            "model_id": "test-usd-model",
            "display_name": "Test USD",
            "input_cost_per_million": "2",
            "output_cost_per_million": "8",
        })
        result = self.db.get_pricing(model_id)
        self.assertEqual(result["currency"], "USD")

    def test_add_pricing_duplicate_fails(self):
        self.db.add_pricing({
            "model_id": "dup-model",
            "display_name": "Dup",
            "input_cost_per_million": "1",
            "output_cost_per_million": "2",
        })
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.add_pricing({
                "model_id": "dup-model",
                "display_name": "Dup Again",
                "input_cost_per_million": "3",
                "output_cost_per_million": "4",
            })

    def test_add_pricing_invalid_currency(self):
        with self.assertRaises(ValueError):
            self.db.add_pricing({
                "model_id": "bad-currency",
                "display_name": "Bad",
                "input_cost_per_million": "1",
                "output_cost_per_million": "2",
                "currency": "EUR",
            })

    def test_add_pricing_invalid_price(self):
        with self.assertRaises(ValueError):
            self.db.add_pricing({
                "model_id": "bad-price",
                "display_name": "Bad",
                "input_cost_per_million": "abc",
                "output_cost_per_million": "2",
            })

    def test_update_pricing(self):
        self.db.add_pricing({
            "model_id": "update-test",
            "display_name": "Before",
            "input_cost_per_million": "1",
            "output_cost_per_million": "2",
        })
        ok = self.db.update_pricing("update-test", {
            "display_name": "After",
            "input_cost_per_million": "5",
            "currency": "RMB",
        })
        self.assertTrue(ok)
        result = self.db.get_pricing("update-test")
        self.assertEqual(result["display_name"], "After")
        self.assertEqual(result["input_cost_per_million"], "5")
        self.assertEqual(result["currency"], "RMB")
        self.assertEqual(result["output_cost_per_million"], "2")  # 未修改的保留

    def test_update_pricing_nonexistent(self):
        ok = self.db.update_pricing("nonexistent", {"display_name": "X"})
        self.assertFalse(ok)

    def test_delete_pricing(self):
        self.db.add_pricing({
            "model_id": "delete-test",
            "display_name": "To Delete",
            "input_cost_per_million": "1",
            "output_cost_per_million": "2",
        })
        ok = self.db.delete_pricing("delete-test")
        self.assertTrue(ok)
        self.assertIsNone(self.db.get_pricing("delete-test"))

    def test_delete_pricing_nonexistent(self):
        ok = self.db.delete_pricing("nonexistent")
        self.assertFalse(ok)
