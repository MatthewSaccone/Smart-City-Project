# S6 — Public Transit Analysis: API Contract

| Property | Specification |
|---|---|
| Document ID | T008-S6-API |
| Contract version | 1.0 |
| Status | Adopted design specification — implementation target |
| Logical service ID | `transit-analytics` (S6) |
| API base | `/v1/transit` |
| Machine-readable specification | `shared/api-contracts/s6-public-transit.openapi.yaml` |
| Platform convention | `docs/api/API-Standards.md`, v1.0 |
| Requirement source | `Group 10 Project PR1.xlsx` → `Service Requirements` → S6 |

## 1. Purpose and scope

S6 publishes public-transit routes and stops, expected vehicle arrival/departure times, reported delays, service disruptions (including cancellations and outages), and available passenger occupancy/congestion information. It distinguishes **published schedules** from **live estimates** and continues offering usable schedule information when live feeds fail. This is an **API design specification**: the service is not assumed to be fully implemented and provider integrations are not claimed to exist.

### Requirements traceability (PR1 — S6)

| PR1 ID | Source requirement (concise restatement) | Defined operations / policy |
|---|---|---|
| FR1 | Expected arrival and departure times at a user-defined stop/station | `GET /v1/transit/stops/{stop_id}/arrivals` |
| FR2 | Identify and report delays affecting transit vehicles | `GET /v1/transit/delays`; arrival/departure delay fields |
| FR3 | Report service disruptions, cancellations, outages | `GET /v1/transit/disruptions` |
| FR4 | Available passenger occupancy/congestion for vehicles or routes | `GET /v1/transit/occupancy` |
| FR5 | Routes and stops within a user-defined area | `GET /v1/transit/routes`, `GET /v1/transit/stops` |
| QR1 | Expected arrival/departure response within **1,500 ms** of valid request | Stop-arrival endpoint performance target |
| QR2 | Publish the last update time for live information and flag updates **older than 60 seconds** | `last_live_update_at`, `live_update_status`, `is_stale` on relevant results |
| QR3 | Continue offering schedules when live data is unavailable; distinguish scheduled times from live estimates | Schedule fallback and event-time `basis` fields |
| QR4 | Maintain defined response-time requirements under **50 concurrent prediction requests** | 50 concurrent stop-arrival/prediction queries must meet QR1 |

**Scope of PR1 vs. T008 design:** PR1 mandates the capabilities and targets above, but does **not** identify transit providers, maximum radius, scheduling horizon, supported modes, or storage technology. Values below for those items are **adopted S6 design choices**, not quotations of PR1. PR1 uses the expression “concurrent prediction requests” for S6 QR4; the acceptance test applies it to S6 arrival/departure estimate requests rather than silently changing the wording.

## 2. Cross-cutting contract rules

- All public operations are read-only `GET` routes and require **no end-user bearer token** in v1. Operational probes are for authorized monitoring infrastructure. Provider credentials stay server-side.
- Every successful response returns an `X-Request-ID` header, and errors use the common `error` envelope with the same request ID.
- JSON fields and query parameters use `snake_case`; URL path segments are lowercase and versioned. All output timestamps are RFC 3339 UTC strings ending in `Z`; request timestamps may include an explicit UTC offset.
- Route/stop/trip/vehicle/disruption IDs are opaque provider-normalized strings, unique within S6. Clients must not infer their meaning or stability across data-provider migrations.
- Geographic coordinates are WGS 84 decimal degrees; `radius_m` uses metres. Time intervals use UTC instants; duration/delay fields use seconds.
- No endpoint accepts a JSON request body. `404` is appropriate for an unknown requested stop; a valid search with no results returns `200` and `items: []` when the underlying dataset is available.
- Query parameters and JSON fields not documented in the OpenAPI contract must be rejected according to the platform validation policy.
- All collection responses use `items` and `pagination: {limit, offset, total, has_more}`, default `limit=25`, maximum `100`, and default `offset=0`. `total` can be `null` only when an exact count cannot be produced; `has_more` is then determined by fetching one extra row.
- Filtering occurs before deterministic sorting and then pagination. Ties are resolved with the record's unique ID. Result-set changes between page requests may move entries; a stable snapshot mechanism may be added in a future compatible revision.

