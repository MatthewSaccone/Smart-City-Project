"""Connections and non-destructive schema initialization for S4's own database."""

import psycopg


SCHEMA = """
CREATE TABLE IF NOT EXISTS parking_datasets (
    stream TEXT PRIMARY KEY CHECK (stream IN ('catalog','pricing','occupancy')),
    revision TEXT NOT NULL,
    payload JSONB NOT NULL CHECK (octet_length(payload::text) <= 8388608),
    received_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
)
"""


class Database:
    """Manage bounded connections without sharing another service's storage."""

    CONNECT_TIMEOUT_SECONDS = 2
    STATEMENT_TIMEOUT_MS = 2000
    # Serialize schema creation when multiple S4 replicas start together.
    SCHEMA_LOCK_ID = 7420430004

    def __init__(self, database_url: str):
        if not database_url or not database_url.strip():
            raise ValueError("PARKING_DATABASE_URL must be configured")
        self.database_url = database_url.strip()

    def connect(self):
        return psycopg.connect(
            self.database_url,
            autocommit=True,
            connect_timeout=self.CONNECT_TIMEOUT_SECONDS,
            options=f"-c statement_timeout={self.STATEMENT_TIMEOUT_MS}",
        )

    def initialize(self, connection=None):
        """Create the schema, preserving existing rows and their timestamps.

        Accept the ingestion worker's connection so initialization and its first
        read use the same database. A failed attempt is retried on reconnect.
        Schema setup never publishes or seeds simulated data.
        """
        if connection is None:
            with self.connect() as connection:
                self.initialize(connection)
            return
        with connection.transaction():
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (self.SCHEMA_LOCK_ID,))
            connection.execute(SCHEMA)
