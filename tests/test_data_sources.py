import unittest
from pathlib import Path

from core_engine.data_source_registry import DataSourceError, create_data_source_registry


class DataSourceRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = create_data_source_registry(Path(__file__).resolve().parents[1])

    def test_only_enabled_sources_are_exposed(self):
        sources = self.registry.list_enabled()

        self.assertEqual({source["id"] for source in sources}, {"sqlite_local", "federated_demo"})
        self.assertTrue(all("connection_secret_ref" not in source for source in sources))

    def test_disabled_source_cannot_be_resolved(self):
        with self.assertRaises(DataSourceError):
            self.registry.resolve("mysql_business")

    def test_enabled_source_resolves_execution_mode(self):
        source = self.registry.resolve("federated_demo")

        self.assertEqual(source["execution_mode"], "federated")


if __name__ == "__main__":
    unittest.main()