### 2.1 Transit data sources and freshness

S6 distinguishes a **static/scheduled transit dataset** from a **live vehicle update dataset**. Suitable formats to investigate include GTFS Schedule and GTFS Realtime, but **no provider or feed is confirmed**. Data format support is a proposed dependency, not a statement that the feed is integrated.

| Field | Meaning |
|---|---|
| `as_of` | UTC time the request is evaluated |
| `last_live_update_at` | Last trustworthy observation/update instant supplied for live transit data; `null` when unknown/unavailable |
| `source_updated_at` | Provider-declared dataset update time, if available |
| `service_updated_at` | Time S6 refreshed its own normalized cache/result |
| `is_live` | Whether returned estimates currently rely on trustworthy live updates |
| `is_stale` | Whether the latest known live update is **more than 60 seconds** old; `false` when no known live timestamp exists (check `live_update_status`) |
| `live_update_status` | `fresh`, `stale`, or `unavailable` |
| `data_status` | `live`, `scheduled`, `cached`, `simulated`, or `unavailable` |
| `source` | Documented provider/feed identifier, such as an example source name; not necessarily a confirmed live provider |
| `coverage_status` | `complete`, `partial`, or `unavailable` for the requested category |

**Freshness decision:** For known live updates, age = `as_of - last_live_update_at`. An update with age **≤ 60 s** is fresh; age **> 60 s** is stale. Future-dated updates are invalid and must not be treated as fresh. If the provider cannot furnish a trustworthy update timestamp, S6 must not assert fresh live predictions and sets `live_update_status: unavailable`.

**Scheduled fallback:** For an arrival/departure, `scheduled_at` is the published timetable; `estimated_at` is a derived live estimate and is `null` when not trustworthy. `display_at` is the field the client should display. Its `basis` must be `live_estimate` when a fresh estimate exists, otherwise `schedule`; clients must not infer that a scheduled time is a live prediction. Stale, previously predicted arrival times must not silently remain labeled live. Schedule availability is governed by its own feed validity period and service calendar, not by the 60-second live-feed threshold. For route/stop search responses, `schedule_valid_through` states the last timestamp covered by the currently available published schedule, and `is_live: false`, `is_stale: false` describes that scheduled dataset (not the state of any real-time feed).

**Degraded behavior:** When the live provider fails, S6 must still answer stop arrival/departure requests with valid published schedules when available, explicitly setting the live update state to `stale` or `unavailable` and selecting `schedule` as the time basis. Delay and occupancy data can be `unavailable` independently. If no acceptable schedule or other requested data exists, respond with a documented `502`, `503`, or `504`; do not invent live times, zeros, passenger counts, or an empty list implying there are no disruptions.

## 3. Endpoint inventory

| Operation ID | Method / path | Purpose | PR1 |
|---|---|---|---|
| `listTransitStops` | `GET /v1/transit/stops` | Stop/station geographic discovery | FR5 |
| `listTransitRoutes` | `GET /v1/transit/routes` | Routes serving a user-defined area | FR5 |
| `getStopArrivals` | `GET /v1/transit/stops/{stop_id}/arrivals` | Upcoming arrivals/departures, schedule and predictions | FR1; QR1–QR4 |
| `listTransitDelays` | `GET /v1/transit/delays` | Reported transit vehicle/trip delays | FR2; QR2–QR3 |
| `listTransitDisruptions` | `GET /v1/transit/disruptions` | Cancellations, outages and service advisories | FR3; QR2–QR3 |
| `listTransitOccupancy` | `GET /v1/transit/occupancy` | Available vehicle/route congestion information | FR4; QR2–QR3 |
| `getHealth` | `GET /health` | Liveness | Common standard |
| `getReadiness` | `GET /ready` | Serve schedule-backed core operations | Common standard |

### 3.1 Search stops in an area — FR5

`GET /v1/transit/stops`

| Query parameter | Required | Validation / meaning |
|---|---|---|
| `latitude`, `longitude` | Yes | WGS 84 finite coordinates; `[-90,90]` and `[-180,180]` |
| `radius_m` | Yes | `0 < radius_m <= 20,000` metres (T008 design maximum) |
| `mode` | No | `bus`, `tram`, `train`; omit for all supported modes |
| `sort_by` | No | `distance_m` (default) or `name` |
| `sort_order` | No | `asc` (default), `desc` |
| `limit`, `offset` | No | Common pagination rules |

