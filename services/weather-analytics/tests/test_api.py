"""Functional tests for T046-T053 (S5 FR1-FR5, QR2) over the public HTTP API."""

from datetime import timedelta

from conftest import NOW, POINT, z


def get(client, path, **params):
    return client.get(path, params={**POINT, **params})


# ------------------------------------------------------------------ T046 FR1 / T050 FR3 / T049 QR2

def test_current_weather_returns_measurements_units_and_timestamps(client):
    response = get(client, "/v1/weather/current")
    assert response.status_code == 200
    body = response.json()
    assert body["location"] == POINT
    assert body["weather"] == {"temperature_c": 7.5, "feels_like_c": 5.4, "relative_humidity_pct": 72.0,
                               "precipitation_rate_mm_h": 0.0, "wind_speed_m_s": 4.2, "wind_gust_m_s": 7.1,
                               "visibility_m": 9000.0, "condition_code": "cloudy"}
    provenance = body["provenance"]
    assert provenance["observed_at"] == z(NOW - timedelta(minutes=5))
    assert provenance["service_updated_at"] == z(NOW)
    assert provenance["source_updated_at"].endswith("Z") and body["as_of"].endswith("Z")
    assert provenance | {"observed_at": None} == {
        "observed_at": None, "source_updated_at": z(NOW - timedelta(minutes=4)), "service_updated_at": z(NOW),
        "is_live": True, "is_stale": False, "source": "test_weather_feed", "data_status": "live"}


def test_missing_measurements_are_null_not_zero(client, provider):
    provider.overrides["weather"] = {"visibility_m": None, "wind_gust_m_s": None, "weather_code": None}
    weather = get(client, "/v1/weather/current").json()["weather"]
    assert weather["visibility_m"] is None and weather["wind_gust_m_s"] is None
    assert weather["condition_code"] == "unknown"


def test_outside_coverage_is_404_and_bad_coordinates_422(client):
    response = client.get("/v1/weather/current", params={"latitude": 10, "longitude": 10})
    assert response.status_code == 404 and response.json()["error"]["code"] == "NOT_FOUND"
    for params in ({"latitude": 91, "longitude": 0}, {"latitude": "nan", "longitude": -81},
                   {"latitude": 43}, {"latitude": 43, "longitude": -81, "unknown": 1}):
        response = client.get("/v1/weather/current", params=params)
        assert response.status_code == 422, params
        assert response.json()["error"]["code"] == "VALIDATION_FAILED"


def test_request_id_is_echoed_and_repeated_params_rejected(client):
    response = client.get("/v1/weather/current", params=POINT, headers={"X-Request-ID": "req-s5-001"})
    assert response.headers["X-Request-ID"] == "req-s5-001"
    response = client.get("/v1/weather/current?latitude=43&latitude=44&longitude=-81",
                          headers={"X-Request-ID": "req-s5-002"})
    assert response.status_code == 422
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"] == "req-s5-002"


def test_get_with_body_is_400(client):
    response = client.request("GET", "/v1/weather/current", params=POINT, content=b"{}")
    assert response.status_code == 400 and response.json()["error"]["code"] == "BAD_REQUEST"


# ------------------------------------------------------------------ T048 FR2

def window(start_hours, end_hours, **extra):
    return {"start_at": z(NOW + timedelta(hours=start_hours)), "end_at": z(NOW + timedelta(hours=end_hours)), **extra}


def test_hourly_forecast_bounds_issue_time_and_pagination(client):
    body = get(client, "/v1/weather/forecast", **window(2, 6, limit=3)).json()
    assert [item["valid_from"] for item in body["items"]] == [z(NOW + timedelta(hours=h)) for h in (2, 3, 4)]
    assert body["pagination"] == {"limit": 3, "offset": 0, "total": 4, "has_more": True}
    assert body["issued_at"] == z(NOW - timedelta(minutes=4))
    assert body["issued_at"] not in {item["valid_from"] for item in body["items"]}
    assert body["resolution"] == "hourly" and body["data_status"] == "live" and not body["is_stale"]
    second = get(client, "/v1/weather/forecast", **window(2, 6, limit=3, offset=3)).json()
    assert [i["valid_from"] for i in second["items"]] == [z(NOW + timedelta(hours=5))]
    descending = get(client, "/v1/weather/forecast", **window(2, 6, sort_order="desc")).json()
    assert descending["items"][0]["valid_from"] == z(NOW + timedelta(hours=5))


