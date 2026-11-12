# T011–T020 implementation and verification (S1 Live Traffic Analytics)

Owner: Matthew C. Verification date: November 11, 2026 (America/Toronto).

> The numbers below come from one development run in a Linux environment. Re-run the
> commands on the demo machine and replace the numbers before using them as final evidence.

## Implemented scope

| Task | Requirement | Implemented behaviour | Limits |
|---|---|---|---|
| T011 | S1 FR1 | Speed of each user from their two newest pings; average speed turned into a congestion level | Simulated users; parked users look like slow traffic |
| T012 | S1 QR1 | Calculation time measured and returned as `calculate_ms` | Measured locally, not on the cloud deployment |
| T013 | S1 FR2 | Route endpoint: level per ~500 m piece and for the whole route | Straight line between the points; no road map |
| T014 | S1 QR2 | Retrieval time measured and returned as `retrieve_ms` | In-memory store inside one instance |
| T015 | S1 FR2.1 | Delay = time at current speed minus time at 50 km/h, per piece and in total | Pieces with no users add no delay and are reported |
| T016 | S1 QR3 | Whole request measured from the client side in tests | Measured locally |
| T017 | S1 FR3 | Area endpoint: users within `radius_m` of a point | Radius limit 50 km |
| T018 | S1 QR4 | Live answers use pings at most 60 s old; older data is labelled `stale_fallback`, `is_live: false` | Cache keeps pings for 10 minutes |
| T019 | S1 FR4 | Every request recalculates from the newest pings | No push updates; the client asks again |
| T020 | S1 QR5 | 50 at-once requests all under 1000 ms | **Placeholder number.** Acceptance needs the value agreed in T002 |

Not included here: GUI integration (T073, T079) and cloud deployment and scaling (T087).
Ingestion, broker and cache (T089), the formal acceptance run (T104) and the S1 load
measurements for T095 were added afterwards and are recorded in
[Testing-T089-T104.md](Testing-T089-T104.md).

## Changed files

All under `services/live-traffic/` except this record:

- `app/main.py`: two added lines that include the new router. Health, root and registry
  lifecycle are unchanged.
- `app/settings.py`, `app/store.py`, `app/analytics.py`, `app/models.py`, `app/routes.py`: new.
- `simulate_users.py`: new demo tool that sends simulated car locations.
- `tests/test_live_traffic.py`, `pytest.ini`, `requirements-dev.txt`: new.
- `requirements.txt`: adds Pydantic v2 as a direct dependency, as parking-analytics does.
- `README.md`: rewritten.

No other service, the registry, the shared client, HAProxy, Compose, the GUI or the shared
API templates were changed.

## Automated tests

Environment: Linux, 2 CPU cores, Python 3.13.18, FastAPI 0.162.4, Pydantic 2.14.3,
HTTPX 0.28.1, pytest 8.4.2.

| Command (repository root) | Result |
|---|---|
| `python -m pytest -c services/live-traffic/pytest.ini services/live-traffic/tests -q` | 41 passed (5 runs in a row). The suite now has 52 tests; the other 11 cover T089 and T096 |
| `python -m pytest -c services/parking-analytics/pytest.ini services/parking-analytics/tests -q` | 32 passed (unchanged service, regression check) |
| `python -m compileall services service-registry` (the CI check) | passed |

| Task | Tests |
|---|---|
| T011 | speed from two pings; slow users are `severe`, fast users are `low`; one ping gives no speed; impossible speeds ignored |
| T012, T014, T016 | area and route requests with 2,000 active users: `retrieve_ms` < 500, `calculate_ms` < 1000, whole request < 1000 ms |
| T013 | 1000 m route gives two segments with their own levels; route endpoint |
| T015 | 25 km/h over 1000 m gives a 72 s delay; fast traffic gives 0, not negative; empty segments reported |
| T017 | users outside the radius are not counted; area endpoint |
| T018 | data exactly 60 s old is live; 61 s old is `stale_fallback`; live preferred over old; no data is `unknown`; store drops pings after 10 minutes |
| T019 | same users send newer pings and the level goes from `severe` to `low` |
| T020 | 50 requests at once with 500 active users, each under 1000 ms |
| Input checks | out-of-range, missing, non-numeric, same start and end, too far apart, future and too-old timestamps all return 422 |
| Starter behaviour | `/health`, `/` and registry registration still work |

## Real server run

The service was started with uvicorn on 127.0.0.1:8081 and fed by `simulate_users.py`.

| Scenario | Observed |
|---|---|
| No data yet | `unknown`, `data_status: no_data` |
| `jam`, route south to north (1,844.8 m) | segments `severe, severe, low, low`; average 13.2 km/h; delay 370.6 s on a normal 132.8 s |
| `jam`, area in the south half, radius 500 m | `severe`, 7.7 km/h, 4 users, `live`, data age 0.5 s |
| `clear` scenario sent afterwards (T019) | all four segments `low`; delay 11.8 s |
| 62 s after the last ping (T018) | same values, `data_status: stale_fallback`, `is_live: false`, data age 64.1 s |

Measured response times with 500 active users (1,000 pings) in the store, half area requests
and half route requests, client on the same machine:

| Load | Success | p50 | p95 | Slowest | Slowest `retrieve_ms` | Slowest `calculate_ms` |
|---|---|---:|---:|---:|---:|---:|
| 50 requests, all at once | 50 / 50 | 84 ms | 129 ms | 133 ms | 1.09 ms | 3.54 ms |
| 500 requests, 50 at a time | 500 / 500 | 119 ms | 323 ms | 745 ms | 2.71 ms | 9.56 ms |

All values are inside the S1 targets (retrieve 500 ms, calculate 1000 ms, return 1000 ms).
This is a local measurement on a small machine, not a cloud performance result.

## Not run

- `docker build` and `docker compose up` (no Docker daemon in the development environment).
  The Dockerfile and Compose file are unchanged.
- Registration against a real registry instance. The lifecycle code is unchanged and its
  unit test passes.
- Any test on the cloud deployment.

## Still required

- The agreed concurrency number from T002, then re-run T020.
- GUI integration by its owner.
- A re-run of the acceptance script (T104) on the cloud deployment once it exists.
