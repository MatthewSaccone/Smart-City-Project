# S3 — Construction & Incident Reporting: API Contract

| Property | Value |
|---|---|
| Document ID | `T008-S3-API` |
| Version | `1.0` (initial contract) |
| Status | Adopted API design; identity provider and persistent storage implementation are integration dependencies |
| Logical service ID | `construction-incidents` (S3) |
| API version | `/v1` |
| Governing conventions | `docs/api/API-Standards.md` |
| Machine-readable specification | `shared/api-contracts/s3-construction-incidents.openapi.yaml` |

## 1. Purpose and scope

S3 manages user-submitted reports of **road construction, accidents, and other traffic-related incidents**. It supports geographic search, report details, persistent submissions, controlled updates, resolution/completion/inaccuracy marking, and withdrawal. Unlike S1 and S2, S3 is a **stateful, user-authenticated reporting service**: it owns durable report records and must enforce per-report ownership rules. Public report reads do **not** reveal reporter identity.

The interface is a design contract for future implementations; it does not claim that all endpoints already exist. Unknown external incident feeds are not assumed to be integrated. Official feeds could be a future ingestion source with separately defined provenance and contracts.

### Requirements traceability (PR1, Service Requirements, S3)

| PR1 requirement | Required capability or quality target | Contract coverage |
|---|---|---|
| **FR1** | Submit construction, accident, and other traffic-related incident reports | `POST /v1/incidents` |
| **FR2** | Store location, incident type, submission time, and description | `IncidentReport`; server-generated `submitted_at` |
| **FR3** | Retrieve reports within a user-defined radius | `GET /v1/incidents` with centre coordinates and `radius_m` |
| **FR4** | Retrieve detailed information for a selected report | `GET /v1/incidents/{incident_id}` |
| **FR5** | Allow users to update or withdraw reports they submitted | `PATCH` and `DELETE /v1/incidents/{incident_id}` |
| **FR6** | Mark incidents resolved, completed, or inaccurate | Owner/moderator `PATCH` with `status` |
| **QR1** | Retrieve available incident reports within **1,500 ms** of a valid request | List/detail read latency acceptance tests |
| **QR2** | Validate required report fields before accepting submissions | `IncidentCreateRequest` and cross-field validation |
| **QR3** | Modify or withdraw only as owner or authorized moderator | Trusted bearer identity + resource ownership check |
| **QR4** | Successfully submitted reports remain accessible after service restart | S3-owned persistent store and restart/recovery test |
| **QR5** | Modify, update, and remove reports within **500 ms** of a request | PATCH/DELETE latency acceptance tests |

## 2. Reporting model and shared semantics

**Incident categories:** `construction`, `accident`, `other_traffic` (T008-controlled enum). **Report statuses:** `active`, `resolved`, `completed`, `inaccurate`, `withdrawn`. Newly accepted reports begin as `active`. `resolved`, `completed`, and `inaccurate` are user/moderator assessments of the report, **not independent verification of ground truth**.

- The report identifier `incident_id` is a **server-generated UUID** represented as an opaque string. Clients must not infer ownership or status from it.
- The service assigns `submitted_at` at successful creation and `updated_at` at the most recent actual modification. Both are UTC ISO 8601 timestamps with `Z`. Optional `observed_at` is supplied by the reporting user and represents when the event was noticed, **not** when the report was submitted.
- Location is WGS 84 decimal degrees. Geospatial search uses great-circle distance from the specified centre to the report coordinates; results within the radius, **including the boundary**, qualify.
- Reports are **user-submitted observations** (`source: "user_report"`, `is_live: false`, `data_status: "user_report"`), not continuously refreshed telemetry or verified official feeds. `is_stale` is computed for an *active* report when `as_of - updated_at` exceeds **7 days**. This is an explicit **T008 review-age convention**, not a PR1 requirement and not proof that newer reports are accurate. Non-active reports are historical states and have `is_stale: false`.
- `as_of` denotes the server's UTC reference time for computing the freshness flag. The service has **no universal 60-second freshness requirement**. An old but still relevant construction report may be returned with `is_stale: true` rather than silently deleted.
- The service retains report history and status/ownership audit records privately. Reporter/owner identifiers and authorization details are **not disclosed in public responses**. Published description text must be treated as untrusted user content by clients.

### Status lifecycle and ownership

