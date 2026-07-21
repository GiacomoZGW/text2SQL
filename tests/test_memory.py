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

            context = store.get_conversation_context("conversation-1", user_id="user-1")
            other_context = store.get_conversation_context("conversation-2")

            store.record_turn("conversation-1", "user-2", "sqlite_local", "private", "greeting", {}, "private")
            isolated_context = store.get_conversation_context("conversation-1", user_id="user-2")

            self.assertEqual(preferences["default_data_source_id"], "federated_demo")
            self.assertFalse(preferences["show_sql"])
            self.assertEqual(preferences["metric_aliases"]["GMV"], "actual_payment")
            self.assertEqual([turn["user_query"] for turn in context], ["统计上个月订单数量", "按城市拆分"])
            self.assertEqual(other_context, [])
            self.assertEqual([turn["user_query"] for turn in isolated_context], ["private"])

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

    def test_hot_context_uses_checkpoint_and_is_not_limited_to_four_turns(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.db", retention_days=30)
            for index in range(7):
                store.record_turn(
                    "conversation-1",
                    "user-1",
                    "sqlite_local",
                    f"question {index}",
                    "text_to_sql",
                    {"metric": "sales"} if index == 0 else {"dimension": f"city_{index}"},
                    f"answer {index}",
                    generated_sql="SELECT city, SUM(total) FROM orders GROUP BY city",
                )

            hot_context = store.get_hot_context("conversation-1", "user-1")

            self.assertEqual(hot_context["metadata"]["available_turns"], 7)
            self.assertEqual(hot_context["metadata"]["selected_turns"], 7)
            self.assertTrue(hot_context["checkpoint"])
            self.assertEqual(
                hot_context["checkpoint"]["confirmed_entities"]["metric"],
                "sales",
            )
            self.assertEqual(
                hot_context["checkpoint"]["confirmed_entities"]["dimension"],
                "city_6",
            )
            self.assertIn("GROUP BY city", hot_context["checkpoint"]["last_successful_sql"])

    def test_failed_turn_updates_checkpoint_error_without_erasing_last_sql(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.db", retention_days=30)
            store.record_turn(
                "conversation-1",
                "user-1",
                "sqlite_local",
                "successful request",
                "text_to_sql",
                {"metric": "sales"},
                "success",
                generated_sql="SELECT SUM(total) FROM orders",
            )
            store.record_turn(
                "conversation-1",
                "user-1",
                "sqlite_local",
                "failed request",
                "text_to_sql",
                {"time_range": "last month"},
                "failed",
                execution_failed=True,
                error_detail="ERROR: unknown column",
            )

            checkpoint = store.get_hot_context("conversation-1", "user-1")["checkpoint"]

            self.assertIn("SUM(total)", checkpoint["last_successful_sql"])
            self.assertEqual(checkpoint["last_error"], "ERROR: unknown column")
            self.assertEqual(checkpoint["confirmed_entities"]["time_range"], "last month")


if __name__ == "__main__":
    unittest.main()
