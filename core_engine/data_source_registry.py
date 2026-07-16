"""Configured, allow-listed data sources exposed to the Data Agent."""

from pathlib import Path
from typing import Any

import yaml


class DataSourceError(ValueError):
    """Raised when an unknown or disabled data source is requested."""


class DataSourceRegistry:
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.config_path = project_root / "config" / "data_sources.yaml"

    def _load(self) -> list[dict[str, Any]]:
        if not self.config_path.exists():
            raise DataSourceError(f"Data-source configuration not found: {self.config_path}")
        with self.config_path.open("r", encoding="utf-8") as config_file:
            data = yaml.safe_load(config_file) or {}
        sources = data.get("data_sources", [])
        if not isinstance(sources, list):
            raise DataSourceError("data_sources must be a list")
        return [source for source in sources if isinstance(source, dict)]

    def list_enabled(self) -> list[dict[str, Any]]:
        return [self._public(source) for source in self._load() if source.get("enabled") is True]

    def resolve(self, data_source_id: str) -> dict[str, Any]:
        for source in self._load():
            if source.get("id") != data_source_id:
                continue
            if source.get("enabled") is not True:
                raise DataSourceError(f"Data source is not enabled: {data_source_id}")
            execution_mode = str(source.get("execution_mode", "")).lower()
            if execution_mode not in {"sqlite", "federated", "direct"}:
                raise DataSourceError(f"Unsupported execution mode for {data_source_id}")
            return source
        raise DataSourceError(f"Unknown data source: {data_source_id}")

    @staticmethod
    def _public(source: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": source.get("id"),
            "name": source.get("name"),
            "engine": source.get("engine"),
            "status": source.get("status"),
            "schema_scope": source.get("schema_scope"),
            "enabled": bool(source.get("enabled")),
        }


def create_data_source_registry(project_root: Path) -> DataSourceRegistry:
    return DataSourceRegistry(project_root)
