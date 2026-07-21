
import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
import uvicorn
import sys
import os
import time
from pathlib import Path
from uuid import uuid4
from starlette.exceptions import HTTPException as StarletteHTTPException

# 将项目根目录加入模块检索路径，以便顺利导入 agents 模块
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from agents.workflow import app as agent_app
from core_engine.data_source_registry import DataSourceError, create_data_source_registry
from core_engine.database_connectors import ConnectorError, direct_connector_registry
from core_engine.access_control import AccessDeniedError, Principal, access_control
from core_engine.evaluation import EvaluationCaseError, load_golden_cases, score_final_state, summarize_scores
from core_engine.durable_tasks import DurableTaskUnavailable, TERMINAL_STATUSES, durable_task_queue
from core_engine.request_control import RequestPaused, request_control
from core_engine.request_runtime import RequestQueueFull, RequestTimedOut, request_runtime
from core_engine.runtime_config import get_runtime_config
from core_engine.telemetry import telemetry
from langchain_core.messages import HumanMessage
from memory import memory_store, semantic_memory_store
from observability import observability_store

data_source_registry = create_data_source_registry(Path(BASE_DIR))
model_runtime_config = get_runtime_config()
LEGACY_DATA_SOURCE_IDS = {"sqlite": "sqlite_local", "federated": "federated_demo"}

@asynccontextmanager
async def lifespan(_: FastAPI):
    telemetry.configure()
    model_status = model_runtime_config.public_status()
    print(
        "[Startup] "
        f"llm={model_status['status']} model={model_status['model']} "
        f"credential_source={model_status['credential_source']} "
        f"state_backend={'postgresql' if os.getenv('RUNTIME_DATABASE_URL', '').strip() else 'sqlite'} "
        f"task_mode={durable_task_queue.mode}"
    )
    if durable_task_queue.mode != "redis_streams":
        reconciled = observability_store.reconcile_stale_running_requests(
            int(os.getenv("REQUEST_STALE_AFTER_SECONDS", "300"))
        )
        if reconciled:
            print(f"[Observability] marked {reconciled} stale running requests as aborted")
    await request_runtime.start()
    yield
    await request_runtime.stop()
    direct_connector_registry.dispose_all()


app = FastAPI(title="Data Agent 真实数据库查询 API", version="2.1", lifespan=lifespan)

allowed_origins = [
    value.strip()
    for value in os.getenv("API_ALLOWED_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8000,http://localhost:8000").split(",")
    if value.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


HTTP_ERROR_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    503: "SERVICE_UNAVAILABLE",
    504: "GATEWAY_TIMEOUT",
}


def _safe_request_identifier(value: str | None, fallback: str | None = None) -> str:
    candidate = (value or fallback or "").strip()
    return candidate[:128] if candidate and candidate.replace("-", "").replace("_", "").isalnum() else uuid4().hex


