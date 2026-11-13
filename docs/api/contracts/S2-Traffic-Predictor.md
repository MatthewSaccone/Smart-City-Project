# S2 — Traffic Predictor: API Contract

| Property | Value |
|---|---|
| Document ID | `T008-S2-API` |
| Version | `1.1` (JWT access migration; review before deployment) |
| Status | Adopted API design; prediction algorithm, historical-data source, and validation dataset are integration dependencies |
| Logical service ID | `traffic-predictor` (S2) |
| API version | `/v1` |
| Governing conventions | `docs/api/API-Standards.md` |
| Machine-readable specification | `shared/api-contracts/s2-traffic-predictor.openapi.yaml` |

## 1. Purpose and scope

S2 analyses **historical road-congestion data** to identify recurring patterns and forecast congestion at future times. It supports a point query, high-congestion time windows for an area, and predictions for road segments within a geographic radius. **A prediction is not a live observation**; model-generated information must be labeled distinctly from current conditions reported by S1.

This contract defines S2's client-facing HTTP interface, data/validation semantics, and quality targets. Model training and ingesting historical observations are **internal workflows**, not open public data-submission endpoints. Public results contain aggregated patterns or forecasts, never raw individual user traces.

### Requirements traceability

| PR1 requirement | Required capability | Contract coverage |
|---|---|---|
| S2 FR1 | Identify recurring congestion by location, time of day, and day of week | `GET /v1/traffic/history/patterns` using a named local timezone and historical period |
| S2 FR2 | Predict congestion for a user-defined location and future time | `POST /v1/traffic/predictions` |
| S2 FR3 | Identify periods when a user-defined area is expected to have high congestion | `GET /v1/traffic/predictions/high-congestion-periods` |
| S2 FR4 | Predict congestion at locations within a user-defined radius | `GET /v1/traffic/predictions/areas` |
| S2 QR1 | Return predictions within **3,000 ms** of receiving a valid request | S2 prediction endpoint service-response target |
| S2 QR2 | Preserve the response-time requirement under **25 concurrent prediction requests** | Concurrent-load acceptance test |
| S2 QR3 | Achieve a predefined minimum accuracy on a historical test dataset | Versioned evaluation policy defined in §5 |

> **Origin of requirements and design choices:** PR1 defines FR1–FR4, QR1–QR3, but does not prescribe exact paths, model algorithm, data provider, radius, forecast horizon, or numerical accuracy cutoff. The choices below are **T008 contract decisions**, not claims that PR1 supplied them or that the endpoints already exist.

## 2. Shared rules and prediction semantics

- Paths start with `/v1/`; JSON keys and query parameters use `snake_case`. All successful JSON timestamp strings representing instants are UTC ISO 8601 with `Z`; timezone-aware input offsets are accepted and normalized to UTC.
- Coordinates use WGS 84 decimal degrees (latitude `[-90,90]`, longitude `[-180,180]`); search radius uses metres, with `0 < radius_m <= 10000`. Non-finite numbers and undeclared body properties are invalid.
- **Forecast times must be in the future, at most 7 days after request receipt.** A forecast time exactly at request time or beyond 7 days is invalid (`422`). Forecast endpoints use one explicitly requested instant rather than implicitly predicting current conditions.
- A high-congestion-period search spans at most **48 hours** inside the 7-day forecast horizon; predictions are evaluated in **30-minute UTC-aligned intervals**. Consecutive high intervals may be merged into contiguous periods.
- Recurring-pattern queries use an **IANA timezone** (such as `America/Toronto`) to interpret the requested day of week and hour of day. This prevents confusion between UTC weekdays and local commuting patterns, including daylight-saving changes. The response includes that timezone. Invalid timezone names return `422`.
- The common categorical values are `low`, `moderate`, `high`, and `unknown`. `unknown` is used only when the response explicitly communicates insufficient evidence; it must not be presented as a valid confident forecast. Numeric scores are **normalized, unitless values in `[0,1]`**, not probabilities of being correct.
- All prediction responses report the **target `forecast_time`**, **`generated_at`** (when the prediction was made), **`model_version`**, and **`training_data_end_at`** (latest historical observation included in the model). `model_valid_until` states the model's approved validity deadline. A model past that deadline is **not eligible for new predictions**; return `503` if no approved model is available. `is_stale` is evaluated against this deadline; successful new predictions are never silently generated from an expired model.
- Predictions have `is_live: false`, `data_status: "predicted"`, `source: "historical_traffic_model"`. Historical-pattern results use `data_status: "historical"`, `source: "historical_traffic_dataset"`. For historical patterns, `is_stale` indicates whether the dataset has passed its declared `dataset_refresh_due_at`; an older dataset can still be served, visibly marked as stale. A dataset refresh cadence will be set with the selected provider.
- All four S2 analytics endpoints require a **verified bearer access token** from the same trusted identity provider used by S3. A POST computes a prediction but creates no persistent client resource. All errors use the adopted envelope and `X-Request-ID`; no endpoint accepts a user identity field as authentication.
- A geographic search outside documented road-network/model coverage returns `404` with `NOT_FOUND` (or the narrower `COVERAGE_NOT_FOUND`). If geography is supported but no trustworthy prediction can be computed due to missing history/model state, return `503` with `SERVICE_UNAVAILABLE` or `INSUFFICIENT_HISTORICAL_DATA`. A successfully evaluated query with zero matching segments/periods returns `200` with `items: []`.

