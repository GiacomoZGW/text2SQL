"""Cooperative pause markers shared by API workers and the agent workflow."""

import os

from .shared_state import SharedState, shared_state


class RequestPaused(RuntimeError):
    """Raised when a running request has been paused by the user."""


class RequestControl:
    def __init__(self, state: SharedState | None = None, pause_ttl_seconds: int | None = None):
        self._state = state or shared_state
        self.pause_ttl_seconds = max(
            30, int(pause_ttl_seconds or os.getenv("REQUEST_PAUSE_TTL_SECONDS", "900"))
        )

    @staticmethod
    def _key(request_id: str) -> str:
        return f"paused-request:{request_id}"

    def pause(self, request_id: str) -> None:
        self._state.set_text(self._key(request_id), "1", self.pause_ttl_seconds)

    def is_paused(self, request_id: str) -> bool:
        return self._state.get_text(self._key(request_id)) == "1"

    def clear(self, request_id: str) -> None:
        self._state.delete(self._key(request_id))


request_control = RequestControl()
