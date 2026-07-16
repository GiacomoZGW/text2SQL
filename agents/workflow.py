import json
import operator
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Annotated, Any, List, TypedDict

import dotenv
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from mindsdb_sql_parser import parse_sql
from pydantic import BaseModel, Field, ValidationError

from core_engine.federation_engine import FederationError, create_federation_engine
from core_engine.data_source_registry import DataSourceError, create_data_source_registry
from core_engine.database_connectors import ConnectorError, direct_connector_registry
from core_engine.access_control import AccessDeniedError, Principal, access_control
from core_engine.request_control import RequestPaused, request_control
from observability import observability_store
from memory import semantic_memory_store
from vector_store import retrieve_relevant_schema

dotenv.load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
MAX_RETRIES = 2
MAX_RESULT_ROWS = 50
INTENT_CONFIDENCE_THRESHOLD = 0.7
federation_engine = create_federation_engine(BASE_DIR)
data_source_registry = create_data_source_registry(BASE_DIR)

INTENT_LABELS = {
    "text_to_sql",
    "data_analysis",
    "schema_question",
    "database_selection",
    "operation_help",
    "greeting",
    "unsafe_operation",
    "clarification_required",
    "out_of_scope",
}
SUPERVISOR_INTENT_ROUTES = {
    "text_to_sql": "semantic_memory",
    "data_analysis": "semantic_memory",
    "schema_question": "schema",
    "database_selection": "data_source",
    "operation_help": "help",
    "greeting": "help",
    "unsafe_operation": "security",
    "clarification_required": "clarification",
    "out_of_scope": "help",
}


class AgentState(TypedDict, total=False):
    """Shared state passed between every agent in the graph."""

    messages: Annotated[List[BaseMessage], operator.add]
    execution_trace: Annotated[List[dict[str, str]], operator.add]
    request_id: str
    user_id: str
    principal: Principal
    user_query: str
    conversation_context: list[dict[str, Any]]
    user_preferences: dict[str, Any]
    semantic_memories: list[dict[str, Any]]
    memory_enabled: bool
    data_source_id: str
    target_db_type: str
    intent: str
    intent_confidence: float
    intent_reason: str
    entities: dict[str, Any]
    needs_clarification: bool
    clarification_question: str
    query_plan: dict[str, Any]
    schema_context: str
    schema_error: str
    relevant_schemas: str
    retrieval_metadata: dict[str, Any]
    candidate_sqls: List[str]
    validated_candidates: List[dict[str, str]]
    selected_candidate_index: int
    generated_sql: str
    validation_result: str
    execution_result: str
    error_count: int
    similarity_threshold: float


class IntentClassification(BaseModel):
    """Validated contract for the structured intent-classifier response."""

    intent: str
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = "LLM classification"
    entities: dict[str, Any] = Field(default_factory=dict)
    needs_clarification: bool = False
    clarification_question: str = ""


def _memory_context_for_prompt(state: AgentState) -> str:
    return json.dumps(
        {
            "recent_turns": state.get("conversation_context", []),
            "user_preferences": state.get("user_preferences", {}),
            "semantic_memories": state.get("semantic_memories", []),
        },
        ensure_ascii=False,
    )


def _trace(
    state: AgentState,
    agent: str,
    status: str,
    detail: str,
    metadata: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    try:
        observability_store.record_agent_event(state.get("request_id", ""), agent, status, detail, metadata)
    except Exception as exc:
        print(f"[Observability] agent event skipped: {exc}")
    return [{"agent": agent, "status": status, "detail": detail}]


def _ensure_request_active(state: AgentState) -> None:
    request_id = state.get("request_id", "")
    if request_id and request_control.is_paused(request_id):
        raise RequestPaused("Request paused by user")


def _token_usage(response: Any) -> dict[str, int | None]:
    usage = getattr(response, "usage_metadata", None) or {}
    if not usage:
        usage = getattr(response, "response_metadata", {}).get("token_usage", {})

    def read(*keys: str) -> int | None:
        for key in keys:
            value = usage.get(key)
            if value is not None:
                return int(value)
        return None

    return {
        "input_tokens": read("input_tokens", "prompt_tokens"),
        "output_tokens": read("output_tokens", "completion_tokens"),
        "total_tokens": read("total_tokens"),
    }


def _invoke_llm(state: AgentState, agent: str, prompt: str):
    _ensure_request_active(state)
    started = time.perf_counter()
    request_id = state.get("request_id", "")
    model = str(getattr(llm, "model_name", None) or getattr(llm, "model", "unknown"))
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        try:
            observability_store.record_llm_call(
                request_id=request_id,
                agent=agent,
                model=model,
                prompt_chars=len(prompt),
                latency_ms=(time.perf_counter() - started) * 1000,
                usage=_token_usage(response),
                status="success",
            )
        except Exception as exc:
            print(f"[Observability] LLM event skipped: {exc}")
        return response
    except Exception as exc:
        try:
            observability_store.record_llm_call(
                request_id=request_id,
                agent=agent,
                model=model,
                prompt_chars=len(prompt),
                latency_ms=(time.perf_counter() - started) * 1000,
                usage={},
                status="failed",
                error_message=str(exc),
            )
        except Exception as observation_error:
            print(f"[Observability] LLM failure event skipped: {observation_error}")
        raise


def _open_connection(db_type: str) -> sqlite3.Connection:
    """Open the standalone SQLite database read-only."""
    if db_type == "federated":
        raise ValueError("联邦查询必须通过 DuckDBFederationEngine 执行")

    db_path = BASE_DIR / "ecommerce_test.db"
    if not db_path.exists():
        raise FileNotFoundError(f"找不到单库数据文件: {db_path}")
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)

    conn.row_factory = sqlite3.Row
    return conn