## 3. Endpoint inventory

| Operation ID | Method and path | Purpose | Auth | Success |
|---|---|---|---|---|
| `listHistoricalPatterns` | `GET /v1/traffic/history/patterns` | Identify recurring congestion by local day/hour | Verified bearer token | `200` |
| `predictPointCongestion` | `POST /v1/traffic/predictions` | Forecast congestion at one location and instant | Verified bearer token | `200` |
| `listHighCongestionPeriods` | `GET /v1/traffic/predictions/high-congestion-periods` | Find future high-congestion periods within an area | Verified bearer token | `200` |
| `listAreaPredictions` | `GET /v1/traffic/predictions/areas` | Forecast road-segment congestion within a radius | Verified bearer token | `200` |
| `getHealth` | `GET /health` | Liveness probe | Monitoring access | `200` |
| `getReadiness` | `GET /ready` | Validated model and historical-data readiness | Monitoring access | `200` / `503` |

### 3.1 Historical recurring patterns — S2 FR1

`GET /v1/traffic/history/patterns`

| Query field | Required | Type / constraints | Default |
|---|---|---|---|
| `latitude`, `longitude` | Yes | Finite WGS 84 coordinates | — |
| `radius_m` | No | Number `(0, 10000]`, metres | `500` |
| `timezone` | Yes | Valid IANA timezone name | — |
| `day_of_week` | No | `monday` through `sunday` | All days |
| `local_hour` | No | Integer `0–23`; interpreted in `timezone` | All hours |
| `start_at`, `end_at` | No | Both present or both omitted; UTC/offset instants; `start_at < end_at <= now`; max 365 days | Last 90 days, ending at request receipt |
| `sort_by` | No | `day_of_week`, `local_hour`, `average_congestion_score` | `day_of_week` |
| `sort_order` | No | `asc`, `desc` | `asc` |
| `limit`, `offset` | No | Limit `1–100`, default `25`; offset `>=0`, default `0` | `25`, `0` |

The service groups historical observations into recurring **local-weekday/hour buckets** for the chosen geographic region and history period. Each result includes its sample count, a normalized mean congestion score (nullable when no evidence), and a congestion category. Hours repeated during daylight-saving transitions are included as distinct real observations in the same local-hour bucket; nonexistent local hours contribute no observations. Sorts use `day_of_week` then `local_hour` as a deterministic tie-breaker.

**Example:**

```http
GET /v1/traffic/history/patterns?latitude=43.0096&longitude=-81.2737&radius_m=500&timezone=America%2FToronto&day_of_week=monday&local_hour=8&limit=25&offset=0
```

**200 response (illustrative values):**

```json
{
  "items": [
    {
      "day_of_week": "monday",
      "local_hour": 8,
      "average_congestion_score": 0.78,
      "congestion_level": "high",
      "observation_count": 124
    }
  ],
  "pagination": { "limit": 25, "offset": 0, "total": 1, "has_more": false },
  "timezone": "America/Toronto",
  "history_start_at": "2026-08-12T18:00:00Z",
  "history_end_at": "2026-11-10T18:00:00Z",
  "dataset_updated_at": "2026-11-10T16:00:00Z",
  "dataset_refresh_due_at": "2026-11-17T16:00:00Z",
  "as_of": "2026-11-10T18:00:00Z",
  "source": "historical_traffic_dataset",
  "is_live": false,
  "is_stale": false,
  "data_status": "historical"
}
```

Historical-pattern queries have **no request body**. `404` means no coverage for the requested geographic location; `200` and `items: []` means the query was valid and the result set is empty. Missing data for an individual bucket is represented as `unknown` with a `null` score rather than a fabricated zero.

### 3.2 Predict congestion at a point — S2 FR2

