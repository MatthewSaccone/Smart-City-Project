# S7 — Gasoline Price Analytics: API Contract

| Property | Specification |
|---|---|
| Document ID | T008-S7-API |
| Contract version | 1.0 |
| Status | Adopted design specification — implementation target |
| Logical service ID | `gasoline-analytics` (S7) |
| API bases | `/v1/gas-stations` and `/v1/gas-prices` |
| Machine-readable specification | `shared/api-contracts/s7-gasoline-analytics.openapi.yaml` |
| Platform convention | `docs/api/API-Standards.md`, v1.0 |
| Requirement source | `Group 10 Project PR1.xlsx` → `Service Requirements` → S7, rows 33–37 |

## 1. Purpose and traceability

S7 allows clients to find nearby fuel stations and the latest accepted user-reported prices, filter by fuel type and price, compare prices across stations in a defined geographic radius, and submit new timestamped price reports for a known station. A **report is an accepted user claim, not independent price verification**. This contract specifies the intended interface; it does not imply that a user-authentication service, station directory, or live fuel-price provider has already been integrated.

PR1 lists **two distinct S7 requirements with the ID `FR3`**. This specification preserves the workbook's identifiers by using *FR3 (timestamp)* and *FR3 (comparison)* for traceability. These qualifiers are local labels, **not** changes to PR1.

| PR1 ID | Source requirement (faithful summary) | Defined operation or rule |
|---|---|---|
| FR1 | Provide available gasoline prices from stations within a user-defined area | `GET /v1/gas-stations` |
| FR2 | Allow users to submit updated gasoline prices for a selected station | `POST /v1/gas-stations/{station_id}/price-reports` |
| FR3 (timestamp) | Record submission/update timestamps for user-reported prices | Server-assigned `submitted_at`, `last_accepted_report_at`; immutable report history |
| FR3 (comparison) | Compare station prices within a user-specified area | `GET /v1/gas-prices/comparisons` |
| FR4 | Filter stations by fuel type and reported price | Station search filters `fuel_type`, `min_price_per_litre`, `max_price_per_litre` |
| QR1 | Return available gas prices within **1,500 ms** of valid request receipt | Search and comparison retrieval latency |
| QR2 | Validate required price-report fields (price, fuel type, station ID, etc.) before acceptance | Request schema and cross-field validation |
| QR3 | Provide timestamp of most recent accepted price report | `last_accepted_report_at` on station records |
| QR4 | Preserve accepted reports across service restart | S7-owned durable report storage; commit before 201 |

**Source versus design:** PR1 does not specify price units, currency, supported fuel grades, geographic maximum, report-expiry threshold, authentication mechanism, or price confirmation policy. The concrete values and controls below are **T008 contract decisions**, not quotations from PR1. S7 is initially scoped to Canada and records prices in **CAD per litre**. A future multi-currency API would require an explicit version/extension policy.

## 2. Common semantics and domain rules

- Read endpoints are public in v1. Creating reports requires `Authorization: Bearer <access_token>` from a trusted identity system; the user identity is derived from validated credentials, **never** from an editable `user_id`/`owner_id` field.
- All HTTP responses include `X-Request-ID`. Errors use the adopted `error` envelope. JSON properties and query names use `snake_case`; outgoing instants are ISO 8601/RFC 3339 UTC with `Z`. No GET endpoint accepts a request body.
- `station_id` and `price_report_id` are opaque service-owned identifiers (1–128 characters in paths, UUID for generated report IDs). A station must exist before S7 accepts a report for it. Report submissions do not create new station records.
- v1 `fuel_type`: `regular_87`, `midgrade_89`, `premium_91`, `premium_93`, or `diesel`. They are **contract labels**, not guarantees a station currently has that fuel in stock. `diesel` is supported as an additional motor-fuel category.
- All prices are JSON numbers in **CAD/L** (`currency: "CAD"`, `price_unit: "CAD/L"`), strictly greater than zero and no greater than **20.000**, at most three digits after the decimal point. Implementations must use **decimal-safe arithmetic/storage**, not binary-float equality for monetary reconciliation.
- A price submission is appended as an immutable `PriceReport`. A later accepted submission updates the station/fuel's displayed quote only when it becomes the latest accepted report **by `submitted_at`**, with `price_report_id` resolving deterministic ties. The service retains previous accepted reports privately. This is a reporting model, not an assertion of truth or external verification.
- Newly accepted user reports have `verification_status: "unverified"`; optional future review can set `"reviewed"`. A reviewed report is still not a guarantee that the station currently charges that price. The public API must never label user reports as provider-verified.
- **Unknown prices are represented by missing quote entries**, not `0.00`. `available_fuel_types` describes catalogued grades, not confirmed stock. A station may therefore appear in geographic results with an empty `prices` array and `price_coverage_status: "unavailable"`.
- Search and comparisons use geodesic distance between WGS 84 coordinates. The radius is inclusive at its boundary. Maximum search radius is **25,000 m**. For comparison, statistics reflect **all eligible matching stations before pagination**; the response's paginated `items` only control the displayed subset.
- Filters run before deterministic sorting and pagination. `limit` defaults to 25 (maximum 100); `offset` defaults to zero. `pagination.total` is an exact count when available, otherwise `null` with `has_more` determined by fetching an extra eligible record.