def _direct_connector_for(data_source_id: str):
    try:
        source = data_source_registry.resolve(data_source_id)
    except DataSourceError as exc:
        raise ConnectorError(f"Unknown direct data source: {data_source_id}") from exc
    if source.get("execution_mode") != "direct":
        raise ConnectorError(f"Data source is not configured for direct execution: {data_source_id}")
    return direct_connector_registry.connector_for(source)


def _sql_dialect_for_state(state: AgentState) -> str:
    db_type = state.get("target_db_type", "sqlite")
    if db_type == "federated":
        return "duckdb"
    if db_type == "direct":
        try:
            return str(data_source_registry.resolve(state.get("data_source_id", "")).get("engine", "sqlite"))
        except DataSourceError:
            return "sqlite"
    return "sqlite"


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def get_database_schema(db_type: str, data_source_id: str = "") -> str:
    """Read the live standalone or DuckDB federated metadata."""
    if db_type == "federated":
        return federation_engine.get_schema()
    if db_type == "direct":
        return _direct_connector_for(data_source_id).get_schema()

    conn = _open_connection(db_type)
    try:
        schema_lines: list[str] = []
        table_rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for row in table_rows:
            table_name = row["name"]
            column_rows = conn.execute(f"PRAGMA table_info({_quote_identifier(table_name)})").fetchall()
            columns = ", ".join(f"{column['name']} ({column['type'] or 'TEXT'})" for column in column_rows)
            schema_lines.append(f"Table: {table_name}\nColumns: {columns}")
        return "\n\n".join(schema_lines)
    finally:
        conn.close()


def _clean_sql(sql: str) -> str:
    return sql.replace("```sql", "").replace("```", "").strip()


def _without_string_literals(sql: str) -> str:
    return re.sub(r"'(?:''|[^'])*'", "''", sql)


def _prepare_read_only_sql(sql: str, db_type: str, data_source_id: str = "") -> tuple[str, str | None]:
    """Return executable SQL only when it is a bounded, read-only SQLite query."""
    clean_sql = _clean_sql(sql)
    if not clean_sql:
        return "", "SQL 为空。"

    if ";" in clean_sql:
        if not clean_sql.endswith(";") or clean_sql.count(";") != 1:
            return "", "只允许执行一条 SQL 语句。"
        clean_sql = clean_sql[:-1].strip()

    if "--" in clean_sql or "/*" in clean_sql or "*/" in clean_sql:
        return "", "SQL 不允许包含注释。"

    normalized = re.sub(r"\s+", " ", clean_sql).strip()
    if not re.match(r"^(SELECT|WITH)\b", normalized, flags=re.IGNORECASE):
        return "", "只允许执行 SELECT 或 WITH 查询。"

    sql_without_literals = _without_string_literals(normalized)
    forbidden = re.compile(
        r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|PRAGMA|VACUUM|REPLACE|MERGE|GRANT|REVOKE)\b",
        flags=re.IGNORECASE,
    )
    if forbidden.search(sql_without_literals):
        return "", "SQL 包含非只读操作。"

    limit_pattern = re.compile(r"\bLIMIT\s+(\d+)\b", flags=re.IGNORECASE)
    limit_match = limit_pattern.search(sql_without_literals)
    if not limit_match:
        normalized = f"{normalized} LIMIT {MAX_RESULT_ROWS}"
    elif int(limit_match.group(1)) > MAX_RESULT_ROWS:
        normalized = limit_pattern.sub(f"LIMIT {MAX_RESULT_ROWS}", normalized, count=1)

    try:
        if db_type == "federated":
            federation_engine.explain(normalized)
        elif db_type == "direct":
            _direct_connector_for(data_source_id).explain(normalized)
        else:
            conn = _open_connection(db_type)
            try:
                conn.execute(f"EXPLAIN QUERY PLAN {normalized}").fetchall()
            finally:
                conn.close()
    except sqlite3.Error as exc:
        return "", f"SQL 预检失败: {exc}"
    except (ConnectorError, FederationError, OSError, ValueError) as exc:
        return "", str(exc)

    return normalized, None


