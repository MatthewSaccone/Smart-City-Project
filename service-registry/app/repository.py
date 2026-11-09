
"""SQLite-backed service registry repository.

SQLite is intended for local development and a single registry process.
It is not the shared, replicated production store required for ASR4.
"""

import json
import sqlite3
import threading

from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any


class RegistryRepository:
    def __init__(
        self,
        database_path: str,
        heartbeat_interval_seconds: int = 5,
    ):
        if heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be positive")

        self.database_path = database_path
        self.lease_seconds = heartbeat_interval_seconds * 3
        self._lock = threading.RLock()

        if database_path != ":memory:":
            Path(database_path).parent.mkdir(
                parents=True,
                exist_ok=True,
            )

        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(
            self.database_path,
            timeout=10,
        )
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self):
        with self._lock, self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")

            db.execute(
                """
                CREATE TABLE IF NOT EXISTS service_instances (
                    instance_id TEXT PRIMARY KEY,
                    service_id TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    registered_at TEXT NOT NULL,
                    last_heartbeat_at TEXT NOT NULL,
                    lease_expires_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
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
    def _iso(value: datetime) -> str:
        if value.tzinfo is None:
            raise ValueError("Datetime must be timezone-aware")

        return value.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _record(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "service_id": row["service_id"],
            "instance_id": row["instance_id"],
            "endpoint": row["endpoint"],
            "version": row["version"],
            "status": row["status"],
            "registered_at": row["registered_at"],
            "last_heartbeat_at": row["last_heartbeat_at"],
            "lease_expires_at": row["lease_expires_at"],
            "metadata": json.loads(row["metadata_json"]),
        }

    def register(
        self,
        request: dict[str, Any],
        now: datetime,
    ) -> dict[str, Any]:
        now_s = self._iso(now)
        expiry = self._iso(
            now + timedelta(seconds=self.lease_seconds)
        )

        with self._lock, self._connect() as db:
            existing = db.execute(
                """
                SELECT service_id, registered_at
                FROM service_instances
                WHERE instance_id = ?
                """,
                (request["instance_id"],),
            ).fetchone()

            if existing and existing["service_id"] != request["service_id"]:
                raise ValueError(
                    "Instance ID is already registered to a different service"
                )

            registered_at = (
                existing["registered_at"] if existing else now_s
            )

            db.execute(
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
                    metadata_json
                )
                VALUES (?, ?, ?, ?, 'healthy', ?, ?, ?, ?)
                ON CONFLICT(instance_id) DO UPDATE SET
                    endpoint = excluded.endpoint,
                    version = excluded.version,
                    status = 'healthy',
                    last_heartbeat_at = excluded.last_heartbeat_at,
                    lease_expires_at = excluded.lease_expires_at,
                    metadata_json = excluded.metadata_json
                """,
                (
                    request["instance_id"],
                    request["service_id"],
                    request["endpoint"],
                    request["version"],
                    registered_at,
                    now_s,
                    expiry,
                    json.dumps(request.get("metadata", {})),
                ),
            )

            row = db.execute(
                """
                SELECT *
                FROM service_instances
                WHERE instance_id = ?
                """,
                (request["instance_id"],),
            ).fetchone()

            return self._record(row)

    def heartbeat(
        self,
        instance_id: str,
        now: datetime,
    ) -> dict[str, Any] | None:
        now_s = self._iso(now)
        expiry = self._iso(
            now + timedelta(seconds=self.lease_seconds)
        )

        with self._lock, self._connect() as db:
            existing = db.execute(
                """
                SELECT lease_expires_at, status
                FROM service_instances
                WHERE instance_id = ?
                """,
                (instance_id,),
            ).fetchone()

            if existing is None:
                return None

            # An expired lease cannot be revived by a heartbeat.
            if (
                existing["status"] != "healthy"
                or existing["lease_expires_at"] <= now_s
            ):
                db.execute(
                    """
                    UPDATE service_instances
                    SET status = 'unavailable'
                    WHERE instance_id = ?
                    """,
                    (instance_id,),
                )
                return None

            db.execute(
                """
                UPDATE service_instances
                SET last_heartbeat_at = ?,
                    lease_expires_at = ?
                WHERE instance_id = ?
                """,
                (now_s, expiry, instance_id),
            )

            row = db.execute(
                """
                SELECT *
                FROM service_instances
                WHERE instance_id = ?
                """,
                (instance_id,),
            ).fetchone()

            return self._record(row)

    def deregister(self, instance_id: str) -> bool:
        with self._lock, self._connect() as db:
            cursor = db.execute(
                """
                DELETE FROM service_instances
                WHERE instance_id = ?
                """,
                (instance_id,),
            )

            return cursor.rowcount > 0

    def get_service(
        self,
        service_id: str,
        now: datetime,
    ) -> list[dict[str, Any]]:
        with self._lock, self._connect() as db:
            rows = db.execute(
                """
                SELECT *
                FROM service_instances
                WHERE service_id = ?
                  AND status = 'healthy'
                  AND lease_expires_at > ?
                ORDER BY instance_id
                """,
                (service_id, self._iso(now)),
            ).fetchall()

            return [self._record(row) for row in rows]

    def list_services(
        self,
        now: datetime,
    ) -> list[dict[str, Any]]:
        with self._lock, self._connect() as db:
            rows = db.execute(
                """
                SELECT service_id, COUNT(*) AS instance_count
                FROM service_instances
                WHERE status = 'healthy'
                  AND lease_expires_at > ?
                GROUP BY service_id
                ORDER BY service_id
                """,
                (self._iso(now),),
            ).fetchall()

            return [
                {
                    "service_id": row["service_id"],
                    "healthy_instance_count": row["instance_count"],
                }
                for row in rows
            ]

    def expire(self, now: datetime) -> int:
        with self._lock, self._connect() as db:
            cursor = db.execute(
                """
                UPDATE service_instances
                SET status = 'unavailable'
                WHERE status = 'healthy'
                  AND lease_expires_at <= ?
                """,
                (self._iso(now),),
            )

            return cursor.rowcount

    def count(self) -> int:
        with self._lock, self._connect() as db:
            return db.execute(
                "SELECT COUNT(*) FROM service_instances"
            ).fetchone()[0]