**Example request**

```http
GET /v1/transit/stops?latitude=43.0096&longitude=-81.2737&radius_m=1500&mode=bus&sort_by=distance_m&sort_order=asc&limit=25&offset=0
```

**Example response — synthetic data**

```json
{
  "items": [
    {"stop_id": "stop-102", "name": "Central Station", "latitude": 43.0098,
     "longitude": -81.2731, "distance_m": 55, "modes": ["bus"],
     "route_ids": ["route-5", "route-12"]}
  ],
  "pagination": {"limit": 25, "offset": 0, "total": 1, "has_more": false},
  "as_of": "2026-11-10T18:00:00Z",
  "source": "example_schedule_feed", "source_updated_at": "2026-11-10T10:00:00Z",
  "data_status": "scheduled", "is_live": false, "is_stale": false,
  "schedule_valid_through": "2026-12-31T23:59:59Z"
}
```

A stop is returned when its coordinates fall within the requested radius (geodesic distance); results have a deterministic distance/name order and unique-ID tie-breaker. An area with no available stops returns an empty collection if schedule data is available.

### 3.2 Search routes in an area — FR5

`GET /v1/transit/routes`

Required: `latitude`, `longitude`, `radius_m`; optional: `mode`, `sort_by` (`short_name`, default; or `route_id`), `sort_order` (`asc`, default), `limit`, `offset`. Radius and coordinate rules match stop searches.

**Example request**

```http
GET /v1/transit/routes?latitude=43.0096&longitude=-81.2737&radius_m=1500&mode=bus
```

**Example response — synthetic data**

```json
{
  "items": [
    {"route_id": "route-5", "short_name": "5", "long_name": "Central Express",
     "mode": "bus", "agency_id": "agency-example"}
  ],
  "pagination": {"limit": 25, "offset": 0, "total": 1, "has_more": false},
  "as_of": "2026-11-10T18:00:00Z",
  "source": "example_schedule_feed", "source_updated_at": "2026-11-10T10:00:00Z",
  "data_status": "scheduled", "is_live": false, "is_stale": false,
  "schedule_valid_through": "2026-12-31T23:59:59Z"
}
```

A route matches when at least one of its advertised stops lies within the requested radius. This rule avoids requiring all providers to supply complete route geometries. A route appears at most once per page; the full matching collection is sorted before pagination.

### 3.3 Expected arrivals and departures — FR1, QR1–QR4

`GET /v1/transit/stops/{stop_id}/arrivals`

| Parameter | Required | Validation |
|---|---|---|
| `stop_id` (path) | Yes | Opaque nonblank string, length `1–128` |
| `start_at` (query) | No | Timezone-aware instant, defaults to request evaluation (`as_of`), no more than 24 hours ahead |
| `window_minutes` (query) | No | Integer `1–180`, default `60` |
| `route_id` (query) | No | Filter by route; max length 128 |
| `sort_by` (query) | No | `display_at` (the supported sort field; default) |
| `sort_order` (query) | No | `asc` (default) or `desc` |
| `limit`, `offset` | No | Common pagination rules |

The interval is `[start_at, start_at + window_minutes)`, with default ascending order by `display_at`, then `trip_id` as a stable tie-breaker; `sort_order=desc` reverses the primary order. The start time must not precede request receipt by more than 5 minutes, and the end must be within the valid schedule horizon. An unknown `stop_id` returns `404`.

**Example request**

```http
GET /v1/transit/stops/stop-102/arrivals?window_minutes=60&route_id=route-5
```

**Example response — synthetic data**