def validate_sql(sql: str, db_type: str = "sqlite", data_source_id: str = "") -> tuple[bool, str, str]:
    """Compatibility wrapper for callers that need a public SQL safety check."""
    prepared_sql, validation_error = _prepare_read_only_sql(sql, db_type, data_source_id)
    return validation_error is None, prepared_sql, validation_error or ""


def execute_real_sql(sql: str, db_type: str, data_source_id: str = "") -> str:
    """Execute one validated read-only query and return a bounded JSON result."""
    prepared_sql, validation_error = _prepare_read_only_sql(sql, db_type, data_source_id)
    if validation_error:
        return f"ERROR: {validation_error}"

    print(f"[Executor] mode={db_type}; sql={prepared_sql}")
    try:
        if db_type == "federated":
            result_list = federation_engine.execute(prepared_sql)
        elif db_type == "direct":
            result_list = _direct_connector_for(data_source_id).execute(prepared_sql)
        else:
            conn = _open_connection(db_type)
            try:
                rows = conn.execute(prepared_sql).fetchall()
            finally:
                conn.close()
            result_list = [dict(row) for row in rows]
        if not result_list:
            return "执行成功，但未找到匹配的数据 (结果为空)。"
        return json.dumps(result_list[:50], ensure_ascii=False)
    except (ConnectorError, sqlite3.Error, FederationError, OSError, ValueError) as exc:
        return f"ERROR: {exc}"


llm = ChatOpenAI(
    model="deepseek-v4-flash",
    temperature=0.1,
    timeout=60,
    max_retries=3,
)


def classify_intent_rules(query: str) -> dict[str, Any] | None:
    """Classify high-confidence intents locally before spending an LLM call."""
    normalized = query.strip().lower()
    if not normalized:
        return {
            "intent": "clarification_required",
            "confidence": 1.0,
            "reason": "empty query",
            "entities": {},
            "needs_clarification": True,
            "clarification_question": "请描述希望查询或分析的数据内容。",
        }

    unsafe_match = re.search(
        r"\b(delete|drop|truncate|update|insert|alter|create|grant|revoke)\b|删除|删掉|清空|更新数据|写入|插入|建表|改表|授权",
        normalized,
    )
    if unsafe_match:
        return {
            "intent": "unsafe_operation",
            "confidence": 0.99,
            "reason": "write or destructive operation requested",
            "entities": {"unsafe_term": unsafe_match.group(0)},
            "needs_clarification": False,
            "clarification_question": "",
        }

    if re.fullmatch(r"(你好|您好|嗨|在吗|你是谁|hi|hello|hey|早上好|下午好|晚上好)[!！。？? ]*", normalized):
        return {
            "intent": "greeting",
            "confidence": 0.98,
            "reason": "greeting pattern",
            "entities": {},
            "needs_clarification": False,
            "clarification_question": "",
        }

    if re.search(r"怎么用|怎么使用|如何使用|使用帮助|帮助|能做什么|功能说明|操作说明", normalized):
        return {
            "intent": "operation_help",
            "confidence": 0.95,
            "reason": "product help request",
            "entities": {},
            "needs_clarification": False,
            "clarification_question": "",
        }

    if re.search(r"切换.*(数据库|数据源)|选择.*(数据库|数据源)|连接.*(数据库|数据源)|使用.*(mysql|postgres|clickhouse|sqlite)", normalized):
        return {
            "intent": "database_selection",
            "confidence": 0.92,
            "reason": "database selection request",
            "entities": {},
            "needs_clarification": False,
            "clarification_question": "",
        }

    if re.search(r"表结构|字段|列名|有哪些表|哪些表|schema|数据源信息|数据库结构", normalized):
        return {
            "intent": "schema_question",
            "confidence": 0.93,
            "reason": "schema metadata request",
            "entities": {},
            "needs_clarification": False,
            "clarification_question": "",
        }

    if re.search(r"天气|翻译|写诗|小说|笑话|音乐推荐", normalized):
        return {
            "intent": "out_of_scope",
            "confidence": 0.9,
            "reason": "request is outside the data-agent domain",
            "entities": {},
            "needs_clarification": False,
            "clarification_question": "",
        }

    if re.search(r"跨库|联邦|mysql|postgres|clickhouse|行为日志|特征库", normalized):
        return {
            "intent": "text_to_sql",
            "confidence": 0.91,
            "reason": "cross-source query pattern",
            "entities": {"requires_federation": True, "target_data_source": "federated_demo"},
            "needs_clarification": False,
            "clarification_question": "",
        }

    if re.search(r"趋势|同比|环比|归因|洞察|转化率", normalized):
        metric = "转化率" if "转化率" in normalized else "分析指标"
        return {
            "intent": "data_analysis",
            "confidence": 0.86,
            "reason": "analysis pattern",
            "entities": {"metric": metric},
            "needs_clarification": False,
            "clarification_question": "",
        }

    if re.search(r"统计|查询|查找|订单|销售|用户", normalized):
        return {
            "intent": "text_to_sql",
            "confidence": 0.82,
            "reason": "data query pattern",
            "entities": {},
            "needs_clarification": False,
            "clarification_question": "",
        }

    if normalized in {"查一下", "查查", "看看", "分析一下", "帮我查", "看看数据"}:
        return {
            "intent": "clarification_required",
            "confidence": 0.9,
            "reason": "query lacks a metric, scope, or time range",
            "entities": {},
            "needs_clarification": True,
            "clarification_question": "请说明要查询的指标、筛选范围或时间范围。",
        }
    return None