def test_daily_forecast_aggregates_utc_days(client):
    body = get(client, "/v1/weather/forecast", **window(3, 54, resolution="daily")).json()
    # 21:00 today -> 00:00 in two days intersects three UTC days.
    assert [item["valid_from"] for item in body["items"]] == [
        z(NOW.replace(hour=0) + timedelta(days=d)) for d in (0, 1, 2)]
    first = body["items"][0]
    assert first["valid_to"] == z(NOW.replace(hour=0) + timedelta(days=1))
    assert first["condition_code"] == "cloudy" and first["temperature_c"] is not None


def test_forecast_validation(client):
    cases = [window(-2, 2), window(3, 2), window(1, 24 * 8), {"start_at": "2026-11-24", "end_at": "2026-11-25"},
             {"start_at": "2026-11-24T01:00:00", "end_at": "2026-11-24T02:00:00"},
             window(1, 2, resolution="weekly"), window(1, 2, limit=101)]
    for params in cases:
        assert get(client, "/v1/weather/forecast", **params).status_code == 422, params
    reasons = get(client, "/v1/weather/forecast", **window(3, 2)).json()["error"]["details"]
    assert reasons == [{"field": "end_at", "reason": "must_be_after_start_at"}]


def test_forecast_accepts_offsets_and_returns_utc(client):
    params = {"start_at": "2026-11-23T15:00:00-05:00", "end_at": "2026-11-23T17:00:00-05:00"}
    body = get(client, "/v1/weather/forecast", **params).json()
    assert body["items"][0]["valid_from"] == "2026-11-23T20:00:00Z"


# ------------------------------------------------------------------ T050 FR3 air quality

def test_air_quality_units_and_aqhi(client):
    body = get(client, "/v1/environment/air-quality").json()
    readings = {item["pollutant"]: item for item in body["pollutants"]}
    assert readings["pm2_5"] == {"pollutant": "pm2_5", "concentration": 12.5, "unit": "ug_m3"}
    assert readings["o3"]["unit"] == "ppb" and abs(readings["o3"]["concentration"] - 30.56) < 0.01
    assert readings["no2"]["unit"] == "ppb" and abs(readings["no2"]["concentration"] - 15.94) < 0.01
    assert readings["co"]["unit"] == "ppm" and abs(readings["co"]["concentration"] - 0.2182) < 0.001
    assert body["air_quality_index"] == {"system": "ca_aqhi", "value": 3.0, "category": "low"}
    assert body["provenance"]["observed_at"] == z(NOW) and body["provenance"]["source_updated_at"] is None


def test_aqhi_is_null_and_missing_pollutants_omitted_when_unavailable(client, provider):
    provider.overrides["air"] = {"so2": None, "co": None}
    service = client.app.state.weather
    body = get(client, "/v1/environment/air-quality").json()
    assert {item["pollutant"] for item in body["pollutants"]} == {"pm2_5", "pm10", "o3", "no2"}
    # Remove an hour of NO2 from the cached 3-hour window -> AQHI cannot be computed reliably.
    entry = next(e for (kind, _), e in service.entries.items() if kind == "air")
    for row in entry.hourly:
        if row["time"] == NOW - timedelta(hours=1):
            row["no2"] = None
    assert get(client, "/v1/environment/air-quality").json()["air_quality_index"] is None


# ------------------------------------------------------------------ trends

def trend(client, metric, start_hours, end_hours, **extra):
    return get(client, "/v1/environment/trends", metric=metric, start_at=z(NOW - timedelta(hours=start_hours)),
               end_at=z(NOW - timedelta(hours=end_hours)), **extra)


def test_trends_hourly_buckets_are_historical(client):
    body = trend(client, "relative_humidity_pct", 4, 0).json()
    assert [item["value"] for item in body["items"]] == [70.0] * 4
    assert body["items"][0] == {"period_start": z(NOW - timedelta(hours=4)), "period_end": z(NOW - timedelta(hours=3)),
                                "value": 70.0, "unit": "pct", "sample_count": 1}
    assert body["coverage_status"] == "complete"
    assert (body["data_status"], body["is_live"], body["is_stale"]) == ("historical", False, False)
    assert body["dataset_updated_at"] == z(NOW) and body["source"] == "test_weather_feed"


