
import hashlib
import json

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import psycopg

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.database import Database
from app.models import (
    IncidentCreateRequest,
    IncidentListQuery,
    IncidentPatchRequest,
)


IDEMPOTENCY_RETENTION_HOURS = 24
STALE_AFTER_DAYS = 7

IDEMPOTENCY_OPERATION = "POST:/v1/incidents"

ALLOWED_STATUS_TRANSITIONS = {
    "active": {"resolved", "completed", "inaccurate"},
    "resolved": {"active"},
    "completed": {"active"},
    "inaccurate": {"active"},
}


class IdempotencyConflict(Exception):
    """Raised when an idempotency key is reused with a different payload."""


class IncidentNotFound(Exception):
    """Raised when an incident does not exist or is publicly unavailable."""


class IncidentForbidden(Exception):
    """Raised when an actor is not authorized to modify an incident."""


class InvalidStatusTransition(Exception):
    """Raised when an incident status transition is not permitted."""


class IncidentRepository:
    """PostgreSQL persistence operations for construction and incidents."""

    SORT_COLUMNS = {
        "submitted_at": "submitted_at",
        "updated_at": "updated_at",
        "distance_from_center_m": "distance_from_center_m",
    }

    def __init__(self, database: Database):
        self.database = database

    def _connect(self):
        return psycopg.connect(
            self.database.database_url,
            connect_timeout=Database.CONNECT_TIMEOUT_SECONDS,
            row_factory=dict_row,
        )

    @staticmethod
    def _utc_now():
        return datetime.now(timezone.utc)

    @staticmethod
    def _iso(value):
        if value is None:
            return None

        return (
            value.astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )

    @staticmethod
    def _fingerprint(request: IncidentCreateRequest) -> str:
        canonical_payload = json.dumps(
            request.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )

        return hashlib.sha256(
            canonical_payload.encode("utf-8")
        ).hexdigest()

    @classmethod
    def _public_detail(cls, row, as_of):
        updated_at = row["updated_at"]

        is_stale = (
            row["status"] == "active"
            and as_of - updated_at
            > timedelta(days=STALE_AFTER_DAYS)
        )

        return {
            "incident_id": str(row["incident_id"]),
            "incident_type": row["incident_type"],
            "status": row["status"],
            "location": {
                "latitude": row["latitude"],
                "longitude": row["longitude"],
            },
            "description": row["description"],
            "observed_at": cls._iso(row["observed_at"]),
            "submitted_at": cls._iso(row["submitted_at"]),
            "updated_at": cls._iso(updated_at),
            "source": "user_report",
            "is_live": False,
            "is_stale": is_stale,
            "data_status": "user_report",
            "as_of": cls._iso(as_of),
        }

    @classmethod
    def _public_list_item(cls, row, as_of):
        updated_at = row["updated_at"]

        is_stale = (
            row["status"] == "active"
            and as_of - updated_at
            > timedelta(days=STALE_AFTER_DAYS)
        )

        return {
            "incident_id": str(row["incident_id"]),
            "incident_type": row["incident_type"],
            "status": row["status"],
            "location": {
                "latitude": row["latitude"],
                "longitude": row["longitude"],
            },
            "submitted_at": cls._iso(row["submitted_at"]),
            "updated_at": cls._iso(updated_at),
            "source": "user_report",
            "is_live": False,
            "is_stale": is_stale,
            "data_status": "user_report",
            "distance_from_center_m": float(
                row["distance_from_center_m"]
            ),
        }

    @staticmethod
    def _can_modify(row, actor_id: str, is_moderator: bool):
        return is_moderator or row["owner_id"] == actor_id

    @staticmethod
    def _audit_snapshot(row):
        """Return JSON-serializable mutable incident fields."""
        return {
            "incident_type": row["incident_type"],
            "location": {
                "latitude": row["latitude"],
                "longitude": row["longitude"],
            },
            "description": row["description"],
            "status": row["status"],
        }

    def create_incident(
        self,
        request: IncidentCreateRequest,
        owner_id: str,
        idempotency_key: str,
    ) -> tuple[int, dict, bool]:
        """
        Create an incident and its idempotency record atomically.

        Returns:
            (HTTP status, original response body, replayed)
        """
        fingerprint = self._fingerprint(request)
        now = self._utc_now()
        expires_at = now + timedelta(
            hours=IDEMPOTENCY_RETENTION_HOURS
        )

        scope = (
            f"{owner_id}:{IDEMPOTENCY_OPERATION}:{idempotency_key}"
        )

        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT pg_advisory_xact_lock(
                        hashtextextended(%s, 0)
                    )
                    """,
                    (scope,),
                )

                cursor.execute(
                    """
                    SELECT
                        request_fingerprint,
                        response_status,
                        response_body,
                        expires_at
                    FROM incident_idempotency_records
                    WHERE owner_id = %s
                      AND operation = %s
                      AND idempotency_key = %s
                    FOR UPDATE
                    """,
                    (
                        owner_id,
                        IDEMPOTENCY_OPERATION,
                        idempotency_key,
                    ),
                )

                existing = cursor.fetchone()

                if existing and existing["expires_at"] > now:
                    if existing["request_fingerprint"] != fingerprint:
                        raise IdempotencyConflict()

                    return (
                        existing["response_status"],
                        existing["response_body"],
                        True,
                    )

                if existing:
                    cursor.execute(
                        """
                        DELETE FROM incident_idempotency_records
                        WHERE owner_id = %s
                          AND operation = %s
                          AND idempotency_key = %s
                        """,
                        (
                            owner_id,
                            IDEMPOTENCY_OPERATION,
                            idempotency_key,
                        ),
                    )

                incident_id = uuid4()

                latitude = request.location.latitude
                longitude = request.location.longitude

                cursor.execute(
                    """
                    INSERT INTO incident_reports (
                        incident_id,
                        incident_type,
                        status,
                        latitude,
                        longitude,
                        location,
                        description,
                        observed_at,
                        owner_id
                    )
                    VALUES (
                        %s,
                        %s,
                        'active',
                        %s,
                        %s,
                        ST_SetSRID(
                            ST_MakePoint(%s, %s),
                            4326
                        )::geography,
                        %s,
                        %s,
                        %s
                    )
                    RETURNING
                        incident_id,
                        incident_type,
                        status,
                        latitude,
                        longitude,
                        description,
                        observed_at,
                        submitted_at,
                        updated_at
                    """,
                    (
                        incident_id,
                        request.incident_type,
                        latitude,
                        longitude,
                        longitude,
                        latitude,
                        request.description,
                        request.observed_at,
                        owner_id,
                    ),
                )

                row = cursor.fetchone()
                response_body = self._public_detail(
                    row,
                    self._utc_now(),
                )

                cursor.execute(
                    """
                    INSERT INTO incident_idempotency_records (
                        owner_id,
                        operation,
                        idempotency_key,
                        request_fingerprint,
                        response_status,
                        response_body,
                        expires_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        owner_id,
                        IDEMPOTENCY_OPERATION,
                        idempotency_key,
                        fingerprint,
                        201,
                        Jsonb(response_body),
                        expires_at,
                    ),
                )

                return 201, response_body, False

    def get_incident(self, incident_id: UUID):
        """Retrieve a public incident, excluding withdrawn reports."""
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        incident_id,
                        incident_type,
                        status,
                        latitude,
                        longitude,
                        description,
                        observed_at,
                        submitted_at,
                        updated_at
                    FROM incident_reports
                    WHERE incident_id = %s
                      AND status <> 'withdrawn'
                    """,
                    (incident_id,),
                )

                row = cursor.fetchone()

        if row is None:
            return None

        return self._public_detail(row, self._utc_now())

    def list_incidents(self, query: IncidentListQuery):
        """Search public incidents within a PostGIS radius."""
        as_of = self._utc_now()

        center_longitude = query.longitude
        center_latitude = query.latitude

        distance_expression = """
            ST_Distance(
                location,
                ST_SetSRID(
                    ST_MakePoint(%s, %s),
                    4326
                )::geography
            )
        """

        filters = [
            """
            ST_DWithin(
                location,
                ST_SetSRID(
                    ST_MakePoint(%s, %s),
                    4326
                )::geography,
                %s
            )
            """,
            "status <> 'withdrawn'",
        ]

        filter_params = [
            center_longitude,
            center_latitude,
            query.radius_m,
        ]

        if query.status != "all":
            filters.append("status = %s")
            filter_params.append(query.status)

        if query.incident_type is not None:
            filters.append("incident_type = %s")
            filter_params.append(query.incident_type)

        where_clause = " AND ".join(filters)

        sort_column = self.SORT_COLUMNS[query.sort_by]
        sort_direction = query.sort_order.upper()

        order_expression = (
            f"{sort_column} {sort_direction}, incident_id ASC"
        )

        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"
                )

                cursor.execute(
                    f"""
                    SELECT COUNT(*) AS total
                    FROM incident_reports
                    WHERE {where_clause}
                    """,
                    filter_params,
                )

                total = cursor.fetchone()["total"]

                cursor.execute(
                    f"""
                    SELECT
                        incident_id,
                        incident_type,
                        status,
                        latitude,
                        longitude,
                        submitted_at,
                        updated_at,
                        {distance_expression}
                            AS distance_from_center_m
                    FROM incident_reports
                    WHERE {where_clause}
                    ORDER BY {order_expression}
                    LIMIT %s
                    OFFSET %s
                    """,
                    [
                        center_longitude,
                        center_latitude,
                        *filter_params,
                        query.limit,
                        query.offset,
                    ],
                )

                rows = cursor.fetchall()

        items = [
            self._public_list_item(row, as_of)
            for row in rows
        ]

        return {
            "items": items,
            "pagination": {
                "limit": query.limit,
                "offset": query.offset,
                "total": total,
                "has_more": query.offset + len(items) < total,
            },
            "as_of": self._iso(as_of),
        }

    def update_incident(
        self,
        incident_id: UUID,
        patch: IncidentPatchRequest,
        actor_id: str,
        is_moderator: bool = False,
    ):
        """
        Update an incident and create an audit event atomically.

        Returns the public detail response. No-op updates do not
        change updated_at or create audit events.
        """
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        incident_id,
                        incident_type,
                        status,
                        latitude,
                        longitude,
                        description,
                        observed_at,
                        submitted_at,
                        updated_at,
                        owner_id
                    FROM incident_reports
                    WHERE incident_id = %s
                    FOR UPDATE
                    """,
                    (incident_id,),
                )

                current = cursor.fetchone()

                if current is None or current["status"] == "withdrawn":
                    raise IncidentNotFound()

                if not self._can_modify(
                    current,
                    actor_id,
                    is_moderator,
                ):
                    raise IncidentForbidden()

                requested = patch.model_dump(exclude_unset=True)
                old_values = self._audit_snapshot(current)

                new_values = dict(old_values)

                for field_name, value in requested.items():
                    if field_name == "location":
                        new_values["location"] = {
                            "latitude": value["latitude"],
                            "longitude": value["longitude"],
                        }
                    else:
                        new_values[field_name] = value

                old_status = current["status"]
                new_status = new_values["status"]

                if (
                    new_status != old_status
                    and new_status not in
                    ALLOWED_STATUS_TRANSITIONS.get(old_status, set())
                ):
                    raise InvalidStatusTransition(
                        f"Cannot transition from {old_status} "
                        f"to {new_status}"
                    )

                changed_fields = {
                    field_name
                    for field_name in requested
                    if new_values[field_name] != old_values[field_name]
                }

                if not changed_fields:
                    return self._public_detail(
                        current,
                        self._utc_now(),
                    )

                set_clauses = []
                parameters = []

                if "incident_type" in changed_fields:
                    set_clauses.append("incident_type = %s")
                    parameters.append(new_values["incident_type"])

                if "location" in changed_fields:
                    latitude = new_values["location"]["latitude"]
                    longitude = new_values["location"]["longitude"]

                    set_clauses.extend([
                        "latitude = %s",
                        "longitude = %s",
                        """
                        location = ST_SetSRID(
                            ST_MakePoint(%s, %s),
                            4326
                        )::geography
                        """,
                    ])

                    parameters.extend([
                        latitude,
                        longitude,
                        longitude,
                        latitude,
                    ])

                if "description" in changed_fields:
                    set_clauses.append("description = %s")
                    parameters.append(new_values["description"])

                if "status" in changed_fields:
                    set_clauses.append("status = %s")
                    parameters.append(new_values["status"])

                set_clauses.append("updated_at = NOW()")
                parameters.append(incident_id)

                cursor.execute(
                    f"""
                    UPDATE incident_reports
                    SET {", ".join(set_clauses)}
                    WHERE incident_id = %s
                    RETURNING
                        incident_id,
                        incident_type,
                        status,
                        latitude,
                        longitude,
                        description,
                        observed_at,
                        submitted_at,
                        updated_at
                    """,
                    parameters,
                )

                updated = cursor.fetchone()

                previous_values = {
                    key: old_values[key]
                    for key in changed_fields
                }
                resulting_values = {
                    key: new_values[key]
                    for key in changed_fields
                }

                cursor.execute(
                    """
                    INSERT INTO incident_audit_events (
                        incident_id,
                        actor_id,
                        action,
                        previous_values,
                        new_values
                    )
                    VALUES (%s, %s, 'updated', %s, %s)
                    """,
                    (
                        incident_id,
                        actor_id,
                        Jsonb(previous_values),
                        Jsonb(resulting_values),
                    ),
                )

        return self._public_detail(updated, self._utc_now())

    def withdraw_incident(
        self,
        incident_id: UUID,
        actor_id: str,
        is_moderator: bool = False,
    ):
        """
        Soft-withdraw an incident and record its audit event.

        Repeated withdrawal is successful for an authorized actor.
        """
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        incident_id,
                        status,
                        owner_id
                    FROM incident_reports
                    WHERE incident_id = %s
                    FOR UPDATE
                    """,
                    (incident_id,),
                )

                current = cursor.fetchone()

                if current is None:
                    raise IncidentNotFound()

                if not self._can_modify(
                    current,
                    actor_id,
                    is_moderator,
                ):
                    raise IncidentForbidden()

                if current["status"] == "withdrawn":
                    return

                cursor.execute(
                    """
                    UPDATE incident_reports
                    SET
                        status = 'withdrawn',
                        updated_at = NOW()
                    WHERE incident_id = %s
                    """,
                    (incident_id,),
                )

                cursor.execute(
                    """
                    INSERT INTO incident_audit_events (
                        incident_id,
                        actor_id,
                        action,
                        previous_values,
                        new_values
                    )
                    VALUES (
                        %s,
                        %s,
                        'withdrawn',
                        %s,
                        %s
                    )
                    """,
                    (
                        incident_id,
                        actor_id,
                        Jsonb({"status": current["status"]}),
                        Jsonb({"status": "withdrawn"}),
                    ),
                )
