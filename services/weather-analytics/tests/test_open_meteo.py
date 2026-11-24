"""Open-Meteo adapter parsing against recorded-shape responses (no network)."""

import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from app.providers import OpenMeteoProvider, ProviderInvalid, ProviderTimeout, ProviderUnavailable

NOW = datetime(2026, 11, 23, 18, 7, tzinfo=timezone.utc)
T0 = int(datetime(2026, 11, 23, 16, tzinfo=timezone.utc).timestamp())

FORECAST = {
    "latitude": 43.0, "longitude": -81.25, "utc_offset_seconds": 0, "timezone": "GMT",
    "current_units": {"time": "unixtime", "interval": "seconds", "temperature_2m": "°C",
                      "wind_speed_10m": "m/s", "wind_gusts_10m": "m/s", "precipitation": "mm"},
    "current": {"time": T0 + 7200, "interval": 900, "temperature_2m": 7.5, "relative_humidity_2m": 72,
                "apparent_temperature": 5.4, "precipitation": 0.5, "weather_code": 45,
                "wind_speed_10m": 4.2, "wind_gusts_10m": 7.1, "visibility": 650.0},
    "hourly_units": {"time": "unixtime", "temperature_2m": "°C", "wind_speed_10m": "m/s"},
    "hourly": {"time": [T0, T0 + 3600, T0 + 7200],
               "temperature_2m": [6.0, 7.0, None], "relative_humidity_2m": [70, 71, 72],
               "precipitation_probability": [10, 20, 30], "precipitation": [0, 0.2, 0.5],
               "weather_code": [3, 61, 45], "wind_speed_10m": [3.0, 4.0, 4.2]},
}
AIR = {
    "current_units": {"pm2_5": "μg/m³", "ozone": "μg/m³"},
    "current": {"time": T0 + 7200, "interval": 3600, "pm2_5": 12.5, "pm10": 20.0, "ozone": 60.0,
                "nitrogen_dioxide": 30.0, "sulphur_dioxide": None, "carbon_monoxide": 250.0},
    "hourly": {"time": [T0, T0 + 3600, T0 + 7200], "pm2_5": [10, 11, 12.5],
               "ozone": [55, 58, 60], "nitrogen_dioxide": [28, 29, 30]},
}


def provider_for(handler):
    return OpenMeteoProvider(transport=httpx.MockTransport(handler))


def run(coroutine):
    return asyncio.run(coroutine)


def test_weather_is_normalized_with_request_parameters():
    seen = {}

    def handler(request):
        seen.update(request.url.params)
        return httpx.Response(200, json=FORECAST)

    async def go():
        provider = provider_for(handler)
        try:
            return await provider.weather(43.0096, -81.2737, now=NOW, past_days=5)
        finally:
            await provider.close()

    bundle = run(go())
    assert seen["wind_speed_unit"] == "ms" and seen["timeformat"] == "unixtime" and seen["past_days"] == "5"
    current = bundle["current"]
    assert current["observed_at"] == "2026-11-23T18:00:00Z"
    assert current["precipitation_rate_mm_h"] == 2.0           # 0.5 mm per 15 min
    assert current["visibility_m"] == 650.0 and current["weather_code"] == 45
    assert bundle["hourly"][2]["temperature_c"] is None
    assert bundle["retrieved_at"] == "2026-11-23T18:07:00Z" and bundle["source"] == "open-meteo"


def test_air_quality_is_normalized():
    bundle = run(provider_for(lambda request: httpx.Response(200, json=AIR)).air_quality(43, -81, now=NOW))
    assert bundle["current"]["o3"] == 60.0 and bundle["current"]["so2"] is None
    assert [row["no2"] for row in bundle["hourly"]] == [28, 29, 30]


@pytest.mark.parametrize("response, error", [
    (httpx.Response(500), ProviderUnavailable),
    (httpx.Response(429), ProviderUnavailable),
    (httpx.Response(400, json={"error": True, "reason": "bad"}), ProviderInvalid),
    (httpx.Response(200, text="not json"), ProviderInvalid),
    (httpx.Response(200, json={**FORECAST, "current_units": {"temperature_2m": "°F"}}), ProviderInvalid),
    (httpx.Response(200, json={**FORECAST, "hourly": {**FORECAST["hourly"], "temperature_2m": [1]}}), ProviderInvalid),
    (httpx.Response(200, json={k: v for k, v in FORECAST.items() if k != "current"}), ProviderInvalid),
])
def test_bad_responses_are_classified(response, error):
    with pytest.raises(error):
        run(provider_for(lambda request: response).weather(43, -81, now=NOW))


def test_timeouts_and_connection_errors_are_classified():
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ProviderTimeout):
        run(provider_for(timeout).weather(43, -81, now=NOW))
    with pytest.raises(ProviderUnavailable):
        run(provider_for(refused).weather(43, -81, now=NOW))
