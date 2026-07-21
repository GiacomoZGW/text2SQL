"""Executable Redis Streams worker for durable Data Agent requests."""

import asyncio
import os
import signal

from fastapi import HTTPException

# Must be set before importing api.main so the worker executes instead of re-enqueuing.
os.environ["DATA_AGENT_WORKER_PROCESS"] = "1"

from api.main import HTTP_ERROR_CODES, QueryRequest, _api_error, query_database
from core_engine.access_control import AccessDeniedError, Principal
from core_engine.durable_tasks import DurableTaskWorker, durable_task_queue
from core_engine.request_runtime import request_runtime
from core_engine.telemetry import telemetry


def _payload_principal(payload: dict[str, object]) -> Principal:
    values = payload.get("principal")
    if not isinstance(values, dict):
        raise ValueError("Durable task is missing its principal")
    return Principal(
        user_id=str(values["user_id"]),
        role=str(values["role"]),
        tenant_id=str(values["tenant_id"]),
    )


def _payload_request(payload: dict[str, object]) -> QueryRequest:
    values = payload.get("request")
    if not isinstance(values, dict):
        raise ValueError("Durable task is missing its query request")
    try:
        return QueryRequest.model_validate(values)
    except AttributeError:
        return QueryRequest.parse_obj(values)


def main() -> None:
    if not durable_task_queue.worker_enabled:
        raise RuntimeError("Set TASK_EXECUTION_MODE=redis_streams before starting the durable worker")
    worker = DurableTaskWorker(durable_task_queue)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def handle_signal(*_: object) -> None:
        worker.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    def execute(payload: dict[str, object]) -> dict[str, object]:
        request = _payload_request(payload)
        trace_context = payload.get("trace_context")
        carrier = trace_context if isinstance(trace_context, dict) else None
        with telemetry.span("durable.task", {"data_agent.request_id": request.client_request_id}, carrier=carrier):
            try:
                return loop.run_until_complete(query_database(request, _payload_principal(payload)))
            except HTTPException as exc:
                status_code = int(exc.status_code)
                message = exc.detail if isinstance(exc.detail, str) else "Request could not be completed."
                return _api_error(
                    None,
                    status_code,
                    HTTP_ERROR_CODES.get(status_code, "HTTP_ERROR"),
                    message,
                    request_id=request.client_request_id,
                    retryable=status_code in {429, 503, 504},
                )
            except AccessDeniedError:
                return _api_error(
                    None,
                    403,
                    "FORBIDDEN",
                    "You are not authorized for this resource.",
                    request_id=request.client_request_id,
                )

    try:
        worker.run(execute)
    finally:
        loop.run_until_complete(request_runtime.stop())
        loop.close()


if __name__ == "__main__":
    main()
