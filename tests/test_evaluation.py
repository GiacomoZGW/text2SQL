import unittest
from pathlib import Path

from core_engine.evaluation import load_golden_cases, score_final_state, summarize_scores


class EvaluationTests(unittest.TestCase):
    def test_default_suite_excludes_extended_cases(self):
        suite_name, cases = load_golden_cases(Path(__file__).resolve().parents[1])

        self.assertEqual(suite_name, "data-agent-golden-regression")
        self.assertEqual({case["id"] for case in cases}, {"greeting", "unsafe-delete"})

    def test_sql_scoring_requires_intent_validation_execution_and_expected_fragments(self):
        case = {
            "id": "orders",
            "expected_intent": "text_to_sql",
            "expected_sql_contains": ["select", "count"],
            "expected_result_contains": ["42"],
        }
        score = score_final_state(
            case,
            {
                "intent": "text_to_sql",
                "generated_sql": "SELECT COUNT(*) AS total FROM orders",
                "validation_result": "VALID",
                "execution_result": '[{"total": 42}]',
            },
        )

        self.assertEqual(score["status"], "passed")
        self.assertTrue(score["intent_correct"])
        self.assertTrue(score["sql_executable"])
        self.assertTrue(score["result_correct"])

    def test_summary_keeps_intent_sql_and_result_dimensions_separate(self):
        summary = summarize_scores(
            [
                {"category": "intent", "status": "passed", "intent_correct": True, "sql_executable": None, "result_correct": None},
                {"category": "sql", "status": "failed", "intent_correct": True, "sql_executable": False, "result_correct": None},
            ]
        )

        self.assertEqual(summary["intent_accuracy"], 100.0)
        self.assertEqual(summary["sql_executable_rate"], 0.0)
        self.assertEqual(summary["by_category"]["sql"]["passed"], 0)


if __name__ == "__main__":
    unittest.main()
