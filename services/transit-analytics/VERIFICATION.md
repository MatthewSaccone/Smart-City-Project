# S6 implementation and verification — T054–T062

Verified November 22, 2026, America/Toronto. Machine-readable reports use November 23
UTC instants. Work is on `transit-analytics-aaliyan`, created from fetched `main`
commit `7e91f7d`. S6 work is isolated to this feature branch; publishing and merging
remain separate from local implementation and verification.

The implementation follows the existing FastAPI/service-registry conventions and
the unchanged T008 Markdown/OpenAPI contracts. S6 v1 reads remain public as specified.
The repository initially contained only S6's starter `/`, `/health`, registration
code and Dockerfile. S5 is also still a starter in this fetched version; no existing
transit operator/feed configuration was found.

## Task status

| Task | Status and executed evidence |
|---|---|
| T054 | Implemented: stop arrivals/departures from calendar-backed timetables, optional validated live overlays, route/time/window filters, terminating/originating trips, overnight service and standard 404/422 responses. Functional and real-HTTP contract tests passed. |
| T055 | Locally verified: every arrival request in the recorded native and Docker workloads met 1,500 ms, including the focused 50-concurrent-request waves and arrivals within mixed traffic. |
| T056 | Implemented: positive vehicle/trip/stop delay reports derived from known estimates and schedules; early estimates have signed negative arrival delays and are excluded from positive-delay collections. |
| T057 | Implemented: original provider/item and cache timestamps, exact 60-second inclusive freshness, 60.001/61-second stale cases, missing timestamps, invalid future timestamps and conservative stale-item handling. |
| T058 | Implemented: disruptions, cancellations, outages and detours, status/location filters, honest partial cached coverage and 503 when no trustworthy alert dataset exists. |
| T059 | Implemented and restart-tested: S6's SQLite schedule persists when both source files disappear; arrivals retain schedule basis, null estimates and original cache timestamps. Readiness survives live-feed failure. |
| T060 | Implemented: route/vehicle occupancy, qualitative levels, nullable unknown counts/capacity/percentage, consistency validation and explicit unavailable coverage. No individual passenger records are stored. |
| T061 | Locally verified at the contract's approved 50-request target: three focused and three mixed waves, each with 50 simultaneously outstanding client requests; no HTTP failures/timeouts. |
| T062 | Implemented: WGS84 geodesic stop search, routes serving nearby stops, 20,000 m radius cap, three supported modes, deterministic sorting, ID tie-breaks and pagination after filtering. |

"Implemented" describes the service capability and executed tests, not a claim of
an operational live provider. Bundled data is synthetic. Controlled unit-test inputs
exercise fresh live-estimate paths without claiming real observations.

## Executed checks

| Check | Result |
|---|---|
| Full S6 suite, including real HTTP 50-request tests | **124 passed**, 1 existing Starlette/httpx deprecation warning |
| Existing S1 regression suite | **57 passed** |
| Existing S2 regression suite | **137 passed** |
| Existing S4 regression suite | **125 passed** |
| Existing registry suite, inside isolated PostgreSQL-backed Docker test project | **21 passed** |
| Actual HTTP responses against the adopted S6 JSON schemas | All six operations passed, plus health/readiness in functional tests |
| Real registry registration and subsequent five-second heartbeat | Passed |
| Source-file loss, container restart and persisted schedule/cache recovery | Passed |
| Graceful shutdown and registry deregistration | Passed |
| Whitespace and Python syntax checks | `git diff --check` and compilation checks passed |

Total: **464 passing tests across those suites**. No S3/S5/S7 test-suite result is
claimed. Their implementations, S1/S2/S4, the registry, shared helpers, frontend,
gateway, API contracts and T009 files were not modified. S1 regression execution
explicitly disabled real database/registry access; S2/S4 fixtures provide isolation.

## Final HTTP measurements

All timings are client-observed milliseconds, including request/response transport.
The scripts use one server worker, local loopback HTTP, persisted connections, six
contract-validation warmups, a 20-request serial baseline, and three waves of 50
focused arrivals followed by three waves of 50 mixed requests across all six domain
endpoints. The data is 169 simulated daily trips, four stops, three routes/modes and
historical synthetic trip/alert/occupancy records. No public API or teammate service
received load. The client timeout is five seconds; acceptance still requires every
arrival to complete within 1,500 ms, not merely an average or p95.

