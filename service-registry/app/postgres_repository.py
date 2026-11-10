
"""PostgreSQL-backed shared service registry repository."""

from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


class PostgresRegistryRepository:
    """Shared registry storage backed by PostgreSQL."""

    def __init__(
        self,
        database_url: str,
        heartbeat_interval_seconds: int = 5,
    ):
        if not database_url:
            raise ValueError("A PostgreSQL database URL is required")

        if heartbeat_interval_seconds <= 0:
            raise ValueError(
                "heartbeat_interval_seconds must be positive"
            )

        self.database_url = database_url
        self.lease_seconds = heartbeat_interval_seconds * 3

        self._initialize()

    def _connect(self):
        return psycopg.connect(
            self.database_url,
            row_factory=dict_row,
            connect_timeout=5,
        )

    def _initialize(self):
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS service_instances (
                    instance_id TEXT PRIMARY KEY,
                    service_id TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    registered_at TIMESTAMPTZ NOT NULL,
                    last_heartbeat_at TIMESTAMPTZ NOT NULL,
                    lease_expires_at TIMESTAMPTZ NOT NULL,
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                )
                """
            )

            db.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_instances_service_status
                ON service_instances(service_id, status)
                """
            )

            db.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_instances_lease
                ON service_instances(lease_expires_at)
                """
            )

    @staticmethod
    def _validate_datetime(value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Datetime must be timezone-aware")

        return value.astimezone(timezone.utc)

    @staticmethod
    def _record(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "service_id": row["service_id"],
            "instance_id": row["instance_id"],
            "endpoint": row["endpoint"],
            "version": row["version"],
            "status": row["status"],
            "registered_at": row["registered_at"],
            "last_heartbeat_at": row["last_heartbeat_at"],
            "lease_expires_at": row["lease_expires_at"],
            "metadata": row["metadata"],
        }

    def register(
        self,
        request: dict[str, Any],
        now: datetime,
    ) -> dict[str, Any]:
        now = self._validate_datetime(now)
        expiry = now + timedelta(seconds=self.lease_seconds)

        with self._connect() as db:
            row = db.execute(
                """
                INSERT INTO service_instances (
                    instance_id,
                    service_id,
                    endpoint,
                    version,
                    status,
                    registered_at,
                    last_heartbeat_at,
                    lease_expires_at,
                    metadata
                )
                VALUES (
                    %s, %s, %s, %s, 'healthy',
                    %s, %s, %s, %s
                )
                ON CONFLICT (instance_id) DO UPDATE SET
                    endpoint = EXCLUDED.endpoint,
                    version = EXCLUDED.version,
                    status = 'healthy',
                    last_heartbeat_at = EXCLUDED.last_heartbeat_at,
                    lease_expires_at = EXCLUDED.lease_expires_at,
                    metadata = EXCLUDED.metadata
                WHERE service_instances.service_id = EXCLUDED.service_id
                RETURNING *
                """,
                (
                    request["instance_id"],
                    request["service_id"],
                    str(request["endpoint"]),
                    request["version"],
                    now,
                    now,
                    expiry,
                    Jsonb(request.get("metadata", {})),
                ),
            ).fetchone()

            if row is None:
                raise ValueError(
                    "Instance ID is already registered to a different service"
                )

            return self._record(row)

    def heartbeat(
        self,
        instance_id: str,
        now: datetime,
    ) -> dict[str, Any] | None:
        now = self._validate_datetime(now)
        expiry = now + timedelta(seconds=self.lease_seconds)

        with self._connect() as db:
            row = db.execute(
                """
                UPDATE service_instances
                SET last_heartbeat_at = %s,
                    lease_expires_at = %s
                WHERE instance_id = %s
                  AND status = 'healthy'
                  AND lease_expires_at > %s
                RETURNING *
                """,
                (now, expiry, instance_id, now),
            ).fetchone()

            if row is not None:
                return self._record(row)

            # An expired lease cannot be revived by a heartbeat.
            db.execute(
                """
                UPDATE service_instances
                SET status = 'unavailable'
                WHERE instance_id = %s
                  AND lease_expires_at <= %s
                """,
                (instance_id, now),
            )

            return None

    def deregister(self, instance_id: str) -> bool:
        with self._connect() as db:
            result = db.execute(
                """
                DELETE FROM service_instances
                WHERE instance_id = %s
                """,
                (instance_id,),
            )

            return result.rowcount > 0

    def get_service(
        self,
        service_id: str,
        now: datetime,
    ) -> list[dict[str, Any]]:
        now = self._validate_datetime(now)

        with self._connect() as db:
            rows = db.execute(
                """
                SELECT *
                FROM service_instances
                WHERE service_id = %s
                  AND status = 'healthy'
                  AND lease_expires_at > %s
                ORDER BY instance_id
                """,
                (service_id, now),
            ).fetchall()

            return [self._record(row) for row in rows]

    def list_services(
        self,
        now: datetime,
    ) -> list[dict[str, Any]]:
        now = self._validate_datetime(now)

        with self._connect() as db:
            rows = db.execute(
                """
                SELECT service_id, COUNT(*) AS instance_count
                FROM service_instances
                WHERE status = 'healthy'
                  AND lease_expires_at > %s
                GROUP BY service_id
                ORDER BY service_id
                """,
                (now,),
            ).fetchall()

            return [
                {
                    "service_id": row["service_id"],
                    "healthy_instance_count": row["instance_count"],
                }
                for row in rows
            ]

    def expire(self, now: datetime) -> int:
        now = self._validate_datetime(now)

        with self._connect() as db:
            result = db.execute(
                """
                UPDATE service_instances
                SET status = 'unavailable'
                WHERE status = 'healthy'
                  AND lease_expires_at <= %s
                """,
                (now,),
            )

            return result.rowcount

    def count(self) -> int:
        with self._connect() as db:
            row = db.execute(
                "SELECT COUNT(*) AS total FROM service_instances"
            ).fetchone()

            return row["total"]