def _api_error(
    request: Request | None,
    status_code: int,
    error_code: str,
    message: str,
    *,
    request_id: str | None = None,
    retryable: bool = False,
    details: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse | dict[str, object]:
    correlation_id = request_id or getattr(getattr(request, "state", None), "request_id", None) or uuid4().hex
    idempotency_key = getattr(getattr(request, "state", None), "idempotency_key", None)
    payload: dict[str, object] = {
        "code": status_code,
        "error": {
            "code": error_code,
            "type": HTTP_ERROR_CODES.get(status_code, "API_ERROR"),
            "message": message,
            "request_id": correlation_id,
            "idempotency_key": idempotency_key,
            "retryable": retryable,
            "details": details or {},
        },
    }
    if request is None:
        return payload
    return JSONResponse(status_code=status_code, content=payload, headers=headers)


@app.middleware("http")
async def attach_request_metadata(request: Request, call_next):
    request.state.request_id = _safe_request_identifier(request.headers.get("X-Request-ID"))
    request.state.idempotency_key = _safe_request_identifier(request.headers.get("Idempotency-Key"), "") if request.headers.get("Idempotency-Key") else None
    with telemetry.span(
        "http.request",
        {
            "http.request.method": request.method,
            "url.path": request.url.path,
            "data_agent.request_id": request.state.request_id,
        },
    ) as span:
        response = await call_next(request)
        if span is not None:
            span.set_attribute("http.response.status_code", response.status_code)
    response.headers["X-Request-ID"] = request.state.request_id
    trace_id = telemetry.current_trace_id()
    if trace_id:
        response.headers["X-Trace-ID"] = trace_id
    if request.state.idempotency_key:
        response.headers["Idempotency-Key"] = request.state.idempotency_key
    return response


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return _api_error(
        request,
        422,
        "VALIDATION_ERROR",
        "Request validation failed.",
        details={"fields": exc.errors()},
    )


@app.exception_handler(AccessDeniedError)
async def access_denied_handler(request: Request, exc: AccessDeniedError):
    return _api_error(request, 403, "FORBIDDEN", "You are not authorized for this resource.")


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    message = exc.detail if isinstance(exc.detail, str) else "Request could not be completed."
    status_code = int(exc.status_code)
    return _api_error(
        request,
        status_code,
        HTTP_ERROR_CODES.get(status_code, "HTTP_ERROR"),
        message,
        retryable=status_code in {429, 503, 504},
        headers=dict(exc.headers or {}),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    print(f"[API] unhandled error request_id={getattr(request.state, 'request_id', '')}: {exc}")
    return _api_error(request, 500, "INTERNAL_ERROR", "An unexpected server error occurred.", retryable=True)


@app.get("/health/live", include_in_schema=False)
async def liveness_probe():
    """Report whether the API process can receive traffic."""
    return {"status": "live"}


@app.get("/health/ready", include_in_schema=False)
async def readiness_probe():
    """Report model, queue, state-store and data-source readiness safely."""
    components: dict[str, object] = {
        "model": model_runtime_config.public_status(),
        "request_runtime": {"status": "ready" if request_runtime._started else "not_ready"},
        "task_queue": {
            "status": "ready" if not durable_task_queue.api_enabled or durable_task_queue.available() else "not_ready",
            "mode": durable_task_queue.mode,
        },
        "state_store": {
            "status": "checking",
            "backend": "postgresql" if os.getenv("RUNTIME_DATABASE_URL", "").strip() else "sqlite",
        },
    }
    try:
        enabled_sources = data_source_registry.list_enabled()
    except DataSourceError as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": str(exc), "components": components},
        )

    components["data_sources"] = {"status": "ready", "enabled": len(enabled_sources)}
    if not model_runtime_config.llm_configured:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "reason": "OPENAI_API_KEY or DASHSCOPE_API_KEY is not configured",
                "components": components,
            },
        )

    if not request_runtime._started:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": "request runtime is not started", "components": components},
        )
    if durable_task_queue.api_enabled and not durable_task_queue.available():
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": "Redis Streams task queue is unavailable", "components": components},
        )
    try:
        # Each call opens a fresh state-store connection, so this also catches a
        # PostgreSQL outage after the API process has started.
        memory_store.get_preferences("__runtime_healthcheck__")
        observability_store.get_request("__runtime_healthcheck__")
    except Exception as exc:
        components["state_store"] = {
            "status": "not_ready",
            "backend": components["state_store"]["backend"],
        }
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "reason": f"runtime state storage is unavailable: {exc}",
                "components": components,
            },
        )
    components["state_store"] = {
        "status": "ready",
        "backend": components["state_store"]["backend"],
    }
    return {"status": "ready", "enabled_data_sources": len(enabled_sources), "components": components}


# 定义前端传入的数据结构
class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=8_000)
    data_source_id: str | None = None
    conversation_id: str | None = None
    clarification_parent_request_id: str | None = None
    client_request_id: str | None = None
    target_db: str | None = None


class PreferenceUpdateRequest(BaseModel):
    default_data_source_id: str | None = None
    response_style: str | None = None
    memory_enabled: bool | None = None
    show_sql: bool | None = None
    default_limit: int | None = None
    metric_aliases: dict[str, str] | None = None


class SemanticMemoryRequest(BaseModel):
    kind: str
    label: str
    definition: str
    data_source_scope: str | None = None
    sql_template: str | None = None
    metadata: dict[str, object] | None = None
    retention_days: int | None = None
    confirmed: bool = False


class UserFeedbackRequest(BaseModel):
    satisfied: bool
    note: str | None = None


class ResultEvaluationRequest(BaseModel):
    correct: bool
    note: str | None = None


class EvaluationRunRequest(BaseModel):
    case_ids: list[str] | None = None
    include_extended: bool = False


def _audit(
    principal: Principal,
    action: str,
    resource_type: str,
    outcome: str,
    *,
    resource_id: str | None = None,
    request_id: str | None = None,
    details: dict[str, object] | None = None,
) -> None:
    try:
        observability_store.record_audit_event(
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
            role=principal.role,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            request_id=request_id,
            outcome=outcome,
            details=details,
        )
    except Exception as exc:
        print(f"[Audit] event skipped: {exc}")


def _authorize_data_source(principal: Principal, data_source_id: str) -> None:
    try:
        access_control.authorize_data_source(principal, data_source_id)
    except AccessDeniedError as exc:
        _audit(principal, "data_source_access", "data_source", "denied", resource_id=data_source_id, details={"reason": str(exc)})
        raise HTTPException(status_code=403, detail="You are not authorized for this data source") from exc


def _authorize_request_owner(principal: Principal, request_id: str) -> None:
    request_data = observability_store.get_request(request_id)
    owner_id = (request_data or {}).get("request", {}).get("user_id")
    if owner_id is None and durable_task_queue.api_enabled:
        try:
            owner_id = durable_task_queue.owner_id(request_id)
        except DurableTaskUnavailable:
            owner_id = None
    if owner_id is None:
        raise HTTPException(status_code=404, detail="Request not found")
    access_control.require_self_or_admin(principal, str(owner_id))


