import os
import time
import unittest
from unittest.mock import patch

import jwt
from fastapi import HTTPException

from core_engine.access_control import AccessDeniedError, Principal, access_control


class AccessControlTests(unittest.TestCase):
    def test_development_mode_uses_server_configured_identity(self):
        with patch.dict(os.environ, {"DATA_AGENT_AUTH_MODE": "development"}, clear=False):
            principal = access_control.authenticate("an-untrusted-client-value")

        self.assertEqual(principal.user_id, "test_user_001")
        self.assertEqual(principal.role, "admin")

    def test_api_key_principal_is_limited_to_its_data_sources(self):
        with patch.dict(
            os.environ,
            {"DATA_AGENT_AUTH_MODE": "api_key", "DATA_AGENT_ANALYST_API_KEY": "analyst-test-key"},
            clear=False,
        ):
            principal = access_control.authenticate("analyst-test-key")

        access_control.authorize_data_source(principal, "sqlite_local")
        with self.assertRaises(AccessDeniedError):
            access_control.authorize_data_source(principal, "mysql_business")

    def test_column_policy_and_tenant_filter_are_enforced_from_sql_ast(self):
        with patch.dict(
            os.environ,
            {"DATA_AGENT_AUTH_MODE": "api_key", "DATA_AGENT_ANALYST_API_KEY": "key"},
            clear=False,
        ):
            analyst = access_control.authenticate("key")

        allowed = access_control.enforce_sql_policy(
            analyst, "sqlite_local", "SELECT id, city FROM users WHERE city = 'Shanghai'", "sqlite"
        )
        self.assertIn("SELECT id, city FROM users", allowed)
        with self.assertRaises(AccessDeniedError):
            access_control.enforce_sql_policy(analyst, "sqlite_local", "SELECT email FROM users", "sqlite")

        with patch.dict(os.environ, {"DATA_AGENT_AUTH_MODE": "development"}, clear=False):
            admin = access_control.authenticate(None)
        tenant_scoped = access_control.enforce_sql_policy(admin, "mysql_business", "SELECT id FROM orders", "mysql")
        self.assertIn("tenant_id", tenant_scoped)
        self.assertIn("local_demo", tenant_scoped)

    def test_oidc_token_maps_claims_to_server_authorized_principal(self):
        now = int(time.time())
        token = jwt.encode(
            {
                "sub": "oidc-user-1",
                "tenant_id": "tenant-a",
                "roles": ["analyst"],
                "iss": "https://issuer.example.test",
                "aud": "data-agent-api",
                "exp": now + 60,
            },
            "oidc-test-secret",
            algorithm="HS256",
        )
        with patch.dict(
            os.environ,
            {
                "DATA_AGENT_AUTH_MODE": "oidc",
                "OIDC_ISSUER": "https://issuer.example.test",
                "OIDC_AUDIENCE": "data-agent-api",
                "OIDC_JWT_SECRET": "oidc-test-secret",
            },
            clear=False,
        ):
            principal = access_control.authenticate(None, f"Bearer {token}")

        self.assertEqual(principal.user_id, "oidc-user-1")
        self.assertEqual(principal.tenant_id, "tenant-a")
        self.assertEqual(principal.role, "analyst")

    def test_rate_limit_rejects_a_second_request_in_the_same_window(self):
        principal = Principal(user_id=f"rate-{time.time_ns()}", role="limited_test", tenant_id="tenant-a")
        with patch.dict(
            os.environ,
            {
                "AUTH_RATE_LIMIT_ENABLED": "true",
                "AUTH_RATE_LIMIT_WINDOW_SECONDS": "60",
                "AUTH_RATE_LIMIT_REQUESTS": "1",
            },
            clear=False,
        ):
            access_control._rate_limit(principal)
            with self.assertRaises(HTTPException) as context:
                access_control._rate_limit(principal)

        self.assertEqual(context.exception.status_code, 429)
        self.assertEqual(context.exception.headers["Retry-After"], "60")


if __name__ == "__main__":
    unittest.main()
