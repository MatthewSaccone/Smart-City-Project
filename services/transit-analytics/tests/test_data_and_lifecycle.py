from datetime import timedelta
import json
from pathlib import Path
import time

from fastapi.testclient import TestClient
import pytest

import app.main as main
from app.models import ScheduleDataset
from app.repository import Repository
from conftest import AREA, NOW


@pytest.mark.parametrize("mutation", ["duplicate-stop", "unknown-route", "unknown-agency", "unknown-stop",
    "unknown-calendar", "unordered-times", "naive-time", "empty-events", "nonfinite-coordinate", "extra-field"])
def test_invalid_schedule_inputs_are_rejected(client, mutation):
    repo = client.app.state.repository
    data = json.loads(repo.schedule_path.read_text())
    if mutation == "duplicate-stop": data["stops"].append(data["stops"][0])
    if mutation == "unknown-route": data["trips"][0]["route_id"] = "absent"
    if mutation == "unknown-agency": data["routes"][0]["agency_id"] = "absent"
    if mutation == "unknown-stop": data["trips"][0]["stop_times"][0]["stop_id"] = "absent"
    if mutation == "unknown-calendar": data["trips"][0]["service_id"] = "absent"
    if mutation == "unordered-times": data["trips"][0]["stop_times"][0]["departure_seconds"] = 9999
    if mutation == "naive-time": data["valid_from"] = "2026-11-01T00:00:00"
    if mutation == "empty-events": data["trips"][0]["stop_times"][0].update(arrival_seconds=None, departure_seconds=None)
    if mutation == "nonfinite-coordinate": data["stops"][0]["latitude"] = float("nan")
    if mutation == "extra-field": data["private_data"] = "must reject"
    with pytest.raises(ValueError):
        ScheduleDataset.model_validate(data)


@pytest.mark.parametrize("mutation", ["future-source", "future-feed", "future-trip", "future-occupancy",
    "bad-trip", "wrong-version", "bad-route", "bad-stop", "duplicate-update", "over-capacity", "wrong-percent", "bad-event-order"])
def test_bad_live_refresh_retains_previous_data_without_claiming_live(client, fresh, publish, mutation):
    repo = client.app.state.repository
    revision = repo.live.revision
    future = (NOW + timedelta(seconds=1)).isoformat()
    if mutation == "future-source": fresh["source_updated_at"] = future
    if mutation == "future-feed": fresh["trips"]["updated_at"] = future
    if mutation == "future-trip": fresh["trips"]["items"][0]["updated_at"] = future
    if mutation == "future-occupancy": fresh["occupancy"]["items"][0]["observed_at"] = future
    if mutation == "bad-trip": fresh["trips"]["items"][0]["trip_id"] = "absent"
    if mutation == "wrong-version": fresh["schedule_version"] = "uninstalled"
    if mutation == "bad-route": fresh["occupancy"]["items"][0]["route_id"] = "absent"
    if mutation == "bad-stop": fresh["disruptions"]["items"][0]["stop_ids"] = ["absent"]
    if mutation == "duplicate-update": fresh["trips"]["items"].append(fresh["trips"]["items"][0])
    if mutation == "over-capacity": fresh["occupancy"]["items"][1]["passenger_count"] = 51
    if mutation == "wrong-percent": fresh["occupancy"]["items"][1]["occupancy_percent"] = 5
    if mutation == "bad-event-order": fresh["trips"]["items"][0]["departure_estimated_at"] = "2026-11-22T12:02:00Z"
    publish(fresh)
    assert repo.live.revision == revision and not repo.live_available
    response = client.get("/v1/transit/stops/stop-102/arrivals")
    assert response.status_code == 200 and response.json()["provenance"]["is_live"] is False


def test_future_source_dates_are_not_replaced_with_processing_time(client):
    repo = client.app.state.repository
    data = json.loads(repo.schedule_path.read_text())
    data["source_updated_at"] = (NOW + timedelta(seconds=1)).isoformat()
    before = repo.schedule
    repo.schedule_path.write_text(json.dumps(data))
    repo.refresh()
    assert repo.schedule == before and not repo.schedule_available
    assert client.get("/v1/transit/stops", params=AREA).json()["source_updated_at"] is None


def test_schedule_refresh_cannot_expose_live_records_with_obsolete_references(client, fresh):
    repo = client.app.state.repository
    schedule = json.loads(repo.schedule_path.read_text())
    schedule["trips"] = [item for item in schedule["trips"] if item["trip_id"] != "trip-5-1200"]
    # Model the moment between the independent schedule and live refreshes.
    repo.install("schedule", schedule, repo.clock())
    response = client.get("/v1/transit/delays", params={"route_id": "route-5"})
    assert response.status_code == 200
    assert response.json()["coverage_status"] == "unavailable"
    assert response.json()["items"] == []
    assert client.get("/ready").status_code == 200


