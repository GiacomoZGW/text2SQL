"""Bounded async worker queue and SSE-friendly status events for agent requests."""

import asyncio
import os
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .request_control import RequestPaused, request_control


class RequestQueueFull(RuntimeError):
    """Raised when the local worker queue has reached its configured capacity."""


class RequestTimedOut(RuntimeError):
    """Raised when a request exceeds the server-side execution timeout."""


@dataclass
class QueuedRequest:
    request_id: str
    action: Callable[[], Any]
    future: asyncio.Future[Any]
    timed_out: bool = False
    started: bool = False


class RequestRuntime:
    """Run blocking LangGraph work outside the FastAPI event loop with bounded concurrency."""

    TERMINAL_STATUSES = {"completed", "failed", "paused", "timed_out"}

    def __init__(
        self,
        worker_count: int | None = None,
        queue_size: int | None = None,
        timeout_seconds: int | None = None,
        event_history_size: int | None = None,
    ):
        self.worker_count = max(1, int(worker_count or os.getenv("AGENT_WORKER_CONCURRENCY", "2")))
        self.queue_size = max(1, int(queue_size or os.getenv("AGENT_QUEUE_SIZE", "32")))
        configured_timeout = timeout_seconds if timeout_seconds is not None else os.getenv("AGENT_REQUEST_TIMEOUT_SECONDS", "120")
        self.timeout_seconds = max(1, int(configured_timeout))
        self.event_history_size = max(8, int(event_history_size or os.getenv("REQUEST_EVENT_HISTORY_SIZE", "64")))
        self._queue: asyncio.Queue[QueuedRequest | None] = asyncio.Queue(maxsize=self.queue_size)
        self._workers: list[asyncio.Task[None]] = []
        self._queued: dict[str, QueuedRequest] = {}
        self._events: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=self.event_history_size))
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._workers = [asyncio.create_task(self._worker(), name=f"agent-worker-{index}") for index in range(self.worker_count)]

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        for _ in self._workers:
            await self._queue.put(None)
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []

    def _publish(self, request_id: str, status: str, **data: Any) -> None:
        event = {
            "event": "status",
            "request_id": request_id,
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **data,
        }
        self._events[request_id].append(event)
        for subscriber in tuple(self._subscribers.get(request_id, ())):
            if not subscriber.full():
                subscriber.put_nowait(event)

    async def submit(self, request_id: str, action: Callable[[], Any]) -> Any:
        await self.start()
        if request_id in self._queued:
            raise ValueError("A request with this client_request_id is already active")
        if self._queue.full():
            raise RequestQueueFull("Agent worker queue is full")

        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        job = QueuedRequest(request_id=request_id, action=action, future=future)
        self._queued[request_id] = job
        self._queue.put_nowait(job)
        self._publish(request_id, "queued", queue_depth=self._queue.qsize())
        return await asyncio.shield(future)

    async def pause(self, request_id: str) -> bool:
        request_control.pause(request_id)
        job = self._queued.get(request_id)
        if job is not None and not job.started and not job.future.done():
            job.future.set_exception(RequestPaused("Request paused by user"))
            self._queued.pop(request_id, None)
            self._publish(request_id, "paused", phase="queued")
            return True
        self._publish(request_id, "pause_requested", phase="running")
        return False

    def status(self, request_id: str) -> dict[str, Any] | None:
        events = self._events.get(request_id)
        return events[-1] if events else None

    async def events(self, request_id: str) -> AsyncIterator[dict[str, Any]]:
        history = list(self._events.get(request_id, ()))
        for event in history:
            yield event
        if history and history[-1]["status"] in self.TERMINAL_STATUSES:
            return

        subscriber: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=16)
        self._subscribers[request_id].add(subscriber)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(subscriber.get(), timeout=15)
                except TimeoutError:
                    yield {"event": "heartbeat", "request_id": request_id, "timestamp": datetime.now(timezone.utc).isoformat()}
                    continue
                yield event
                if event["status"] in self.TERMINAL_STATUSES:
                    return
        finally:
            self._subscribers[request_id].discard(subscriber)
            if not self._subscribers[request_id]:
                self._subscribers.pop(request_id, None)

    async def _worker(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                if job is None:
                    return
                if job.future.done() or request_control.is_paused(job.request_id):
                    if not job.future.done():
                        job.future.set_exception(RequestPaused("Request paused before execution"))
                    self._publish(job.request_id, "paused", phase="queued")
                    continue

                job.started = True
                self._publish(job.request_id, "running")
                try:
                    result = await asyncio.wait_for(
                        asyncio.to_thread(self._run_sync_job, job), timeout=self.timeout_seconds
                    )
                except TimeoutError:
                    job.timed_out = True
                    request_control.pause(job.request_id)
                    if not job.future.done():
                        job.future.set_exception(RequestTimedOut("Agent request exceeded the execution timeout"))
                    self._publish(job.request_id, "timed_out", timeout_seconds=self.timeout_seconds)
                except RequestPaused as exc:
                    if not job.future.done():
                        job.future.set_exception(exc)
                    self._publish(job.request_id, "paused", phase="running")
                except Exception as exc:
                    if not job.future.done():
                        job.future.set_exception(exc)
                    self._publish(job.request_id, "failed", error=str(exc))
                else:
                    if not job.future.done():
                        job.future.set_result(result)
                    self._publish(job.request_id, "completed")
            finally:
                if job is not None:
                    self._queued.pop(job.request_id, None)
                self._queue.task_done()

    @staticmethod
    def _run_sync_job(job: QueuedRequest) -> Any:
        try:
            return job.action()
        finally:
            # A timed-out thread exits only after the workflow reaches a cooperative pause check.
            if job.timed_out:
                request_control.clear(job.request_id)


request_runtime = RequestRuntime()