### 2.1 Report recency, freshness and provenance

| Field | Meaning |
|---|---|
| `submitted_at` | Server-generated instant S7 durably accepted a price report; **not** the station's actual observation time |
| `observed_at` | Optional user-declared time at which the price was seen; `null` if unavailable |
| `last_accepted_report_at` | Latest `submitted_at` of an accepted user price report for the station; `null` if none |
| `as_of` | Server's UTC reference instant for the response |
| `freshness_basis` | `observed_at` when provided; otherwise `submitted_at`, which is only a **report-age proxy** |
| `is_stale` | `true` when the applicable basis is more than **24 hours** before `as_of`; exact 24 hours is not stale |
| `is_live` | `false` for all user-reported quotes; a recent report is **not** an automatically updating live feed |
| `source` | `user_report` for price quotes; station-directory provenance is separate |
| `data_status` | `user_report` for known quotes, `unavailable` when no eligible quote exists |

**24-hour review threshold:** This is a design choice, not PR1 QR3. A stale report can still be returned with `is_stale: true` and its original timestamp. The contract does **not** assert that a report at or below 24 hours reflects the real pump price. The API retains stale reports for transparent fallback but never relabels them current or live. If a source/feed fails, a persisted station directory and accepted reports remain usable; when no usable directory is available, return a documented `502`, `503`, or `504` rather than pretending that no stations exist.

A station response's `is_stale` is `true` when **any included price quote** is stale; when there are no quotes, it is `false` and `price_coverage_status` must make the absence explicit. Its `last_accepted_report_at` considers **all** accepted station reports, regardless of requested fuel or freshness filter. Price comparisons default to excluding stale quotes; stations without an eligible quote cannot participate in a numerical comparison.

## 3. Endpoint inventory

| Operation ID | Method and path | Purpose | PR1 |
|---|---|---|---|
| `searchGasStations` | `GET /v1/gas-stations` | Geographic station/price search and fuel/price filters | FR1, FR4; QR1, QR3 |
| `getGasStation` | `GET /v1/gas-stations/{station_id}` | Station details, fuel prices and latest accepted-report timestamp | FR1, FR3 (timestamp) |
| `compareGasPrices` | `GET /v1/gas-prices/comparisons` | Price comparisons across an area for one fuel type | FR3 (comparison); QR1 |
| `createGasPriceReport` | `POST /v1/gas-stations/{station_id}/price-reports` | Authenticated, durable price-report submission | FR2, FR3 (timestamp); QR2, QR4 |
| `getHealth` | `GET /health` | Application liveness | Common standard |
| `getReadiness` | `GET /ready` | Persistent catalog/report-store readiness | Common standard |

### 3.1 Search nearby stations and prices — FR1, FR4, QR1

`GET /v1/gas-stations`

