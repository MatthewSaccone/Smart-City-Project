# Smart City Platform — Common API Standards

| Document property | Value |
|---|---|
| Document ID | T008-API-STD |
| Version | 1.0 |
| Status | Adopted standard — binding for S1–S7 |
| Applies to | Public and internal HTTP/JSON APIs of the seven Smart City domain services |
| Related deliverables | Individual S1–S7 API contracts; service-owned data models; external dependency specifications |

## 1. Purpose and conformance

This document defines the **common API contract** for the Smart City Platform. Its purpose is to ensure that clients can communicate consistently with any of the seven domain microservices, regardless of the technology used to implement them.

All S1–S7 API implementations **MUST conform to this document**. The service-specific OpenAPI contracts define their particular endpoints, schemas, data sources, access policies, and quality requirements. Where a service has a more specific approved requirement, its OpenAPI contract **MUST document that requirement explicitly** without silently changing a platform-wide convention.

The terms **MUST**, **SHOULD**, and **MAY** mean required, recommended, and optional, respectively. Examples in this document illustrate the required conventions; they do not declare that an endpoint has already been built.

The Service Registry and other infrastructure components may have additional administrative protocols. Their externally exposed HTTP interfaces SHOULD follow the general conventions in this document where applicable. Infrastructure credentials MUST NOT be treated as proof of end-user identity.

## 2. URL paths, resources, and versioning

1. Every domain endpoint **MUST** use a versioned path starting with `/v1/` for the first public contract version. Example: `/v1/parking/facilities`.
2. URL segments **MUST** be lowercase. Multiword segments **MUST** use `kebab-case`, such as `/gas-stations`.
3. Resource collections **SHOULD** use plural nouns, such as `/v1/incidents`. A single resource **SHOULD** be addressed by a stable path identifier, such as `/v1/incidents/{incident_id}`.
4. Paths **SHOULD** describe resources rather than HTTP operations; use `GET /v1/incidents` instead of `GET /v1/getIncidents`.
5. Path parameters **MUST** identify a resource or hierarchical scope. Query parameters **MUST** be used for optional search, filtering, sorting, pagination, and other non-resource-identifying inputs.
6. Structured submissions and processing requests **SHOULD** use a JSON request body.
7. Operational endpoints `/health` and `/ready` **MUST** remain outside API versioning.
8. API versions describe the **interface contract**, not the software release version. Backward-incompatible changes **MUST** be explicitly versioned or introduced through an approved migration plan. Additive changes MUST be reviewed for client compatibility.
9. If a gateway rewrites requests internally, it **MUST** preserve the externally documented path and behavior from the client's perspective. Public gateway routing and service-internal routes MUST have a documented mapping.

## 3. HTTP methods and success responses

| Method | Required semantics | Typical successful status |
|---|---|---|
| `GET` | Read a resource or collection without an intended state change. | `200 OK` |
| `POST` | Create a resource or submit a structured processing operation. | `201 Created` for creation; `200 OK` for synchronous processing |
| `PUT` | Replace a resource representation when full replacement is supported. | `200 OK` or `204 No Content` |
| `PATCH` | Apply a partial update to a resource. | `200 OK` or `204 No Content` |
| `DELETE` | Delete or withdraw a resource, subject to the service's rules. | `204 No Content` or documented alternative |

- Every endpoint **MUST** document all successful response codes and whether a response body is included.
- `201 Created` responses **SHOULD** provide the created resource's identifier and a `Location` header when a canonical resource URL exists.
- `204 No Content` responses **MUST NOT** include a response body.
- A `POST` that computes an estimate without creating a resource **SHOULD** return `200 OK` with the result.
- Retrying non-idempotent operations can create duplicate effects. Endpoints that accept write submissions **MUST** specify their duplicate-handling and idempotency policy.

## 4. JSON naming, data types, and units

