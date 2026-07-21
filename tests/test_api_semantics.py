import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.main import app
from core_engine.request_runtime import RequestQueueFull, RequestTimedOut


class ApiSemanticsTests(unittest.TestCase):
    def test_validation_errors_use_consistent_json_and_http_status(self):
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/query",
                json={"query": ""},
                headers={"X-Request-ID": "trace-123", "Idempotency-Key": "retry-123"},
            )

        payload = response.json()
        self.assertEqual(response.status_code, 422)
        self.assertEqual(payload["code"], 422)
        self.assertEqual(payload["error"]["code"], "VALIDATION_ERROR")
        self.assertEqual(payload["error"]["request_id"], "trace-123")
        self.assertEqual(payload["error"]["idempotency_key"], "retry-123")
        self.assertFalse(payload["error"]["retryable"])
        self.assertEqual(response.headers["X-Request-ID"], "trace-123")

    def test_not_found_uses_consistent_json_and_http_status(self):
        with TestClient(app) as client:
            response = client.get("/api/v1/not-a-route")

        payload = response.json()
        self.assertEqual(response.status_code, 404)
        self.assertEqual(payload["error"]["code"], "NOT_FOUND")
        self.assertFalse(payload["error"]["retryable"])

    def test_endpoint_http_exception_uses_the_same_error_contract(self):
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/query",
                json={"query": "count orders", "data_source_id": "not-configured"},
            )

        payload = response.json()
        self.assertEqual(response.status_code, 422)
        self.assertEqual(payload["error"]["code"], "VALIDATION_ERROR")
        self.assertIn("request_id", payload["error"])

    def test_timeout_returns_gateway_timeout_with_retry_metadata(self):
        async def timed_out(*_args, **_kwargs):
            raise RequestTimedOut("timed out")

        with TestClient(app) as client, patch("api.main.request_runtime.submit", new=timed_out):
            response = client.post(
                "/api/v1/query",
                json={"query": "count orders", "client_request_id": "timeout-test"},
            )

        payload = response.json()
        self.assertEqual(response.status_code, 504)
        self.assertEqual(payload["error"]["code"], "REQUEST_TIMED_OUT")
        self.assertEqual(payload["error"]["request_id"], "timeout-test")
        self.assertTrue(payload["error"]["retryable"])

    def test_queue_full_returns_retry_after(self):
        async def queue_full(*_args, **_kwargs):
            raise RequestQueueFull("full")

        with TestClient(app) as client, patch("api.main.request_runtime.submit", new=queue_full):
            response = client.post("/api/v1/query", json={"query": "count orders"})

        payload = response.json()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(payload["error"]["code"], "QUERY_QUEUE_FULL")
        self.assertTrue(payload["error"]["retryable"])
        self.assertEqual(response.headers["Retry-After"], "3")


if __name__ == "__main__":
    unittest.main()
