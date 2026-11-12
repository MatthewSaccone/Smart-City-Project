"""Unit tests for S2 request/response models (contract Step 3)."""
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.models import (
    AreaQuery, Coordinate, HighPeriodsQuery, HistoryQuery,
    PointPredictionRequest, PointPredictionResponse,
)


NOW = lambda: datetime.now(timezone.utc)


def valid_point_request():
    return {
        "location": {"latitude": 43.0096, "longitude": -81.2737},
        "forecast_time": (NOW() + timedelta(hours=2)).isoformat(),
    }


def test_point_request_is_valid():
    request = PointPredictionRequest.model_validate(valid_point_request())
    assert request.location.latitude == 43.0096
    assert request.forecast_time.tzinfo is not None


@pytest.mark.parametrize("bad_latitude", [90.1, -91, float("nan"), float("inf")])
def test_reject_invalid_coordinates(bad_latitude):
    data = valid_point_request()
    data["location"]["latitude"] = bad_latitude
    with pytest.raises(ValidationError):
        PointPredictionRequest.model_validate(data)


def test_reject_extra_request_field():
    data = valid_point_request()
    data["unexpected"] = True
    with pytest.raises(ValidationError):
        PointPredictionRequest.model_validate(data)


@pytest.mark.parametrize("hours", [-1, 24 * 8])
def test_reject_forecast_outside_allowed_window(hours):
    data = valid_point_request()
    data["forecast_time"] = (NOW() + timedelta(hours=hours)).isoformat()
    with pytest.raises(ValidationError):
        PointPredictionRequest.model_validate(data)


def test_reject_timestamp_without_timezone():
    data = valid_point_request()
    data["forecast_time"] = (datetime.now() + timedelta(hours=1)).isoformat()
    with pytest.raises(ValidationError):
        PointPredictionRequest.model_validate(data)


def test_history_query_validates_timezone_and_date_pair():
    base = {"latitude": 43.0, "longitude": -81.2, "timezone": "America/Toronto"}
    HistoryQuery.model_validate(base)
    with pytest.raises(ValidationError):
        HistoryQuery.model_validate(base | {"timezone": "Not/A_Timezone"})
    with pytest.raises(ValidationError):
        HistoryQuery.model_validate(base | {"start_at": (NOW() - timedelta(days=10)).isoformat()})


def test_high_periods_limit_is_48_hours():
    args = {"latitude": 43.0, "longitude": -81.2, "radius_m": 1500}
    with pytest.raises(ValidationError):
        HighPeriodsQuery.model_validate(args | {
            "start_at": NOW() + timedelta(hours=1),
            "end_at": NOW() + timedelta(hours=51),
        })


def test_area_query_rejects_excessive_radius():
    with pytest.raises(ValidationError):
        AreaQuery.model_validate({
            "latitude": 43.0, "longitude": -81.2, "radius_m": 10001,
            "forecast_time": NOW() + timedelta(hours=1)
        })


def test_response_timestamps_render_as_utc_z():
    result = PointPredictionResponse.model_validate({
        "prediction": {
            "location": {"latitude": 43.0, "longitude": -81.2},
            "forecast_time": NOW() + timedelta(hours=2),
            "congestion_level": "high", "estimated_congestion_score": 0.8,
        },
        "generated_at": NOW(), "model_version": "test-model-1",
        "training_data_end_at": NOW() - timedelta(days=1),
        "model_valid_until": NOW() + timedelta(days=1),
        "as_of": NOW(), "is_stale": False,
    })
    encoded = result.model_dump(mode="json")
    assert encoded["generated_at"].endswith("Z")
    assert encoded["prediction"]["forecast_time"].endswith("Z")
    assert encoded["source"] == "historical_traffic_model"
    assert encoded["is_live"] is False
