import pytest

from app.database import Database


def test_database_rejects_empty_connection_string():
    with pytest.raises(ValueError):
        Database("")


def test_database_rejects_whitespace_connection_string():
    with pytest.raises(ValueError):
        Database("   ")


def test_database_from_env(monkeypatch):
    monkeypatch.setenv(
        "INCIDENTS_DATABASE_URL",
        "postgresql://test:test@localhost:5432/test_db",
    )

    database = Database.from_env()

    assert database.database_url == (
        "postgresql://test:test@localhost:5432/test_db"
    )


def test_database_from_env_requires_connection_string(monkeypatch):
    monkeypatch.delenv("INCIDENTS_DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError):
        Database.from_env()


def test_check_available_returns_true_when_database_responds(
    monkeypatch,
):
    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, query):
            assert query == "SELECT 1"

        def fetchone(self):
            return (1,)

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return FakeCursor()

    monkeypatch.setattr(
        "app.database.psycopg.connect",
        lambda *args, **kwargs: FakeConnection(),
    )

    database = Database("postgresql://test:test@localhost/test")

    assert database.check_available() is True


def test_check_available_returns_false_when_database_fails(
    monkeypatch,
):
    def failing_connect(*args, **kwargs):
        raise ConnectionError("Database unavailable")

    monkeypatch.setattr(
        "app.database.psycopg.connect",
        failing_connect,
    )

    database = Database("postgresql://test:test@localhost/test")

    assert database.check_available() is False


def test_initialize_creates_all_schema_objects(monkeypatch):
    executed_statements = []

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, statement):
            executed_statements.append(statement)

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return FakeCursor()

    monkeypatch.setattr(
        "app.database.psycopg.connect",
        lambda *args, **kwargs: FakeConnection(),
    )

    database = Database("postgresql://test:test@localhost/test")
    database.initialize()

    executed_sql = "\n".join(executed_statements)

    assert "CREATE EXTENSION IF NOT EXISTS postgis" in executed_sql
    assert "CREATE TABLE IF NOT EXISTS incident_reports" in executed_sql
    assert "CREATE TABLE IF NOT EXISTS incident_audit_events" in executed_sql
    assert (
        "CREATE TABLE IF NOT EXISTS incident_idempotency_records"
        in executed_sql
    )
    assert "USING GIST (location)" in executed_sql