Native environment: Python 3.11.9 on `Windows-10-10.0.26200-SP0` as reported by
Python, 20 logical CPUs. Client packages: httpx 0.28.1, httpcore 1.0.9, anyio 4.16.0,
sniffio 1.3.1. The Docker server uses the existing Python 3.12-slim pattern, one
worker; Docker reported 20 CPUs and 8,130,506,752 bytes of memory. The existing
registry's two replicas, its PostgreSQL database and HAProxy ran in the isolated
Compose project. Its test resources were removed afterward.

| Environment / workload | Successful / total | Average | p50 | p95 | Maximum | Errors / timeouts |
|---|---:|---:|---:|---:|---:|---:|
| Native serial arrivals | 20/20 | 1.701 | 1.634 | 2.041 | 2.371 | 0/0 |
| Native arrivals, 50 concurrent | 150/150 | 55.530 | 52.597 | 99.576 | 116.379 | 0/0 |
| Native mixed, 50 concurrent | 150/150 | 60.989 | 57.467 | 98.833 | 108.684 | 0/0 |
| Docker serial arrivals | 20/20 | 3.138 | 3.043 | 4.113 | 5.455 | 0/0 |
| Docker arrivals, 50 concurrent | 150/150 | 61.704 | 58.015 | 101.110 | 117.947 | 0/0 |
| Docker mixed, 50 concurrent | 150/150 | 63.391 | 59.271 | 112.369 | 126.608 | 0/0 |

Mixed traffic contained 25 arrival requests per three-wave run; all 25 met the
deadline. Their maximums were **102.190 ms native** and **113.234 ms Docker**.
Both concurrent workloads achieved a measured peak of **50 in-flight client
requests**. These are local checks on a bounded fixture; production/cloud
scalability is explicitly **not verified**.

Canonical evidence: [native report](performance-local.json) and
[Docker integration report](verification-compose.json). Each contains the actual
measurement timestamp, dataset, package/environment details, outcomes and qualifiers.

## Failures found and corrected

The initial load test exceeded the deadline even though all responses succeeded.
The retained [initial report](performance-initial.json) records a maximum mixed
arrival response of **1,661.710 ms** and one arrival over the 1,500 ms target.
The implementation originally constructed response models for out-of-window
timetable entries. It now filters those entries first, computes calendar activity
once per service day and uses async handlers for the in-memory read path, avoiding
unnecessary thread-pool handoffs. It still performs no provider/database I/O per
request.

Native test-runner results remained variable after that change. A separate timed
server wrapper observed server processing below 5 ms while client round trips were
much larger. Profiling the client found over 1,000 repeated optional-module import
lookups in `httpcore._synchronization.current_async_library`, with over a second
spent searching/importing. The installed httpcore falls back when `sniffio` is
absent, but repeats the failed import. S6's development requirements now declare
the small `sniffio` dependency; installing 1.3.1 removed that measured overhead.
The target, request counts, concurrency, warmups and timeouts were not relaxed.
The full suite and the recorded native/Docker workloads then passed.

Additional regression coverage protects against a schedule refresh exposing live
records validated against an older schedule revision. Unchanged content preserves
its original receipt timestamp when rebound to a validated schedule snapshot.

## File manifest

Every path below is relative to `services/transit-analytics/`. Four existing files
were modified and 22 files added; all work remains scoped to S6.

