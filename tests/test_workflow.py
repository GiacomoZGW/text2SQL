import unittest

from agents.workflow import MAX_RESULT_ROWS, validate_sql


class ValidateSqlTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
