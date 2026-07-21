# Security and Multi-Tenant Operation

## Authentication modes

The default `development` mode resolves every local request to the configured demo principal `test_user_001`; request-body `user_id` is ignored.

For any shared or deployed environment, configure API-key authentication or OIDC before starting the API:

```dotenv
DATA_AGENT_AUTH_MODE=api_key
DATA_AGENT_ADMIN_API_KEY=replace-with-a-long-random-secret
DATA_AGENT_ANALYST_API_KEY=replace-with-a-different-random-secret
API_ALLOWED_ORIGINS=https://data-agent.example.com
```

The frontend can pass a key through its build environment only for trusted internal deployments:

```dotenv
VITE_DATA_AGENT_API_KEY=replace-with-a-short-lived-user-key
```

Do not use a browser-exposed administrator key in a public application. Put an SSO/OIDC gateway in front of this API for internet-facing use.

## OIDC / SSO

The API can validate Bearer JWTs directly with the IdP's rotating JWKS. This keeps the existing RBAC and tenant SQL policy in force while making the identity, tenant, and role server-side claims rather than browser input.

```dotenv
DATA_AGENT_AUTH_MODE=oidc
OIDC_ISSUER=https://login.example.com/realms/data
OIDC_AUDIENCE=data-agent-api
OIDC_JWKS_URL=https://login.example.com/realms/data/protocol/openid-connect/certs
OIDC_USER_CLAIM=sub
OIDC_TENANT_CLAIM=tenant_id
OIDC_ROLE_CLAIM=roles
AUTH_RATE_LIMIT_ENABLED=true
AUTH_RATE_LIMIT_WINDOW_SECONDS=60
AUTH_RATE_LIMIT_REQUESTS=60
```

Configure `role_mapping` in `access_control.yaml` when an IdP role name differs from an application role. Claims are synchronized on every authenticated request, so role and tenant changes take effect on the user's next request. Key rotation is handled by the JWKS client cache; use `OIDC_JWT_SECRET` only for a local test issuer, never for a public deployment.

The limiter is per authenticated user and uses Redis when available, with a process-local fallback for local development. Role-specific quotas live in `auth.rate_limit.role_requests`.

## RBAC and tenancy

`access_control.yaml` maps principals to a role and tenant. Each role contains:

- visible and queryable data sources;
- allowed tables and columns for every source;
- administrator capability for observability and audit-log access.

The Validator uses SQLGlot AST parsing to enforce table and column rules. `tenant_isolation` settings append a tenant predicate to protected source queries. Enable a tenant rule only after every permitted table has that tenant column.

## Audit

Security events are stored in `observability.db` in the `audit_logs` table. They include actor, role, tenant, action, resource, outcome, request ID, and safe structured details. API keys and raw user queries are never written to this audit table.

Administrators can read the latest events at `GET /api/v1/audit-events`.

## Tracing

Set `OTEL_EXPORTER_OTLP_ENDPOINT` to enable OpenTelemetry OTLP/HTTP export. A request span is created at the API boundary, and Agent events, LLM calls, and SQL execution are attached to the same trace. `X-Request-ID` remains the application-level correlation ID; `X-Trace-ID` is returned when tracing is active.
