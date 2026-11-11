# S4 — Parking Analytics: API Contract

| Property | Value |
|---|---|
| Document ID | `T008-S4-API` |
| Version | `1.0` (initial contract) |
| Status | Adopted T008 interface design; provider integrations and domain implementation are separate delivery work |
| Logical service ID | `parking-analytics` (S4) |
| API version | `/v1` |
| Governing conventions | `docs/api/API-Standards.md` |
| Machine-readable specification | `shared/api-contracts/s4-parking-analytics.openapi.yaml` |

## 1. Purpose and scope

S4 searches for parking facilities within a user-selected geographic area, describes **estimated** parking occupancy and congestion derived from time-location observations, and provides the rates and fees associated with a selected facility. The client must be able to distinguish an authoritative facility description, an occupancy **estimate**, and externally supplied pricing information. Occupancy is not guaranteed availability of a specific space.

This contract defines the client-facing HTTP interface and one **restricted, computation-only** batch-estimation operation. Continuous location ingestion, facility-catalog refresh, and price-feed synchronization are **internal integrations**, not open public submission endpoints. The service does not expose raw individual location traces through its public read endpoints.

### Requirements traceability (PR1, Service Requirements, S4)

| PR1 requirement | Required capability | Contract coverage |
|---|---|---|
| **FR1** | Identify available parking facilities within a user-defined geographic area | `GET /v1/parking/facilities`: bounding-box facility search, operational/availability status |
| **FR2** | Provide current parking occupancy or congestion based on users' time-location data | Occupancy snapshot returned by search/detail; restricted `POST /v1/parking/occupancy/estimate` for time-location estimation |
| **FR3** | Provide parking fees, rates, and occupancy for a selected facility | `GET /v1/parking/facilities/{facility_id}` with explicit pricing breakdown and occupancy |
| **QR1** | Return parking facility information within **2,000 ms** of a valid request | Search and detail retrieval acceptance tests |
| **QR2** | State when parking occupancy and fee information was last updated | Separate `observed_at`/`service_updated_at` for occupancy and `source_updated_at`/`service_updated_at` for pricing |
| **QR3** | Preserve defined response-time requirements with **25 concurrent prediction requests** | Concurrent-load test, including estimation requests and facility retrieval |
| **QR4** | Continue providing available parking information when live congestion data is unavailable | Explicit cached/unknown occupancy fallback; independent facility/pricing result fields |

> **Requirement boundary:** PR1 does not prescribe these particular paths, bounding-box limits, live-data freshness cutoffs, pricing tariff model, data provider, or storage technology. Those are contract-design decisions below. The 2,000 ms and 25-concurrent-request targets **do** come from PR1. A future implementation may differ from this contract and must be brought into conformance; no current implementation is asserted here.

## 2. General rules and parking semantics

