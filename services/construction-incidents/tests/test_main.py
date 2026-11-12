
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.auth import Principal, get_current_principal
from app.main import app
from app.repository import (
    IdempotencyConflict,
    IncidentForbidden,
    IncidentNotFound,
    InvalidStatusTransition,
)


INCIDENT_ID = uuid4()

NOW = datetime.now(timezone.utc)


def make_public_incident():
    return {
        "incident_id": str(INCIDENT_ID),
        "incident_type": "construction",
        "status": "active",
        "location": {
            "latitude": 43.6532,
            "longitude": -79.3832,
        },
        "description": "Road construction blocking one lane",
        "observed_at": None,
        "submitted_at": NOW,
        "updated_at": NOW,
        "source": "user_report",
        "is_live": False,
        "is_stale": False,
        "data_status": "user_report",
        "as_of": NOW,
    }


def make_list_response():
    return {
        "items": [
            {
                "incident_id": str(INCIDENT_ID),
                "incident_type": "construction",
                "status": "active",
                "location": {
                    "latitude": 43.6532,
                    "longitude": -79.3832,
                },
                "submitted_at": NOW,
                "updated_at": NOW,
                "source": "user_report",
                "is_live": False,
                "is_stale": False,
                "data_status": "user_report",
                "distance_from_center_m": 0.0,
            }
        ],
        "pagination": {
            "limit": 25,
            "offset": 0,
            "total": 1,
            "has_more": False,
        },
        "as_of": NOW,
    }


class FakeRepository:
    """In-memory repository substitute for HTTP endpoint tests."""

    def __init__(self):
        self.incident = make_public_incident()
        self.list_response = make_list_response()

        self.list_query = None
        self.create_arguments = None
        self.update_arguments = None
        self.withdraw_arguments = None

        self.create_error = None
        self.get_error = None
        self.update_error = None
        self.withdraw_error = None

        self.create_status = 201
        self.create_replayed = False

    def list_incidents(self, query):
        self.list_query = query
        return self.list_response

    def get_incident(self, incident_id):
        if self.get_error:
            raise self.get_error

        if incident_id == INCIDENT_ID:
            return self.incident

        return None

    def create_incident(
        self,
        request,
        owner_id,
        idempotency_key,
    ):
        self.create_arguments = {
            "request": request,
            "owner_id": owner_id,
            "idempotency_key": idempotency_key,
        }

        if self.create_error:
            raise self.create_error

        return (
            self.create_status,
            self.incident,
            self.create_replayed,
        )

    def update_incident(
        self,
        incident_id,
        patch,
        actor_id,
        is_moderator=False,
    ):
        self.update_arguments = {
            "incident_id": incident_id,
            "patch": patch,
            "actor_id": actor_id,
            "is_moderator": is_moderator,
        }

        if self.update_error:
            raise self.update_error

        return self.incident

    def withdraw_incident(
        self,
        incident_id,
        actor_id,
        is_moderator=False,
    ):
        self.withdraw_arguments = {
            "incident_id": incident_id,
            "actor_id": actor_id,
            "is_moderator": is_moderator,
        }

        if self.withdraw_error:
            raise self.withdraw_error


@pytest.fixture
def principal():
    return Principal(
        subject="http-test-user",
        roles=frozenset({"user"}),
        moderator_role="s3_moderator",
    )


@pytest.fixture
def repository(monkeypatch):
    fake_repository = FakeRepository()

    monkeypatch.setattr(
        app.state,
        "repository",
        fake_repository,
        raising=False,
    )

    return fake_repository


@pytest.fixture
def client(principal):
    app.dependency_overrides[get_current_principal] = (
        lambda: principal
    )

    # Do not enter TestClient as a context manager here.
    # That would start the real application lifespan and database.
    test_client = TestClient(app)

    yield test_client

    test_client.close()
    app.dependency_overrides.pop(
        get_current_principal,
        None,
    )


# -------------------------------------------------------------------
# Health and readiness
# -------------------------------------------------------------------


def test_health_returns_healthy_response(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "service": "construction-incidents",
        "status": "healthy",
    }


