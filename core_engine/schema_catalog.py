"""Build a versioned, metadata-rich schema catalog from live database schema."""

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CATALOG_CONFIG_PATH = PROJECT_ROOT / "config" / "schema_catalog.yaml"


def _split_columns(column_text: str) -> list[dict[str, str]]:
    columns: list[dict[str, str]] = []
    for item in column_text.split(","):
        value = item.strip()
        if not value:
            continue
        if " (" in value and value.endswith(")"):
            name, column_type = value[:-1].split(" (", 1)
        else:
            name, _, column_type = value.partition(" ")
        columns.append({"name": name.strip(), "type": column_type.strip() or "TEXT"})
    return columns


def _live_tables(schema_context: str) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    for section in schema_context.split("\n\n"):
        lines = [line.strip() for line in section.splitlines() if line.strip()]
        if not lines or not lines[0].startswith("Table: "):
            continue
        table = {"name": lines[0].removeprefix("Table: ").strip(), "columns": [], "foreign_keys": []}
        for line in lines[1:]:
            if line.startswith("Columns: "):
                table["columns"] = _split_columns(line.removeprefix("Columns: "))
            elif line.startswith("Foreign Keys: "):
                for relation in line.removeprefix("Foreign Keys: ").split(";"):
                    relation = relation.strip()
                    if "->" in relation:
                        table["foreign_keys"].append(relation)
        tables.append(table)
    return tables


def _load_config() -> dict[str, Any]:
    if not CATALOG_CONFIG_PATH.exists():
        return {}
    with CATALOG_CONFIG_PATH.open("r", encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file) or {}
    return config if isinstance(config, dict) else {}


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def build_schema_catalog(schema_context: str, data_source_id: str, db_type: str) -> dict[str, Any]:
    """Merge live metadata with versioned business metadata from configuration."""
    config = _load_config()
    source_configs = _mapping(config.get("data_sources"))
    source_config = _mapping(source_configs.get(data_source_id))
    table_overrides = _mapping(source_config.get("tables"))
    catalog_tables: list[dict[str, Any]] = []

    for live_table in _live_tables(schema_context):
        table_name = str(live_table["name"])
        override = _mapping(table_overrides.get(table_name))
        column_overrides = _mapping(override.get("columns"))
        columns: list[dict[str, Any]] = []
        for live_column in live_table["columns"]:
            column_override = _mapping(column_overrides.get(live_column["name"]))
            columns.append(
                {
                    **live_column,
                    "description": str(column_override.get("description", "")),
                    "aliases": _list_of_strings(column_override.get("aliases")),
                    "enum_values": _list_of_strings(column_override.get("enum_values")),
                }
            )
        catalog_tables.append(
            {
                "name": table_name,
                "description": str(override.get("description", "")),
                "aliases": _list_of_strings(override.get("aliases")),
                "columns": columns,
                "foreign_keys": live_table["foreign_keys"],
                "permissions": _mapping(override.get("permissions")),
            }
        )

    relationships = [item for item in source_config.get("relationships", []) if isinstance(item, dict)]
    for table in catalog_tables:
        for relation in table["foreign_keys"]:
            left, _, right = relation.partition("->")
            if left and right:
                relationships.append({"from": left.strip(), "to": right.strip(), "kind": "foreign_key"})

    catalog = {
        "data_source_id": data_source_id,
        "db_type": db_type,
        "tables": catalog_tables,
        "relationships": relationships,
        "metrics": _mapping(source_config.get("metrics")),
        "permissions": _mapping(source_config.get("permissions")),
        "configured_version": str(source_config.get("version", config.get("version", "1"))),
    }
    version_payload = json.dumps(catalog, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    catalog["schema_version"] = hashlib.sha256(version_payload.encode("utf-8")).hexdigest()[:12]
    return catalog


def render_schema_catalog(catalog: dict[str, Any]) -> str:
    """Render catalog fields into retrieval-friendly, table-scoped documents."""
    metric_lines = [
        f"{name}: {details.get('definition', '')}"
        for name, details in _mapping(catalog.get("metrics")).items()
        if isinstance(details, dict)
    ]
    sections: list[str] = []
    for table in catalog.get("tables", []):
        lines = [f"Table: {table['name']}"]
        if table.get("description"):
            lines.append(f"Description: {table['description']}")
        if table.get("aliases"):
            lines.append("Aliases: " + ", ".join(table["aliases"]))
        lines.append("Columns: " + ", ".join(f"{column['name']} ({column['type']})" for column in table["columns"]))
        for column in table["columns"]:
            metadata: list[str] = []
            if column.get("description"):
                metadata.append(f"description={column['description']}")
            if column.get("aliases"):
                metadata.append("aliases=" + ", ".join(column["aliases"]))
            if column.get("enum_values"):
                metadata.append("enum=" + ", ".join(column["enum_values"]))
            if metadata:
                lines.append(f"Column Metadata: {column['name']} | " + " | ".join(metadata))
        if table.get("foreign_keys"):
            lines.append("Foreign Keys: " + "; ".join(table["foreign_keys"]))
        if metric_lines:
            lines.append("Metrics: " + "; ".join(metric_lines))
        permissions = _mapping(table.get("permissions"))
        if permissions:
            lines.append("Permissions: " + json.dumps(permissions, ensure_ascii=False, sort_keys=True))
        lines.append(f"Schema Version: {catalog['schema_version']}")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)
