# T009 / T039 / T041 implementation and verification

Owner: Aaliyan Muhammad. Verification date: November 9, 2026 (America/Toronto).
Branch: `feature/aaliyan-t009-t039-t041`, based on `main` at `fa6663b` after checking
GitHub's branch head. The checks below were run before the feature commit.

## Implemented scope

| Task | Implemented behavior | Limits |
|---|---|---|
| T039 | Validated bounding-box facility search, inclusive edges, empty results, calculated occupancy alongside each result | Simulated facility catalog; no map UI, reservation or live provider |
| T041 | Stateless batch occupancy estimation using recent, deduplicated subjects and geofenced dwell; explicit unknown and over-capacity evidence | Participation/GPS/vehicle assumptions; simulated replay or submitted observations, no ingestion/storage |
| T009 | Documented/configured feedback policy; sustained triggers, cooldowns, min/max, conservative scale-in, readiness hold; registry-based round robin; simulation and real local process actuator | Prototype uses starter HTTP endpoints; no deployed cloud autoscaling or ASR5 acceptance claim |

No T043 pricing/detail work is included. The GUI, shared API templates, registry/client,
HAProxy, Compose and unrelated service source files remain unchanged.

## Changed files

Parking (`services/parking-analytics/`):

- `app/main.py`: include the new router; keep existing health/root/registry lifecycle.
- `app/models.py`, `app/routes.py`, `app/analytics.py`, `app/catalog.py`: parking-owned
  schemas, endpoints, calculations and fixture loader.
- `app/fixtures/parking.json`: synthetic facilities and raw time/location samples.
- `requirements.txt`: declare Pydantic v2, already used by the registry, as a direct dependency.
- `requirements-dev.txt`, `pytest.ini`, `tests/test_parking.py`: test dependencies/configuration/tests.
- `README.md`: API examples, calculation method, assumptions and exact commands.

Scaling (`scripts/scaling/`):

- `policy.json`, `policy.py`: configured decision loop without infrastructure side effects.
- `routing.py`: existing-registry discovery and eligible-instance round robin for the harness.
- `simulate.py`: labeled deterministic virtual-time scenario.
- `local_demo.py`: isolated local registry/workers, measured request windows and process actuator.
- `check_registry.ps1`: disposable PostgreSQL helper for existing regression suites.
- `pytest.ini`, `tests/test_policy.py`, `README.md`: tests, configuration and demonstration commands.

Documentation: this record and `docs/architecture/Scaling-and-Routing-T009.md`.

## Automated test results

Environment: Windows host, isolated Python 3.11 environment; FastAPI 0.162.2,
Pydantic 2.14.3, HTTPX 0.28.1, pytest 8.4.2. Runtime dependencies reuse existing project
ranges. The existing Dockerfile also built and ran with Linux/Python 3.12.

| Command (repository root unless stated) | Result |
|---|---|
| `.\.venv\Scripts\python.exe -m pytest -c services/parking-analytics/pytest.ini services/parking-analytics/tests -q` | 32 passed |
| `.\.venv\Scripts\python.exe -m pytest -c scripts/scaling/pytest.ini scripts/scaling/tests -q` | 24 passed |
| `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/scaling/check_registry.ps1` | 21 existing registry tests passed, including all 7 PostgreSQL tests |

Total: **77 passing tests**. An earlier registry run without PostgreSQL had 14 passed and
7 skipped; the disposable PostgreSQL run resolved those skips. Existing TestClient usage
emits a Starlette deprecation warning for HTTPX; no test failed and no dependency migration
outside this task was attempted.

Parking coverage includes geographic boundaries, invalid/nonfinite input, missing bounds,
empty results, fixture provenance, changing observation input, duplicate subjects/timestamps,
dwell and freshness edges, stale/future/departed users, sampling gaps, overlapping geofences,
capacity saturation, unknown estimates, and existing registry lifecycle/key requirements.

Scaling coverage includes baseline stability and margin, 200-rps demand decisions, brief
spikes, telemetry gaps, cooldown/readiness hold, healthy slow scale-in, error/latency guards,
limits/invalid configuration, discovery refresh, round robin, lease expiry, draining and
bounded retry to another instance. No performance result is inferred from a unit test.

## Executed local process prototype

```powershell
.\.venv\Scripts\python.exe scripts/scaling/local_demo.py --rps 45 --seconds 30
```

Observed run (starter S1 root endpoint):

- Scale-out decision: **1 -> 3 workers at 15.062 seconds** from workload start.
- Launch-to-discovery times: **1.016, 1.032 and 1.031 seconds** (initial plus two new workers).
- **1,350 HTTP requests, zero errors**, measured overall p95 approximately **16 ms**.
- Request distribution: initial worker 900; each new worker 225, proving new discovered
  instances actually received HTTP requests.
- Six measured windows completed in about 32.2 seconds, including worker startup pauses.
- The run cleaned up its own loopback processes and temporary database.

The final trigger margin retains the same behavior for this 45-rps scenario and is covered
by the final policy test run. This is evidence of local actuation, discovery and distribution,
not a benchmark of S1 traffic analytics. The harness pauses workload during actuation;
reported request latency excludes that pause. Real scale-in was not exercised in this
short run; its policy was exercised by tests/simulation.

The executed virtual-time simulation used 20/200/5 rps, requested ten replicas at virtual
second 30 after a peak beginning at second 20, and requested one scale-in at second 185.
Latency and immediate readiness were synthetic inputs, clearly labeled in the output.

## Container and configuration checks

```powershell
docker build -t scms-parking-t039-t041 -f services/parking-analytics/Dockerfile services
```

Build passed without modifying the existing Dockerfile. A disposable container on a random
loopback port passed real HTTP `/health`, geographic search, simulated-source labeling and
POST occupancy calculation checks. The fixture replay calculated two subjects at Central.
The container was removed afterward.

`docker compose config --quiet` passed with local placeholder values supplied for the
required registry key and PostgreSQL password. The full Compose stack and registry
HAProxy failover were not run as an acceptance test. No shared deployment was changed.

## Demonstration and remaining dependencies

Follow [parking commands](../services/parking-analytics/README.md) to search an area and
submit changed observations. Follow [scaling commands](../scripts/scaling/README.md)
to reproduce the simulation, process prototype and registry tests.

Still required: an approved live observation source and shared ingestion/storage contracts;
GUI/gateway integration by its owner; calibration of the proposed per-replica request target;
production orchestration/metrics/draining integration; and representative deployed S1 tests
for all ASR5 timing and p95 targets. The local 45-rps starter demo does not verify a continuous
200-rps analytics workload, cloud startup timing, or post-scale analytics latency.
