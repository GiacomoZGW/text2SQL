import os
import unittest
from unittest.mock import patch

from core_engine.access_control import AccessDeniedError, access_control


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


if __name__ == "__main__":
    unittest.main()
