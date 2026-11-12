# S3 — Construction & Incident Reporting

S3 is the Smart City platform's user-submitted construction and traffic incident reporting microservice. It supports creating reports, searching geographically, retrieving report details, updating report information and status, and withdrawing reports.

The service is implemented using **FastAPI**, **PostgreSQL/PostGIS**, and verified **RS256 JWT authentication**.

## 1. Responsibilities

S3 provides:

- Submission of construction, accident, and other traffic-related reports.
- Geographic search for reports within a specified radius.
- Public retrieval of individual non-withdrawn reports.
- Authenticated report updates and status transitions.
- Owner/moderator authorization for modifications and withdrawals.
- Durable report persistence, private ownership records, audit history, and idempotent creation.
- Health and readiness endpoints for monitoring.

S3 owns its report data. It does not use the Service Registry database for domain persistence.

## 2. Technology

| Area | Technology |
|---|---|
| API framework | FastAPI |
| Language | Python |
| Database | PostgreSQL |
| Geographic queries | PostGIS |
| Database driver | psycopg 3 |
| Authentication | RS256 JWT verification |
| Signing-key discovery | JWKS |
| Validation | Pydantic |
| Containerization | Docker |
| Testing | pytest, FastAPI TestClient |

## 3. API Endpoints

Base URL for local development:

`http://localhost:8083`

| Method | Endpoint | Purpose | Authentication |
|---|---|---|---|
| GET | `/health` | Process liveness | None |
| GET | `/ready` | Database and token-verification readiness | None |
| GET | `/v1/incidents` | Search reports within a radius | Public |
| GET | `/v1/incidents/{incident_id}` | Retrieve one report | Public |
| POST | `/v1/incidents` | Create a report | Bearer token |
| PATCH | `/v1/incidents/{incident_id}` | Update a report or status | Owner or moderator |
| DELETE | `/v1/incidents/{incident_id}` | Withdraw a report | Owner or moderator |

The complete request and response schemas, validation rules, status lifecycle, and error conventions are documented in:

`docs/api/contracts/S3-Construction-Incidents.md`

The machine-readable OpenAPI specification is:

`shared/api-contracts/s3-construction-incidents.openapi.yaml`

## 4. Report Model

### Incident categories

- `construction`
- `accident`
- `other_traffic`

### Report statuses

- `active`
- `resolved`
- `completed`
- `inaccurate`
- `withdrawn`

New reports are created with the `active` status.

Reports can be resolved, completed, or marked inaccurate. Eligible reports can be reopened by changing their status to `active`. Withdrawal is performed through `DELETE` and is a soft deletion.

Withdrawn reports are excluded from public search and detail responses. Their private records and audit history remain in persistent storage.

## 5. Authentication and Authorization

S3 acts as a resource server. It does not issue access tokens or establish user identities.

Authenticated write requests must provide a bearer JWT signed using RS256. The service verifies the token using the configured issuer, audience, and JWKS endpoint.

The verified token subject identifies the requester. Moderator permissions are derived from verified role claims.

The following environment variables configure token verification:

| Variable | Purpose |
|---|---|
| `JWT_ISSUER` | Expected token issuer |
| `JWT_AUDIENCE` | Expected token audience |
| `JWT_JWKS_URL` | Trusted signing-key endpoint |
| `JWT_ROLE_CLAIM` | Claim containing roles; defaults to `roles` |
| `JWT_MODERATOR_ROLE` | Moderator role name; defaults to `moderator` |

The production identity provider and its final claim conventions must be selected and configured before deployment.

A report's owner identifier is stored privately. It is never included in public report responses.

## 6. Persistence

S3 uses a dedicated PostgreSQL database with PostGIS support.

The database is separate from the Service Registry database and is managed independently by S3.

Persistent records support:

- Incident report data.
- Private report ownership.
- Idempotency records for report creation.
- Private audit history for report changes and status transitions.

Report creation and its required private metadata are committed transactionally before a successful creation response is returned.

Creation idempotency keys are scoped to the verified user and create operation, with a 24-hour retention period.

The Docker Compose database service is named `incidents-postgres` and uses a persistent Docker volume.

## 7. Local Development

Run commands from the repository root.

### Configuration

