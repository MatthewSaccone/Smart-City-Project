# S6 Public Transit Analytics — T054–T062

Implements the six public GET operations in the adopted
[T008 S6 contract](../../docs/api/contracts/S6-Public-Transit.md) and its
[OpenAPI specification](../../shared/api-contracts/s6-public-transit.openapi.yaml).
These v1 reads **do not require JWTs**. Provider credentials never belong in caller
requests. The registry API key is used only by the existing shared registry client.

The bundled timetable and historical observations are **simulated**. No transit
operator, live feed, GTFS integration or production passenger measurement is claimed.
Default arrival results use published synthetic schedules, never fabricated live
predictions. All source labels for simulated inputs begin with `simulated:`.
See [verification and task coverage](VERIFICATION.md) for executed checks and limits.

## Run independently (PowerShell, repository root)

Use the team's existing Python 3.11+ environment. Dependencies follow the other
services; only Pydantic is made explicit alongside existing FastAPI/httpx/uvicorn.
The S6 development requirements also declare `sniffio` for HTTPX's async load-test
client. Profiling found repeated failed optional-module imports without it on
Windows; installing the declared development dependencies removes that measurement
overhead. It does not add runtime infrastructure or change the acceptance target.

```powershell
.\.venv\Scripts\python.exe -m pip install -r services/transit-analytics/requirements-dev.txt
$env:PYTHONPATH = (Resolve-Path .\services).Path
$env:REGISTRY_URL = ''
$env:TRANSIT_DATABASE_PATH = 'services/transit-analytics/data/transit.sqlite'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir services/transit-analytics --host 127.0.0.1 --port 8086
```

Open <http://127.0.0.1:8086/docs>. `/health` returns
`{"service":"transit-analytics","status":"healthy"}`. `/ready` returns the same
two-field shape with `status: ready` while a valid schedule can serve the core
operations. Missing/expired schedules produce a standard retryable 503 error.
An optional live feed is not a readiness dependency.

## Run in Docker Compose

The root Compose service already advertises `transit-analytics` at port 8086 and
uses the shared registry client. No root configuration or teammate service changed.
Use the S6-only overlay to retain its database across container replacement:

```powershell
# Configure the existing root .env secrets required by the team's Compose file.
$transitCompose = @('-f','docker-compose.yml','-f','services/transit-analytics/compose.transit.yml')
docker compose @transitCompose up --build -d transit-analytics
Invoke-RestMethod 'http://localhost:8086/health'
Invoke-RestMethod 'http://localhost:8086/ready'
```

The overlay adds only S6's `transit-datasets` volume, environment and health check.
The existing registration endpoint, five-second heartbeats, retries and graceful
deregistration are reused. Docker runs uvicorn with `exec` so shutdown reaches the
application. T091's frontend/gateway and whole-system integration remain separately
owned work.

## Endpoints and examples

All collections filter, sort deterministically, then paginate. Defaults are
`limit=25`, `offset=0`; maximum limit is 100. Undocumented/repeated parameters,
invalid enums, nonfinite coordinates, blank IDs and invalid time intervals return
422. GET bodies return 400. Every response carries `X-Request-ID`; errors use the
platform `error` envelope. The checked-in API contracts are unchanged.

| GET path | Parameters | Behavior |
|---|---|---|
| `/v1/transit/stops` | Required `latitude`, `longitude`, `radius_m`; optional `mode`, `sort_by=distance_m\|name`, `sort_order`, `limit`, `offset` | Geodesic radius `(0,20000]` m; modes `bus`, `tram`, `train`; ties by stop ID |
| `/v1/transit/routes` | Same area/mode/page fields; `sort_by=short_name\|route_id` | Route matches when an advertised stop is inside the area; deduplicated |
| `/v1/transit/stops/{stop_id}/arrivals` | Optional `start_at`, `window_minutes=1..180` (default 60), `route_id`, `sort_by=display_at`, `sort_order`, pagination | Known stop required; unknown stop 404; schedule/live arrival and departure independently nullable |
| `/v1/transit/delays` | At least `route_id` or `stop_id`; optional `sort_by=event_at\|delay_seconds`, default descending; pagination | Positive reported delays only; route/stop filters intersect |
| `/v1/transit/disruptions` | At least `route_id` or `stop_id`; optional `status=active\|planned\|resolved`, `kind=disruption\|cancellation\|outage\|detour`, `sort_by=updated_at\|starts_at`, default descending; pagination | Structured alerts; original dates retained; missing trustworthy dataset returns 503 |
| `/v1/transit/occupancy` | At least `route_id` or `vehicle_id`; optional `sort_by=observed_at\|route_id`, default descending; pagination | Qualitative levels and known numeric observations; unknown counts remain null |