- All public paths begin with `/v1/`; keys and query parameters use `snake_case`. Instants in success responses use ISO 8601/RFC 3339 **UTC with `Z`**. Timezone-aware request offsets are accepted and normalized to UTC.
- Coordinates use finite WGS 84 decimal degrees: latitude `[-90,90]`, longitude `[-180,180]`. Distances use metres. For search, the client supplies **all four** bounding coordinates, satisfying `min_lat <= max_lat` and `min_lon <= max_lon`. A box spanning more than **2.0° of latitude or 2.0° of longitude** returns `422`. Antimeridian-crossing boxes are not accepted in v1; split them into separate requests.
- Facilities have stable opaque `facility_id` identifiers, a name, center point, maximum capacity, optional geofence radius, and operational `facility_status`: `open`, `closed`, or `unknown`. A closed facility is not available even if its last occupancy estimate suggested free spaces.
- **Occupancy is estimated, not directly guaranteed.** `estimated_occupied_spaces`, `estimated_available_spaces`, and `occupancy_ratio` are nullable. Unknown counts are `null`, **never zero**. A valid estimate is clamped to capacity; if estimated arrivals exceed capacity, `capacity_exceeded` is `true` in the batch result. Congestion values are `unknown`, `low`, `moderate`, `high`, and `full`.
- The user-facing `availability_status` is `available`, `full`, `closed`, or `unknown`. It is `closed` when facility status is closed; `available` only when the facility is open **and** a current acceptable estimate indicates more than zero spaces; `full` only when open and an acceptable estimate indicates zero spaces; otherwise it is `unknown`. A cached/stale count must not produce the affirmative `available` status.
- **Freshness decisions (adopted for S4 v1):** An occupancy snapshot is fresh only when its latest `observed_at` timestamp is **at most 300 seconds old** relative to response processing time. Pricing is fresh only when its provider `source_updated_at` is **at most 24 hours old**. Both use inclusive cutoffs; a missing timestamp means freshness cannot be established and the data must be treated as stale/unverified. These are S4 design choices, **not PR1-stated numeric thresholds**.
- Occupancy and pricing have **independent freshness metadata**: `is_live`, `is_stale`, `source`, `data_status`, and their relevant update times. `data_status` is one of `live`, `cached`, `simulated`, or `unavailable` for facility-facing snapshots. `is_live` is `true` only for a verified live-updating source; it is not synonymous with `is_stale: false`.
- The `as_of` field means the time at which a response was evaluated; it does **not** replace `observed_at` or `source_updated_at`. If a data provider is unavailable, the service returns any usable known facility information and, where available, labeled cached prices/occupancy. Missing measurement and price fields must be `null` rather than fabricated. A stale estimate may be displayed but must not be presented as a live indication that parking is available.
- Prices are **informational tariffs, not a guaranteed final charge or a booking quote**. All v1 prices are nonnegative decimal **CAD** amounts (ISO 4217 `CAD`). A rate specifies whether it is charged by `hour`, `day`, or `entry`; additional fees have an independent description. Unknown pricing is represented by `null`, not a zero-dollar rate. Rates and fee timestamps are displayed independently of occupancy timestamps.
- Public read endpoints do not need end-user authentication. The batch estimation endpoint accepts privacy-sensitive observations and is **restricted to authenticated internal service clients** with the `parking:estimate` permission. The platform's trusted token issuer, validation rules, and distribution belong to the platform security specification. A Registry API key alone is not a valid end-user or estimator credential.
- All errors use the common JSON `error` envelope with `X-Request-ID`; all successful responses include `X-Request-ID`. The service rejects undeclared request-body properties. A no-match search returns `200` with `items: []`, while an unknown facility ID returns `404`.

## 3. Endpoint inventory

| Operation ID | Method and path | Purpose | Access | Success |
|---|---|---|---|---|
| `searchParkingFacilities` | `GET /v1/parking/facilities` | Geographic search and availability summary | Public read | `200` |
| `getParkingFacility` | `GET /v1/parking/facilities/{facility_id}` | Facility detail, estimated occupancy, rates and fees | Public read | `200` |
| `estimateParkingOccupancy` | `POST /v1/parking/occupancy/estimate` | Calculate occupancy from timestamped pseudonymous observations; no write | Restricted internal service | `200` |
| `getHealth` | `GET /health` | Liveness probe | Monitoring access | `200` |
| `getReadiness` | `GET /ready` | Readiness for facility-information and fallback operations | Monitoring access | `200` / `503` |

### 3.1 Search parking facilities — FR1, FR2, QR1, QR2, QR4

`GET /v1/parking/facilities`