| Query parameter | Required | Constraints / default |
|---|---|---|
| `latitude`, `longitude` | Yes | Finite WGS 84 decimal coordinates, `[-90,90]` and `[-180,180]` |
| `radius_m` | Yes | `0 < radius_m <= 25000` metres |
| `fuel_type` | No | One supported fuel code; omitting returns all catalogued grades |
| `min_price_per_litre`, `max_price_per_litre` | No | CAD/L, `0.001–20.000`, max three decimals; require `fuel_type` when used; min must be ≤ max |
| `include_stale` | No | Boolean, default `true`; stale quotes are included with explicit flags |
| `sort_by` | No | `distance_m` (default), `name`, `price_per_litre`, `submitted_at`; sorting by price/report time requires `fuel_type` |
| `sort_order` | No | `asc` (default) or `desc` |
| `limit`, `offset` | No | Shared `1–100` / `>=0` conventions |

A `fuel_type` without a price filter returns stations catalogued as offering that grade even if no reported price is available. A **price filter** includes only stations with an eligible quote for the requested fuel that meets the price range. For price/time sorting, stations without an eligible quote sort last, with `station_id` as the final tie-breaker. `include_stale=false` suppresses old quotes but does not suppress a station solely because its report is old. `price_coverage_status` is `available`, `partial`, `stale_only`, or `unavailable`.

**Example request**

```http
GET /v1/gas-stations?latitude=43.0096&longitude=-81.2737&radius_m=2500&fuel_type=regular_87&max_price_per_litre=1.700&sort_by=price_per_litre&sort_order=asc&limit=25&offset=0
```

**200 response (synthetic information; not an actual station price)**

```json
{
  "items": [{
    "station_id": "station-example-001", "name": "Example Fuel Station",
    "address": "123 Example Road, London, ON", "latitude": 43.0102,
    "longitude": -81.2702, "distance_m": 300,
    "available_fuel_types": ["regular_87", "premium_91"],
    "prices": [{
      "fuel_type": "regular_87", "price_per_litre": 1.659,
      "currency": "CAD", "price_unit": "CAD/L",
      "price_report_id": "a4b2c3d4-1111-4222-8333-444455556666",
      "submitted_at": "2026-11-10T17:45:00Z", "observed_at": "2026-11-10T17:40:00Z",
      "freshness_basis": "observed_at", "verification_status": "unverified",
      "source": "user_report", "data_status": "user_report",
      "is_live": false, "is_stale": false
    }],
    "last_accepted_report_at": "2026-11-10T17:45:00Z",
    "price_coverage_status": "available", "is_stale": false
  }],
  "pagination": {"limit": 25, "offset": 0, "total": 1, "has_more": false},
  "as_of": "2026-11-10T18:00:00Z", "source": "station_catalog_and_user_reports",
  "data_status": "user_report", "is_live": false, "is_stale": false
}
```

A valid geographic query with no matching stations returns `200`, `items: []`, and ordinary pagination when the station catalog is usable. `source` identifies the general catalog/report process, not a confirmed external provider.

### 3.2 Retrieve selected station details — FR1, FR3 (timestamp)

`GET /v1/gas-stations/{station_id}` accepts the path identifier and optional `include_stale` (default `true`). Its **200 response** includes the same station fields as a search item, except `distance_m` is `null` because no search centre was supplied. `prices` includes the latest accepted report per fuel, subject to `include_stale`; the record's `last_accepted_report_at` is the most recent accepted report timestamp across all fuels. Unknown `station_id` returns `404 NOT_FOUND`.

**Example request**

```http
GET /v1/gas-stations/station-example-001
```

**200 response (synthetic information)**

```json
{
  "station_id": "station-example-001", "name": "Example Fuel Station",
  "address": "123 Example Road, London, ON", "latitude": 43.0102,
  "longitude": -81.2702, "distance_m": null,
  "available_fuel_types": ["regular_87", "premium_91"],
  "prices": [{
    "fuel_type": "regular_87", "price_per_litre": 1.659,
    "currency": "CAD", "price_unit": "CAD/L",
    "price_report_id": "a4b2c3d4-1111-4222-8333-444455556666",
    "submitted_at": "2026-11-10T17:45:00Z", "observed_at": "2026-11-10T17:40:00Z",
    "freshness_basis": "observed_at", "verification_status": "unverified",
    "source": "user_report", "data_status": "user_report",
    "is_live": false, "is_stale": false
  }],
  "last_accepted_report_at": "2026-11-10T17:45:00Z",
  "price_coverage_status": "available", "as_of": "2026-11-10T18:00:00Z",
  "source": "station_catalog_and_user_reports", "data_status": "user_report",
  "is_live": false, "is_stale": false
}
```