`POST /v1/traffic/predictions`

**Required JSON body:**

```json
{
  "location": { "latitude": 43.0096, "longitude": -81.2737 },
  "forecast_time": "2026-11-12T17:00:00Z"
}
```

| Field | Required | Type / constraints | Meaning |
|---|---|---|---|
| `location` | Yes | Coordinate object, exactly `latitude` and `longitude` | Location to forecast |
| `forecast_time` | Yes | Timezone-aware instant in `(request_received_at, request_received_at + 7 days]` | Future prediction target |

**200 response (illustrative values):**

```json
{
  "prediction": {
    "location": { "latitude": 43.0096, "longitude": -81.2737 },
    "forecast_time": "2026-11-12T17:00:00Z",
    "congestion_level": "high",
    "estimated_congestion_score": 0.82
  },
  "generated_at": "2026-11-10T18:00:01Z",
  "model_version": "traffic-model-1.0",
  "training_data_end_at": "2026-11-09T23:59:59Z",
  "model_valid_until": "2026-12-10T00:00:00Z",
  "as_of": "2026-11-10T18:00:01Z",
  "source": "historical_traffic_model",
  "is_live": false,
  "is_stale": false,
  "data_status": "predicted"
}
```

The forecast represents the **future target instant**, not the latest real-world traffic observation. Its score is an ordinal prediction input/estimate, not a calibrated likelihood unless a later approved model contract explicitly establishes calibration. Since this POST only computes, it returns `200`, not `201`, and is safe to repeat without duplicate resource-creation effects.

### 3.3 Identify future high-congestion periods — S2 FR3

`GET /v1/traffic/predictions/high-congestion-periods`

Required query parameters: `latitude`, `longitude`, `radius_m`, `start_at`, `end_at`. Coordinates and radius follow §2. Both instants must be future, `start_at < end_at`, `end_at <= request_received_at + 7 days`, and the window length must not exceed 48 hours. Optional `limit` (default 25, maximum 100), `offset` (default 0), `sort_by` (`start_at` or `end_at`; default `start_at`), and `sort_order` (`asc` or `desc`; default `asc`) use the platform conventions.

The service evaluates consecutive **30-minute UTC-aligned forecast slots** that overlap the requested window. It returns merged contiguous intervals only when the approved area-level prediction classification is `high`. Returned period boundaries are clipped to the requested time window. An empty `items` collection means the service successfully evaluated the window and found **no high intervals**, not that its prediction dataset was unavailable.

**Example request:**

```http
GET /v1/traffic/predictions/high-congestion-periods?latitude=43.0096&longitude=-81.2737&radius_m=1500&start_at=2026-11-12T12%3A00%3A00Z&end_at=2026-11-13T00%3A00%3A00Z&limit=25&offset=0
```

**200 response (illustrative values):**

```json
{
  "items": [
    {
      "start_at": "2026-11-12T16:00:00Z",
      "end_at": "2026-11-12T18:00:00Z",
      "congestion_level": "high"
    }
  ],
  "pagination": { "limit": 25, "offset": 0, "total": 1, "has_more": false },
  "generated_at": "2026-11-10T18:00:01Z",
  "model_version": "traffic-model-1.0",
  "training_data_end_at": "2026-11-09T23:59:59Z",
  "model_valid_until": "2026-12-10T00:00:00Z",
  "as_of": "2026-11-10T18:00:01Z",
  "source": "historical_traffic_model",
  "is_live": false,
  "is_stale": false,
  "data_status": "predicted"
}
```

The exact algorithm for aggregating segment forecasts into an area's `high` classification is a **model specification dependency** and must be defined before acceptance testing.

### 3.4 Forecast locations in a radius — S2 FR4

`GET /v1/traffic/predictions/areas`

Required query parameters: `latitude`, `longitude`, `radius_m`, and `forecast_time`. Optional `limit` (1–100, default 25), `offset` (nonnegative, default 0), `sort_by` (`distance_from_center_m`, `segment_id`, or `congestion_level`; default `distance_from_center_m`), and `sort_order` (`asc`, `desc`; default `asc`). A stable `segment_id` tie-breaker is mandatory. `congestion_level` sorting orders `low < moderate < high < unknown`, not alphabetical order.

**Example:**

```http
GET /v1/traffic/predictions/areas?latitude=43.0096&longitude=-81.2737&radius_m=1500&forecast_time=2026-11-12T17%3A00%3A00Z&limit=25&offset=0
```

**200 response (illustrative values):**

