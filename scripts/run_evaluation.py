"""Run versioned Text2SQL golden cases locally or in CI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.workflow import app as workflow_app
from agents.workflow import classify_intent_rules, data_source_registry
from core_engine.access_control import Principal
from core_engine.evaluation import load_golden_cases, score_final_state, summarize_scores
from langchain_core.messages import HumanMessage


def _initial_state(case: dict[str, Any]) -> dict[str, Any]:
    data_source_id = str(case.get("data_source_id", "sqlite_local"))
    source = data_source_registry.resolve(data_source_id)
    return {
        "messages": [HumanMessage(content=str(case["query"]))],
        "request_id": f"cli-eval-{case['id']}",
        "user_id": "evaluation_runner",
        "principal": Principal(user_id="evaluation_runner", role="admin", tenant_id="evaluation"),
        "data_source_id": data_source_id,
        "target_db_type": str(source["execution_mode"]),
        "conversation_context": [],
        "user_preferences": {},
        "memory_enabled": False,
        "error_count": 0,
        "similarity_threshold": 0.8,
    }


def run(include_extended: bool, offline: bool) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    suite_name, cases = load_golden_cases(PROJECT_ROOT, include_extended=include_extended)
    results: list[dict[str, Any]] = []
    for case in cases:
        if offline:
            classification = classify_intent_rules(str(case["query"])) or {}
            final_state: dict[str, Any] = {"intent": classification.get("intent", "")}
        else:
            final_state = workflow_app.invoke(_initial_state(case))
        score = score_final_state(case, final_state)
        results.append({"case_id": case["id"], "category": case.get("category", "uncategorized"), **score})
    return suite_name, results, summarize_scores(results)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-extended", action="store_true", help="Include model/database-backed cases")
    parser.add_argument("--offline", action="store_true", help="Run only deterministic intent-rule cases")
    parser.add_argument("--output", type=Path, help="Write the JSON report to this path")
    args = parser.parse_args()

    suite_name, results, summary = run(args.include_extended, args.offline)
    report = {"suite_name": suite_name, "offline": args.offline, "summary": summary, "results": results}
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if summary["passed_cases"] == summary["total_cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
