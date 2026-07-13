import tempfile
import unittest
from pathlib import Path

from memory.semantic_store import SemanticMemoryStore


class SemanticMemoryStoreTests(unittest.TestCase):
    def test_confirmed_memory_is_user_scoped_and_searchable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SemanticMemoryStore(Path(temp_dir) / "memory.db", retention_days=30)
            stored = store.upsert(
                user_id="user-1",
                kind="metric_definition",
                label="GMV",
                definition="GMV is calculated from actual_payment.",
                data_source_scope="sqlite_local",
                metadata={"column": "actual_payment"},
                confirmed=True,
            )
            store.upsert(
                user_id="user-2",
                kind="metric_definition",
                label="GMV",
                definition="Other user's definition.",
                data_source_scope="sqlite_local",
                confirmed=True,
            )

            matches = store.search("user-1", "How should GMV be calculated?", "sqlite_local")
            other_matches = store.search("user-2", "actual_payment", "sqlite_local")

            self.assertEqual(matches[0]["label"], "GMV")
            self.assertEqual(matches[0]["definition"], "GMV is calculated from actual_payment.")
            self.assertEqual(other_matches[0]["definition"], "Other user's definition.")
            self.assertTrue(store.delete("user-1", stored["memory_id"]))
            self.assertEqual(store.search("user-1", "GMV", "sqlite_local"), [])

    def test_successful_sql_templates_are_redacted_before_storage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SemanticMemoryStore(Path(temp_dir) / "memory.db", retention_days=30)
            store.save_successful_sql_template(
                user_id="user-1",
                data_source_id="sqlite_local",
                sql="SELECT * FROM orders WHERE city = 'Shanghai' AND user_id = 123 LIMIT 50",
                intent="text_to_sql",
                entities={"metric": "orders"},
            )

            matches = store.search("user-1", "orders by city", "sqlite_local")

            self.assertIn("city = ?", matches[0]["sql_template"])
            self.assertNotIn("Shanghai", matches[0]["sql_template"])
            self.assertNotIn("123", matches[0]["sql_template"])


if __name__ == "__main__":
    unittest.main()