| Query field | Required | Type / validation | Default |
|---|---|---|---|
| `min_lat`, `max_lat` | Yes | Finite decimal degrees `[-90, 90]`, in ascending order; span at most `2.0` degrees | — |
| `min_lon`, `max_lon` | Yes | Finite decimal degrees `[-180, 180]`, in ascending order; span at most `2.0` degrees | — |
| `facility_status` | No | `open`, `closed`, `unknown` | All statuses |
| `availability_status` | No | `available`, `full`, `closed`, `unknown` | All statuses |
| `sort_by` | No | `name` or `facility_id` | `name` |
| `sort_order` | No | `asc` or `desc` | `asc` |
| `limit` | No | Integer `1–100` | `25` |
| `offset` | No | Integer `>=0` | `0` |

Filters apply before stable sorting (with `facility_id` as tie-breaker) and pagination. Facility search matches **facility centers**, not geofence intersections. Only the documented filters/sort fields are supported.

**Example request**

```http
GET /v1/parking/facilities?min_lat=43.00&min_lon=-81.30&max_lat=43.05&max_lon=-81.20&facility_status=open&limit=25&offset=0
```

**Example 200 response — synthetic values**

```json
{
  "items": [
    {
      "facility_id": "parking-001",
      "name": "Example Downtown Parking",
      "latitude": 43.015,
      "longitude": -81.25,
      "capacity": 120,
      "geofence_radius_m": 80,
      "facility_status": "open",
      "availability_status": "available",
      "occupancy": {
        "estimated_occupied_spaces": 55,
        "estimated_available_spaces": 65,
        "occupancy_ratio": 0.4583,
        "congestion": "low",
        "observed_at": "2026-11-10T17:59:30Z",
        "service_updated_at": "2026-11-10T18:00:00Z",
        "is_live": true,
        "is_stale": false,
        "source": "parking_observation_provider",
        "data_status": "live"
      },
      "pricing": {
        "currency": "CAD",
        "starting_rate_amount": 3.50,
        "starting_rate_unit": "hour",
        "source_updated_at": "2026-11-10T09:00:00Z",
        "service_updated_at": "2026-11-10T09:01:00Z",
        "is_live": false,
        "is_stale": false,
        "source": "parking_rate_provider",
        "data_status": "cached"
      }
    }
  ],
  "pagination": {"limit": 25, "offset": 0, "total": 1, "has_more": false},
  "as_of": "2026-11-10T18:00:00Z",
  "facility_source": "parking_facility_catalog"
}
```

`capacity` represents the maximum usable spaces, not the observed number currently free. An unknown occupancy count does **not** exclude the facility unless the caller explicitly filters by `availability_status`. A source outage with a usable facility catalog returns `200` and degraded snapshots, not an unqualified `503`.

### 3.2 Retrieve a selected facility — FR2, FR3, QR1, QR2, QR4

`GET /v1/parking/facilities/{facility_id}`

| Field | Location | Required | Constraint |
|---|---|---|---|
| `facility_id` | Path | Yes | Opaque `1–64` character identifier matching `[a-z0-9][a-z0-9_-]*` |

No request body, pagination, or query filters are accepted. A facility not in the owned catalog returns `404 NOT_FOUND`.

**Example request**

```http
GET /v1/parking/facilities/parking-001
```

**Example 200 response — synthetic rates and fees**

```json
{
  "facility": {
    "facility_id": "parking-001",
    "name": "Example Downtown Parking",
    "latitude": 43.015,
    "longitude": -81.25,
    "capacity": 120,
    "geofence_radius_m": 80,
    "facility_status": "open"
  },
  "availability_status": "available",
  "occupancy": {
    "estimated_occupied_spaces": 55,
    "estimated_available_spaces": 65,
    "occupancy_ratio": 0.4583,
    "congestion": "low",
    "observed_at": "2026-11-10T17:59:30Z",
    "service_updated_at": "2026-11-10T18:00:00Z",
    "is_live": true,
    "is_stale": false,
    "source": "parking_observation_provider",
    "data_status": "live"
  },
  "pricing": {
    "currency": "CAD",
    "rates": [
      {"rate_id": "hourly", "label": "Standard hourly rate", "amount": 3.50, "unit": "hour"},
      {"rate_id": "daily", "label": "Daily maximum", "amount": 18.00, "unit": "day"}
    ],
    "additional_fees": [
      {"fee_id": "service_fee", "label": "Per-entry service fee", "amount": 1.00}
    ],
    "tax_included": false,
    "source_updated_at": "2026-11-10T09:00:00Z",
    "service_updated_at": "2026-11-10T09:01:00Z",
    "is_live": false,
    "is_stale": false,
    "source": "parking_rate_provider",
    "data_status": "cached"
  },
  "as_of": "2026-11-10T18:00:00Z",
  "facility_source": "parking_facility_catalog"
}
```

