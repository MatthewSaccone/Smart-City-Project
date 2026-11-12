
import os
from uuid import uuid4

import pytest

from app.database import Database
from app.models import (
    IncidentCreateRequest,
    IncidentListQuery,
    IncidentPatchRequest,
)
from app.repository import (
    IdempotencyConflict,
    IncidentForbidden,
    IncidentNotFound,
    IncidentRepository,
    InvalidStatusTransition,
)


@pytest.fixture
def repository():
    database_url = os.getenv("INCIDENTS_DATABASE_URL")

    if not database_url:
        pytest.skip("INCIDENTS_DATABASE_URL is not configured")

    database = Database(database_url)
    database.initialize()

    return IncidentRepository(database)


@pytest.fixture
def owner_id():
    return f"repository-test-{uuid4()}"


@pytest.fixture
def cleanup(repository, owner_id):
    yield

    with repository._connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM incident_audit_events
                WHERE incident_id IN (
                    SELECT incident_id
                    FROM incident_reports
                    WHERE owner_id = %s
                )
                """,
                (owner_id,),
            )

            cursor.execute(
                """
                DELETE FROM incident_reports
                WHERE owner_id = %s
                """,
                (owner_id,),
            )

            cursor.execute(
                """
                DELETE FROM incident_idempotency_records
                WHERE owner_id = %s
                """,
                (owner_id,),
            )


def make_request(
    description="Road construction blocking one lane",
    latitude=43.6532,
    longitude=-79.3832,
):
    return IncidentCreateRequest(
        incident_type="construction",
        location={
            "latitude": latitude,
            "longitude": longitude,
        },
        description=description,
    )


def test_create_incident_persists_report(
    repository,
    owner_id,
    cleanup,
):
    request = make_request()

    status_code, response, replayed = (
        repository.create_incident(
            request,
            owner_id,
            "create-test-001",
        )
    )

    assert status_code == 201
    assert replayed is False

    assert response["incident_type"] == "construction"
    assert response["status"] == "active"
    assert response["description"] == request.description
    assert response["source"] == "user_report"

    assert "owner_id" not in response
    assert "audit_events" not in response


def test_create_incident_returns_same_response_for_retry(
    repository,
    owner_id,
    cleanup,
):
    request = make_request()

    first = repository.create_incident(
        request,
        owner_id,
        "retry-test-001",
    )

    second = repository.create_incident(
        request,
        owner_id,
        "retry-test-001",
    )

    assert first[0] == 201
    assert second[0] == 201

    assert first[1] == second[1]
    assert first[2] is False
    assert second[2] is True


def test_reusing_idempotency_key_with_different_payload_fails(
    repository,
    owner_id,
    cleanup,
):
    repository.create_incident(
        make_request(),
        owner_id,
        "conflict-test-001",
    )

    with pytest.raises(IdempotencyConflict):
        repository.create_incident(
            make_request(
                description="An accident is blocking the road"
            ),
            owner_id,
            "conflict-test-001",
        )


def test_different_idempotency_keys_create_separate_incidents(
    repository,
    owner_id,
    cleanup,
):
    request = make_request()

    first = repository.create_incident(
        request,
        owner_id,
        "separate-test-001",
    )

    second = repository.create_incident(
        request,
        owner_id,
        "separate-test-002",
    )

    assert first[1]["incident_id"] != second[1]["incident_id"]


def test_get_incident_returns_public_details(
    repository,
    owner_id,
    cleanup,
):
    _, created, _ = repository.create_incident(
        make_request(),
        owner_id,
        "detail-test-001",
    )

    from uuid import UUID

    incident = repository.get_incident(
        UUID(created["incident_id"])
    )

    assert incident is not None
    assert incident["incident_id"] == created["incident_id"]
    assert incident["description"] == created["description"]

    assert "owner_id" not in incident


def test_get_incident_returns_none_for_missing_id(
    repository,
):
    incident = repository.get_incident(uuid4())

    assert incident is None


def test_list_incidents_returns_nearby_reports(
    repository,
    owner_id,
    cleanup,
):
    _, created, _ = repository.create_incident(
        make_request(),
        owner_id,
        "list-test-001",
    )

    query = IncidentListQuery(
        latitude=43.6532,
        longitude=-79.3832,
        radius_m=1000,
    )

    result = repository.list_incidents(query)

    matching = [
        item
        for item in result["items"]
        if item["incident_id"] == created["incident_id"]
    ]

    assert len(matching) == 1
    assert matching[0]["distance_from_center_m"] >= 0

    assert result["pagination"]["total"] >= 1
    assert result["pagination"]["has_more"] is False


def test_list_incidents_excludes_reports_outside_radius(
    repository,
    owner_id,
    cleanup,
):
    _, created, _ = repository.create_incident(
        make_request(
            latitude=43.6532,
            longitude=-79.3832,
        ),
        owner_id,
        "outside-test-001",
    )

    query = IncidentListQuery(
        latitude=45.4215,
        longitude=-75.6972,
        radius_m=1000,
    )

    result = repository.list_incidents(query)

    incident_ids = [
        item["incident_id"]
        for item in result["items"]
    ]

    assert created["incident_id"] not in incident_ids


def test_list_incidents_filters_by_incident_type(
    repository,
    owner_id,
    cleanup,
):
    _, created, _ = repository.create_incident(
        make_request(),
        owner_id,
        "filter-test-001",
    )

    query = IncidentListQuery(
        latitude=43.6532,
        longitude=-79.3832,
        radius_m=1000,
        incident_type="accident",
    )

    result = repository.list_incidents(query)

    incident_ids = [
        item["incident_id"]
        for item in result["items"]
    ]

    assert created["incident_id"] not in incident_ids


def test_list_incidents_supports_pagination(
    repository,
    owner_id,
    cleanup,
):
    for index in range(3):
        repository.create_incident(
            make_request(
                description=f"Construction report number {index}"
            ),
            owner_id,
            f"pagination-test-{index:03d}",
        )

    query = IncidentListQuery(
        latitude=43.6532,
        longitude=-79.3832,
        radius_m=1000,
        limit=2,
        offset=0,
    )

    result = repository.list_incidents(query)

    assert len(result["items"]) == 2
    assert result["pagination"]["total"] >= 3
    assert result["pagination"]["has_more"] is True


def test_withdrawn_incidents_are_hidden_from_public_reads(
    repository,
    owner_id,
    cleanup,
):
    _, created, _ = repository.create_incident(
        make_request(),
        owner_id,
        "withdrawn-test-001",
    )

    from uuid import UUID

    incident_id = UUID(created["incident_id"])

    with repository._connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE incident_reports
                SET status = 'withdrawn'
                WHERE incident_id = %s
                """,
                (incident_id,),
            )

    assert repository.get_incident(incident_id) is None

    result = repository.list_incidents(
        IncidentListQuery(
            latitude=43.6532,
            longitude=-79.3832,
            radius_m=1000,
            status="all",
        )
    )

    incident_ids = [
        item["incident_id"]
        for item in result["items"]
    ]

    assert str(incident_id) not in incident_ids



