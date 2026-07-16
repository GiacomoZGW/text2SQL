import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from core_engine.database_connectors import ConnectorError, SqlAlchemyConnector


class DatabaseConnectorTests(unittest.TestCase):
    def setUp(self):
        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.path = Path(path)
        connection = sqlite3.connect(self.path)
        connection.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, amount REAL)")
        connection.executemany("INSERT INTO orders (amount) VALUES (?)", [(10.0,), (20.0,), (30.0,)])
        connection.commit()
        connection.close()
        self.env_name = "TEST_DIRECT_SQLITE_URI"
        os.environ[self.env_name] = f"sqlite:///{self.path.as_posix()}"
        self.connector = SqlAlchemyConnector(
            {
                "id": "test_direct_sqlite",
                "engine": "sqlite",
                "connection_secret_ref": self.env_name,
                "connector": {
                    "pool_size": 1,
                    "max_overflow": 0,
                    "connect_timeout_seconds": 2,
                    "query_timeout_seconds": 2,
                    "max_rows": 2,
                },
            }
        )

    def tearDown(self):
        self.connector.dispose()
        os.environ.pop(self.env_name, None)
        self.path.unlink(missing_ok=True)

    def test_health_schema_and_row_cap(self):
        self.assertTrue(self.connector.health_check()["healthy"])
        self.assertIn("Table: orders", self.connector.get_schema())

        rows = self.connector.execute("SELECT id, amount FROM orders ORDER BY id")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["id"], 1)

    def test_write_sql_is_rejected_at_connector_boundary(self):
        with self.assertRaises(ConnectorError):
            self.connector.execute("DELETE FROM orders")

        with self.assertRaises(ConnectorError):
            self.connector.explain("SELECT 1; SELECT 2")


if __name__ == "__main__":
    unittest.main()
