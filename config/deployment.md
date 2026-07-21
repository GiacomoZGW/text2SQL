# Deployment Guide

## Dependency Sets

The default runtime uses the in-process `dashscope_memory` Schema cache and does not install Chroma:

```bash
python -m pip install -r requirements.txt
```

Install direct database drivers only when a corresponding source is enabled:

```bash
python -m pip install -r requirements-connectors.txt
```

Chroma is an opt-in compatibility backend. It is isolated because its embedded runtime has been unstable on this Windows project. Only install it when `SCHEMA_VECTOR_BACKEND=chroma` is intentionally configured:

```bash
python -m pip install -r requirements-chroma.txt
```

## Docker Compose

Copy the container template to `.env.docker`, fill in only the required secrets, then run:

```bash
# PowerShell: Copy-Item .env.docker.example .env.docker
# macOS/Linux: cp .env.docker.example .env.docker
docker compose --env-file .env.docker up --build -d
```

`.env.docker` is separate from the local development `.env`, so Docker Compose never has to parse editor-specific or shell-specific lines in that file.

The API is available at `http://127.0.0.1:8000`. Compose starts a separate `worker` service for LangGraph execution, PostgreSQL as the shared source of truth for memory, audit, evaluation, and observability state, and Redis for task streams, pause markers, and Schema cache entries. The `agent_state` volume remains available only for local SQLite fallback data.

PostgreSQL is also exposed on `127.0.0.1:${POSTGRES_STATE_PORT:-5432}` for local PyCharm connections. Start only the state services with `docker compose --env-file .env.docker up -d postgres redis`, or start the complete system with `docker compose --env-file .env.docker up --build -d`. The local connection URL is `postgresql://<user>:<password>@127.0.0.1:<port>/<database>`; the Docker API and Worker instead use the internal hostname `postgres`.

Use `http://127.0.0.1:8000/health/live` for a process probe and `http://127.0.0.1:8000/health/ready` for a readiness probe. The readiness endpoint verifies that the worker runtime has started and the data-source configuration can be read; it does not open every business database connection. Compose also checks Redis connectivity from the durable Worker, so `docker compose ps` shows unhealthy infrastructure before a task is accepted.

For Dockerized direct database connections, do not use `127.0.0.1` in a source URI to reach a database on the host. Use a network service name or `host.docker.internal` where supported.

## Process Model

Compose enables `TASK_EXECUTION_MODE=redis_streams`: the API only validates and submits a task, while the Worker consumes Redis Stream entries. Task state and result are retained for seven days by default, stale pending entries are claimed by a restarted Worker, and infrastructure failures receive bounded retries. The frontend waits through `GET /api/v1/requests/{request_id}/result`; other clients should treat the initial `202` response as asynchronous.

## Migrating Existing Local State

After PostgreSQL is reachable, set `RUNTIME_DATABASE_URL` and run the idempotent migration from the project root:

```bash
python scripts/migrate_runtime_state.py
```

It copies the local `memory.db` and `observability.db` tables without deleting either source file. Back up those files before a production migration and verify the resulting row counts in the command output.

## Regression Gates

Run deterministic golden cases before changing prompts, models, Schema Catalog metadata, or access policy:

```bash
python scripts/run_evaluation.py --offline --output artifacts/evaluation.json
```

Use `--include-extended` without `--offline` in a secure environment with model credentials to run database-backed Text2SQL cases. GitHub Actions runs unit tests, the offline evaluation suite, Docker configuration validation, a FastAPI end-to-end bootstrap flow, and a Playwright browser flow.
