"""Build bounded, role-specific LLM context from persisted workflow state.

The application keeps complete conversation turns and query results in storage,
but those records are cold storage. This module creates the smaller hot
context that is safe to inject into a particular agent prompt.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Mapping


def _positive_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


# Character budgets are deliberately conservative and provider-neutral. API
# usage remains the authoritative token count; these limits prevent one large
# record from making a prompt unexpectedly huge before that call is made.
RECENT_TURNS_MAX_CHARS = _positive_int("CONTEXT_RECENT_TURNS_MAX_CHARS", 6_000)
SEMANTIC_MEMORY_MAX_CHARS = _positive_int("CONTEXT_SEMANTIC_MEMORY_MAX_CHARS", 4_000)
SCHEMA_MAX_CHARS = _positive_int("CONTEXT_SCHEMA_MAX_CHARS", 16_000)
RESULT_MAX_CHARS = _positive_int("CONTEXT_RESULT_MAX_CHARS", 12_000)
RESULT_MAX_ROWS = _positive_int("CONTEXT_RESULT_MAX_ROWS", 20)
FEEDBACK_MAX_CHARS = _positive_int("CONTEXT_FEEDBACK_MAX_CHARS", 2_000)
CELL_MAX_CHARS = _positive_int("CONTEXT_RESULT_CELL_MAX_CHARS", 500)
CHECKPOINT_MAX_CHARS = _positive_int("CONTEXT_CHECKPOINT_MAX_CHARS", 3_500)
PACKAGE_OVERHEAD_CHARS = 1_000


@dataclass(frozen=True)
class ContextPackage:
    """A prompt-ready context view and its budgeting metadata."""

    agent: str
    content: str
    max_chars: int
    truncated_sections: tuple[str, ...]

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "context_chars": len(self.content),
            "context_estimated_tokens": (len(self.content) + 3) // 4,
            "context_budget_chars": self.max_chars,
            "truncated_sections": list(self.truncated_sections),
        }


def _truncate(value: Any, limit: int) -> tuple[str, bool]:
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text, False
    return f"{text[:limit]}... [truncated {len(text) - limit} chars]", True


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _bounded_json_items(items: list[dict[str, Any]], limit: int) -> tuple[str, bool]:
    selected: list[dict[str, Any]] = []
    truncated = False
    for item in items:
        candidate = [*selected, item]
        if len(_json(candidate)) > limit:
            truncated = True
            break
        selected.append(item)
    if len(selected) < len(items):
        truncated = True
    payload: dict[str, Any] = {"items": selected}
    if truncated:
        payload["notice"] = f"Only {len(selected)} of {len(items)} records fit this context budget."
    rendered, rendered_truncated = _truncate(_json(payload), limit)
    return rendered, truncated or rendered_truncated


def _recent_turns(state: Mapping[str, Any]) -> tuple[str, bool]:
    turns = state.get("conversation_context") or []
    compact_turns: list[dict[str, Any]] = []
    for turn in turns:
        if not isinstance(turn, Mapping):
            continue
        query, _ = _truncate(turn.get("user_query"), 700)
        answer, _ = _truncate(turn.get("answer_summary"), 700)
        entities, _ = _truncate(_json(turn.get("entities") or {}), 500)
        compact_turns.append(
            {
                "user_query": query,
                "intent": str(turn.get("intent") or "")[:80],
                "entities": entities,
                "answer_summary": answer,
                "data_source_id": str(turn.get("data_source_id") or "")[:120],
            }
        )
    return _bounded_json_items(compact_turns, RECENT_TURNS_MAX_CHARS)


def _checkpoint(state: Mapping[str, Any]) -> tuple[str, bool]:
    checkpoint = state.get("conversation_checkpoint") or {}
    if not isinstance(checkpoint, Mapping):
        return "{}", False
    entities = checkpoint.get("confirmed_entities") or {}
    payload = {
        "active_data_source_id": checkpoint.get("active_data_source_id"),
        "latest_intent": checkpoint.get("latest_intent"),
        "confirmed_entities": entities if isinstance(entities, Mapping) else {},
        "latest_user_query": checkpoint.get("latest_user_query"),
        "latest_answer_summary": checkpoint.get("latest_answer_summary"),
        "last_successful_sql": checkpoint.get("last_successful_sql"),
        "last_error": checkpoint.get("last_error"),
        "updated_at": checkpoint.get("updated_at"),
    }
    return _truncate(_json(payload), CHECKPOINT_MAX_CHARS)


def _preferences(state: Mapping[str, Any]) -> tuple[str, bool]:
    preferences = state.get("user_preferences") or {}
    if not isinstance(preferences, Mapping):
        return "{}", False
    aliases = preferences.get("metric_aliases") or {}
    if not isinstance(aliases, Mapping):
        aliases = {}
    compact_aliases = {
        str(key)[:100]: str(value)[:200]
        for key, value in list(aliases.items())[:20]
    }
    payload = {
        "default_data_source_id": preferences.get("default_data_source_id"),
        "response_style": preferences.get("response_style"),
        "show_sql": preferences.get("show_sql"),
        "default_limit": preferences.get("default_limit"),
        "metric_aliases": compact_aliases,
    }
    return _truncate(_json(payload), 2_000)


def _semantic_memories(state: Mapping[str, Any]) -> tuple[str, bool]:
    memories = state.get("semantic_memories") or []
    compact_memories: list[dict[str, Any]] = []
    for memory in memories:
        if not isinstance(memory, Mapping):
            continue
        definition, _ = _truncate(memory.get("definition"), 1_000)
        template, _ = _truncate(memory.get("sql_template"), 1_000)
        metadata, _ = _truncate(_json(memory.get("metadata") or {}), 500)
        compact_memories.append(
            {
                "kind": str(memory.get("kind") or "")[:80],
                "label": str(memory.get("label") or "")[:200],
                "definition": definition,
                "sql_template": template,
                "metadata": metadata,
                "similarity": memory.get("similarity"),
            }
        )
    return _bounded_json_items(compact_memories, SEMANTIC_MEMORY_MAX_CHARS)


def _schema(state: Mapping[str, Any]) -> tuple[str, bool]:
    schema = str(state.get("relevant_schemas") or state.get("schema_context") or "")
    if len(schema) <= SCHEMA_MAX_CHARS:
        return schema, False

    # Preserve complete table sections first. A partial final section is still
    # useful, but the explicit notice prevents the model from assuming it saw
    # the whole catalog.
    parts = [part.strip() for part in schema.split("\n\n") if part.strip()]
    notice = "\n\n[Additional schema sections omitted by context budget.]"
    section_budget = max(1, SCHEMA_MAX_CHARS - len(notice))
    kept: list[str] = []
    used = 0
    for part in parts:
        separator = 2 if kept else 0
        if used + separator + len(part) > section_budget:
            break
        kept.append(part)
        used += separator + len(part)
    if not kept:
        text, _ = _truncate(schema, SCHEMA_MAX_CHARS)
        return text, True
    return "\n\n".join(kept) + notice, True


def bound_schema_context(schema: str) -> tuple[str, bool]:
    """Apply the schema budget before a retrieved schema enters workflow state."""
    return _schema({"relevant_schemas": schema})


def _result(state: Mapping[str, Any]) -> tuple[str, bool]:
    raw = str(state.get("execution_result") or "")
    if raw.startswith("ERROR:"):
        return _truncate(raw, FEEDBACK_MAX_CHARS)
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return _truncate(raw, RESULT_MAX_CHARS)
    if not isinstance(parsed, list):
        return _truncate(_json(parsed), RESULT_MAX_CHARS)

    rows: list[dict[str, Any]] = []
    cell_truncated = False
    for row in parsed[:RESULT_MAX_ROWS]:
        if not isinstance(row, Mapping):
            rows.append({"value": row})
            continue
        compact_row: dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, str):
                compact_value, was_truncated = _truncate(value, CELL_MAX_CHARS)
                compact_row[str(key)[:160]] = compact_value
                cell_truncated = cell_truncated or was_truncated
            else:
                compact_row[str(key)[:160]] = value
        rows.append(compact_row)
    payload: dict[str, Any] = {
        "returned_row_count": len(parsed),
        "shown_rows": len(rows),
        "rows": rows,
    }
    if len(parsed) > len(rows):
        payload["notice"] = "Rows beyond shown_rows are retained in cold storage but not included in this prompt."
    text, truncated_by_size = _truncate(_json(payload), RESULT_MAX_CHARS)
    return text, cell_truncated or len(parsed) > len(rows) or truncated_by_size


def _feedback(state: Mapping[str, Any]) -> tuple[str, bool]:
    feedback = state.get("validation_result") or state.get("execution_result") or "none"
    return _truncate(feedback, FEEDBACK_MAX_CHARS)


def build_agent_context(state: Mapping[str, Any], agent: str) -> ContextPackage:
    """Return the minimum bounded context required by a named workflow agent."""
    sections: list[tuple[str, str]] = []
    truncated: list[str] = []

    def add(name: str, value: str, was_truncated: bool) -> None:
        sections.append((name, value))
        if was_truncated:
            truncated.append(name)

    if agent == "intent":
        checkpoint, checkpoint_truncated = _checkpoint(state)
        recent, was_truncated = _recent_turns(state)
        prefs, prefs_truncated = _preferences(state)
        add("Structured conversation checkpoint", checkpoint, checkpoint_truncated)
        add("Recent conversation turns", recent, was_truncated)
        add("User preferences", prefs, prefs_truncated)
    elif agent == "sql":
        checkpoint, checkpoint_truncated = _checkpoint(state)
        recent, recent_truncated = _recent_turns(state)
        prefs, prefs_truncated = _preferences(state)
        semantic, semantic_truncated = _semantic_memories(state)
        add("Structured conversation checkpoint", checkpoint, checkpoint_truncated)
        add("Recent conversation turns", recent, recent_truncated)
        add("User preferences", prefs, prefs_truncated)
        add("Relevant semantic memory", semantic, semantic_truncated)
    elif agent == "reviewer":
        schema, schema_truncated = _schema(state)
        plan, plan_truncated = _truncate(_json(state.get("query_plan") or {}), 3_000)
        add("Query plan", plan, plan_truncated)
        add("Relevant live schema", schema, schema_truncated)
    elif agent == "analyst":
        result, result_truncated = _result(state)
        sql, sql_truncated = _truncate(state.get("generated_sql") or "", 4_000)
        add("Executed SQL", sql, sql_truncated)
        add("Actual query result", result, result_truncated)
    else:
        raise ValueError(f"Unsupported context agent: {agent}")

    content = "\n\n".join(f"### {name}\n{value}" for name, value in sections)
    max_chars = {
        "intent": CHECKPOINT_MAX_CHARS + RECENT_TURNS_MAX_CHARS + 2_000 + PACKAGE_OVERHEAD_CHARS,
        "sql": CHECKPOINT_MAX_CHARS + RECENT_TURNS_MAX_CHARS + 2_000 + SEMANTIC_MEMORY_MAX_CHARS + PACKAGE_OVERHEAD_CHARS,
        "reviewer": SCHEMA_MAX_CHARS + 3_000 + PACKAGE_OVERHEAD_CHARS,
        "analyst": RESULT_MAX_CHARS + 4_000 + PACKAGE_OVERHEAD_CHARS,
    }[agent]
    return ContextPackage(agent, content, max_chars, tuple(truncated))
