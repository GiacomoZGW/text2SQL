"""Authentication, RBAC, tenant SQL policy, and request identity helpers."""

import hmac
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from fastapi import Header, HTTPException
from sqlglot import exp, parse_one
from sqlglot.errors import ParseError


class AccessDeniedError(PermissionError):
    """Raised when a principal is not permitted to access a protected resource."""


@dataclass(frozen=True)
class Principal:
    user_id: str
    role: str
    tenant_id: str

    def public(self) -> dict[str, str]:
        return {"user_id": self.user_id, "role": self.role, "tenant_id": self.tenant_id}


class AccessControl:
    """Load environment-backed identities and enforce configuration-backed authorization."""

    def __init__(self, project_root: Path):
        self.config_path = project_root / "config" / "access_control.yaml"

    def _load(self) -> dict[str, Any]:
        if not self.config_path.exists():
            raise RuntimeError(f"Access-control configuration not found: {self.config_path}")
        with self.config_path.open("r", encoding="utf-8") as config_file:
            data = yaml.safe_load(config_file) or {}
        if not isinstance(data, dict):
            raise RuntimeError("Access-control configuration must be a mapping")
        return data

    def _principal_from_entry(self, entry: dict[str, Any]) -> Principal:
        user_id = str(entry.get("user_id", "")).strip()
        role = str(entry.get("role", "")).strip()
        tenant_id = str(entry.get("tenant_id", "")).strip()
        if not user_id or not role or not tenant_id:
            raise RuntimeError("Each principal requires user_id, role, and tenant_id")
        if role not in self._load().get("roles", {}):
            raise RuntimeError(f"Unknown role in principal configuration: {role}")
        return Principal(user_id=user_id, role=role, tenant_id=tenant_id)

    def authenticate(self, api_key: str | None) -> Principal:
        config = self._load()
        auth = config.get("auth", {}) if isinstance(config.get("auth", {}), dict) else {}
        mode = os.getenv("DATA_AGENT_AUTH_MODE", str(auth.get("default_mode", "development"))).lower()
        principals = [item for item in config.get("principals", []) if isinstance(item, dict)]
        if mode == "development":
            development_user = str(auth.get("development_user_id", "test_user_001"))
            entry = next((item for item in principals if item.get("user_id") == development_user), None)
            if entry is None:
                raise RuntimeError("Configured development user was not found")
            return self._principal_from_entry(entry)
        if mode != "api_key":
            raise RuntimeError("DATA_AGENT_AUTH_MODE must be development or api_key")
        if not api_key:
            raise HTTPException(status_code=401, detail="Missing X-API-Key")
        for entry in principals:
            key_env = str(entry.get("api_key_env", "")).strip()
            expected = os.getenv(key_env, "") if key_env else ""
            if expected and hmac.compare_digest(api_key, expected):
                return self._principal_from_entry(entry)
        raise HTTPException(status_code=401, detail="Invalid API key")

    def current_principal(self, api_key: str | None = Header(default=None, alias="X-API-Key")) -> Principal:
        return self.authenticate(api_key)

    def _role(self, principal: Principal) -> dict[str, Any]:
        roles = self._load().get("roles", {})
        role = roles.get(principal.role)
        if not isinstance(role, dict):
            raise AccessDeniedError(f"Role is not configured: {principal.role}")
        return role

    def is_admin(self, principal: Principal) -> bool:
        return bool(self._role(principal).get("admin", False))

    def require_admin(self, principal: Principal) -> None:
        if not self.is_admin(principal):
            raise HTTPException(status_code=403, detail="Administrator role required")

    def require_self_or_admin(self, principal: Principal, user_id: str) -> None:
        if principal.user_id != user_id and not self.is_admin(principal):
            raise HTTPException(status_code=403, detail="You may only access your own user-scoped data")

    def authorize_data_source(self, principal: Principal, data_source_id: str) -> None:
        allowed_sources = self._role(principal).get("data_sources", [])
        if "*" not in allowed_sources and data_source_id not in allowed_sources:
            raise AccessDeniedError(f"Role {principal.role} cannot access data source {data_source_id}")

    def visible_sources(self, principal: Principal, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [source for source in sources if self._can_access_data_source(principal, str(source.get("id", "")))]

    def _can_access_data_source(self, principal: Principal, data_source_id: str) -> bool:
        try:
            self.authorize_data_source(principal, data_source_id)
            return True
        except AccessDeniedError:
            return False

    def enforce_sql_policy(self, principal: Principal, data_source_id: str, sql: str, dialect: str) -> str:
        """Validate table/column access and inject tenant scope where configured."""
        self.authorize_data_source(principal, data_source_id)
        try:
            tree = parse_one(sql, read=dialect)
        except ParseError as exc:
            raise AccessDeniedError(f"SQL policy parser rejected the query: {exc}") from exc
        if not isinstance(tree, (exp.Select, exp.Union, exp.Subquery, exp.Paren)) and not tree.find(exp.Select):
            raise AccessDeniedError("Only query expressions are eligible for SQL access policies")

        role = self._role(principal)
        table_policy = role.get("table_access", {}) if isinstance(role.get("table_access", {}), dict) else {}
        source_policy = table_policy.get(data_source_id, table_policy.get("*", {}))
        if not isinstance(source_policy, dict):
            raise AccessDeniedError(f"No table policy is configured for {data_source_id}")
        allowed_tables = {str(item).lower() for item in source_policy.get("tables", [])}
        columns_policy = source_policy.get("columns", {}) if isinstance(source_policy.get("columns", {}), dict) else {}

        cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE) if cte.alias_or_name}
        alias_map: dict[str, str] = {}
        for table in tree.find_all(exp.Table):
            table_name = table.name.lower()
            if table_name in cte_names:
                continue
            qualified_name = ".".join(part for part in (table.db, table.name) if part).lower()
            if "*" not in allowed_tables and table_name not in allowed_tables and qualified_name not in allowed_tables:
                raise AccessDeniedError(f"Table access denied: {qualified_name or table_name}")
            alias_map[table.alias_or_name.lower()] = table_name

        restrictive_columns = any("*" not in {str(value) for value in values} for values in columns_policy.values())
        if restrictive_columns and any(True for _ in tree.find_all(exp.Star)):
            raise AccessDeniedError("SELECT * is not allowed when column permissions are restricted")
        for column in tree.find_all(exp.Column):
            column_name = column.name.lower()
            if column.table:
                table_name = alias_map.get(column.table.lower(), column.table.lower())
            elif len(set(alias_map.values())) == 1:
                table_name = next(iter(alias_map.values()))
            else:
                table_name = "*"
            allowed_columns = columns_policy.get(table_name, columns_policy.get("*", []))
            allowed_column_names = {str(item).lower() for item in allowed_columns}
            if "*" not in allowed_column_names and column_name not in allowed_column_names:
                raise AccessDeniedError(f"Column access denied: {table_name}.{column_name}")

        tenant_policy = self._load().get("tenant_isolation", {})
        source_tenant_policy = tenant_policy.get(data_source_id, {}) if isinstance(tenant_policy, dict) else {}
        if source_tenant_policy.get("enabled") is True:
            tenant_column = str(source_tenant_policy.get("column", "tenant_id"))
            for select in tree.find_all(exp.Select):
                direct_tables = [table for table in select.find_all(exp.Table) if table.parent_select is select]
                if not direct_tables:
                    continue
                conditions = [
                    exp.EQ(
                        this=exp.column(tenant_column, table=table.alias_or_name),
                        expression=exp.Literal.string(principal.tenant_id),
                    )
                    for table in direct_tables
                    if table.name.lower() not in cte_names
                ]
                if conditions:
                    tenant_condition = conditions[0]
                    for condition in conditions[1:]:
                        tenant_condition = exp.and_(tenant_condition, condition)
                    existing_where = select.args.get("where")
                    existing_condition = existing_where.this if existing_where is not None else None
                    select.set("where", exp.Where(this=exp.and_(existing_condition, tenant_condition)))
        return tree.sql(dialect=dialect)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
access_control = AccessControl(PROJECT_ROOT)
