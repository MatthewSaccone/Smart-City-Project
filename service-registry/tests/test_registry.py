from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from app.main import create_app
from app.repository import RegistryRepository

def make_client(tmp_path):
    repo = RegistryRepository(str(tmp_path / "test-registry.db"), heartbeat_interval_seconds=5)
    return TestClient(create_app(repo)), repo

def payload(instance_id="live-traffic-local-1", endpoint="http://localhost:8081"):
    return {
        "service_id": "live-traffic",
        "instance_id": instance_id,
        "endpoint": endpoint,
        "version": "0.1.0",
        "metadata": {"environment": "test"}
    }

def test_register_and_discover(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        response = client.post("/v1/instances/register", json=payload())
        assert response.status_code == 201
        found = client.get("/v1/services/live-traffic")
        assert found.status_code == 200
        assert len(found.json()["instances"]) == 1

def test_multiple_instances_for_one_service(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        assert client.post("/v1/instances/register", json=payload()).status_code == 201
        second = payload("live-traffic-local-2", "http://localhost:8082")
        assert client.post("/v1/instances/register", json=second).status_code == 201
        found = client.get("/v1/services/live-traffic")
        assert len(found.json()["instances"]) == 2

def test_registration_is_idempotent_for_instance_id(tmp_path):
    client, repo = make_client(tmp_path)
    with client:
        client.post("/v1/instances/register", json=payload())
        client.post("/v1/instances/register", json=payload(endpoint="http://localhost:8099"))
        assert repo.count() == 1
        assert client.get("/v1/services/live-traffic").json()["instances"][0]["endpoint"] == "http://localhost:8099/"

def test_heartbeat_and_deregister(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        client.post("/v1/instances/register", json=payload())
        assert client.post("/v1/instances/live-traffic-local-1/heartbeat").status_code == 200
        assert client.delete("/v1/instances/live-traffic-local-1").status_code == 204
        assert client.get("/v1/services/live-traffic").status_code == 503

def test_expired_instance_is_not_discoverable(tmp_path):
    _, repo = make_client(tmp_path)
    now = datetime.now(timezone.utc)
    repo.register(payload(), now)
    assert len(repo.get_service("live-traffic", now)) == 1
    assert repo.expire(now + timedelta(seconds=16)) == 1
    assert repo.get_service("live-traffic", now + timedelta(seconds=16)) == []

def test_unknown_heartbeat_and_service(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        assert client.post("/v1/instances/missing/heartbeat").status_code == 404
        assert client.get("/v1/services/not-a-service").status_code == 503

def test_secret_metadata_rejected(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        bad = payload()
        bad["metadata"] = {"api_key": "do-not-store"}
        assert client.post("/v1/instances/register", json=bad).status_code == 422