| Current status | Supported action | Next status | Authorized caller |
|---|---|---|---|
| `active` | Resolve, complete, or mark inaccurate | `resolved`, `completed`, or `inaccurate` | Report owner or moderator |
| `resolved`, `completed`, `inaccurate` | Reopen after correction | `active` | Report owner or moderator |
| Any non-withdrawn status | Withdraw using `DELETE` | `withdrawn` | Report owner or moderator |
| `withdrawn` | Edit/reopen through public API | Not permitted | None |

No status transition is inferred from the report's age. Setting an already-current field to its existing value is a no-op, without a new modification timestamp. Changing to an unsupported status or forbidden transition returns `422` or `409`, respectively. Modifying, withdrawing, or marking a report in any way is restricted under S3 QR3, including any transition to `inaccurate`.

## 3. Endpoint inventory

| Operation ID | Method and path | Purpose | Access | Success |
|---|---|---|---|---|
| `listIncidents` | `GET /v1/incidents` | Geographic/filterable report search (FR3) | Public | `200` |
| `getIncident` | `GET /v1/incidents/{incident_id}` | Retrieve one non-withdrawn report (FR4) | Public | `200` |
| `createIncident` | `POST /v1/incidents` | Submit and persist a report (FR1–FR2) | Bearer-authenticated user | `201` |
| `updateIncident` | `PATCH /v1/incidents/{incident_id}` | Edit fields or report status (FR5–FR6) | Verified owner or moderator | `200` |
| `withdrawIncident` | `DELETE /v1/incidents/{incident_id}` | Withdraw report (FR5) | Verified owner or moderator | `204` |
| `getHealth` | `GET /health` | Liveness | Monitoring access | `200` |
| `getReadiness` | `GET /ready` | Readiness | Monitoring access | `200` or `503` |

Every response with a body uses JSON, and **all responses** include `X-Request-ID` as defined by the common standards. No request body is accepted for either GET operation or DELETE.

### 3.1 Create an incident — FR1, FR2, QR2, QR4

`POST /v1/incidents` requires a trusted `Authorization: Bearer <access_token>` header and **`Idempotency-Key`**. The service derives ownership from verified identity; a supplied `owner_id`, `user_id`, `submitted_at`, or `status` is rejected as an undeclared JSON field.

**Request example:**

```http
POST /v1/incidents
Authorization: Bearer <access_token>
Idempotency-Key: incident-create-20261110-a1
Content-Type: application/json
```

```json
{
  "incident_type": "construction",
  "location": {
    "latitude": 43.0096,
    "longitude": -81.2737
  },
  "description": "Road construction is blocking the westbound lane near the intersection.",
  "observed_at": "2026-11-10T17:52:00Z"
}
```

| Body field | Required? | Validation | Meaning |
|---|---|---|---|
| `incident_type` | Yes | `construction`, `accident`, or `other_traffic` | Category of report |
| `location` | Yes | Required `latitude` `[-90,90]` and `longitude` `[-180,180]`; finite numbers | Geographic location |
| `description` | Yes | String, 10–2,000 characters after surrounding whitespace is trimmed; cannot consist only of whitespace | Description of the incident |
| `observed_at` | No | Timezone-aware ISO 8601 instant, no more than five minutes in the future relative to receipt | When the reporter noticed the event |

`observed_at` may be earlier than `submitted_at` and has no maximum lookback in this version. A submission missing required fields, with unexpected fields, or with invalid values fails validation (`422`); syntactically malformed requests return `400`.

**201 Created response example:**

```json
{
  "incident_id": "e0b7286a-789d-4c76-b640-3f1c77f8029b",
  "incident_type": "construction",
  "status": "active",
  "location": {
    "latitude": 43.0096,
    "longitude": -81.2737
  },
  "description": "Road construction is blocking the westbound lane near the intersection.",
  "observed_at": "2026-11-10T17:52:00Z",
  "submitted_at": "2026-11-10T18:00:00Z",
  "updated_at": "2026-11-10T18:00:00Z",
  "source": "user_report",
  "is_live": false,
  "is_stale": false,
  "data_status": "user_report",
  "as_of": "2026-11-10T18:00:00Z"
}
```

Response includes `Location: /v1/incidents/e0b7286a-789d-4c76-b640-3f1c77f8029b` (the actual UUID). The service must commit report data and private ownership metadata durably **before** returning success. Server-generated timestamps are never trusted from the request body.

