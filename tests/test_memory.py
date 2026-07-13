import tempfile
import unittest
from pathlib import Path

from memory.store import MemoryStore


class MemoryStoreTests(unittest.TestCase):
    def test_preferences_and_conversation_context_are_isolated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.db", retention_days=30)
            preferences = store.update_preferences(
                "user-1",
                {
                    "default_data_source_id": "federated_demo",
                    "response_style": "detailed",
                    "show_sql": False,
                    "metric_aliases": {"GMV": "actual_payment"},
                },
            )
            store.record_turn(
                "conversation-1",
                "user-1",
                "federated_demo",
                "统计上个月订单数量",
                "text_to_sql",
                {"metric": "订单数量", "time_range": "上个月"},
                "上个月订单数量为 120。",
            )
            store.record_turn(
                "conversation-1",
                "user-1",
                "federated_demo",
                "按城市拆分",
                "text_to_sql",
                {"dimension": "城市"},
                "已按城市拆分订单数量。",
            )

            context = store.get_conversation_context("conversation-1")
            other_context = store.get_conversation_context("conversation-2")

            self.assertEqual(preferences["default_data_source_id"], "federated_demo")
            self.assertFalse(preferences["show_sql"])
            self.assertEqual(preferences["metric_aliases"]["GMV"], "actual_payment")
            self.assertEqual([turn["user_query"] for turn in context], ["统计上个月订单数量", "按城市拆分"])
            self.assertEqual(other_context, [])

    def test_memory_can_be_disabled_and_deleted_per_user(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.db", retention_days=30)
            store.update_preferences("user-1", {"memory_enabled": False})
            store.record_turn(
                "conversation-1", "user-1", "sqlite_local", "hello", "greeting", {}, "hello"
            )

            self.assertFalse(store.get_preferences("user-1")["memory_enabled"])

            deleted = store.delete_user_memory("user-1")

            self.assertEqual(deleted["conversation_turns"], 1)
            self.assertEqual(deleted["preferences"], 1)
            self.assertTrue(store.get_preferences("user-1")["memory_enabled"])
            self.assertEqual(store.get_conversation_context("conversation-1"), [])


if __name__ == "__main__":
    unittest.main()
