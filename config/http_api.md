# HTTP API Semantics

All successful API responses retain the existing envelope:

```json
{"code": 200, "data": {}}
```

Error responses always use the real HTTP status and a stable envelope:

```json
{
  "code": 429,
  "error": {
    "code": "QUERY_QUEUE_FULL",
    "type": "RATE_LIMITED",
    "message": "The query queue is busy. Retry shortly.",
    "request_id": "server-correlation-id",
    "idempotency_key": "client-request-id",
    "retryable": true,
    "details": {"runtime_status": "queue_rejected"}
  }
}
```

`X-Request-ID` is returned on every response. Clients may send it to continue a
trace. Query clients should send an `Idempotency-Key`; the browser workspace
uses its generated request ID for this header and `client_request_id`.

| Situation | HTTP | Error code | Retry |
| --- | --- | --- | --- |
| Invalid body or source | 422 | `VALIDATION_ERROR` | No |
| Authentication or permission failure | 401/403 | `UNAUTHENTICATED` / `FORBIDDEN` | No |
| Missing resource | 404 | `NOT_FOUND` | No |
| User-paused request | 409 | `REQUEST_PAUSED` | Yes |
| Queue at capacity | 429 | `QUERY_QUEUE_FULL` | Yes, honor `Retry-After` |
| State, queue, or source unavailable | 503 | `SERVICE_UNAVAILABLE` / `DATA_SOURCE_UNAVAILABLE` | Yes |
| Request timeout | 504 | `REQUEST_TIMED_OUT` | Yes |
| Unexpected execution failure | 500 | `QUERY_EXECUTION_ERROR` | Usually yes |

Durable query submission returns `202 Accepted`; poll the returned
`result_url`. A terminal error preserves the same error object and its matching
HTTP status when that result is fetched.
