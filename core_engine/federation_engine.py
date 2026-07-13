"""DuckDB federation over real database URIs with a local demo fallback."""

import json
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import yaml


class FederationError(RuntimeError):
    """Raised when a configured federated source cannot be queried safely."""


class DuckDBFederationEngine:
    """Attach MySQL, PostgreSQL or ClickHouse sources behind stable catalog aliases."""

    EXTENSIONS = {
        "mysql": "mysql",
        "postgres": "postgres",
        "clickhouse": "clickhouse",
        "sqlite": "sqlite",
    }

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.config_path = project_root / "config" / "federation.yaml"

    def _load_config(self) -> dict[str, Any]:
        if not self.config_path.exists():
            raise FederationError(f"联邦配置不存在: {self.config_path}")
        with self.config_path.open("r", encoding="utf-8") as config_file:
            config = yaml.safe_load(config_file) or {}
        if config.get("engine") != "duckdb":
            raise FederationError("当前只支持 engine: duckdb")
        if not config.get("sources"):
            raise FederationError("联邦配置未定义任何数据源")
        return config

    @staticmethod
    def _quote_identifier(value: str) -> str:
        return '"' + value.replace('"', '""') + '"'

    @staticmethod
    def _quote_literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    def _load_extension(self, connection: duckdb.DuckDBPyConnection, source_type: str) -> None:
        extension = self.EXTENSIONS.get(source_type)
        if not extension:
            raise FederationError(f"不支持的数据源类型: {source_type}")
        try:
            connection.execute(f"LOAD {extension}")
        except duckdb.Error:
            try:
                connection.execute(f"INSTALL {extension}")
                connection.execute(f"LOAD {extension}")
            except duckdb.Error as exc:
                raise FederationError(f"无法加载 DuckDB {extension} 扩展: {exc}") from exc

    def _attach_source(
        self,
        connection: duckdb.DuckDBPyConnection,
        alias: str,
        source_config: dict[str, Any],
    ) -> dict[str, str]:
        source_type = str(source_config.get("type", "")).lower()
        uri_env = str(source_config.get("uri_env", ""))
        remote_uri = os.getenv(uri_env) if uri_env else None
        if remote_uri:
            self._load_extension(connection, source_type)
            attach_uri = remote_uri
            active_type = source_type
            mode = "remote"
        else:
            demo_file = source_config.get("demo_sqlite")
            if not demo_file:
                raise FederationError(f"数据源 {alias} 未配置 {uri_env}，且没有本地演示库")
            demo_path = self.project_root / str(demo_file)
            if not demo_path.exists():
                raise FederationError(f"数据源 {alias} 的本地演示库不存在: {demo_path}")
            self._load_extension(connection, "sqlite")
            attach_uri = str(demo_path)
            active_type = "sqlite"
            mode = "demo"

        connection.execute(
            f"ATTACH {self._quote_literal(attach_uri)} AS {self._quote_identifier(alias)} "
            f"(TYPE {active_type.upper()}, READ_ONLY)"
        )
        return {"alias": alias, "mode": mode, "type": source_type}

    def connect(self) -> tuple[duckdb.DuckDBPyConnection, list[dict[str, str]]]:
        config = self._load_config()
        connection = duckdb.connect(":memory:")
        active_sources: list[dict[str, str]] = []
        try:
            for alias, source_config in config["sources"].items():
                active_sources.append(self._attach_source(connection, alias, source_config))
            return connection, active_sources
        except Exception:
            connection.close()
            raise

    def describe_sources(self) -> list[dict[str, str]]:
        connection, active_sources = self.connect()
        connection.close()
        return active_sources

    def get_schema(self) -> str:
        connection, active_sources = self.connect()
        try:
            source_modes = {source["alias"]: source["mode"] for source in active_sources}
            schema_lines: list[str] = []
            for source in active_sources:
                alias = source["alias"]
                table_rows = connection.execute(
                    "SELECT table_name FROM duckdb_tables() WHERE database_name = ? ORDER BY table_name",
                    [alias],
                ).fetchall()
                for (table_name,) in table_rows:
                    columns = connection.execute(
                        f"DESCRIBE {self._quote_identifier(alias)}.{self._quote_identifier(str(table_name))}"
                    ).fetchall()
                    column_text = ", ".join(f"{column[0]} ({column[1]})" for column in columns)
                    schema_lines.append(
                        f"Table: {alias}.{table_name}\n"
                        f"Source: {source['type']} ({source_modes[alias]})\n"
                        f"Columns: {column_text}"
                    )
            return "\n\n".join(schema_lines)
        except duckdb.Error as exc:
            raise FederationError(f"读取 DuckDB 联邦 Schema 失败: {exc}") from exc
        finally:
            connection.close()

    def explain(self, sql: str) -> None:
        connection, _ = self.connect()
        try:
            connection.execute(f"EXPLAIN {sql}").fetchall()
        except duckdb.Error as exc:
            raise FederationError(f"DuckDB 联邦 SQL 预检失败: {exc}") from exc
        finally:
            connection.close()

    @staticmethod
    def _json_value(value: Any) -> Any:
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value)
        return value

    def execute(self, sql: str) -> list[dict[str, Any]]:
        connection, _ = self.connect()
        try:
            cursor = connection.execute(sql)
            columns = [column[0] for column in cursor.description]
            return [
                {column: self._json_value(value) for column, value in zip(columns, row)}
                for row in cursor.fetchall()
            ]
        except duckdb.Error as exc:
            raise FederationError(f"DuckDB 联邦 SQL 执行失败: {exc}") from exc
        finally:
            connection.close()


def create_federation_engine(project_root: Path) -> DuckDBFederationEngine:
    return DuckDBFederationEngine(project_root)