def _parse_intent_classification(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed = IntentClassification.model_validate(payload)
    except AttributeError:
        parsed = IntentClassification.parse_obj(payload)
    except ValidationError as exc:
        raise ValueError(f"Invalid intent payload: {exc}") from exc

    intent = parsed.intent.strip().lower()
    if intent not in INTENT_LABELS:
        raise ValueError(f"Unsupported intent: {intent}")
    return {
        "intent": intent,
        "confidence": parsed.confidence,
        "reason": parsed.reason.strip() or "LLM classification",
        "entities": parsed.entities,
        "needs_clarification": parsed.needs_clarification or intent == "clarification_required",
        "clarification_question": parsed.clarification_question.strip(),
    }


def _apply_intent_guardrails(result: dict[str, Any], query: str) -> dict[str, Any]:
    intent = result["intent"]
    confidence = float(result["confidence"])
    entities = result.get("entities", {})
    metric = str(entities.get("metric", "")).strip() if isinstance(entities, dict) else ""
    needs_clarification = bool(result.get("needs_clarification"))
    clarification_question = str(result.get("clarification_question", "")).strip()

    if confidence < INTENT_CONFIDENCE_THRESHOLD:
        intent = "clarification_required"
        needs_clarification = True
        clarification_question = clarification_question or "我还不能确定你的目标。请补充要查询的指标、范围或时间条件。"
        result["reason"] = f"low confidence ({confidence:.2f}): {result['reason']}"
    elif intent == "data_analysis" and not metric:
        intent = "clarification_required"
        needs_clarification = True
        clarification_question = clarification_question or "请说明要分析的指标，例如销售额、订单数或转化率。"
        result["reason"] = "data analysis intent is missing a metric"

    result["intent"] = intent
    result["needs_clarification"] = needs_clarification
    result["clarification_question"] = clarification_question
    return result


def _classify_intent_with_llm(state: AgentState, query: str) -> dict[str, Any]:
    prompt = f"""
You are an intent classifier for a read-only Text2SQL system.
Classify the user's request into exactly one of:
{', '.join(sorted(INTENT_LABELS))}.

Return JSON only with this schema:
{{
  "intent": "one allowed label",
  "confidence": 0.0,
  "reason": "short explanation",
  "entities": {{"metric": "", "time_range": "", "dimensions": [], "target_data_source": "", "requires_federation": false}},
  "needs_clarification": false,
  "clarification_question": ""
}}

Mark requests to write, update, delete, create, or change data as unsafe_operation.
For data_analysis, include a non-empty entities.metric. If required information is missing,
return clarification_required and a concise clarification_question.
Conversation context and user preferences (use only when relevant):
{_memory_context_for_prompt(state)}
User request: {query}
"""
    response = _invoke_llm(state, "intent", prompt)
    match = re.search(r"\{.*\}", response.content.strip(), flags=re.DOTALL)
    if not match:
        raise ValueError("Intent classifier did not return JSON")
    return _apply_intent_guardrails(_parse_intent_classification(json.loads(match.group(0))), query)


def intent_agent(state: AgentState) -> dict[str, Any]:
    started = time.perf_counter()
    user_query = str(state["messages"][-1].content).strip()
    result = classify_intent_rules(user_query)
    source = "rules"
    if result is None:
        try:
            result = _classify_intent_with_llm(state, user_query)
            source = "llm"
        except Exception as exc:
            result = {
                "intent": "text_to_sql",
                "confidence": 0.5,
                "reason": f"LLM classifier fallback: {exc}",
                "entities": {},
                "needs_clarification": False,
                "clarification_question": "",
            }
            source = "fallback"

    result = _apply_intent_guardrails(result, user_query)
    try:
        observability_store.record_intent_result(
            request_id=state.get("request_id", ""),
            intent=result["intent"],
            confidence=result["confidence"],
            needs_clarification=result["needs_clarification"],
            latency_ms=(time.perf_counter() - started) * 1000,
        )
    except Exception as exc:
        print(f"[Observability] intent summary skipped: {exc}")
    detail = f"intent={result['intent']}; source={source}; confidence={result['confidence']:.2f}"
    return {
        "user_query": user_query,
        "intent": result["intent"],
        "intent_confidence": result["confidence"],
        "intent_reason": result["reason"],
        "entities": result["entities"],
        "needs_clarification": result["needs_clarification"],
        "clarification_question": result["clarification_question"],
        "execution_trace": _trace(
            state,
            "intent",
            "completed",
            detail,
            {"source": source, "intent": result["intent"], "confidence": result["confidence"]},
        ),
    }


def intent_response_agent(state: AgentState) -> dict[str, Any]:
    intent = state.get("intent", "out_of_scope")
    if intent == "unsafe_operation":
        answer = "当前系统只允许只读查询，不能执行删除、更新、写入或结构变更操作。请改为描述需要查看的数据。"
    elif intent == "clarification_required":
        answer = state.get("clarification_question") or "请补充查询的指标、范围或时间条件。"
    elif intent == "greeting":
        answer = "你好，我可以帮助你查询业务数据、解释表结构，或分析查询结果。"
    elif intent == "operation_help":
        answer = "你可以直接输入数据问题，例如“统计上个月各商品类别的销售额”。系统会生成并安全执行只读 SQL。"
    elif intent == "database_selection":
        answer = "请在左侧配置中心选择已启用的数据源。当前支持 SQLite 与多源联邦查询；未录入的数据源不会直接执行。"
    elif intent == "schema_question":
        table_names = re.findall(r"Table:\s*([^\n]+)", state.get("schema_context", ""))
        answer = "当前可用表：" + ("、".join(table_names[:12]) if table_names else "暂未读取到表结构")
    else:
        answer = "我专注于数据查询、Schema 说明和分析任务。请告诉我希望查看的业务数据。"
    return {
        "messages": [AIMessage(content=answer)],
        "execution_trace": _trace(state, "intent_response", "completed", f"responded_to={intent}"),
    }


def security_agent(state: AgentState) -> dict[str, Any]:
    unsafe_term = state.get("entities", {}).get("unsafe_term", "写入或结构变更")
    answer = f"检测到“{unsafe_term}”请求。当前系统仅支持只读查询，不会执行删除、更新、写入或结构变更操作。请改为描述需要查看的数据。"
    return {
        "messages": [AIMessage(content=answer)],
        "execution_trace": _trace(state, "security", "completed", "read-only policy enforced"),
    }


def clarification_agent(state: AgentState) -> dict[str, Any]:
    answer = state.get("clarification_question") or "请补充要查询的指标、范围或时间条件。"
    return {
        "messages": [AIMessage(content=answer)],
        "execution_trace": _trace(state, "clarification", "completed", "requested missing query details"),
    }


def help_agent(state: AgentState) -> dict[str, Any]:
    intent = state.get("intent", "operation_help")
    if intent == "greeting":
        answer = "你好，我可以帮助你查询业务数据、解释表结构，或分析查询结果。"
    elif intent == "operation_help":
        answer = "你可以直接输入数据问题，例如“统计上个月各商品类别的销售额”。系统会生成并安全执行只读 SQL。"
    else:
        answer = "我专注于数据查询、Schema 说明和分析任务。请告诉我希望查看的业务数据。"
    return {
        "messages": [AIMessage(content=answer)],
        "execution_trace": _trace(state, "help", "completed", f"responded_to={intent}"),
    }


def data_source_agent(state: AgentState) -> dict[str, Any]:
    requested = state.get("data_source_id", "")
    available = data_source_registry.list_enabled()
    source_names = "、".join(str(source["name"]) for source in available) or "暂无已启用数据源"
    answer = f"当前可用数据源：{source_names}。当前选择：{requested or '未选择'}。"
    return {
        "messages": [AIMessage(content=answer)],
        "execution_trace": _trace(
            state,
            "data_source",
            "completed",
            "returned available data-source status",
            {"requested_data_source": requested, "available": available},
        ),
    }


def schema_response_agent(state: AgentState) -> dict[str, Any]:
    table_names = re.findall(r"Table:\s*([^\n]+)", state.get("schema_context", ""))
    answer = "当前可用表：" + ("、".join(table_names[:12]) if table_names else "暂未读取到表结构")
    return {
        "messages": [AIMessage(content=answer)],
        "execution_trace": _trace(state, "schema_response", "completed", f"tables={len(table_names)}"),
    }


def supervisor_agent(state: AgentState) -> dict[str, Any]:
    user_query = state["messages"][-1].content
    requested_target = str(state.get("target_db_type", "sqlite")).lower()
    federation_terms = ("跨库", "联邦", "mysql", "mongo", "clickhouse", "行为日志", "特征库")
    if requested_target in {"direct", "federated"}:
        resolved_target = requested_target
    else:
        resolved_target = "federated" if any(term in user_query.lower() for term in federation_terms) else "sqlite"
    plan = {
        "requested_target": requested_target,
        "execution_mode": resolved_target,
        "requires_federation": resolved_target == "federated",
        "intent": state.get("intent", "text_to_sql"),
        "intent_confidence": state.get("intent_confidence", 0.0),
        "entities": state.get("entities", {}),
        "memory_context_turns": len(state.get("conversation_context", [])),
        "preference_default_source": state.get("user_preferences", {}).get("default_data_source_id"),
        "next_agent": SUPERVISOR_INTENT_ROUTES.get(state.get("intent", ""), "help"),
        "retry_context": state.get("validation_result") or state.get("execution_result") or "无",
    }
    print(f"[Supervisor] route={resolved_target}; next={plan['next_agent']}")
    try:
        observability_store.record_supervisor_route(state.get("request_id", ""), plan["next_agent"])
    except Exception as exc:
        print(f"[Observability] supervisor route skipped: {exc}")
    return {
        "user_query": user_query,
        "target_db_type": resolved_target,
        "query_plan": plan,
        "execution_trace": _trace(
            state,
            "supervisor",
            "completed",
            f"route={resolved_target}; next={plan['next_agent']}",
            plan,
        ),
    }


def schema_agent(state: AgentState) -> dict[str, Any]:
    db_type = state.get("target_db_type", "sqlite")
    try:
        schema_context = get_database_schema(db_type, state.get("data_source_id", ""))
    except (ConnectorError, sqlite3.Error, FederationError, OSError, ValueError) as exc:
        error = f"ERROR: Schema 加载失败: {exc}"
        return {
            "schema_error": error,
            "validation_result": error,
            "error_count": state.get("error_count", 0) + 1,
            "execution_trace": _trace(state, "schema", "failed", error),
        }

    threshold = max(0.4, state.get("similarity_threshold", 0.8) - state.get("error_count", 0) * 0.2)
    print(f"[Schema] source={db_type}; tables={schema_context.count('Table:')}")
    return {
        "schema_context": schema_context,
        "schema_error": "",
        "similarity_threshold": threshold,
        "execution_trace": _trace(
            state, "schema", "completed", f"tables={schema_context.count('Table:')}", {"db_type": db_type}
        ),
    }


def semantic_memory_agent(state: AgentState) -> dict[str, Any]:
    if not state.get("memory_enabled", True):
        return {
            "semantic_memories": [],
            "execution_trace": _trace(state, "semantic_memory", "completed", "memory disabled"),
        }
    try:
        memories = semantic_memory_store.search(
            user_id=state.get("user_id", ""),
            query=state.get("user_query", ""),
            data_source_id=state.get("data_source_id"),
        )
        sources = sorted({memory["source"] for memory in memories})
        detail = f"retrieved={len(memories)}; source={','.join(sources) or 'none'}"
        return {
            "semantic_memories": memories,
            "execution_trace": _trace(
                state,
                "semantic_memory",
                "completed",
                detail,
                {"memory_count": len(memories), "sources": sources},
            ),
        }
    except Exception as exc:
        return {
            "semantic_memories": [],
            "execution_trace": _trace(state, "semantic_memory", "failed", str(exc)),
        }


def schema_retrieval_agent(state: AgentState) -> dict[str, Any]:
    limit = min(6, 4 + state.get("error_count", 0))
    retrieval = retrieve_relevant_schema(
        query=state.get("user_query", ""),
        db_type=state.get("target_db_type", "sqlite"),
        schema_context=state.get("schema_context", ""),
        limit=limit,
    )
    detail = f"{retrieval['source']}; {retrieval['detail']}"
    print(f"[Retrieval] {detail}")
    return {
        "relevant_schemas": retrieval["context"],
        "retrieval_metadata": {
            "source": retrieval["source"],
            "detail": retrieval["detail"],
            "tables": retrieval["tables"],
        },
        "execution_trace": _trace(
            state,
            "retrieval",
            "completed",
            detail,
            {"source": retrieval["source"], "tables": retrieval["tables"]},
        ),
    }


def sql_agent(state: AgentState) -> dict[str, Any]:
    db_type = state.get("target_db_type", "sqlite")
    if db_type == "federated":
        dialect_rules = "使用 DuckDB 联邦 SQL，并且表必须使用 Schema 中的库名前缀，例如 db_mysql.orders。"
        dialect_name = "DuckDB federation"
    elif db_type == "direct":
        try:
            source = data_source_registry.resolve(state.get("data_source_id", ""))
            dialect_name = str(source.get("engine", "SQL")).upper()
        except DataSourceError:
            dialect_name = "configured database"
        dialect_rules = f"使用 {dialect_name} 方言，并且仅使用当前数据源 Schema 中返回的表和字段。"
    else:
        dialect_rules = "使用 Schema 中的未限定表名，不要增加库名前缀。"
        dialect_name = "SQLite"

    base_prompt = f"""
你是 SQL 专家 Agent。请针对用户问题生成一条 {dialect_name} 查询。

查询规划：{json.dumps(state.get('query_plan', {}), ensure_ascii=False)}
Chroma 召回的 Schema：
{state.get('relevant_schemas', state.get('schema_context', ''))}

用户问题：{state.get('user_query', '')}
会话记忆与用户偏好：{_memory_context_for_prompt(state)}
上一轮校验或执行反馈：{state.get('validation_result') or state.get('execution_result') or '无'}

约束：
1. 只返回一条纯 SQL，不要 Markdown、注释或解释。
2. 只能使用 SELECT 或 WITH；不得写入、建表、附加数据库或执行 PRAGMA。
3. {dialect_rules}
4. 未明确要求全量明细时使用 LIMIT 50。
"""
    strategies = (
        "优先选择直接、可读性强的聚合或关联写法。",
        "独立检查关联键、聚合粒度和过滤条件，生成可替代的正确写法。",
    )
    candidate_sqls: list[str] = []
    print(f"[SQL] generating candidates for {db_type}")
    for strategy in strategies:
        response = _invoke_llm(state, "sql", f"{base_prompt}\n候选策略：{strategy}")
        candidate_sql = _clean_sql(response.content)
        if candidate_sql:
            candidate_sqls.append(candidate_sql)

    return {
        "candidate_sqls": candidate_sqls,
        "execution_trace": _trace(state, "sql", "completed", f"candidates={len(candidate_sqls)}"),
    }


def validator_agent(state: AgentState) -> dict[str, Any]:
    validated_candidates: list[dict[str, str]] = []
    validation_errors: list[str] = []
    for index, candidate_sql in enumerate(state.get("candidate_sqls", []), start=1):
        policy_sql = candidate_sql
        principal = state.get("principal")
        if principal:
            try:
                policy_sql = access_control.enforce_sql_policy(
                    principal,
                    state.get("data_source_id", ""),
                    candidate_sql,
                    _sql_dialect_for_state(state),
                )
                observability_store.record_audit_event(
                    user_id=principal.user_id,
                    tenant_id=principal.tenant_id,
                    role=principal.role,
                    action="sql_policy_check",
                    resource_type="data_source",
                    resource_id=state.get("data_source_id", ""),
                    request_id=state.get("request_id", ""),
                    outcome="allowed",
                    details={"candidate_index": index},
                )
            except AccessDeniedError as exc:
                validation_errors.append(f"Candidate {index}: access policy rejected SQL: {exc}")
                observability_store.record_audit_event(
                    user_id=principal.user_id,
                    tenant_id=principal.tenant_id,
                    role=principal.role,
                    action="sql_policy_check",
                    resource_type="data_source",
                    resource_id=state.get("data_source_id", ""),
                    request_id=state.get("request_id", ""),
                    outcome="denied",
                    details={"candidate_index": index, "reason": str(exc)},
                )
                continue
        prepared_sql, validation_error = _prepare_read_only_sql(
            policy_sql, state.get("target_db_type", "sqlite"), state.get("data_source_id", "")
        )
        if validation_error:
            validation_errors.append(f"候选 {index}: {validation_error}")
            continue
        try:
            parse_sql(prepared_sql)
        except Exception as exc:
            validation_errors.append(f"候选 {index}: AST 解析失败: {exc}")
            continue
        validated_candidates.append({"index": str(index), "sql": prepared_sql})

    if not validated_candidates:
        detail = "; ".join(validation_errors) or "SQL Agent 未生成候选 SQL。"
        error = f"ERROR: Validator: {detail}"
        print(f"[Validator] rejected: {detail}")
        return {
            "validation_result": error,
            "error_count": state.get("error_count", 0) + 1,
            "execution_trace": _trace(state, "validator", "failed", detail),
        }

    print(f"[Validator] accepted {len(validated_candidates)} AST-validated candidates")
    return {
        "validated_candidates": validated_candidates,
        "validation_result": "VALID",
        "execution_trace": _trace(
            state,
            "validator",
            "completed",
            f"ast_validated={len(validated_candidates)}; rejected={len(validation_errors)}",
            {"valid_candidates": len(validated_candidates), "rejected_candidates": len(validation_errors)},
        ),
    }


def sql_reviewer_agent(state: AgentState) -> dict[str, Any]:
    candidates = state.get("validated_candidates", [])
    if not candidates:
        return {"validation_result": "ERROR: Reviewer: 没有可评审的 SQL 候选。"}
    if len(candidates) == 1:
        selected = candidates[0]
        return {
            "generated_sql": selected["sql"],
            "selected_candidate_index": int(selected["index"]),
            "execution_trace": _trace(state, "reviewer", "completed", "single validated candidate selected"),
        }

    candidate_text = "\n\n".join(
        f"候选 {candidate['index']}:\n{candidate['sql']}" for candidate in candidates
    )
    prompt = f"""
你是 SQL 评审 Agent。根据用户问题、查询规划和 Schema，在以下已经通过 AST 与只读校验的候选 SQL 中选择最准确的一条。

用户问题：{state.get('user_query', '')}
查询规划：{json.dumps(state.get('query_plan', {}), ensure_ascii=False)}
Schema：{state.get('relevant_schemas', '')}

{candidate_text}

只返回 `CANDIDATE: N`，N 为候选编号。不要输出其他文字。
"""
    try:
        response = _invoke_llm(state, "reviewer", prompt)
        match = re.search(r"CANDIDATE\s*:\s*(\d+)", response.content, flags=re.IGNORECASE)
        selected_index = int(match.group(1)) if match else int(candidates[0]["index"])
    except Exception:
        selected_index = int(candidates[0]["index"])

    selected = next((item for item in candidates if int(item["index"]) == selected_index), candidates[0])
    print(f"[Reviewer] selected candidate {selected['index']}")
    return {
        "generated_sql": selected["sql"],
        "selected_candidate_index": int(selected["index"]),
        "execution_trace": _trace(state, "reviewer", "completed", f"selected={selected['index']}"),
    }


def executor_agent(state: AgentState) -> dict[str, Any]:
    result = execute_real_sql(
        state.get("generated_sql", ""), state.get("target_db_type", "sqlite"), state.get("data_source_id", "")
    )
    if result.startswith("ERROR:"):
        print(f"[Executor] failed: {result}")
        return {
            "execution_result": result,
            "error_count": state.get("error_count", 0) + 1,
            "execution_trace": _trace(state, "executor", "failed", result),
        }

    print("[Executor] query completed")
    return {
        "execution_result": result,
        "execution_trace": _trace(state, "executor", "completed", "query completed"),
    }


def analyst_agent(state: AgentState) -> dict[str, Any]:
    result = state.get("execution_result", "")
    if result.startswith("ERROR:") or state.get("validation_result", "").startswith("ERROR:"):
        error = state.get("validation_result") if state.get("validation_result", "").startswith("ERROR:") else result
        answer = f"查询未能安全执行。\n\n原因：{error}\n\n请调整问题后重试。"
        return {
            "messages": [AIMessage(content=answer)],
            "execution_trace": _trace(state, "analyst", "completed", "returned execution guidance"),
        }

    prompt = f"""
你是数据分析 Agent。只能根据以下真实查询结果回答，不得补造数据。

用户问题：{state.get('user_query', '')}
执行 SQL：{state.get('generated_sql', '')}
查询结果：{result}
用户输出偏好：{state.get('user_preferences', {}).get('response_style', 'concise')}

请输出简洁、专业的 Markdown 分析：说明核心结果，并在数据足够时给出 1-3 条明确洞察。
"""
    print("[Analyst] generating report")
    response = _invoke_llm(state, "analyst", prompt)
    return {
        "messages": [AIMessage(content=response.content)],
        "execution_trace": _trace(state, "analyst", "completed", "report generated"),
    }


def route_after_schema(state: AgentState) -> str:
    if state.get("intent") == "schema_question":
        return "schema_response"
    return "analyst" if state.get("schema_error", "").startswith("ERROR:") else "retrieval"


def route_after_supervisor(state: AgentState) -> str:
    return SUPERVISOR_INTENT_ROUTES.get(state.get("intent", ""), "help")


def route_after_validator(state: AgentState) -> str:
    if state.get("validation_result", "").startswith("ERROR:"):
        return "schema" if state.get("error_count", 0) <= MAX_RETRIES else "analyst"
    return "reviewer"


def route_after_executor(state: AgentState) -> str:
    if state.get("execution_result", "").startswith("ERROR:"):
        return "schema" if state.get("error_count", 0) <= MAX_RETRIES else "analyst"
    return "analyst"


workflow = StateGraph(AgentState)
workflow.add_node("intent", intent_agent)
workflow.add_node("supervisor", supervisor_agent)
workflow.add_node("semantic_memory", semantic_memory_agent)
workflow.add_node("schema", schema_agent)
workflow.add_node("retrieval", schema_retrieval_agent)
workflow.add_node("sql", sql_agent)
workflow.add_node("validator", validator_agent)
workflow.add_node("reviewer", sql_reviewer_agent)
workflow.add_node("executor", executor_agent)
workflow.add_node("analyst", analyst_agent)
workflow.add_node("security", security_agent)
workflow.add_node("clarification", clarification_agent)
workflow.add_node("help", help_agent)
workflow.add_node("data_source", data_source_agent)
workflow.add_node("schema_response", schema_response_agent)

workflow.set_entry_point("intent")
workflow.add_edge("intent", "supervisor")
workflow.add_conditional_edges(
    "supervisor",
    route_after_supervisor,
    {
        "semantic_memory": "semantic_memory",
        "schema": "schema",
        "data_source": "data_source",
        "help": "help",
        "security": "security",
        "clarification": "clarification",
    },
)
workflow.add_edge("semantic_memory", "schema")
workflow.add_conditional_edges(
    "schema",
    route_after_schema,
    {"retrieval": "retrieval", "analyst": "analyst", "schema_response": "schema_response"},
)
workflow.add_edge("retrieval", "sql")
workflow.add_edge("sql", "validator")
workflow.add_conditional_edges(
    "validator", route_after_validator, {"schema": "schema", "reviewer": "reviewer", "analyst": "analyst"}
)
workflow.add_edge("reviewer", "executor")
workflow.add_conditional_edges(
    "executor", route_after_executor, {"schema": "schema", "analyst": "analyst"}
)
workflow.add_edge("analyst", END)
workflow.add_edge("security", END)
workflow.add_edge("clarification", END)
workflow.add_edge("help", END)
workflow.add_edge("data_source", END)
workflow.add_edge("schema_response", END)

app = workflow.compile()