A station without accepted price reports returns `prices: []`, `last_accepted_report_at: null`, and `price_coverage_status: "unavailable"`.

### 3.3 Compare prices in an area — FR3 (comparison), QR1

`GET /v1/gas-prices/comparisons`

Required `latitude`, `longitude`, `radius_m`, **`fuel_type`**. Optional: `include_stale=false`, `sort_by=price_per_litre|distance_m|submitted_at` (default `price_per_litre`), `sort_order=asc|desc` (default `asc`), `limit=25`, `offset=0`. Geographic constraints match §3.1. Every included station has one latest eligible quote for the requested fuel. Stale quotes are excluded by default, or included with `is_stale: true` when the client explicitly opts in.

**Example request**

```http
GET /v1/gas-prices/comparisons?latitude=43.0096&longitude=-81.2737&radius_m=2500&fuel_type=regular_87&sort_by=price_per_litre&sort_order=asc
```

**200 response (synthetic information)**

```json
{
  "fuel_type": "regular_87", "currency": "CAD", "price_unit": "CAD/L",
  "items": [
    {"station_id": "station-example-001", "name": "Example Fuel Station",
     "distance_m": 300, "quote": {
       "fuel_type": "regular_87", "price_per_litre": 1.659,
       "currency": "CAD", "price_unit": "CAD/L",
       "price_report_id": "a4b2c3d4-1111-4222-8333-444455556666",
       "submitted_at": "2026-11-10T17:45:00Z", "observed_at": "2026-11-10T17:40:00Z",
       "freshness_basis": "observed_at", "verification_status": "unverified",
       "source": "user_report", "data_status": "user_report",
       "is_live": false, "is_stale": false
     }},
    {"station_id": "station-example-002", "name": "Second Example Station",
     "distance_m": 750, "quote": {
       "fuel_type": "regular_87", "price_per_litre": 1.699,
       "currency": "CAD", "price_unit": "CAD/L",
       "price_report_id": "b5c6d7e8-1111-4222-8333-555566667777",
       "submitted_at": "2026-11-10T17:35:00Z", "observed_at": null,
       "freshness_basis": "submitted_at", "verification_status": "unverified",
       "source": "user_report", "data_status": "user_report",
       "is_live": false, "is_stale": false
     }}
  ],
  "pagination": {"limit": 25, "offset": 0, "total": 2, "has_more": false},
  "summary": {"eligible_station_count": 2, "min_price_per_litre": 1.659,
              "max_price_per_litre": 1.699, "mean_price_per_litre": 1.679,
              "spread_per_litre": 0.040, "comparison_status": "available"},
  "as_of": "2026-11-10T18:00:00Z", "source": "user_reports",
  "data_status": "user_report", "is_live": false, "is_stale": false
}
```

For zero or one eligible station, the response still succeeds with `items` and `pagination`, but sets `comparison_status: "insufficient_data"` and all comparison statistics to `null`. When at least two eligible stations exist, `comparison_status: "available"`, summary statistics use their reported prices, and `spread_per_litre = max - min`. The mean is the **unweighted arithmetic mean of one eligible quote per station**. This is a comparison of reports, not a promise of checkout prices. Missing price coverage must never be represented as a numerical zero. A provider/catalog outage must not be disguised as a truthful empty result.

### 3.4 Submit a new price report — FR2, FR3 (timestamp), QR2, QR4

`POST /v1/gas-stations/{station_id}/price-reports` requires a verified Bearer token, an `Idempotency-Key`, and a JSON body. The `station_id` in the URL identifies the known station; no client-supplied owner, submission timestamp, verification state, or station ID is accepted in the JSON body.

**Example request**

```http
POST /v1/gas-stations/station-example-001/price-reports
Authorization: Bearer <access_token>
Idempotency-Key: gas-report-20261110-a1
Content-Type: application/json
```

```json
{
  "fuel_type": "regular_87",
  "price_per_litre": 1.659,
  "currency": "CAD",
  "observed_at": "2026-11-10T17:40:00Z"
}
```

