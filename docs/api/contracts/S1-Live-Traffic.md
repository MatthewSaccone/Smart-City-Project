# S1 — Live Traffic Analytics: API Contract

| Property | Value |
|---|---|
| Document ID | `T008-S1-API` |
| Version | `1.0` (initial contract) |
| Status | Adopted design baseline; external-provider selection and internal event format remain integration dependencies |
| Logical service ID | `live-traffic` (S1) |
| API version | `/v1` |
| Governing conventions | `docs/api/API-Standards.md` |
| Machine-readable specification | `shared/api-contracts/s1-live-traffic.openapi.yaml` |

## 1. Purpose and scope

S1 reports **current road congestion** and **estimated route delays** using recent, time-stamped traffic/location observations. It supports queries between two user-defined points and searches within a user-defined radius. Results refresh when newly received traffic data is processed.

This contract specifies the **client-facing HTTP interface**. An upstream ingestion component and message broker provide observations internally; the ingestion feed is not an open, public HTTP endpoint. No public response exposes individual users' raw location histories.

### Requirements traceability

| PR1 requirement | Required capability | Contract coverage |
|---|---|---|
| S1 FR1 | Analyze live time and location data from active users to determine congestion | Internal observation ingestion, freshness semantics, and congestion outputs |
| S1 FR2 | Report congestion between two user-defined points | `POST /v1/traffic/routes/congestion` |
| S1 FR2.1 | Estimate travel delays along the specified route | `estimated_delay_s`, `estimated_duration_s`, `baseline_duration_s` in route response |
| S1 FR3 | Report congestion within a user-defined radius | `GET /v1/traffic/areas/congestion` |
| S1 FR4 | Refresh congestion/delay estimates as new observations arrive | Ingestion/subscription contract and `service_updated_at` / `as_of` in responses |
| S1 QR1 | Calculate congestion within 1,000 ms after all required input is received | Implementation performance requirement, measured separately from upstream retrieval |
| S1 QR2 | Retrieve available live location/timestamp data within 500 ms after a valid request | Cache/ingestion and performance requirement |
| S1 QR3 | Return congestion and delays within 1,000 ms after calculation completes | Implementation performance requirement |
| S1 QR4 | Use location data **received within the previous 60 s** for live calculations | `received_at` freshness, with explicit stale-result fallback |
| S1 QR5 | Preserve response-time targets at **50 concurrent requests** | Load testing requirement |
| ASR5 | Under peak scaled load, 200 requests/s, 95% responses within 2 s, scale-out start within 30 s and instances serve within 60 s | Deployment and performance verification; not a new request field |
| ASR7 | Normally provide data at most 60 s old; return cached last-known data with stale indication when older; use a polling/ingestion component and pub/sub updates | Freshness metadata and internal event dependency |

> **Scope boundary:** The workbook does not designate a traffic data provider, road-network/routing provider, congestion algorithm, or precise route-selection policy. Their integration and algorithm choices must be specified before implementation. The HTTP shapes below are design decisions made for T008, not claims about currently implemented features.

## 2. Shared request/response rules

- All paths use `/v1/`, and JSON keys and query parameters use `snake_case`.
- Coordinates use decimal degrees (WGS 84); latitude in `[-90, 90]`, longitude in `[-180, 180]`.
- Distances and search radii are in **metres** (`_m`); durations and delays are in **seconds** (`_s`).
- All transmitted timestamps are ISO 8601 UTC with `Z`. Input timestamps containing an explicit offset are normalized to UTC; timezone-naïve values are invalid.
- Normal fresh responses use observations received in the preceding **60 seconds**, measured against the calculation's `as_of` timestamp. Exactly 60 seconds old is eligible; older observations may not be used for a new *live* congestion calculation.
- When a live update is unavailable, the **last successfully calculated** results may be returned as cached results with `is_stale: true`, `is_live: false`, `data_status: "cached"`, and the timestamps of the actual underlying observations/result. Cached results must not be mislabeled as a new live computation.
- Missing estimates use `null` when the schema allows it, never an invented zero. An empty geographic match returns `200` and `items: []`; inability to serve even a usable cached result returns an appropriate 5xx error.
- These query endpoints are read-only in effect, even when POST is used to submit a complex route calculation. They do not write user-facing resources.
- Queries are public read operations in this version. The gateway may apply rate limits. Private location records are never returned.
- Error bodies conform to the common error envelope; `X-Request-ID` is returned and propagated.

## 3. Endpoint inventory

| Operation ID | Method and path | Purpose | Auth | Typical success |
|---|---|---|---|---|
| `getRouteCongestion` | `POST /v1/traffic/routes/congestion` | Estimate congestion and delay between two points | Public read | `200` |
| `listAreaCongestion` | `GET /v1/traffic/areas/congestion` | List road-segment congestion in a radius | Public read | `200` |
| `getHealth` | `GET /health` | Liveness | Monitoring access | `200` |
| `getReadiness` | `GET /ready` | Readiness for essential S1 operations or supported cached fallback | Monitoring access | `200` / `503` |

