"""Database lifecycle, startup readiness and recovery without losing S4 fallback."""

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from app.database import Database
from app.datasets import StoredFeed
from app.ingestion import ParkingIngestion, Settings
import app.main as main
from app.service import ParkingService
from test_ingestion import publisher


@pytest.fixture
def database(monkeypatch):
    state = SimpleNamespace(records={}, initialized=False, attempts=0, failure=None, on_read=None)

    class Store:
        def __init__(self, database_url):
            self.database_url = database_url

        @contextmanager
        def connect(self):
            if state.failure == "connect":
                raise OSError("private connection details must not be logged")
            yield SimpleNamespace(execute=lambda statement: None)

        def initialize(self, connection):
            state.attempts += 1
            if state.failure == "initialize":
                raise OSError("private connection details must not be logged")
            state.initialized = True

        def read(self, connection):
            assert state.initialized, "S4 read the database before schema initialization"
            if state.on_read:
                state.on_read()
            return state.records

    monkeypatch.setattr("app.ingestion.ParkingDataStore", Store)
    monkeypatch.setenv("PARKING_DATABASE_URL", "postgresql://s4-dedicated.invalid/parking")
    return state


@pytest.mark.parametrize("database_url", [None, "", "   "])
def test_empty_database_url_cannot_connect_using_implicit_driver_defaults(database_url):
    with pytest.raises(ValueError, match="PARKING_DATABASE_URL"):
        Database(database_url)


def test_startup_initializes_empty_database_without_seeding_or_claiming_readiness(database, monkeypatch, identity):
    monkeypatch.setattr(ParkingIngestion, "start", lambda self: None)
    with TestClient(main.app, headers={"Authorization": "Bearer " + identity.token()}) as client:
        assert database.initialized and database.attempts == 1
        assert client.app.state.database is client.app.state.ingestion.database
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 503
        assert database.records == {}
        assert client.app.state.parking.facilities == []


def test_fixture_startup_does_not_require_a_database(database, monkeypatch, identity):
    monkeypatch.delenv("PARKING_DATABASE_URL")
    monkeypatch.setattr(ParkingIngestion, "start", lambda self: None)
    with TestClient(main.app, headers={"Authorization": "Bearer " + identity.token()}) as client:
        assert client.app.state.database is None
        assert client.get("/ready").status_code == 200
        assert database.attempts == 0


@pytest.mark.parametrize("failure", ["connect", "initialize"])
def test_startup_failure_preserves_cached_data_and_recovers(database, failure, monkeypatch, tmp_path, caplog, identity):
    received_at = datetime.now(timezone.utc)
    database.records = {name: StoredFeed(payload=feed.model_dump(mode="json"), received_at=received_at)
                        for name, feed in publisher.demo_feeds().items()}
    path = str(tmp_path / "s4.sqlite")
    monkeypatch.setenv("PARKING_CACHE_PATH", path)
    first = ParkingService(cache_path=path)
    manager = ParkingIngestion(first)
    manager.refresh_once()
    original = first.detail(first.facilities[0], first.clock())
    database.failure = failure
    monkeypatch.setattr(ParkingIngestion, "start", lambda self: None)
    with TestClient(main.app, headers={"Authorization": "Bearer " + identity.token()}) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 200
        service, ingestion = client.app.state.parking, client.app.state.ingestion
        fallback = service.detail(service.facilities[0], service.clock())
        assert fallback.facility == original.facility
        assert fallback.pricing.rates == original.pricing.rates
        assert fallback.pricing.service_updated_at == original.pricing.service_updated_at
        assert fallback.occupancy.service_updated_at == original.occupancy.service_updated_at
        assert not any(ingestion.sources.values())
        assert ingestion.failures == 1
        assert "private connection details" not in caplog.text
        database.failure = None
        ingestion.refresh_once()
        assert all(ingestion.sources.values()) and ingestion.failures == 0
        recovered = service.detail(service.facilities[0], service.clock())
        assert recovered.pricing == original.pricing
        assert recovered.occupancy.service_updated_at == original.occupancy.service_updated_at


def test_background_worker_retries_schema_initialization_after_startup_failure(database):
    received_at = datetime.now(timezone.utc)
    database.records = {name: StoredFeed(payload=feed.model_dump(mode="json"), received_at=received_at)
                        for name, feed in publisher.demo_feeds().items()}
    service = ParkingService()
    manager = ParkingIngestion(service, settings=Settings(interval=.1, retry_max=.2))
    database.failure = "initialize"
    manager.refresh_once()
    assert not service.catalog_available and manager.failures == 1
    database.failure = None
    database.on_read = manager.stop_event.set
    try:
        manager.start()
        manager.thread.join(timeout=3)
        assert not manager.thread.is_alive()
        assert database.attempts == 2
        assert all(manager.sources.values())
        assert service.catalog_available and manager.failures == 0
    finally:
        manager.close()
