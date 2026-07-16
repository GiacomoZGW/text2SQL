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
                    tenant_id TEXT,
                    role TEXT,
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
                    is_evaluation INTEGER NOT NULL DEFAULT 0,
                    technical_success INTEGER,
                    sql_executable INTEGER,
                    result_correct INTEGER,
                    user_satisfied INTEGER,
                    evaluation_note TEXT,
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

                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT,
                    user_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    action TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT,
                    outcome TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS evaluation_runs (
                    run_id TEXT PRIMARY KEY,
                    suite_name TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL,
                    total_cases INTEGER NOT NULL DEFAULT 0,
                    passed_cases INTEGER NOT NULL DEFAULT 0,
                    details_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS evaluation_case_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    request_id TEXT,
                    status TEXT NOT NULL,
                    intent_expected TEXT,
                    intent_actual TEXT,
                    intent_correct INTEGER,
                    sql_executable INTEGER,
                    result_correct INTEGER,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_requests_started_at ON requests(started_at);
                CREATE INDEX IF NOT EXISTS idx_agent_events_request_id ON agent_events(request_id);
                CREATE INDEX IF NOT EXISTS idx_llm_calls_request_id ON llm_calls(request_id);
                CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs(created_at);
                CREATE INDEX IF NOT EXISTS idx_audit_logs_user_id ON audit_logs(user_id);
                CREATE INDEX IF NOT EXISTS idx_audit_logs_request_id ON audit_logs(request_id);
                CREATE INDEX IF NOT EXISTS idx_evaluation_case_results_run_id ON evaluation_case_results(run_id);
                """
            )
            self._ensure_request_columns(connection)
            connection.execute(
                """
                UPDATE requests
                SET technical_success = CASE WHEN status = 'success' THEN 1 ELSE 0 END
                WHERE technical_success IS NULL
                  AND status IN ('success', 'failed', 'paused', 'timed_out', 'queue_rejected')
                """
            )
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
            "tenant_id": "TEXT",
            "role": "TEXT",
            "conversation_id": "TEXT",
            "clarification_parent_request_id": "TEXT",
            "clarification_resolved": "INTEGER NOT NULL DEFAULT 0",
            "intent": "TEXT",
            "intent_confidence": "REAL",
            "needs_clarification": "INTEGER NOT NULL DEFAULT 0",
            "intent_latency_ms": "REAL",
            "supervisor_route": "TEXT",
            "is_evaluation": "INTEGER NOT NULL DEFAULT 0",
            "technical_success": "INTEGER",
            "sql_executable": "INTEGER",
            "result_correct": "INTEGER",
            "user_satisfied": "INTEGER",
            "evaluation_note": "TEXT",
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
        tenant_id: str | None = None,
        role: str | None = None,
        is_evaluation: bool = False,
    ) -> None:
        query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()
        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT INTO requests (
                    request_id, user_id, tenant_id, role, query_hash, query_chars, target_db, data_source_id,
                    conversation_id, clarification_parent_request_id, status, is_evaluation, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)
                """,
                (
                    request_id,
                    user_id,
                    tenant_id,
                    role,
                    query_hash,
                    len(query),
                    target_db,
                    data_source_id,
                    conversation_id,
                    clarification_parent_request_id,
                    int(is_evaluation),
                    _utc_now(),
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def record_audit_event(
        self,
        *,
        user_id: str,
        tenant_id: str,
        role: str,
        action: str,
        resource_type: str,
        outcome: str,
        resource_id: str | None = None,
        request_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Persist security-relevant access decisions without storing credentials or raw queries."""
        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT INTO audit_logs (
                    request_id, user_id, tenant_id, role, action, resource_type,
                    resource_id, outcome, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    user_id,
                    tenant_id,
                    role,
                    action,
                    resource_type,
                    resource_id,
                    outcome,
                    json.dumps(details or {}, ensure_ascii=False),
                    _utc_now(),
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def list_audit_events(self, limit: int = 100, user_id: str | None = None) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(500, int(limit)))
        connection = self._connect()
        try:
            if user_id:
                rows = connection.execute(
                    "SELECT * FROM audit_logs WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, bounded_limit)
                ).fetchall()
            else:
                rows = connection.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (bounded_limit,)).fetchall()
            return [
                {**dict(row), "details": json.loads(row["details_json"] or "{}")}
                for row in rows
            ]
        finally:
            connection.close()

    def complete_request(
        self,
        request_id: str,
        status: str,
        latency_ms: float,
        retries: int,
        error_message: str | None = None,
        technical_success: bool | None = None,
        sql_executable: bool | None = None,
    ) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """
                UPDATE requests
                SET status = ?, completed_at = ?, latency_ms = ?, retries = ?, error_message = ?,
                    technical_success = COALESCE(?, technical_success),
                    sql_executable = COALESCE(?, sql_executable)
                WHERE request_id = ?
                """,
                (
                    status,
                    _utc_now(),
                    latency_ms,
                    retries,
                    error_message,
                    None if technical_success is None else int(technical_success),
                    None if sql_executable is None else int(sql_executable),
                    request_id,
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def reconcile_stale_running_requests(self, max_age_seconds: int = 300) -> int:
        """Close requests left running by a process restart so they do not skew live metrics."""
        max_age_seconds = max(30, int(max_age_seconds))
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)).isoformat()
        connection = self._connect()
        try:
            cursor = connection.execute(
                """
                UPDATE requests
                SET status = 'aborted', completed_at = ?, error_message = ?, technical_success = 0
                WHERE status = 'running' AND started_at < ?
                """,
                (_utc_now(), "Server restarted before request completion", cutoff),
            )
            connection.commit()
            return cursor.rowcount
        finally:
            connection.close()

    def record_quality_feedback(
        self,
        request_id: str,
        *,
        result_correct: bool | None = None,
        user_satisfied: bool | None = None,
        note: str | None = None,
    ) -> None:
        if result_correct is None and user_satisfied is None and note is None:
            return
        connection = self._connect()
        try:
            connection.execute(
                """
                UPDATE requests
                SET result_correct = COALESCE(?, result_correct),
                    user_satisfied = COALESCE(?, user_satisfied),
                    evaluation_note = COALESCE(?, evaluation_note)
                WHERE request_id = ?
                """,
                (
                    None if result_correct is None else int(result_correct),
                    None if user_satisfied is None else int(user_satisfied),
                    note[:1000] if note else None,
                    request_id,
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def start_evaluation_run(self, run_id: str, suite_name: str, total_cases: int) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT INTO evaluation_runs (run_id, suite_name, started_at, status, total_cases)
                VALUES (?, ?, ?, 'running', ?)
                """,
                (run_id, suite_name, _utc_now(), total_cases),
            )
            connection.commit()
        finally:
            connection.close()

    def record_evaluation_case(
        self,
        run_id: str,
        case_id: str,
        status: str,
        *,
        request_id: str | None = None,
        intent_expected: str | None = None,
        intent_actual: str | None = None,
        intent_correct: bool | None = None,
        sql_executable: bool | None = None,
        result_correct: bool | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT INTO evaluation_case_results (
                    run_id, case_id, request_id, status, intent_expected, intent_actual,
                    intent_correct, sql_executable, result_correct, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    case_id,
                    request_id,
                    status,
                    intent_expected,
                    intent_actual,
                    None if intent_correct is None else int(intent_correct),
                    None if sql_executable is None else int(sql_executable),
                    None if result_correct is None else int(result_correct),
                    json.dumps(details or {}, ensure_ascii=False),
                    _utc_now(),
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def complete_evaluation_run(self, run_id: str, passed_cases: int, details: dict[str, Any]) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """
                UPDATE evaluation_runs
                SET completed_at = ?, status = 'completed', passed_cases = ?, details_json = ?
                WHERE run_id = ?
                """,
                (_utc_now(), passed_cases, json.dumps(details, ensure_ascii=False), run_id),
            )
            connection.commit()
        finally:
            connection.close()

    def get_evaluation_run(self, run_id: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            run = connection.execute("SELECT * FROM evaluation_runs WHERE run_id = ?", (run_id,)).fetchone()
            if run is None:
                return None
            cases = self._rows(
                connection,
                "SELECT * FROM evaluation_case_results WHERE run_id = ? ORDER BY id",
                (run_id,),
            )
            return {
                "run": {**dict(run), "details": json.loads(run["details_json"] or "{}")},
                "cases": [{**case, "details": json.loads(case["details_json"] or "{}") } for case in cases],
            }
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
                           SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running_count,
                           SUM(CASE WHEN status = 'aborted' THEN 1 ELSE 0 END) AS aborted_count,
                           SUM(CASE WHEN status IN ('success', 'failed', 'paused', 'timed_out', 'queue_rejected') THEN 1 ELSE 0 END) AS terminal_count,
                           SUM(CASE WHEN technical_success = 1 THEN 1 ELSE 0 END) AS technical_success_count,
                           SUM(CASE WHEN sql_executable IS NOT NULL THEN 1 ELSE 0 END) AS sql_attempt_count,
                           SUM(CASE WHEN sql_executable = 1 THEN 1 ELSE 0 END) AS sql_executable_count,
                           SUM(CASE WHEN result_correct IS NOT NULL THEN 1 ELSE 0 END) AS result_evaluated_count,
                           SUM(CASE WHEN result_correct = 1 THEN 1 ELSE 0 END) AS result_correct_count,
                           SUM(CASE WHEN user_satisfied IS NOT NULL THEN 1 ELSE 0 END) AS satisfaction_response_count,
                           SUM(CASE WHEN user_satisfied = 1 THEN 1 ELSE 0 END) AS satisfied_count,
                           AVG(latency_ms) AS average_latency_ms,
                           AVG(retries) AS average_retries
                    FROM requests WHERE started_at >= ? AND is_evaluation = 0
                    """,
                    (since,),
                ).fetchone()
            )
            token_totals = dict(
                connection.execute(
                    """
                    SELECT COUNT(*) AS llm_call_count, SUM(llm_calls.input_tokens) AS input_tokens,
                           SUM(llm_calls.output_tokens) AS output_tokens, SUM(llm_calls.total_tokens) AS total_tokens,
                           AVG(llm_calls.latency_ms) AS average_llm_latency_ms
                    FROM llm_calls JOIN requests ON requests.request_id = llm_calls.request_id
                    WHERE llm_calls.created_at >= ? AND requests.is_evaluation = 0
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
                    FROM requests WHERE started_at >= ? AND is_evaluation = 0
                    """,
                    (since,),
                ).fetchone()
            )
            intent_tokens = dict(
                connection.execute(
                    """
                    SELECT SUM(llm_calls.input_tokens) AS input_tokens, SUM(llm_calls.output_tokens) AS output_tokens,
                           SUM(llm_calls.total_tokens) AS total_tokens, AVG(llm_calls.latency_ms) AS average_latency_ms
                    FROM llm_calls JOIN requests ON requests.request_id = llm_calls.request_id
                    WHERE llm_calls.created_at >= ? AND llm_calls.agent = 'intent' AND requests.is_evaluation = 0
                    """,
                    (since,),
                ).fetchone()
            )
            routes = self._rows(
                connection,
                """
                SELECT supervisor_route AS route, COUNT(*) AS request_count
                FROM requests
                WHERE started_at >= ? AND is_evaluation = 0 AND supervisor_route IS NOT NULL
                GROUP BY supervisor_route ORDER BY request_count DESC, supervisor_route
                """,
                (since,),
            )
            clarification_followup_success_count = connection.execute(
                """
                SELECT COUNT(*) FROM requests
                WHERE started_at >= ? AND is_evaluation = 0 AND clarification_parent_request_id IS NOT NULL AND status = 'success'
                """,
                (since,),
            ).fetchone()[0]
            by_agent = self._rows(
                connection,
                """
                SELECT llm_calls.agent, COUNT(*) AS call_count, SUM(llm_calls.total_tokens) AS total_tokens,
                       AVG(llm_calls.latency_ms) AS average_latency_ms,
                       SUM(CASE WHEN llm_calls.status = 'success' THEN 1 ELSE 0 END) AS success_count
                FROM llm_calls JOIN requests ON requests.request_id = llm_calls.request_id
                WHERE llm_calls.created_at >= ? AND requests.is_evaluation = 0
                GROUP BY llm_calls.agent ORDER BY call_count DESC
                """,
                (since,),
            )
            agent_effectiveness = self._rows(
                connection,
                """
                SELECT agent, COUNT(*) AS event_count,
                       SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_count,
                       SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_count
                FROM agent_events
                WHERE created_at >= ? AND request_id IN (
                    SELECT request_id FROM requests WHERE is_evaluation = 0
                )
                GROUP BY agent ORDER BY agent
                """,
                (since,),
            )
            request_buckets = self._rows(
                connection,
                """
                SELECT substr(started_at, 1, 13) AS bucket,
                       COUNT(*) AS request_count,
                       SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running_count,
                       SUM(CASE WHEN status = 'aborted' THEN 1 ELSE 0 END) AS aborted_count,
                       SUM(CASE WHEN status IN ('success', 'failed', 'paused', 'timed_out', 'queue_rejected') THEN 1 ELSE 0 END) AS terminal_count,
                       SUM(CASE WHEN technical_success = 1 THEN 1 ELSE 0 END) AS technical_success_count
                FROM requests WHERE started_at >= ? AND is_evaluation = 0 GROUP BY bucket ORDER BY bucket
                """,
                (since,),
            )
            token_buckets = self._rows(
                connection,
                """
                SELECT substr(llm_calls.created_at, 1, 13) AS bucket,
                       SUM(llm_calls.total_tokens) AS total_tokens,
                       AVG(llm_calls.latency_ms) AS average_llm_latency_ms
                FROM llm_calls JOIN requests ON requests.request_id = llm_calls.request_id
                WHERE llm_calls.created_at >= ? AND requests.is_evaluation = 0
                GROUP BY bucket ORDER BY bucket
                """,
                (since,),
            )
            token_by_bucket = {row["bucket"]: row for row in token_buckets}
            timeline = []
            for request_bucket in request_buckets:
                token_bucket = token_by_bucket.get(request_bucket["bucket"], {})
                request_count = request_bucket["request_count"] or 0
                terminal_count = request_bucket["terminal_count"] or 0
                technical_success_count = request_bucket["technical_success_count"] or 0
                timeline.append(
                    {
                        "timestamp": f"{request_bucket['bucket']}:00:00Z",
                        "request_count": request_count,
                        "running_count": request_bucket["running_count"] or 0,
                        "aborted_count": request_bucket["aborted_count"] or 0,
                        "technical_success_rate": round(technical_success_count / terminal_count * 100, 2)
                        if terminal_count
                        else 0,
                        "total_tokens": token_bucket.get("total_tokens") or 0,
                        "average_llm_latency_ms": round(token_bucket.get("average_llm_latency_ms") or 0, 2),
                    }
                )
            for row in agent_effectiveness:
                terminal_event_count = (row["completed_count"] or 0) + (row["failed_count"] or 0)
                row["completion_rate"] = round((row["completed_count"] or 0) / terminal_event_count * 100, 2) if terminal_event_count else 0
            terminal_count = request_totals.get("terminal_count") or 0
            sql_attempt_count = request_totals.get("sql_attempt_count") or 0
            result_evaluated_count = request_totals.get("result_evaluated_count") or 0
            satisfaction_response_count = request_totals.get("satisfaction_response_count") or 0
            request_totals["technical_success_rate"] = round(
                (request_totals.get("technical_success_count") or 0) / terminal_count * 100, 2
            ) if terminal_count else 0
            request_totals["sql_executable_rate"] = round(
                (request_totals.get("sql_executable_count") or 0) / sql_attempt_count * 100, 2
            ) if sql_attempt_count else 0
            request_totals["result_correct_rate"] = round(
                (request_totals.get("result_correct_count") or 0) / result_evaluated_count * 100, 2
            ) if result_evaluated_count else 0
            request_totals["satisfaction_rate"] = round(
                (request_totals.get("satisfied_count") or 0) / satisfaction_response_count * 100, 2
            ) if satisfaction_response_count else 0
            request_totals["success_rate"] = request_totals["technical_success_rate"]
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