**Duplicate / retry policy:** `Idempotency-Key` must be 8–128 characters containing letters, digits, `_` or `-`. Keys are scoped to the verified user and create-operation path, retained for **24 hours**. Repeating an identical request with the same key returns the **same initial `201` response and report ID**; reusing the key with a different canonical request body returns `409 RESOURCE_CONFLICT` (specific code `IDEMPOTENCY_KEY_CONFLICT`). Requests with different keys can create separate reports even if their geography and description coincide; no speculative geographic de-duplication is performed. After key expiry, a repeated request may create a new report. Clients should persist a key while retrying an uncertain creation outcome.

### 3.2 Search incidents within a radius — FR3, QR1

`GET /v1/incidents`

| Query field | Required? | Type / constraints | Default |
|---|---|---|---|
| `latitude` | Yes | Finite decimal latitude `[-90,90]` | — |
| `longitude` | Yes | Finite decimal longitude `[-180,180]` | — |
| `radius_m` | Yes | Positive finite metres, `0 < radius_m <= 25000` | — |
| `incident_type` | No | `construction`, `accident`, `other_traffic` | All types |
| `status` | No | `active`, `resolved`, `completed`, `inaccurate`, `all` | `active` |
| `sort_by` | No | `submitted_at`, `updated_at`, `distance_from_center_m` | `submitted_at` |
| `sort_order` | No | `asc` or `desc` | `desc` |
| `limit` | No | Integer 1–100 | 25 |
| `offset` | No | Nonnegative integer | 0 |

The **25,000 m maximum radius** is a T008 API design limit, not specified by PR1. A public search **never includes withdrawn reports**. `status=all` means every non-withdrawn status. Filter first, sort next, then paginate; break equal-sort ties by `incident_id` ascending to keep ordering deterministic for unchanged data. The `distance_from_center_m` field is geodesic distance, not travel distance. Empty result sets return `200` with empty `items`.

**Example request:**

```http
GET /v1/incidents?latitude=43.0096&longitude=-81.2737&radius_m=1500&status=active&sort_by=submitted_at&sort_order=desc&limit=25&offset=0
```

**200 response example:**

```json
{
  "items": [
    {
      "incident_id": "e0b7286a-789d-4c76-b640-3f1c77f8029b",
      "incident_type": "construction",
      "status": "active",
      "location": {
        "latitude": 43.0096,
        "longitude": -81.2737
      },
      "submitted_at": "2026-11-10T18:00:00Z",
      "updated_at": "2026-11-10T18:00:00Z",
      "source": "user_report",
      "is_live": false,
      "is_stale": false,
      "data_status": "user_report",
      "distance_from_center_m": 130.0
    }
  ],
  "pagination": {
    "limit": 25,
    "offset": 0,
    "total": 1,
    "has_more": false
  },
  "as_of": "2026-11-10T18:00:00Z"
}
```

The `pagination.total` value is the exact count of matching non-withdrawn reports after filters and before offset/limit. If a future backend cannot compute it reliably, it must return `null` and calculate `has_more` with a look-ahead row, as permitted by the common standard. Each item has its own stale flag; the list's `as_of` supplies a shared age-reference time. No anonymous user's identity is included.

### 3.3 Retrieve one report — FR4, QR1

`GET /v1/incidents/{incident_id}` returns the complete non-withdrawn public report representation in `200 OK`:

```json
{
  "incident_id": "e0b7286a-789d-4c76-b640-3f1c77f8029b",
  "incident_type": "construction",
  "status": "active",
  "location": {
    "latitude": 43.0096,
    "longitude": -81.2737
  },
  "description": "Road construction is blocking the westbound lane near the intersection.",
  "observed_at": "2026-11-10T17:52:00Z",
  "submitted_at": "2026-11-10T18:00:00Z",
  "updated_at": "2026-11-10T18:00:00Z",
  "source": "user_report",
  "is_live": false,
  "is_stale": false,
  "data_status": "user_report",
  "as_of": "2026-11-10T18:00:00Z"
}
```

`incident_id` must be a valid UUID string, and the server performs an exact lookup. A valid UUID not associated with a visible report returns `404 NOT_FOUND`. Withdrawn reports also return `404` to public read clients; they remain in the private durable audit store rather than being hard-deleted. Invalid UUID syntax returns `422`.

### 3.4 Update a report or mark its status — FR5, FR6, QR3, QR5

`PATCH /v1/incidents/{incident_id}` requires verified bearer identity and checks that the requester is the **report owner or an authorized moderator**, regardless of whether the request changes text, location, type, or status.

**Request example:**

