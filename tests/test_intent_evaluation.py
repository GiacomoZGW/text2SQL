import unittest

from agents.workflow import classify_intent_rules


INTENT_EVALUATION_SAMPLES = (
    ("你好", "greeting"),
    ("怎么使用这个系统", "operation_help"),
    ("有哪些表和字段", "schema_question"),
    ("删除所有订单", "unsafe_operation"),
    ("查一下", "clarification_required"),
    ("统计上个月订单数量", "text_to_sql"),
    ("跨库查询行为日志和订单", "text_to_sql"),
    ("分析订单转化率趋势", "data_analysis"),
    ("今天天气怎么样", "out_of_scope"),
)


class IntentEvaluationTests(unittest.TestCase):
    def test_fixed_intent_samples_meet_baseline_accuracy(self):
        correct = 0
        clarification_hits = 0
        clarification_expected = 0
        for query, expected_intent in INTENT_EVALUATION_SAMPLES:
            result = classify_intent_rules(query)
            self.assertIsNotNone(result, query)
            correct += result["intent"] == expected_intent
            clarification_expected += expected_intent == "clarification_required"
            clarification_hits += result["intent"] == "clarification_required" and result["needs_clarification"]

        accuracy = correct / len(INTENT_EVALUATION_SAMPLES)
        clarification_hit_rate = clarification_hits / clarification_expected
        self.assertGreaterEqual(accuracy, 0.9)
        self.assertEqual(clarification_hit_rate, 1.0)


if __name__ == "__main__":
    unittest.main()