def test_trends_backfill_history_and_report_partial_coverage(client, provider):
    body = trend(client, "pm2_5_ug_m3", 24 * 5, 24 * 4, resolution="daily").json()
    assert max(provider.past_days) == 6
    assert body["items"][0]["unit"] == "ug_m3"
    # The provider's simulated history begins at midnight, so the first day is full.
    assert body["coverage_status"] in {"complete", "partial"}
    provider.mode["weather"] = "down"
    gap = trend(client, "temperature_c", 24 * 20, 24 * 19).json()
    assert gap["coverage_status"] == "unavailable"
    assert all(item["value"] is None and item["sample_count"] == 0 for item in gap["items"])


def test_trend_validation(client):
    assert trend(client, "temperature_c", 2, 3).status_code == 422          # end before start
    assert trend(client, "temperature_c", 24 * 31, 24 * 30).status_code == 422  # older than 30 days
    assert trend(client, "temperature_c", 24 * 8, 0).status_code == 422     # longer than 7 days
    assert trend(client, "temperature_c", 1, -2).status_code == 422         # end in the future
    assert trend(client, "wind", 2, 0).status_code == 422                    # unsupported metric


# ------------------------------------------------------------------ T052 FR4 / T053 FR5

def guidance(client, **params):
    return get(client, "/v1/weather/travel-guidance", **params).json()


def test_guidance_low_risk_with_traceable_inputs(client):
    body = guidance(client)
    assert (body["assessment_status"], body["risk_level"], body["hazards"]) == ("assessed", "low", [])
    assert body["rule_set_version"] == "weather_travel_rules_v1"
    assert [item["dataset"] for item in body["inputs"]] == ["weather_observation", "air_quality"]
    assert any("not an official warning" in text for text in body["recommendations"])


def test_guidance_detects_hazards_from_current_conditions(client, provider):
    provider.overrides["weather"] = {"visibility_m": 150.0, "weather_code": 66, "temperature_c": -1.0,
                                     "feels_like_c": -8.0, "wind_speed_m_s": 15.0, "wind_gust_m_s": 21.0}
    body = guidance(client)
    hazards = {item["hazard_type"]: item["severity"] for item in body["hazards"]}
    assert hazards == {"reduced_visibility": "high", "strong_wind": "moderate", "icy_conditions": "high"}
    assert body["risk_level"] == "high" and len(body["recommendations"]) == 4


def test_guidance_poor_air_quality(client, provider):
    provider.overrides["air"] = {}
    service = client.app.state.weather
    guidance(client)
    entry = next(e for (kind, _), e in service.entries.items() if kind == "air")
    for row in entry.hourly:
        row.update(o3=400.0, no2=150.0, pm2_5=90.0)
    body = guidance(client)
    assert {h["hazard_type"]: h["severity"] for h in body["hazards"]} == {"poor_air_quality": "high"}


def test_guidance_uses_forecast_for_future_travel(client, provider):
    body = guidance(client, travel_at=z(NOW + timedelta(hours=30)))
    assert [item["dataset"] for item in body["inputs"]] == ["weather_forecast"]
    assert body["assessment_status"] == "assessed"
    assert guidance(client, travel_at=z(NOW - timedelta(hours=2))).get("error")


def test_guidance_insufficient_data_when_observation_stale_or_missing(client, provider, clock):
    get(client, "/v1/weather/current")
    provider.mode = {"weather": "down", "air": "down"}
    clock.now = NOW + timedelta(hours=2)
    body = guidance(client)
    assert (body["assessment_status"], body["risk_level"]) == ("insufficient_data", "unknown")
    assert body["inputs"][0]["is_stale"] is True and body["hazards"] == []
    client.app.state.weather.entries.clear()
    body = guidance(client)
    assert (body["assessment_status"], body["risk_level"], body["inputs"]) == ("insufficient_data", "unknown", [])


# ------------------------------------------------------------------ operations

def test_health_and_ready(client):
    assert client.get("/health").json() == {"service": "weather-analytics", "status": "healthy"}
    assert client.get("/ready").json() == {"service": "weather-analytics", "status": "ready"}
