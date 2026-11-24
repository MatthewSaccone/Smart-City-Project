# S7 — Gasoline Price Analytics

Implementation of the adopted API contract:

- `docs/api/API-Standards.md`
- `docs/api/contracts/S7-Gasoline-Analytics.md`
- `shared/api-contracts/s7-gasoline-analytics.openapi.yaml`

**All example stations are synthetic; no licensed live station directory or external gasoline price feed is bundled.** S7 reads its own persistent station catalog and user-submitted price reports. It never invents prices, calls reports verified/live, or treats an unpopulated catalog as a truthful empty search result. The sample CSV is for demonstrations and tests only.

## API endpoints

| Method | Path | Access |
| --- | --- | --- |
| GET | `/v1/gas-stations` | Public |
| GET | `/v1/gas-stations/{station_id}` | Public |
| GET | `/v1/gas-prices/comparisons` | Public |
| POST | `/v1/gas-stations/{station_id}/price-reports` | Verified bearer identity with `gasoline:report` permission |
| GET | `/health` | Operational |
| GET | `/ready` | Operational |

The two original PR1 requirements labeled FR3 are separately covered: report timestamps and price comparison. All domain operations use contract field names, price units **CAD per litre**, and the contract's five fuel codes.

## Start using the project's Docker Compose stack (PowerShell)

From the **repository root**, after setting up `.env` as documented in the main README:

```powershell
docker compose `
  -f docker-compose.yml `
  -f docker-compose.s2-db.yml `
  -f services/traffic-predictor/compose.ingestion.yml `
  -f services/parking-analytics/compose.parking.yml `
  up -d --build gasoline-analytics
```

For the full application, omit `gasoline-analytics` from the end of that command. S7 runs at `http://localhost:8087`, and its interactive Swagger UI is at `http://localhost:8087/docs`. S7 registers itself with the existing Service Registry and uses the shared JWT verification settings (`JWT_ISSUER`, `JWT_AUDIENCE`, `JWT_JWKS_URL`, etc.). **Registry authentication is not end-user authentication.**

S7 data is stored at `/app/data/gasoline.sqlite3` in the **`gasoline-analytics-data` named Docker volume**. The volume survives `docker compose down` and container rebuilds. Do **not** use `down -v` when testing QR4.

An empty directory is intentionally **not ready**. Until a real or opt-in synthetic catalog is imported, `GET /health` is `200` but `GET /ready` and domain reads are `503`, not a misleading `200` with an empty list.

## Import a station directory

A station must already exist in S7's directory to receive a report. Import a CSV with these exact columns:

```text
station_id,name,address,latitude,longitude,available_fuel_types
```

`available_fuel_types` is a `|`-separated list of supported codes. Decimal coordinates are WGS 84. Station IDs are stable and unique across your S7 directory. Choose and document an authorized directory source; confirm licensing and the supplier's grade/coordinate coverage before production use. A catalog import contains **station data, not fuel prices**. Re-importing a station updates its catalog metadata without discarding reports.

To try a completely synthetic demonstration directory **only**:

```powershell
docker compose exec gasoline-analytics python -m app.catalog_import `
  /app/catalog/EXAMPLE_ONLY_stations.csv `
  --source synthetic_demo_only
```

To import an approved real directory, copy a correctly formatted `stations.csv` file into `services/gasoline-analytics/catalog/` and run:

```powershell
docker compose exec gasoline-analytics python -m app.catalog_import `
  /app/catalog/stations.csv `
  --source your_approved_station_directory
```

After import:

```powershell
Invoke-RestMethod http://localhost:8087/ready
Invoke-RestMethod 'http://localhost:8087/v1/gas-stations?latitude=43.0096&longitude=-81.2737&radius_m=2500'
```

The search may have `prices: []` because **no actual user reports exist yet**. This is intentional.

## Submit reports with real authentication

The `POST` endpoint requires:

1. A **verified signed JWT** with `gasoline:report` in the validated `scope`, `permissions`, or configured role claim. Configure that permission in the existing Keycloak issuer; the S7 service itself does not issue tokens or provide a backdoor. Anonymous requests return `401`, and verified callers missing the permission receive `403`.
2. A known station ID and `Idempotency-Key` of 8–128 ASCII letters, digits, `_`, or `-`.
3. JSON body with `fuel_type`, decimal-safe `price_per_litre` (greater than zero, no more than 20.000, at most 3 decimal places), and literal `currency: "CAD"`. `observed_at` is optional, but when given must be a timezone-aware timestamp no older than seven days or more than five minutes ahead.

