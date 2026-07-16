"""Read-only SQLAlchemy connectors for individually managed data sources."""

import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from threading import Lock
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError


class ConnectorError(RuntimeError):
    """Raised when a configured database connector cannot safely serve a request."""


class ConnectorConfigurationError(ConnectorError):
    """Raised when the connector's environment-backed configuration is incomplete."""


READ_ONLY_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|PRAGMA|VACUUM|REPLACE|MERGE|GRANT|REVOKE|CALL|COPY)\b",
    flags=re.IGNORECASE,
)


def _without_string_literals(sql: str) -> str:
    return re.sub(r"'(?:''|[^'])*'", "''", sql)


def assert_read_only_sql(sql: str) -> str:
    """Reject non-query SQL again at the connection boundary."""
    normalized = re.sub(r"\s+", " ", sql.strip()).rstrip(";").strip()
    if not normalized or not re.match(r"^(SELECT|WITH)\b", normalized, flags=re.IGNORECASE):
        raise ConnectorError("Only SELECT or WITH queries are allowed for direct connectors")
    if ";" in normalized or "--" in normalized or "/*" in normalized or "*/" in normalized:
        raise ConnectorError("Only one comment-free query is allowed for direct connectors")
    if READ_ONLY_FORBIDDEN.search(_without_string_literals(normalized)):
        raise ConnectorError("The query contains a forbidden write or administration operation")
    return normalized


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


@dataclass(frozen=True)
class ConnectorSettings:
    data_source_id: str
    engine: str
    uri_env: str
    schema: str | None
    pool_size: int
    max_overflow: int
    pool_recycle_seconds: int
    connect_timeout_seconds: int
    query_timeout_seconds: int
    max_rows: int
    tls: dict[str, Any]

    @classmethod
    def from_source(cls, source: dict[str, Any]) -> "ConnectorSettings":
        config = source.get("connector", {})
        if not isinstance(config, dict):
            raise ConnectorConfigurationError("connector must be a mapping")
        engine = str(source.get("engine", "")).lower()
        uri_env = str(source.get("connection_secret_ref", "")).strip()
        if engine not in {"mysql", "postgres", "clickhouse", "sqlite"}:
            raise ConnectorConfigurationError(f"Unsupported direct connector engine: {engine}")
        if not uri_env:
            raise ConnectorConfigurationError("connection_secret_ref must name a URI environment variable")
        return cls(
            data_source_id=str(source.get("id", "")),
            engine=engine,
            uri_env=uri_env,
            schema=str(config.get("schema") or "").strip() or None,
            pool_size=max(1, int(config.get("pool_size", 3))),
            max_overflow=max(0, int(config.get("max_overflow", 2))),
            pool_recycle_seconds=max(30, int(config.get("pool_recycle_seconds", 1800))),
            connect_timeout_seconds=max(1, int(config.get("connect_timeout_seconds", 5))),
            query_timeout_seconds=max(1, int(config.get("query_timeout_seconds", 30))),
            max_rows=max(1, min(1000, int(config.get("max_rows", 50)))),
            tls=config.get("tls", {}) if isinstance(config.get("tls", {}), dict) else {},
        )


