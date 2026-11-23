"""Every S5 response validates against the adopted OpenAPI contract."""

from datetime import timedelta
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
import pytest
import yaml

from conftest import NOW, POINT, z

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = yaml.safe_load((ROOT / "shared/api-contracts/s5-weather-air-quality.openapi.yaml").read_text(encoding="utf-8"))


def validate(response, schema):
    Draft202012Validator({"$ref": f"#/components/schemas/{schema}", "components": CONTRACT["components"]},
                         format_checker=FormatChecker()).validate(response.json())
    assert response.headers["X-Request-ID"]


CASES = [
    ("/v1/weather/current", {}, "CurrentWeatherResponse"),
    ("/v1/weather/forecast", {"start_at": z(NOW + timedelta(hours=1)), "end_at": z(NOW + timedelta(days=3)),
                              "resolution": "daily"}, "ForecastResponse"),
    ("/v1/weather/forecast", {"start_at": z(NOW + timedelta(hours=1)), "end_at": z(NOW + timedelta(hours=9))},
     "ForecastResponse"),
    ("/v1/environment/air-quality", {}, "AirQualityResponse"),
    ("/v1/environment/trends", {"metric": "aqhi", "start_at": z(NOW - timedelta(days=1)), "end_at": z(NOW)},
     "TrendResponse"),
    ("/v1/weather/travel-guidance", {}, "TravelGuidanceResponse"),
    ("/v1/weather/travel-guidance", {"travel_at": z(NOW + timedelta(days=2))}, "TravelGuidanceResponse"),
]


@pytest.mark.parametrize("path, params, schema", CASES)
def test_success_responses_match_contract(client, path, params, schema):
    response = client.get(path, params={**POINT, **params})
    assert response.status_code == 200, response.text
    validate(response, schema)


@pytest.mark.parametrize("path, params, schema", CASES[:2] + CASES[3:4])
def test_cached_fallback_responses_match_contract(client, provider, clock, path, params, schema):
    client.get(path, params={**POINT, **params})
    provider.mode = {"weather": "down", "air": "down"}
    clock.now = NOW + timedelta(minutes=50)
    response = client.get(path, params={**POINT, **params})
    validate(response, schema)


def test_operations_and_errors_match_contract(client):
    validate(client.get("/health"), "HealthResponse")
    validate(client.get("/ready"), "ReadyResponse")
    validate(client.get("/v1/weather/current", params={"latitude": 1, "longitude": 1}), "ErrorEnvelope")
    validate(client.get("/v1/weather/current", params={"latitude": 100, "longitude": 1}), "ErrorEnvelope")
