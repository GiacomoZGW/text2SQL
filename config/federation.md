# DuckDB Federation Configuration

The federated workflow uses the aliases in `federation.yaml` regardless of where each source runs.

Set these optional values in the project `.env` file to query real services:

```dotenv
MYSQL_URI=mysql://user:password@host:3306/business_db
POSTGRES_URI=postgresql://user:password@host:5432/logs_db
CLICKHOUSE_URI=clickhouse://user:password@host:8123/features_db
```

When a URI is absent, the same alias is mounted through DuckDB's SQLite extension using the local demo database listed in `federation.yaml`.

| SQL alias | Real source type | Environment variable |
| --- | --- | --- |
| `db_mysql` | MySQL | `MYSQL_URI` |
| `db_mongo` | PostgreSQL log source | `POSTGRES_URI` |
| `db_ch` | ClickHouse | `CLICKHOUSE_URI` |

DuckDB downloads an extension the first time it connects to a source type. The process running the API therefore needs network access for a source type whose extension is not already installed.

Schema retrieval defaults to `dashscope_memory`, which keeps DashScope vectors in the API process and avoids local Chroma locking on this Windows environment. Set `SCHEMA_VECTOR_BACKEND=chroma` only after verifying the local `chromadb` runtime is stable.
