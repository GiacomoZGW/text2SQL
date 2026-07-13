"""Short-term conversation memory and durable user preferences."""

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "memory.db"
DEFAULT_RETENTION_DAYS = 30


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryStore:
    def __init__(self, db_path: Path | str | None = None, retention_days: int | None = None):
        self.db_path = Path(db_path or os.getenv("MEMORY_DB_PATH", DEFAULT_DB_PATH))
        self.retention_days = max(1, int(retention_days or os.getenv("MEMORY_RETENTION_DAYS", DEFAULT_RETENTION_DAYS)))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

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
                """
            )
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
            preference_count = 0
            if include_preferences:
                preference_count = connection.execute("DELETE FROM user_preferences WHERE user_id = ?", (user_id,)).rowcount
            connection.commit()
            return {"conversation_turns": turn_count, "preferences": preference_count}
        finally:
            connection.close()

    def get_conversation_context(self, conversation_id: str | None, limit: int = 4) -> list[dict[str, Any]]:
        if not conversation_id:
            return []
        self.purge_expired()
        connection = self._connect()
        try:
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

    def record_turn(
        self,
        conversation_id: str | None,
        user_id: str,
        data_source_id: str,
        user_query: str,
        intent: str,
        entities: dict[str, Any] | None,
        answer: str,
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
            connection.commit()
        finally:
            connection.close()


memory_store = MemoryStore()