```json
{
  "stop_id": "stop-102",
  "as_of": "2026-11-10T18:00:00Z",
  "items": [{
    "trip_id": "trip-501", "route_id": "route-5", "stop_id": "stop-102",
    "vehicle_id": "vehicle-31", "destination": "West Terminal",
    "arrival": {
      "scheduled_at": "2026-11-10T18:10:00Z",
      "estimated_at": "2026-11-10T18:13:00Z",
      "display_at": "2026-11-10T18:13:00Z",
      "basis": "live_estimate", "delay_seconds": 180
    },
    "departure": {
      "scheduled_at": "2026-11-10T18:11:00Z",
      "estimated_at": "2026-11-10T18:14:00Z",
      "display_at": "2026-11-10T18:14:00Z",
      "basis": "live_estimate", "delay_seconds": 180
    }
  }],
  "pagination": {"limit": 25, "offset": 0, "total": 1, "has_more": false},
  "provenance": {
    "last_live_update_at": "2026-11-10T17:59:35Z",
    "source_updated_at": "2026-11-10T17:59:35Z",
    "service_updated_at": "2026-11-10T17:59:40Z",
    "is_live": true, "is_stale": false,
    "live_update_status": "fresh", "data_status": "live",
    "source": "example_transit_realtime_feed"
  }
}
```

**Required fallback (QR3):** If the last live update is stale or absent, retain `arrival.scheduled_at` / `departure.scheduled_at`; set each `estimated_at` to `null`, each `display_at` to its `scheduled_at`, and `basis` to `schedule`. Set `last_live_update_at` to its real prior timestamp or `null`, `is_live: false`, and `live_update_status: stale` or `unavailable`. For known stale updates, `is_stale: true`. Example:

```json
{
  "scheduled_at": "2026-11-10T18:10:00Z",
  "estimated_at": null,
  "display_at": "2026-11-10T18:10:00Z",
  "basis": "schedule",
  "delay_seconds": null
}
```

`arrival` and `departure` can independently be `null` when the corresponding event is not scheduled at the stop (for example, a terminating trip). At least one must exist. A delayed live estimate's signed `delay_seconds` equals `estimated_at - scheduled_at`; positive indicates late, negative early. There must be no fabricated estimated time for unavailable live data. Provider-to-service latency and response time must be bounded to meet **QR1: ≤1,500 ms** for valid arrival/departure requests, including schedule fallback. QR4 requires that the arrival/prediction endpoint continue to meet its specified response-time target under **50 concurrent prediction requests**.

### 3.4 Transit delays — FR2

`GET /v1/transit/delays`

Require at least one of `route_id` or `stop_id`; both may be supplied to filter intersection. Optional `sort_by=delay_seconds|event_at` (default `event_at`), `sort_order=desc|asc` (default `desc`), `limit`, `offset`.

**Example request**

```http
GET /v1/transit/delays?route_id=route-5&sort_by=delay_seconds&sort_order=desc
```

**Example response — synthetic data**

```json
{
  "items": [{
    "trip_id": "trip-501", "route_id": "route-5", "stop_id": "stop-102",
    "vehicle_id": "vehicle-31", "event_type": "arrival",
    "event_at": "2026-11-10T18:13:00Z", "delay_seconds": 180
  }],
  "pagination": {"limit": 25, "offset": 0, "total": 1, "has_more": false},
  "as_of": "2026-11-10T18:00:00Z",
  "coverage_status": "complete",
  "provenance": {
    "last_live_update_at": "2026-11-10T17:59:35Z",
    "source_updated_at": "2026-11-10T17:59:35Z",
    "service_updated_at": "2026-11-10T17:59:40Z",
    "is_live": true, "is_stale": false,
    "live_update_status": "fresh", "data_status": "live",
    "source": "example_transit_realtime_feed"
  }
}
```

Only known delays greater than zero are included. When live delay data is unavailable, return `200` with `items: []`, `coverage_status: unavailable`, and explicit provenance **only if clients can distinguish “not known” from “no delays”**; otherwise use `503`. Do not interpret an empty list during an outage as a statement that all transit is on time.

### 3.5 Transit disruptions, cancellations, outages — FR3

`GET /v1/transit/disruptions`

Optional `route_id`, `stop_id`, `status=active|planned|resolved`, `kind=disruption|cancellation|outage|detour`, `sort_by=updated_at|starts_at` (default `updated_at`), `sort_order=asc|desc` (default `desc`), `limit`, `offset`. At least one of `route_id` and `stop_id` must be supplied to constrain the query in v1.

**Example request**

```http
GET /v1/transit/disruptions?route_id=route-5&status=active
```

**Example response — synthetic data**

