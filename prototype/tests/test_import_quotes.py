from contextlib import closing
"""Regression checks use temporary databases; original Excel/JSON are read only."""

import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from import_quotes import ROOT, import_pending


class PendingImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.db = self.base / "pending.db"
        self.input = self.base / "input.json"
        self.data = json.loads((ROOT / "output/brazil_ocean_raw.json").read_text(encoding="utf-8"))

    def run_import(self, data=None):
        self.input.write_text(json.dumps(self.data if data is None else data, ensure_ascii=False), encoding="utf-8")
        return import_pending(self.input, self.db, ROOT / "source")

    def query(self, sql):
        with closing(sqlite3.connect(str(self.db))) as conn, conn:
            return conn.execute(sql).fetchall()

    def test_repeat_and_reordered_input_do_not_duplicate(self):
        first = self.run_import()
        before = self.query("SELECT * FROM pending_quote_details ORDER BY source_row, price_cell")
        reordered = copy.deepcopy(self.data)
        reordered["records"].reverse()
        second = self.run_import(reordered)
        self.assertEqual((first["inserted_categories"], first["inserted_prices"]), (8, 24))
        self.assertEqual((second["inserted_categories"], second["inserted_prices"]), (0, 0))
        self.assertEqual(second["database_counts"], {"quote_sources": 1, "pending_categories": 8, "pending_prices": 24})
        self.assertEqual(before, self.query("SELECT * FROM pending_quote_details ORDER BY source_row, price_cell"))

    def test_only_explicit_pending_records_are_imported(self):
        self.data["records"][0]["reviewed"] = True
        result = self.run_import()
        self.assertEqual(result["skipped_reviewed_categories"], 1)
        self.assertEqual(result["database_counts"]["pending_categories"], 7)
        self.assertEqual(result["database_counts"]["pending_prices"], 21)
        self.assertEqual(self.query("SELECT COUNT(*) FROM pending_categories WHERE source_row = 5"), [(0,)])
        self.data["records"][0]["reviewed"] = False
        result = self.run_import()
        self.assertEqual((result["inserted_categories"], result["inserted_prices"]), (1, 3))

    def test_no_pending_records_creates_no_quote_rows(self):
        for record in self.data["records"]:
            record["reviewed"] = True
        result = self.run_import()
        self.assertEqual(set(result["database_counts"].values()), {0})

    def test_missing_or_ambiguous_review_status_fails_before_database_creation(self):
        for value in (None, 0, "false"):
            with self.subTest(value=value):
                self.data["records"][0]["reviewed"] = value
                with self.assertRaises(ValueError):
                    self.run_import()
                self.assertFalse(self.db.exists())
        del self.data["records"][0]["reviewed"]
        with self.assertRaises(ValueError):
            self.run_import()

    def test_invalid_prices_and_excel_mismatch_leave_database_unchanged(self):
        self.run_import()
        before = self.db.read_bytes()
        for value in (None, True, "820", -1, 0, float("nan"), float("inf"), 999999):
            with self.subTest(value=value):
                modified = copy.deepcopy(self.data)
                modified["records"][-1]["prices_usd_per_cbm"]["10+CBM"] = value
                with self.assertRaises(ValueError):
                    self.run_import(modified)
                self.assertEqual(before, self.db.read_bytes())

    def test_late_database_conflict_rolls_back_new_rows(self):
        subset = copy.deepcopy(self.data)
        subset["records"][0]["reviewed"] = True
        self.run_import(subset)
        with closing(sqlite3.connect(str(self.db))) as conn, conn:
            conn.execute("UPDATE pending_prices SET price_amount = '999999' WHERE price_cell = 'G12'")
        before = self.db.read_bytes()
        with self.assertRaises(ValueError):
            self.run_import()
        self.assertEqual(before, self.db.read_bytes())
        self.assertEqual(self.query("SELECT COUNT(*) FROM pending_categories"), [(7,)])

    def test_database_constraints_reject_approval_and_validity(self):
        self.run_import()
        statements = [
            "UPDATE pending_prices SET review_status = 'approved'",
            "UPDATE pending_categories SET review_status = 'approved'",
            "UPDATE quote_sources SET review_status = 'approved'",
            "UPDATE pending_prices SET usable_for_quote = 1",
            "UPDATE pending_categories SET usable_for_quote = 1",
            "UPDATE quote_sources SET usable_for_quote = 1",
            "UPDATE quote_sources SET valid_until = '2099-01-01'",
            "UPDATE pending_prices SET boundary_status = 'confirmed'",
        ]
        for sql in statements:
            with self.subTest(sql=sql), closing(sqlite3.connect(str(self.db))) as conn, conn:
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(sql)

    def test_unrelated_database_is_not_modified(self):
        with closing(sqlite3.connect(str(self.db))) as conn, conn:
            conn.execute("CREATE TABLE other_data (note TEXT)")
            conn.execute("INSERT INTO other_data VALUES ('keep')")
        before = self.db.read_bytes()
        with self.assertRaises(ValueError):
            self.run_import()
        self.assertEqual(before, self.db.read_bytes())

    def test_precise_provenance_is_preserved(self):
        self.run_import()
        self.assertEqual(self.query("SELECT DISTINCT effective_date_raw, effective_date_cell FROM pending_quote_details"),
                         [(self.data["effective_date_raw"], "A3")])
        records = self.query("""SELECT source_row, category_cell, products_cell, volume_weight_rule_cell, price_cell,
            review_status, usable_for_quote FROM pending_quote_details ORDER BY source_row, price_cell""")
        expected = [(row, f"A{row}", f"B{row}", f"H{row}", f"{col}{row}", "pending_review", 0)
                    for row in range(5, 13) for col in "EFG"]
        self.assertEqual(records, expected)


if __name__ == "__main__":
    unittest.main()
