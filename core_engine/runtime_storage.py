"""Database adapter for runtime state that prefers PostgreSQL when configured.

The application keeps a SQLite fallback for local demos and tests. Production
deployments can set ``RUNTIME_DATABASE_URL`` (or a store-specific URL) to move
state to PostgreSQL without changing the stores' parameterized SQL calls.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable


def qmark_to_pyformat(statement: str) -> str:
    """Convert DB-API qmark placeholders without touching quoted question marks."""
    result: list[str] = []
    in_single_quote = False
    index = 0
    while index < len(statement):
        character = statement[index]
        if character == "'":
            result.append(character)
            if in_single_quote and index + 1 < len(statement) and statement[index + 1] == "'":
                result.append("'")
                index += 2
                continue
            in_single_quote = not in_single_quote
        elif character == "?" and not in_single_quote:
            result.append("%s")
        else:
            result.append(character)
        index += 1
    return "".join(result)


class PostgreSQLConnection:
    """Small sqlite-like wrapper around psycopg's dictionary-row connection."""

    def __init__(self, database_url: str):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - depends on deployment extras
            raise RuntimeError(
                "PostgreSQL runtime storage requires psycopg. Install requirements.txt again."
            ) from exc
        self._connection = psycopg.connect(database_url, connect_timeout=10, row_factory=dict_row)

    @staticmethod
    def _prepare(statement: str) -> str:
        normalized = statement.strip()
        normalized = normalized.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
        if normalized.upper().startswith("INSERT OR IGNORE INTO"):
            normalized = "INSERT INTO" + normalized[len("INSERT OR IGNORE INTO") :]
            if "ON CONFLICT" not in normalized.upper():
                normalized = normalized.rstrip(";") + " ON CONFLICT DO NOTHING"
        return qmark_to_pyformat(normalized)

    def execute(self, statement: str, parameters: Iterable[Any] | None = None):
        cursor = self._connection.cursor()
        cursor.execute(self._prepare(statement), tuple(parameters or ()))
        return cursor

    def executescript(self, script: str) -> None:
        # Store initialization scripts intentionally contain only standalone DDL.
        for statement in script.split(";"):
            if statement.strip():
                self.execute(statement)

    def commit(self) -> None:
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()


class RuntimeStorage:
    """Create SQLite or PostgreSQL connections for a runtime-state store."""

    def __init__(self, sqlite_path: Path, database_url: str | None = None):
        self.sqlite_path = Path(sqlite_path)
        self.database_url = (database_url or "").strip() or None
        if self.database_url and not self.database_url.startswith(("postgres://", "postgresql://")):
            raise ValueError("Runtime database URL must use postgresql:// or postgres://")

    @property
    def is_postgres(self) -> bool:
        return self.database_url is not None

    def connect(self) -> sqlite3.Connection | PostgreSQLConnection:
        if self.database_url:
            return PostgreSQLConnection(self.database_url)
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.sqlite_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection
