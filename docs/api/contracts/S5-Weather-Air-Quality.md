# S5 — Weather & Air Quality: API Contract

| Property | Value |
|---|---|
| Document ID | `T008-S5-API` |
| Version | `1.0` (initial contract) |
| Status | Adopted T008 interface design; provider selection and implementation are separate tasks |
| Logical service ID | `weather-analytics` (S5) |
| API version | `/v1` |
| Governing conventions | `docs/api/API-Standards.md` |
| Machine-readable specification | `shared/api-contracts/s5-weather-air-quality.openapi.yaml` |

## 1. Purpose and scope

S5 provides **current weather measurements**, **location-based forecasts**, **air-quality and pollution measurements**, **environmental trends**, and **weather-related travel precautions**. Clients must be able to distinguish source measurements, forward-looking forecasts, and **derived** travel guidance. A travel-risk assessment is informational: it is **not** a declaration that a route is safe, nor a replacement for authoritative weather warnings or emergency instructions.

All five client-facing operations are public read-only `GET` requests. Data ingestion, external-provider polling, weather-model calculations, and cached-feed refresh are internal integrations, **not** public data-submission operations. The external providers and identity/trust mechanisms for provider ingestion are **not yet selected**.

### Requirements traceability (PR1, `Service Requirements`, S5)

| Requirement | PR1 obligation | Contract coverage |
|---|---|---|
| **FR1** | Current weather conditions for a user-defined location | `GET /v1/weather/current` |
| **FR2** | Weather forecasts for a user-defined location and future period | `GET /v1/weather/forecast` |
| **FR3** | Environmental conditions including air quality, humidity and pollution measurements | Humidity in `current`; air-quality index and named pollutants in `GET /v1/environment/air-quality` |
| **FR4** | Identify weather conditions that may affect travel safety | Hazard assessment in `GET /v1/weather/travel-guidance` |
| **FR5** | Provide travel-related recommendations or precautions based on reported and forecast weather | `recommendations[]` and evidence in `travel-guidance` |
| **QR1** | Return available information within **2,000 ms** of a valid request | Response-latency acceptance tests for all S5 read operations |
| **QR2** | Provide observation or update timestamps for weather and environmental measurements | Per-dataset provenance and `observed_at`, `source_updated_at`, `service_updated_at` as applicable |
| **QR3** | Continue returning the most recently available weather information when the external source is unavailable, **clearly identifying outdated information** | Cached fallback with `is_stale: true`, `data_status: cached`; unavailable fields are `null`, not zero |

**Scope note:** The `List of Services` worksheet also mentions “live trends”; `GET /v1/environment/trends` makes trends explicit. PR1 **does not** prescribe endpoint paths, weather providers, a risk-scoring algorithm, forecast coverage, measurement thresholds, or the freshness periods chosen below. Those values are **T008 design decisions**. No S5 concurrency target is specified in PR1; do not mistakenly apply S6's 50-concurrent-request requirement to S5.

## 2. Shared data, units, and freshness rules

- Locations are WGS 84 decimal degrees: `latitude` in `[-90,90]`, `longitude` in `[-180,180]`, both finite. Query values are required unless explicitly indicated otherwise. Coordinate pairs represent **points**; the provider's actual spatial precision must not be overstated.
- All timestamps representing instants use ISO 8601/RFC 3339. Inputs may contain explicit UTC offsets, but outputs **MUST** use UTC with `Z`. `as_of` is the server's reference instant; `observed_at` is the physical measurement time; `issued_at` is the time a forecast was issued. These must not be interchanged.
- Weather units: `temperature_c`, `feels_like_c` in °C; `relative_humidity_pct` and `precipitation_probability_pct` in percentage points `0–100`; `precipitation_rate_mm_h` in mm/h; `wind_speed_m_s` and `wind_gust_m_s` in m/s; `visibility_m` in metres.
- Pollutant concentration units are fixed by type: `pm2_5` and `pm10` use **µg/m³** (`ug_m3`); `o3`, `no2`, `so2` use **parts per billion** (`ppb`); `co` uses **parts per million** (`ppm`). Provider inputs MUST be converted before output and must not be silently relabeled.
- `air_quality_index` is nullable; when present, its `system` is `ca_aqhi`, its `value` is a nonnegative numeric Canadian Air Quality Health Index value, and its `category` is `low`, `moderate`, `high`, or `very_high`. This is **not interchangeable** with US AQI. If the selected provider cannot supply a reliable AQHI, return `air_quality_index: null`, retaining available pollutant measurements.
- Nullable measurements indicate **unknown/unavailable**, not zero. Unknown conditions use `condition_code: unknown`. Do not derive an apparently precise travel-risk category from missing measurements.