@app.post("/api/v1/query")
async def query_database(
    request: QueryRequest,
    principal: Principal = Depends(access_control.current_principal),
    http_request: Request = None,
):
    if not model_runtime_config.llm_configured:
        raise HTTPException(
            status_code=503,
            detail="LLM is not configured. Set OPENAI_API_KEY or DASHSCOPE_API_KEY and restart the service.",
        )
    print(f"\n[API] received query: {request.query}")
    user_preferences = memory_store.get_preferences(principal.user_id)
    memory_enabled = bool(user_preferences.get("memory_enabled", True))
    data_source_id = (
        request.data_source_id
        or (user_preferences.get("default_data_source_id") if memory_enabled else None)
        or LEGACY_DATA_SOURCE_IDS.get(request.target_db or "", "sqlite_local")
    )
    retain_pause_marker = False
    try:
        data_source = data_source_registry.resolve(data_source_id)
    except DataSourceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _authorize_data_source(principal, data_source_id)

    header_idempotency_key = getattr(getattr(http_request, "state", None), "idempotency_key", None)
    request_id = (request.client_request_id or header_idempotency_key or uuid4().hex).strip()
    if not request_id or len(request_id) > 128:
        raise HTTPException(status_code=422, detail="Invalid client_request_id")
    if http_request is not None:
        http_request.state.idempotency_key = request_id
    if request.clarification_parent_request_id:
        _authorize_request_owner(principal, request.clarification_parent_request_id)
    execution_mode = str(data_source["execution_mode"])
    if durable_task_queue.api_enabled:
        try:
            existing_task = durable_task_queue.get(request_id)
        except DurableTaskUnavailable as exc:
            raise HTTPException(status_code=503, detail="Durable task queue is unavailable") from exc
        if existing_task is not None:
            if existing_task.get("user_id") != principal.user_id:
                access_control.require_self_or_admin(principal, str(existing_task.get("user_id", "")))
            return JSONResponse(
                status_code=202,
                content={
                    "code": 202,
                    "data": {
                        "request_id": request_id,
                        "status": existing_task["public"]["status"],
                        "status_url": f"/api/v1/requests/{request_id}/status",
                        "result_url": f"/api/v1/requests/{request_id}/result",
                    },
                },
            )
        request_control.clear(request_id)
        observability_store.start_request(
            request_id,
            principal.user_id,
            request.query,
            execution_mode,
            data_source_id=data_source_id,
            conversation_id=request.conversation_id,
            clarification_parent_request_id=request.clarification_parent_request_id,
            tenant_id=principal.tenant_id,
            role=principal.role,
            status="queued",
        )
        try:
            trace_context: dict[str, str] = {}
            telemetry.inject(trace_context)
            task = durable_task_queue.enqueue(
                request_id,
                principal.user_id,
                {
                    "request": request.model_dump(),
                    "principal": principal.public(),
                    "trace_context": trace_context,
                },
            )
        except DurableTaskUnavailable as exc:
            observability_store.complete_request(
                request_id, "queue_unavailable", 0, 0, str(exc), technical_success=False
            )
            raise HTTPException(status_code=503, detail="Durable task queue is unavailable") from exc
        _audit(principal, "query", "data_source", "queued", resource_id=data_source_id, request_id=request_id)
        return JSONResponse(
            status_code=202,
            content={
                "code": 202,
                "data": {
                    "request_id": request_id,
                    "status": task["public"]["status"],
                    "status_url": f"/api/v1/requests/{request_id}/status",
                    "result_url": f"/api/v1/requests/{request_id}/result",
                },
            },
        )
    request_control.clear(request_id)
    started = time.perf_counter()
    hot_context = (
        memory_store.get_hot_context(request.conversation_id, user_id=principal.user_id)
        if memory_enabled
        else {"recent_turns": [], "checkpoint": {}, "metadata": {}}
    )
    conversation_context = hot_context["recent_turns"]
    observability_store.start_request(
        request_id,
        principal.user_id,
        request.query,
        execution_mode,
        data_source_id=data_source_id,
        conversation_id=request.conversation_id,
        clarification_parent_request_id=request.clarification_parent_request_id,
        tenant_id=principal.tenant_id,
        role=principal.role,
    )
    _audit(principal, "query", "data_source", "started", resource_id=data_source_id, request_id=request_id)

    # 构造传递给 LangGraph 的初始状态
    initial_state = {
        "messages": [HumanMessage(content=request.query)],
        "request_id": request_id,
        "user_id": principal.user_id,
        "principal": principal,
        "data_source_id": data_source_id,
        "target_db_type": execution_mode,
        "conversation_context": conversation_context,
        "conversation_checkpoint": hot_context["checkpoint"],
        "conversation_context_metadata": hot_context["metadata"],
        "user_preferences": user_preferences if memory_enabled else {},
        "memory_enabled": memory_enabled,
        "error_count": 0,
        "similarity_threshold": 0.8
    }

    try:
        # 调用 LangGraph 核心工作流
        final_state = await request_runtime.submit(request_id, lambda: agent_app.invoke(initial_state))
        if request_control.is_paused(request_id):
            raise RequestPaused("Request paused by user")

        # 提取 AI 最后一步生成的数据分析报告
        answer = final_state["messages"][-1].content
        execution_failed = final_state.get("execution_result", "").startswith("ERROR:") or final_state.get(
            "validation_result", ""
        ).startswith("ERROR:")
        is_sql_request = final_state.get("intent") in {"text_to_sql", "data_analysis"}
        sql_executable = (
            bool(final_state.get("generated_sql"))
            and final_state.get("validation_result") == "VALID"
            and not final_state.get("execution_result", "").startswith("ERROR:")
        ) if is_sql_request else None
        observability_store.complete_request(
            request_id=request_id,
            status="failed" if execution_failed else "success",
            latency_ms=(time.perf_counter() - started) * 1000,
            retries=final_state.get("error_count", 0),
            error_message=final_state.get("execution_result") if execution_failed else None,
            technical_success=not execution_failed,
            sql_executable=sql_executable,
        )
        if (
            request.clarification_parent_request_id
            and not execution_failed
            and final_state.get("intent") in {"text_to_sql", "data_analysis"}
        ):
            observability_store.mark_clarification_resolved(request.clarification_parent_request_id)
        if memory_enabled:
            memory_store.record_turn(
                conversation_id=request.conversation_id,
                user_id=principal.user_id,
                data_source_id=data_source_id,
                user_query=request.query,
                intent=final_state.get("intent", ""),
                entities=final_state.get("entities", {}),
                answer=answer,
                generated_sql=final_state.get("generated_sql", ""),
                execution_failed=execution_failed,
                error_detail=(
                    final_state.get("validation_result", "")
                    if final_state.get("validation_result", "").startswith("ERROR:")
                    else final_state.get("execution_result", "")
                ),
            )
        if request.data_source_id:
            user_preferences = memory_store.update_preferences(
                principal.user_id, {"default_data_source_id": data_source_id}
            )
        semantic_template = None
        if memory_enabled and not execution_failed and final_state.get("intent") in {"text_to_sql", "data_analysis"}:
            try:
                semantic_template = semantic_memory_store.save_successful_sql_template(
                    user_id=principal.user_id,
                    data_source_id=data_source_id,
                    sql=final_state.get("generated_sql", ""),
                    intent=final_state.get("intent", ""),
                    entities=final_state.get("entities", {}),
                )
            except Exception as exc:
                print(f"[Memory] semantic template skipped: {exc}")
        observability_data = observability_store.get_request(request_id) or {}
        llm_calls = observability_data.get("llm_calls", [])
        token_usage = {
            "llm_calls": len(llm_calls),
            "input_tokens": sum(call.get("input_tokens") or 0 for call in llm_calls),
            "output_tokens": sum(call.get("output_tokens") or 0 for call in llm_calls),
            "total_tokens": sum(call.get("total_tokens") or 0 for call in llm_calls),
            "llm_latency_ms": round(sum(call.get("latency_ms") or 0 for call in llm_calls), 2),
        }

        _audit(
            principal,
            "query",
            "data_source",
            "failed" if execution_failed else "success",
            resource_id=data_source_id,
            request_id=request_id,
            details={"intent": final_state.get("intent", ""), "retries": final_state.get("error_count", 0)},
        )

        return {
            "code": 200,
            "data": {
                "answer": answer,
                "metrics": {
                    "data_source": {
                        "id": data_source_id,
                        "name": data_source["name"],
                        "engine": data_source["engine"],
                        "status": data_source["status"],
                    },
                    "memory": {
                        "context_turns": len(conversation_context),
                        "available_turns": hot_context["metadata"].get("available_turns", 0),
                        "hot_context_chars": hot_context["metadata"].get("used_chars", 0),
                        "hot_context_budget_chars": hot_context["metadata"].get("char_budget", 0),
                        "older_turns_omitted": hot_context["metadata"].get("older_turns_omitted", False),
                        "checkpoint_available": bool(hot_context["checkpoint"]),
                        "enabled": memory_enabled,
                        "default_data_source_id": user_preferences.get("default_data_source_id"),
                        "semantic_memories_used": len(final_state.get("semantic_memories", [])),
                        "semantic_template_saved": bool(semantic_template),
                    },
                    "intent": {
                        "name": final_state.get("intent", ""),
                        "confidence": final_state.get("intent_confidence", 0.0),
                        "reason": final_state.get("intent_reason", ""),
                        "entities": final_state.get("entities", {}),
                        "needs_clarification": final_state.get("needs_clarification", False),
                        "clarification_question": final_state.get("clarification_question", ""),
                    },
                    "retries_triggered": final_state.get("error_count", 0),
                    "final_threshold": final_state.get("similarity_threshold", 0.8),
                    "executed_sql": final_state.get("generated_sql", ""),
                    "query_plan": final_state.get("query_plan", {}),
                    "retrieval": final_state.get("retrieval_metadata", {}),
                    "candidate_count": len(final_state.get("candidate_sqls", [])),
                    "selected_candidate_index": final_state.get("selected_candidate_index"),
                    "validation_result": final_state.get("validation_result", ""),
                    "execution_trace": final_state.get("execution_trace", []),
                    "observability_request_id": request_id,
                    "token_usage": token_usage,
                }
            }
        }
    except RequestTimedOut:
        retain_pause_marker = True
        observability_store.complete_request(
            request_id=request_id,
            status="timed_out",
            latency_ms=(time.perf_counter() - started) * 1000,
            retries=0,
            error_message="Agent request exceeded the execution timeout",
            technical_success=False,
        )
        _audit(principal, "query", "data_source", "timed_out", resource_id=data_source_id, request_id=request_id)
        return _api_error(
            http_request,
            504,
            "REQUEST_TIMED_OUT",
            "The request exceeded the execution timeout. Narrow the query and retry.",
            request_id=request_id,
            retryable=True,
            details={"runtime_status": "timed_out"},
        )
    except RequestQueueFull:
        observability_store.complete_request(
            request_id=request_id,
            status="queue_rejected",
            latency_ms=(time.perf_counter() - started) * 1000,
            retries=0,
            error_message="Agent worker queue is full",
            technical_success=False,
        )
        _audit(principal, "query", "data_source", "queue_rejected", resource_id=data_source_id, request_id=request_id)
        return _api_error(
            http_request,
            429,
            "QUERY_QUEUE_FULL",
            "The query queue is busy. Retry shortly.",
            request_id=request_id,
            retryable=True,
            details={"runtime_status": "queue_rejected"},
            headers={"Retry-After": "3"},
        )
    except RequestPaused:
        observability_store.complete_request(
            request_id=request_id,
            status="paused",
            latency_ms=(time.perf_counter() - started) * 1000,
            retries=0,
            error_message="Request paused by user",
            technical_success=False,
        )
        _audit(principal, "query", "data_source", "paused", resource_id=data_source_id, request_id=request_id)
        return _api_error(
            http_request,
            409,
            "REQUEST_PAUSED",
            "The request was paused.",
            request_id=request_id,
            retryable=True,
            details={"runtime_status": "paused"},
        )
    except Exception as e:
        observability_store.complete_request(
            request_id=request_id,
            status="failed",
            latency_ms=(time.perf_counter() - started) * 1000,
            retries=0,
            error_message=str(e),
            technical_success=False,
        )
        _audit(principal, "query", "data_source", "failed", resource_id=data_source_id, request_id=request_id, details={"error": str(e)})
        return _api_error(
            http_request,
            500,
            "QUERY_EXECUTION_ERROR",
            "The query could not be completed due to an internal error.",
            request_id=request_id,
            retryable=True,
        )
    finally:
        if not retain_pause_marker:
            request_control.clear(request_id)


