import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.workflow import MAX_RESULT_ROWS, validate_sql


class ValidateSqlTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database_root = Path(directory.name)
        with sqlite3.connect(self.database_root / "ecommerce_test.db") as connection:
            connection.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, total_amount REAL)")
            connection.execute("INSERT INTO orders (total_amount) VALUES (10.0)")
        patcher = patch("agents.workflow.BASE_DIR", self.database_root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_select_is_approved_and_bounded(self):
        valid, sql, error = validate_sql("SELECT * FROM orders")

        self.assertTrue(valid)
        self.assertEqual(error, "")
        self.assertTrue(sql.endswith(f"LIMIT {MAX_RESULT_ROWS}"))

    def test_write_statement_is_rejected(self):
        valid, _, error = validate_sql("DELETE FROM orders")

        self.assertFalse(valid)
        self.assertIn("SELECT", error)

    def test_multiple_statements_are_rejected(self):
        valid, _, error = validate_sql("SELECT * FROM orders; DROP TABLE orders")

        self.assertFalse(valid)
        self.assertIn("一条", error)

    def test_large_limit_is_clamped(self):
        valid, sql, _ = validate_sql("SELECT * FROM orders LIMIT 1000")

        self.assertTrue(valid)
        self.assertIn(f"LIMIT {MAX_RESULT_ROWS}", sql)

    def test_unknown_column_is_rejected_using_the_temporary_schema(self):
        valid, _, error = validate_sql("SELECT missing_column FROM orders")

        self.assertFalse(valid)
        self.assertIn("missing_column", error)

    def test_missing_database_is_rejected_without_creating_a_file(self):
        missing_root = self.database_root / "missing"
        with patch("agents.workflow.BASE_DIR", missing_root):
            valid, _, error = validate_sql("SELECT * FROM orders")

        self.assertFalse(valid)
        self.assertTrue(error)
        self.assertFalse(missing_root.exists())


if __name__ == "__main__":
    unittest.main()
