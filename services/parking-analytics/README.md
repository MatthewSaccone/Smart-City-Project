# S4 Parking Analytics — T039–T045

Implements geographic facility search and occupancy estimates calculated from time/location
observations. The catalog and bundled observations are **simulated**, not a live parking feed.
The service implements the adopted [T008 S4 contract](../../docs/api/contracts/S4-Parking-Analytics.md)
and [common standards](../../docs/api/API-Standards.md), including selected-facility tariffs,
independent freshness metadata, outage fallback, pagination and JWT authorization.
S4 now shares S2's tested Keycloak verifier and implements [T010 data integration](T010.md).
See [JWT setup](AUTHENTICATION.md), the [original task results](VERIFICATION.md), and
[current integration evidence](verification-auth-t010.json). S1, S3, GUI and T009 remain unchanged.

## Run locally (PowerShell, repository root)

Use Python 3.11+ (the existing Dockerfile uses 3.12). If needed, create an isolated environment:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r services/parking-analytics/requirements-dev.txt
$env:PYTHONPATH = (Resolve-Path .\services).Path
$env:REGISTRY_URL = ""
$env:JWT_ISSUER = 'http://localhost:8088/realms/smart-city'
$env:JWT_AUDIENCE = 'smart-city-api'
$env:JWT_JWKS_URL = 'http://localhost:8088/realms/smart-city/protocol/openid-connect/certs'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir services/parking-analytics --host 127.0.0.1 --port 8084
```

The services parent must be on `PYTHONPATH` for the existing `shared.registry_client` import.
Open <http://127.0.0.1:8084/docs>; use **Authorize** with a Keycloak access token.
Search/detail/status require identity; estimation also requires `parking:estimate`.
Use [AUTHENTICATION.md](AUTHENTICATION.md) to obtain a token, then set
`$headers = @{ Authorization = "Bearer $env:PARKING_ACCESS_TOKEN" }` in the caller terminal.
`/health` remains accessible. `/ready` succeeds with
a usable catalog even when occupancy is unavailable; missing essential catalog data yields 503.
Registration is optional locally. When enabling it, set `REGISTRY_URL`, `REGISTRY_API_KEY`
and `REGISTRY_ADVERTISED_ENDPOINT` to addresses reachable by the intended consumer.
The existing registration, five-second heartbeat and shutdown behavior are unchanged.

## T039: geographic search

```powershell
Invoke-RestMethod 'http://127.0.0.1:8084/v1/parking/facilities?min_lat=43.65&min_lon=-79.39&max_lat=43.66&max_lon=-79.37' -Headers $headers | ConvertTo-Json -Depth 8
```

`GET /v1/parking/facilities` requires `min_lat`, `min_lon`, `max_lat`, `max_lon` in WGS84
degrees. It includes facility **centers** on the rectangle edges, returns 200 with an empty
`items` list when nothing matches. Bounds must span at most **2 degrees per axis**.
Missing, nonfinite, out-of-range, reversed, repeated or unsupported inputs return the adopted
422 `error` envelope with `X-Request-ID`. Antimeridian areas must be split into two queries.
It does not perform polygon overlap, radius search, geocoding or space reservation.

The example returns `demo-central` and `demo-east`. Each result includes calculated
occupancy; a full facility or unknown estimate is explicitly visible. Facilities are listed
even when full or unknown, rather than claiming that every catalog entry has free spaces.
`estimated_available_spaces` is an inference, not a guarantee of an available parking space.

Search returns `items`, `pagination`, processing-time `as_of` and `facility_source`.
Optional filters are `facility_status` and `availability_status`; sorting uses `sort_by=name`
or `facility_id` and `sort_order=asc|desc`, followed by a stable ID tie-breaker. Pagination
uses `limit=1..100` (default 25) and `offset>=0` (default 0), after filtering and sorting.

The bundled observations are replayed at **2026-11-09T17:00:00Z** only when calculating
the fixture aggregate. Public `as_of` is actual processing time. Original observation
timestamps remain visible, with `data_status: simulated`, `is_live: false` and `is_stale`
calculated against actual current time. Expected historical estimates: Central 2/4
(moderate), East 2/2 (full), North unknown. They are not current availability claims.
Stale/cached/simulated occupancy cannot produce an affirmative `available` status;
the known closed North facility returns `availability_status: closed`.

## T043: select a facility and inspect tariffs

```powershell
Invoke-RestMethod 'http://127.0.0.1:8084/v1/parking/facilities/demo-central' -Headers $headers | ConvertTo-Json -Depth 8
```

The detail response combines `facility`, `availability_status`, `occupancy`, `pricing`,
`as_of` and `facility_source`. Central has simulated CAD hourly/daily tariffs and a separate
entry fee. East has a simulated entry tariff. Both have unknown provider update timestamps;
the service reports them as null, rather than inventing a date. North has
no known pricing: empty rates/fees and null currency/tax/timestamps, never a zero-dollar claim.
Prices are informational tariffs, not a final tax-inclusive quote or reservation.
Search's starting rate is the first configured tariff, not a comparison across incompatible
hour/day/entry units. Invalid ID syntax returns 422; a well-formed missing ID returns 404.
IDs match `[a-z0-9][a-z0-9_-]*`, at most 64 characters; detail accepts no query fields/body.

## T042 / T045: freshness and independent fallback

Occupancy exposes original `observed_at` and actual cache/result `service_updated_at`.
Prices separately expose `source_updated_at` and actual tariff-load `service_updated_at`.
Unchanged source aggregates do not continually rewrite their cache update timestamp.
Missing provider timestamps stay null. All timestamp outputs normalize to UTC `Z`.

Occupancy is stale after **300 seconds** and prices after **24 hours**, with inclusive
freshness boundaries. Missing or future source timestamps cannot establish freshness.
The fixture timestamps are deliberately historical; restarting/replaying does not make
them live or replace them with the current time. `is_live` and `is_stale` have distinct meanings.

Catalog and tariffs default to service-owned JSON fixtures. Optional S4-owned PostgreSQL
holds normalized stream snapshots; `PARKING_CACHE_PATH` preserves the latest catalog,
tariffs and aggregate snapshot for restart fallback, without raw subjects or observations.
The Compose overlay mounts an S4-only volume. A provider failure preserves known locations/rates and legitimate aggregate values;
genuine source-backed estimates become `cached`, while simulations retain `simulated`
provenance. Missing occupancy becomes null/unknown. Cached counts never imply available spaces.

Source refresh runs in a background worker, every `PARKING_REFRESH_SECONDS` (default 5),
with PostgreSQL notification hints and bounded retry when configured;
reads do not call a provider. `PARKING_OCCUPANCY_MODE=unavailable` simulates a source timeout.
`fixture` is the default. `file` plus `PARKING_OCCUPANCY_FILE` accepts only internal,
explicitly simulated aggregate files; it cannot certify a live feed. Production providers
remain unselected. A provider adapter must supply bounded validated aggregates and finite
I/O deadlines; no external provider is queried by this demonstration.

## T041: calculate occupancy from observations

`POST /v1/parking/occupancy/estimate` accepts up to 10,000 observations and an internal
bearer credential with `parking:estimate` permission:

```json
{
  "as_of": "2026-11-09T17:00:00Z",
  "observations": [
    {"subject_id": "demo-one", "latitude": 43.6532, "longitude": -79.3832, "observed_at": "2026-11-09T16:59:10Z"},
    {"subject_id": "demo-one", "latitude": 43.6532, "longitude": -79.3832, "observed_at": "2026-11-09T16:59:50Z"}
  ]
}
```

This example calculates one occupied space at Central, rather than returning the two
subjects in the bundled replay. An omitted `as_of` uses current UTC time; replaying old
observations without it correctly produces unknown estimates.

To submit the clearly simulated fixture, obtain `PARKING_ACCESS_TOKEN` in the client
terminal using a Keycloak service account granted `parking:estimate`.
Alternatively use Swagger's Authorize button:

```powershell
$demo = Get-Content services/parking-analytics/app/fixtures/parking.json -Raw | ConvertFrom-Json
$body = @{ as_of = $demo.as_of; observations = $demo.observations } | ConvertTo-Json -Depth 6
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8084/v1/parking/occupancy/estimate' -Headers @{ Authorization = "Bearer $env:PARKING_ACCESS_TOKEN" } -ContentType 'application/json' -Body $body | ConvertTo-Json -Depth 8
```

Calculation (`stationary_unique_subjects_v1`):

1. Keep samples in the inclusive previous 120 seconds up to `as_of`. Ignore future samples.
2. Deduplicate each anonymous subject's identical timestamps and sort chronologically.
   Conflicting locations for the same subject/timestamp are rejected with 422.
3. Assign each sample to the nearest facility center within its configured geofence, using
   Haversine distance in meters. Overlapping geofences never double-count a subject.
4. Require the subject's latest sample to be no older than 60 seconds. Require a consecutive
   run in the same facility spanning at least 30 seconds, with no sample gap over 60 seconds.
   A latest sample outside the lot means the subject has left and is not counted there.
5. Count each qualifying subject once. Cap occupied spaces at catalog capacity and expose
   `capacity_exceeded` plus the uncapped `qualifying_subjects` count if evidence exceeds it.
6. Classify the estimated ratio: low <50%, moderate 50–<80%, high 80–<100%, full 100%.
   With no qualifying subjects, report null counts/ratio and `unknown`, never a proven empty lot.

Assumptions: one stationary participating subject represents one parked vehicle; all
relevant users share a stable anonymous identifier; the facility catalog and capacities
are valid. Dwell within a geofence is a parking proxy, not proof of a parked vehicle.
Pedestrians, multiple passengers, sparse participation, GPS error and adjacent streets
can bias the estimate. No statistical expansion to unobserved vehicles is attempted.

The estimator is stateless: submissions are not saved or combined across requests. Each
batch must contain its own dwell history. Responses contain `items`,
`source: submitted_observations`, `data_status: computed`, `is_live: false`, and
freshness against actual response time, independently of historical `as_of`.
`as_of` may not be more than 60 seconds after request receipt. Submitting a batch does not
update the public occupancy cache or persist raw observations. Invalid JSON/structure uses
400; valid JSON with invalid fields uses 422, with sanitized errors that omit raw inputs.

`PARKING_INTERNAL_CLIENTS` opaque credentials are replaced by the shared trusted JWT
verifier. Missing/invalid identity returns 401; a verified caller without the estimator
permission returns 403. Unavailable required signing keys return retryable 503.
See [AUTHENTICATION.md](AUTHENTICATION.md) for Keycloak, claim mapping and migration.

## Tests, timing and Compose

From the repository root, run this suite separately from the registry's suite because
both services use a top-level Python package named `app`:

```powershell
.\.venv\Scripts\python.exe -m pytest -c services/parking-analytics/pytest.ini services/parking-analytics/tests -q
.\.venv\Scripts\python.exe services/parking-analytics/tools/benchmark.py --spawn-local
.\.venv\Scripts\python.exe services/parking-analytics/tools/verify_compose.py
```

The tests retain the original estimator cases and add T008 schema validation using a
test-only JSON Schema dependency, exact freshness boundaries, outage/recovery, aggregate
persistence, authentication, sanitization, and real HTTP timing/concurrency checks.

T008 resolves the old task-sheet concurrency note: **25 simultaneous estimation requests**
while checking facility reads against 2,000 ms. The benchmark uses 1,000 simulated
observations/request, 3 facilities, 3 warmed-up rounds, 25 estimator clients and 5 read clients.
It reports every read operation's p50/p95/max, errors and estimator timings. The contract
does not prescribe a separate estimation deadline. These are bounded local tests, not
production scalability certification or tests of all 10,000-observation workloads.

`verify_compose.py` builds an isolated randomly named project from the real root Compose
configuration, publishes random loopback ports, checks registration and heartbeats, runs
the same benchmark plus existing PostgreSQL registry tests, restarts parking with its
source unavailable and verifies persisted fallback. It removes only its own containers
and volumes. It does not start S1 or other domain services.

For an interactive Compose demonstration, stop the standalone server on 8084, configure
root `.env` and Keycloak as described in AUTHENTICATION.md, and retain your existing
registry/database secret values:

```powershell
docker compose -p parking-aaliyan-demo -f docker-compose.yml -f services/parking-analytics/compose.parking.yml up --build -d parking-analytics
```

To demonstrate source failure while retaining its own persisted aggregate:

```powershell
$env:PARKING_OCCUPANCY_MODE = 'unavailable'
docker compose -p parking-aaliyan-demo -f docker-compose.yml -f services/parking-analytics/compose.parking.yml up -d --no-deps --force-recreate parking-analytics
Invoke-RestMethod 'http://127.0.0.1:8084/v1/parking/facilities/demo-central' -Headers $headers | ConvertTo-Json -Depth 8
```

Restore `PARKING_OCCUPANCY_MODE=fixture` and repeat the `up` command to recover.
Keep the same registry/database secret values for that demo project's lifetime.
Root Compose passes the existing shared JWT settings to S4. The optional overlays add
only S4's cache volume and dedicated ingestion database. The startup command uses `exec`
so SIGTERM reaches Uvicorn for graceful worker shutdown and registry deregistration.

## Migration from the pre-T008 starter API

The adopted contract replaces the earlier search `facilities` key and estimator `occupancy`
key with `items`. Search now has pagination and independent per-field provenance instead
of a global `is_live` flag. Public `as_of` is processing time; historical observation times
are in `occupancy.observed_at`. The estimator is now restricted, as required by T008.
Consumers should use the agreed contract rather than the pre-contract response examples.
