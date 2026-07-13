"""Backward-compatible schema search entry point backed by Chroma."""

from .schema_indexer import retrieve_relevant_schema


def search_schemas(query: str, threshold: float, schema_context: str, db_type: str = "sqlite") -> str:
    """Return the Chroma-retrieved schema context for legacy callers."""
    limit = 6 if threshold < 0.6 else 4
    return retrieve_relevant_schema(query, db_type, schema_context, limit=limit)["context"]