### 3.1 Route congestion

`POST /v1/traffic/routes/congestion`

**Why POST?** This operation accepts a structured route request, performs a calculation, and does not create a permanent resource. Success uses `200 OK`, not `201 Created`.

**JSON request (required body):**

```json
{
  "origin": { "latitude": 43.0096, "longitude": -81.2737 },
  "destination": { "latitude": 43.015, "longitude": -81.2500 },
  "route_selection": "fastest"
}
```

| Field | Required | Type / constraints | Meaning |
|---|---|---|---|
| `origin` | Yes | `Coordinate` | Start point |
| `destination` | Yes | `Coordinate` | End point; must differ from origin |
| `route_selection` | No | `fastest` or `shortest`; default `fastest` | Route choice when more than one candidate exists |

**Design dependency:** A routing/network provider must determine the route associated with the two points and the selected preference. A later extension may accept an explicit route geometry or route identifier; this version does not imply that arbitrary routes are supported.

**200 response example (illustrative values):**

```json
{
  "route": {
    "origin": { "latitude": 43.0096, "longitude": -81.2737 },
    "destination": { "latitude": 43.015, "longitude": -81.25 },
    "route_selection": "fastest",
    "distance_m": 3200,
    "congestion_level": "moderate",
    "baseline_duration_s": 420,
    "estimated_duration_s": 570,
    "estimated_delay_s": 150
  },
  "as_of": "2026-11-10T18:00:00Z",
  "received_at": "2026-11-10T17:59:40Z",
  "service_updated_at": "2026-11-10T18:00:00Z",
  "source": "traffic_feed",
  "is_live": true,
  "is_stale": false,
  "data_status": "live"
}
```

- `congestion_level` is `low`, `moderate`, `high`, or `unknown`. The numeric thresholds for these categories must be defined in the congestion algorithm specification.
- `baseline_duration_s` represents estimated travel duration without the detected congestion; `estimated_duration_s` includes it; `estimated_delay_s` is their nonnegative difference. These can be `null` if reliable estimates are not available.
- `distance_m` is the computed route distance (not the straight-line distance between points).
- `as_of` is the reference time for the result; `received_at` is the latest observation receipt time used to calculate it; `service_updated_at` is the time the result was last calculated. Cached fallback responses retain the timestamps from the cached result.

**Errors:** `400 BAD_REQUEST`, `404 NOT_FOUND` (`ROUTE_NOT_FOUND`), `422 VALIDATION_FAILED`, `429 RATE_LIMITED`, `500 INTERNAL_ERROR`, `502 UPSTREAM_FAILURE`, `503 SERVICE_UNAVAILABLE`, `504 UPSTREAM_TIMEOUT`. The 404 case means no traversable route between the supplied points can be determined; missing live data alone is not a 404.

**Retry semantics:** This POST is computational and has no intended resource mutation, so repeating it is safe from duplicate-write effects. Network/provider failures should be retried only with a bounded policy.

### 3.2 Radius-based road congestion

`GET /v1/traffic/areas/congestion`

**Query parameters:**

| Field | Required | Type / constraints | Default |
|---|---|---|---|
| `latitude` | Yes | decimal, finite, `[-90,90]` | — |
| `longitude` | Yes | decimal, finite, `[-180,180]` | — |
| `radius_m` | Yes | decimal, finite, `0 < radius_m <= 10000` | — |
| `limit` | No | integer, 1–100 | 25 |
| `offset` | No | integer, >= 0 | 0 |
| `sort_by` | No | `distance_from_center_m` or `segment_id` | `distance_from_center_m` |
| `sort_order` | No | `asc` or `desc` | `asc` |

The 10,000 m maximum is a **T008 design limit**, not a value imposed by PR1. Results are sorted by the requested field, with `segment_id` as the stable tie-breaker, before pagination. Distances measure the search-centre-to-segment representative point, not road-network travel distance.

**Example request:**

```http
GET /v1/traffic/areas/congestion?latitude=43.0096&longitude=-81.2737&radius_m=1500&limit=25&offset=0&sort_by=distance_from_center_m&sort_order=asc
```

**200 response example (illustrative values):**

```json
{
  "items": [
    {
      "segment_id": "segment-123",
      "center": { "latitude": 43.0101, "longitude": -81.2729 },
      "distance_from_center_m": 80,
      "congestion_level": "high"
    }
  ],
  "pagination": { "limit": 25, "offset": 0, "total": 1, "has_more": false },
  "as_of": "2026-11-10T18:00:00Z",
  "received_at": "2026-11-10T17:59:40Z",
  "service_updated_at": "2026-11-10T18:00:00Z",
  "source": "traffic_feed",
  "is_live": true,
  "is_stale": false,
  "data_status": "live"
}
```

