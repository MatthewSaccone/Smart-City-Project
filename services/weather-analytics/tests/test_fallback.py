"""T051 / S5 QR3: keep serving the most recent weather when the provider fails, clearly marked."""

from datetime import timedelta

import pytest

from conftest import NOW, POINT, z


def current(client):
    return client.get("/v1/weather/current", params=POINT)


def test_outage_serves_cached_weather_with_original_timestamps(client, provider, clock):
    live = current(client).json()
    provider.mode["weather"] = "down"
    clock.now = NOW + timedelta(minutes=10)           # past the 5-minute refresh, still fresh (<30 min)
    body = current(client).json()
    assert body["weather"] == live["weather"]
    assert body["provenance"]["observed_at"] == live["provenance"]["observed_at"]
    assert body["provenance"]["service_updated_at"] == z(NOW)
    assert (body["provenance"]["data_status"], body["provenance"]["is_live"], body["provenance"]["is_stale"]) == \
        ("cached", False, False)
    clock.now = NOW + timedelta(hours=3)              # older than 30 minutes -> clearly outdated
    body = current(client).json()
    assert body["provenance"]["is_stale"] is True and body["provenance"]["data_status"] == "cached"
    assert body["provenance"]["observed_at"] == live["provenance"]["observed_at"]
    assert body["as_of"] == z(NOW + timedelta(hours=3))


def test_forecast_and_air_quality_fall_back_independently(client, provider, clock):
    params = {**POINT, "start_at": z(NOW + timedelta(hours=8)), "end_at": z(NOW + timedelta(hours=10))}
    client.get("/v1/weather/forecast", params=params)
    provider.mode["weather"] = "timeout"
    clock.now = NOW + timedelta(hours=7)
    body = client.get("/v1/weather/forecast", params=params).json()
    assert body["data_status"] == "cached" and body["is_stale"] is True and len(body["items"]) == 2
    # Air quality has never been fetched and is down: only that operation fails.
    provider.mode["air"] = "down"
    response = client.get("/v1/environment/air-quality", params=POINT)
    assert response.status_code == 503 and response.json()["error"]["retryable"] is True
    assert current(client).status_code == 200


@pytest.mark.parametrize("mode, status, code, source", [
    ("timeout", 504, "UPSTREAM_TIMEOUT", "upstream"),
    ("invalid", 502, "UPSTREAM_FAILURE", "upstream"),
    ("down", 503, "SERVICE_UNAVAILABLE", "service"),
])
def test_no_cache_maps_provider_failures_to_errors(client, provider, mode, status, code, source):
    provider.mode["weather"] = mode
    response = current(client)
    assert response.status_code == status
    error = response.json()["error"]
    assert (error["code"], error["source"], error["retryable"]) == (code, source, True)


def test_backoff_avoids_hammering_a_failed_provider(client, provider, clock):
    current(client)
    provider.mode["weather"] = "down"
    clock.now = NOW + timedelta(minutes=6)
    for _ in range(5):
        assert current(client).json()["provenance"]["data_status"] == "cached"
    assert provider.calls["weather"] == 2
    provider.mode["weather"] = "ok"
    clock.now += timedelta(seconds=31)
    assert current(client).json()["provenance"]["data_status"] == "live"


def test_cached_weather_survives_restart(client, provider, clock, make_service):
    current(client)
    provider.mode["weather"] = "down"
    client.app.state.weather = make_service()          # new process, same S5-owned store
    clock.now = NOW + timedelta(minutes=40)
    body = current(client).json()
    assert body["provenance"]["data_status"] == "cached" and body["provenance"]["is_stale"] is True
    assert body["provenance"]["observed_at"] == z(NOW - timedelta(minutes=5))
    assert client.get("/ready").status_code == 200


def test_ready_fails_only_without_any_usable_weather(client, provider):
    provider.mode["weather"] = "down"
    current(client)
    response = client.get("/ready")
    assert response.status_code == 503 and response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    provider.mode = {"weather": "ok", "air": "down"}
    client.app.state.weather.failures.clear()
    current(client)
    assert client.get("/ready").status_code == 200    # missing AQ alone does not fail readiness


def test_hung_provider_is_cut_off_by_time_budget(client, provider, clock):
    current(client)
    provider.mode["weather"] = "hang"
    clock.now = NOW + timedelta(minutes=6)
    body = current(client).json()
    assert body["provenance"]["data_status"] == "cached"