def test_update_incident_changes_description_and_records_audit(
    repository,
    owner_id,
    cleanup,
):
    _, created, _ = repository.create_incident(
        make_request(),
        owner_id,
        "patch-test-001",
    )

    from uuid import UUID

    incident_id = UUID(created["incident_id"])

    updated = repository.update_incident(
        incident_id,
        IncidentPatchRequest(
            description="Road construction now blocks two lanes"
        ),
        actor_id=owner_id,
    )

    assert updated["description"] == (
        "Road construction now blocks two lanes"
    )

    assert updated["incident_id"] == created["incident_id"]

    with repository._connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT actor_id, action, previous_values, new_values
                FROM incident_audit_events
                WHERE incident_id = %s
                """,
                (incident_id,),
            )

            audit = cursor.fetchone()

    assert audit is not None
    assert audit["actor_id"] == owner_id
    assert audit["action"] == "updated"
    assert audit["previous_values"]["description"] == created["description"]
    assert audit["new_values"]["description"] == (
        "Road construction now blocks two lanes"
    )


def test_update_incident_changes_location_and_status(
    repository,
    owner_id,
    cleanup,
):
    _, created, _ = repository.create_incident(
        make_request(),
        owner_id,
        "patch-test-002",
    )

    from uuid import UUID

    updated = repository.update_incident(
        UUID(created["incident_id"]),
        IncidentPatchRequest(
            location={
                "latitude": 43.7000,
                "longitude": -79.4000,
            },
            status="resolved",
        ),
        actor_id=owner_id,
    )

    assert updated["location"]["latitude"] == pytest.approx(43.7)
    assert updated["location"]["longitude"] == pytest.approx(-79.4)
    assert updated["status"] == "resolved"


def test_update_incident_rejects_non_owner(
    repository,
    owner_id,
    cleanup,
):
    _, created, _ = repository.create_incident(
        make_request(),
        owner_id,
        "patch-test-003",
    )

    from uuid import UUID

    with pytest.raises(IncidentForbidden):
        repository.update_incident(
            UUID(created["incident_id"]),
            IncidentPatchRequest(
                description="Updated description for this road report"
            ),
            actor_id="different-user",
        )


def test_update_incident_allows_moderator(
    repository,
    owner_id,
    cleanup,
):
    _, created, _ = repository.create_incident(
        make_request(),
        owner_id,
        "patch-test-004",
    )

    from uuid import UUID

    updated = repository.update_incident(
        UUID(created["incident_id"]),
        IncidentPatchRequest(status="resolved"),
        actor_id="moderator-user",
        is_moderator=True,
    )

    assert updated["status"] == "resolved"


def test_update_incident_rejects_invalid_status_transition(
    repository,
    owner_id,
    cleanup,
):
    _, created, _ = repository.create_incident(
        make_request(),
        owner_id,
        "patch-test-005",
    )

    from uuid import UUID

    incident_id = UUID(created["incident_id"])

    repository.update_incident(
        incident_id,
        IncidentPatchRequest(status="resolved"),
        actor_id=owner_id,
    )

    with pytest.raises(InvalidStatusTransition):
        repository.update_incident(
            incident_id,
            IncidentPatchRequest(status="completed"),
            actor_id=owner_id,
        )


def test_no_op_patch_does_not_create_audit_event(
    repository,
    owner_id,
    cleanup,
):
    _, created, _ = repository.create_incident(
        make_request(),
        owner_id,
        "patch-test-006",
    )

    from uuid import UUID

    incident_id = UUID(created["incident_id"])

    original = repository.get_incident(incident_id)

    updated = repository.update_incident(
        incident_id,
        IncidentPatchRequest(
            description=created["description"]
        ),
        actor_id=owner_id,
    )

    assert updated["updated_at"] == original["updated_at"]

    with repository._connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*) AS total
                FROM incident_audit_events
                WHERE incident_id = %s
                """,
                (incident_id,),
            )

            assert cursor.fetchone()["total"] == 0


