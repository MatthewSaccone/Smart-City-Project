
import os

import psycopg


class Database:
    """Connection manager and schema initializer for S3's PostgreSQL database."""

    CONNECT_TIMEOUT_SECONDS = 5

    SCHEMA_STATEMENTS = (
        """
        CREATE EXTENSION IF NOT EXISTS postgis
        """,
        """
        CREATE TABLE IF NOT EXISTS incident_reports (
            incident_id UUID PRIMARY KEY,
            incident_type TEXT NOT NULL
                CHECK (
                    incident_type IN (
                        'construction',
                        'accident',
                        'other_traffic'
                    )
                ),
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (
                    status IN (
                        'active',
                        'resolved',
                        'completed',
                        'inaccurate',
                        'withdrawn'
                    )
                ),
            latitude DOUBLE PRECISION NOT NULL
                CHECK (latitude BETWEEN -90 AND 90),
            longitude DOUBLE PRECISION NOT NULL
                CHECK (longitude BETWEEN -180 AND 180),
            location GEOGRAPHY(POINT, 4326) NOT NULL,
            CONSTRAINT incident_location_coordinates_match CHECK (
                ST_Y(location::geometry) = latitude
                AND ST_X(location::geometry) = longitude
            ),
            description TEXT NOT NULL
                CHECK (char_length(description) BETWEEN 10 AND 2000),
            observed_at TIMESTAMPTZ,
            owner_id TEXT NOT NULL,
            submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'incident_location_coordinates_match'
                  AND conrelid = 'incident_reports'::regclass
            ) THEN
                ALTER TABLE incident_reports
                ADD CONSTRAINT incident_location_coordinates_match
                CHECK (
                    ST_Y(location::geometry) = latitude
                    AND ST_X(location::geometry) = longitude
                );
            END IF;
        END
        $$
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_incident_reports_location
        ON incident_reports USING GIST (location)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_incident_reports_status
        ON incident_reports (status)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_incident_reports_submitted_at
        ON incident_reports (submitted_at)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_incident_reports_updated_at
        ON incident_reports (updated_at)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_incident_reports_status_submitted
        ON incident_reports (status, submitted_at DESC, incident_id ASC)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_incident_reports_status_updated
        ON incident_reports (status, updated_at DESC, incident_id ASC)
        """,
        """
        CREATE TABLE IF NOT EXISTS incident_audit_events (
            audit_event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            incident_id UUID NOT NULL
                REFERENCES incident_reports (incident_id),
            actor_id TEXT NOT NULL,
            action TEXT NOT NULL,
            previous_values JSONB,
            new_values JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_incident_audit_events_incident
        ON incident_audit_events (incident_id, created_at)
        """,
        """
        CREATE TABLE IF NOT EXISTS incident_idempotency_records (
            owner_id TEXT NOT NULL,
            operation TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            request_fingerprint TEXT NOT NULL,
            response_status SMALLINT NOT NULL,
            response_body JSONB NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            PRIMARY KEY (
                owner_id,
                operation,
                idempotency_key
            )
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_incident_idempotency_expiry
        ON incident_idempotency_records (expires_at)
        """,
    )

    def __init__(self, database_url: str):
        if not database_url or not database_url.strip():
            raise ValueError("INCIDENTS_DATABASE_URL is not configured")

        self.database_url = database_url.strip()

    @classmethod
    def from_env(cls):
        database_url = os.getenv("INCIDENTS_DATABASE_URL", "")

        if not database_url.strip():
            raise RuntimeError(
                "INCIDENTS_DATABASE_URL must be configured"
            )

        return cls(database_url)

    def initialize(self):
        """Create the PostGIS extension, tables, and indexes if necessary."""
        with psycopg.connect(
            self.database_url,
            connect_timeout=self.CONNECT_TIMEOUT_SECONDS,
        ) as connection:
            with connection.cursor() as cursor:
                for statement in self.SCHEMA_STATEMENTS:
                    cursor.execute(statement)

    def check_available(self) -> bool:
        """Return whether the database can currently execute queries."""
        try:
            with psycopg.connect(
                self.database_url,
                connect_timeout=self.CONNECT_TIMEOUT_SECONDS,
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    result = cursor.fetchone()

            return result == (1,)

        except Exception:
            return False
