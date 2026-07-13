import unittest

from agents.workflow import _apply_intent_guardrails, _parse_intent_classification, classify_intent_rules, route_after_supervisor


class IntentClassificationTests(unittest.TestCase):
    def test_destructive_request_is_rejected_before_sql_generation(self):
        result = classify_intent_rules("删除所有订单")

        self.assertIsNotNone(result)
        self.assertEqual(result["intent"], "unsafe_operation")
        self.assertEqual(route_after_supervisor({"intent": result["intent"]}), "security")

    def test_greeting_uses_non_sql_response(self):
        result = classify_intent_rules("你好")

        self.assertIsNotNone(result)
        self.assertEqual(result["intent"], "greeting")
        self.assertEqual(route_after_supervisor({"intent": result["intent"]}), "help")

    def test_schema_question_continues_to_supervisor(self):
        result = classify_intent_rules("有哪些表和字段")

        self.assertIsNotNone(result)
        self.assertEqual(result["intent"], "schema_question")
        self.assertEqual(route_after_supervisor({"intent": result["intent"]}), "schema")

    def test_ambiguous_request_requires_clarification(self):
        result = classify_intent_rules("查一下")

        self.assertIsNotNone(result)
        self.assertEqual(result["intent"], "clarification_required")
        self.assertTrue(result["needs_clarification"])

    def test_low_confidence_llm_result_becomes_clarification(self):
        result = _apply_intent_guardrails(
            _parse_intent_classification(
                {
                    "intent": "text_to_sql",
                    "confidence": 0.4,
                    "reason": "ambiguous request",
                    "entities": {},
                }
            ),
            "帮我看看",
        )

        self.assertEqual(result["intent"], "clarification_required")
        self.assertTrue(result["needs_clarification"])

    def test_data_analysis_without_metric_becomes_clarification(self):
        result = _apply_intent_guardrails(
            _parse_intent_classification(
                {
                    "intent": "data_analysis",
                    "confidence": 0.9,
                    "reason": "analysis request",
                    "entities": {"time_range": "last month"},
                }
            ),
            "分析一下",
        )

        self.assertEqual(result["intent"], "clarification_required")


if __name__ == "__main__":
    unittest.main()