def test_ready_returns_ready_when_dependencies_are_available(
    client,
    monkeypatch,
):
    class FakeDatabase:
        def check_available(self):
            return True

    class FakeTokenVerifier:
        def check_available(self):
            return True

    monkeypatch.setattr(
        app.state,
        "database",
        FakeDatabase(),
        raising=False,
    )

    monkeypatch.setattr(
        app.state,
        "token_verifier",
        FakeTokenVerifier(),
        raising=False,
    )

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "service": "construction-incidents",
        "status": "ready",
    }


def test_ready_returns_503_when_database_is_unavailable(
    client,
    monkeypatch,
):
    class FakeDatabase:
        def check_available(self):
            return False

    monkeypatch.setattr(
        app.state,
        "database",
        FakeDatabase(),
        raising=False,
    )

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == (
        "SERVICE_UNAVAILABLE"
    )


def test_ready_returns_503_when_token_verification_is_unavailable(
    client,
    monkeypatch,
):
    class FakeDatabase:
        def check_available(self):
            return True

    class FakeTokenVerifier:
        def check_available(self):
            return False

    monkeypatch.setattr(
        app.state,
        "database",
        FakeDatabase(),
        raising=False,
    )

    monkeypatch.setattr(
        app.state,
        "token_verifier",
        FakeTokenVerifier(),
        raising=False,
    )

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == (
        "SERVICE_UNAVAILABLE"
    )


# -------------------------------------------------------------------
# Public incident listing
# -------------------------------------------------------------------


def test_list_incidents_returns_public_list(
    client,
    repository,
):
    response = client.get(
        "/v1/incidents",
        params={
            "latitude": 43.6532,
            "longitude": -79.3832,
            "radius_m": 1000,
        },
    )

    assert response.status_code == 200

    body = response.json()

    assert len(body["items"]) == 1
    assert body["items"][0]["incident_id"] == str(INCIDENT_ID)
    assert body["items"][0]["incident_type"] == "construction"

    assert body["pagination"]["total"] == 1
    assert body["pagination"]["has_more"] is False

    # List summaries must not expose descriptions.
    assert "description" not in body["items"][0]

    # Public responses must not expose private ownership information.
    assert "owner_id" not in body["items"][0]


