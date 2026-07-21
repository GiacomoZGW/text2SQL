"""Authentication, RBAC, tenant SQL policy, and request identity helpers."""

import hmac
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jwt
import yaml
from fastapi import Header, HTTPException, Request
from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

from core_engine.shared_state import shared_state


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
        self._jwks_clients: dict[str, Any] = {}

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

    @staticmethod
    def _mapping(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _auth_config(self) -> dict[str, Any]:
        return self._mapping(self._load().get("auth"))

    @staticmethod
    def _env_or_config(name: str, config: dict[str, Any], key: str) -> str:
        return os.getenv(name, str(config.get(key, ""))).strip()

    def _oidc_config(self) -> dict[str, Any]:
        return self._mapping(self._auth_config().get("oidc"))

    @staticmethod
    def _claim(claims: dict[str, Any], claim_name: str) -> Any:
        value: Any = claims
        for part in claim_name.split("."):
            if not isinstance(value, dict):
                return None
            value = value.get(part)
        return value

    def _oidc_principal(self, authorization: str | None) -> Principal:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="Missing Bearer token")
        token = authorization.split(" ", 1)[1].strip()
        if not token:
            raise HTTPException(status_code=401, detail="Missing Bearer token")

        config = self._oidc_config()
        issuer = self._env_or_config("OIDC_ISSUER", config, "issuer")
        audience = self._env_or_config("OIDC_AUDIENCE", config, "audience")
        jwks_url = self._env_or_config("OIDC_JWKS_URL", config, "jwks_url")
        shared_secret = os.getenv("OIDC_JWT_SECRET", "").strip()
        if not issuer:
            raise RuntimeError("OIDC_ISSUER must be configured when DATA_AGENT_AUTH_MODE=oidc")
        try:
            decode_options: dict[str, Any] = {"require": ["exp", "sub"]}
            decode_kwargs: dict[str, Any] = {"issuer": issuer, "options": decode_options}
            if audience:
                decode_kwargs["audience"] = audience
            else:
                decode_options["verify_aud"] = False
            if shared_secret:
                claims = jwt.decode(token, shared_secret, algorithms=["HS256"], **decode_kwargs)
            else:
                if not jwks_url:
                    raise RuntimeError("OIDC_JWKS_URL must be configured for asymmetric OIDC tokens")
                client = self._jwks_clients.get(jwks_url)
                if client is None:
                    client = jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=300)
                    self._jwks_clients[jwks_url] = client
                signing_key = client.get_signing_key_from_jwt(token).key
                claims = jwt.decode(token, signing_key, algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"], **decode_kwargs)
        except (jwt.PyJWTError, ValueError) as exc:
            raise HTTPException(status_code=401, detail="Invalid or expired OIDC token") from exc

        user_claim = self._env_or_config("OIDC_USER_CLAIM", config, "user_claim") or "sub"
        tenant_claim = self._env_or_config("OIDC_TENANT_CLAIM", config, "tenant_claim") or "tenant_id"
        role_claim = self._env_or_config("OIDC_ROLE_CLAIM", config, "role_claim") or "roles"
        user_id = str(self._claim(claims, user_claim) or "").strip()
        tenant_id = str(self._claim(claims, tenant_claim) or "").strip()
        raw_roles = self._claim(claims, role_claim)
        roles = raw_roles if isinstance(raw_roles, list) else [raw_roles]
        role_mapping = self._mapping(config.get("role_mapping"))
        configured_roles = self._load().get("roles", {})
        mapped_roles = [str(role_mapping.get(str(role), role)).strip() for role in roles if str(role).strip()]
        role = next((candidate for candidate in mapped_roles if candidate in configured_roles), "")
        if not user_id or not tenant_id or not role:
            raise HTTPException(status_code=403, detail="OIDC token is missing an authorized user, tenant, or role claim")

        # Claim-based just-in-time sync means role and tenant changes made in the
        # IdP apply on the next request without trusting client-provided fields.
        shared_state.set_json(
            f"identity:{user_id}",
            {"user_id": user_id, "tenant_id": tenant_id, "role": role, "issuer": issuer, "synced_at": int(time.time())},
            86_400,
        )
        return Principal(user_id=user_id, role=role, tenant_id=tenant_id)

    def authenticate(self, api_key: str | None, authorization: str | None = None) -> Principal:
        config = self._load()
        auth = self._auth_config()
        mode = os.getenv("DATA_AGENT_AUTH_MODE", str(auth.get("default_mode", "development"))).lower()
        principals = [item for item in config.get("principals", []) if isinstance(item, dict)]
        if mode == "development":
            development_user = str(auth.get("development_user_id", "test_user_001"))
            entry = next((item for item in principals if item.get("user_id") == development_user), None)
            if entry is None:
                raise RuntimeError("Configured development user was not found")
            return self._principal_from_entry(entry)
        if mode == "oidc":
            return self._oidc_principal(authorization)
        if mode != "api_key":
            raise RuntimeError("DATA_AGENT_AUTH_MODE must be development, api_key, or oidc")
        if not api_key:
            raise HTTPException(status_code=401, detail="Missing X-API-Key")
        for entry in principals:
            key_env = str(entry.get("api_key_env", "")).strip()
            expected = os.getenv(key_env, "") if key_env else ""
            if expected and hmac.compare_digest(api_key, expected):
                return self._principal_from_entry(entry)
        raise HTTPException(status_code=401, detail="Invalid API key")

    def _rate_limit(self, principal: Principal) -> None:
        config = self._mapping(self._auth_config().get("rate_limit"))
        enabled = os.getenv("AUTH_RATE_LIMIT_ENABLED", str(config.get("enabled", "false"))).lower() in {"1", "true", "yes"}
        if not enabled:
            return
        window_seconds = max(1, int(os.getenv("AUTH_RATE_LIMIT_WINDOW_SECONDS", str(config.get("window_seconds", 60)))))
        default_limit = max(1, int(os.getenv("AUTH_RATE_LIMIT_REQUESTS", str(config.get("requests", 60)))))
        role_limits = self._mapping(config.get("role_requests"))
        limit = max(1, int(role_limits.get(principal.role, default_limit)))
        window = int(time.time() // window_seconds)
        count = shared_state.increment(f"auth-rate:{principal.user_id}:{window}", window_seconds + 1)
        if count > limit:
            raise HTTPException(
                status_code=429,
                detail="Request rate limit exceeded",
                headers={"Retry-After": str(window_seconds)},
            )

    def current_principal(
        self,
        request: Request,
        api_key: str | None = Header(default=None, alias="X-API-Key"),
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> Principal:
        principal = self.authenticate(api_key, authorization)
        self._rate_limit(principal)
        request.state.principal = principal
        return principal

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
