
from datetime import datetime, timezone, timedelta

from fastapi.testclient import TestClient

from app.main import create_app
from app.repository import RegistryRepository


TEST_API_KEY = "test-registry-key"
AUTH_HEADERS = {"X-Registry-API-Key": TEST_API_KEY}


def make_client(tmp_path):
    repo = RegistryRepository(
        str(tmp_path / "test-registry.db"),
        heartbeat_interval_seconds=5,
    )

    app = create_app(
        repository=repo,
        api_key=TEST_API_KEY,
    )

    return TestClient(app), repo


def payload(
    instance_id="live-traffic-local-1",
    endpoint="http://localhost:8081",
    service_id="live-traffic",
):
    return {
        "service_id": service_id,
        "instance_id": instance_id,
        "endpoint": endpoint,
        "version": "0.1.0",
        "metadata": {"environment": "test"},
    }


def test_health_and_readiness(tmp_path):
    client, _ = make_client(tmp_path)

    with client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 200


def test_register_and_discover(tmp_path):
    client, _ = make_client(tmp_path)

    with client:
        response = client.post(
            "/v1/instances/register",
            json=payload(),
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 201

        found = client.get("/v1/services/live-traffic")

        assert found.status_code == 200
        assert len(found.json()["instances"]) == 1


def test_multiple_instances_for_one_service(tmp_path):
    client, _ = make_client(tmp_path)

    with client:
        first = client.post(
            "/v1/instances/register",
            json=payload(),
            headers=AUTH_HEADERS,
        )

        second = client.post(
            "/v1/instances/register",
            json=payload(
                "live-traffic-local-2",
                "http://localhost:8082",
            ),
            headers=AUTH_HEADERS,
        )

        assert first.status_code == 201
        assert second.status_code == 201

        found = client.get("/v1/services/live-traffic")

        assert len(found.json()["instances"]) == 2


def test_registration_is_idempotent_for_instance_id(tmp_path):
    client, repo = make_client(tmp_path)

    with client:
        first = client.post(
            "/v1/instances/register",
            json=payload(),
            headers=AUTH_HEADERS,
        )

        second = client.post(
            "/v1/instances/register",
            json=payload(endpoint="http://localhost:8099"),
            headers=AUTH_HEADERS,
        )

        assert first.status_code == 201
        assert second.status_code == 201
        assert repo.count() == 1

        instances = client.get(
            "/v1/services/live-traffic"
        ).json()["instances"]

        assert instances[0]["endpoint"] == "http://localhost:8099/"


def test_instance_cannot_change_service_id(tmp_path):
    client, repo = make_client(tmp_path)

    with client:
        first = client.post(
            "/v1/instances/register",
            json=payload(),
            headers=AUTH_HEADERS,
        )

        conflicting = client.post(
            "/v1/instances/register",
            json=payload(service_id="weather-analytics"),
            headers=AUTH_HEADERS,
        )

        assert first.status_code == 201
        assert conflicting.status_code == 409
        assert repo.count() == 1

        assert client.get(
            "/v1/services/live-traffic"
        ).status_code == 200

        assert client.get(
            "/v1/services/weather-analytics"
        ).status_code == 503


def test_heartbeat_and_deregister(tmp_path):
    client, _ = make_client(tmp_path)

    with client:
        registered = client.post(
            "/v1/instances/register",
            json=payload(),
            headers=AUTH_HEADERS,
        )

        assert registered.status_code == 201

        heartbeat = client.post(
            "/v1/instances/live-traffic-local-1/heartbeat",
            headers=AUTH_HEADERS,
        )

        assert heartbeat.status_code == 200

        deleted = client.delete(
            "/v1/instances/live-traffic-local-1",
            headers=AUTH_HEADERS,
        )

        assert deleted.status_code == 204

        assert client.get(
            "/v1/services/live-traffic"
        ).status_code == 503


def test_expired_instance_is_not_discoverable(tmp_path):
    _, repo = make_client(tmp_path)

    now = datetime.now(timezone.utc)

    repo.register(payload(), now)

    assert len(repo.get_service("live-traffic", now)) == 1

    expiry_time = now + timedelta(seconds=16)

    assert repo.expire(expiry_time) == 1

    assert repo.get_service("live-traffic", expiry_time) == []


def test_expired_instance_cannot_be_revived_by_heartbeat(tmp_path):
    _, repo = make_client(tmp_path)

    registered_at = datetime.now(timezone.utc)
    repo.register(payload(), registered_at)

    expired_at = registered_at + timedelta(seconds=16)

    heartbeat_result = repo.heartbeat(
        "live-traffic-local-1",
        expired_at,
    )

    assert heartbeat_result is None

    assert repo.get_service(
        "live-traffic",
        expired_at,
    ) == []

    # A fresh registration is allowed to restore the instance.
    restored = repo.register(
        payload(),
        expired_at,
    )

    assert restored["status"] == "healthy"


def test_unknown_heartbeat_and_service(tmp_path):
    client, _ = make_client(tmp_path)

    with client:
        heartbeat = client.post(
            "/v1/instances/missing/heartbeat",
            headers=AUTH_HEADERS,
        )

        assert heartbeat.status_code == 404

        assert client.get(
            "/v1/services/not-a-service"
        ).status_code == 503


def test_management_endpoints_require_api_key(tmp_path):
    client, _ = make_client(tmp_path)

    with client:
        # Missing key
        missing_key = client.post(
            "/v1/instances/register",
            json=payload(),
        )

        assert missing_key.status_code == 401

        # Incorrect key
        incorrect_key = client.post(
            "/v1/instances/register",
            json=payload(),
            headers={"X-Registry-API-Key": "incorrect"},
        )

        assert incorrect_key.status_code == 401

        # Correct key
        authorized = client.post(
            "/v1/instances/register",
            json=payload(),
            headers=AUTH_HEADERS,
        )

        assert authorized.status_code == 201


def test_discovery_does_not_require_api_key(tmp_path):
    client, _ = make_client(tmp_path)

    with client:
        client.post(
            "/v1/instances/register",
            json=payload(),
            headers=AUTH_HEADERS,
        )

        response = client.get("/v1/services/live-traffic")

        assert response.status_code == 200


def test_secret_metadata_rejected(tmp_path):
    client, _ = make_client(tmp_path)

    with client:
        bad = payload()
        bad["metadata"] = {"api_key": "do-not-store"}

        response = client.post(
            "/v1/instances/register",
            json=bad,
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 422


def test_nested_secret_metadata_rejected(tmp_path):
    client, _ = make_client(tmp_path)

    with client:
        bad = payload()
        bad["metadata"] = {
            "configuration": {
                "credentials": "sensitive-value"
            }
        }

        response = client.post(
            "/v1/instances/register",
            json=bad,
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 422


def test_harmless_metadata_values_are_allowed(tmp_path):
    client, _ = make_client(tmp_path)

    with client:
        good = payload()
        good["metadata"] = {
            "environment": "development",
            "description": "Token processing service",
        }

        response = client.post(
            "/v1/instances/register",
            json=good,
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 201