1. JSON request and response bodies **MUST** use `application/json` unless another media type is expressly specified. No-content responses do not require a JSON body.
2. JSON properties and query parameter names **MUST** use lowercase `snake_case` (for example, `service_id`, `last_updated_at`, `fuel_type`). Enum values **SHOULD** also use lowercase `snake_case` unless a published external standard defines their spelling.
3. Every field **MUST** specify its type, requiredness, constraints, and meaning in the corresponding OpenAPI schema. Optional and nullable are different: a field can be omitted only if declared optional, and can be `null` only if declared nullable.
4. Resource identifiers **MUST** be treated as opaque strings by clients. Each service contract **MUST** specify identifier format and uniqueness scope where relevant.
5. Measurements **MUST** have documented units. The shared defaults for new API fields are:

   | Measurement | Convention |
   |---|---|
   | Latitude / longitude | Decimal degrees, WGS 84 |
   | Distance / radius | Metres (`m`) |
   | Duration / delay | Seconds (`s`), unless the field explicitly names another unit (e.g., `_minutes`) |
   | Temperature | Degrees Celsius (`°C`) |
   | Money | Numeric value plus an explicitly documented ISO 4217 currency code |
   | Fuel prices and air-quality measurements | Service contract **MUST** declare the pricing basis or pollutant measurement unit |

6. Unknown or unavailable numeric measurements **MUST NOT** be represented as an invented zero. Use a documented nullable value and/or an explicit availability status.
7. Responses **SHOULD** distinguish measurements from derived estimates and identify the data source when provenance affects interpretation.

**Illustrative response fragment:**

```json
{
  "incident_id": "incident-123",
  "incident_type": "construction",
  "latitude": 43.0096,
  "longitude": -81.2737,
  "created_at": "2026-11-10T18:00:00Z"
}
```

## 5. Input validation

Validation **MUST** occur before a request changes persistent state or initiates an expensive operation. The API **MUST** validate both individual fields and relationships among fields.

| Category | Required rule |
|---|---|
| Required fields | Reject missing mandatory path, query, header, or body fields. Defaults MUST be explicitly documented. |
| Data types | Reject values that cannot be represented as the declared type without ambiguous coercion. |
| Unknown JSON fields | Reject undeclared properties unless an endpoint explicitly defines an extensible object or metadata field. |
| Latitude | Finite number in the inclusive range `[-90, 90]`. |
| Longitude | Finite number in the inclusive range `[-180, 180]`. |
| Bounding boxes | Require `min_lat <= max_lat` and `min_lon <= max_lon` for ordinary boxes. Antimeridian-crossing searches MUST have separately documented handling. |
| Radius | Positive finite number in metres; each endpoint MUST define its permitted maximum. |
| Time | Valid ISO 8601 timestamp containing explicit timezone information. |
| Enumerations | Accept only values declared in the service contract. |
| Numeric quantities | Enforce documented ranges and units (for example, nonnegative monetary prices and positive capacities, when applicable). |
| Cross-field rules | Reject contradictory combinations even when individual fields pass validation. |
| Duplicate submissions | Document unique keys, replay behavior, and what occurs after repeated submission. |
| Payload limits | Declare maximum request size and collection lengths for endpoints processing large inputs. |

Validation failures involving well-formed requests with unacceptable field values or business constraints **MUST** return `422`. Malformed request syntax or invalid overall HTTP/request structure **MUST** return `400`. All such responses **MUST** use the standard error envelope defined in Section 7.

## 6. Timestamps, freshness, and provenance

### 6.1 Time representation

- All timestamp fields representing instants **MUST** be encoded as ISO 8601/RFC 3339 strings in UTC, using `Z` for output, for example `2026-11-10T18:00:00Z`.
- Requests **MAY** use an explicit numeric timezone offset; services **MUST** normalize accepted instants to UTC for storage and output.
- Timezone-naïve timestamps **MUST** be rejected rather than silently interpreted as UTC.
- Timestamps **MUST** represent distinct events accurately. A service **MUST NOT** substitute ingestion or processing time for measurement time without making the distinction explicit.

| Field | Standard meaning |
|---|---|
| `observed_at` | Time a physical observation or measurement occurred. |
| `received_at` | Time the platform first received the observation. |
| `ingested_at` | Time a service incorporated the observation into its managed dataset; use only when distinct from `received_at`. |
| `source_updated_at` | Time an upstream provider reports updating its dataset. |
| `service_updated_at` | Time the microservice updated its own result or cache. |
| `as_of` | Reference instant used to evaluate a query or derived estimate. |
| `submitted_at` / `updated_at` | Time a submitted resource was accepted or later modified. |

A missing source timestamp **MUST** be represented as unknown, not replaced with an invented value.

### 6.2 Freshness rules

