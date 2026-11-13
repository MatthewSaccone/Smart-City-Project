
"""PostgreSQL connection and schema management for Live Traffic."""

import psycopg


class Database:
    CONNECT_TIMEOUT_SECONDS = 5

    def __init__(self, database_url: str):
        if not database_url or not database_url.strip():
            raise ValueError("S1_DATABASE_URL must be configured")

        self.database_url = database_url

    def initialize(self):
        """Create the location-ping table and its indexes if necessary."""
        statements = (
            """
            CREATE TABLE IF NOT EXISTS traffic_location_pings (
                user_id TEXT NOT NULL,
                latitude DOUBLE PRECISION NOT NULL,
                longitude DOUBLE PRECISION NOT NULL,
                timestamp TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (user_id, timestamp)
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS
            idx_traffic_location_pings_timestamp
            ON traffic_location_pings (timestamp)
            """,
        )

        with psycopg.connect(
            self.database_url,
            connect_timeout=self.CONNECT_TIMEOUT_SECONDS,
        ) as connection:
            with connection.cursor() as cursor:
                for statement in statements:
                    cursor.execute(statement)

    def check_available(self) -> bool:
        """Check whether PostgreSQL accepts connections and queries."""
        try:
            with psycopg.connect(
                self.database_url,
                connect_timeout=self.CONNECT_TIMEOUT_SECONDS,
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    return cursor.fetchone() == (1,)
        except Exception:
            return False
    
    def try_acquire_advisory_lock(self, lock_id: int):
        """Acquire a session-level PostgreSQL advisory lock.

        Returns the open connection when acquired, or None when another
        replica currently holds the lock.
        """
        connection = None

        try:
            connection = psycopg.connect(
                self.database_url,
                connect_timeout=self.CONNECT_TIMEOUT_SECONDS,
                autocommit=True,
            )

            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_try_advisory_lock(%s)",
                    (lock_id,),
                )
                acquired = cursor.fetchone()[0]

            if acquired:
                return connection

            connection.close()
            return None

        except Exception:
            if connection is not None:
                connection.close()
            raise
