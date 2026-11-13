"""Independent S4 streams, invalidation, bounded fallback and dataset validation."""

from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.datasets import CatalogFeed, DatasetChanged, StoredFeed, revision, validate_feed
from app.ingestion import ParkingIngestion, Settings
from app.service import ParkingService
from app.sources import UnavailableSource

TOOL = Path(__file__).resolve().parents[1] / "tools/import_dataset.py"
spec = importlib.util.spec_from_file_location("s4_import_dataset", TOOL)
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)
NOW = datetime(2026, 11, 12, 17, tzinfo=timezone.utc)


@pytest.fixture
def local(tmp_path):
    clock = SimpleNamespace(now=NOW)
    service = ParkingService(cache_path=str(tmp_path / "cache.sqlite"), clock=lambda: clock.now)
    ingestion = ParkingIngestion(service)
    ingestion.refresh_once()
    yield service, ingestion, clock
    ingestion.close()


def test_refresh_is_idempotent_and_preserves_independent_update_times(local):
    service, ingestion, clock = local
    before = service.detail(service.facilities[0], clock.now)
    updates = ingestion.updates
    clock.now += timedelta(seconds=60)
    ingestion.refresh_once()
    after = service.detail(service.facilities[0], clock.now)
    assert ingestion.updates == updates == 3
    assert after.occupancy.service_updated_at == before.occupancy.service_updated_at
    assert after.pricing.service_updated_at == before.pricing.service_updated_at
    assert after.pricing.source_updated_at is None


def test_occupancy_outage_preserves_catalog_and_tariff_source_state(local):
    service, ingestion, clock = local
    before = service.detail(service.facilities[0], clock.now)
    service.source = UnavailableSource()
    ingestion.refresh_once()
    assert ingestion.sources == {"catalog": True, "pricing": True, "occupancy": False}
    after = service.detail(service.facilities[0], clock.now)
    assert after.pricing == before.pricing
    assert after.occupancy.observed_at == before.occupancy.observed_at
    assert after.availability_status == "unknown"
    service.require_catalog()


def test_pricing_update_does_not_change_occupancy_timestamp(local, monkeypatch, tmp_path):
    service, ingestion, clock = local
    before = service.detail(service.facilities[0], clock.now)
    payload = publisher.demo_feeds()["pricing"].model_dump(mode="json")
    payload["facilities"]["demo-central"]["rates"][0]["amount"] = 4
    path = tmp_path / "rates.json"
    path.write_text(json.dumps(payload))
    monkeypatch.setenv("PARKING_PRICING_FILE", str(path))
    clock.now += timedelta(seconds=30)
    ingestion.refresh_once()
    after = service.detail(service.facilities[0], clock.now)
    assert float(after.pricing.rates[0].amount) == 4
    assert after.pricing.service_updated_at == clock.now
    assert after.pricing.source_updated_at is None
    assert after.occupancy.service_updated_at == before.occupancy.service_updated_at
    path.write_text("malformed")
    ingestion.refresh_once()
    assert not ingestion.sources["pricing"] and ingestion.sources["occupancy"]
    assert service.detail(service.facilities[0], clock.now).pricing.rates == after.pricing.rates


def test_restart_restores_catalog_tariffs_and_occupancy(local, monkeypatch):
    service, ingestion, clock = local
    before = service.detail(service.facilities[0], clock.now)
    # Same data-source namespace, but no functioning source on this restart.
    restarted = ParkingService(source=UnavailableSource(), cache_path=service.cache.path, clock=lambda: clock.now)
    restored = ParkingIngestion(restarted)
    after = restarted.detail(restarted.facilities[0], clock.now)
    assert after.pricing == before.pricing
    assert after.occupancy.service_updated_at == before.occupancy.service_updated_at
    retained = restarted.cache.read_streams(restored.namespace)
    assert set(retained) == {"catalog", "pricing", "occupancy"}
    assert "subject_id" not in json.dumps({key: value.model_dump(mode="json") for key, value in retained.items()})


def test_retention_expires_occupancy_without_removing_known_rates(local):
    service, ingestion, clock = local
    clock.now += timedelta(seconds=86401)
    after = service.detail(service.facilities[0], clock.now)
    assert after.occupancy.estimated_occupied_spaces is None
    assert after.occupancy.data_status == "unavailable"
    assert after.pricing.rates and after.facility.name


def test_partial_postgres_failure_does_not_poison_other_streams(monkeypatch):
    monkeypatch.setenv("PARKING_DATABASE_URL", "postgresql://not-connected.invalid/s4")
    service = ParkingService(clock=lambda: NOW)
    ingestion = ParkingIngestion(service)
    records = {name: StoredFeed(payload=feed.model_dump(mode="json"), received_at=NOW)
               for name, feed in publisher.demo_feeds().items()}
    records["occupancy"] = StoredFeed(payload={"invalid": "aggregate"}, received_at=NOW)
    ingestion.database = SimpleNamespace(read=lambda connection: records)
    with pytest.raises(Exception):
        ingestion.reconcile(object())
    assert ingestion.sources == {"catalog": True, "pricing": True, "occupancy": False}
    assert service.catalog_available and service.prices


