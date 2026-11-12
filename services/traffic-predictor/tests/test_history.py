"""T021 deterministic historical-aggregation tests; fixture contains synthetic examples."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.history import HistoryStore, TrafficObservation, congestion_level
from app.models import HistoryQuery
from app.main import app

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_store() -> HistoryStore:
    return HistoryStore.from_files(
        FIXTURES / "EXAMPLE_ONLY_historical_traffic.csv",
        FIXTURES / "EXAMPLE_ONLY_history_metadata.json",
    )


def query(**kwargs) -> HistoryQuery:
    values = dict(
        latitude=43.0096, longitude=-81.2737, timezone="America/Toronto",
        start_at="2026-11-01T00:00:00Z", end_at="2026-11-10T00:00:00Z",
    )
    values.update(kwargs)
    return HistoryQuery.model_validate(values)


def test_monday_8am_average_and_sample_count():
    result = fixture_store().historical_patterns(query(day_of_week="monday", local_hour=8))
    assert result.pagination.total == 1
    assert result.items[0].observation_count == 3
    assert result.items[0].average_congestion_score == pytest.approx(0.75)
    assert result.items[0].congestion_level == "high"
    assert result.data_status == "historical"
    assert result.is_live is False


def test_toronto_dst_fall_back_groups_two_distinct_real_hours():
    result = fixture_store().historical_patterns(query(day_of_week="sunday", local_hour=1))
    assert result.items[0].observation_count == 2
    assert result.items[0].average_congestion_score == pytest.approx(0.55)


def test_missing_bucket_is_unknown_not_fake_zero():
    result = fixture_store().historical_patterns(query(day_of_week="wednesday", local_hour=3))
    item = result.items[0]
    assert item.average_congestion_score is None
    assert item.congestion_level == "unknown"
    assert item.observation_count == 0


def test_other_weekdays_and_filters_do_not_leak():
    monday = fixture_store().historical_patterns(query(day_of_week="monday", local_hour=8))
    tuesday = fixture_store().historical_patterns(query(day_of_week="tuesday", local_hour=8))
    assert monday.items[0].observation_count == 3
    assert tuesday.items[0].observation_count == 1
    assert tuesday.items[0].average_congestion_score == pytest.approx(0.4)


def test_pagination_is_applied_after_sorting():
    result = fixture_store().historical_patterns(query(limit=2, offset=1))
    assert result.pagination.total == 168
    assert result.pagination.limit == 2
    assert result.pagination.has_more is True
    assert len(result.items) == 2
    assert [item.local_hour for item in result.items] == [1, 2]


def test_no_coverage_is_404():
    from app.errors import ServiceError
    with pytest.raises(ServiceError) as caught:
        fixture_store().historical_patterns(query(latitude=44.0))
    assert caught.value.status_code == 404
    assert caught.value.code == "COVERAGE_NOT_FOUND"


def test_stale_metadata_is_reported_without_changing_historical_results():
    store = fixture_store()
    store.dataset_refresh_due_at = datetime.now(timezone.utc) - timedelta(hours=1)
    result = store.historical_patterns(query(day_of_week="monday", local_hour=8))
    assert result.is_stale is True
    assert result.items[0].observation_count == 3


def test_duplicate_conflicting_record_rejected():
    store = fixture_store()
    first = store.observations[0]
    conflict = TrafficObservation(first.segment_id, first.latitude, first.longitude,
                                  first.observed_at, 20.0, first.free_flow_speed_kmh)
    with pytest.raises(ValueError):
        HistoryStore([first, conflict], store.dataset_updated_at, store.dataset_refresh_due_at)


def test_speed_score_thresholds_are_explicit():
    assert congestion_level(0.0) == "low"
    assert congestion_level(0.3) == "moderate"
    assert congestion_level(0.6) == "high"
    assert congestion_level(None) == "unknown"


def test_live_http_route_independent_of_untrained_prediction_model(monkeypatch):
    monkeypatch.setenv("S2_HISTORY_CSV", str(FIXTURES / "EXAMPLE_ONLY_historical_traffic.csv"))
    monkeypatch.setenv("S2_HISTORY_METADATA", str(FIXTURES / "EXAMPLE_ONLY_history_metadata.json"))
    monkeypatch.delenv("REGISTRY_URL", raising=False)
    monkeypatch.delenv("S2_DATABASE_URL", raising=False)
    with TestClient(app) as client:
        response = client.get("/v1/traffic/history/patterns", params={
            "latitude": 43.0096, "longitude": -81.2737,
            "timezone": "America/Toronto", "day_of_week": "monday", "local_hour": 8,
            "start_at": "2026-11-01T00:00:00Z", "end_at": "2026-11-10T00:00:00Z",
        })
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["items"][0]["observation_count"] == 3
        assert data["items"][0]["average_congestion_score"] == pytest.approx(0.75)
        assert data["dataset_updated_at"].endswith("Z")
        assert response.headers["X-Request-ID"]
        # Forecast routes correctly remain unavailable until a verified model exists.
        forecast = client.post("/v1/traffic/predictions", json={
            "location": {"latitude": 43.0096, "longitude": -81.2737},
            "forecast_time": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        })
        assert forecast.status_code == 503