The facility's `rates` list and `additional_fees` list are separate to avoid suggesting that fees are automatically included in the advertised hourly/daily tariff. If price information is unavailable, `pricing` remains an object with `rates: []`, `additional_fees: []`, `currency: null`, `tax_included: null`, null update timestamps, `data_status: "unavailable"`, and `is_stale: true`; this must not be interpreted as free parking. If occupancy is unavailable, retain the facility and price information, set occupancy measurements to `null`, `congestion: "unknown"`, `data_status: "unavailable"`, `is_stale: true`, and `availability_status: "unknown"` (unless the facility is known closed).

### 3.3 Estimate occupancy from observations — FR2, QR3

`POST /v1/parking/occupancy/estimate`

**Purpose:** Compute a deterministic batch estimate from supplied recent time-location observations against the service-owned facility catalog. This is a **computation-only endpoint**: it does not create an occupancy report, update the public live occupancy cache, or persist the raw submitted observations.

**Access:** Authenticated internal service identity with the `parking:estimate` permission; external user clients must use a trusted application layer, not call this endpoint directly.

| JSON field | Required | Type / validation | Default |
|---|---|---|---|
| `observations` | Yes | Array of `0–10000` observations | — |
| `observations[].subject_id` | Yes | Nonempty pseudonymous string, at most 128 chars; must contain a non-whitespace character | — |
| `observations[].latitude`, `longitude` | Yes | Finite WGS 84 coordinates | — |
| `observations[].observed_at` | Yes | Timezone-aware ISO 8601 instant | — |
| `as_of` | No | Timezone-aware ISO 8601 reference instant, no more than 60 seconds after receipt | Server time in UTC |

**Estimator contract (adopted v1):** Examine the **120 seconds preceding `as_of`** (inclusive). For a pseudonymous subject, retain its latest observation within the window, require it to be no more than **60 seconds** before `as_of`, and require at least **30 seconds** of continuous presence within the same facility geofence with no observation gap greater than **60 seconds**. If a point lies in multiple geofences, assign it to the **nearest center** (breaking equal distances by `facility_id`). Count each qualifying subject at most once for a facility. The provider and algorithm are still implementation dependencies, but these rules make the computation testable.

Repeated identical `(subject_id, observed_at, coordinates)` observations are deduplicated. The same subject and timestamp at conflicting positions is invalid (`422`). Observations outside the reference window are ignored; an empty request returns an explicit unknown estimate rather than an invented count of zero. Counts are bounded to facility capacity and `capacity_exceeded` records an overcount. Historical `as_of` input is supported for reproducible replay but must not be represented as live current occupancy.

**Example request**

```http
POST /v1/parking/occupancy/estimate
Authorization: Bearer <internal_service_token>
Content-Type: application/json

{
  "as_of": "2026-11-10T18:00:00Z",
  "observations": [
    {"subject_id": "pseudonym-17", "latitude": 43.015, "longitude": -81.25, "observed_at": "2026-11-10T17:59:00Z"},
    {"subject_id": "pseudonym-17", "latitude": 43.015, "longitude": -81.25, "observed_at": "2026-11-10T17:59:40Z"}
  ]
}
```

**Example 200 response — synthetic data**