```http
PATCH /v1/incidents/e0b7286a-789d-4c76-b640-3f1c77f8029b
Authorization: Bearer <access_token>
Content-Type: application/json
```

```json
{
  "description": "Westbound lane remains closed; a signed detour is available.",
  "status": "resolved"
}
```

The patch body accepts one or more of `incident_type`, `location`, `description`, and `status`. It must contain **at least one** of these fields. Values use the same constraints as creation; a PATCH cannot set `status=withdrawn`, reassign ownership, or change server timestamps. A status-only PATCH supports FR6.

**200 response example:**

```json
{
  "incident_id": "e0b7286a-789d-4c76-b640-3f1c77f8029b",
  "incident_type": "construction",
  "status": "resolved",
  "location": {
    "latitude": 43.0096,
    "longitude": -81.2737
  },
  "description": "Westbound lane remains closed; a signed detour is available.",
  "observed_at": "2026-11-10T17:52:00Z",
  "submitted_at": "2026-11-10T18:00:00Z",
  "updated_at": "2026-11-10T18:15:00Z",
  "source": "user_report",
  "is_live": false,
  "is_stale": false,
  "data_status": "user_report",
  "as_of": "2026-11-10T18:15:00Z"
}
```

- An authenticated non-owner who is not a moderator receives `403 FORBIDDEN` for an existing non-withdrawn report. A missing report returns `404`. A withdrawn report cannot be edited and returns `409 RESOURCE_CONFLICT` to its verified owner/moderator, without exposing it to a non-owner.
- If a patch makes no actual change, the service returns the unchanged representation and does not append a duplicate audit transition.
- PATCH is not promised to be safe for blind automatic retries after a network timeout; a client should retrieve the report before retrying an uncertain update. All actual modifications are committed atomically, and the old and new values are recorded in private audit history.

### 3.5 Withdraw a report — FR5, QR3, QR5

`DELETE /v1/incidents/{incident_id}` requires bearer authentication and report-owner/moderator authorization. A successful withdrawal returns **`204 No Content` with an empty body**.

Withdrawal is a **durable soft deletion**: the report becomes `withdrawn`, is excluded from public search, and its public detail URL returns `404`. Its private record and audit trail remain for operational accountability. Repeating DELETE by the same authorized owner/moderator returns `204` without additional changes; a non-owner cannot use DELETE to infer a withdrawn report's contents. A missing UUID returns `404` and malformed UUID syntax returns `422`.

## 4. Validation, errors, and security

All error bodies use the shared `error` envelope, with `code`, safe `message`, `request_id`, `details`, `retryable`, and `source`. Response header `X-Request-ID` must equal the body's correlation ID on errors.

**Example validation error:**

```json
{
  "error": {
    "code": "VALIDATION_FAILED",
    "message": "Latitude must be between -90 and 90.",
    "request_id": "req-example-001",
    "details": [
      {
        "field": "location.latitude",
        "reason": "out_of_range"
      }
    ],
    "retryable": false,
    "source": "service"
  }
}
```

| HTTP | Standard/specific application code | Condition |
|---|---|---|
| `400` | `BAD_REQUEST` | Malformed request syntax or structure |
| `401` | `AUTHENTICATION_REQUIRED` | Missing, expired, or invalid bearer credentials on write operations |
| `403` | `FORBIDDEN` | Authenticated requester is neither owner nor moderator |
| `404` | `NOT_FOUND` | Report missing or withdrawn from public visibility |
| `409` | `RESOURCE_CONFLICT` / `IDEMPOTENCY_KEY_CONFLICT` | Invalid current status for modification, or reused creation key with a different payload |
| `422` | `VALIDATION_FAILED` | Invalid UUID, coordinates, radius, report fields, status transition, or request body |
| `429` | `RATE_LIMITED` | Client/gateway rate limit exceeded |
| `500` | `INTERNAL_ERROR` | Unexpected service failure |
| `502` | `UPSTREAM_FAILURE` | Required identity/dependency provider returned an unusable response |
| `503` | `SERVICE_UNAVAILABLE` | Persistent storage or indispensable security dependency unavailable |
| `504` | `UPSTREAM_TIMEOUT` | Required external identity/dependency service timed out |

All write operations must validate the bearer token via the trusted platform identity mechanism. The verified principal is compared to private stored ownership, and role claims are trusted only when issued/verified by the approved authority. Unknown users are not silently treated as authenticated. Read paths are public only for **non-withdrawn** report content. Validate and bound user descriptions; frontend clients must escape/sanitize display of untrusted text. Rate limiting, quotas, and abuse moderation should apply to report submissions.