Create a local `.env` file from the example if one does not already exist:

```powershell
Copy-Item .env.example .env
```

Set a strong local value for `INCIDENTS_POSTGRES_PASSWORD`. Do not commit the populated `.env` file or share its contents.

The service also requires the trusted JWT configuration described above for authenticated requests and readiness.

### Start S3 and its database

```powershell
docker compose up --build -d incidents-postgres construction-incidents
```

View service logs:

```powershell
docker compose logs -f construction-incidents
```

The local service is expected to listen on port `8083`.

### Stop the service

```powershell
docker compose down
```

This does not remove the persistent database volume. Do not use `docker compose down -v` unless you intentionally want to delete local database data.

## 8. Example Requests

### Check liveness

```powershell
Invoke-RestMethod http://localhost:8083/health
```

### Check readiness

```powershell
Invoke-RestMethod http://localhost:8083/ready
```

Readiness requires the database and trusted token-verification dependency to be available. A `503` response indicates that one or more required dependencies are unavailable or not configured.

### Search for reports

```powershell
Invoke-RestMethod "http://localhost:8083/v1/incidents?latitude=43.0096&longitude=-81.2737&radius_m=1500"
```

### Retrieve a report

```powershell
Invoke-RestMethod "http://localhost:8083/v1/incidents/{incident_id}"
```

### Create a report

An authenticated request requires a valid token and an idempotency key.

```powershell
$headers = @{
    Authorization = "Bearer $token"
    "Idempotency-Key" = "incident-create-example-001"
}

$body = @{
    incident_type = "construction"
    location = @{
        latitude = 43.0096
        longitude = -81.2737
    }
    description = "Road construction is blocking the westbound lane near the intersection."
} | ConvertTo-Json -Depth 5

Invoke-RestMethod `
    -Method Post `
    -Uri "http://localhost:8083/v1/incidents" `
    -Headers $headers `
    -ContentType "application/json" `
    -Body $body
```

`$token` must contain a token issued by the configured trusted identity provider. The example does not create or issue a token.

## 9. Validation and Error Handling

S3 validates:

- Incident category and status values.
- Finite WGS 84 latitude and longitude values.
- Required report descriptions and length limits.
- Timezone-aware observation timestamps.
- Radius and geographic search parameters.
- UUID path parameters.
- Request body fields and unexpected properties.
- Ownership and moderator authorization.
- Status transition rules.
- Idempotency-key format and reuse.

Errors use the shared platform error envelope and include a request ID for correlation.

Important responses include:

| HTTP status | Meaning |
|---|---|
| `400` | Malformed request |
| `401` | Missing or invalid authentication |
| `403` | Authenticated requester is not authorized |
| `404` | Report not found or not publicly visible |
| `409` | Resource or idempotency conflict |
| `422` | Validation failure |
| `503` | Required dependency unavailable |

## 10. Automated Tests

S3's tests are located in:

`services/construction-incidents/tests/`

The test modules cover authentication, database handling, error responses, API endpoints, request models, and repository behaviour.

Run the service test suite from the repository root:

```powershell
pytest services/construction-incidents/tests -v
```

For a focused API endpoint test run:

```powershell
pytest services/construction-incidents/tests/test_main.py -v
```

Test results and implementation evidence are recorded separately in:

`docs/Testing-T028-T038.md`

## 11. Architecture and Related Documentation

- API contract: `docs/api/contracts/S3-Construction-Incidents.md`
- S3 architecture: `docs/architecture/S3-Construction-Incidents.md`
- Service data ownership: `docs/architecture/Service-Data-Ownership.md`
- Implementation and verification: `docs/Testing-T028-T038.md`
- General API conventions: `docs/api/API-Standards.md`

## 12. Outstanding Deployment Considerations

The implementation provides the core S3 reporting and persistence functionality. The following still require completion or acceptance evidence:

- Selection and production configuration of the trusted identity provider.
- Confirmation of production JWT issuer, audience, signing keys, and role claims.
- Approved retention and privacy policies for report ownership and audit data.
- Production database provisioning, backup, and recovery procedures.
- Verification of S3 QR1 and QR5 latency targets under representative load.
- Verification of QR4 persistence through a service restart.
- Integration and acceptance testing through the deployed gateway and frontend.