Each service contract **MUST** identify (a) which timestamp determines age, (b) its freshness threshold if applicable, (c) how a stale response is marked, and (d) the fallback when live data is unavailable. **There is no platform-wide 60-second cutoff**: thresholds depend on the service's requirements.

The following service-specific requirements are authoritative for their respective contracts:

| Service | Required freshness and fallback behavior |
|---|---|
| S1 — Live Traffic | Congestion calculations use location data **received within the previous 60 seconds** (S1 QR4). A separate observation-age rule, if needed, MUST be defined explicitly. |
| S4 — Parking | Expose relevant update times for availability/occupancy and fees; document fallback behavior if live occupancy information is unavailable. |
| S5 — Weather & Air Quality | Expose observation/update timestamps; when a provider fails, retain the latest usable data and identify it as outdated (S5 QR2–QR3). |
| S6 — Public Transit | Identify live information **older than 60 seconds** and distinguish scheduled times from live estimates when real-time updates fail (S6 QR2–QR3). |
| S7 — Gasoline Prices | Expose the timestamp of the most recently accepted price report (S7 QR3); any expiry threshold must be explicitly defined in its contract. |
| S2 / S3 | Specify any required age, retention, and historical-data semantics in the respective service contracts. |

### 6.3 Freshness metadata and response behavior

Where a response depends on time-sensitive or simulated data, its contract **MUST** specify:

- `is_stale` (`boolean`): whether the relevant observation/update exceeds the service's declared freshness rule.
- `is_live` (`boolean`): whether the data is sourced from a live updating feed; `false` does not by itself mean stale.
- `source` (`string`): the documented source or provider identifier.
- The appropriate timestamp fields, chosen according to Section 6.1.
- Whether the response is live, cached, simulated, or historical; these categories MUST NOT be conflated.

If outdated or cached information remains usable under the service contract, a service **MAY** return `200 OK` with explicit freshness/fallback metadata. If no acceptable result is available, it **MUST** return a documented error, such as `502`, `503`, or `504` as appropriate. A response **MUST NOT** present historical or simulated data as current live information.

**Illustrative fallback response:**

```json
{
  "temperature_c": 7.5,
  "observed_at": "2026-11-10T11:58:00Z",
  "is_stale": true,
  "is_live": false,
  "source": "weather_provider",
  "data_status": "cached"
}
```

## 7. Standard error contract

### 7.1 Required error envelope

Every non-successful API response with an error body **MUST** use the following structure, including authentication, validation, upstream, and internal service errors:

```json
{
  "error": {
    "code": "INVALID_COORDINATES",
    "message": "Latitude must be between -90 and 90.",
    "request_id": "req-example-001",
    "details": [
      { "field": "min_lat", "reason": "out_of_range" }
    ],
    "retryable": false,
    "source": "service"
  }
}
```

| Field | Type | Contract requirement |
|---|---|---|
| `error.code` | string | Stable uppercase `SNAKE_CASE` application error code; service contracts MUST enumerate codes they return. |
| `error.message` | string | Human-readable and safe for clients; MUST NOT disclose secrets, stack traces, SQL, or sensitive internal details. |
| `error.request_id` | string | Opaque request correlation ID; MUST also appear in the `X-Request-ID` response header. |
| `error.details` | array | Structured details; MUST be an empty array when there are no details. Validation entries SHOULD contain `field` and `reason`. |
| `error.retryable` | boolean | Whether retrying may succeed after a transient condition; MUST NOT imply that a non-idempotent operation can be retried without duplicate effects. |
| `error.source` | string enum | Exactly one of `service`, `upstream`, or `gateway`. |

The HTTP status code identifies the class of failure; `error.code` identifies the specific condition. The error envelope MUST remain structurally consistent even when the originating component is a gateway or upstream dependency.

### 7.2 Status and application-code mapping