### Freshness policy (T008 design values)

| Data | Age measured from | Fresh when age ≤ | Stale when age > | Degraded behavior |
|---|---|---:|---:|---|
| Current weather | `observed_at` | **30 minutes** | 30 minutes | Return the last measurement with `is_stale: true` and `data_status: cached` when the source fails; preserve actual observation time |
| Air quality / pollution | `observed_at` | **60 minutes** | 60 minutes | Return last available readings as stale when the source fails, with missing pollutants explicitly unavailable |
| Forecast | `issued_at` | **6 hours** | 6 hours | Return most recently issued forecast with `is_stale: true` if the requested interval is covered; never claim expired forecast periods are current observations |
| Historical trends | Historical interval, not the wall-clock age of a historic observation | N/A | N/A | Label historical results as `data_status: historical`; expose dataset timestamps and incomplete intervals rather than calling past measurements live |
| Derived travel guidance | Underlying weather, forecast, and air-quality inputs | Based on applicable rows above | Based on applicable rows above | Flag stale inputs; if essential evidence is missing or too old to support a reliable assessment, set `assessment_status: insufficient_data` and `risk_level: unknown` |

**Provider-outage requirement:** S5 MUST return its most recently available **weather** information with the original timestamps and outdated-data flags when an upstream service fails. Age alone MUST NOT cause the service to erase the last known observation; however, an old observation must never be presented as current. If no weather observation/forecast is stored for a query, S5 may return an appropriate `503`, `502`, or `504` error; air quality can be unavailable independently of weather. A successful weather response must not manufacture values for unavailable sources.

**Provenance representation:** All data-bearing responses include `is_live`, `is_stale`, `source`, and `data_status` (`live`, `cached`, `historical`, `simulated`, `unavailable`) at the relevant dataset level. `source_updated_at` is `null` if not supplied by the provider. The service MUST also return applicable `observed_at`, `issued_at`, and `service_updated_at` timestamps; their meanings follow `API-Standards.md`. Examples use synthetic identifiers and readings, **not confirmed live providers**.

## 3. Endpoint inventory

| Operation ID | Method and path | Purpose | Access | Success |
|---|---|---|---|---|
| `getCurrentWeather` | `GET /v1/weather/current` | Current weather, including humidity | Public | `200` |
| `getWeatherForecast` | `GET /v1/weather/forecast` | Location/time-bounded forecasts | Public | `200` |
| `getAirQuality` | `GET /v1/environment/air-quality` | Latest air quality index and pollutant measurements | Public | `200` |
| `getEnvironmentalTrends` | `GET /v1/environment/trends` | Time-bucketed historical weather/pollution trends | Public | `200` |
| `getWeatherTravelGuidance` | `GET /v1/weather/travel-guidance` | Conditions affecting travel and recommended precautions | Public | `200` |
| `getHealth` | `GET /health` | Process liveness | Monitoring | `200` |
| `getReadiness` | `GET /ready` | Essential read/cached-fallback capability | Monitoring | `200` or `503` |

No GET operation accepts a JSON request body. Successful responses and errors MUST include `X-Request-ID`; error bodies use the adopted common `error` envelope. All five client-facing operations are **read-only** and require no bearer token for v1. Provider credentials remain internal and MUST NOT be exposed in public responses.

### 3.1 Current weather — FR1, FR3, QR1–QR3

`GET /v1/weather/current`

| Query field | Required | Type / validation |
|---|---|---|
| `latitude` | Yes | Finite number in `[-90,90]` |
| `longitude` | Yes | Finite number in `[-180,180]` |

**Example request**

```http
GET /v1/weather/current?latitude=43.0096&longitude=-81.2737
```

**Example 200 response — synthetic**

