# S4 T039–T045 verification — November 11, 2026

This is the original T039–T045 delivery record. The subsequent JWT/T010 integration
updates authentication, source refresh and storage; see [T010.md](T010.md),
[AUTHENTICATION.md](AUTHENTICATION.md) and [current evidence](verification-auth-t010.json).

## Git and inspection baseline

The initial working tree was clean on `feature/aaliyan-t009-t039-t041` at `3662ba6`,
tracking the same pushed origin branch. That contribution, including T009, T039 and T041,
was already merged into `main` through `2f479ee`. Taha's completed T008 contracts were
merged through `9f00868`. `git fetch origin` confirmed the team state; local `main` was
four commits behind and fast-forwarded with `git pull --ff-only origin main`.

`parking-analytics-aaliyan` did not exist locally or remotely and was created from updated
`main` at `9f00868` before implementation. No existing work was reset, rebased,
cherry-picked, overwritten or duplicated. Verification preceded the S4 feature commit.

The original S4 suite passed **32 tests**. Search and the real observation-based estimator
already worked, but their pre-contract response envelopes lacked the adopted T008 shape.
There was no selected-facility pricing operation, independent pricing freshness, provider
outage/recovery acceptance suite, or reproducible concurrent-performance evidence.
Existing observation/reference timestamps were retained rather than replaced with fake
fresh dates. The estimator algorithm remains intact, with support added for a null geofence.

Read before changes: `docs/api/contracts/S4-Parking-Analytics.md`, its OpenAPI YAML,
`docs/api/API-Standards.md`, and the S4 data-ownership/external-dependency sections.
The updated task-sheet PDF confirms Aaliyan's T039–T045 ownership. Its T044 note still
claims T002 must resolve a numeric target, but the newly adopted S4 contract explicitly
sets **25 concurrent occupancy-estimation requests while testing facility retrieval**.
This resolves the copied “prediction requests” wording for this implementation. A separate
numeric estimator deadline is not prescribed; estimator latency is reported, not invented.

## Task status

| Task | Status and evidence |
|---|---|
| T039 | Implemented and reverified: coordinate/span validation, center matching, operational/availability filters, stable sorting and pagination. Unknown/stale estimates cannot affirm available spaces. |
| T040 | Local pass: populated search, empty search and detail all below 2,000 ms in host and Compose measurements, including concurrent estimation load. |
| T041 | Existing calculations preserved and reverified: deduplication, time window, dwell/gap, nearest geofence, capacity clamping, null/unknown and authenticated computation-only API. |
| T042 | Implemented: independent original observation/provider-update and actual service-update times; UTC normalization; inclusive 300-second/24-hour freshness boundaries; unknown provider times remain null. |
| T043 | Implemented with explicitly simulated tariffs: selected-facility occupancy, CAD rates, additional fees, tax flag, invalid/missing ID errors and independent unavailable pricing. |
| T044 | Local pass at 25 simultaneous estimators, 1,000 observations each, with 5 concurrent facility-read clients. Results do not certify production scale or all 10,000-observation workloads. |
| T045 | Implemented/tested: live-source outage to cached/unknown behavior, recovery, unaffected catalog/tariffs, healthy degraded readiness and persisted aggregate fallback across container restart. |

## Executed automated checks

```powershell
.\.venv\Scripts\python.exe -m pytest -c services/parking-analytics/pytest.ini services/parking-analytics/tests -q
.\.venv\Scripts\python.exe -m pytest -c scripts/scaling/pytest.ini scripts/scaling/tests -q
.\.venv\Scripts\python.exe services/parking-analytics/tools/verify_compose.py --output services/parking-analytics/performance-compose.json
```

- **74 S4 tests passed**, including adopted OpenAPI response-schema checks, input/error/auth
  behavior, the original estimator scenarios, freshness boundaries, fallback/recovery,
  SQLite aggregate restart tests and actual network HTTP timing/concurrency.
- **24 existing T009 tests passed**; T009 files were not changed.
- **21 existing registry tests passed** inside the isolated Compose registry container,
  including PostgreSQL-backed tests; registry source/contracts were not changed.
- Total: **119 passing tests** across those suites. Starlette emits the existing HTTPX
  TestClient deprecation warning; no dependency migration was attempted.
- `git diff --check` passed. Source/configuration changes are entirely inside S4.

Host testing used Python 3.11.9 on Windows with 20 reported logical CPUs. Container
verification used the existing Python 3.12 Docker base and the team's PostgreSQL/HAProxy
topology. Root Compose does not set service CPU/memory limits; these are local development
measurements rather than provisioned production-resource guarantees.

## Timing results