```json
{
  "items": [{
    "disruption_id": "disruption-72", "kind": "cancellation", "status": "active",
    "message": "Selected service is cancelled.", "route_ids": ["route-5"],
    "stop_ids": ["stop-102"], "starts_at": "2026-11-10T18:00:00Z",
    "ends_at": null, "updated_at": "2026-11-10T17:59:20Z"
  }],
  "pagination": {"limit": 25, "offset": 0, "total": 1, "has_more": false},
  "as_of": "2026-11-10T18:00:00Z", "coverage_status": "complete",
  "provenance": {
    "last_live_update_at": "2026-11-10T17:59:20Z",
    "source_updated_at": "2026-11-10T17:59:20Z",
    "service_updated_at": "2026-11-10T17:59:25Z",
    "is_live": true, "is_stale": false,
    "live_update_status": "fresh", "data_status": "live",
    "source": "example_transit_alerts_feed"
  }
}
```

When provider alerts are unavailable, a last known disruption may be returned as cached only with its original timestamps, stale flags, and partial/unavailable coverage disclosure. If no trustworthy disruption dataset exists, return `503`, `502`, or `504` rather than an empty collection that might misrepresent service as operating normally.

### 3.6 Passenger occupancy and congestion — FR4

`GET /v1/transit/occupancy`

At least one of `route_id` and `vehicle_id` is required. If both are supplied, the service filters their intersection. Optional `sort_by=observed_at|route_id` (default `observed_at`), `sort_order=desc|asc` (default `desc`), `limit`, `offset`.

**Example request**

```http
GET /v1/transit/occupancy?route_id=route-5
```

**Example response — synthetic data**

```json
{
  "items": [{
    "route_id": "route-5", "vehicle_id": "vehicle-31",
    "occupancy_level": "moderate", "passenger_count": null,
    "capacity": null, "occupancy_percent": null,
    "observed_at": "2026-11-10T17:59:30Z"
  }],
  "pagination": {"limit": 25, "offset": 0, "total": 1, "has_more": false},
  "as_of": "2026-11-10T18:00:00Z", "coverage_status": "partial",
  "provenance": {
    "last_live_update_at": "2026-11-10T17:59:30Z",
    "source_updated_at": "2026-11-10T17:59:30Z",
    "service_updated_at": "2026-11-10T17:59:35Z",
    "is_live": true, "is_stale": false,
    "live_update_status": "fresh", "data_status": "live",
    "source": "example_vehicle_occupancy_feed"
  }
}
```

`occupancy_level` is `low`, `moderate`, `high`, `full`, or `unknown`. Numerical occupancy fields are nullable rather than invented zero; percentage is `0–100` when known. The service may supply a qualitative level without an exact passenger count. Without supported occupancy data, return `200 items: []` **only** with `coverage_status: unavailable` and `live_update_status: unavailable`; this must not imply empty vehicles. Stale observations retain truthful timestamps and must never be labeled current.

## 4. Validation, errors, security and transport

- Input types/ranges follow `API-Standards.md`: finite WGS 84 coordinates, positive capped radius, valid nullable vs optional fields, aware timestamps, explicit enums. Oversized/missing/contradictory parameters return `422` with structured details; malformed requests return `400`.
- `stop_id`, `route_id`, `trip_id`, `vehicle_id`, and `disruption_id` must be nonblank opaque strings of `1–128` characters. An unavailable resource versus a transient feed outage must not be conflated (`404` vs `503`).
- Standard errors must enumerate `400`, `404`, `422`, `429`, `500`, `502`, `503`, and `504`. Specific codes may refine `VALIDATION_FAILED`, `NOT_FOUND`, `UPSTREAM_FAILURE`, `SERVICE_UNAVAILABLE`, and `UPSTREAM_TIMEOUT` while retaining the common envelope.
- `401` and `403` apply if an operation becomes protected in a later approved version. Version 1 client-facing reads are public; provider tokens and service-to-service authentication are internal.
- Every provider request must have a finite timeout. Cached schedule responses must not wait indefinitely for live providers. S6 MUST avoid calling an external provider synchronously on every client request when cached data meets the contract.
- No client-submitted writes, duplicate submissions, or idempotency keys apply to the v1 public API.