```json
{
  "location": {"latitude": 43.0096, "longitude": -81.2737},
  "as_of": "2026-11-10T18:00:00Z",
  "weather": {
    "temperature_c": 7.5,
    "feels_like_c": 5.4,
    "relative_humidity_pct": 72,
    "precipitation_rate_mm_h": 0.0,
    "wind_speed_m_s": 4.2,
    "wind_gust_m_s": 7.1,
    "visibility_m": 650,
    "condition_code": "fog"
  },
  "provenance": {
    "observed_at": "2026-11-10T17:55:00Z",
    "source_updated_at": "2026-11-10T17:56:00Z",
    "service_updated_at": "2026-11-10T17:56:05Z",
    "is_live": true,
    "is_stale": false,
    "source": "example_weather_feed",
    "data_status": "live"
  }
}
```

`weather` contains the most recent usable measurement at the requested location. Individual unavailable measurements are `null` rather than `0`; if the last cached observation is old, the **same structure** is returned with `is_live: false`, `is_stale: true`, `data_status: cached`, and its original timestamps. A location outside provider coverage returns `404 NOT_FOUND`; a covered location without current data may return fallback or `503` if nothing usable exists. `condition_code` is one of `clear`, `cloudy`, `rain`, `snow`, `mixed_precipitation`, `fog`, `thunderstorm`, `windy`, `unknown`.

### 3.2 Weather forecast — FR2, FR4, FR5, QR1–QR3

`GET /v1/weather/forecast`

| Query field | Required | Type / validation | Default |
|---|---|---|---|
| `latitude`, `longitude` | Yes | Valid finite WGS 84 coordinates | — |
| `start_at` | Yes | Timezone-aware instant, at or after request receipt | — |
| `end_at` | Yes | Strictly after `start_at`, at most **7 days** after request receipt | — |
| `resolution` | No | `hourly` or `daily` | `hourly` |
| `sort_by` | No | `valid_from` or `valid_to` | `valid_from` |
| `sort_order` | No | `asc` or `desc` | `asc` |
| `limit` | No | Integer `1–100` | `25` |
| `offset` | No | Nonnegative integer | `0` |

The response includes forecast intervals intersecting `[start_at, end_at)`; each item has `valid_from < valid_to`. `hourly` and `daily` describe nominal interval resolution, not a guarantee that a provider has complete coverage. Results MUST be sorted by the requested `sort_by` field and `sort_order`, with `valid_to` as the deterministic tie-breaker when sorting by `valid_from` (or `valid_from` when sorting by `valid_to`). Filters precede sorting and pagination. A covered location with no forecast intervals in the specified range returns `200` with `items: []` and appropriate metadata, not invented readings.

**Example request**

```http
GET /v1/weather/forecast?latitude=43.0096&longitude=-81.2737&start_at=2026-11-12T12%3A00%3A00Z&end_at=2026-11-12T14%3A00%3A00Z&resolution=hourly
```

**Example 200 response — synthetic**

```json
{
  "location": {"latitude": 43.0096, "longitude": -81.2737},
  "as_of": "2026-11-10T18:00:00Z",
  "items": [
    {
      "valid_from": "2026-11-12T12:00:00Z",
      "valid_to": "2026-11-12T13:00:00Z",
      "temperature_c": 4.0,
      "precipitation_probability_pct": 65,
      "wind_speed_m_s": 8.0,
      "condition_code": "rain"
    }
  ],
  "pagination": {"limit": 25, "offset": 0, "total": 1, "has_more": false},
  "resolution": "hourly",
  "issued_at": "2026-11-10T15:00:00Z",
  "source_updated_at": "2026-11-10T15:01:00Z",
  "service_updated_at": "2026-11-10T15:01:10Z",
  "is_live": true,
  "is_stale": false,
  "source": "example_forecast_feed",
  "data_status": "live"
}
```

`issued_at` is the time the forecast run was published, **not** the time predicted for each item. If upstream refresh fails, an available older forecast may be served as `cached` and `is_stale: true`. If the requested future period lies outside all known forecast coverage, return a valid empty collection if the location is supported, with the stale/coverage state documented; never extrapolate secretly. A `502`/`503`/`504` is used when no usable forecast dataset can be served.

### 3.3 Latest air quality and pollution measurements — FR3, QR1–QR2

`GET /v1/environment/air-quality`

Required query parameters: `latitude` and `longitude`, subject to common coordinate validation.

**Example request**

```http
GET /v1/environment/air-quality?latitude=43.0096&longitude=-81.2737
```

**Example 200 response — synthetic**