@app.get("/api/v1/data-sources")
async def list_data_sources(principal: Principal = Depends(access_control.current_principal)):
    sources = access_control.visible_sources(principal, data_source_registry.list_enabled())
    _audit(principal, "list", "data_source", "success", details={"count": len(sources)})
    return {"code": 200, "data": sources}


@app.get("/api/v1/session")
async def current_session(principal: Principal = Depends(access_control.current_principal)):
    return {"code": 200, "data": principal.public()}


@app.get("/api/v1/data-sources/{data_source_id}/health")
async def check_data_source_health(
    data_source_id: str,
    principal: Principal = Depends(access_control.current_principal),
    http_request: Request = None,
):
    """Probe one enabled direct connector without exposing its URI or credentials."""
    try:
        source = data_source_registry.resolve(data_source_id)
    except DataSourceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _authorize_data_source(principal, data_source_id)

    if source.get("execution_mode") != "direct":
        return {
            "code": 200,
            "data": {
                "data_source_id": data_source_id,
                "healthy": None,
                "detail": "Health probes are managed by the local SQLite or DuckDB federation runtime.",
            },
        }

    try:
        health = direct_connector_registry.connector_for(source).health_check()
    except ConnectorError as exc:
        health = {"healthy": False, "engine": source.get("engine"), "error": str(exc)}
    if not health.get("healthy"):
        return _api_error(
            http_request,
            503,
            "DATA_SOURCE_UNAVAILABLE",
            "The data source health check failed.",
            retryable=True,
            details={"data_source_id": data_source_id, "engine": source.get("engine")},
        )
    return {"code": 200, "data": {"data_source_id": data_source_id, **health}}