def test_list_incidents_passes_query_parameters_to_repository(
    client,
    repository,
):
    response = client.get(
        "/v1/incidents",
        params={
            "latitude": 43.6532,
            "longitude": -79.3832,
            "radius_m": 5000,
            "incident_type": "accident",
            "status": "all",
            "sort_by": "updated_at",
            "sort_order": "asc",
            "limit": 10,
            "offset": 5,
        },
    )

    assert response.status_code == 200

    query = repository.list_query

    assert query.latitude == 43.6532
    assert query.longitude == -79.3832
    assert query.radius_m == 5000
    assert query.incident_type == "accident"
    assert query.status == "all"
    assert query.sort_by == "updated_at"
    assert query.sort_order == "asc"
    assert query.limit == 10
    assert query.offset == 5


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"latitude": 43.6532, "longitude": -79.3832},
        {
            "latitude": 91,
            "longitude": -79.3832,
            "radius_m": 1000,
        },
        {
            "latitude": 43.6532,
            "longitude": -181,
            "radius_m": 1000,
        },
        {
            "latitude": 43.6532,
            "longitude": -79.3832,
            "radius_m": 0,
        },
        {
            "latitude": 43.6532,
            "longitude": -79.3832,
            "radius_m": 26000,
        },
        {
            "latitude": 43.6532,
            "longitude": -79.3832,
            "radius_m": 1000,
            "limit": 101,
        },
        {
            "latitude": 43.6532,
            "longitude": -79.3832,
            "radius_m": 1000,
            "status": "withdrawn",
        },
    ],
)
def test_list_incidents_rejects_invalid_query_parameters(
    client,
    params,
):
    response = client.get(
        "/v1/incidents",
        params=params,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == (
        "VALIDATION_FAILED"
    )


# -------------------------------------------------------------------
# Public incident detail
# -------------------------------------------------------------------


def test_get_incident_returns_public_details(
    client,
    repository,
):
    response = client.get(
        f"/v1/incidents/{INCIDENT_ID}"
    )

    assert response.status_code == 200

    body = response.json()

    assert body["incident_id"] == str(INCIDENT_ID)
    assert body["description"] == (
        "Road construction blocking one lane"
    )
    assert body["source"] == "user_report"
    assert body["is_live"] is False

    assert "owner_id" not in body
    assert "audit_events" not in body


def test_get_incident_returns_404_for_missing_report(
    client,
    repository,
):
    response = client.get(
        f"/v1/incidents/{uuid4()}"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_get_incident_rejects_invalid_uuid(client):
    response = client.get(
        "/v1/incidents/not-a-uuid"
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == (
        "VALIDATION_FAILED"
    )
    

def test_create_incident_requires_authentication():
    # Temporarily remove the authenticated-principal override.
    previous_override = app.dependency_overrides.pop(
        get_current_principal,
        None,
    )

    test_client = TestClient(app)

    try:
        response = test_client.post(
            "/v1/incidents",
            headers={
                "Idempotency-Key": "http-auth-test-001",
            },
            json={
                "incident_type": "construction",
                "location": {
                    "latitude": 43.6532,
                    "longitude": -79.3832,
                },
                "description": "Road construction blocking one lane",
            },
        )
    finally:
        test_client.close()

        if previous_override is not None:
            app.dependency_overrides[
                get_current_principal
            ] = previous_override

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"

    body = response.json()

    assert body["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert body["error"]["request_id"]


# -------------------------------------------------------------------
# Incident creation
# -------------------------------------------------------------------


def test_create_incident_returns_201_and_location_header(
    client,
    repository,
):
    response = client.post(
        "/v1/incidents",
        headers={
            "Idempotency-Key": "http-test-001",
        },
        json={
            "incident_type": "construction",
            "location": {
                "latitude": 43.6532,
                "longitude": -79.3832,
            },
            "description": "Road construction blocking one lane",
        },
    )

    assert response.status_code == 201

    body = response.json()

    assert body["incident_id"] == str(INCIDENT_ID)
    assert body["status"] == "active"

    assert response.headers["Location"] == (
        f"/v1/incidents/{INCIDENT_ID}"
    )

    assert repository.create_arguments["owner_id"] == (
        "http-test-user"
    )

    assert repository.create_arguments["idempotency_key"] == (
        "http-test-001"
    )


def test_create_incident_rejects_missing_idempotency_key(client):
    response = client.post(
        "/v1/incidents",
        json={
            "incident_type": "construction",
            "location": {
                "latitude": 43.6532,
                "longitude": -79.3832,
            },
            "description": "Road construction blocking one lane",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == (
        "VALIDATION_FAILED"
    )


@pytest.mark.parametrize(
    "key",
    [
        "short",
        "invalid key",
        "invalid/key",
        "x" * 129,
    ],
)
def test_create_incident_rejects_invalid_idempotency_key(
    client,
    key,
):
    response = client.post(
        "/v1/incidents",
        headers={
            "Idempotency-Key": key,
        },
        json={
            "incident_type": "construction",
            "location": {
                "latitude": 43.6532,
                "longitude": -79.3832,
            },
            "description": "Road construction blocking one lane",
        },
    )

    assert response.status_code == 422


def test_create_incident_rejects_invalid_request_body(client):
    response = client.post(
        "/v1/incidents",
        headers={
            "Idempotency-Key": "http-test-002",
        },
        json={
            "incident_type": "invalid_type",
            "location": {
                "latitude": 100,
                "longitude": -79.3832,
            },
            "description": "Short",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == (
        "VALIDATION_FAILED"
    )


def test_create_incident_rejects_client_supplied_owner(client):
    response = client.post(
        "/v1/incidents",
        headers={
            "Idempotency-Key": "http-test-003",
        },
        json={
            "incident_type": "construction",
            "location": {
                "latitude": 43.6532,
                "longitude": -79.3832,
            },
            "description": "Road construction blocking one lane",
            "owner_id": "someone-else",
        },
    )

    assert response.status_code == 422


def test_create_incident_returns_idempotency_conflict(
    client,
    repository,
):
    repository.create_error = IdempotencyConflict()

    response = client.post(
        "/v1/incidents",
        headers={
            "Idempotency-Key": "http-test-004",
        },
        json={
            "incident_type": "construction",
            "location": {
                "latitude": 43.6532,
                "longitude": -79.3832,
            },
            "description": "Road construction blocking one lane",
        },
    )

    assert response.status_code == 409

    body = response.json()

    assert body["error"]["code"] == (
        "IDEMPOTENCY_KEY_CONFLICT"
    )


# -------------------------------------------------------------------
# Incident updates
# -------------------------------------------------------------------


def test_update_incident_passes_verified_identity_to_repository(
    client,
    repository,
):
    response = client.patch(
        f"/v1/incidents/{INCIDENT_ID}",
        json={
            "description": "Road construction now blocks two lanes",
        },
    )

    assert response.status_code == 200

    arguments = repository.update_arguments

    assert arguments["incident_id"] == INCIDENT_ID
    assert arguments["actor_id"] == "http-test-user"
    assert arguments["is_moderator"] is False

    assert arguments["patch"].description == (
        "Road construction now blocks two lanes"
    )


def test_update_incident_rejects_empty_patch(client):
    response = client.patch(
        f"/v1/incidents/{INCIDENT_ID}",
        json={},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == (
        "VALIDATION_FAILED"
    )


def test_update_incident_rejects_withdrawn_status(client):
    response = client.patch(
        f"/v1/incidents/{INCIDENT_ID}",
        json={
            "status": "withdrawn",
        },
    )

    assert response.status_code == 422


def test_update_incident_returns_403_for_non_owner(
    client,
    repository,
):
    repository.update_error = IncidentForbidden()

    response = client.patch(
        f"/v1/incidents/{INCIDENT_ID}",
        json={
            "description": "Updated road construction information",
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_update_incident_returns_404_for_missing_report(
    client,
    repository,
):
    repository.update_error = IncidentNotFound()

    response = client.patch(
        f"/v1/incidents/{INCIDENT_ID}",
        json={
            "description": "Updated road construction information",
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_update_incident_returns_422_for_invalid_transition(
    client,
    repository,
):
    repository.update_error = InvalidStatusTransition(
        "Cannot transition from resolved to completed"
    )

    response = client.patch(
        f"/v1/incidents/{INCIDENT_ID}",
        json={
            "status": "completed",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == (
        "VALIDATION_FAILED"
    )


# -------------------------------------------------------------------
# Incident withdrawal
# -------------------------------------------------------------------


def test_withdraw_incident_returns_empty_204_response(
    client,
    repository,
):
    response = client.delete(
        f"/v1/incidents/{INCIDENT_ID}"
    )

    assert response.status_code == 204
    assert response.content == b""

    arguments = repository.withdraw_arguments

    assert arguments["incident_id"] == INCIDENT_ID
    assert arguments["actor_id"] == "http-test-user"
    assert arguments["is_moderator"] is False


def test_withdraw_incident_returns_403_for_non_owner(
    client,
    repository,
):
    repository.withdraw_error = IncidentForbidden()

    response = client.delete(
        f"/v1/incidents/{INCIDENT_ID}"
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_withdraw_incident_returns_404_for_missing_report(
    client,
    repository,
):
    repository.withdraw_error = IncidentNotFound()

    response = client.delete(
        f"/v1/incidents/{INCIDENT_ID}"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


# -------------------------------------------------------------------
# Request correlation and standard error format
# -------------------------------------------------------------------


def test_request_id_is_preserved_in_response(
    client,
):
    request_id = "http-test-request-123"

    response = client.get(
        "/v1/incidents/not-a-uuid",
        headers={
            "X-Request-ID": request_id,
        },
    )

    assert response.status_code == 422
    assert response.headers["X-Request-ID"] == request_id
    assert response.json()["error"]["request_id"] == request_id


def test_generated_request_id_is_returned_when_not_supplied(
    client,
):
    response = client.get(
        "/v1/incidents/not-a-uuid"
    )

    assert response.status_code == 422

    request_id = response.headers["X-Request-ID"]

    assert request_id
    assert response.json()["error"]["request_id"] == request_id


def test_malformed_json_returns_bad_request(client):
    response = client.post(
        "/v1/incidents",
        headers={
            "Idempotency-Key": "http-test-005",
            "Content-Type": "application/json",
        },
        content='{"incident_type": ',
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "BAD_REQUEST"
