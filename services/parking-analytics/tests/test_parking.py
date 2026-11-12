from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.analytics import distance_m, estimate_occupancy
from app.models import Facility, Observation


NOW = datetime(2026, 11, 9, 17, tzinfo=timezone.utc)
LOT = Facility(facility_id="lot", name="Test lot", latitude=43.65,
               longitude=-79.38, capacity=2, geofence_radius_m=50)
BOUNDS = {"min_lat": 43.65, "min_lon": -79.39, "max_lat": 43.66, "max_lon": -79.37}


def sample(subject="one", seconds=-10, latitude=43.65, longitude=-79.38):
    return Observation(subject_id=subject, latitude=latitude, longitude=longitude,
                       observed_at=NOW + timedelta(seconds=seconds))


def stationary(subject="one"):
    return [sample(subject, -50), sample(subject, -10)]


ESTIMATE_HEADERS = {"Authorization": "Bearer test-estimator-token-long-enough"}


def test_geographic_search_with_calculated_fixture_occupancy(client):
    response = client.get("/v1/parking/facilities", params=BOUNDS)
    assert response.status_code == 200
    body = response.json()
    assert body["facility_source"] == "simulated_fixture"
    facilities = {item["facility_id"]: item for item in body["items"]}
    assert set(facilities) == {"demo-central", "demo-east"}
    assert facilities["demo-central"]["occupancy"]["estimated_occupied_spaces"] == 2
    assert facilities["demo-east"]["occupancy"]["congestion"] == "full"
    assert facilities["demo-central"]["occupancy"]["data_status"] == "simulated"
    assert facilities["demo-central"]["occupancy"]["observed_at"] == "2026-11-09T16:59:55Z"


def test_inclusive_point_bounds_and_empty_area(client):
    response = client.get("/v1/parking/facilities", params={
        "min_lat": 43.6532, "max_lat": 43.6532, "min_lon": -79.3832, "max_lon": -79.3832})
    assert [f["facility_id"] for f in response.json()["items"]] == ["demo-central"]
    assert client.get("/v1/parking/facilities", params={
        "min_lat": 0, "max_lat": 1, "min_lon": 0, "max_lon": 1}).json()["items"] == []


@pytest.mark.parametrize("changes", [
    {"min_lat": 91}, {"max_lon": -181}, {"min_lat": 45}, {"min_lon": -70},
    {"min_lat": "NaN"}, {"max_lon": "inf"}, {"min_lon": "not-a-number"},
])
def test_invalid_bounds(client, changes):
    assert client.get("/v1/parking/facilities", params=BOUNDS | changes).status_code == 422


def test_missing_bounds_and_existing_health(client):
    assert client.get("/v1/parking/facilities").status_code == 422
    assert client.get("/health").status_code == 200


def test_dwell_duplicates_and_multiple_subjects():
    observations = stationary() + stationary() + stationary("two") + [sample("passing")]
    result = estimate_occupancy([LOT], observations, NOW)[0]
    assert result.estimated_occupied_spaces == 2
    assert result.qualifying_subjects == 2
    assert result.estimated_available_spaces == 0 and result.congestion == "full"


@pytest.mark.parametrize("observations", [
    [], [sample()], [sample(seconds=-29), sample(seconds=0)],
    [sample(seconds=-120), sample(seconds=-61)],
    [sample(seconds=10), sample(seconds=50)],
    stationary() + [sample(seconds=-1, latitude=44)],
    [sample(seconds=-90), sample(seconds=-10)],
])
def test_insufficient_stale_future_departed_or_discontinuous_evidence_is_unknown(observations):
    result = estimate_occupancy([LOT], observations, NOW)[0]
    assert result.congestion == "unknown"
    assert result.estimated_available_spaces is None
    assert result.occupancy_ratio is None


def test_unsorted_samples_and_inclusive_freshness_dwell_boundaries():
    result = estimate_occupancy([LOT], [sample(seconds=-60), sample(seconds=-90)], NOW)[0]
    assert result.estimated_occupied_spaces == 1
    assert result.occupancy_ratio == 0.5