| File | Change / purpose |
|---|---|
| `app/main.py` | Modified: initialize S6 storage and worker, wire domain routes, contract probes and lifecycle cleanup; preserve registry behavior |
| `Dockerfile` | Modified: `exec` uvicorn for correct signal delivery and graceful deregistration |
| `requirements.txt` | Modified: declare Pydantic explicitly, matching existing service conventions |
| `README.md` | Modified: startup, deployment, exact endpoint/parameter inventory, requests/responses, data formats and limitations |
| `app/models.py` | Added: public response and normalized input models, bounded data, timestamp/reference validation |
| `app/database.py` | Added: idempotent S6-owned SQLite snapshot schema, bounded persistence, unchanged-content handling |
| `app/repository.py` | Added: independent schedule/live refresh, snapshot binding, durable restart fallback and bounded retention |
| `app/service.py` | Added: calendar-backed arrivals, geodesic searches, live/fallback policy and delay/alert/occupancy queries |
| `app/routes.py` | Added: all six contracted GET operations and query validation |
| `app/errors.py` | Added: platform error envelopes, validated request IDs, GET-body and repeated-parameter rejection |
| `app/fixtures/schedule.json` | Added: explicitly simulated versioned UTC timetable, agencies, routes, stops, calendars and trips |
| `app/fixtures/live.json` | Added: explicitly simulated historical trip/alert/occupancy examples with fixed original dates |
| `compose.transit.yml` | Added: S6-only persistent volume, settings and health check; no root Compose edit |
| `requirements-dev.txt` | Added: existing pytest/JSON-schema/YAML conventions and declared HTTPX async-detection helper |
| `pytest.ini` | Added: S6-specific test/import paths |
| `tests/conftest.py` | Added: isolated source copies, temporary database, deterministic clock and test data helpers |
| `tests/test_api.py` | Added: endpoint/schema/validation/geography/filter/sort/pagination tests |
| `tests/test_fallback.py` | Added: 60-second boundary, truthful outage behavior, calendars and restart durability tests |
| `tests/test_data_and_lifecycle.py` | Added: invalid source data, references, snapshot consistency, worker and registry lifecycle tests |
| `tests/test_performance.py` | Added: real HTTP baseline, 50 focused arrivals and 50 mixed requests with mandatory assertions |
| `tools/benchmark.py` | Added: reproducible native/owned-loopback load test, response conformance and actual statistics |
| `tools/verify_compose.py` | Added: disposable Docker/registry tests, load, source loss/restart, regression and cleanup |
| `performance-initial.json` | Added: retained failed initial measurements, without obscuring the development failure |
| `performance-local.json` | Added: final successful native measurements |
| `verification-compose.json` | Added: final successful Docker integration/performance evidence |
| `VERIFICATION.md` | Added: task traceability, executed results, manifest and review boundaries |

## Remaining dependencies and review boundaries

- No authoritative transit provider, licensing agreement, real-time feed or genuine
  passenger measurement is configured. The fixtures cannot be advertised as live.
- The adapter accepts normalized JSON, not raw GTFS Schedule/GTFS-Realtime. Operator
  local-time/DST conversion must be handled by a future approved normalizer; current
  internal service calendars are explicitly UTC.
- The demo schedule's validity ends at the 2027-01-01 UTC horizon. Expired schedules
  become unready; the service never silently regenerates dates to make them current.
- SQLite/instance-owned volumes support this implementation and its measured local
  workload. Cloud deployment, larger real catalogs, multi-replica data distribution,
  sustained load, provider credentials/rate limits and licensing need separate
  operational verification.
- T091's shared frontend/gateway integration stays with its assigned owner. No
  platform broker or completion of all S5–S7 T010 work is claimed.
- No configured repository linter/type-checker was found for S6. Runtime validation,
  contract schemas, Python compilation and whitespace checks were used; no result
  from an unexecuted tool is claimed.

## Reproduction and Git review

From the repository root, the complete commands and six endpoint request/response
examples are in [README](README.md). The three principal checks are:

```powershell
.\.venv\Scripts\python.exe -m pytest -c services/transit-analytics/pytest.ini services/transit-analytics/tests -q
.\.venv\Scripts\python.exe services/transit-analytics/tools/benchmark.py --spawn-local --output services/transit-analytics/performance-local.json
.\.venv\Scripts\python.exe services/transit-analytics/tools/verify_compose.py --output services/transit-analytics/verification-compose.json
git status --short --branch
git diff --check
```

The branch is `transit-analytics-aaliyan`. Use `git status --short --branch` and
`git log -1 --oneline` to inspect its current commit and publication state.
Local `main` was updated only by fast-forward before branch creation.
Existing feature branches and teammates' changes were preserved. The main API
contracts, service implementations outside S6, shared utilities, infrastructure,
frontend and T009 have no changes from this task.
