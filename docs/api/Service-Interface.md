# Service Interfaces and API Contracts

> **Status:** Service interface inventory and API contract index. The S3 Construction & Incidents interface is documented in its service-specific contract and implemented in the corresponding microservice. Other services should use their respective service-specific contracts as the source of truth.

## 1. Purpose

This document provides an index of the external interfaces between the frontend, API gateway, service registry, and domain microservices.

Each service-specific contract defines its endpoints, request and response schemas, validation rules, authentication requirements, and expected behaviour. Contracts should be reviewed before dependent components integrate.

## 2. General API Conventions

The following conventions apply across the Smart City platform unless a service-specific contract documents an exception:

- Versioned HTTP API paths, using `/v1` for versioned service endpoints.
- JSON request and response formats.
- UTC timestamps represented using ISO 8601.
- Consistent pagination for collection endpoints.
- Consistent error response structure.
- Explicit input validation and documented constraints.
- Request IDs for tracing and error correlation.
- Authentication and authorization requirements documented per endpoint.
- Defined timeout, retry, and idempotency behaviour where applicable.
- Explicit data freshness and availability indicators where relevant.

### Standard Error Envelope

Service errors use the following response structure:

```json
{
  "error": {
    "code": "VALIDATION_FAILED",
    "message": "The request contains invalid fields",
    "request_id": "example-request-id",
    "details": [],
    "retryable": false,
    "source": "service"
  }
}
```

The HTTP status code and error code communicate the failure category. The request ID supports tracing, while `details` provides additional validation information when applicable.

## 3. Component Interface Inventory

| Component | Consumer | Main interface purpose |
|---|---|---|
| Frontend | User | User interactions and data presentation |
| API Gateway | Frontend | Client-facing API entry point and request routing |
| Service Registry | Gateway / service consumers | Logical service discovery and health monitoring |
| Live Traffic (S1) | Gateway / frontend | Current traffic and congestion information |
| Traffic Predictor (S2) | Gateway / frontend | Historical traffic analysis and future congestion estimates |
| Construction & Incidents (S3) | Gateway / frontend | User-submitted construction and incident reports |
| Parking Analytics (S4) | Gateway / frontend | Parking availability, congestion, and pricing |
| Weather Analytics (S5) | Gateway / frontend | Weather and air-quality information |
| Transit Analytics (S6) | Gateway / frontend | Public transit information and analysis |
| Gasoline Analytics (S7) | Gateway / frontend | Fuel prices and station information |

Each domain service maintains its own application logic and data responsibilities. The gateway routes requests to services, while the registry provides logical service discovery rather than requiring consumers to hard-code service addresses.

## 4. Service Contract Index

The following documents define the service-specific API contracts:

| Service | Contract |
|---|---|
| S1 – Live Traffic | [S1 Live Traffic API Contract](contracts/S1-Live-Traffic.md) |
| S2 – Traffic Predictor | [S2 Traffic Predictor API Contract](contracts/S2-Traffic-Predictor.md) |
| S3 – Construction & Incidents | [S3 Construction & Incidents API Contract](contracts/S3-Construction-Incidents.md) |
| S4 – Parking Analytics | [S4 Parking Analytics API Contract](contracts/S4-Parking-Analytics.md) |
| S5 – Weather & Air Quality | [S5 Weather & Air Quality API Contract](contracts/S5-Weather-Air-Quality.md) |
| S6 – Public Transit | [S6 Public Transit API Contract](contracts/S6-Public-Transit.md) |
| S7 – Gasoline Analytics | [S7 Gasoline Analytics API Contract](contracts/S7-Gasoline-Analytics.md) |

The S3 service also has an OpenAPI specification:

`shared/api-contracts/s3-construction-incidents.openapi.yaml`

The service-specific contract and OpenAPI specification should remain consistent with the implemented service.

## 5. S3 – Construction & Incidents Interface Summary

S3 provides user-submitted construction and incident reports for a geographic area. It supports public report retrieval and authenticated report creation and management.

### 5.1 Endpoints

| Method | Endpoint | Authentication | Purpose |
|---|---|---|---|
| GET | `/v1/incidents` | Public | List reports within a geographic radius, with filtering, sorting, and pagination |
| GET | `/v1/incidents/{incident_id}` | Public | Retrieve an individual report |
| POST | `/v1/incidents` | Bearer token required | Submit a new report |
| PATCH | `/v1/incidents/{incident_id}` | Bearer token required | Update an existing report or its status |
| DELETE | `/v1/incidents/{incident_id}` | Bearer token required | Withdraw a report |
| GET | `/health` | Public | Check service liveness |
| GET | `/ready` | Public | Check database and trusted token-verification readiness |

### 5.2 Report Types and Statuses

Supported report types:

- `construction`
- `accident`
- `other_traffic`

Supported report statuses:

- `active`
- `resolved`
- `completed`
- `inaccurate`
- `withdrawn`

New reports are created with `active` status. Reports can transition between permitted statuses according to the documented lifecycle. Withdrawal is a soft-delete operation; withdrawn reports are excluded from public retrieval.

### 5.3 Authentication and Authorization

- Public list and detail endpoints do not require authentication.
- Creating, updating, and withdrawing reports require a verified bearer token.
- Only the report owner or an authorized moderator may modify or withdraw a report.
- User identity and roles are derived from verified token claims, not client-supplied request fields.
- Ownership information and private audit records are not exposed in public report responses.

### 5.4 Validation and Data Behaviour

- Geographic coordinates use WGS84 latitude and longitude.
- Report descriptions must contain between 10 and 2,000 characters after trimming whitespace.
- Report timestamps are generated or validated by the service according to the API contract.
- Client-supplied ownership, identity, and server-managed status fields are rejected.
- Report creation supports idempotency keys to prevent duplicate submissions caused by retries.
- Public responses include source and data-freshness metadata.
- Active reports that have not been updated for more than seven days are identified as stale.

Refer to the [S3 Construction & Incidents API Contract](contracts/S3-Construction-Incidents.md) for complete parameter definitions, schemas, status transitions, error responses, and examples.

## 6. Contract Validation

Service contracts should be validated against their implementations and associated tests.

Validation should cover:

- Required and optional request fields.
- Data types, formats, and enumerations.
- Geographic and numeric constraints.
- Success response status codes and schemas.
- Standard error responses and HTTP status codes.
- Authentication and authorization requirements.
- Idempotency and retry behaviour.
- Pagination, sorting, and filtering.
- Data freshness and availability behaviour.
- Backward compatibility and contract changes.

Where possible, FastAPI services should expose OpenAPI specifications that reflect their implemented endpoints and schemas.

## 7. Contract Change Process

Changes to service interfaces should follow this process:

1. Propose the change and identify affected service consumers.
2. Update the relevant service-specific contract and examples.
3. Review compatibility, validation, and versioning implications.
4. Update the service implementation and associated tests.
5. Coordinate any required consumer or gateway changes.
6. Record the change in the relevant documentation and release commit.

## 8. Related Documentation

- [API Standards](API-Standards.md) – Shared API conventions and response standards.
- [S3 Construction & Incidents API Contract](contracts/S3-Construction-Incidents.md) – Detailed S3 endpoint and schema definitions.
- [S3 Construction & Incidents Architecture](../architecture/S3-Construction-Incidents.md) – Internal service architecture.
- [S3 Construction & Incidents README](../../services/construction-incidents/README.md) – Local setup, configuration, and execution instructions.
- [Testing and Validation](../Testing-and-Validation.md) – Project-level testing and validation approach.