**Errors:** `400 BAD_REQUEST`, `422 VALIDATION_FAILED`, `429 RATE_LIMITED`, `500 INTERNAL_ERROR`, `502 UPSTREAM_FAILURE`, `503 SERVICE_UNAVAILABLE`, `504 UPSTREAM_TIMEOUT`. When no road segments match but the service can answer the query, return `200` with an empty `items` array.

## 4. Freshness and update contract

S1 consumes a continuously refreshed **internal observation feed**. A separate ingestion component obtains observations from a chosen traffic provider and publishes them to a broker. S1 uses newly received observations to recalculate or invalidate cached congestion and delay estimates; clients see updated values on subsequent requests. **This initial API does not promise server-push notifications to clients.**

Rules:

1. Incoming observations must have a real measurement timestamp (`observed_at`) when provided, plus a trustworthy platform receipt timestamp (`received_at`). These denote different events.
2. Live calculations may only use locations whose `received_at` is within 60 seconds of `as_of` (S1 QR4). Unknown or future receipt times must not be silently treated as recent.
3. If the newest usable result exceeds the 60-second age criterion, S1 may return its **last known calculated result** with `is_stale: true`, `is_live: false`, `data_status: "cached"` (ASR7). It must not use stale input to generate a purported new live estimate.
4. If no live or acceptable cached result exists, return the appropriate standard error envelope; if a valid geographic query contains no matching road segments, return a successful empty collection.
5. The API must preserve original data timestamps so frontend users can be shown *last updated at …*, including in fallback mode.
6. The ingestion provider, pub/sub message schema, topic name, update/poll frequency, retention, duplicate suppression, and threshold-to-congestion mapping are dependencies to settle in the **S1 internal data/event contract** and external-dependency inventory; they are not guessed here.

## 5. Security, ownership, and dependencies

- Public S1 HTTP endpoints are **read-only analytics** and do not require end-user login. API Gateway restrictions/rate limits may still apply.
- Only trusted platform services may access the internal ingestion/broker pathway. End-user identity and raw movement histories must not be exposed in the public analytics responses.
- **S1 owns:** recent aggregated road-segment observations, the active congestion snapshot, computed route/area congestion results, and its cache metadata. Raw ingest events may be short-lived; retention policy is an explicit data-ownership decision.
- **S1 consumes:** a traffic/location observation provider and a routing/road-network provider, potentially mediated by ingestion and a pub/sub broker. Provider identity, access method, API credentials, rate limits, and fallbacks are **not specified in PR1** and belong in the T008 external-dependency deliverable.
- S1 does **not** own the Service Registry's database; other microservices must use documented S1 APIs rather than write S1-owned data directly.

## 6. Operational endpoints and readiness

- `GET /health`: `200` with `{ "service": "live-traffic", "status": "healthy" }` when the process responds.
- `GET /ready`: `200` with `{ "service": "live-traffic", "status": "ready" }` if S1 can serve essential traffic analytics from a live snapshot **or a permitted cached fallback**. Return `503` with the shared error envelope when neither is usable.
- A temporary live-provider outage does not automatically make S1 unready if cached fallback is still valid under the documented policy.

## 7. Acceptance and verification checklist

- [ ] OpenAPI request/response schemas and examples validate; neither public endpoint is described as implemented until tests pass.
- [ ] Origin/destination coordinates and route preference validated; same-point requests rejected.
- [ ] Radius, limit, offset, and sort parameters validated, including non-finite values and stable page ordering.
- [ ] Live calculations exclude observations **received** more than 60 seconds before `as_of`.
- [ ] Stale cached results are visibly marked and retain original timestamps; no cached result is mislabeled live.
- [ ] Route travel delay fields remain internally consistent (`estimated_delay_s = max(0, estimated_duration_s - baseline_duration_s)` when both durations exist).
- [ ] The service updates derived results after new observations reach its subscription/cache.
- [ ] Unexpected errors, validation failures, upstream failures, and rate limits use the standard error envelope and `X-Request-ID`.
- [ ] Demonstrate S1 QR1–QR5 timing/concurrency targets and ASR5/ASR7 deployment/freshness requirements with repeatable tests.
- [ ] Finalize and document the route provider, observation provider, internal pub/sub event contract, congestion classification algorithm, and any retention/privacy policy before deployment.

## 8. Contract change policy

All implementations and client integrations must conform to this document and the matching OpenAPI definition. If the prose and OpenAPI disagree, reconcile both **before** implementation. Incompatible changes require a documented revision/versioning decision under `API-Standards.md`.
