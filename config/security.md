# Security and Multi-Tenant Operation

## Authentication modes

The default `development` mode resolves every local request to the configured demo principal `test_user_001`; request-body `user_id` is ignored.

For any shared or deployed environment, configure API-key authentication before starting the API:

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

## RBAC and tenancy

`access_control.yaml` maps principals to a role and tenant. Each role contains:

- visible and queryable data sources;
- allowed tables and columns for every source;
- administrator capability for observability and audit-log access.

The Validator uses SQLGlot AST parsing to enforce table and column rules. `tenant_isolation` settings append a tenant predicate to protected source queries. Enable a tenant rule only after every permitted table has that tenant column.

## Audit

Security events are stored in `observability.db` in the `audit_logs` table. They include actor, role, tenant, action, resource, outcome, request ID, and safe structured details. API keys and raw user queries are never written to this audit table.

Administrators can read the latest events at `GET /api/v1/audit-events`.
