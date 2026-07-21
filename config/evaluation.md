# Evaluation and Metrics

## Online metrics

The monitoring page distinguishes four quality layers instead of presenting a single ambiguous hit rate:

- Technical success rate: successful terminal requests divided by terminal requests. `running` and restart-aborted requests are excluded from this denominator.
- SQL executable rate: executable SQL divided by requests that attempted SQL generation.
- Result correctness rate: approved-correct results divided by results that have been reviewed.
- User satisfaction: positive feedback divided by submitted feedback.

At startup, requests left in `running` longer than `REQUEST_STALE_AFTER_SECONDS` (default `300`) are marked `aborted`. They remain visible as lifecycle diagnostics but do not reduce technical success.

Users can submit feedback with `POST /api/v1/requests/{request_id}/feedback`. Administrators record correctness with `POST /api/v1/requests/{request_id}/result-evaluation`.

## Golden regression suite

`golden_evaluation_cases.yaml` is the versioned question set. Each case declares an expected intent and can optionally require SQL fragments and expected result fragments.

Administrators can inspect and run the suite:

```text
GET  /api/v1/evaluation/cases
POST /api/v1/evaluation/run
GET  /api/v1/evaluation/runs/{run_id}
```

The default suite contains stable, local-rule cases. Pass `{"include_extended": true}` to include SQL cases that can invoke the configured model and database. Evaluation requests and Agent events are flagged separately and excluded from production dashboard calculations.

For CI and release gates, run the deterministic subset without model credentials:

```text
python scripts/run_evaluation.py --offline --output artifacts/evaluation.json
```

The generated report separates intent accuracy, SQL executable rate, result correctness, and category-level pass counts. Run the extended suite in a protected environment after any prompt, model, Schema Catalog, connector, or authorization-policy change.
