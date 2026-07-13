"""Cooperative request pause markers shared by the API and agent workflow."""

from threading import Lock


class RequestPaused(RuntimeError):
    """Raised when a running request has been paused by the user."""


class RequestControl:
    def __init__(self):
        self._paused_ids: set[str] = set()
        self._lock = Lock()

    def pause(self, request_id: str) -> None:
        with self._lock:
            self._paused_ids.add(request_id)

    def is_paused(self, request_id: str) -> bool:
        with self._lock:
            return request_id in self._paused_ids

    def clear(self, request_id: str) -> None:
        with self._lock:
            self._paused_ids.discard(request_id)


request_control = RequestControl()