def test_cancelled_and_early_live_trips(client, fresh, publish):
    update = fresh["trips"]["items"][0]
    update["cancelled"] = True
    publish(fresh)
    arrivals = client.get("/v1/transit/stops/stop-102/arrivals").json()["items"]
    assert not any(item["trip_id"] == update["trip_id"] for item in arrivals)
    update.update(cancelled=False, arrival_estimated_at="2026-11-22T11:59:00Z", departure_estimated_at="2026-11-22T12:00:00Z")
    publish(fresh)
    result = client.get("/v1/transit/stops/stop-102/arrivals", params={"start_at": "2026-11-22T11:55:00Z"}).json()
    row = next(item for item in result["items"] if item["trip_id"] == update["trip_id"])
    assert row["arrival"]["delay_seconds"] == -60 and row["departure"]["delay_seconds"] == -60
    assert client.get("/v1/transit/delays", params={"route_id": "route-5"}).json()["items"] == []


def test_interval_end_and_calendar_additions(client, configuration):
    configuration.now = NOW.replace(month=12, day=31, hour=23, minute=30)
    assert client.get("/v1/transit/stops/stop-102/arrivals").status_code == 422
    assert client.get("/v1/transit/stops/stop-102/arrivals", params={"window_minutes": 30}).status_code == 200
    repo = client.app.state.repository
    configuration.now = NOW
    data = json.loads(repo.schedule_path.read_text())
    data["calendars"][0].update(weekdays=[0], added_dates=["2026-11-22"])
    repo.schedule_path.write_text(json.dumps(data))
    repo.refresh()
    assert client.get("/v1/transit/stops/stop-102/arrivals").json()["items"]


def test_requests_use_installed_snapshots_without_file_or_database_access(client, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Request attempted source I/O")
    repo = client.app.state.repository
    monkeypatch.setattr(repo.database, "connect", forbidden)
    monkeypatch.setattr(repo, "refresh", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    assert client.get("/v1/transit/stops", params=AREA).status_code == 200
    assert client.get("/v1/transit/stops/stop-102/arrivals").status_code == 200


def test_worker_installs_changes_and_stops_cleanly(configuration, monkeypatch):
    monkeypatch.setenv("TRANSIT_REFRESH_SECONDS", ".1")
    repo = Repository()
    repo.refresh()
    before = repo.schedule.revision
    data = json.loads(repo.schedule_path.read_text())
    data["stops"][0]["name"] = "Changed simulated station"
    repo.schedule_path.write_text(json.dumps(data))
    try:
        repo.start()
        deadline = time.monotonic() + 3
        while repo.schedule.revision == before and time.monotonic() < deadline:
            time.sleep(.02)
        assert repo.schedule.revision != before
    finally:
        repo.close()
    assert not repo.thread.is_alive()


def test_registry_lifecycle_uses_existing_client_and_five_second_heartbeats(monkeypatch):
    calls = []
    class Registry:
        def __init__(self, **kwargs): calls.append(kwargs)
        async def start(self): calls.append("started")
        async def stop(self): calls.append("stopped")
    monkeypatch.setattr(main, "REGISTRY_URL", "http://isolated-registry.invalid")
    monkeypatch.setattr(main, "REGISTRY_API_KEY", "isolated-test-key")
    monkeypatch.setattr(main, "RegistryClient", Registry)
    with TestClient(main.app) as client:
        assert client.get("/ready").status_code == 200
        assert calls[0]["service_id"] == "transit-analytics"
        assert calls[0]["heartbeat_interval"] == 5
    assert calls[1:] == ["started", "stopped"]


def test_configured_registry_requires_its_existing_api_key(monkeypatch):
    monkeypatch.setattr(main, "REGISTRY_URL", "http://isolated-registry.invalid")
    monkeypatch.setattr(main, "REGISTRY_API_KEY", None)
    with pytest.raises(RuntimeError, match="REGISTRY_API_KEY"):
        with TestClient(main.app):
            pass


@pytest.mark.parametrize("name,value", [("TRANSIT_REFRESH_SECONDS", "nan"), ("TRANSIT_REFRESH_SECONDS", "0"),
    ("TRANSIT_LIVE_RETENTION_SECONDS", "inf"), ("TRANSIT_LIVE_MODE", "undefined")])
def test_configuration_limits(name, value, monkeypatch):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError): Repository()
