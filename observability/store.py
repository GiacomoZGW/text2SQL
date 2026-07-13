"""Local, privacy-conscious observability storage for Data Agent requests."""

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "observability.db"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ObservabilityStore:
    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path or os.getenv("OBSERVABILITY_DB_PATH", DEFAULT_DB_PATH))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS requests (
                    request_id TEXT PRIMARY KEY,
                    user_id TEXT,
                    query_hash TEXT NOT NULL,
                    query_chars INTEGER NOT NULL,
                    target_db TEXT NOT NULL,
                    data_source_id TEXT,
                    conversation_id TEXT,
                    clarification_parent_request_id TEXT,
                    clarification_resolved INTEGER NOT NULL DEFAULT 0,
                    intent TEXT,
                    intent_confidence REAL,
                    needs_clarification INTEGER NOT NULL DEFAULT 0,
                    intent_latency_ms REAL,
                    supervisor_route TEXT,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    latency_ms REAL,
                    retries INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT
                );

                CREATE TABLE IF NOT EXISTS agent_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    agent TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detail TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS llm_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    agent TEXT NOT NULL,
                    model TEXT,
                    prompt_chars INTEGER NOT NULL,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    total_tokens INTEGER,
                    latency_ms REAL NOT NULL,
                    status TEXT NOT NULL,
                    error_message TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_requests_started_at ON requests(started_at);
                CREATE INDEX IF NOT EXISTS idx_agent_events_request_id ON agent_events(request_id);
                CREATE INDEX IF NOT EXISTS idx_llm_calls_request_id ON llm_calls(request_id);
                """
            )
            self._ensure_request_columns(connection)
            connection.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_requests_conversation_id ON requests(conversation_id);
                CREATE INDEX IF NOT EXISTS idx_requests_intent ON requests(intent);
                """
            )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _ensure_request_columns(connection: sqlite3.Connection) -> None:
        existing = {row["name"] for row in connection.execute("PRAGMA table_info(requests)").fetchall()}
        columns = {
            "data_source_id": "TEXT",
            "conversation_id": "TEXT",
            "clarification_parent_request_id": "TEXT",
            "clarification_resolved": "INTEGER NOT NULL DEFAULT 0",
            "intent": "TEXT",
            "intent_confidence": "REAL",
            "needs_clarification": "INTEGER NOT NULL DEFAULT 0",
            "intent_latency_ms": "REAL",
            "supervisor_route": "TEXT",
        }
        for name, definition in columns.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE requests ADD COLUMN {name} {definition}")

    def start_request(
        self,
        request_id: str,
        user_id: str,
        query: str,
        target_db: str,
        data_source_id: str | None = None,
        conversation_id: str | None = None,
        clarification_parent_request_id: str | None = None,
    ) -> None:
        query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()
        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT INTO requests (
                    request_id, user_id, query_hash, query_chars, target_db, data_source_id,
                    conversation_id, clarification_parent_request_id, status, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', ?)
                """,
                (
                    request_id,
                    user_id,
                    query_hash,
                    len(query),
                    target_db,
                    data_source_id,
                    conversation_id,
                    clarification_parent_request_id,
                    _utc_now(),
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def complete_request(
        self,
        request_id: str,
        status: str,
        latency_ms: float,
        retries: int,
        error_message: str | None = None,
    ) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """
                UPDATE requests
                SET status = ?, completed_at = ?, latency_ms = ?, retries = ?, error_message = ?
                WHERE request_id = ?
                """,
                (status, _utc_now(), latency_ms, retries, error_message, request_id),
            )
            connection.commit()
        finally:
            connection.close()

    def record_intent_result(
        self,
        request_id: str,
        intent: str,
        confidence: float,
        needs_clarification: bool,
        latency_ms: float,
    ) -> None:
        if not request_id:
            return
        connection = self._connect()
        try:
            connection.execute(
                """
                UPDATE requests
                SET intent = ?, intent_confidence = ?, needs_clarification = ?, intent_latency_ms = ?
                WHERE request_id = ?
                """,
                (intent, confidence, int(needs_clarification), latency_ms, request_id),
            )
            connection.commit()
        finally:
            connection.close()

    def record_supervisor_route(self, request_id: str, route: str) -> None:
        if not request_id:
            return
        connection = self._connect()
        try:
            connection.execute("UPDATE requests SET supervisor_route = ? WHERE request_id = ?", (route, request_id))
            connection.commit()
        finally:
            connection.close()

    def mark_clarification_resolved(self, clarification_request_id: str) -> None:
        if not clarification_request_id:
            return
        connection = self._connect()
        try:
            connection.execute(
                "UPDATE requests SET clarification_resolved = 1 WHERE request_id = ? AND needs_clarification = 1",
                (clarification_request_id,),
            )
            connection.commit()
        finally:
            connection.close()

    def record_agent_event(
        self,
        request_id: str,
        agent: str,
        status: str,
        detail: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not request_id:
            return
        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT INTO agent_events (request_id, agent, status, detail, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (request_id, agent, status, detail, json.dumps(metadata or {}, ensure_ascii=False), _utc_now()),
            )
            connection.commit()
        finally:
            connection.close()

    def record_llm_call(
        self,
        request_id: str,
        agent: str,
        model: str,
        prompt_chars: int,
        latency_ms: float,
        usage: dict[str, int | None],
        status: str,
        error_message: str | None = None,
    ) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT INTO llm_calls (
                    request_id, agent, model, prompt_chars, input_tokens, output_tokens,
                    total_tokens, latency_ms, status, error_message, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    agent,
                    model,
                    prompt_chars,
                    usage.get("input_tokens"),
                    usage.get("output_tokens"),
                    usage.get("total_tokens"),
                    latency_ms,
                    status,
                    error_message,
                    _utc_now(),
                ),
            )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _rows(connection: sqlite3.Connection, query: str, values: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return [dict(row) for row in connection.execute(query, values).fetchall()]

    def get_request(self, request_id: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            request = connection.execute("SELECT * FROM requests WHERE request_id = ?", (request_id,)).fetchone()
            if request is None:
                return None
            return {
                "request": dict(request),
                "agent_events": self._rows(
                    connection,
                    "SELECT agent, status, detail, metadata_json, created_at FROM agent_events WHERE request_id = ? ORDER BY id",
                    (request_id,),
                ),
                "llm_calls": self._rows(
                    connection,
                    """
                    SELECT agent, model, prompt_chars, input_tokens, output_tokens, total_tokens,
                           latency_ms, status, error_message, created_at
                    FROM llm_calls WHERE request_id = ? ORDER BY id
                    """,
                    (request_id,),
                ),
            }
        finally:
            connection.close()

    def summary(self, window_hours: int = 24) -> dict[str, Any]:
        window_hours = max(1, min(window_hours, 24 * 30))
        since = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
        connection = self._connect()
        try:
            request_totals = dict(
                connection.execute(
                    """
                    SELECT COUNT(*) AS request_count,
                           SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count,
                           SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_count,
                           AVG(latency_ms) AS average_latency_ms,
                           AVG(retries) AS average_retries
                    FROM requests WHERE started_at >= ?
                    """,
                    (since,),
                ).fetchone()
            )
            token_totals = dict(
                connection.execute(
                    """
                    SELECT COUNT(*) AS llm_call_count, SUM(input_tokens) AS input_tokens,
                           SUM(output_tokens) AS output_tokens, SUM(total_tokens) AS total_tokens,
                           AVG(latency_ms) AS average_llm_latency_ms
                    FROM llm_calls WHERE created_at >= ?
                    """,
                    (since,),
                ).fetchone()
            )
            intent_totals = dict(
                connection.execute(
                    """
                    SELECT COUNT(intent) AS classified_count,
                           AVG(intent_confidence) AS average_confidence,
                           AVG(intent_latency_ms) AS average_latency_ms,
                           SUM(CASE WHEN needs_clarification = 1 THEN 1 ELSE 0 END) AS clarification_count,
                           SUM(CASE WHEN clarification_resolved = 1 THEN 1 ELSE 0 END) AS clarification_resolved_count
                    FROM requests WHERE started_at >= ?
                    """,
                    (since,),
                ).fetchone()
            )
            intent_tokens = dict(
                connection.execute(
                    """
                    SELECT SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens,
                           SUM(total_tokens) AS total_tokens, AVG(latency_ms) AS average_latency_ms
                    FROM llm_calls WHERE created_at >= ? AND agent = 'intent'
                    """,
                    (since,),
                ).fetchone()
            )
            routes = self._rows(
                connection,
                """
                SELECT supervisor_route AS route, COUNT(*) AS request_count
                FROM requests
                WHERE started_at >= ? AND supervisor_route IS NOT NULL
                GROUP BY supervisor_route ORDER BY request_count DESC, supervisor_route
                """,
                (since,),
            )
            clarification_followup_success_count = connection.execute(
                """
                SELECT COUNT(*) FROM requests
                WHERE started_at >= ? AND clarification_parent_request_id IS NOT NULL AND status = 'success'
                """,
                (since,),
            ).fetchone()[0]
            by_agent = self._rows(
                connection,
                """
                SELECT agent, COUNT(*) AS call_count, SUM(total_tokens) AS total_tokens,
                       AVG(latency_ms) AS average_latency_ms,
                       SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count
                FROM llm_calls WHERE created_at >= ? GROUP BY agent ORDER BY call_count DESC
                """,
                (since,),
            )
            agent_effectiveness = self._rows(
                connection,
                """
                SELECT agent, COUNT(*) AS event_count,
                       SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_count,
                       SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_count
                FROM agent_events WHERE created_at >= ? GROUP BY agent ORDER BY agent
                """,
                (since,),
            )
            request_buckets = self._rows(
                connection,
                """
                SELECT substr(started_at, 1, 13) AS bucket,
                       COUNT(*) AS request_count,
                       SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count
                FROM requests WHERE started_at >= ? GROUP BY bucket ORDER BY bucket
                """,
                (since,),
            )
            token_buckets = self._rows(
                connection,
                """
                SELECT substr(created_at, 1, 13) AS bucket,
                       SUM(total_tokens) AS total_tokens,
                       AVG(latency_ms) AS average_llm_latency_ms
                FROM llm_calls WHERE created_at >= ? GROUP BY bucket ORDER BY bucket
                """,
                (since,),
            )
            token_by_bucket = {row["bucket"]: row for row in token_buckets}
            timeline = []
            for request_bucket in request_buckets:
                token_bucket = token_by_bucket.get(request_bucket["bucket"], {})
                request_count = request_bucket["request_count"] or 0
                success_count = request_bucket["success_count"] or 0
                timeline.append(
                    {
                        "timestamp": f"{request_bucket['bucket']}:00:00Z",
                        "request_count": request_count,
                        "success_rate": round(success_count / request_count * 100, 2) if request_count else 0,
                        "total_tokens": token_bucket.get("total_tokens") or 0,
                        "average_llm_latency_ms": round(token_bucket.get("average_llm_latency_ms") or 0, 2),
                    }
                )
            for row in agent_effectiveness:
                event_count = row["event_count"] or 0
                row["success_rate"] = round((row["completed_count"] or 0) / event_count * 100, 2) if event_count else 0
            request_totals["success_rate"] = round(
                (request_totals.get("success_count") or 0) / request_totals["request_count"] * 100, 2
            ) if request_totals.get("request_count") else 0
            intent_totals["average_confidence"] = round(intent_totals.get("average_confidence") or 0, 3)
            intent_totals["average_latency_ms"] = round(intent_totals.get("average_latency_ms") or 0, 2)
            intent_totals["clarification_followup_success_count"] = clarification_followup_success_count or 0
            intent_tokens["average_latency_ms"] = round(intent_tokens.get("average_latency_ms") or 0, 2)
            return {
                "window_hours": window_hours,
                "requests": request_totals,
                "llm": token_totals,
                "by_agent": by_agent,
                "agent_effectiveness": agent_effectiveness,
                "intent": {"classification": intent_totals, "llm": intent_tokens, "routes": routes},
                "timeline": timeline,
            }
        finally:
            connection.close()


observability_store = ObservabilityStore()
