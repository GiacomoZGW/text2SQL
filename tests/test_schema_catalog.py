import unittest

from core_engine.schema_catalog import build_schema_catalog, render_schema_catalog


class SchemaCatalogTests(unittest.TestCase):
    def test_live_schema_is_enriched_with_business_metadata_and_version(self):
        catalog = build_schema_catalog(
            "Table: orders\nColumns: id (INTEGER), user_id (TEXT), total_amount (REAL)",
            "sqlite_local",
            "sqlite",
        )

        self.assertEqual(catalog["tables"][0]["name"], "orders")
        self.assertEqual(catalog["tables"][0]["columns"][1]["aliases"], ["user", "customer", "buyer"])
        self.assertIn("gmv", catalog["metrics"])
        rendered = render_schema_catalog(catalog)
        self.assertIn("Schema Version:", rendered)
        self.assertIn("Aliases: order, orders", rendered)
