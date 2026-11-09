# Service Interfaces and API Contracts

> **Status:** Contract template and initial service inventory. The endpoint examples below are placeholders, not claims about implemented routes. Update from the actual OpenAPI specifications and code.

## 1. Purpose

This document defines the external interfaces between the frontend, gateway, registry, and domain services.

Contracts should be agreed before dependent components integrate.

## 2. General API Conventions

The team should standardize:

- Base URL and API versioning.
- JSON request and response formats unless a documented exception exists.
- UTC timestamps in ISO 8601 format.
- Consistent pagination for collection endpoints.
- Consistent error response structure.
- Input validation and documented constraints.
- Request IDs/correlation IDs for tracing.
- Authentication and authorization.
- Timeouts, retry rules, and idempotency.

### Suggested Error Envelope

```json
{
  "error": {
    "code": "INVALID_REQUEST",
    "message": "A required field is missing",
    "request_id": "example-request-id"
  }
}
```

This is a proposed convention. Confirm and adopt it before implementation.

## 3. Component Interface Inventory

| Component | Consumer | Main interface purpose |
|---|---|---|
| Frontend | User | User interactions and data presentation |
| Gateway | Frontend | Client-facing API entry point |
| Registry | Gateway / service consumers | Logical service discovery |
| Live Traffic | Gateway / frontend | Current traffic and congestion |
| Traffic Predictor | Gateway / frontend | Future traffic estimates |
| Construction & Incidents | Gateway / frontend | Incident and construction reports |
| Parking Analytics | Gateway / frontend | Parking availability and pricing |
| Weather Analytics | Gateway / frontend | Weather and air quality |
| Transit Analytics | Gateway / frontend | Transit information and analysis |
| Gasoline Analytics | Gateway / frontend | Fuel prices and station data |

## 4. Service Contract Templates

For every endpoint, document:

| Field | Description |
|---|---|
| Service and endpoint | Logical service ID and HTTP path |
| Method | GET, POST, PUT, PATCH, DELETE |
| Purpose | What the operation does |
| Authentication | Required identity/role |
| Path/query parameters | Names, types, constraints |
| Request body | Schema and example |
| Success response | Status code and schema |
| Error responses | Status codes and error codes |
| Timeout | Maximum expected processing time |
| Idempotency | Whether safe to retry |
| Data freshness | Expected age of returned data |
| Test coverage | Associated test IDs |

## 5. Initial Service-Specific Contract Scope

### 5.1 Live Traffic

Candidate operations:

- Retrieve current traffic conditions for a location or area.
- Compare congestion between two points or within a radius.
- Return estimated delays and data freshness.

Define coordinate format, radius units, time zone, freshness metadata, and behaviour when live data is unavailable.

### 5.2 Traffic Predictor

Candidate operations:

- Retrieve historical congestion for a location, time, and day.
- Predict congestion for a future time and area.

Define forecast horizon, prediction interval, confidence/uncertainty representation, model version, and behaviour when historical data is insufficient.

### 5.3 Construction & Incidents

Candidate operations:

- List active construction and incident reports in an area.
- Retrieve a report by identifier.
- Create, update, resolve, or close a report if user reporting is in scope.

Define report ownership, status lifecycle, validation, duplicate handling, and moderation rules.

### 5.4 Parking Analytics

Candidate operations:

- Retrieve parking availability and congestion for an area.
- Retrieve price and fee information.

Define availability meaning, currency, fee units, update timestamp, and unknown/stale-data behaviour.

### 5.5 Weather Analytics

Candidate operations:

- Retrieve current weather for a location.
- Retrieve air-quality measurements and trends.

Define units, pollutant identifiers, concentration units, observation timestamp, and provider attribution.

### 5.6 Transit Analytics

Candidate operations:

- Retrieve transit status or service information for an area/route.
- Retrieve relevant delays or service disruptions.

Define route/stop identifiers, schedule versus real-time fields, time zone, and stale-data behaviour.

### 5.7 Gasoline Analytics

Candidate operations:

- Retrieve station prices by area and fuel type.
- Retrieve station details and last-updated timestamps.
- Compare prices across stations.

Define fuel-type enumeration, station identifier, currency, price unit, and update timestamp.

## 6. OpenAPI and Contract Validation

Where possible, expose an OpenAPI document from each FastAPI service and use it as the implementation reference.

Validate:

- Required and optional fields.
- Types, formats, and enumerations.
- Success and error responses.
- Backward compatibility.
- Example payloads.
- Authentication requirements.

## 7. Contract Change Process

1. Propose the change and identify consumers.
2. Update the contract and examples.
3. Review compatibility and versioning impact.
4. Update implementation and tests.
5. Coordinate consumer updates.
6. Record the release/commit that introduced the change.