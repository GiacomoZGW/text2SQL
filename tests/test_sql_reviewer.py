import unittest

from core_engine.sql_reviewer import review_execution_plan, review_sql_candidate


CATALOG = {
    "tables": [
        {
            "name": "orders",
            "columns": [
                {"name": "id"},
                {"name": "user_id"},
                {"name": "total_amount"},
                {"name": "category"},
                {"name": "city"},
            ],
        },
        {"name": "users", "columns": [{"name": "id"}, {"name": "city"}]},
    ],
    "relationships": [{"from": "orders.user_id", "to": "users.id", "kind": "foreign_key"}],
    "permissions": {},
}


class SqlReviewerTests(unittest.TestCase):
    def test_approves_valid_join_and_aggregation(self):
        result = review_sql_candidate(
            "SELECT u.city, SUM(o.total_amount) AS total FROM orders o JOIN users u ON o.user_id = u.id GROUP BY u.city",
            CATALOG,
        )
        self.assertTrue(result["approved"])

    def test_rejects_missing_column_and_invalid_join(self):
        result = review_sql_candidate(
            "SELECT o.missing FROM orders o JOIN users u ON o.id = u.id",
            CATALOG,
        )
        self.assertFalse(result["approved"])
        self.assertTrue(any("Column does not exist" in error for error in result["errors"]))
        self.assertTrue(any("JOIN condition" in error for error in result["errors"]))

    def test_approves_aggregate_output_alias_reused_by_order_by(self):
        result = review_sql_candidate(
            "SELECT COUNT(DISTINCT user_id) AS user_count FROM orders ORDER BY user_count DESC",
            CATALOG,
        )
        self.assertTrue(result["approved"])

    def test_approves_city_distribution_query_with_count_alias(self):
        result = review_sql_candidate(
            "SELECT city, COUNT(DISTINCT user_id) AS user_count FROM orders "
            "WHERE category = 'electronics' GROUP BY city ORDER BY user_count DESC",
            CATALOG,
        )
        self.assertTrue(result["approved"])

    def test_rejects_invalid_aggregate_shape_and_plan_risk(self):
        aggregate = review_sql_candidate("SELECT id, SUM(total_amount) FROM orders", CATALOG)
        plan = review_execution_plan([("SCAN orders",), ("SCAN users",)], max_full_scans=1)
        self.assertFalse(aggregate["approved"])
        self.assertFalse(plan["approved"])
