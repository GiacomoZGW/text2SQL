import json
import unittest

from core_engine.context_builder import (
    RESULT_MAX_ROWS,
    SCHEMA_MAX_CHARS,
    build_agent_context,
)


class ContextBuilderTests(unittest.TestCase):
    def _state(self):
        return {
            "conversation_context": [
                {
                    "user_query": "show sales by city " + "q" * 1_000,
                    "intent": "text_to_sql",
                    "entities": {"metric": "sales"},
                    "answer_summary": "summary " + "a" * 1_000,
                    "data_source_id": "sqlite_local",
                }
            ],
            "conversation_checkpoint": {
                "active_data_source_id": "sqlite_local",
                "confirmed_entities": {"metric": "sales", "time_range": "last month"},
                "last_successful_sql": "SELECT SUM(total) FROM orders",
            },
            "user_preferences": {
                "response_style": "concise",
                "metric_aliases": {"GMV": "actual_payment"},
            },
            "semantic_memories": [
                {
                    "kind": "sql_template",
                    "label": "monthly sales",
                    "definition": "trusted reference " + "m" * 1_500,
                    "sql_template": "SELECT * FROM orders",
                    "metadata": {"metric": "sales"},
                    "similarity": 0.9,
                }
            ],
            "query_plan": {"metric": "sales"},
            "relevant_schemas": "Table: orders\nColumns: id, city, total",
            "execution_result": json.dumps(
                [
                    {"id": index, "note": "x" * 600}
                    for index in range(RESULT_MAX_ROWS + 5)
                ]
            ),
            "generated_sql": "SELECT id, note FROM orders",
        }

    def test_intent_context_excludes_semantic_memory(self):
        package = build_agent_context(self._state(), "intent")

        self.assertIn("Recent conversation turns", package.content)
        self.assertIn("Structured conversation checkpoint", package.content)
        self.assertNotIn("Relevant semantic memory", package.content)
        self.assertNotIn("monthly sales", package.content)
        self.assertLessEqual(len(package.content), package.max_chars)

    def test_sql_context_is_bounded_and_includes_retrieved_memory(self):
        package = build_agent_context(self._state(), "sql")

        self.assertIn("Relevant semantic memory", package.content)
        self.assertIn("last_successful_sql", package.content)
        self.assertIn("monthly sales", package.content)
        self.assertLessEqual(len(package.content), package.max_chars)
        self.assertGreaterEqual(package.metadata["context_estimated_tokens"], 1)

    def test_analyst_context_limits_rows_and_large_cells(self):
        package = build_agent_context(self._state(), "analyst")

        self.assertIn('"shown_rows":' + str(RESULT_MAX_ROWS), package.content)
        self.assertIn("truncated", package.content)
        self.assertIn("Actual query result", package.truncated_sections)
        self.assertLessEqual(len(package.content), package.max_chars)

    def test_reviewer_schema_context_is_capped(self):
        state = self._state()
        state["relevant_schemas"] = "\n\n".join(
            f"Table: table_{index}\nColumns: " + "col TEXT, " * 300
            for index in range(10)
        )

        package = build_agent_context(state, "reviewer")

        self.assertIn("Additional schema sections omitted", package.content)
        self.assertIn("Relevant live schema", package.truncated_sections)
        self.assertLessEqual(len(package.content), SCHEMA_MAX_CHARS + 3_000)


if __name__ == "__main__":
    unittest.main()