| Field | Required | Validation |
|---|---|---|
| `fuel_type` | Yes | One of five supported codes |
| `price_per_litre` | Yes | Numeric CAD/L; `0 < p <= 20`, max 3 fractional digits |
| `currency` | Yes | Literal `CAD` |
| `observed_at` | No | Aware ISO 8601 time; no more than 7 days in past or 5 minutes in future relative to request receipt |

The service validates a known station, all required fields and types, supported fuel category, decimal price precision, and time bounds **before accepting**. The server generates `price_report_id` (UUID), `submitted_at` and private owner attribution. `observed_at` is preserved if supplied, otherwise stored as `null`; the server must not pretend its acceptance time was the user's measurement time.

**201 Created response (synthetic)**

```json
{
  "price_report_id": "a4b2c3d4-1111-4222-8333-444455556666",
  "station_id": "station-example-001",
  "fuel_type": "regular_87", "price_per_litre": 1.659,
  "currency": "CAD", "price_unit": "CAD/L",
  "observed_at": "2026-11-10T17:40:00Z",
  "submitted_at": "2026-11-10T17:45:00Z",
  "verification_status": "unverified", "source": "user_report",
  "data_status": "user_report", "is_live": false,
  "is_stale": false
}
```

Return `Location: /v1/gas-stations/{station_id}/price-reports/{price_report_id}` as the canonical resource identifier only if a future read-by-report operation exists; in v1, **do not emit a Location header pointing to an undocumented route**. The 201 response includes the generated report ID without disclosing the reporter's identity.

**Submission idempotency:** `Idempotency-Key` is required, 8–128 letters/digits/underscores/hyphens; scoped to authenticated identity, station ID and create operation for 24 hours. Identical replay under the same key returns the **same original 201 response and report ID**; reuse with a different canonical body returns `409 IDEMPOTENCY_KEY_CONFLICT`. Different keys may create distinct reports, even for the same station/grade/price. The service must durably commit report, timestamp, owner association, and idempotency mapping before acknowledging success. Requests for an unknown station return `404`, not an automatically created station.

## 4. Errors, security, readiness and operational behavior

- Errors `400`, `401`, `403`, `404`, `409`, `422`, `429`, `500`, `502`, `503`, `504` use the common `error` object with `code`, `message`, `request_id`, `details`, `retryable`, `source`; relevant statuses are specified per operation in OpenAPI.
- Missing/invalid bearer credentials on report creation return `401`; authenticated identities disallowed from reporting return `403`. Public reads need no bearer token. Missing station IDs return `404`; invalid report bodies and contradictory price filters return `422`.
- Requests that exceed known rate limits return `429` and SHOULD provide `Retry-After`. All network/provider operations use finite timeouts and bounded retries only when replay-safe. No client-supplied personal identity is trusted from request JSON.
- `/health` returns `200 {"service":"gasoline-analytics","status":"healthy"}` while the process can respond without expensive dependency probes. `/ready` returns `200 {"service":"gasoline-analytics","status":"ready"}` only when the **S7-owned persistent station catalog and report storage are readable and writable**, including a usable persisted catalog during external feed outages; otherwise `503` with the error envelope. Monitor upstream provider degradation separately from process readiness.
- S7 QR1 targets **≤1,500 ms** for valid requests retrieving available gasoline prices (including geographic search and comparison). Performance measurement begins on receipt and includes filtering, sorting, pagination and response serialization. No separate S7 concurrency target is specified in PR1.

## 5. Service-owned data and external dependencies

| Entity / collection | Key attributes and ownership | Storage proposal |
|---|---|---|
| `GasStation` | `station_id`, name, address, coordinates, catalogued fuel types, catalog update/source metadata; owned by S7 | S7-owned relational station catalog; spatial index (e.g. PostgreSQL + PostGIS) |
| `PriceReport` | `price_report_id`, station/fuel/price, user-observed time (nullable), accepted time, private verified owner identity, report review status; owned by S7 | Durable append-only reports with indexes on station/fuel/submission time |
| `CurrentPriceByFuel` | Latest accepted report ID and displayed quote for each station/fuel; owned by S7 | Derivable read projection/materialized view; recalculated from durable reports |
| `SubmissionIdempotency` | Scoped idempotency key, canonical body fingerprint, initial response/record ID, expiry; owned by S7 | S7 durable store (24-hour active retention) |
| Provider data/cache | Normalized station metadata and any *future approved* external price-feed provenance; owned after ingestion by S7 | Time-bounded source-aware cache, independent of Service Registry storage |