## 5. Service-owned data and external dependencies

| Data category | Owning service | Suggested persistence / cache |
|---|---|---|
| Normalized agencies, routes, stops, trips, service calendars and stop times | S6 | S6-owned relational dataset (e.g., PostgreSQL with optional PostGIS), persisted across instance restarts |
| Current vehicle arrival/departure estimates and last-update timestamps | S6 | Timestamped bounded cache, source reference retained |
| Transit disruptions, cancellations, outages and status | S6 | Cache with provider update times; optional historical retention |
| Available vehicle/route occupancy observations and estimates | S6 | Timestamped bounded cache; minimize personally identifying data |
| Service registration/discovery metadata | Service Registry | Registry-owned storage, **not** S6 domain storage |

**Dependencies requiring confirmation:** an authoritative schedule feed and publisher; a real-time trip-update or vehicle feed; service-alert feed; vehicle occupancy data where offered; published operator/service-area coverage and licenses. For each, document provider/dataset, access protocol, credentials, refresh interval, limits, field coverage, outage behavior, and whether the provider is selected or merely proposed. Do not invent provider availability, real-time coverage, or passenger-count data. S6 must have its own data ownership boundary, even when a managed database platform is shared at infrastructure level.

**Readiness:** `/health` returns process liveness without expensive dependency checks. `/ready` succeeds while S6 can answer required schedule-backed stop/route/arrival operations using a valid persisted schedule dataset, even if live updates are unavailable. It returns `503` when no acceptable schedule-backed operation can run. Nonessential live providers and occupancy feeds are reported in service monitoring, not automatically treated as mandatory readiness dependencies. All operational response bodies follow the common two-field JSON convention.

## 6. Acceptance verification

1. **FR5:** Geographic stop/route queries include matching entities only, respect mode filters, stable sorting and `limit`/`offset`, and return truthful empty results.
2. **FR1:** For a known stop, output at least one published arrival or departure where a schedule exists; distinguish timetable and live estimate independently for each event.
3. **QR2:** With a recorded live update exactly 60 seconds old, mark `fresh`; with 61 seconds, mark `stale`, preserve `last_live_update_at`, and stop presenting that prediction as current.
4. **QR3:** Disable live feeds while retaining valid schedules; arrival/departure requests must still succeed with `basis: schedule` and no fabricated `estimated_at`.
5. **FR2:** Delays have documented positive seconds and correlate to trips/stops; missing live coverage is not treated as “all trips on time.”
6. **FR3:** Cancellations and outages are represented separately from generic advisories; stale alerts retain source update timestamps or fail explicitly if no usable dataset exists.
7. **FR4:** Occupancy levels are available when supported, and `null` is used for unavailable counts/capacity; unavailable feed coverage is explicit.
8. **QR1:** Arrival/departure requests complete within **1,500 ms** of valid request receipt under the specified normal conditions.
9. **QR4:** Repeat the QR1 latency test at **50 concurrent arrival/prediction requests** without violating the defined response target.
10. **Conformance:** Validate request/response examples against OpenAPI, standard `X-Request-ID` and error envelope, `/health` and `/ready`, and no unauthorized cross-service database access.

## 7. Contract acceptance checklist

- [x] Logical service ID; operation IDs; HTTP methods, paths and purposes
- [x] Request parameters, types, constraints, units and examples; no GET bodies
- [x] Response schemas, sample outputs and standard error envelope
- [x] Required QR latency/concurrency, freshness and scheduled fallback rules
- [x] Filtering, ordering, pagination, access policy, operational endpoints
- [x] Service-owned entities, proposed storage boundary and candidate upstream dependencies
- [ ] External feed providers, licenses, coverage and contracts **confirmed** (implementation dependency)
- [ ] Executable conformance, latency and degraded-feed tests **implemented** (implementation phase)

## 8. Change control

This is the S6 v1 **implementation target**. Changes affecting public paths, schemas, time semantics, authentication, validation, or error behavior must update the Markdown and OpenAPI specifications together and follow the versioning/change-control policy in `API-Standards.md`.

---

**Design principle:** A scheduled arrival time is useful even without live telemetry, but it is **never** a live estimate. S6 must show what is known, when it was updated, and what cannot currently be determined.
