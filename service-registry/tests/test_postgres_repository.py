
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.postgres_repository import PostgresRegistryRepository


DATABASE_URL = os.getenv("TEST_REGISTRY_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="TEST_REGISTRY_DATABASE_URL is not configured",
)


@pytest.fixture
def repositories():
    suffix = uuid.uuid4().hex

    service_id = f"integration-test-{suffix}"
    instance_id = f"integration-instance-{suffix}"

    first = PostgresRegistryRepository(
        DATABASE_URL,
        heartbeat_interval_seconds=5,
    )

    second = PostgresRegistryRepository(
        DATABASE_URL,
        heartbeat_interval_seconds=5,
    )

    try:
        yield first, second, service_id, instance_id
    finally:
        first.deregister(instance_id)


def make_request(service_id, instance_id):
    return {
        "service_id": service_id,
        "instance_id": instance_id,
        "endpoint": "http://localhost:9999",
        "version": "1.0.0",
        "metadata": {"environment": "integration-test"},
    }


def test_postgres_registration_and_discovery(repositories):
    first, second, service_id, instance_id = repositories
    now = datetime.now(timezone.utc)

    registered = first.register(
        make_request(service_id, instance_id),
        now,
    )

    assert registered["status"] == "healthy"
    assert registered["service_id"] == service_id

    # A separate repository connection must discover the record.
    discovered = second.get_service(service_id, now)

    assert len(discovered) == 1
    assert discovered[0]["instance_id"] == instance_id


def test_postgres_registration_is_idempotent(repositories):
    first, second, service_id, instance_id = repositories
    now = datetime.now(timezone.utc)

    request = make_request(service_id, instance_id)

    first.register(request, now)

    updated_request = make_request(service_id, instance_id)
    updated_request["endpoint"] = "http://localhost:9998"

    updated = second.register(
        updated_request,
        now + timedelta(seconds=1),
    )

    assert updated["endpoint"] == "http://localhost:9998"
    assert len(first.get_service(service_id, now + timedelta(seconds=1))) == 1


def test_postgres_instance_cannot_change_service_id(repositories):
    first, _, service_id, instance_id = repositories
    now = datetime.now(timezone.utc)

    first.register(
        make_request(service_id, instance_id),
        now,
    )

    conflicting_request = make_request(
        f"other-service-{uuid.uuid4().hex}",
        instance_id,
    )

    with pytest.raises(ValueError):
        first.register(conflicting_request, now + timedelta(seconds=1))


def test_postgres_heartbeat_renews_lease(repositories):
    first, second, service_id, instance_id = repositories
    now = datetime.now(timezone.utc)

    registered = first.register(
        make_request(service_id, instance_id),
        now,
    )

    heartbeat_time = now + timedelta(seconds=5)

    renewed = second.heartbeat(instance_id, heartbeat_time)

    assert renewed is not None
    assert renewed["lease_expires_at"] > registered["lease_expires_at"]


def test_postgres_expired_instance_cannot_be_revived(repositories):
    first, second, service_id, instance_id = repositories
    now = datetime.now(timezone.utc)

    first.register(
        make_request(service_id, instance_id),
        now,
    )

    expired_at = now + timedelta(seconds=16)

    assert second.heartbeat(instance_id, expired_at) is None
    assert second.get_service(service_id, expired_at) == []

    # A fresh registration can restore the instance.
    restored = second.register(
        make_request(service_id, instance_id),
        expired_at,
    )

    assert restored["status"] == "healthy"


def test_postgres_deregistration_is_shared(repositories):
    first, second, service_id, instance_id = repositories
    now = datetime.now(timezone.utc)

    first.register(
        make_request(service_id, instance_id),
        now,
    )

    assert second.deregister(instance_id) is True
    assert first.get_service(service_id, now) == []


def test_postgres_state_survives_repository_reconnection(repositories):
    first, _, service_id, instance_id = repositories
    now = datetime.now(timezone.utc)

    first.register(
        make_request(service_id, instance_id),
        now,
    )

    reconnected = PostgresRegistryRepository(
        DATABASE_URL,
        heartbeat_interval_seconds=5,
    )

    discovered = reconnected.get_service(service_id, now)

    assert len(discovered) == 1
    assert discovered[0]["instance_id"] == instance_id
