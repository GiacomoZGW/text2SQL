"""Copy existing local SQLite runtime state into the configured PostgreSQL database.

Run after setting RUNTIME_DATABASE_URL. The script is idempotent and leaves the
source SQLite files untouched:

    RUNTIME_DATABASE_URL=postgresql://... python scripts/migrate_runtime_state.py
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core_engine.runtime_storage import RuntimeStorage
from memory.store import DEFAULT_DB_PATH as DEFAULT_MEMORY_DB_PATH
from observability.store import DEFAULT_DB_PATH as DEFAULT_OBSERVABILITY_DB_PATH


TABLES = {
    "memory": ["user_preferences", "conversation_turns", "conversation_checkpoints", "semantic_memories"],
    "observability": [
        "requests",
        "agent_events",
        "llm_calls",
        "audit_logs",
        "evaluation_runs",
        "evaluation_case_results",
    ],
}
ID_TABLES = {"conversation_turns", "agent_events", "llm_calls", "audit_logs", "evaluation_case_results"}


def _copy_table(source: sqlite3.Connection, target, table: str) -> int:
    exists = source.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
    if exists is None:
        return 0
    columns = [row["name"] for row in source.execute(f"PRAGMA table_info({table})").fetchall()]
    rows = source.execute(f"SELECT * FROM {table}").fetchall()
    if not rows:
        return 0
    quoted_columns = ", ".join(columns)
    placeholders = ", ".join("?" for _ in columns)
    statement = f"INSERT INTO {table} ({quoted_columns}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
    for row in rows:
        target.execute(statement, tuple(row[column] for column in columns))
    if table in ID_TABLES:
        target.execute(
            "SELECT setval(pg_get_serial_sequence(?, 'id'), COALESCE((SELECT MAX(id) FROM "
            + table
            + "), 1), true)",
            (table,),
        )
    return len(rows)


def _copy_database(path: Path, target, tables: list[str]) -> dict[str, int]:
    if not path.exists():
        return {table: 0 for table in tables}
    source = sqlite3.connect(path)
    source.row_factory = sqlite3.Row
    try:
        return {table: _copy_table(source, target, table) for table in tables}
    finally:
        source.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate local runtime SQLite state to PostgreSQL.")
    parser.add_argument("--memory-db", type=Path, default=DEFAULT_MEMORY_DB_PATH)
    parser.add_argument("--observability-db", type=Path, default=DEFAULT_OBSERVABILITY_DB_PATH)
    args = parser.parse_args()

    database_url = os.getenv("RUNTIME_DATABASE_URL")
    if not database_url:
        raise SystemExit("Set RUNTIME_DATABASE_URL to a PostgreSQL connection URL before migrating.")
    storage = RuntimeStorage(PROJECT_ROOT / "runtime-state-placeholder.db", database_url)
    target = storage.connect()
    try:
        # Initializing stores creates the PostgreSQL schema before the copy.
        from memory.semantic_store import SemanticMemoryStore
        from memory.store import MemoryStore
        from observability.store import ObservabilityStore

        MemoryStore()
        SemanticMemoryStore()
        ObservabilityStore()
        result = {
            "memory": _copy_database(args.memory_db, target, TABLES["memory"]),
            "observability": _copy_database(args.observability_db, target, TABLES["observability"]),
        }
        target.commit()
    finally:
        target.close()

    for group, counts in result.items():
        copied = sum(counts.values())
        print(f"{group}: copied {copied} row(s) - " + ", ".join(f"{name}={count}" for name, count in counts.items()))


if __name__ == "__main__":
    main()