Except where stated otherwise, sort order defaults ascending. Descending reverses
the primary order while ID tie-breakers remain ascending. IDs are opaque nonblank
strings, maximum 128 characters. All output instants normalize to UTC `Z`.

```powershell
$base = 'http://127.0.0.1:8086'
Invoke-RestMethod "$base/v1/transit/stops?latitude=43.0096&longitude=-81.2737&radius_m=1500" | ConvertTo-Json -Depth 8
Invoke-RestMethod "$base/v1/transit/routes?latitude=43.0096&longitude=-81.2737&radius_m=1500&mode=bus" | ConvertTo-Json -Depth 8
Invoke-RestMethod "$base/v1/transit/stops/stop-102/arrivals?window_minutes=60" | ConvertTo-Json -Depth 8
Invoke-RestMethod "$base/v1/transit/delays?route_id=route-5" | ConvertTo-Json -Depth 8
Invoke-RestMethod "$base/v1/transit/disruptions?route_id=route-5&kind=cancellation" | ConvertTo-Json -Depth 8
Invoke-RestMethod "$base/v1/transit/occupancy?route_id=route-5" | ConvertTo-Json -Depth 8
```

A stop response includes `items`, `pagination`, `as_of`, `source`,
`source_updated_at`, `data_status: scheduled`, `is_live: false`, `is_stale: false`,
and `schedule_valid_through`. `scheduled` is the contract's only allowed directory
status, including simulated timetables; the `simulated:` source makes that provenance
explicit. A nearby result includes `stop-102` (Central Station), its coordinates,
distance, supported modes and route IDs. A simulated occupancy result includes:

```json
{
  "route_id": "route-5", "vehicle_id": "vehicle-demo-31",
  "occupancy_level": "moderate", "passenger_count": null,
  "capacity": null, "occupancy_percent": null,
  "observed_at": "2026-11-10T11:59:30Z"
}
```

It is wrapped in the collection shape with `coverage_status: partial` and
`provenance.data_status: simulated`, `is_live: false`, and its truthful historical
freshness state. It does not describe current passenger counts. Example scheduled
arrival event (date/time varies according to the timetable query):

```json
{
  "scheduled_at": "2026-11-22T12:00:00Z", "estimated_at": null,
  "display_at": "2026-11-22T12:00:00Z", "basis": "schedule", "delay_seconds": null
}
```

Arrival/departure collections also return `stop_id`, `as_of`, pagination and
provenance. The interval is half-open `[start_at,start_at+window_minutes)`; an item
qualifies when at least one of its events falls inside it and sorts by its first
in-window display time, then trip ID. `start_at` defaults to evaluation time,
must be timezone-aware, no earlier than five minutes ago, no later than 24 hours
ahead, and the interval must fit the published schedule horizon. Missing estimates
never become invented zeros. Fresh authorized input can supply signed early/late
estimates and cancellations; simulations never enable `live_estimate` output.

## S6-owned data and refresh

`app/database.py` creates a SQLite schema automatically. It keeps at most one
versioned normalized timetable and one bounded live snapshot in the active source
namespace. The registry never stores S6 domain data. Snapshot content hashes prevent
unchanged refreshes from rewriting receipt timestamps. Reads use installed memory
snapshots, not disk or provider calls; input refresh runs in a background worker.

The normalized JSON formats are the executable Pydantic schemas in
`app/models.py`; the bundled JSON files are complete examples. Inputs are capped at
8 MiB, with bounded collections, unique IDs, referential validation and UTC dates.
The timetable includes agencies, routes, stops, service calendars (weekdays,
additions, removals), trips and ordered stop times. Seconds since UTC service-day
midnight may exceed 24 hours for overnight trips. Calendar/source version and
validity horizon survive restart. The fixture contains 169 daily trips, four stops
and three modes and is valid from 2026-11-01 through the 2027-01-01 UTC horizon.