```json
{
  "items": [
    {
      "segment_id": "segment-123",
      "center": { "latitude": 43.0101, "longitude": -81.2729 },
      "distance_from_center_m": 80,
      "forecast_time": "2026-11-12T17:00:00Z",
      "congestion_level": "high",
      "estimated_congestion_score": 0.82
    }
  ],
  "pagination": { "limit": 25, "offset": 0, "total": 1, "has_more": false },
  "generated_at": "2026-11-10T18:00:01Z",
  "model_version": "traffic-model-1.0",
  "training_data_end_at": "2026-11-09T23:59:59Z",
  "model_valid_until": "2026-12-10T00:00:00Z",
  "as_of": "2026-11-10T18:00:01Z",
  "source": "historical_traffic_model",
  "is_live": false,
  "is_stale": false,
  "data_status": "predicted"
}
```

`distance_from_center_m` refers to the search center and road segment's representative point, not driving distance. Pagination is applied **after** geographic filtering and stable sorting.

## 4. Validation, errors, and retries

- `401 AUTHENTICATION_REQUIRED`: missing, malformed, expired, wrongly signed, wrong-issuer or wrong-audience access token. Includes `WWW-Authenticate: Bearer`. Authentication runs before analytics dependencies; a registry key or caller-supplied identity header is not sufficient.
- `503 SERVICE_UNAVAILABLE`: trusted token verification is unconfigured or signing keys cannot be retrieved when needed. No anonymous fallback is provided. An unexpired cached JWKS may still validate tokens; the five-minute JWKS cache is not extended during an outage.
- Required fields, strict types, finite numeric coordinates, permitted radius and timestamp ranges, and all cross-field relationships are validated before expensive predictions.
- `422 VALIDATION_FAILED`: unsupported timezone, malformed field value, forecast in the past, forecast beyond 7 days, history interval in the future, history range longer than 365 days, high-period request window longer than 48 hours, or any contradictory bounds. Malformed request syntax receives `400 BAD_REQUEST`.
- `404 NOT_FOUND` / `COVERAGE_NOT_FOUND`: requested location/area is outside supported geographic/model coverage.
- `503 SERVICE_UNAVAILABLE` / `INSUFFICIENT_HISTORICAL_DATA`: the location is in coverage, but no validated model or sufficient historical evidence exists to produce an acceptable prediction. An expired model cannot silently generate a normal prediction.
- `502 UPSTREAM_FAILURE`, `504 UPSTREAM_TIMEOUT`: unusable provider result or upstream timeout; `429 RATE_LIMITED` when applicable; `500 INTERNAL_ERROR` for unexpected service failures.
- Every error follows `API-Standards.md` §7: `error.code`, `message`, `request_id`, `details`, `retryable`, and `source`. An `X-Request-ID` response header accompanies both successful and error responses.
- All four analytics operations have no intended state mutation, including the POST computation. Retrying the same request has no duplicate-write effect, although **time-dependent forecasts may change** when a model or dataset changes. Retries must have bounded timeouts/backoff.

## 5. Prediction quality, performance, and verification

### 5.1 Service-level quality targets

1. **S2 QR1:** The service must produce the complete response for a valid prediction request within **3,000 ms** from receipt at the S2 HTTP ingress to completion of its response, excluding client-to-gateway network transit. This applies to all S2 prediction endpoints. Timestamps are not a substitute for latency instrumentation.
2. **S2 QR2:** Maintain the same **3,000 ms** target under **25 concurrently active prediction requests** in the defined representative test workload. The load-test report must state request mix, geographic coverage, cold/warm model state, hardware, duration, and observed response latency. This target is not equivalent to 25 requests per second.
3. **S2 QR3:** A model is eligible to serve forecasts only after it meets **macro-averaged F1 score >= 0.70** for `low`, `moderate`, and `high` congestion categories on an independently held-out, chronologically later historical dataset. Report per-class precision/recall, confusion matrix, coverage, and model/dataset version. Avoid using data from the test interval in training or feature construction. This numeric cutoff and metric are **T008 design choices** because PR1 states the need for a predefined minimum accuracy without specifying it. If class balance/data quality makes the criterion unsuitable, revise the contract and test plan explicitly before model acceptance; do not silently replace it.

### 5.2 Model and data lifecycle

- Every served prediction must be reproducibly associated with `model_version` and a known `training_data_end_at`.
- The validation/deployment process assigns a UTC `model_valid_until` based on the approved data/model lifecycle; **no numerical retraining frequency is imposed by PR1**. When that deadline passes, a new prediction must use another validated model or return `503`. The model's prediction metadata is not a claim of live traffic freshness.
- Historical datasets expose `dataset_updated_at` and `dataset_refresh_due_at`. If the latter has passed, patterns may still be returned as historical with `is_stale: true`, but cannot be mislabeled current/live. The actual refresh period is a dependency to define for the chosen source.
- If the model supports uncertainty/confidence values in the future, they must be added with definitions and calibration evidence, not fabricated or silently identified as accuracy.