Example (replace the placeholder access token with a **genuine** bearer token issued by your configured identity provider):

```powershell
$token = '<valid-Keycloak-access-token>'
$body = '{"fuel_type":"regular_87","price_per_litre":1.659,"currency":"CAD"}'
Invoke-RestMethod -Method Post `
  -Uri http://localhost:8087/v1/gas-stations/example-001/price-reports `
  -Headers @{ Authorization="Bearer $token"; 'Idempotency-Key'='demo-report-0001' } `
  -ContentType 'application/json' -Body $body
```

The response is `201` with a generated UUID report ID and UTC `submitted_at`. A byte-different JSON request representing the same canonical values replays the same result with the same key; a different canonical report under the same key returns `409`. The key is scoped to authenticated subject + station + operation and expires after 24 hours. The accepted report, identity association (private), materialized latest quote, and idempotency response are committed together before `201`.

**Freshness:** A quote is stale only when the age of `observed_at` (if provided) or else `submitted_at` is **greater than** 24 hours. Without `observed_at`, `submitted_at` is merely a *report-age proxy*. Comparisons exclude stale quotes unless `include_stale=true`. User reports are always `is_live=false`.

## Local development without Docker

From the repository root, with Python 3.12 or later:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r services/gasoline-analytics/requirements-dev.txt
$env:PYTHONPATH = 'services;services/gasoline-analytics'
$env:S7_DB_PATH = "$PWD\s7-local.sqlite3"
$env:APP_ENV = 'development'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8087
```

Set JWT issuer/audience/JWKS environment variables if testing authenticated submissions. When running outside the Compose network, `JWT_JWKS_URL` must be reachable **from Windows**, not just from Docker (`keycloak:8080` is usually a container-only hostname). `REGISTRY_URL` is optional locally. The SQLite file is ignored by Git.

## Test and measure

From the repository root:

```powershell
$env:PYTHONPATH = 'services;services/gasoline-analytics'
.\.venv\Scripts\python.exe -m pytest -q services/gasoline-analytics/tests
```

Tests use isolated synthetic stations and a simulated *verified* principal only via FastAPI test dependency overrides. Production S7 **never bypasses JWT verification**. Coverage includes contract schema validation, filters, timestamps, validation/errors, 24-hour staleness boundary, idempotency, database restart, search radius, and request correlation.

For a real HTTP timing scenario against a running S7 with an imported catalog:

```powershell
.\.venv\Scripts\python.exe services/gasoline-analytics/tools/benchmark_s7.py `
  --latitude 43.0096 --longitude -81.2737 --radius-m 2500 `
  --requests 100 --workers 10
```

This measures actual HTTP response time, including JSON serialization, for search and comparison, and reports p50/p95/max. QR1 targets **1,500 ms per valid request**. Benchmark evidence should state load and dataset sizes; passing a small synthetic benchmark is **not** proof of performance at production scale.

## Design notes and limitations

- SQLite is an **S7-owned relational database**, instead of the contract's *proposed* PostgreSQL/PostGIS storage; it provides transaction durability and indexed coordinate preselection with exact spherical distance filtering. Revisit PostgreSQL/PostGIS if catalog volume or concurrent reporting requires it.
- S7 has **no confirmed live station-directory or fuel-price provider**. Production deployment requires sourcing and licensing real station metadata and documenting its updates. A previously imported directory remains usable during provider downtime. No price data is pulled from providers.
- Public price quotes are derived solely from immutable accepted reports; each new accepted report is `unverified`, with no assertion that the displayed price is the current pump price.
- This implementation does not include per-client rate limiting or a fully provisioned Keycloak role/client configuration. Configure those at the trusted gateway/identity platform before public deployment, and carry out realistic QR1 benchmarks.
- The application's FastAPI Swagger UI documents its implemented routes. The **approved, authoritative** OpenAPI specification remains `shared/api-contracts/s7-gasoline-analytics.openapi.yaml`; tests verify returned example shapes against it.