This adapter expects **upstream-normalized UTC service calendars**. It does not
parse raw GTFS, local operator timezones or GTFS-Realtime protobuf. A future approved
provider adapter must correctly normalize operator calendars/DST before producing
this format. No such adapter or provider is claimed here.

Live JSON references the installed schedule version and independently optional
`trips`, `disruptions`, and `occupancy` categories, each with a timestamp and
coverage declaration. Trip updates identify the service date, trip, stop and optional
vehicle; their estimates may be null. Data labels are controlled by `simulated`;
simulated feeds **or** timetables cannot produce genuine live-estimate claims.
Future observation/update times, invalid references and inconsistent occupancy
numbers are rejected; a bad refresh preserves the last accepted snapshots.

| Setting | Default | Meaning |
|---|---|---|
| `TRANSIT_DATABASE_PATH` | `data/transit.sqlite` relative to the working directory | S6's own persisted dataset/cache; use an instance-owned path/volume |
| `TRANSIT_SCHEDULE_FILE` | bundled `app/fixtures/schedule.json` | Approved normalized timetable, or explicitly simulated fixture |
| `TRANSIT_LIVE_FILE` | bundled `app/fixtures/live.json` | Normalized update snapshot; bundled observations are historical/simulated |
| `TRANSIT_LIVE_MODE` | `file` | `file` or `unavailable` to simulate an outage |
| `TRANSIT_REFRESH_SECONDS` | `5` | Background file reconciliation interval; 0.1–3600 seconds |
| `TRANSIT_LIVE_RETENTION_SECONDS` | `86400` | Maximum retained-live-snapshot receipt age; positive, at most seven days |

No HTTP provider requests run on the public request path. Filesystem adapters are
for local bounded snapshots, not uncontrolled network mounts. Their periodic
content reconciliation recovers changed or missed file updates without a broker.
No platform-wide broker or completed S5–S7 integration is claimed.

## Freshness, outages and recovery

The oldest applicable feed/item observation time determines the conservative live
freshness state. Exactly 60 seconds is fresh; anything older is stale. A missing
timestamp cannot establish freshness. Source and receipt timestamps remain distinct;
processing time is never substituted for a provider update. Arrivals only use live
estimates when the provider snapshot is available, non-simulated and fresh, and the
timetable is non-simulated. Otherwise they retain `scheduled_at`, clear estimates
and delay values, and use `display_at=scheduled_at`, `basis: schedule`.

On source failure or restart, a valid retained timetable still serves core reads.
Known stale alerts/occupancy may be returned with original dates and partial
coverage; after the maximum cache retention, live data becomes unavailable. Missing
delay/occupancy coverage produces `items: []`, `coverage_status: unavailable` and
`live_update_status: unavailable`. Missing disruption coverage returns 503, avoiding
a misleading claim that no disruptions exist. Scheduled data has its own validity
horizon and is not made stale merely by a live-feed outage.

For an isolated native demonstration, start with a clean instance-owned cache and
set `$env:TRANSIT_LIVE_MODE='unavailable'` before launch. Existing real data must not
be renamed or removed for a demonstration. The integration script below performs
source-loss/restart tests only inside its own disposable container.

## Reproduce verification

```powershell
.\.venv\Scripts\python.exe -m pytest -c services/transit-analytics/pytest.ini services/transit-analytics/tests -q
.\.venv\Scripts\python.exe services/transit-analytics/tools/benchmark.py --spawn-local --output services/transit-analytics/performance-local.json
.\.venv\Scripts\python.exe services/transit-analytics/tools/verify_compose.py --output services/transit-analytics/verification-compose.json
```

The load script starts a separate local server/cache, validates actual responses
against the adopted OpenAPI, runs 20 serial arrivals, then three waves of 50
simultaneous arrivals and three waves of 50 mixed endpoint requests. It records
averages, p50/p95/max, status counts, timeouts and achieved peak in-flight counts.
The target is 1,500 ms for every arrival response, not just an average or p95.
Both scripts remove their own test resources and never load public APIs or team
deployments. Docker tests also verify real registration, heartbeats, restart fallback,
graceful deregistration and the existing registry regression suite.

Unselected providers/licenses, raw GTFS parsing, operator-local timezone conversion,
production catalog sizes, cloud latency and multi-replica storage are deployment
dependencies. The implementation and local tests do not prove production scalability.