## 5. Persistence, ownership, and external dependencies

**S3-owned data entities** (initial design): `IncidentReport` (ID, category, coordinates, description, status, reporter owner ID, event/submission/update timestamps), `IncidentAuditEvent` (incident ID, authorized actor ID, action, previous/new status and change metadata), and `IdempotencyRecord` (verified user scope, operation, key, request fingerprint, original response, expiry). Internal ownership and audit fields are **never public report response fields**.

**Persistence:** S3 must use a durable **service-owned database** with transactional changes and geographic index support; PostgreSQL with PostGIS is a proposed storage technology. It is **not** the Service Registry's database. Reports accepted with `201` and their ownership metadata must remain accessible after the S3 process/container restarts (QR4). Withdrawn reports remain durable private records; a finite retention/privacy policy for audit and sensitive user-submitted content must be documented before deployment.

**External/trusted dependencies:** a platform-approved identity provider or token verifier is necessary for authenticated writes. Report submissions themselves do not require a live third-party construction feed. Municipal roadwork/incident datasets are **candidates to investigate**, not assumed integrations. If added, their retrieval method, licensing, data update cadence, rate limits, identifiers, trust classification, and failure behavior must be specified in the separate external-dependency inventory. External submissions must not bypass S3's ownership or moderation policy.

**Readiness:**

- `GET /health` returns `200` and `{ "service": "construction-incidents", "status": "healthy" }` if the process responds, without expensive dependency checks.
- `GET /ready` returns `200` and `{ "service": "construction-incidents", "status": "ready" }` only when report storage is accessible for necessary read/write operations and trusted token verification is available (including valid cached key material where approved). Otherwise return `503` with the common error envelope.
- A separate, optional external municipal feed being unavailable **does not** make user-submitted reporting unready when report storage and auth work.

## 6. Performance and verification

1. **S3 QR1:** `GET /v1/incidents` and `GET /v1/incidents/{incident_id}` must complete within **1,500 ms** after receipt of a valid request. Measurement boundary: S3 HTTP ingress to complete HTTP response, including persistent lookup; state representative result count and load in test evidence.
2. **S3 QR5:** `PATCH` and `DELETE` must complete within **500 ms** of a valid request, including authorization and durable commit. This requirement does not invent a 500 ms target for `POST`, which PR1 does not state.
3. **S3 QR2:** Missing/invalid inputs must be rejected before a report is committed; malformed and conflicting submissions must never leave partial records.
4. **S3 QR3:** Verify that a non-owner, non-moderator cannot modify, resolve, mark inaccurate, or withdraw a report, even if they supply an arbitrary user ID in the body or headers.
5. **S3 QR4:** Restart the service process/container, reconnect to its service-owned store, and prove previously accepted reports and their ownership/status remain accessible and correct.
6. Verify public reads do not expose withdrawn records or the owner ID; duplicate-key replays create only one record; status transitions and audit history persist.
7. Verify radius boundary, stable ordering, 25/100 pagination conventions, UTF-8 description handling, UTC normalization, structured errors, rate limits, and readiness behavior.

## 7. Contract acceptance checklist

- [ ] OpenAPI 3.1 parses and its local `$ref`s resolve; all documented examples validate against their schemas.
- [ ] Every S3 FR1–FR6 and QR1–QR5 is traced to an endpoint, state rule, or quality test.
- [ ] Public list/detail exclude withdrawn reports; report ownership stays private.
- [ ] Create requires authentication and idempotency; accepted records are durable before returning `201`.
- [ ] PATCH and DELETE enforce verified ownership or authorized moderator role; status changes are auditable.
- [ ] Status, timestamp, geospatial, and body validation reject invalid requests consistently.
- [ ] List filtering, sorting, tie-breaker, pagination, and 7-day active-report review-age flag behave as specified.
- [ ] QR1 1,500 ms, QR5 500 ms, and QR4 restart persistence are verified with repeatable tests.
- [ ] Identity provider/token claims, database/storage technology, report retention/privacy policy, and optional external incident-feed choice are recorded in T008 dependency/ownership documentation before deployment.

## 8. Contract change policy

The Markdown document and its OpenAPI companion jointly define the **implementation target**. If prose and schema disagree, reconcile both before implementation. Breaking API changes require an approved version/migration plan under `API-Standards.md`. This contract does **not** describe pre-existing source-code behavior.
