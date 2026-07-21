# Runtime Configuration

The default API mode runs blocking LangGraph work through a bounded in-process queue. Redis is optional in that mode and shares only pause markers and Schema-vector cache entries.

```dotenv
# REDIS_URL=redis://localhost:6379/0

# Shared runtime state. When unset, local SQLite files remain the fallback.
# RUNTIME_DATABASE_URL=postgresql://data_agent:password@localhost:5432/data_agent
# MEMORY_DATABASE_URL=postgresql://data_agent:password@localhost:5432/data_agent
# OBSERVABILITY_DATABASE_URL=postgresql://data_agent:password@localhost:5432/data_agent

# Unified LLM and embedding settings. OPENAI_API_KEY takes precedence for
# ChatOpenAI; DASHSCOPE_API_KEY is used as a compatible fallback and by
# DashScope embeddings.
# OPENAI_API_KEY=
# OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
# LLM_MODEL=deepseek-v4-flash
# LLM_TEMPERATURE=0.1
# LLM_TIMEOUT_SECONDS=60
# LLM_MAX_RETRIES=3
# LLM_MAX_TOKENS=4096
# DASHSCOPE_API_KEY=
# EMBEDDING_MODEL=text-embedding-v4
# EMBEDDING_MAX_RETRIES=3

# Local default: execute through the API process.
# TASK_EXECUTION_MODE=inline

# Durable mode: API enqueues Redis Stream tasks; start task_worker separately.
# TASK_EXECUTION_MODE=redis_streams
# DURABLE_TASK_MAX_ATTEMPTS=3
# DURABLE_TASK_RECLAIM_IDLE_MS=60000
# DURABLE_TASK_TTL_SECONDS=604800

# AGENT_WORKER_CONCURRENCY=2
# AGENT_QUEUE_SIZE=32
# AGENT_REQUEST_TIMEOUT_SECONDS=120
# REQUEST_PAUSE_TTL_SECONDS=900
# SCHEMA_CACHE_TTL_SECONDS=3600
# SCHEMA_REVIEW_MAX_FULL_SCANS=1
# SCHEMA_REVIEW_MAX_EXPLAIN_COST=100000
```

With `TASK_EXECUTION_MODE=redis_streams`, `POST /api/v1/query` records a queued request and returns `202`; `python -m core_engine.task_worker` consumes the task from a Redis Stream consumer group. Restarted Workers claim pending entries that exceed `DURABLE_TASK_RECLAIM_IDLE_MS`. Infrastructure failures retry up to `DURABLE_TASK_MAX_ATTEMPTS`; a paused task remains terminal.

Use one API worker for local development. In Redis Streams mode API replicas and Worker replicas can scale independently. A running LLM call still stops cooperatively at the workflow's pause checks.

`RUNTIME_DATABASE_URL` moves conversation memory, semantic memory, audit logs, evaluation records, and observability data to PostgreSQL. The store-specific URLs override it only when an intentional split is needed. When no PostgreSQL URL is set, the container uses the persisted SQLite files at `MEMORY_DB_PATH` and `OBSERVABILITY_DB_PATH`; this is suitable for local demos only.

Schema retrieval uses the versioned business metadata in `config/schema_catalog.yaml` in addition to live database metadata. It applies data-source/table metadata filters first, then combines vector similarity, keyword matching, and metadata matches. SQL candidates also pass a deterministic Reviewer check for catalog tables and columns, configured relationships, aggregation shape, and bounded `EXPLAIN` plan risk. `SCHEMA_REVIEW_MAX_FULL_SCANS` and `SCHEMA_REVIEW_MAX_EXPLAIN_COST` only reject plans that exceed the configured high-risk thresholds.
