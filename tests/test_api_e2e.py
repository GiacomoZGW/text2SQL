"""HTTP-level regression flow that exercises authentication and API routing."""

import unittest

from fastapi.testclient import TestClient

from api.main import app


class ApiEndToEndTests(unittest.TestCase):
    def test_development_session_can_load_the_workspace_bootstrap_data(self):
        with TestClient(app) as client:
            session = client.get("/api/v1/session")
            sources = client.get("/api/v1/data-sources")
            readiness = client.get("/health/ready")

        self.assertEqual(session.status_code, 200)
        self.assertEqual(session.json()["data"]["user_id"], "test_user_001")
        self.assertEqual(sources.status_code, 200)
        self.assertTrue(sources.json()["data"])
        self.assertEqual(readiness.status_code, 200)
