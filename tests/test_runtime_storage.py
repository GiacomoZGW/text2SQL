import tempfile
import unittest
from pathlib import Path

from core_engine.runtime_storage import PostgreSQLConnection, RuntimeStorage, qmark_to_pyformat


class RuntimeStorageTests(unittest.TestCase):
    def test_qmark_conversion_keeps_quoted_question_marks(self):
        statement = "SELECT '?' AS literal WHERE first = ? AND escaped = 'it''s ?'"
        self.assertEqual(
            qmark_to_pyformat(statement),
            "SELECT '?' AS literal WHERE first = %s AND escaped = 'it''s ?'",
        )

    def test_sqlite_is_the_default_local_backend(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = RuntimeStorage(Path(temp_dir) / "state.db")
            connection = storage.connect()
            try:
                connection.execute("CREATE TABLE checks (value TEXT)")
                connection.execute("INSERT INTO checks (value) VALUES (?)", ("ok",))
                connection.commit()
                self.assertEqual(connection.execute("SELECT value FROM checks").fetchone()["value"], "ok")
            finally:
                connection.close()

    def test_postgres_adapter_translates_sqlite_only_insert_syntax(self):
        statement = PostgreSQLConnection._prepare("INSERT OR IGNORE INTO requests (id) VALUES (?)")
        self.assertEqual(statement, "INSERT INTO requests (id) VALUES (%s) ON CONFLICT DO NOTHING")


if __name__ == "__main__":
    unittest.main()
