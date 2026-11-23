from datetime import timedelta
import json
import os

from fastapi.testclient import TestClient
import pytest

import app.main as main
from app.repository import Repository
from conftest import AREA, NOW

ARRIVALS = "/v1/transit/stops/stop-102/arrivals"


@pytest.mark.parametrize("age,status,live", [(60, "fresh", True), (60.001, "stale", False), (61, "stale", False)])
def test_exact_freshness_boundary(client, fresh, publish, age, status, live):
    stamp = (NOW - timedelta(seconds=age)).isoformat()
    fresh["trips"]["updated_at"] = stamp
    fresh["trips"]["items"][0]["updated_at"] = stamp
    publish(fresh)
    result = client.get(ARRIVALS, params={"window_minutes": 15}).json()
    provenance = result["provenance"]
    assert provenance["live_update_status"] == status and provenance["is_live"] is live
    assert provenance["is_stale"] is (not live)
    trip = next(item for item in result["items"] if item["trip_id"] == "trip-5-1200")
    event = trip["arrival"]
    assert event["basis"] == ("live_estimate" if live else "schedule")
    assert event["estimated_at"] == ("2026-11-22T12:03:00Z" if live else None)
    assert provenance["last_live_update_at"] == stamp.replace("+00:00", "Z")


def test_old_item_cannot_be_refreshed_by_a_newer_feed_timestamp(client, fresh, publish):
    fresh["trips"]["updated_at"] = NOW.isoformat()
    fresh["trips"]["items"][0]["updated_at"] = (NOW - timedelta(seconds=61)).isoformat()
    publish(fresh)
    result = client.get(ARRIVALS).json()
    assert result["provenance"]["is_stale"]
    assert all(event["basis"] == "schedule" for row in result["items"] for event in (row["arrival"], row["departure"]) if event)


@pytest.mark.parametrize("where", ["feed", "item"])
def test_unknown_update_times_never_claim_live_estimates(client, fresh, publish, where):
    if where == "feed":
        fresh["trips"]["updated_at"] = None
    else:
        fresh["trips"]["items"][0]["updated_at"] = None
    publish(fresh)
    result = client.get(ARRIVALS).json()
    assert result["provenance"]["live_update_status"] == "unavailable"
    assert result["provenance"]["last_live_update_at"] is None
    assert not result["provenance"]["is_stale"]


def test_live_file_outage_preserves_schedule_and_original_timestamps(client, fresh):
    repo = client.app.state.repository
    before = client.get(ARRIVALS).json()
    repo.live_path.unlink()
    repo.refresh()
    after = client.get(ARRIVALS).json()
    assert client.get("/ready").status_code == 200
    assert after["items"] and after["provenance"]["is_live"] is False
    assert after["provenance"]["last_live_update_at"] == before["provenance"]["last_live_update_at"]
    assert after["provenance"]["live_update_status"] == "unavailable"
    assert all(event["estimated_at"] is None for row in after["items"] for event in (row["arrival"], row["departure"]) if event)
    alerts = client.get("/v1/transit/disruptions", params={"route_id": "route-5"}).json()
    assert alerts["coverage_status"] == "partial" and alerts["provenance"]["data_status"] == "cached"
    assert alerts["items"]


def test_unavailable_optional_categories_are_explicit(client, fresh, publish):
    fresh.update(trips=None, occupancy=None, disruptions=None)
    publish(fresh)
    assert client.get(ARRIVALS).status_code == 200
    for path in ("occupancy", "delays"):
        result = client.get("/v1/transit/" + path, params={"route_id": "route-5"}).json()
        assert result["items"] == [] and result["coverage_status"] == "unavailable"
        assert result["provenance"]["live_update_status"] == "unavailable"
    assert client.get("/v1/transit/disruptions", params={"route_id": "route-5"}).status_code == 503


def test_schedule_and_live_survive_restart_with_both_source_files_missing(client, fresh):
    repo = client.app.state.repository
    original = client.get(ARRIVALS).json()
    schedule_receipt = repo.schedule.received_at
    live_receipt = repo.live.received_at
    repo.schedule_path.unlink()
    repo.live_path.unlink()
    restarted = Repository()
    restarted.refresh()
    assert restarted.schedule.received_at == schedule_receipt and restarted.live.received_at == live_receipt
    assert not restarted.live_available
    from app.service import TransitService
    from app.routes import ArrivalsQuery
    fallback = TransitService(restarted).arrivals("stop-102", ArrivalsQuery())
    assert fallback.items and fallback.provenance.last_live_update_at.isoformat().replace("+00:00", "Z") == original["provenance"]["last_live_update_at"]
    assert fallback.provenance.data_status == "scheduled"


def test_refresh_does_not_fabricate_new_ingestion_timestamps(client, configuration):
    repo = client.app.state.repository
    before = (repo.schedule.received_at, repo.live.received_at)
    configuration.now += timedelta(seconds=5)
    repo.refresh()
    assert (repo.schedule.received_at, repo.live.received_at) == before
    assert (Repository().schedule.received_at, Repository().live.received_at) == before


def test_expired_retention_discards_live_counts_but_preserves_schedules(client, fresh, configuration):
    configuration.now += timedelta(seconds=86401)
    assert client.get("/ready").status_code == 200
    occupancy = client.get("/v1/transit/occupancy", params={"route_id": "route-5"}).json()
    assert occupancy["items"] == [] and occupancy["coverage_status"] == "unavailable"
    assert client.get(ARRIVALS).json()["items"]


def test_schedule_horizon_and_startup_without_essential_data(monkeypatch, configuration):
    path = os.environ["TRANSIT_SCHEDULE_FILE"]
    from pathlib import Path
    Path(path).unlink()
    with TestClient(main.app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 503
        assert client.get(ARRIVALS).status_code == 503


def test_expired_schedule_makes_service_unready(client, configuration):
    configuration.now = NOW.replace(year=2027, month=2)
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 503
    assert client.get("/v1/transit/stops", params=AREA).status_code == 503


def test_calendar_exceptions_and_after_midnight_service(client, configuration):
    configuration.now = NOW.replace(hour=1)
    result = client.get(ARRIVALS, params={"window_minutes": 5}).json()
    assert any(item["trip_id"] == "trip-night" for item in result["items"])
    repo = client.app.state.repository
    payload = json.loads(repo.schedule_path.read_text())
    payload["calendars"][0]["removed_dates"] = ["2026-11-21", "2026-11-22"]
    repo.schedule_path.write_text(json.dumps(payload))
    repo.refresh()
    assert client.get(ARRIVALS).json()["items"] == []


def test_missing_live_data_never_changes_fixture_provenance_into_live(client):
    result = client.get(ARRIVALS).json()
    assert result["provenance"]["data_status"] == "simulated"
    assert not result["provenance"]["is_live"]
