"""Safe live-schema retrieval with an in-process DashScope vector cache.

Chroma is kept as an opt-in backend because its embedded Windows runtime can
terminate the interpreter instead of raising a Python exception.
"""

import hashlib
import math
import os
import re
from pathlib import Path
from typing import Any

from langchain_community.embeddings import DashScopeEmbeddings
from langchain_core.documents import Document

from core_engine.runtime_config import get_runtime_config
from core_engine.shared_state import shared_state

VECTOR_STORE_DIR = Path(__file__).resolve().parent / "chroma_db"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BACKEND = "dashscope_memory"
SCHEMA_CACHE_TTL_SECONDS = max(60, int(os.getenv("SCHEMA_CACHE_TTL_SECONDS", "3600")))


class DashScopeSDKEmbeddings:
    """Compatibility adapter used by semantic memory and schema retrieval."""

    def __init__(self, model: str | None = None):
        model_config = get_runtime_config()
        self._embeddings = DashScopeEmbeddings(
            model=model or model_config.embedding_model,
            dashscope_api_key=model_config.dashscope_api_key,
            max_retries=model_config.embedding_max_retries,
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embeddings.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._embeddings.embed_query(text)


# Schema changes are rare and the cache is rebuilt whenever its catalog hash changes.
_MEMORY_CATALOGS: dict[str, dict[str, Any]] = {}


def _catalog_hash(schema_context: str) -> str:
    return hashlib.sha256(schema_context.encode("utf-8")).hexdigest()


def _line_value(content: str, prefix: str) -> str:
    for line in content.splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip()
    return ""


def _schema_documents(schema_context: str, db_type: str, data_source_id: str = "") -> list[Document]:
    documents: list[Document] = []
    catalog_hash = _catalog_hash(schema_context)
    for section in schema_context.split("\n\n"):
        content = section.strip()
        if not content or not content.startswith("Table: "):
            continue
        table_name = content.splitlines()[0].removeprefix("Table: ").strip()
        aliases = _line_value(content, "Aliases: ")
        metrics = _line_value(content, "Metrics: ")
        documents.append(
            Document(
                page_content=content,
                metadata={
                    "table": table_name,
                    "db_type": db_type,
                    "data_source_id": data_source_id,
                    "aliases": aliases,
                    "metrics": metrics,
                    "catalog_hash": catalog_hash,
                },
            )
        )
    return documents


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


def _lexical_score(query: str, content: str) -> float:
    query_terms = set(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", query.lower()))
    content_terms = set(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", content.lower()))
    if not query_terms:
        return 0.0
    return len(query_terms & content_terms) / len(query_terms)


def _metadata_score(query: str, document: Document) -> float:
    metadata_text = " ".join(
        str(document.metadata.get(field, "")) for field in ("table", "aliases", "metrics", "data_source_id")
    )
    return _lexical_score(query, metadata_text)


def _hybrid_rank(
    query: str,
    documents: list[Document],
    vectors: list[list[float]] | None = None,
) -> list[tuple[Document, float]]:
    query_vector: list[float] | None = None
    if vectors:
        try:
            query_vector = DashScopeSDKEmbeddings().embed_query(query)
        except Exception:
            query_vector = None

    ranked: list[tuple[Document, float]] = []
    for index, document in enumerate(documents):
        lexical = _lexical_score(query, document.page_content)
        metadata = _metadata_score(query, document)
        if query_vector is not None and vectors is not None:
            vector = max(0.0, (_cosine_similarity(query_vector, vectors[index]) + 1.0) / 2.0)
            score = vector * 0.55 + lexical * 0.30 + metadata * 0.15
        else:
            score = lexical * 0.70 + metadata * 0.30
        ranked.append((document, score))
    return sorted(ranked, key=lambda item: item[1], reverse=True)


def _memory_matches(query: str, documents: list[Document], db_type: str, limit: int) -> tuple[list[Document], str, str]:
    catalog_hash = documents[0].metadata["catalog_hash"]
    cache = _MEMORY_CATALOGS.get(db_type)
    vectors: list[list[float]] | None = None
    detail_suffix = ""
    shared_cache_key = f"schema-vectors:{db_type}:{catalog_hash}"

    if cache and cache["catalog_hash"] == catalog_hash:
        vectors = cache["vectors"]
    else:
        shared_vectors = shared_state.get_json(shared_cache_key)
        if isinstance(shared_vectors, list) and len(shared_vectors) == len(documents):
            vectors = shared_vectors
            detail_suffix = "; cache=shared"
        else:
            try:
                vectors = DashScopeSDKEmbeddings().embed_documents([document.page_content for document in documents])
                if len(vectors) != len(documents):
                    raise ValueError("embedding count does not match schema documents")
                shared_state.set_json(shared_cache_key, vectors, SCHEMA_CACHE_TTL_SECONDS)
            except Exception as exc:
                vectors = None
                detail_suffix = f"; embedding fallback={exc}"
        _MEMORY_CATALOGS[db_type] = {"catalog_hash": catalog_hash, "vectors": vectors}

    if vectors:
        ranked = _hybrid_rank(query, documents, vectors)
        return [document for document, _ in ranked[:limit]], "dashscope_memory", detail_suffix + "; ranking=vector+keyword+metadata"

    ranked_documents = _hybrid_rank(query, documents)
    return [document for document, _ in ranked_documents[:limit]], "lexical_memory", detail_suffix + "; ranking=keyword+metadata"


def _collection_name(db_type: str) -> str:
    return f"schema_catalog_{db_type}"


def _get_chroma_vectorstore(db_type: str):
    # Import lazily so the default backend never loads Chroma's native runtime.
    from langchain_chroma import Chroma

    VECTOR_STORE_DIR.mkdir(parents=True, exist_ok=True)
    return Chroma(
        collection_name=_collection_name(db_type),
        persist_directory=str(VECTOR_STORE_DIR),
        embedding_function=DashScopeSDKEmbeddings(),
    )


def _chroma_matches(query: str, documents: list[Document], db_type: str, limit: int) -> list[Document]:
    vectorstore = _get_chroma_vectorstore(db_type)
    catalog_hash = documents[0].metadata["catalog_hash"]
    existing = vectorstore.get(include=["metadatas"])
    hashes = {metadata.get("catalog_hash") for metadata in existing.get("metadatas", []) if metadata}
    if not existing.get("ids") or len(existing["ids"]) != len(documents) or hashes != {catalog_hash}:
        if existing.get("ids"):
            vectorstore.delete(ids=existing["ids"])
        vectorstore.add_documents(documents)
    candidate_limit = min(max(limit * 3, limit), len(documents))
    return [document for document, _ in vectorstore.similarity_search_with_score(query, k=candidate_limit)]


def retrieve_relevant_schema(
    query: str,
    db_type: str,
    schema_context: str,
    limit: int = 4,
    data_source_id: str = "",
    allowed_tables: set[str] | None = None,
) -> dict[str, Any]:
    """Retrieve live schema safely, falling back to lexical ranking when embedding fails."""
    documents = _schema_documents(schema_context, db_type, data_source_id)
    if allowed_tables is not None:
        normalized_allowed = {table.lower() for table in allowed_tables}
        documents = [
            document
            for document in documents
            if document.metadata["table"].lower() in normalized_allowed
            or document.metadata["table"].lower().split(".")[-1] in normalized_allowed
        ]
    if not documents:
        return {"context": schema_context, "source": "fallback", "detail": "schema catalog is empty", "tables": []}

    bounded_limit = max(1, min(limit, len(documents)))
    backend = os.getenv("SCHEMA_VECTOR_BACKEND", DEFAULT_BACKEND).strip().lower()
    if backend == "chroma":
        try:
            matches = _chroma_matches(query, documents, db_type, bounded_limit)
            matches = [document for document, _ in _hybrid_rank(query, matches)[:bounded_limit]]
            source = "chroma"
            detail = f"retrieved={len(matches)} of {len(documents)} tables; ranking=vector+keyword+metadata"
        except Exception as exc:
            matches, source, suffix = _memory_matches(query, documents, db_type, bounded_limit)
            detail = f"Chroma unavailable: {exc}; retrieved={len(matches)} of {len(documents)} tables{suffix}"
    else:
        matches, source, suffix = _memory_matches(query, documents, db_type, bounded_limit)
        detail = f"retrieved={len(matches)} of {len(documents)} tables{suffix}"

    return {
        "context": "\n\n".join(document.page_content for document in matches),
        "source": source,
        "detail": detail,
        "tables": [document.metadata["table"] for document in matches],
    }


def build_schema_index(schema_context: str, db_type: str = "sqlite") -> None:
    """Warm the default in-memory schema cache without starting Chroma."""
    documents = _schema_documents(schema_context, db_type)
    if not documents:
        raise ValueError("schema_context does not contain any tables")
    _memory_matches("", documents, db_type, len(documents))
