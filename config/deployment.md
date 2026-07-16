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

The API is available at `http://127.0.0.1:8000`. Compose persists memory and observability SQLite files in the `agent_state` volume and runs Redis for shared pause markers and Schema cache entries.

Use `http://127.0.0.1:8000/health/live` for a process probe and `http://127.0.0.1:8000/health/ready` for a readiness probe. The readiness endpoint verifies that the worker runtime has started and the data-source configuration can be read; it does not open every business database connection.

For Dockerized direct database connections, do not use `127.0.0.1` in a source URI to reach a database on the host. Use a network service name or `host.docker.internal` where supported.

## Process Model

Run one Uvicorn worker for the current in-process request queue. Redis shares pause and cache keys across processes, but it does not make the queue durable. A deployment needing retries or durable long-running jobs should put the workflow behind a separate queue and worker service before increasing Uvicorn worker count.
