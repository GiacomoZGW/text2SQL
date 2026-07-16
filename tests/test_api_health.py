import unittest

from fastapi.testclient import TestClient

from api.main import app


class ApiHealthTests(unittest.TestCase):
    def test_liveness_and_readiness_probes(self):
        with TestClient(app) as client:
            live = client.get("/health/live")
            ready = client.get("/health/ready")

        self.assertEqual(live.status_code, 200)
        self.assertEqual(live.json(), {"status": "live"})
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.json()["status"], "ready")
        self.assertGreaterEqual(ready.json()["enabled_data_sources"], 1)


if __name__ == "__main__":
    unittest.main()
