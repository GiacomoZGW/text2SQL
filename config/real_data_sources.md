# Real Data Source Connectors

MySQL, PostgreSQL, and ClickHouse are defined as disabled `direct` data sources in `data_sources.yaml`.
They are not visible to the frontend until both their URI environment variable is set and `enabled` is changed to `true`.

## Environment variables

```dotenv
# SQLAlchemy URLs. Keep credentials only in .env or a deployment secret manager.
MYSQL_URI=mysql+pymysql://readonly_user:password@mysql.example.com:3306/business?charset=utf8mb4
POSTGRES_URI=postgresql+psycopg://readonly_user:password@postgres.example.com:5432/logs
CLICKHOUSE_URI=clickhousedb://readonly_user:password@clickhouse.example.com:8443/features?secure=true

# Optional certificate paths used when TLS is enabled in data_sources.yaml.
MYSQL_SSL_CA=/run/secrets/mysql-ca.pem
POSTGRES_SSL_ROOT_CERT=/run/secrets/postgres-ca.pem
```

Install the optional drivers before enabling a source:

```powershell
pip install -r requirements.txt
```

## Guardrails

- Every source uses its own SQLAlchemy connection pool with `pool_pre_ping`, recycle, connect timeout, query timeout, and row cap settings.
- The connector validates SQL again at the database boundary and only accepts a single `SELECT` or `WITH` statement.
- PostgreSQL and MySQL sessions are marked read-only. ClickHouse is asked to use read-only and max-execution-time settings. Use database accounts with read-only permissions as the final enforcement layer.
- TLS is enabled per source. Put certificate paths in environment variables, never in Git.
- Call `GET /api/v1/data-sources/{data_source_id}/health` after enabling a source. The response intentionally contains no URI or secret.

## Federation

Keep `federated_demo` for cross-database SQL through DuckDB. Direct connectors handle a single selected database; federation still requires all participating sources to be configured and reachable through `config/federation.yaml`.