| HTTP status | Condition | Standard application code | Retry policy |
|---|---|---|---|
| `400 Bad Request` | Malformed request or general request-structure failure | `BAD_REQUEST` | Correct request. |
| `401 Unauthorized` | Missing/invalid credentials | `AUTHENTICATION_REQUIRED` | Provide valid credentials. |
| `403 Forbidden` | Authenticated requester lacks permission | `FORBIDDEN` | Obtain permission. |
| `404 Not Found` | Resource missing or deliberately concealed | `NOT_FOUND` | Check resource identifier. |
| `409 Conflict` | Request conflicts with current state | `RESOURCE_CONFLICT` | Resolve state or duplicate conflict. |
| `422 Unprocessable Content` | Field or business validation failure | `VALIDATION_FAILED` | Correct field values. |
| `429 Too Many Requests` | Rate limit exceeded | `RATE_LIMITED` | Observe `Retry-After` when supplied. |
| `500 Internal Server Error` | Unexpected service failure | `INTERNAL_ERROR` | Retry only when safe. |
| `502 Bad Gateway` | Upstream response invalid or unusable | `UPSTREAM_FAILURE` | Retry transient failures safely. |
| `503 Service Unavailable` | Required service/dependency unavailable | `SERVICE_UNAVAILABLE` | Retry after recovery when safe. |
| `504 Gateway Timeout` | Upstream request timed out | `UPSTREAM_TIMEOUT` | Retry cautiously when safe. |

Services MAY define more specific error codes (for example, `INVALID_COORDINATES`) while retaining the correct HTTP status. Rate-limit responses **SHOULD** send `Retry-After` when a retry time is known.

### 7.3 Request correlation and retries

- The platform **MUST** use the `X-Request-ID` HTTP header for request correlation.
- The trusted gateway or first trusted ingress point **MUST** assign a request ID; when a service receives a request without a trusted ID, it **MUST** generate one. Untrusted client-supplied IDs MUST be validated or replaced before entering trusted logs and traces.
- Services **MUST** propagate the trusted request ID through downstream requests and record it in structured error logs, without logging credentials or sensitive personal information.
- Network/provider requests **MUST** use finite timeouts. Retries of transient failures **SHOULD** use bounded exponential backoff and avoid retry storms.
- Requests with invalid values or credentials **MUST NOT** be retried unchanged automatically.
- State-changing operations that support safe replay **SHOULD** accept an `Idempotency-Key` header. Each such endpoint **MUST** define key scope, expiry, and behavior for repeated or conflicting requests. An `Idempotency-Key` does not replace authentication.

## 8. Authentication and authorization

1. Every endpoint's contract **MUST** declare whether it is public, authenticated, or restricted by role/ownership.
2. End-user protected endpoints **MUST** receive authenticated identity from a trusted mechanism. The standard HTTP carrier is `Authorization: Bearer <access_token>`; the identity issuer, token validation rules, and claims are specified by the platform security design and referenced by service contracts.
3. Services **MUST NOT** accept a user-controlled `user_id`, `owner_id`, or role field as sufficient proof of identity or permission.
4. User-submitted write operations **MUST** require authentication unless an explicitly documented, approved exception applies. Read operations MAY be public when the specific resource is intended for public access.
5. S3 incident-report modification and withdrawal **MUST** be limited to the verified report owner or an authorized moderator (S3 QR3). Similar ownership and moderation policies for other services MUST be declared in their contracts.
6. Return `401` when authentication is missing or invalid; return `403` when a known authenticated caller lacks permission. `404` MAY be used intentionally to avoid disclosing protected resources when that policy is documented.
7. Service-to-service credentials and end-user identity are distinct. A shared infrastructure or Registry credential **MUST NOT** by itself grant end-user permissions.
8. Credentials **MUST** be kept out of committed source files and ordinary logs, and **MUST** be transmitted over protected channels. Authenticated identity forwarded by a gateway MUST be integrity-protected and unavailable for direct client spoofing.
9. The platform security specification **MUST** define the trusted identity provider, token validation requirements, gateway trust boundaries, roles, and token renewal/revocation behavior before protected endpoints are deployed.

## 9. Filtering, sorting, and pagination

Collection endpoints that can grow beyond a small fixed size **MUST** define filtering, sorting, and pagination behavior. The shared convention is:

| Feature | Parameters | Standard rule |
|---|---|---|
| Filtering | Named query parameters, e.g., `incident_type=construction` | Only documented filter fields and values accepted. |
| Sorting | `sort_by`, `sort_order` | `sort_order` is `asc` or `desc`; service contract MUST enumerate sortable fields and default ordering. |
| Pagination | `limit`, `offset` | Default `limit=25`, maximum `limit=100`, default `offset=0`; `limit` MUST be positive and `offset` nonnegative. |
| Empty result | — | Return `200 OK` and an empty `items` array. |

