# S5 Weather & Air Quality Analytics — T046–T053

This service implements the adopted [S5 contract](../../docs/api/contracts/S5-Weather-Air-Quality.md)
([OpenAPI](../../shared/api-contracts/s5-weather-air-quality.openapi.yaml)). All five operations are
public, read-only `GET`s, so no JWT is needed. Every response carries `X-Request-ID`, and errors use the
platform `error` envelope.

| GET path | Requirement | Notes |
|---|---|---|
| `/v1/weather/current?latitude&longitude` | FR1, FR3, QR1–QR3 | Includes humidity. Unavailable measurements are `null`, never `0` |
| `/v1/weather/forecast?latitude&longitude&start_at&end_at` | FR2, QR1–QR3 | `resolution=hourly\|daily`, `sort_by`, `sort_order`, `limit`, `offset`. Window ≤ 7 days ahead |
| `/v1/environment/air-quality?latitude&longitude` | FR3, QR1, QR2 | Canadian AQHI plus PM2.5/PM10 (µg/m³), O₃/NO₂/SO₂ (ppb), CO (ppm) |
| `/v1/environment/trends?latitude&longitude&metric&start_at&end_at` | FR3 (trends) | `temperature_c`, `relative_humidity_pct`, `pm2_5_ug_m3`, `aqhi`. Past 30 days, ≤ 7-day range |
| `/v1/weather/travel-guidance?latitude&longitude[&travel_at]` | FR4, FR5, QR1–QR3 | Rule set [`weather_travel_rules_v1`](../../docs/api/contracts/S5-Travel-Rules-v1.md) |
| `/health`, `/ready` | Operations | `/ready` stays 200 during a provider outage while cached weather exists |

## Data provider

| `WEATHER_PROVIDER` | Behaviour |
|---|---|
| `open_meteo` (default) | [Open-Meteo](https://open-meteo.com) forecast and air-quality APIs. Free, no API key, global coverage. Its values come from weather models, not station readings |
| `simulated` | Deterministic synthetic data for offline demos. Always labelled `data_status: simulated` and `source: simulated:…` |
| `unavailable` | Every provider call fails. Use it to demonstrate the QR3 fallback |

Open-Meteo does not publish the time a forecast run was issued, so S5 reports the time it retrieved the
forecast as `issued_at`, and `source_updated_at` is `null`. Requests outside `WEATHER_COVERAGE_BBOX`
(default: Ontario) return `404 NOT_FOUND`.

## Caching, freshness and outage fallback (QR1, QR3)

- Data is cached per ~1 km cell (lat/lon rounded to 0.01°) and refreshed at most every
  `WEATHER_REFRESH_SECONDS` (300 s). Concurrent requests for the same cell share one provider call.
- Each provider call is cut off after `WEATHER_PROVIDER_TIMEOUT_SECONDS` (1.5 s, capped at 1.8 s), so a
  slow or hung provider can't push a response past the 2000 ms target.
- If the provider fails, the last bundle for that cell is returned with its **original** `observed_at`,
  plus `data_status: cached` and `is_live: false`. `is_stale` turns true past the contract cut-offs:
  30 min for weather, 60 min for air quality, 6 h for forecasts. After a failure, S5 waits
  `WEATHER_RETRY_BACKOFF_SECONDS` (30 s) before calling the provider again for that cell.
- With nothing cached, a provider timeout returns `504`, an unusable response returns `502`, and an
  unreachable provider returns `503`. Air quality fails independently of weather.
- Bundles and hourly history live in an S5-owned SQLite file (`WEATHER_DATABASE_PATH`), so the fallback
  still works after a restart. Hourly history is kept for 35 days and feeds `/environment/trends`.
  When a trend request reaches further back than the stored history, S5 backfills it from the provider
  (up to 31 days).

## Run locally (repository root)

Bash:

```bash
python -m pip install -r services/weather-analytics/requirements-dev.txt
cd services/weather-analytics && python -m pytest -q
PYTHONPATH=.. WEATHER_PROVIDER=simulated python -m uvicorn app.main:app --port 8085
```

PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -r services/weather-analytics/requirements-dev.txt
$env:PYTHONPATH = (Resolve-Path .\services).Path
$env:REGISTRY_URL = ''
$env:WEATHER_PROVIDER = 'open_meteo'   # or 'simulated' when offline
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir services/weather-analytics --port 8085
```

Then open <http://127.0.0.1:8085/docs>, or try:

```
GET /v1/weather/current?latitude=43.0096&longitude=-81.2737
GET /v1/weather/travel-guidance?latitude=43.0096&longitude=-81.2737&travel_at=2026-11-25T12:00:00Z
```

## Docker Compose

The root Compose file already registers `weather-analytics` on port 8085. The S5 overlay adds a
persistent data volume, provider settings and a health check:

```bash
docker compose -f docker-compose.yml -f services/weather-analytics/compose.weather.yml up --build -d weather-analytics
```

The dashboard's **Weather Analytics** page calls `/v1/weather/current` through
`frontend/src/app/api/service-request/route.ts`.

## Task verification (T046–T053)

`python -m pytest -q` runs 82 tests with no network access. The provider is faked, and Open-Meteo is
exercised with recorded-shape responses.

| Task | Requirement | Evidence |
|---|---|---|
| T046 | FR1 current weather | `test_api.py::test_current_weather_returns_measurements_units_and_timestamps`, `test_missing_measurements_are_null_not_zero` |
| T047 | QR1 ≤ 2000 ms | `test_performance.py`: every read under a 300 ms provider, every read with a hung provider (falls back inside budget), cached p95 < 100 ms |
| T048 | FR2 forecast | `test_api.py::test_hourly_forecast_*`, `test_daily_forecast_*`, `test_forecast_validation` |
| T049 | QR2 timestamps | `observed_at` / `issued_at` / `service_updated_at` assertions in `test_api.py` and `test_fallback.py`; UTC `Z` outputs |
| T050 | FR3 AQ, humidity, pollution | `test_api.py::test_air_quality_units_and_aqhi`, `test_aqhi_is_null_*`, trends tests |
| T051 | QR3 outage fallback | `test_fallback.py`: cached + stale flags, original timestamps, restart survival, 502/503/504 mapping, backoff |
| T052 | FR4 travel hazards | `test_api.py::test_guidance_*`, `test_analytics.py::test_rule_thresholds` |
| T053 | FR5 recommendations | `test_guidance_detects_hazards_from_current_conditions`, `test_guidance_insufficient_data_*` |
| — | Contract conformance | `test_contract.py` validates live and cached responses against the OpenAPI schemas |

**Not yet verified:** live calls to Open-Meteo (the dev sandbox had no outbound access to it) and a
Docker Compose run. Run both once before demo or cloud deployment (T090/T108).


## Shared authentication

See [Authentik setup and verification](../../docs/Authentik-Migration.md) before calling protected routes. Existing dated test reports are not verification of the migrated deployment.