@app.get("/api/v1/memory/preferences/{user_id}")
async def get_user_preferences(user_id: str, principal: Principal = Depends(access_control.current_principal)):
    access_control.require_self_or_admin(principal, user_id)
    return {"code": 200, "data": memory_store.get_preferences(user_id)}


@app.put("/api/v1/memory/preferences/{user_id}")
async def update_user_preferences(
    user_id: str, request: PreferenceUpdateRequest, principal: Principal = Depends(access_control.current_principal)
):
    access_control.require_self_or_admin(principal, user_id)
    updates = request.model_dump(exclude_none=True)
    data_source_id = updates.get("default_data_source_id")
    if data_source_id:
        try:
            data_source_registry.resolve(data_source_id)
        except DataSourceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _authorize_data_source(principal, data_source_id)
    try:
        result = memory_store.update_preferences(user_id, updates)
        _audit(principal, "update", "preferences", "success", resource_id=user_id)
        return {"code": 200, "data": result}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/api/v1/memory/{user_id}")
async def delete_user_memory(user_id: str, principal: Principal = Depends(access_control.current_principal)):
    access_control.require_self_or_admin(principal, user_id)
    conversation = memory_store.delete_user_memory(user_id, include_preferences=True)
    semantic_count = semantic_memory_store.delete_all(user_id)
    _audit(principal, "delete", "memory", "success", resource_id=user_id)
    return {"code": 200, "data": {**conversation, "semantic_memories": semantic_count}}