```json
{
  "location": {"latitude": 43.0096, "longitude": -81.2737},
  "as_of": "2026-11-10T18:00:00Z",
  "air_quality_index": {"system": "ca_aqhi", "value": 4.0, "category": "moderate"},
  "pollutants": [
    {"pollutant": "pm2_5", "concentration": 12.5, "unit": "ug_m3"},
    {"pollutant": "o3", "concentration": 28.0, "unit": "ppb"}
  ],
  "provenance": {
    "observed_at": "2026-11-10T17:30:00Z",
    "source_updated_at": "2026-11-10T17:35:00Z",
    "service_updated_at": "2026-11-10T17:35:10Z",
    "is_live": true,
    "is_stale": false,
    "source": "example_air_quality_feed",
    "data_status": "live"
  }
}
```

`air_quality_index` may be `null` if a comparable Canadian AQHI is not supplied. `pollutants` may be empty **only when data availability is explicitly conveyed**; zero pollutant concentration MUST NOT stand in for missing measurements. Missing AQ data MUST NOT prevent independent weather operations from succeeding. The provider remains unconfirmed.

### 3.4 Environmental trends — S5 service-description trend scope

`GET /v1/environment/trends`

| Query field | Required | Type / validation | Default |
|---|---|---|---|
| `latitude`, `longitude` | Yes | Valid WGS 84 coordinates | — |
| `metric` | Yes | `temperature_c`, `relative_humidity_pct`, `pm2_5_ug_m3`, or `aqhi` | — |
| `start_at`, `end_at` | Yes | Both timezone-aware; `start_at < end_at <= now`; range at most **7 days**, and within last **30 days** | — |
| `resolution` | No | `hourly` or `daily` | `hourly` |
| `sort_by` | No | `period_start` or `period_end` | `period_start` |
| `sort_order` | No | `asc` or `desc` | `asc` |
| `limit` | No | Integer `1–100` | `25` |
| `offset` | No | Nonnegative integer | `0` |

Trend values are arithmetic means of **valid source measurements in each interval**, not predictions. Bucket boundaries follow UTC. Empty/missing buckets MUST NOT be silently filled with zeros; `value: null` and `sample_count: 0` indicate missing measurements for a returned bucket. A data gap may also be represented by omitting that bucket, but the response MUST communicate partial coverage. Pagination is applied after sorting by the selected `sort_by` field and `sort_order`, using the other period boundary as a stable tie-breaker.

**Example request**

```http
GET /v1/environment/trends?latitude=43.0096&longitude=-81.2737&metric=pm2_5_ug_m3&start_at=2026-11-09T16%3A00%3A00Z&end_at=2026-11-09T18%3A00%3A00Z
```

**Example 200 response — synthetic**

```json
{
  "location": {"latitude": 43.0096, "longitude": -81.2737},
  "metric": "pm2_5_ug_m3",
  "resolution": "hourly",
  "items": [
    {"period_start": "2026-11-09T16:00:00Z", "period_end": "2026-11-09T17:00:00Z", "value": 9.5, "unit": "ug_m3", "sample_count": 3},
    {"period_start": "2026-11-09T17:00:00Z", "period_end": "2026-11-09T18:00:00Z", "value": null, "unit": "ug_m3", "sample_count": 0}
  ],
  "pagination": {"limit": 25, "offset": 0, "total": 2, "has_more": false},
  "coverage_status": "partial",
  "dataset_updated_at": "2026-11-10T17:00:00Z",
  "as_of": "2026-11-10T18:00:00Z",
  "source": "example_environment_history",
  "is_live": false,
  "is_stale": false,
  "data_status": "historical"
}
```

Historical records are labeled `historical`, even if a recent source produces them. The service MUST not label historical trends as current measurements. If a provider outage interrupts collection, return available buckets with `coverage_status: partial` and preserve their original periods. `is_stale` here is `false` because historic observations are **not evaluated against live-current freshness thresholds**; coverage is communicated separately.

### 3.5 Travel conditions and precautions — FR4, FR5, QR1–QR3

`GET /v1/weather/travel-guidance`

| Query field | Required | Type / validation | Default |
|---|---|---|---|
| `latitude`, `longitude` | Yes | Valid WGS 84 coordinates | — |
| `travel_at` | No | Timezone-aware; from request time through at most **7 days** ahead | Server's current UTC time |

**Example request**

```http
GET /v1/weather/travel-guidance?latitude=43.0096&longitude=-81.2737
```

**Example 200 response — synthetic**

