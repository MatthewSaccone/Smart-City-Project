# T009 scaling prototype

See [the design](../../docs/architecture/Scaling-and-Routing-T009.md) for thresholds,
metrics, replica limits, statelessness, routing, scale-in and ASR5 evidence boundaries.
No shared infrastructure, registry contracts, GUI or S1 source files are changed.

## Setup and tests (PowerShell, repository root)

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r service-registry/requirements.txt
.\.venv\Scripts\python.exe -m pytest -c scripts/scaling/pytest.ini scripts/scaling/tests -q
```

The prototype reuses existing FastAPI/Uvicorn/HTTPX/psycopg dependencies. There is no
Kubernetes, Docker socket, new runtime framework or cloud account requirement.

## Virtual-time policy simulation

```powershell
.\.venv\Scripts\python.exe scripts/scaling/simulate.py
```

Inputs: 20 rps baseline, then 200 rps, then 5 rps. Output identifies virtual time,
synthetic latency and immediate-readiness assumptions. It shows bounded scale-out and
slow scale-in; it is not a load test or a measurement of infrastructure performance.

## Actual local workers and discovery

```powershell
.\.venv\Scripts\python.exe scripts/scaling/local_demo.py --rps 45 --seconds 30
```

This opt-in command launches an isolated registry and existing S1 starter workers on
random loopback ports. It creates a temporary SQLite database and random management key,
measures paced request windows, makes scaling decisions, starts additional processes,
waits for their registry records, and routes HTTP calls across discovered workers.
It prints startup/discovery timings, decisions, request counts, errors, p95 and per-instance
distribution. Expected: 1 -> 3 replicas after sustained 45 rps, with requests reaching
the new instances in later windows. It removes its own processes/storage on exit.
No existing registry, container, database or shared deployment is targeted.

For an optional higher-rate prototype run, use `--rps 200 --seconds 30`; this may launch
up to ten local workers and needs sufficient memory. `--service parking-analytics` can
exercise the same registration mechanism using the parking service, still at its root
endpoint. These commands do not generate a representative analytics workload.

The controller's 120-second scale-in behavior is covered by deterministic tests and
the simulation. The short process demo focuses on scale-out/discovery/distribution.
The actuator only drains between completed batches. Ports are allocated optimistically;
a competing process taking a selected port causes an explicit startup failure.

## Existing registry regression suite

Run separately from parking tests to avoid their identically named `app` packages:

```powershell
$env:REGISTRY_DATABASE_PATH = Join-Path $env:TEMP 'scms-local-regression.db'
Push-Location service-registry
..\.venv\Scripts\python.exe -m pytest tests -q
Pop-Location
```

PostgreSQL tests skip unless `TEST_REGISTRY_DATABASE_URL` points to a dedicated disposable
test database. Never direct these tests at shared or production registry state.

With Docker running, this helper starts a uniquely named disposable PostgreSQL container
on a random loopback port, runs both existing registry suites, and removes its own container
and temporary SQLite file. It restores the calling session's environment variables:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/scaling/check_registry.ps1
```

## Verification record

Results are recorded after execution in [the contribution verification record](../../docs/Testing-T009-T039-T041.md).