def test_future_samples_do_not_replace_valid_evidence():
    result = estimate_occupancy([LOT], stationary() + [sample(seconds=5, latitude=44)], NOW)[0]
    assert result.estimated_occupied_spaces == 1


def test_overlapping_geofences_do_not_count_same_subject_twice():
    other = LOT.model_copy(update={"facility_id": "other", "latitude": 43.6501})
    results = estimate_occupancy([other, LOT], stationary(), NOW)
    assert {item.facility_id: item.qualifying_subjects for item in results} == {"other": 0, "lot": 1}


def test_capacity_is_capped_and_overflow_is_visible():
    result = estimate_occupancy([LOT], sum((stationary(str(i)) for i in range(3)), []), NOW)[0]
    assert result.estimated_occupied_spaces == 2
    assert result.qualifying_subjects == 3 and result.capacity_exceeded is True


def test_batch_endpoint_calculates_from_changed_inputs_without_persisting(client):
    observations = [sample(latitude=43.6532, longitude=-79.3832, seconds=t).model_dump(mode="json")
                    for t in (-50, -10)]
    request = {"as_of": NOW.isoformat(), "observations": observations}
    body = client.post("/v1/parking/occupancy/estimate", json=request, headers=ESTIMATE_HEADERS).json()
    assert body["source"] == "submitted_observations" and body["is_live"] is False
    assert body["items"][0]["estimated_occupied_spaces"] == 1
    empty = client.post("/v1/parking/occupancy/estimate", json={"observations": []}, headers=ESTIMATE_HEADERS)
    assert empty.status_code == 200
    assert all(item["congestion"] == "unknown" for item in empty.json()["items"])


@pytest.mark.parametrize("changes", [
    {"observed_at": "2026-11-09T17:00:00"}, {"latitude": 91},
    {"subject_id": ""}, {"subject_id": "   "}, {"unexpected": "value"},
])
def test_observation_validation(client, changes):
    observation = sample().model_dump(mode="json") | changes
    assert client.post("/v1/parking/occupancy/estimate", headers=ESTIMATE_HEADERS, json={
        "observations": [observation]}).status_code == 422


def test_conflicting_same_timestamp_is_rejected(client):
    first = sample().model_dump(mode="json")
    assert client.post("/v1/parking/occupancy/estimate", headers=ESTIMATE_HEADERS, json={
        "observations": [first, first | {"latitude": 44}]}).status_code == 422


def test_distance_uses_meters_and_rejects_timezone_free_analysis():
    assert distance_m(0, 0, 0, 0) == 0
    assert 111000 < distance_m(0, 0, 0, 1) < 112000
    with pytest.raises(ValueError, match="timezone"):
        estimate_occupancy([LOT], [], datetime(2026, 11, 9))


def test_existing_registry_lifecycle_is_preserved(monkeypatch):
    registry = Mock(start=AsyncMock(), stop=AsyncMock())
    constructor = Mock(return_value=registry)
    monkeypatch.setattr(main, "REGISTRY_URL", "http://registry:8080")
    monkeypatch.setattr(main, "REGISTRY_API_KEY", "test-key")
    monkeypatch.setattr(main, "RegistryClient", constructor)
    with TestClient(main.app):
        registry.start.assert_awaited_once()
        assert constructor.call_args.kwargs["service_id"] == "parking-analytics"
        assert constructor.call_args.kwargs["heartbeat_interval"] == 5.0
    registry.stop.assert_awaited_once()


def test_registry_key_is_still_required(monkeypatch):
    monkeypatch.setattr(main, "REGISTRY_URL", "http://registry:8080")
    monkeypatch.setattr(main, "REGISTRY_API_KEY", None)
    with pytest.raises(RuntimeError, match="REGISTRY_API_KEY"):
        with TestClient(main.app):
            pass


def test_facility_without_geofence_cannot_produce_a_false_count():
    facility = LOT.model_copy(update={"geofence_radius_m": None})
    assert estimate_occupancy([facility], stationary(), NOW)[0].estimated_occupied_spaces is None