class SqlAlchemyConnector:
    """A pooled, read-only connection to one configured MySQL/Postgres/ClickHouse source."""

    def __init__(self, source: dict[str, Any]):
        self.settings = ConnectorSettings.from_source(source)
        self._engine: Engine | None = None
        self._uri: str | None = None
        self._lock = Lock()

    def _connection_uri(self) -> str:
        uri = os.getenv(self.settings.uri_env, "").strip()
        if not uri:
            raise ConnectorConfigurationError(
                f"Data source {self.settings.data_source_id} is missing {self.settings.uri_env}"
            )
        return uri

    def _connect_args(self) -> dict[str, Any]:
        timeout = self.settings.connect_timeout_seconds
        tls = self.settings.tls
        if self.settings.engine == "mysql":
            args: dict[str, Any] = {
                "connect_timeout": timeout,
                "read_timeout": self.settings.query_timeout_seconds,
                "write_timeout": self.settings.query_timeout_seconds,
            }
            if tls.get("enabled") is True:
                ssl: dict[str, str] = {}
                for field, env_name in (("ca", "ca_cert_env"), ("cert", "client_cert_env"), ("key", "client_key_env")):
                    value = os.getenv(str(tls.get(env_name, "")), "").strip()
                    if value:
                        ssl[field] = value
                args["ssl"] = ssl
            return args
        if self.settings.engine == "postgres":
            args = {"connect_timeout": timeout}
            if tls.get("enabled") is True:
                args["sslmode"] = str(tls.get("mode", "require"))
                for field, env_name in (
                    ("sslrootcert", "ca_cert_env"),
                    ("sslcert", "client_cert_env"),
                    ("sslkey", "client_key_env"),
                ):
                    value = os.getenv(str(tls.get(env_name, "")), "").strip()
                    if value:
                        args[field] = value
            return args
        if self.settings.engine == "sqlite":
            return {"timeout": timeout}
        return {}

    def engine(self) -> Engine:
        uri = self._connection_uri()
        with self._lock:
            if self._engine is not None and self._uri == uri:
                return self._engine
            if self._engine is not None:
                self._engine.dispose()
            self._uri = uri
            self._engine = create_engine(
                uri,
                pool_pre_ping=True,
                pool_size=self.settings.pool_size,
                max_overflow=self.settings.max_overflow,
                pool_recycle=self.settings.pool_recycle_seconds,
                connect_args=self._connect_args(),
            )
            return self._engine

    def _configure_read_only_session(self, connection: Any) -> None:
        timeout_ms = self.settings.query_timeout_seconds * 1000
        if self.settings.engine == "postgres":
            connection.execute(text("SET TRANSACTION READ ONLY"))
            connection.execute(text("SET LOCAL statement_timeout = :timeout_ms"), {"timeout_ms": timeout_ms})
        elif self.settings.engine == "mysql":
            connection.execute(text("SET SESSION TRANSACTION READ ONLY"))
            try:
                connection.execute(text("SET SESSION MAX_EXECUTION_TIME = :timeout_ms"), {"timeout_ms": timeout_ms})
            except SQLAlchemyError:
                # Older MySQL variants may not expose MAX_EXECUTION_TIME; the client timeout remains active.
                pass
        elif self.settings.engine == "clickhouse":
            try:
                connection.execute(text("SET readonly = 1"))
                connection.execute(text("SET max_execution_time = :timeout_seconds"), {"timeout_seconds": self.settings.query_timeout_seconds})
            except SQLAlchemyError:
                # ClickHouse can also enforce these values through URI settings and the account policy.
                pass

    def _connection(self) -> Any:
        return self.engine().connect()

    def health_check(self) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            with self._connection() as connection:
                connection.execute(text("SELECT 1")).fetchone()
            return {
                "healthy": True,
                "engine": self.settings.engine,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            }
        except (SQLAlchemyError, ConnectorError) as exc:
            return {"healthy": False, "engine": self.settings.engine, "error": str(exc)}

    def get_schema(self) -> str:
        try:
            inspector = inspect(self.engine())
            table_names = inspector.get_table_names(schema=self.settings.schema)
            schema_lines: list[str] = []
            for table_name in sorted(table_names):
                columns = inspector.get_columns(table_name, schema=self.settings.schema)
                column_text = ", ".join(f"{column['name']} ({column['type']})" for column in columns)
                qualified_name = f"{self.settings.schema}.{table_name}" if self.settings.schema else table_name
                schema_lines.append(f"Table: {qualified_name}\nColumns: {column_text}")
            return "\n\n".join(schema_lines)
        except SQLAlchemyError as exc:
            raise ConnectorError(f"Unable to read schema for {self.settings.data_source_id}: {exc}") from exc

    def explain(self, sql: str) -> None:
        safe_sql = assert_read_only_sql(sql)
        try:
            with self._connection() as connection:
                self._configure_read_only_session(connection)
                connection.execute(text(f"EXPLAIN {safe_sql}")).fetchmany(1)
        except SQLAlchemyError as exc:
            raise ConnectorError(f"Direct SQL preflight failed: {exc}") from exc

    def execute(self, sql: str) -> list[dict[str, Any]]:
        safe_sql = assert_read_only_sql(sql)
        try:
            with self._connection() as connection:
                self._configure_read_only_session(connection)
                result = connection.execute(text(safe_sql))
                return [
                    {column: _json_value(value) for column, value in row.items()}
                    for row in result.mappings().fetchmany(self.settings.max_rows)
                ]
        except SQLAlchemyError as exc:
            raise ConnectorError(f"Direct SQL execution failed: {exc}") from exc

    def dispose(self) -> None:
        with self._lock:
            if self._engine is not None:
                self._engine.dispose()
                self._engine = None
                self._uri = None


class DirectConnectorRegistry:
    """Keep one pool per configured source without exposing connection details to callers."""

    def __init__(self):
        self._connectors: dict[str, SqlAlchemyConnector] = {}
        self._lock = Lock()

    def connector_for(self, source: dict[str, Any]) -> SqlAlchemyConnector:
        source_id = str(source.get("id", ""))
        if not source_id:
            raise ConnectorConfigurationError("Data source id is required")
        with self._lock:
            connector = self._connectors.get(source_id)
            if connector is None:
                connector = SqlAlchemyConnector(source)
                self._connectors[source_id] = connector
            return connector

    def dispose_all(self) -> None:
        with self._lock:
            for connector in self._connectors.values():
                connector.dispose()
            self._connectors.clear()


direct_connector_registry = DirectConnectorRegistry()