@app.post("/api/v1/requests/{request_id}/pause")
async def pause_request(request_id: str, principal: Principal = Depends(access_control.current_principal)):
    _authorize_request_owner(principal, request_id)
    if durable_task_queue.api_enabled:
        try:
            task = durable_task_queue.request_pause(request_id)
        except DurableTaskUnavailable as exc:
            raise HTTPException(status_code=503, detail="Durable task queue is unavailable") from exc
        if task is not None:
            request_control.pause(request_id)
            status = str(task["public"]["status"])
            if status == "paused":
                observability_store.complete_request(
                    request_id, "paused", 0, 0, "Request paused before worker execution", technical_success=False
                )
            _audit(principal, "pause", "request", "success", resource_id=request_id)
            return {
                "code": 200,
                "data": {"request_id": request_id, "paused": True, "queued": status == "paused", "status": status},
            }
    queued = await request_runtime.pause(request_id)
    _audit(principal, "pause", "request", "success", resource_id=request_id)
    return {"code": 200, "data": {"request_id": request_id, "paused": True, "queued": queued}}


@app.get("/api/v1/requests/{request_id}/status")
async def request_status(request_id: str, principal: Principal = Depends(access_control.current_principal)):
    _authorize_request_owner(principal, request_id)
    if durable_task_queue.api_enabled:
        try:
            task = durable_task_queue.get(request_id)
        except DurableTaskUnavailable as exc:
            raise HTTPException(status_code=503, detail="Durable task queue is unavailable") from exc
        if task is not None:
            return {"code": 200, "data": task["public"]}
    status = request_runtime.status(request_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Request status not found")
    return {"code": 200, "data": status}


@app.get("/api/v1/requests/{request_id}/events")
async def request_events(request_id: str, principal: Principal = Depends(access_control.current_principal)):
    _authorize_request_owner(principal, request_id)
    if durable_task_queue.api_enabled:
        async def durable_event_stream():
            last_status = ""
            while True:
                try:
                    task = durable_task_queue.get(request_id)
                except DurableTaskUnavailable:
                    yield f"event: status\ndata: {json.dumps({'request_id': request_id, 'status': 'unavailable'})}\n\n"
                    return
                if task is None:
                    return
                status = str(task["public"]["status"])
                if status != last_status:
                    yield f"event: status\ndata: {json.dumps(task['public'], ensure_ascii=False)}\n\n"
                    last_status = status
                if status in TERMINAL_STATUSES:
                    return
                await asyncio.sleep(1)

        return StreamingResponse(
            durable_event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )
    async def event_stream():
        async for event in request_runtime.events(request_id):
            yield f"event: {event['event']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.get("/api/v1/requests/{request_id}/result")
async def request_result(
    request_id: str,
    principal: Principal = Depends(access_control.current_principal),
    http_request: Request = None,
):
    """Return a completed durable task result or a 202 lifecycle snapshot while it is pending."""
    _authorize_request_owner(principal, request_id)
    if not durable_task_queue.api_enabled:
        raise HTTPException(status_code=404, detail="Durable task execution is not enabled")
    try:
        task = durable_task_queue.get(request_id)
    except DurableTaskUnavailable as exc:
        raise HTTPException(status_code=503, detail="Durable task queue is unavailable") from exc
    if task is None:
        raise HTTPException(status_code=404, detail="Request task not found")
    status = str(task["public"]["status"])
    result = task.get("result")
    if isinstance(result, dict):
        result_code = int(result.get("code", 200))
        if result_code >= 400:
            return JSONResponse(status_code=result_code, content=result)
        return result
    if status in TERMINAL_STATUSES:
        error_status = 409 if status == "paused" else 500
        return _api_error(
            http_request,
            error_status,
            "REQUEST_PAUSED" if status == "paused" else "REQUEST_RESULT_MISSING",
            "The request did not produce a result.",
            request_id=request_id,
            retryable=status != "failed",
            details={"runtime_status": status},
        )
    return JSONResponse(status_code=202, content={"code": 202, "data": task["public"]})


@app.post("/api/v1/requests/{request_id}/feedback")
async def submit_request_feedback(
    request_id: str, feedback: UserFeedbackRequest, principal: Principal = Depends(access_control.current_principal)
):
    _authorize_request_owner(principal, request_id)
    observability_store.record_quality_feedback(
        request_id, user_satisfied=feedback.satisfied, note=feedback.note
    )
    _audit(
        principal,
        "feedback",
        "request",
        "success",
        resource_id=request_id,
        details={"satisfied": feedback.satisfied},
    )
    return {"code": 200, "data": {"request_id": request_id, "satisfied": feedback.satisfied}}


@app.post("/api/v1/requests/{request_id}/result-evaluation")
async def evaluate_request_result(
    request_id: str, evaluation: ResultEvaluationRequest, principal: Principal = Depends(access_control.current_principal)
):
    access_control.require_admin(principal)
    if observability_store.get_request(request_id) is None:
        raise HTTPException(status_code=404, detail="Request not found")
    observability_store.record_quality_feedback(
        request_id, result_correct=evaluation.correct, note=evaluation.note
    )
    _audit(
        principal,
        "result_evaluation",
        "request",
        "success",
        resource_id=request_id,
        details={"correct": evaluation.correct},
    )
    return {"code": 200, "data": {"request_id": request_id, "correct": evaluation.correct}}


@app.get("/api/v1/memory/semantic")
async def search_semantic_memory(
    user_id: str,
    query: str,
    data_source_id: str | None = None,
    limit: int = 4,
    principal: Principal = Depends(access_control.current_principal),
):
    access_control.require_self_or_admin(principal, user_id)
    if data_source_id:
        _authorize_data_source(principal, data_source_id)
    return {
        "code": 200,
        "data": semantic_memory_store.search(user_id, query, data_source_id, limit=limit),
    }


@app.post("/api/v1/memory/semantic/{user_id}")
async def confirm_semantic_memory(
    user_id: str, request: SemanticMemoryRequest, principal: Principal = Depends(access_control.current_principal)
):
    access_control.require_self_or_admin(principal, user_id)
    if not request.confirmed:
        raise HTTPException(status_code=422, detail="Semantic memory must be explicitly confirmed")
    if request.data_source_scope:
        try:
            data_source_registry.resolve(request.data_source_scope)
        except DataSourceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _authorize_data_source(principal, request.data_source_scope)
    try:
        stored = semantic_memory_store.upsert(
            user_id=user_id,
            kind=request.kind,
            label=request.label,
            definition=request.definition,
            data_source_scope=request.data_source_scope,
            sql_template=request.sql_template,
            metadata=request.metadata,
            confirmed=True,
            retention_days=request.retention_days,
        )
        if request.kind == "field_alias":
            preferences = memory_store.get_preferences(user_id)
            aliases = {**preferences["metric_aliases"], request.label: request.definition}
            memory_store.update_preferences(user_id, {"metric_aliases": aliases})
        _audit(principal, "create", "semantic_memory", "success", resource_id=stored.get("memory_id"))
        return {"code": 200, "data": stored}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/api/v1/memory/semantic/{user_id}/{memory_id}")
async def delete_semantic_memory(
    user_id: str, memory_id: str, principal: Principal = Depends(access_control.current_principal)
):
    access_control.require_self_or_admin(principal, user_id)
    if not semantic_memory_store.delete(user_id, memory_id):
        raise HTTPException(status_code=404, detail="Semantic memory not found")
    _audit(principal, "delete", "semantic_memory", "success", resource_id=memory_id)
    return {"code": 200, "data": {"deleted": True}}


@app.get("/api/v1/observability/summary")
async def observability_summary(
    window_hours: int = 24, principal: Principal = Depends(access_control.current_principal)
):
    access_control.require_admin(principal)
    _audit(principal, "read", "observability_summary", "success")
    return {"code": 200, "data": observability_store.summary(window_hours)}


@app.get("/api/v1/observability/requests/{request_id}")
async def observability_request(request_id: str, principal: Principal = Depends(access_control.current_principal)):
    data = observability_store.get_request(request_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Observability request not found")
    access_control.require_self_or_admin(principal, str(data["request"].get("user_id", "")))
    _audit(principal, "read", "observability_request", "success", resource_id=request_id)
    return {"code": 200, "data": data}


@app.get("/api/v1/audit-events")
async def audit_events(limit: int = 100, principal: Principal = Depends(access_control.current_principal)):
    access_control.require_admin(principal)
    _audit(principal, "read", "audit_log", "success", details={"limit": limit})
    return {"code": 200, "data": observability_store.list_audit_events(limit=limit)}


@app.get("/api/v1/evaluation/cases")
async def evaluation_cases(
    include_extended: bool = False, principal: Principal = Depends(access_control.current_principal)
):
    access_control.require_admin(principal)
    try:
        suite_name, cases = load_golden_cases(Path(BASE_DIR), include_extended=include_extended)
    except (OSError, EvaluationCaseError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    _audit(principal, "read", "evaluation_cases", "success", details={"count": len(cases)})
    return {"code": 200, "data": {"suite_name": suite_name, "cases": cases}}


@app.post("/api/v1/evaluation/run")
async def run_evaluation(
    request: EvaluationRunRequest, principal: Principal = Depends(access_control.current_principal)
):
    access_control.require_admin(principal)
    try:
        suite_name, cases = load_golden_cases(Path(BASE_DIR), include_extended=request.include_extended)
    except (OSError, EvaluationCaseError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if request.case_ids:
        requested_ids = set(request.case_ids)
        cases = [case for case in cases if str(case["id"]) in requested_ids]
        if len(cases) != len(requested_ids):
            raise HTTPException(status_code=422, detail="One or more evaluation case ids are unavailable")
    if not cases:
        raise HTTPException(status_code=422, detail="No evaluation cases selected")

    run_id = f"eval-{uuid4().hex}"
    observability_store.start_evaluation_run(run_id, suite_name, len(cases))
    passed_cases = 0
    results: list[dict[str, object]] = []
    for case in cases:
        case_id = str(case["id"])
        request_id = f"{run_id}-{case_id}"
        try:
            data_source_id = str(case.get("data_source_id", "sqlite_local"))
            _authorize_data_source(principal, data_source_id)
            source = data_source_registry.resolve(data_source_id)
            observability_store.start_request(
                request_id,
                principal.user_id,
                str(case["query"]),
                str(source["execution_mode"]),
                data_source_id=data_source_id,
                tenant_id=principal.tenant_id,
                role=principal.role,
                is_evaluation=True,
            )
            initial_state = {
                "messages": [HumanMessage(content=str(case["query"]))],
                "request_id": request_id,
                "user_id": principal.user_id,
                "principal": principal,
                "data_source_id": data_source_id,
                "target_db_type": str(source["execution_mode"]),
                "conversation_context": [],
                "user_preferences": {},
                "memory_enabled": False,
                "error_count": 0,
                "similarity_threshold": 0.8,
            }
            final_state = await request_runtime.submit(request_id, lambda: agent_app.invoke(initial_state))
            execution_failed = final_state.get("execution_result", "").startswith("ERROR:") or final_state.get(
                "validation_result", ""
            ).startswith("ERROR:")
            is_sql_request = final_state.get("intent") in {"text_to_sql", "data_analysis"}
            sql_executable = (
                bool(final_state.get("generated_sql"))
                and final_state.get("validation_result") == "VALID"
                and not execution_failed
            ) if is_sql_request else None
            score = score_final_state(case, final_state)
            observability_store.complete_request(
                request_id,
                "failed" if execution_failed else "success",
                0,
                int(final_state.get("error_count", 0)),
                final_state.get("execution_result") if execution_failed else None,
                technical_success=not execution_failed,
                sql_executable=sql_executable,
            )
            observability_store.record_evaluation_case(
                run_id,
                case_id,
                str(score["status"]),
                request_id=request_id,
                intent_expected=str(score["intent_expected"]),
                intent_actual=str(score["intent_actual"]),
                intent_correct=bool(score["intent_correct"]),
                sql_executable=score["sql_executable"],
                result_correct=score["result_correct"],
                details=score["details"],
            )
            if score["status"] == "passed":
                passed_cases += 1
            results.append({"case_id": case_id, "category": case.get("category", "uncategorized"), **score})
        except Exception as exc:
            observability_store.complete_request(
                request_id, "failed", 0, 0, str(exc), technical_success=False
            )
            observability_store.record_evaluation_case(
                run_id, case_id, "error", request_id=request_id, details={"error": str(exc)}
            )
            results.append({"case_id": case_id, "category": case.get("category", "uncategorized"), "status": "error", "details": {"error": str(exc)}})

    summary = summarize_scores(results)
    observability_store.complete_evaluation_run(run_id, passed_cases, summary)
    _audit(principal, "run", "evaluation", "success", resource_id=run_id, details=summary)
    return {"code": 200, "data": {"run_id": run_id, "summary": summary, "results": results}}


@app.get("/api/v1/evaluation/runs/{run_id}")
async def evaluation_run(run_id: str, principal: Principal = Depends(access_control.current_principal)):
    access_control.require_admin(principal)
    result = observability_store.get_evaluation_run(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Evaluation run not found")
    return {"code": 200, "data": result}


def _resolve_frontend_dist_dir():
    for rel in ("frontend/dist", "dist_package/frontend"):
        d = os.path.join(BASE_DIR, rel)
        if os.path.isfile(os.path.join(d, "index.html")):
            return d
    return None


STATIC_DIR = _resolve_frontend_dist_dir()
STATIC_ABS = os.path.abspath(STATIC_DIR) if STATIC_DIR else None

if STATIC_DIR and STATIC_ABS:
    index_html = os.path.join(STATIC_DIR, "index.html")

    @app.get("/", include_in_schema=False)
    async def spa_index():
        return FileResponse(index_html)

    @app.get("/{path_name:path}", include_in_schema=False)
    async def spa_fallback(path_name: str):
        if path_name.startswith("api"):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = os.path.abspath(os.path.join(STATIC_DIR, path_name))
        if not (candidate == STATIC_ABS or candidate.startswith(STATIC_ABS + os.sep)):
            raise HTTPException(status_code=404, detail="Not Found")
        if os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(index_html)


if __name__ == "__main__":
    print("[API] starting FastAPI server...")
    if STATIC_DIR:
        print(f"[API] static frontend found: {STATIC_DIR}")
        print("   浏览器访问: http://127.0.0.1:8000/")
    else:
        print("[API] frontend/dist not found; API-only mode. Build with: cd frontend && npm run build")
    uvicorn.run(app, host="127.0.0.1", port=8000)
