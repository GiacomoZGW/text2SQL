"""User-scoped semantic memory with DashScope vector retrieval and safe fallback."""

import hashlib
import json
import math
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core_engine.runtime_storage import RuntimeStorage
from core_engine.runtime_config import get_runtime_config
from vector_store.schema_indexer import DashScopeSDKEmbeddings

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "memory.db"
DEFAULT_RETENTION_DAYS = 90
ALLOWED_KINDS = {"metric_definition", "field_alias", "sql_template"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


class SemanticMemoryStore:
    def __init__(self, db_path: Path | str | None = None, retention_days: int | None = None):
        self.db_path = Path(db_path or os.getenv("MEMORY_DB_PATH", DEFAULT_DB_PATH))
        self.retention_days = max(1, int(retention_days or os.getenv("SEMANTIC_MEMORY_RETENTION_DAYS", DEFAULT_RETENTION_DAYS)))
        self._embeddings: DashScopeSDKEmbeddings | None = None
        self._embedding_unavailable = False
        database_url = None if db_path is not None else os.getenv("MEMORY_DATABASE_URL") or os.getenv("RUNTIME_DATABASE_URL")
        self._storage = RuntimeStorage(self.db_path, database_url)
        self._initialize()

    def _connect(self):
        return self._storage.connect()

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS semantic_memories (
                    memory_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    label TEXT NOT NULL,
                    definition TEXT NOT NULL,
                    sql_template TEXT,
                    data_source_scope TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    content_hash TEXT NOT NULL,
                    embedding_json TEXT,
                    embedding_model TEXT,
                    confirmed INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    UNIQUE(user_id, content_hash)
                );
                CREATE INDEX IF NOT EXISTS idx_semantic_memories_lookup
                    ON semantic_memories(user_id, data_source_scope, expires_at);
                """
            )
            connection.commit()
        finally:
            connection.close()

    def _embed(self, text: str) -> list[float] | None:
        if self._embedding_unavailable:
            return None
        try:
            if self._embeddings is None:
                self._embeddings = DashScopeSDKEmbeddings()
            return self._embeddings.embed_query(text)
        except Exception:
            self._embedding_unavailable = True
            return None

    @staticmethod
    def _content(label: str, definition: str, sql_template: str | None, metadata: dict[str, Any]) -> str:
        return "\n".join(
            part for part in [label, definition, sql_template or "", json.dumps(metadata, ensure_ascii=False)] if part
        )

    def purge_expired(self) -> None:
        connection = self._connect()
        try:
            connection.execute("DELETE FROM semantic_memories WHERE expires_at < ?", (_utc_now(),))
            connection.commit()
        finally:
            connection.close()

    def upsert(
        self,
        user_id: str,
        kind: str,
        label: str,
        definition: str,
        data_source_scope: str | None = None,
        sql_template: str | None = None,
        metadata: dict[str, Any] | None = None,
        confirmed: bool = True,
        retention_days: int | None = None,
    ) -> dict[str, Any]:
        if kind not in ALLOWED_KINDS:
            raise ValueError(f"Unsupported semantic-memory kind: {kind}")
        if not label.strip() or not definition.strip():
            raise ValueError("label and definition are required")
        metadata = metadata or {}
        content = self._content(label.strip(), definition.strip(), sql_template, metadata)
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        memory_id = hashlib.sha256(f"{user_id}:{content_hash}".encode("utf-8")).hexdigest()
        embedding = self._embed(content)
        expires_at = (datetime.now(timezone.utc) + timedelta(days=retention_days or self.retention_days)).isoformat()
        now = _utc_now()
        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT INTO semantic_memories (
                    memory_id, user_id, kind, label, definition, sql_template, data_source_scope,
                    metadata_json, content_hash, embedding_json, embedding_model, confirmed,
                    created_at, updated_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, content_hash) DO UPDATE SET
                    label = excluded.label,
                    definition = excluded.definition,
                    sql_template = excluded.sql_template,
                    data_source_scope = excluded.data_source_scope,
                    metadata_json = excluded.metadata_json,
                    embedding_json = COALESCE(excluded.embedding_json, semantic_memories.embedding_json),
                    embedding_model = COALESCE(excluded.embedding_model, semantic_memories.embedding_model),
                    confirmed = excluded.confirmed,
                    updated_at = excluded.updated_at,
                    expires_at = excluded.expires_at
                """,
                (
                    memory_id,
                    user_id,
                    kind,
                    label.strip(),
                    definition.strip(),
                    sql_template,
                    data_source_scope,
                    json.dumps(metadata, ensure_ascii=False),
                    content_hash,
                    json.dumps(embedding) if embedding else None,
                    get_runtime_config().embedding_model if embedding else None,
                    int(confirmed),
                    now,
                    now,
                    expires_at,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        return {"memory_id": memory_id, "embedding_stored": embedding is not None, "expires_at": expires_at}

    def search(
        self,
        user_id: str,
        query: str,
        data_source_id: str | None,
        limit: int = 4,
    ) -> list[dict[str, Any]]:
        self.purge_expired()
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT * FROM semantic_memories
                WHERE user_id = ? AND confirmed = 1 AND expires_at >= ?
                  AND (data_source_scope IS NULL OR data_source_scope = '' OR data_source_scope = ?)
                """,
                (user_id, _utc_now(), data_source_id),
            ).fetchall()
        finally:
            connection.close()
        if not rows:
            return []

        query_embedding = self._embed(query)
        ranked: list[tuple[float, sqlite3.Row, str]] = []
        for row in rows:
            content = self._content(
                row["label"], row["definition"], row["sql_template"], json.loads(row["metadata_json"] or "{}")
            )
            embedding = json.loads(row["embedding_json"]) if row["embedding_json"] else None
            if query_embedding and embedding:
                score = _cosine_similarity(query_embedding, embedding)
                source = "dashscope_vector"
            else:
                query_chars = set(query.lower())
                content_chars = set(content.lower())
                score = len(query_chars & content_chars) / max(len(query_chars), 1)
                source = "lexical_fallback"
            ranked.append((score, row, source))

        matches = []
        for score, row, source in sorted(ranked, key=lambda item: item[0], reverse=True)[: max(1, min(limit, 10))]:
            matches.append(
                {
                    "memory_id": row["memory_id"],
                    "kind": row["kind"],
                    "label": row["label"],
                    "definition": row["definition"],
                    "sql_template": row["sql_template"],
                    "data_source_scope": row["data_source_scope"],
                    "metadata": json.loads(row["metadata_json"] or "{}"),
                    "similarity": round(score, 4),
                    "source": source,
                }
            )
        return matches

    def delete(self, user_id: str, memory_id: str) -> bool:
        connection = self._connect()
        try:
            cursor = connection.execute(
                "DELETE FROM semantic_memories WHERE memory_id = ? AND user_id = ?", (memory_id, user_id)
            )
            connection.commit()
            return cursor.rowcount > 0
        finally:
            connection.close()

    def delete_all(self, user_id: str) -> int:
        connection = self._connect()
        try:
            cursor = connection.execute("DELETE FROM semantic_memories WHERE user_id = ?", (user_id,))
            connection.commit()
            return cursor.rowcount
        finally:
            connection.close()

    def save_successful_sql_template(
        self,
        user_id: str,
        data_source_id: str,
        sql: str,
        intent: str,
        entities: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not sql.strip():
            return None
        template = re.sub(r"'(?:''|[^'])*'", "?", sql)
        template = re.sub(r"\b\d+(?:\.\d+)?\b", "?", template)
        safe_entities = {
            key: value
            for key, value in (entities or {}).items()
            if key in {"metric", "time_range", "dimensions", "target_data_source", "requires_federation"}
        }
        metric = str(safe_entities.get("metric") or intent)
        return self.upsert(
            user_id=user_id,
            kind="sql_template",
            label=f"Verified {metric} SQL template",
            definition="Validated read-only SQL template. It contains no query result or original user question.",
            data_source_scope=data_source_id,
            sql_template=template,
            metadata={"intent": intent, "entities": safe_entities, "verification": "executed"},
            confirmed=True,
        )


semantic_memory_store = SemanticMemoryStore()