## 6. Service ownership, dependencies, and readiness

**S2-owned data (initial design):** historical traffic snapshots/aggregates acquired under an approved feed, derived recurring weekday/hour patterns, prediction outputs/caches, training datasets/feature definitions, versioned model artifacts, and validation metrics. Historical observations remain time-stamped and traceable to their source.

**S2-consumed data:** one or more authorized historical road-traffic datasets and corresponding road-segment/geographic identifiers. A documented S1-to-S2 historical export/API may be used **if explicitly designed**, but S2 does not read or write S1's private database directly. External provider, access/authentication, cadence, rate limits, licensing, storage, and fallback details remain part of the T008 external dependency inventory; none is claimed as confirmed.

**Storage boundary:** S2 owns its persistence and model storage. The Service Registry's PostgreSQL instance is not the default shared domain database. Storage technology and historical-data retention must be documented in the S2 ownership matrix before deployment.

**Operational endpoints:**

- `GET /health` returns `200` and `{ "service": "traffic-predictor", "status": "healthy" }` when the process responds, without performing expensive model inference.
- `GET /ready` returns `200` and `{ "service": "traffic-predictor", "status": "ready" }` when sufficient historical data and an approved, unexpired model are loaded/accessible for supported prediction workloads. If neither a validated active model nor an explicitly approved fallback can serve required predictions, it returns `503` using the shared error envelope.
- Provider outages do not automatically make S2 unready while the service can legitimately serve predictions from retained validated datasets and model artifacts. No expired model is silently treated as a valid fallback.

## 7. Contract acceptance checklist

- [ ] OpenAPI 3.1 document parses and every `$ref`, response schema, and example is valid.
- [ ] Historical queries correctly group by IANA-local weekday/hour, including DST changes and partial/missing observations.
- [ ] Historical and predicted data are labeled distinctly, with original timestamps and provenance.
- [ ] Forecast timestamps obey the future, 7-day, and 48-hour (period search) limits; input offsets normalize to UTC.
- [ ] Geospatial constraints, strict field validation, pagination, sorting, and unique tie-breaks are tested.
- [ ] No model results are presented when approved model validity/coverage requirements are unmet.
- [ ] All errors use the common envelope with `X-Request-ID` and documented retry policy.
- [ ] QR1 (3,000 ms), QR2 (25 concurrent requests), and QR3 (macro-F1 >= 0.70 on held-out historical data) have repeatable test evidence.
- [ ] Dataset provider, ingestion contract, update cadence, model training/evaluation procedure, coverage limits, ownership, retention, and storage decisions are documented in T008's data/dependency deliverables.

## 8. Contract change policy

The approved OpenAPI document and this Markdown contract define the target implementation. They must agree on paths, types, defaults, examples, status codes, and quality semantics. Incompatible changes must follow `API-Standards.md` versioning/change-control rules before deployment.

### 8.1 Version 1.1 access migration

This feature branch implements the requested S2 authentication integration with the
existing S3/Keycloak identity provider. Requiring tokens changes the previous anonymous
access policy, so this migration must be reviewed with the affected clients before
deployment. Paths and request/response models remain `/v1`.

1. Configure the same `JWT_ISSUER`, `JWT_AUDIENCE`, and trusted `JWT_JWKS_URL` as S3.
   Only RS256 signatures are accepted; `exp`, `iat`, `iss`, `aud`, and a nonempty `sub`
   are required. The configured clock tolerance is 0–60 seconds (default 30).
2. Frontend/application clients must obtain identity-provider access tokens; internal
   callers should use a confidential service-account client with client credentials.
   Configure the Keycloak audience mapper so issued tokens include `smart-city-api`.
   Any valid identity for the configured audience may access these read-only analytics;
   no new moderator role or ownership rule is introduced.
3. Consumers and benchmark clients must send `Authorization: Bearer <access_token>`
   on every analytics request and refresh expired tokens. Update clients before
   rolling out the protected service. There is no local authentication bypass.
4. `/health` and `/ready` remain accessible operational probes. Readiness retains its
   existing dataset/model semantics; it does not establish identity-provider availability.
5. S1, S3, S4, the registry key, databases, prediction logic and model acceptance gates
   are unchanged by this migration.
