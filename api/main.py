
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn
import sys
import os
import time
from pathlib import Path
from uuid import uuid4

# 将项目根目录加入模块检索路径，以便顺利导入 agents 模块
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from agents.workflow import app as agent_app
from core_engine.data_source_registry import DataSourceError, create_data_source_registry
from core_engine.request_control import RequestPaused, request_control
from langchain_core.messages import HumanMessage
from memory import memory_store, semantic_memory_store
from observability import observability_store

data_source_registry = create_data_source_registry(Path(BASE_DIR))
LEGACY_DATA_SOURCE_IDS = {"sqlite": "sqlite_local", "federated": "federated_demo"}

app = FastAPI(title="Data Agent 真实数据库查询 API", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 定义前端传入的数据结构
class QueryRequest(BaseModel):
    user_id: str = "test_user_001"
    query: str
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


@app.post("/api/v1/query")
async def query_database(request: QueryRequest):
    print(f"\n[API] received query: {request.query}")
    user_preferences = memory_store.get_preferences(request.user_id)
    memory_enabled = bool(user_preferences.get("memory_enabled", True))
    data_source_id = (
        request.data_source_id
        or (user_preferences.get("default_data_source_id") if memory_enabled else None)
        or LEGACY_DATA_SOURCE_IDS.get(request.target_db or "", "sqlite_local")
    )
    try:
        data_source = data_source_registry.resolve(data_source_id)
    except DataSourceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    request_id = (request.client_request_id or uuid4().hex).strip()
    if not request_id or len(request_id) > 128:
        raise HTTPException(status_code=422, detail="Invalid client_request_id")
    request_control.clear(request_id)
    started = time.perf_counter()
    execution_mode = str(data_source["execution_mode"])
    conversation_context = memory_store.get_conversation_context(request.conversation_id) if memory_enabled else []
    observability_store.start_request(
        request_id,
        request.user_id,
        request.query,
        execution_mode,
        data_source_id=data_source_id,
        conversation_id=request.conversation_id,
        clarification_parent_request_id=request.clarification_parent_request_id,
    )

    # 构造传递给 LangGraph 的初始状态
    initial_state = {
        "messages": [HumanMessage(content=request.query)],
        "request_id": request_id,
        "user_id": request.user_id,
        "data_source_id": data_source_id,
        "target_db_type": execution_mode,
        "conversation_context": conversation_context,
        "user_preferences": user_preferences if memory_enabled else {},
        "memory_enabled": memory_enabled,
        "error_count": 0,
        "similarity_threshold": 0.8
    }

    try:
        # 调用 LangGraph 核心工作流
        final_state = agent_app.invoke(initial_state)
        if request_control.is_paused(request_id):
            raise RequestPaused("Request paused by user")

        # 提取 AI 最后一步生成的数据分析报告
        answer = final_state["messages"][-1].content
        execution_failed = final_state.get("execution_result", "").startswith("ERROR:") or final_state.get(
            "validation_result", ""
        ).startswith("ERROR:")
        observability_store.complete_request(
            request_id=request_id,
            status="failed" if execution_failed else "success",
            latency_ms=(time.perf_counter() - started) * 1000,
            retries=final_state.get("error_count", 0),
            error_message=final_state.get("execution_result") if execution_failed else None,
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
                user_id=request.user_id,
                data_source_id=data_source_id,
                user_query=request.query,
                intent=final_state.get("intent", ""),
                entities=final_state.get("entities", {}),
                answer=answer,
            )
        if request.data_source_id:
            user_preferences = memory_store.update_preferences(
                request.user_id, {"default_data_source_id": data_source_id}
            )
        semantic_template = None
        if memory_enabled and not execution_failed and final_state.get("intent") in {"text_to_sql", "data_analysis"}:
            try:
                semantic_template = semantic_memory_store.save_successful_sql_template(
                    user_id=request.user_id,
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
    except RequestPaused:
        observability_store.complete_request(
            request_id=request_id,
            status="paused",
            latency_ms=(time.perf_counter() - started) * 1000,
            retries=0,
            error_message="Request paused by user",
        )
        return {"code": 499, "data": {"answer": "请求已暂停。", "metrics": {"request_id": request_id}}}
    except Exception as e:
        observability_store.complete_request(
            request_id=request_id,
            status="failed",
            latency_ms=(time.perf_counter() - started) * 1000,
            retries=0,
            error_message=str(e),
        )
        return {
            "code": 500,
            "data": {
                "answer": f"API 内部执行发生错误: {str(e)}",
                "metrics": {}
            }
        }
    finally:
        request_control.clear(request_id)


@app.get("/api/v1/data-sources")
async def list_data_sources():
    return {"code": 200, "data": data_source_registry.list_enabled()}


@app.get("/api/v1/memory/preferences/{user_id}")
async def get_user_preferences(user_id: str):
    return {"code": 200, "data": memory_store.get_preferences(user_id)}


@app.put("/api/v1/memory/preferences/{user_id}")
async def update_user_preferences(user_id: str, request: PreferenceUpdateRequest):
    updates = request.model_dump(exclude_none=True)
    data_source_id = updates.get("default_data_source_id")
    if data_source_id:
        try:
            data_source_registry.resolve(data_source_id)
        except DataSourceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        return {"code": 200, "data": memory_store.update_preferences(user_id, updates)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/api/v1/memory/{user_id}")
async def delete_user_memory(user_id: str):
    conversation = memory_store.delete_user_memory(user_id, include_preferences=True)
    semantic_count = semantic_memory_store.delete_all(user_id)
    return {"code": 200, "data": {**conversation, "semantic_memories": semantic_count}}


@app.post("/api/v1/requests/{request_id}/pause")
async def pause_request(request_id: str):
    request_control.pause(request_id)
    return {"code": 200, "data": {"request_id": request_id, "paused": True}}


@app.get("/api/v1/memory/semantic")
async def search_semantic_memory(user_id: str, query: str, data_source_id: str | None = None, limit: int = 4):
    return {
        "code": 200,
        "data": semantic_memory_store.search(user_id, query, data_source_id, limit=limit),
    }


@app.post("/api/v1/memory/semantic/{user_id}")
async def confirm_semantic_memory(user_id: str, request: SemanticMemoryRequest):
    if not request.confirmed:
        raise HTTPException(status_code=422, detail="Semantic memory must be explicitly confirmed")
    if request.data_source_scope:
        try:
            data_source_registry.resolve(request.data_source_scope)
        except DataSourceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
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
        return {"code": 200, "data": stored}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/api/v1/memory/semantic/{user_id}/{memory_id}")
async def delete_semantic_memory(user_id: str, memory_id: str):
    if not semantic_memory_store.delete(user_id, memory_id):
        raise HTTPException(status_code=404, detail="Semantic memory not found")
    return {"code": 200, "data": {"deleted": True}}


@app.get("/api/v1/observability/summary")
async def observability_summary(window_hours: int = 24):
    return {"code": 200, "data": observability_store.summary(window_hours)}


@app.get("/api/v1/observability/requests/{request_id}")
async def observability_request(request_id: str):
    data = observability_store.get_request(request_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Observability request not found")
    return {"code": 200, "data": data}


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