Filtering MUST be applied before sorting; stable sorting MUST be applied before pagination. The sorting order MUST include an unambiguous tie-breaker so repeated page requests are deterministic for unchanged data.

**Standard collection response envelope:**

```json
{
  "items": [
    { "incident_id": "incident-123", "incident_type": "construction" }
  ],
  "pagination": {
    "limit": 25,
    "offset": 0,
    "total": 1,
    "has_more": false
  }
}
```

- `items` **MUST** be an array; it MAY be empty.
- `pagination.limit`, `pagination.offset`, and `pagination.has_more` **MUST** be present for paginated endpoints.
- `pagination.total` **MUST** be an integer when a reliable count is available, otherwise `null`; no arbitrary estimate may be presented as an exact total.
- The service contract MUST define how `has_more` is determined when the total count is unavailable.
- Single-resource and bounded-result endpoints need not use pagination. High-volume changing streams MAY specify cursor-based pagination in their service contract, with an explicit rationale and documented parameter/response format.

**Illustrative request:**

```http
GET /v1/incidents?incident_type=construction&sort_by=created_at&sort_order=desc&limit=25&offset=0
```

## 10. Liveness and readiness

Every domain service **MUST** expose these unversioned operational endpoints:

| Endpoint | Meaning | Required response |
|---|---|---|
| `GET /health` | Liveness: process is responsive; MUST NOT require expensive dependency checks. | `200 OK` and a small JSON health body while responsive. |
| `GET /ready` | Readiness: service can perform its essential operations, including explicitly supported degraded/fallback mode. | `200 OK` with a readiness body when ready; `503 Service Unavailable` using the standard error envelope when not ready. |

**Standard successful responses:**

```json
{ "service": "parking-analytics", "status": "healthy" }
```

```json
{ "service": "parking-analytics", "status": "ready" }
```

- Each service contract **MUST** list dependencies necessary for readiness and state whether a fallback (such as usable cached information) allows it to remain ready.
- Monitoring endpoints SHOULD be accessible to authorized monitoring infrastructure without requiring ordinary end-user credentials. Their exposure MUST be restricted appropriately at the network or gateway layer.
- These endpoints **MUST NOT** disclose secrets, database credentials, connection strings, or sensitive operational details.
- Traffic routing and deployment systems **SHOULD** consult readiness (rather than only liveness) when choosing which service instances can receive application traffic.

## 11. API contract documentation and change control

Each of S1–S7 **MUST** publish a versioned OpenAPI specification conforming to these standards. Every endpoint specification **MUST** include:

1. Logical service ID, operation ID, purpose, HTTP method, and full versioned path.
2. Mapped functional/quality requirements where applicable.
3. Authentication, authorization, ownership, and relevant roles.
4. Path, query, and header parameters (names, requiredness, types, defaults, validation rules, and units).
5. Request-body schema and a representative request example, or explicit statement that there is no body.
6. Success codes, response schema, and representative response example.
7. Error statuses, application error codes, validation failures, and examples using Section 7's envelope.
8. Timestamp semantics, freshness thresholds, source/provenance, and stale-data behavior.
9. Sorting, filtering, pagination, timeout/retry, and idempotency rules where relevant.
10. Service-owned data entities and external dependency references where relevant.

The **approved API contract is the implementation target**. Service implementations, tests, client integrations, and generated API documentation MUST match the approved contract. Any incompatible interface change MUST undergo review, update its contract, identify affected consumers, and use an approved version/migration plan before deployment.

## 12. Relationship to other T008 deliverables

This document defines conventions shared by all services; it does not replace service-specific design artifacts. T008 also requires:

- **S1–S7 API contracts:** precise operations, request/response models, error behavior, permissions, and requirement mappings.
- **Data ownership specification:** entities, attributes, relationships, persistence/caching, service ownership, and storage technology. Each service's data MUST have a clearly identified owner; domain services MUST NOT rely on a universally shared writable domain database.
- **External dependency specification:** provider/dataset, data consumed, protocol, authentication, update frequency, limits, availability risks, fallback behavior, and provider confirmation status.

A service-specific contract MUST fill in any value explicitly delegated here—for example, supported fuel types, a maximum geographic search radius, price units/currency, or its readiness dependencies—without contradicting the common standards.

---

**Normative principle:** These conventions specify **how the Smart City APIs are required to behave**. Implementations are expected to conform to the contract; the contract is not a description of any particular codebase or framework.