```json
{
  "items": [
    {
      "facility_id": "parking-001",
      "estimated_occupied_spaces": 1,
      "estimated_available_spaces": 119,
      "occupancy_ratio": 0.0083,
      "congestion": "low",
      "qualifying_subjects": 1,
      "capacity_exceeded": false,
      "latest_observation_at": "2026-11-10T17:59:40Z"
    }
  ],
  "as_of": "2026-11-10T18:00:00Z",
  "method": "stationary_unique_subjects_v1",
  "source": "submitted_observations",
  "is_live": false,
  "is_stale": true,
  "data_status": "computed"
}
```

`is_stale` in this computation response is evaluated against **actual response time**, not merely against historical `as_of`. The example deliberately uses a historical reference time and is therefore marked stale/not live. The per-facility list is a **bounded computation result** over the service-owned catalog, not a paginated public search. The estimator input and response **must not expose real personal identifiers**, and submitted pseudonyms and detailed trajectories must not be written to ordinary logs or persisted by this operation.

## 4. Error handling and validation

All errors **MUST** return the common `error` envelope with `code`, `message`, `request_id`, `details`, `retryable`, and `source`; `X-Request-ID` must match the JSON `request_id`.

| HTTP | Error code / condition | Applies to |
|---|---|---|
| `400` | `BAD_REQUEST` — malformed request syntax or incompatible structure | All |
| `401` | `AUTHENTICATION_REQUIRED` — missing/invalid internal token | Estimation |
| `403` | `FORBIDDEN` — service client lacks `parking:estimate` | Estimation |
| `404` | `NOT_FOUND` — unknown facility ID | Detail |
| `409` | `RESOURCE_CONFLICT` — conflicting operation state (if applicable; no create endpoints in v1) | Reserved general status |
| `422` | `VALIDATION_FAILED` — invalid coordinates, bounds, data types, contradictory observations, timestamp constraints | All with inputs |
| `429` | `RATE_LIMITED` — client/provider policy exceeded | All |
| `500` | `INTERNAL_ERROR` — unexpected internal fault | All |
| `502` | `UPSTREAM_FAILURE` — unusable provider response without acceptable fallback | Search/detail |
| `503` | `SERVICE_UNAVAILABLE` — catalog/essential data or estimator unavailable | All |
| `504` | `UPSTREAM_TIMEOUT` — upstream deadline exceeded without acceptable fallback | Search/detail |

Example validation error:

```json
{
  "error": {
    "code": "VALIDATION_FAILED",
    "message": "Geographic bounding box is invalid.",
    "request_id": "req-s4-001",
    "details": [{"field": "min_lat", "reason": "must_not_exceed_max_lat"}],
    "retryable": false,
    "source": "service"
  }
}
```

- Missing or invalid query fields are not silently defaulted except where explicitly specified. Unknown JSON request-body properties are rejected. Non-finite values (`NaN`, infinity) are invalid.
- If the facility catalog is usable but occupancy refresh fails, **do not** return an error just because live occupancy is missing; return the facility with cached/unavailable occupancy and explicit status. Similarly, previously known rates may be returned as `cached` and marked stale when their 24-hour threshold has elapsed.
- A fallback is never allowed to invent prices, availability, timestamps, or occupied-space counts. If **no usable facility catalog** is accessible, use a documented 5xx error and mark `/ready` unavailable unless an adequate persisted facility fallback exists.
- The batch estimator has no state-changing side effects: repeating an identical request with the same `as_of` and unchanged facility catalog computes equivalent results; **no `Idempotency-Key` is required**. Omitting `as_of` makes separate requests reference different times, so equivalent results are not guaranteed.

## 5. Service-owned data and dependencies

