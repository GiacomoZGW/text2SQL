"""Golden evaluation cases and deterministic scoring for workflow regression runs."""

from pathlib import Path
from typing import Any

import yaml


class EvaluationCaseError(ValueError):
    """Raised when a golden evaluation case is malformed."""


def load_golden_cases(project_root: Path, include_extended: bool = False) -> tuple[str, list[dict[str, Any]]]:
    path = project_root / "config" / "golden_evaluation_cases.yaml"
    with path.open("r", encoding="utf-8") as case_file:
        config = yaml.safe_load(case_file) or {}
    suite_name = str(config.get("suite_name", "golden-regression"))
    cases = config.get("cases", [])
    if not isinstance(cases, list):
        raise EvaluationCaseError("cases must be a list")
    selected: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("id", "")).strip()
        query = str(case.get("query", "")).strip()
        expected_intent = str(case.get("expected_intent", "")).strip()
        if not case_id or not query or not expected_intent:
            raise EvaluationCaseError("Every case requires id, query, and expected_intent")
        if case.get("extended") is True and not include_extended:
            continue
        selected.append(case)
    return suite_name, selected


def score_final_state(case: dict[str, Any], final_state: dict[str, Any]) -> dict[str, Any]:
    """Score a workflow state without treating agent completion as answer correctness."""
    expected_intent = str(case["expected_intent"])
    actual_intent = str(final_state.get("intent", ""))
    intent_correct = actual_intent == expected_intent
    expects_sql = bool(case.get("expected_sql_contains"))
    generated_sql = str(final_state.get("generated_sql", ""))
    execution_result = str(final_state.get("execution_result", ""))
    sql_executable = None
    if expects_sql:
        expected_fragments = [str(item).lower() for item in case.get("expected_sql_contains", [])]
        sql_executable = (
            bool(generated_sql)
            and final_state.get("validation_result") == "VALID"
            and not execution_result.startswith("ERROR:")
            and all(fragment in generated_sql.lower() for fragment in expected_fragments)
        )
    expected_result_fragments = [str(item).lower() for item in case.get("expected_result_contains", [])]
    result_correct = None
    if expected_result_fragments:
        result_correct = all(fragment in execution_result.lower() for fragment in expected_result_fragments)
    passed = intent_correct and sql_executable is not False and result_correct is not False
    return {
        "status": "passed" if passed else "failed",
        "intent_expected": expected_intent,
        "intent_actual": actual_intent,
        "intent_correct": intent_correct,
        "sql_executable": sql_executable,
        "result_correct": result_correct,
        "details": {
            "expected_sql_contains": case.get("expected_sql_contains", []),
            "expected_result_contains": case.get("expected_result_contains", []),
        },
    }


def summarize_scores(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate evaluation dimensions without conflating agent events with quality."""
    total = len(results)
    passed = sum(1 for result in results if result.get("status") == "passed")
    intent_evaluated = [result for result in results if result.get("intent_correct") is not None]
    sql_evaluated = [result for result in results if result.get("sql_executable") is not None]
    result_evaluated = [result for result in results if result.get("result_correct") is not None]
    by_category: dict[str, dict[str, int]] = {}
    for result in results:
        category = str(result.get("category", "uncategorized"))
        bucket = by_category.setdefault(category, {"total": 0, "passed": 0})
        bucket["total"] += 1
        if result.get("status") == "passed":
            bucket["passed"] += 1
    return {
        "total_cases": total,
        "passed_cases": passed,
        "pass_rate": round(passed / total * 100, 2) if total else 0,
        "intent_accuracy": round(sum(bool(result["intent_correct"]) for result in intent_evaluated) / len(intent_evaluated) * 100, 2)
        if intent_evaluated
        else None,
        "sql_executable_rate": round(sum(bool(result["sql_executable"]) for result in sql_evaluated) / len(sql_evaluated) * 100, 2)
        if sql_evaluated
        else None,
        "result_correct_rate": round(sum(bool(result["result_correct"]) for result in result_evaluated) / len(result_evaluated) * 100, 2)
        if result_evaluated
        else None,
        "by_category": by_category,
    }
