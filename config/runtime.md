# Runtime Configuration

The API runs blocking LangGraph work through a bounded in-process worker queue.
It starts with a safe local fallback and can share coordination state through Redis.

```dotenv
# Optional: enables shared pause markers and Schema-vector cache across API workers.
# REDIS_URL=redis://localhost:6379/0

# AGENT_WORKER_CONCURRENCY=2
# AGENT_QUEUE_SIZE=32
# AGENT_REQUEST_TIMEOUT_SECONDS=120
# REQUEST_PAUSE_TTL_SECONDS=900
# SCHEMA_CACHE_TTL_SECONDS=3600
```

Use one API worker for local development. When running multiple API workers, configure `REDIS_URL` so pause requests and Schema vectors are visible to every process. The in-process queue is intentionally not durable; production deployments that require durable jobs should use a dedicated external queue and worker service.

The container image sets `MEMORY_DB_PATH` and `OBSERVABILITY_DB_PATH` to `/var/lib/data-agent`, which is persisted by `docker-compose.yml`. Keep those paths on persistent storage in every production deployment.
