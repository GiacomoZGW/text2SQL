import unittest
from unittest.mock import patch

from vector_store import schema_indexer


class SchemaIndexerTests(unittest.TestCase):
    def setUp(self):
        schema_indexer._MEMORY_CATALOGS.clear()
        self.schema = "\n\n".join(
            [
                "Table: orders\nColumns: id INTEGER, total_amount REAL, category TEXT",
                "Table: users\nColumns: id INTEGER, city TEXT, name TEXT",
            ]
        )

    @patch("vector_store.schema_indexer.DashScopeSDKEmbeddings")
    def test_default_backend_uses_in_process_dashscope_memory(self, embeddings_class):
        instance = embeddings_class.return_value
        instance.embed_documents.return_value = [[1.0, 0.0], [0.0, 1.0]]
        instance.embed_query.return_value = [1.0, 0.0]

        result = schema_indexer.retrieve_relevant_schema("sales total", "sqlite", self.schema)

        self.assertEqual(result["source"], "dashscope_memory")
        self.assertEqual(result["tables"], ["orders", "users"])
        self.assertIn("Table: orders", result["context"])

    @patch("vector_store.schema_indexer.DashScopeSDKEmbeddings")
    def test_embedding_failure_uses_lexical_memory_without_chroma(self, embeddings_class):
        embeddings_class.return_value.embed_documents.side_effect = RuntimeError("embedding unavailable")

        result = schema_indexer.retrieve_relevant_schema("city", "sqlite_lexical", self.schema)

        self.assertEqual(result["source"], "lexical_memory")
        self.assertEqual(result["tables"][0], "users")

    @patch("vector_store.schema_indexer.DashScopeSDKEmbeddings")
    def test_metadata_table_filter_is_applied_before_ranking(self, embeddings_class):
        embeddings_class.return_value.embed_documents.return_value = [[1.0, 0.0], [0.0, 1.0]]
        embeddings_class.return_value.embed_query.return_value = [0.0, 1.0]

        result = schema_indexer.retrieve_relevant_schema(
            "city",
            "sqlite_filtered",
            self.schema,
            data_source_id="sqlite_local",
            allowed_tables={"orders"},
        )

        self.assertEqual(result["tables"], ["orders"])


if __name__ == "__main__":
    unittest.main()
