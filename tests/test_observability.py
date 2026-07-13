import tempfile
import unittest
from pathlib import Path

from observability.store import ObservabilityStore


class ObservabilityStoreTests(unittest.TestCase):
    def test_request_events_llm_usage_and_summary_are_persisted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ObservabilityStore(Path(temp_dir) / "observability.db")
            store.start_request("request-1", "user-1", "统计订单数量", "sqlite")
            store.record_agent_event("request-1", "schema", "completed", "tables=2", {"db_type": "sqlite"})
            store.record_llm_call(
                request_id="request-1",
                agent="sql",
                model="test-model",
                prompt_chars=120,
                latency_ms=25.5,
                usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                status="success",
            )
            store.complete_request("request-1", "success", 80.0, 0)

            request = store.get_request("request-1")
            summary = store.summary()

            self.assertEqual(request["request"]["status"], "success")
            self.assertEqual(len(request["agent_events"]), 1)
            self.assertEqual(request["llm_calls"][0]["total_tokens"], 15)
            self.assertNotIn("统计订单数量", str(request))
            self.assertEqual(summary["requests"]["request_count"], 1)
            self.assertEqual(summary["llm"]["total_tokens"], 15)

    def test_intent_route_and_clarification_metrics_are_persisted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ObservabilityStore(Path(temp_dir) / "observability.db")
            store.start_request(
                "clarification-1",
                "user-1",
                "查一下",
                "sqlite",
                data_source_id="sqlite_local",
                conversation_id="conversation-1",
            )
            store.record_intent_result("clarification-1", "clarification_required", 0.62, True, 12.5)
            store.record_supervisor_route("clarification-1", "clarification")
            store.complete_request("clarification-1", "success", 20.0, 0)

            store.start_request(
                "followup-1",
                "user-1",
                "统计上个月订单数量",
                "sqlite",
                data_source_id="sqlite_local",
                conversation_id="conversation-1",
                clarification_parent_request_id="clarification-1",
            )
            store.record_intent_result("followup-1", "text_to_sql", 0.92, False, 8.0)
            store.record_supervisor_route("followup-1", "schema")
            store.record_llm_call(
                request_id="followup-1",
                agent="intent",
                model="test-model",
                prompt_chars=80,
                latency_ms=10.0,
                usage={"input_tokens": 8, "output_tokens": 4, "total_tokens": 12},
                status="success",
            )
            store.complete_request("followup-1", "success", 100.0, 0)
            store.mark_clarification_resolved("clarification-1")

            summary = store.summary()
            request = store.get_request("clarification-1")

            self.assertEqual(request["request"]["data_source_id"], "sqlite_local")
            self.assertEqual(request["request"]["clarification_resolved"], 1)
            self.assertEqual(summary["intent"]["classification"]["classified_count"], 2)
            self.assertEqual(summary["intent"]["classification"]["clarification_resolved_count"], 1)
            self.assertEqual(summary["intent"]["llm"]["total_tokens"], 12)
            self.assertEqual(summary["intent"]["routes"][0]["route"], "clarification")


if __name__ == "__main__":
    unittest.main()
