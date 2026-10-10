import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.main import app
from api_test_case import ConfiguredApiTestCase
from core_engine.runtime_config import ModelRuntimeConfig


class ApiHealthTests(ConfiguredApiTestCase):
    def test_liveness_and_readiness_probes(self):
        with TestClient(app) as client:
            live = client.get("/health/live")
            ready = client.get("/health/ready")

        self.assertEqual(live.status_code, 200)
        self.assertEqual(live.json(), {"status": "live"})
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.json()["status"], "ready")
        self.assertGreaterEqual(ready.json()["enabled_data_sources"], 1)

    def test_missing_model_credentials_leave_service_not_ready(self):
        with patch("api.main.model_runtime_config", ModelRuntimeConfig()), TestClient(app) as client:
            live = client.get("/health/live")
            ready = client.get("/health/ready")

        self.assertEqual(live.status_code, 200)
        self.assertEqual(ready.status_code, 503)
        self.assertEqual(ready.json()["status"], "not_ready")
        self.assertEqual(ready.json()["components"]["model"]["credential_source"], "missing")


if __name__ == "__main__":
    unittest.main()