`GasStation` to `PriceReport` is one-to-many. One station has zero or more accepted reports per fuel; `CurrentPriceByFuel` selects at most one displayed user-reported quote for each `(station_id, fuel_type)` pair. Identity is held in private ownership metadata rather than public responses. No user report is a privileged authority about actual prices. QR4 is verified by restarting the service and confirming accepted reports and displayed quotes are still retrievable.

**External dependency decisions to investigate (not confirmed):**

1. **Station catalog/directory provider:** geographic coordinates, identifiers, offered grades; confirm license, update schedule, API/feed protocol, coverage, rate limits, credentials and fallback to persisted catalog.
2. **Price feed (optional):** existing external gas-price datasets may later supplement user reports, but no provider is chosen; specify source precedence and separate provider/user verification semantics before combining them in one displayed quote.
3. **Authentication/identity:** a trusted access-token issuer and role/permission validation must exist before authenticated submissions can operate; no identities may be derived from request body fields.
4. **Geographic search infrastructure:** geodesic distance support and spatial indexes; use independent S7 data ownership, never the Registry's writable database as a universal domain store.

## 6. Acceptance tests and contract conformance

1. **FR1:** Geographic search within and exactly on radius boundary returns the expected stations, prices if known, accurate source timestamps, and `200` with empty `items` when the known catalog has no matches.
2. **FR4:** Fuel-only, min/max price, inclusive thresholds, and `include_stale` filters work; price filters without a fuel type and reversed min/max values return `422`.
3. **FR3 (comparison):** Comparison uses the selected fuel only, excludes stale quotes by default, computes summary from all eligible stations before pagination, and marks insufficient data without invented zeros.
4. **FR2/QR2:** Reject unknown station ID (`404`), invalid fuel, missing price, zero/negative price, excessive decimal places, out-of-range or timezone-naïve observation time, and client-supplied owner/submission-time fields (`422`).
5. **FR3 (timestamp)/QR3:** The server assigns `submitted_at`, preserves optional `observed_at`, updates `last_accepted_report_at` after successful acceptance, and never represents a new submission time as a fresh physical observation.
6. **QR4:** An acknowledged report, idempotency record, and derived current quote remain available after an S7 restart; duplicate retry returns the original 201 response and same report ID.
7. **Freshness:** At exactly 24 hours the quote isn't stale; beyond 24 hours it is. When observation time is unavailable, report age is labeled as a proxy. Stale quotes are not labeled verified or live.
8. **QR1:** Search and price-comparison operations return available prices within **1,500 ms** for valid requests under documented normal load.
9. **Security:** An anonymous report request receives 401; disallowed authenticated caller 403; no reporter identifiers leak through the public API.
10. **Contract:** Validate the OpenAPI request/response examples and required error/health/readiness structures, and verify `X-Request-ID` propagation.

## 7. Contract acceptance checklist

- [ ] Logical service ID and API endpoint inventory traceable to both PR1 FR3 entries
- [ ] Paths, methods, parameter/body schemas, types, units, examples and errors
- [ ] Authentication, idempotency, timestamp/provenance and freshness policies
- [ ] Geographic price filtering and comparison summary semantics
- [ ] Data ownership, storage proposal, upstream dependencies and fallback behavior
- [ ] QR1–QR4 acceptance targets mapped to observable behavior
- [ ] Station directory, user identity issuer, optional provider feeds and licenses confirmed (implementation prerequisites)
- [ ] Executable conformance, restart durability and performance tests implemented (implementation phase)

## 8. Change control

This contract is the S7 version 1 **implementation target**; it deliberately does not cite current Python implementation files. Breaking changes to endpoints, accepted fuel types, price units/currency, submission semantics or response schemas require contract review and a migration/versioning decision under `API-Standards.md`. Downstream clients and tests must follow the approved contract.