Sources: [host JSON](performance-local.json) and [Compose JSON](performance-compose.json).
The latter records the finished implementation, including the S4-owned Compose overlay.
Both tests used 3 simulated facilities; 1,000 observations per estimate (500 pseudonymous
subjects with two timestamps); 3 warm-up rounds; 10 serial calls per read operation; then
3 rounds of 25 simultaneous estimation calls plus 5 concurrent readers. Every facility
read must finish within 2,000 ms; errors fail the benchmark. Both recorded zero errors.

| Operation | Host max, ms | Compose max, ms |
|---|---:|---:|
| Sequential populated search | 4.188 | 4.117 |
| Sequential empty search | 3.964 | 4.059 |
| Sequential facility detail | 3.985 | 3.898 |
| Populated search during estimation | 294.804 | 251.290 |
| Empty search during estimation | 67.810 | 55.299 |
| Facility detail during estimation | 52.466 | 59.848 |
| Estimation, reported without an invented deadline | 334.484 | 271.802 |

Each benchmark recorded **75 measured estimation requests**, a peak of **25 in flight**,
and **45 facility reads during the mixed-load rounds**. The final Compose estimator p95
was **265.053 ms**. Timing is measured end to end over HTTP, including client parsing;
startup and explicitly documented warm-up are outside the measured request intervals.
No further performance optimization was needed for the defined local workload.

## Compose and outage evidence

`tools/verify_compose.py` uses the unmodified root Compose plus `compose.parking.yml`
and a temporary random-port override. A uniquely named project starts only S4, PostgreSQL,
both registry APIs and HAProxy. It verifies:

1. S4 registration under `parking-analytics` and the original advertised container endpoint.
2. A later heartbeat advances `last_heartbeat_at` through the existing registry gateway.
3. Search/detail and the 25-request workload pass over the built container's real HTTP port.
4. The existing SQLite/API and PostgreSQL registry regression tests pass.
5. Recreating only parking with its source unavailable still returns the catalog/tariffs,
   the original persisted occupancy aggregate and cache timestamp, `is_live: false`,
   stale/simulated provenance and `availability_status: unknown`; `/ready` remains 200.
6. Test-owned containers and volumes are removed afterward. S1 and other domain services
   are never started by this check.

Source-backed live/cached transitions use a test-only provider in the unit tests; no real
provider is falsely claimed. Bundled simulated observations remain marked `simulated`
even during fallback. Tariff provider dates are unknown and null. Actual service update
timestamps record real ingest/cache events; they are not substituted for provider dates.

## File manifest

All paths below are relative to `services/parking-analytics/`:

- Updated: `app/main.py` (refresh lifecycle, readiness, standard health/errors),
  `app/models.py`, `app/routes.py`, `app/catalog.py`, `app/analytics.py` (null geofence only),
  `app/fixtures/parking.json`, `tests/test_parking.py`, `requirements-dev.txt`, `Dockerfile`,
  and `README.md`.
- Created: `app/auth.py`, `app/errors.py`, `app/cache.py`, `app/service.py`, `app/sources.py`,
  `app/fixtures/pricing.json`, `tests/conftest.py`, `tests/test_s4_contract.py`,
  `tests/test_fallback.py`, `tests/test_performance.py`, `tools/benchmark.py`,
  `tools/verify_compose.py`, `compose.parking.yml`, both performance JSON reports, and this record.
- Runtime dependencies unchanged. Added JSON Schema validation as a test-only dependency;
  PyYAML is explicitly declared for contract tests and was already a Uvicorn dependency.

See [README](README.md) for local server, search/detail, authenticated estimation,
Docker Compose, outage demonstration and reproducible benchmark commands. Pre-contract
clients must migrate from the old `facilities`/`occupancy` envelopes to T008's `items`.

## Remaining dependencies

Facility locations, capacities, occupancy observations and tariffs remain synthetic fixtures.
No approved live occupancy, facility or tariff provider exists in the inspected repository.
S4 needs an approved provider integration and production storage/refresh operations before
claiming current real-world availability/prices. The optional SQLite aggregate cache and
fixture files are S4-owned; they do not use the registry database or another service's data.

Internal bearer credentials are explicitly configured local service identities with
server-side permissions, closed by default. The platform token issuer, expiry/rotation,
revocation and protected deployment trust boundary remain security integration decisions.
This contribution does not choose a platform identity provider or change shared contracts.
Public UI/gateway integration remains with its existing owner. Production concurrency,
large catalogs, long-duration runs, remote networks and worst-case payloads are not certified.

T009, Matthew C's S1, frontend, registry, HAProxy, shared contracts, root Compose and other
service source files are preserved. The changes are isolated to the S4 feature branch.