def test_withdraw_incident_soft_deletes_and_records_audit(
    repository,
    owner_id,
    cleanup,
):
    _, created, _ = repository.create_incident(
        make_request(),
        owner_id,
        "delete-test-001",
    )

    from uuid import UUID

    incident_id = UUID(created["incident_id"])

    result = repository.withdraw_incident(
        incident_id,
        actor_id=owner_id,
    )

    assert result is None
    assert repository.get_incident(incident_id) is None

    with repository._connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT status
                FROM incident_reports
                WHERE incident_id = %s
                """,
                (incident_id,),
            )

            report = cursor.fetchone()

            cursor.execute(
                """
                SELECT actor_id, action, previous_values, new_values
                FROM incident_audit_events
                WHERE incident_id = %s
                """,
                (incident_id,),
            )

            audit = cursor.fetchone()

    assert report["status"] == "withdrawn"
    assert audit["actor_id"] == owner_id
    assert audit["action"] == "withdrawn"
    assert audit["previous_values"]["status"] == "active"
    assert audit["new_values"]["status"] == "withdrawn"


def test_repeated_withdrawal_by_authorized_owner_succeeds(
    repository,
    owner_id,
    cleanup,
):
    _, created, _ = repository.create_incident(
        make_request(),
        owner_id,
        "delete-test-002",
    )

    from uuid import UUID

    incident_id = UUID(created["incident_id"])

    repository.withdraw_incident(incident_id, owner_id)

    # Repeating the operation should succeed without adding another audit.
    repository.withdraw_incident(incident_id, owner_id)

    with repository._connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*) AS total
                FROM incident_audit_events
                WHERE incident_id = %s
                """,
                (incident_id,),
            )

            assert cursor.fetchone()["total"] == 1


def test_withdraw_incident_rejects_non_owner(
    repository,
    owner_id,
    cleanup,
):
    _, created, _ = repository.create_incident(
        make_request(),
        owner_id,
        "delete-test-003",
    )

    from uuid import UUID

    with pytest.raises(IncidentForbidden):
        repository.withdraw_incident(
            UUID(created["incident_id"]),
            actor_id="different-user",
        )


def test_update_incident_rejects_withdrawn_report(
    repository,
    owner_id,
    cleanup,
):
    _, created, _ = repository.create_incident(
        make_request(),
        owner_id,
        "patch-test-007",
    )

    from uuid import UUID

    incident_id = UUID(created["incident_id"])

    repository.withdraw_incident(incident_id, owner_id)

    with pytest.raises(IncidentNotFound):
        repository.update_incident(
            incident_id,
            IncidentPatchRequest(
                status="active"
            ),
            actor_id=owner_id,
        )
