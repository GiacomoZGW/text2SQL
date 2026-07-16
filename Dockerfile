# Build the static workspace separately so the runtime image contains no Node.js tooling.
FROM node:22-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MEMORY_DB_PATH=/var/lib/data-agent/memory.db \
    OBSERVABILITY_DB_PATH=/var/lib/data-agent/observability.db
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./
COPY --from=frontend-build /frontend/dist ./frontend/dist

RUN groupadd --system dataagent \
    && useradd --system --gid dataagent --home /app dataagent \
    && mkdir -p /var/lib/data-agent \
    && chown -R dataagent:dataagent /app /var/lib/data-agent
USER dataagent

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/health/ready', timeout=3)"
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