```json
{
  "location": {"latitude": 43.0096, "longitude": -81.2737},
  "travel_at": "2026-11-10T18:00:00Z",
  "as_of": "2026-11-10T18:00:00Z",
  "assessment_status": "assessed",
  "risk_level": "moderate",
  "hazards": [
    {"hazard_type": "reduced_visibility", "severity": "moderate", "description": "Visibility may be reduced in fog."}
  ],
  "recommendations": ["Allow additional travel time and monitor official weather advisories."],
  "rule_set_version": "weather_travel_rules_v1",
  "inputs": [
    {"dataset": "weather_observation", "reference_at": "2026-11-10T17:55:00Z", "is_stale": false}
  ]
}
```

- `risk_level` is `low`, `moderate`, `high`, or `unknown`. It describes **potential weather-related travel difficulty**, not a guarantee of road or personal safety. `assessment_status` is `assessed` or `insufficient_data`.
- `hazard_type` is one of `reduced_visibility`, `heavy_precipitation`, `strong_wind`, `icy_conditions`, `extreme_heat`, `extreme_cold`, `poor_air_quality`; `severity` is `moderate` or `high`. Derived output MUST be traceable to reported/forecast conditions and the versioned rule set. The precise weather thresholds used by those rules MUST be specified in a separate, testable **travel-rule specification** before implementation; PR1 doesn't supply them.
- If essential data are unavailable or too stale for a credible assessment, return `assessment_status: insufficient_data`, `risk_level: unknown`, and a transparent recommendation explaining the uncertainty. DO NOT report `low` merely because no hazards could be detected from missing inputs. A stale weather observation may still be returned in the ordinary weather endpoint under QR3.
- Guidance MAY reference authoritative provider alerts when integrated, but it MUST NOT invent an official alert, imply that an official warning was checked when none was, or claim an unverified route is safe.

## 4. Errors, validation, and read behavior

All error bodies use the common JSON envelope: `error.code` (uppercase snake case), `error.message`, `error.request_id`, `error.details[]`, `error.retryable`, and `error.source` (`service`, `upstream`, `gateway`); the ID MUST match the `X-Request-ID` response header. HTTP mappings are:

| HTTP | Code / meaning | Applicable operations |
|---|---|---|
| `400` | `BAD_REQUEST`: malformed request structure | All |
| `404` | `NOT_FOUND`: location outside supported geographic coverage | Weather, forecast, air quality, trends, guidance when no coverage exists |
| `422` | `VALIDATION_FAILED`: invalid coordinates, dates, enums, time ordering, time horizon or numeric limits | All with parameters |
| `429` | `RATE_LIMITED`: request rate limit exceeded | All |
| `500` | `INTERNAL_ERROR`: unexpected internal failure | All |
| `502` | `UPSTREAM_FAILURE`: invalid/unusable provider response with no acceptable fallback | All provider-backed reads |
| `503` | `SERVICE_UNAVAILABLE`: essential information unavailable without an acceptable fallback | All provider-backed reads; `/ready` |
| `504` | `UPSTREAM_TIMEOUT`: provider timeout without acceptable fallback | All provider-backed reads |

**Example error:**

```json
{
  "error": {
    "code": "VALIDATION_FAILED",
    "message": "End time must be after start time.",
    "request_id": "req-s5-001",
    "details": [{"field": "end_at", "reason": "must_be_after_start_at"}],
    "retryable": false,
    "source": "service"
  }
}
```

- A covered location with an empty historical/forecast interval returns `200` and `items: []`; a wholly unsupported location returns `404`. A source outage with usable cached data returns `200` and degraded metadata, rather than a misleading unqualified `503`.
- Unknown query fields and ambiguous or timezone-naïve timestamps are rejected as invalid. No API accepts non-finite coordinates or arbitrary provider-defined `metric` names. Range restrictions are validated relative to the trusted request-receipt time.
- GETs have no intentional persistent side effects, no JSON request bodies, and no idempotency-key requirement. Stable offset pagination uses the common envelope (`items`, `pagination.limit`, `pagination.offset`, `pagination.total`, `pagination.has_more`).

## 5. Service-owned data and external dependencies