@pytest.mark.parametrize("stream", ["catalog", "pricing", "occupancy"])
def test_malformed_and_unknown_fields_rejected(stream):
    payload = publisher.demo_feeds()[stream].model_dump(mode="json")
    with pytest.raises(ValueError):
        validate_feed(stream, {**payload, "subject_id": "private-user"})
    with pytest.raises(ValueError):
        validate_feed(stream, {**payload, "schema_version": 99})


def test_catalog_capacity_change_invalidates_incompatible_occupancy(local):
    service, ingestion, clock = local
    payload = publisher.demo_feeds()["catalog"].model_dump(mode="json")
    payload["facilities"][0]["capacity"] = 1
    ingestion.install("catalog", StoredFeed(payload=payload, received_at=clock.now))
    assert service.snapshot is None
    assert service.detail(service.facilities[0], clock.now).occupancy.estimated_available_spaces is None


def test_file_source_cannot_certify_live_data(local, monkeypatch, tmp_path):
    service, ingestion, clock = local
    payload = publisher.demo_feeds()["catalog"].model_dump(mode="json")
    payload["simulated"] = False
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(payload))
    monkeypatch.setenv("PARKING_CATALOG_FILE", str(path))
    ingestion.refresh_once()
    assert not ingestion.sources["catalog"]
    assert service.catalog_source == "simulated_fixture" and service.catalog_available


@pytest.mark.parametrize("name,value", [("PARKING_REFRESH_SECONDS", "nan"), ("PARKING_REFRESH_SECONDS", "0"),
    ("PARKING_RETRY_MAX_SECONDS", "1"), ("PARKING_CACHE_RETENTION_SECONDS", "-1")])
def test_settings_are_finite_and_bounded(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        Settings.from_env()


def test_changed_event_validates_version_and_digest():
    event = DatasetChanged(stream="catalog", revision=revision(publisher.demo_feeds()["catalog"]))
    assert DatasetChanged.model_validate_json(event.model_dump_json()) == event
    with pytest.raises(ValueError):
        DatasetChanged.model_validate({**event.model_dump(), "stream": "users"})


def test_custom_file_catalog_survives_restart_with_its_occupancy_cache(tmp_path, monkeypatch):
    feeds = publisher.demo_feeds()
    catalog = feeds["catalog"].model_dump(mode="json")
    catalog["source"] = "custom_operator"
    ids = {item["facility_id"]: "custom-" + item["facility_id"] for item in catalog["facilities"]}
    for item in catalog["facilities"]:
        item["facility_id"] = ids[item["facility_id"]]
    pricing = feeds["pricing"].model_dump(mode="json")
    pricing["facilities"] = {ids[key]: value for key, value in pricing["facilities"].items()}
    for stream, payload in (("catalog", catalog), ("pricing", pricing)):
        path = tmp_path / (stream + ".json")
        path.write_text(json.dumps(payload))
        monkeypatch.setenv("PARKING_" + stream.upper() + "_FILE", str(path))
    cache_path = str(tmp_path / "cached.sqlite")
    first = ParkingService(cache_path=cache_path, clock=lambda: NOW)
    manager = ParkingIngestion(first)
    manager.refresh_once()
    original = first.detail(first.facilities[0], NOW)
    restarted = ParkingService(cache_path=cache_path, clock=lambda: NOW)
    restored = ParkingIngestion(restarted)
    after = restarted.detail(restarted.facilities[0], NOW)
    assert restarted.cache.path == cache_path
    assert after.facility == original.facility
    assert after.facility_source == "simulated:custom_operator"
    assert after.pricing == original.pricing
    assert after.occupancy.estimated_occupied_spaces == original.occupancy.estimated_occupied_spaces


def test_occupancy_remains_usable_when_optional_disk_write_fails(monkeypatch):
    service = ParkingService(clock=lambda: NOW)
    monkeypatch.setattr(service.cache, "write", lambda snapshot: (_ for _ in ()).throw(OSError("disk full")))
    service.refresh()
    assert service.feed_available and service.snapshot is not None


def test_catalog_refresh_during_reads_keeps_capacity_and_occupancy_consistent(client):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    import time
    from test_parking import BOUNDS
    service = client.app.state.parking
    ingestion = client.app.state.ingestion
    stop = Event()

    def replace_catalog():
        index = 0
        while not stop.is_set():
            payload = publisher.demo_feeds()["catalog"].model_dump(mode="json")
            payload["facilities"][0]["capacity"] = 1 if index % 2 else 4
            ingestion.install("catalog", StoredFeed(payload=payload, received_at=service.clock()))
            service.refresh()
            index += 1
            time.sleep(.001)
        return index

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(replace_catalog)
        try:
            for _ in range(30):
                response = client.get("/v1/parking/facilities", params=BOUNDS)
                assert response.status_code == 200
                for item in response.json()["items"]:
                    occupancy = item["occupancy"]
                    if occupancy["estimated_occupied_spaces"] is not None:
                        assert occupancy["estimated_occupied_spaces"] <= item["capacity"]
                        assert occupancy["estimated_occupied_spaces"] + occupancy["estimated_available_spaces"] == item["capacity"]
        finally:
            stop.set()
        assert writer.result(timeout=5) > 0
    assert client.get("/v1/parking/ingestion/status?unexpected=1").status_code == 422
