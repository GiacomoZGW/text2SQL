"""Redis Streams-backed durable request queue for the Agent worker process."""

import json
import os
import socket
import time
from datetime import datetime, timezone
from typing import Any, Callable


class DurableTaskError(RuntimeError):
    """Raised when a durable task cannot be submitted or consumed."""


class DurableTaskUnavailable(DurableTaskError):
    """Raised when Redis Streams is required but unavailable."""


TERMINAL_STATUSES = {"completed", "failed", "paused", "timed_out"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode(value: Any) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _decode_mapping(values: dict[Any, Any]) -> dict[str, str]:
    return {_decode(key): _decode(value) for key, value in values.items()}


class DurableTaskQueue:
    """Persist query payloads, lifecycle state, and results in Redis Streams."""

    def __init__(
        self,
        redis_url: str | None = None,
        namespace: str | None = None,
        client: Any | None = None,
    ):
        self.redis_url = (redis_url or os.getenv("REDIS_URL", "")).strip()
        self.namespace = (namespace or os.getenv("DURABLE_TASK_NAMESPACE", "data-agent")).strip(":") or "data-agent"
        self.stream_name = os.getenv("DURABLE_TASK_STREAM", f"{self.namespace}:query-jobs")
        self.group_name = os.getenv("DURABLE_TASK_GROUP", f"{self.namespace}:workers")
        self.task_ttl_seconds = max(3_600, int(os.getenv("DURABLE_TASK_TTL_SECONDS", "604800")))
        self.max_attempts = max(1, int(os.getenv("DURABLE_TASK_MAX_ATTEMPTS", "3")))
        self.reclaim_idle_ms = max(1_000, int(os.getenv("DURABLE_TASK_RECLAIM_IDLE_MS", "60000")))
        self.stream_maxlen = max(100, int(os.getenv("DURABLE_TASK_STREAM_MAXLEN", "10000")))
        self.redis_socket_timeout_seconds = max(
            10.0,
            float(os.getenv("REDIS_SOCKET_TIMEOUT_SECONDS", "10")),
        )
        self.worker_retry_seconds = max(
            0.1,
            float(os.getenv("DURABLE_TASK_WORKER_RETRY_SECONDS", "1")),
        )
        self._client = client

    @property
    def mode(self) -> str:
        return os.getenv("TASK_EXECUTION_MODE", "inline").strip().lower()

    @property
    def is_worker_process(self) -> bool:
        return os.getenv("DATA_AGENT_WORKER_PROCESS", "").strip().lower() in {"1", "true", "yes"}

    @property
    def api_enabled(self) -> bool:
        return self.mode == "redis_streams" and not self.is_worker_process

    @property
    def worker_enabled(self) -> bool:
        return self.mode == "redis_streams" and self.is_worker_process

    def _redis(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.redis_url:
            raise DurableTaskUnavailable("TASK_EXECUTION_MODE=redis_streams requires REDIS_URL")
        try:
            import redis

            client = redis.Redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=1,
                socket_timeout=self.redis_socket_timeout_seconds,
                health_check_interval=15,
            )
            client.ping()
            self._client = client
            return client
        except Exception as exc:
            raise DurableTaskUnavailable(f"Redis Streams is unavailable: {exc}") from exc

    def available(self) -> bool:
        try:
            self._redis().ping()
            return True
        except Exception:
            return False

    def _task_key(self, request_id: str) -> str:
        return f"{self.namespace}:task:{request_id}"

    @staticmethod
    def _public(record: dict[str, str]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "request_id": record.get("request_id", ""),
            "status": record.get("status", "unknown"),
            "attempt": int(record.get("attempt", "0") or 0),
            "max_attempts": int(record.get("max_attempts", "0") or 0),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
        }
        if record.get("error"):
            result["error"] = record["error"]
        return result

    def get(self, request_id: str) -> dict[str, Any] | None:
        values = _decode_mapping(self._redis().hgetall(self._task_key(request_id)))
        if not values:
            return None
        values["public"] = self._public(values)
        for field in ("payload", "result"):
            if values.get(field):
                try:
                    values[field] = json.loads(values[field])
                except json.JSONDecodeError:
                    values[field] = None
        return values

    def owner_id(self, request_id: str) -> str | None:
        task = self.get(request_id)
        return str(task.get("user_id")) if task and task.get("user_id") else None

    def enqueue(self, request_id: str, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        client = self._redis()
        key = self._task_key(request_id)
        if not client.hsetnx(key, "request_id", request_id):
            existing = self.get(request_id)
            if existing is None:
                raise DurableTaskError(f"Unable to load existing task {request_id}")
            return existing

        now = _utc_now()
        mapping = {
            "user_id": user_id,
            "status": "queued",
            "attempt": "0",
            "max_attempts": str(self.max_attempts),
            "payload": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            "created_at": now,
            "updated_at": now,
        }
        try:
            pipeline = client.pipeline(transaction=True)
            pipeline.hset(key, mapping=mapping)
            pipeline.expire(key, self.task_ttl_seconds)
            pipeline.xadd(self.stream_name, {"request_id": request_id}, maxlen=self.stream_maxlen, approximate=True)
            pipeline.execute()
        except Exception as exc:
            client.delete(key)
            raise DurableTaskUnavailable(f"Unable to enqueue durable task: {exc}") from exc
        task = self.get(request_id)
        if task is None:
            raise DurableTaskError(f"Unable to read queued task {request_id}")
        return task

    def _update(self, request_id: str, **values: Any) -> dict[str, Any] | None:
        mapping = {key: str(value) for key, value in values.items() if value is not None}
        mapping["updated_at"] = _utc_now()
        client = self._redis()
        key = self._task_key(request_id)
        if not client.exists(key):
            return None
        client.hset(key, mapping=mapping)
        client.expire(key, self.task_ttl_seconds)
        return self.get(request_id)

    def request_pause(self, request_id: str) -> dict[str, Any] | None:
        task = self.get(request_id)
        if task is None:
            return None
        status = str(task.get("status", ""))
        if status in TERMINAL_STATUSES:
            return task
        next_status = "paused" if status == "queued" else "pause_requested"
        return self._update(request_id, status=next_status)

    def ensure_consumer_group(self) -> None:
        client = self._redis()
        try:
            client.xgroup_create(self.stream_name, self.group_name, id="0-0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise DurableTaskUnavailable(f"Unable to create Redis consumer group: {exc}") from exc

    def _claim_stale(self, consumer_name: str) -> list[tuple[str, dict[str, str]]]:
        client = self._redis()
        try:
            claimed = client.xautoclaim(
                self.stream_name,
                self.group_name,
                consumer_name,
                min_idle_time=self.reclaim_idle_ms,
                start_id="0-0",
                count=10,
            )
        except AttributeError:
            return []
        except Exception as exc:
            raise DurableTaskUnavailable(f"Unable to recover pending tasks: {exc}") from exc
        messages = claimed[1] if isinstance(claimed, (list, tuple)) and len(claimed) > 1 else []
        return [(_decode(message_id), _decode_mapping(fields)) for message_id, fields in messages]

    def next_messages(self, consumer_name: str, block_ms: int = 5_000) -> list[tuple[str, dict[str, str]]]:
        self.ensure_consumer_group()
        claimed = self._claim_stale(consumer_name)
        if claimed:
            return claimed
        response = self._redis().xreadgroup(
            self.group_name,
            consumer_name,
            {self.stream_name: ">"},
            count=1,
            block=max(1, int(block_ms)),
        )
        messages: list[tuple[str, dict[str, str]]] = []
        for _, stream_messages in response or []:
            for message_id, fields in stream_messages:
                messages.append((_decode(message_id), _decode_mapping(fields)))
        return messages

    def acknowledge(self, message_id: str) -> None:
        self._redis().xack(self.stream_name, self.group_name, message_id)

    def mark_running(self, request_id: str) -> dict[str, Any] | None:
        task = self.get(request_id)
        if task is None:
            return None
        attempt = int(task.get("attempt", "0") or 0) + 1
        return self._update(request_id, status="running", attempt=attempt, error="")

    def complete(self, request_id: str, response: dict[str, Any]) -> dict[str, Any] | None:
        return self._update(
            request_id,
            status="completed",
            result=json.dumps(response, ensure_ascii=False, separators=(",", ":")),
            error="",
        )

    def finish_paused(self, request_id: str, response: dict[str, Any] | None = None) -> dict[str, Any] | None:
        values: dict[str, Any] = {"status": "paused", "error": "Request paused by user"}
        if response is not None:
            values["result"] = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
        return self._update(request_id, **values)

    def fail_or_retry(self, request_id: str, error: str, response: dict[str, Any] | None = None) -> bool:
        """Return True when a retry was submitted, False when the task is terminal."""
        task = self.get(request_id)
        if task is None:
            return False
        attempt = int(task.get("attempt", "0") or 0)
        if attempt < self.max_attempts:
            self._update(request_id, status="queued", error=error[:2_000])
            self._redis().xadd(
                self.stream_name,
                {"request_id": request_id},
                maxlen=self.stream_maxlen,
                approximate=True,
            )
            return True
        values: dict[str, Any] = {"status": "failed", "error": error[:2_000]}
        if response is not None:
            values["result"] = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
        self._update(request_id, **values)
        return False


class DurableTaskWorker:
    """Consume Redis Stream entries and retry only infrastructure-level failures."""

    def __init__(self, queue: DurableTaskQueue, consumer_name: str | None = None):
        self.queue = queue
        self.consumer_name = consumer_name or os.getenv(
            "DURABLE_TASK_CONSUMER", f"{socket.gethostname()}-{os.getpid()}"
        )
        self._running = True

    def stop(self) -> None:
        self._running = False

    def _handle(self, message_id: str, fields: dict[str, str], handler: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        request_id = fields.get("request_id", "")
        if not request_id:
            self.queue.acknowledge(message_id)
            return
        task = self.queue.get(request_id)
        if task is None or str(task.get("status", "")) in TERMINAL_STATUSES:
            self.queue.acknowledge(message_id)
            return
        if task.get("status") == "pause_requested" or task.get("status") == "paused":
            self.queue.finish_paused(request_id)
            self.queue.acknowledge(message_id)
            return
        running = self.queue.mark_running(request_id)
        if running is None:
            self.queue.acknowledge(message_id)
            return
        try:
            response = handler(dict(task.get("payload") or {}))
            response_code = int(response.get("code", 500)) if isinstance(response, dict) else 500
            if response_code == 499:
                self.queue.finish_paused(request_id, response if isinstance(response, dict) else None)
            elif response_code >= 500:
                self.queue.fail_or_retry(request_id, f"Worker response code {response_code}", response)
            else:
                self.queue.complete(request_id, response)
        except Exception as exc:
            self.queue.fail_or_retry(request_id, str(exc))
        finally:
            self.queue.acknowledge(message_id)

    def run(self, handler: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self.queue.ensure_consumer_group()
        while self._running:
            try:
                for message_id, fields in self.queue.next_messages(self.consumer_name):
                    self._handle(message_id, fields, handler)
            except Exception as exc:
                self.queue._client = None
                print(f"[Worker] queue read failed; retrying: {exc}", flush=True)
                time.sleep(self.queue.worker_retry_seconds)


durable_task_queue = DurableTaskQueue()