| Entity | Ownership and retention/persistence rule |
|---|---|
| `WeatherObservation` | S5-owned normalized current/cached weather measurements, location/coverage, measurement time, source and update timestamps. Retain latest usable observation for provider-outage fallback. |
| `ForecastRun` / `ForecastInterval` | S5-owned cached forecast runs with `issued_at` and interval validity; keep the most recent usable run when refresh fails. Forecasts are predictions, **not** observations. |
| `AirQualityObservation` | S5-owned normalized AQHI (if available) and pollutant concentrations with declared units, measurement time and source metadata. May have a different provider and freshness state from weather. |
| `EnvironmentalTrendBucket` | S5-owned historical aggregated measurements, time bounds, count, metric and coverage metadata, kept as allowed by provider terms and retention policy. |
| `TravelGuidanceResult` | Derived from S5 measurements/forecasts and a versioned rule set. May be computed on request or cached with input provenance; not an independent live observation. |

**Ownership rule:** S5's persistence/caches are owned by S5, not the Service Registry or another domain microservice (ASR6). The contract recommends a service-owned durable normalized store plus an optional short-lived cache, but **does not prescribe PostgreSQL or a specific vendor**. Retention periods and provider licensing must be resolved during the T008 data-ownership/dependency deliverable.

**Provider register — unconfirmed:** A weather observation/forecast source, an air-quality/pollution source, and (optionally) authoritative travel-related alert feeds are dependencies to investigate. Do **not** interpret synthetic source identifiers in the examples as contracted providers. The later dependency register MUST record actual provider name, data coverage, access protocol, authentication, update frequency, rate limits, license/retention conditions, outage behavior, and confirmation status.

**Readiness:** `GET /health` checks process liveness without expensive upstream calls. `GET /ready` returns `200` when S5 can provide weather information through its live feed **or an honest cached fallback** for its supported area; a live-provider outage alone MUST NOT make it unready when cached weather remains available. If essential weather information cannot be provided at all, readiness returns `503` in the standard error envelope. Lack of air-quality data does not make the entire service unready when independent weather operations remain usable, but the affected air-quality operation MUST report unavailability honestly. Monitoring paths must be appropriately network-restricted.

## 6. Acceptance verification

| Requirement | Minimum verification |
|---|---|
| **FR1** | Query current conditions for valid coordinates; verify humidity, units, timestamps and incomplete-measurement handling. |
| **FR2** | Query future hourly/daily intervals, validate bounds within seven days, and verify `issued_at` differs from forecast validity intervals. |
| **FR3** | Return AQHI when available, named pollutant readings with fixed units, and a nullable index when a provider cannot supply AQHI. |
| **FR4–FR5** | Given specified input weather/forecast conditions and an approved travel-rule table, verify derived hazards, risk classification and corresponding precautions; insufficient data must produce `unknown`, not a false low risk. |
| **QR1 — 2,000 ms** | Each valid S5 read returns available information in **2,000 ms** or less under the agreed test setup, including representative cached-fallback requests. No additional numeric concurrency guarantee is inferred. |
| **QR2 — time metadata** | Verify all relevant observations/updates preserve original measurement/issue timestamps; UTC `Z` on outputs. |
| **QR3 — outage fallback** | Simulate external-weather-provider failure and test fresh/older cached data: return latest weather with original timestamps, `is_live: false`, `is_stale` according to age and `data_status: cached`. |
| **Common standards** | Validate OpenAPI schema/examples, error envelope and `X-Request-ID`, public-read policy, query validation, sorting/pagination and `/health`/`/ready` semantics. |

## 7. Contract acceptance checklist

- [ ] FR1–FR5, QR1–QR3 each map to testable operations, schemas, and acceptance cases.
- [ ] Current observations, forecasts, trends and derived guidance are clearly distinguished.
- [ ] Measurements have explicit units, availability semantics and independently accurate timestamps.
- [ ] Data-outage fallback preserves the **latest known weather data** without falsely describing stale information as live.
- [ ] Travel guidance cannot claim safe conditions from missing evidence or invent official alerts.
- [ ] S5 data ownership and candidate external providers are documented separately from shared infrastructure.
- [ ] Endpoints obey common URL, JSON, validation, error, request-ID and operational conventions.
- [ ] Performance target of **2,000 ms** is verified without inventing extra PR1 concurrency requirements.

## 8. Change control

This document is governed by `docs/api/API-Standards.md`. Implementations, tests and clients **MUST** conform to the approved contract. Breaking changes to paths, request fields, enums, freshness definitions, units or response semantics require an approved compatibility migration or new API version. Provider and storage implementations may change without changing the public API contract if their observable behavior remains conformant.
