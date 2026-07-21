"""Short-term conversation memory and durable user preferences."""

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core_engine.runtime_storage import RuntimeStorage

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "memory.db"
DEFAULT_RETENTION_DAYS = 30
DEFAULT_HOT_CONTEXT_CHARS = 6_000
DEFAULT_HOT_CONTEXT_MAX_TURNS = 12


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryStore:
    def __init__(self, db_path: Path | str | None = None, retention_days: int | None = None):
        self.db_path = Path(db_path or os.getenv("MEMORY_DB_PATH", DEFAULT_DB_PATH))
        self.retention_days = max(1, int(retention_days or os.getenv("MEMORY_RETENTION_DAYS", DEFAULT_RETENTION_DAYS)))
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
                CREATE TABLE IF NOT EXISTS user_preferences (
                    user_id TEXT PRIMARY KEY,
                    default_data_source_id TEXT,
                    response_style TEXT NOT NULL DEFAULT 'concise',
                    memory_enabled INTEGER NOT NULL DEFAULT 1,
                    show_sql INTEGER NOT NULL DEFAULT 1,
                    default_limit INTEGER NOT NULL DEFAULT 50,
                    metric_aliases_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS conversation_turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    data_source_id TEXT,
                    user_query TEXT NOT NULL,
                    intent TEXT,
                    entities_json TEXT NOT NULL DEFAULT '{}',
                    answer_summary TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_memory_turns_conversation
                    ON conversation_turns(conversation_id, id DESC);
                CREATE INDEX IF NOT EXISTS idx_memory_turns_created_at
                    ON conversation_turns(created_at);

                CREATE TABLE IF NOT EXISTS conversation_checkpoints (
                    conversation_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    checkpoint_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (conversation_id, user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_memory_checkpoints_updated_at
                    ON conversation_checkpoints(updated_at);
                """
            )
            if not self._storage.is_postgres:
                self._ensure_preference_columns(connection)
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _ensure_preference_columns(connection: sqlite3.Connection) -> None:
        existing = {row["name"] for row in connection.execute("PRAGMA table_info(user_preferences)").fetchall()}
        if "memory_enabled" not in existing:
            connection.execute("ALTER TABLE user_preferences ADD COLUMN memory_enabled INTEGER NOT NULL DEFAULT 1")

    def purge_expired(self) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.retention_days)).isoformat()
        connection = self._connect()
        try:
            connection.execute("DELETE FROM conversation_turns WHERE created_at < ?", (cutoff,))
            connection.execute("DELETE FROM conversation_checkpoints WHERE updated_at < ?", (cutoff,))
            connection.commit()
        finally:
            connection.close()

    def get_preferences(self, user_id: str) -> dict[str, Any]:
        defaults = {
            "user_id": user_id,
            "default_data_source_id": None,
            "response_style": "concise",
            "memory_enabled": True,
            "show_sql": True,
            "default_limit": 50,
            "metric_aliases": {},
        }
        connection = self._connect()
        try:
            row = connection.execute("SELECT * FROM user_preferences WHERE user_id = ?", (user_id,)).fetchone()
            if row is None:
                return defaults
            aliases = json.loads(row["metric_aliases_json"] or "{}")
            return {
                "user_id": row["user_id"],
                "default_data_source_id": row["default_data_source_id"],
                "response_style": row["response_style"],
                "memory_enabled": bool(row["memory_enabled"]),
                "show_sql": bool(row["show_sql"]),
                "default_limit": row["default_limit"],
                "metric_aliases": aliases if isinstance(aliases, dict) else {},
            }
        finally:
            connection.close()

    def update_preferences(self, user_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        current = self.get_preferences(user_id)
        allowed = {"default_data_source_id", "response_style", "memory_enabled", "show_sql", "default_limit", "metric_aliases"}
        merged = {**current, **{key: value for key, value in updates.items() if key in allowed}}
        merged["default_limit"] = max(1, min(int(merged["default_limit"]), 500))
        merged["show_sql"] = bool(merged["show_sql"])
        merged["memory_enabled"] = bool(merged["memory_enabled"])
        if not isinstance(merged["metric_aliases"], dict):
            raise ValueError("metric_aliases must be an object")
        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT INTO user_preferences (
                    user_id, default_data_source_id, response_style, memory_enabled, show_sql, default_limit,
                    metric_aliases_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    default_data_source_id = excluded.default_data_source_id,
                    response_style = excluded.response_style,
                    memory_enabled = excluded.memory_enabled,
                    show_sql = excluded.show_sql,
                    default_limit = excluded.default_limit,
                    metric_aliases_json = excluded.metric_aliases_json,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    merged["default_data_source_id"],
                    str(merged["response_style"]),
                    int(merged["memory_enabled"]),
                    int(merged["show_sql"]),
                    merged["default_limit"],
                    json.dumps(merged["metric_aliases"], ensure_ascii=False),
                    _utc_now(),
                ),
            )
            connection.commit()
        finally:
            connection.close()
        return self.get_preferences(user_id)

    def delete_user_memory(self, user_id: str, include_preferences: bool = True) -> dict[str, int]:
        connection = self._connect()
        try:
            turn_count = connection.execute("DELETE FROM conversation_turns WHERE user_id = ?", (user_id,)).rowcount
            checkpoint_count = connection.execute(
                "DELETE FROM conversation_checkpoints WHERE user_id = ?", (user_id,)
            ).rowcount
            preference_count = 0
            if include_preferences:
                preference_count = connection.execute("DELETE FROM user_preferences WHERE user_id = ?", (user_id,)).rowcount
            connection.commit()
            return {
                "conversation_turns": turn_count,
                "conversation_checkpoints": checkpoint_count,
                "preferences": preference_count,
            }
        finally:
            connection.close()

    def get_conversation_context(
        self, conversation_id: str | None, limit: int = 4, user_id: str | None = None
    ) -> list[dict[str, Any]]:
        if not conversation_id:
            return []
        self.purge_expired()
        connection = self._connect()
        try:
            if user_id:
                rows = connection.execute(
                    """
                    SELECT user_query, intent, entities_json, answer_summary, data_source_id, created_at
                    FROM conversation_turns WHERE conversation_id = ? AND user_id = ? ORDER BY id DESC LIMIT ?
                    """,
                    (conversation_id, user_id, max(1, min(limit, 10))),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT user_query, intent, entities_json, answer_summary, data_source_id, created_at
                    FROM conversation_turns WHERE conversation_id = ? ORDER BY id DESC LIMIT ?
                    """,
                    (conversation_id, max(1, min(limit, 10))),
                ).fetchall()
            context = []
            for row in reversed(rows):
                entities = json.loads(row["entities_json"] or "{}")
                context.append(
                    {
                        "user_query": row["user_query"],
                        "intent": row["intent"],
                        "entities": entities if isinstance(entities, dict) else {},
                        "answer_summary": row["answer_summary"] or "",
                        "data_source_id": row["data_source_id"],
                        "created_at": row["created_at"],
                    }
                )
            return context
        finally:
            connection.close()

    @staticmethod
    def _row_to_turn(row: sqlite3.Row) -> dict[str, Any]:
        entities = json.loads(row["entities_json"] or "{}")
        return {
            "user_query": row["user_query"],
            "intent": row["intent"],
            "entities": entities if isinstance(entities, dict) else {},
            "answer_summary": row["answer_summary"] or "",
            "data_source_id": row["data_source_id"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _hot_context_config() -> tuple[int, int]:
        try:
            char_budget = int(os.getenv("CONTEXT_HOT_HISTORY_MAX_CHARS", str(DEFAULT_HOT_CONTEXT_CHARS)))
        except ValueError:
            char_budget = DEFAULT_HOT_CONTEXT_CHARS
        try:
            max_turns = int(os.getenv("CONTEXT_HOT_HISTORY_MAX_TURNS", str(DEFAULT_HOT_CONTEXT_MAX_TURNS)))
        except ValueError:
            max_turns = DEFAULT_HOT_CONTEXT_MAX_TURNS
        return max(1_000, char_budget), max(1, min(max_turns, 30))

    def get_hot_context(
        self,
        conversation_id: str | None,
        user_id: str,
    ) -> dict[str, Any]:
        """Return a budgeted recent-turn window plus a durable task checkpoint."""
        empty = {
            "recent_turns": [],
            "checkpoint": {},
            "metadata": {
                "available_turns": 0,
                "selected_turns": 0,
                "char_budget": 0,
                "used_chars": 0,
                "older_turns_omitted": False,
            },
        }
        if not conversation_id:
            return empty

        self.purge_expired()
        char_budget, max_turns = self._hot_context_config()
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT user_query, intent, entities_json, answer_summary, data_source_id, created_at
                FROM conversation_turns
                WHERE conversation_id = ? AND user_id = ?
                ORDER BY id DESC LIMIT ?
                """,
                (conversation_id, user_id, max_turns),
            ).fetchall()
            total = connection.execute(
                """
                SELECT COUNT(*) AS count FROM conversation_turns
                WHERE conversation_id = ? AND user_id = ?
                """,
                (conversation_id, user_id),
            ).fetchone()["count"]
            checkpoint_row = connection.execute(
                """
                SELECT checkpoint_json FROM conversation_checkpoints
                WHERE conversation_id = ? AND user_id = ?
                """,
                (conversation_id, user_id),
            ).fetchone()
        finally:
            connection.close()

        checkpoint: dict[str, Any] = {}
        if checkpoint_row:
            try:
                parsed = json.loads(checkpoint_row["checkpoint_json"])
                checkpoint = parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                checkpoint = {}

        selected: list[dict[str, Any]] = []
        used_chars = 0
        for row in rows:
            turn = self._row_to_turn(row)
            turn_chars = len(json.dumps(turn, ensure_ascii=False, default=str))
            if selected and used_chars + turn_chars > char_budget:
                break
            selected.append(turn)
            used_chars += turn_chars

        selected.reverse()
        return {
            "recent_turns": selected,
            "checkpoint": checkpoint,
            "metadata": {
                "available_turns": total,
                "selected_turns": len(selected),
                "char_budget": char_budget,
                "used_chars": used_chars,
                "older_turns_omitted": total > len(selected),
            },
        }

    @staticmethod
    def _checkpoint_entities(entities: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(entities, dict):
            return {}
        return {
            str(key)[:100]: value
            for key, value in entities.items()
            if value not in (None, "", [], {})
        }

    def record_turn(
        self,
        conversation_id: str | None,
        user_id: str,
        data_source_id: str,
        user_query: str,
        intent: str,
        entities: dict[str, Any] | None,
        answer: str,
        generated_sql: str = "",
        execution_failed: bool = False,
        error_detail: str = "",
    ) -> None:
        if not conversation_id:
            return
        self.purge_expired()
        answer_summary = " ".join(answer.split())[:1200]
        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT INTO conversation_turns (
                    conversation_id, user_id, data_source_id, user_query, intent,
                    entities_json, answer_summary, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    user_id,
                    data_source_id,
                    user_query[:2000],
                    intent,
                    json.dumps(entities or {}, ensure_ascii=False),
                    answer_summary,
                    _utc_now(),
                ),
            )
            checkpoint_row = connection.execute(
                """
                SELECT checkpoint_json FROM conversation_checkpoints
                WHERE conversation_id = ? AND user_id = ?
                """,
                (conversation_id, user_id),
            ).fetchone()
            checkpoint: dict[str, Any] = {}
            if checkpoint_row:
                try:
                    parsed = json.loads(checkpoint_row["checkpoint_json"])
                    checkpoint = parsed if isinstance(parsed, dict) else {}
                except json.JSONDecodeError:
                    checkpoint = {}

            checkpoint.update(
                {
                    "version": 1,
                    "active_data_source_id": data_source_id,
                    "latest_intent": intent[:80],
                    "latest_user_query": user_query[:1_000],
                    "latest_answer_summary": answer_summary,
                    "updated_at": _utc_now(),
                }
            )
            entity_snapshot = self._checkpoint_entities(entities)
            if entity_snapshot:
                prior_entities = checkpoint.get("confirmed_entities")
                merged_entities = dict(prior_entities) if isinstance(prior_entities, dict) else {}
                merged_entities.update(entity_snapshot)
                checkpoint["confirmed_entities"] = merged_entities
            if not execution_failed and generated_sql.strip():
                checkpoint["last_successful_sql"] = generated_sql[:4_000]
                checkpoint.pop("last_error", None)
            elif execution_failed:
                checkpoint["last_error"] = (error_detail or answer_summary)[:2_000]

            connection.execute(
                """
                INSERT INTO conversation_checkpoints (
                    conversation_id, user_id, checkpoint_json, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(conversation_id, user_id) DO UPDATE SET
                    checkpoint_json = excluded.checkpoint_json,
                    updated_at = excluded.updated_at
                """,
                (
                    conversation_id,
                    user_id,
                    json.dumps(checkpoint, ensure_ascii=False),
                    checkpoint["updated_at"],
                ),
            )
            connection.commit()
        finally:
            connection.close()


memory_store = MemoryStore()