| Entity | Ownership and persistence rule |
|---|---|
| `ParkingFacility` | S4 owns the normalized catalog, stable facility IDs, capacities, locations, operational status, and geofence configuration. Persist a usable catalog and its source metadata separately from the Registry. |
| `OccupancySnapshot` | S4 owns estimated occupancy results, the algorithm version, source/observation timestamps, and freshness state. Keep only bounded, privacy-preserving aggregated results required for serving live/cached responses. |
| `ParkingRate` / `ParkingFee` | S4 owns normalized cached tariffs, fee conditions, currency, provider update timestamps, and freshness metadata. The provider remains authoritative for source tariffs. |
| Raw `TimeLocationObservation` | Supplied to the restricted estimator; **not persisted** by that endpoint. Any ingestion workflow collecting location data must separately define consent, retention, minimization, and access controls. |

S4 MUST have **service-owned storage**, not a shared domain database or the Service Registry's PostgreSQL database (ASR6). A PostgreSQL database with optional PostGIS geographic indexing is a **storage proposal**, not a prerequisite of this HTTP contract. A local fixture may support testing, but production responses MUST identify simulations and MUST NOT represent them as live information.

**External provider decisions remain unconfirmed:** Facility catalog/operating status, live or time-location-derived occupancy observations, and fee/rate schedules may come from different providers. A provider register must list its API/feed protocol, credential requirements, measurement coverage, update frequency, rate limits, outage behavior, and confirmation status. Until integrations are selected, do not name hypothetical providers as approved.

**Readiness policy:** `/health` reports process liveness only. `/ready` succeeds when a valid facility catalog can be queried and the service can return an honest live **or degraded/fallback** response. Loss of the live occupancy feed alone must not make S4 unready when its catalog and fallback are usable; inability to return any facility catalog makes readiness fail with `503`. Public operational endpoints should be accessible to authorized monitoring infrastructure, not indiscriminately exposed over the public internet.

## 6. Performance and acceptance verification

| Requirement | Verification |
|---|---|
| **QR1 — 2,000 ms** | Valid facility-search and facility-detail requests return within **2,000 ms** under the agreed test setup; verify both populated and empty search responses. |
| **QR2 — data freshness** | Both occupancy and price updates are returned separately; test exactly 300 seconds and 301 seconds for occupancy, and exactly 24 hours and later for pricing; missing timestamps must never be presented as fresh. |
| **QR3 — 25 concurrent prediction requests** | Execute **25 simultaneous occupancy-estimation requests** with an agreed bounded dataset, while testing that facility retrieval still meets its 2,000 ms target. PR1 does not define a separate numerical estimate-computation deadline; record estimator latency rather than pretending one is prescribed. |
| **QR4 — degraded availability** | Simulate an occupancy-provider outage; confirm search and detail still show available catalog data and any legitimate cached pricing, with stale/unknown occupancy correctly flagged. |
| **FR2 — estimator correctness** | Test 120-second window, 60-second recency, 30-second dwell, 60-second gap, geofence ties, conflicting same-time coordinates, capacity clamping, and unavailable estimates. |
| **Common standards** | Validate OpenAPI schema, request samples, standardized errors, stable pagination, UTC output, public vs. service-only authentication, and health/readiness responses. |

## 7. Contract acceptance checklist

- [ ] All three S4 operations implement the defined request and response schemas.
- [ ] FR1–FR3 and QR1–QR4 are demonstrably covered.
- [ ] Separate occupancy and pricing timestamps and freshness states are returned.
- [ ] Simulated, live, cached, and unavailable data cannot be confused.
- [ ] Anonymous callers cannot submit raw observation batches.
- [ ] Service-owned catalog, occupancy snapshots, and tariff cache survive the intended failure modes without sharing a domain database with other services.
- [ ] Geographic validation, stable pagination, fee units, error envelopes, and request IDs comply with the common standards.
- [ ] Performance and fallback acceptance scenarios are documented and reproducible.

## 8. Change control

This interface follows `docs/api/API-Standards.md`. Breaking changes to request parameters, authentication requirements, field semantics, or response shapes require a new contract version or an explicitly approved compatibility migration. Implementation details may change without revising the API contract as long as externally observable behavior remains conformant.
