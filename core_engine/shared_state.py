"""Optional Redis-backed state with a thread-safe in-memory fallback."""

import json
import os
import time
from threading import Lock
from typing import Any


class SharedState:
    """Store small coordination values without making Redis mandatory for local runs."""

    def __init__(self, redis_url: str | None = None, namespace: str = "text2sql"):
        self.redis_url = (redis_url or os.getenv("REDIS_URL", "")).strip()
        self.namespace = namespace.strip(":") or "text2sql"
        self._memory: dict[str, tuple[float, str]] = {}
        self._lock = Lock()
        self._redis: Any | None = None
        self._redis_retry_after = 0.0

    def _key(self, key: str) -> str:
        return f"{self.namespace}:{key}"

    def _client(self):
        if not self.redis_url or time.monotonic() < self._redis_retry_after:
            return None
        if self._redis is not None:
            return self._redis
        try:
            import redis

            client = redis.Redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=0.5,
                socket_timeout=0.5,
            )
            client.ping()
            self._redis = client
            return client
        except Exception:
            self._redis = None
            self._redis_retry_after = time.monotonic() + 30
            return None

    def set_text(self, key: str, value: str, ttl_seconds: int) -> None:
        ttl_seconds = max(1, int(ttl_seconds))
        client = self._client()
        if client is not None:
            try:
                client.set(self._key(key), value, ex=ttl_seconds)
                return
            except Exception:
                self._redis = None
                self._redis_retry_after = time.monotonic() + 30
        with self._lock:
            self._memory[key] = (time.monotonic() + ttl_seconds, value)

    def get_text(self, key: str) -> str | None:
        client = self._client()
        if client is not None:
            try:
                return client.get(self._key(key))
            except Exception:
                self._redis = None
                self._redis_retry_after = time.monotonic() + 30
        with self._lock:
            cached = self._memory.get(key)
            if cached is None:
                return None
            expires_at, value = cached
            if time.monotonic() >= expires_at:
                self._memory.pop(key, None)
                return None
            return value

    def delete(self, key: str) -> None:
        client = self._client()
        if client is not None:
            try:
                client.delete(self._key(key))
            except Exception:
                self._redis = None
                self._redis_retry_after = time.monotonic() + 30
        with self._lock:
            self._memory.pop(key, None)

    def set_json(self, key: str, value: Any, ttl_seconds: int) -> None:
        self.set_text(key, json.dumps(value, ensure_ascii=False, separators=(",", ":")), ttl_seconds)

    def get_json(self, key: str) -> Any | None:
        value = self.get_text(key)
        if value is None:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            self.delete(key)
            return None


shared_state = SharedState()
