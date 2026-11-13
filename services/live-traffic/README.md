# Live Traffic Analytics (S1) — T011 to T020, T089, T104

Works out road congestion and travel delays from the time and location data sent by active users. Owner: Matthew C.

The location data used in demos is **simulated** by `simulate_users.py` or `simulate_feed.py`. No real user data and no external traffic provider is connected by default.

## What is implemented

| Task | Requirement | Where |
|---|---|---|
| T011 | S1 FR1: analyse live time and location data to get congestion levels | `app/analytics.py` (`build_speed_samples`, `congestion_level`) |
| T012 | S1 QR1: calculate within 1000 ms | `calculate_ms` in every response; timing test |
| T013 | S1 FR2: congestion between two user-defined points | `GET /v1/traffic/congestion/route` |
| T014 | S1 QR2: retrieve location data within 500 ms | `retrieve_ms` in every response; timing test |
| T015 | S1 FR2.1: estimated travel delay along the route | `estimated_delay_seconds` in the route response |
| T016 | S1 QR3: return the result within 1000 ms | timing test measures the whole request |
| T017 | S1 FR3: congestion within a user-defined radius | `GET /v1/traffic/congestion/area` |
| T018 | S1 QR4: use location data from the previous 60 seconds | `LIVE_WINDOW_SECONDS`; `data_status` and `is_live` in every response |
| T019 | S1 FR4: update estimates as new data arrives | every request recalculates from the newest pings |
| T020 | S1 QR5: hold response times under concurrent requests | concurrency test (50 at once, see the note below) |
| T089 | ASR7: traffic ingestion, broker and timestamped cache | `app/ingestion.py` (feed poller), `app/broker.py`, `app/store.py`, `app/database.py`, `GET /v1/traffic/status` |
| T104 | Run all S1 acceptance tests and record expected and actual results | `acceptance_test.py`, results in `docs/evidence/S1-acceptance-results.md` |

Partly covered, S1 side only: T095 (`load_test.py` measures 20 and 200 requests per second on one instance; scale-out timing needs T087), T096 (60-second boundary, stale labels, outage and recovery for S1) and T099 (the acceptance evidence file).

## Limits to know about

- **T020 target:** the task sheet says acceptance is blocked until T002 agrees a number. The test uses 50 at-once requests as a placeholder. Change `CONCURRENT_REQUESTS` in `tests/test_live_traffic.py` to the agreed number.
- **Straight-line routes:** the service has no road map. The "route" between two points is the straight line between them, cut into pieces of about 500 m.
- **Shared storage:** location pings are persisted in a dedicated PostgreSQL database named `smart_city_traffic`. All S1 replicas use the same database, so submitted locations are visible across replicas and survive service restarts. Pings older than the 10-minute cache window are removed.
- **Local broker:** the broker remains in-process and delivers events synchronously within the receiving instance. PostgreSQL provides shared location storage; it is not a distributed message broker.
- **Single feed poller:** when `TRAFFIC_SOURCE_URL` is configured, replicas coordinate through a PostgreSQL advisory lock so only one replica polls the external source at a time. Other replicas remain on standby. If the lock-holding replica stops or loses its database connection, another replica can acquire the lock.
- **Ingestion design:** the 5-second polling interval, event fields and in-process broker are implementation defaults. Check them against the T010 design if it becomes available.
- **Authentication:** protected endpoints require a valid Keycloak RS256 JWT. `/health`, `/ready` and the root endpoint remain public. User IDs are scoped to the authenticated subject; do not submit real people's location history.
- **Standalone operation:** the local development command below does not configure PostgreSQL or Keycloak automatically. For the complete authenticated, persistent deployment, use the root Docker Compose configuration.

## Run locally

Use Python 3.11 or newer. From the repository root:

PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r services/live-traffic/requirements-dev.txt
$env:PYTHONPATH = (Resolve-Path .\services).Path
$env:REGISTRY_URL = ""
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir services/live-traffic --host 127.0.0.1 --port 8081
```

macOS or Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r services/live-traffic/requirements-dev.txt
export PYTHONPATH="$(pwd)/services"
export REGISTRY_URL=""
.venv/bin/python -m uvicorn app.main:app --app-dir services/live-traffic --host 127.0.0.1 --port 8081
```

**Note:** this standalone command is intended for local development without registry integration. The complete Docker Compose deployment supplies the PostgreSQL database, Keycloak JWT configuration and registry connection. `/ready` checks JWT configuration, JWKS availability and database availability; it will not report ready if required infrastructure is unavailable.

Open [http://127.0.0.1:8081/docs](http://127.0.0.1:8081/docs) to see and try every endpoint.

## Docker Compose deployment

The complete S1 deployment is managed from the repository root:

```powershell
docker compose up -d live-traffic-postgres
docker compose up -d --build live-traffic
```

The `live-traffic-postgres` container uses PostgreSQL 16 and the persistent `live-traffic-postgres-data` volume. The S1 database is separate from the service registry and other microservices' databases.

The Compose configuration supplies:

- `S1_DATABASE_URL`: connection string for the dedicated S1 PostgreSQL database.
- `TRAFFIC_POSTGRES_PASSWORD`: database password configured in the root `.env` file.
- `JWT_ISSUER`: expected Keycloak token issuer.
- `JWT_AUDIENCE`: expected token audience.
- `JWT_JWKS_URL`: Keycloak JSON Web Key Set endpoint.
- `JWT_ROLE_CLAIM`: JWT claim containing the user's roles.
- `JWT_MODERATOR_ROLE`: moderator role name.
- `APP_ENV` and `JWT_ALLOW_INSECURE_JWKS`: environment-specific security settings.

The service initializes its database schema at startup. The readiness endpoint checks database connectivity and JWT verification configuration.

## Demo with simulated cars

In a second terminal, from the repository root:

```powershell
.\.venv\Scripts\python.exe services/live-traffic/simulate_users.py --scenario jam
```

Eight simulated cars drive along one made-up straight street. In the `jam` scenario the south half crawls at 8 km/h and the north half moves at 48 km/h. After about five seconds, query the endpoints.

For a standalone development instance:

```powershell
Invoke-RestMethod 'http://127.0.0.1:8081/v1/traffic/congestion/route?start_latitude=42.9840&start_longitude=-81.2500&end_latitude=43.0000&end_longitude=-81.2560' | ConvertTo-Json -Depth 5
Invoke-RestMethod 'http://127.0.0.1:8081/v1/traffic/congestion/area?latitude=42.9880&longitude=-81.2515&radius_m=500' | ConvertTo-Json -Depth 5
```

For the authenticated Compose deployment, include a valid Keycloak bearer token in requests to protected endpoints. Do not paste tokens into shared logs or documentation.

Expected: segments 1 and 2 are `severe`, segments 3 and 4 are `low`, and the route has a delay of roughly six minutes. Stop the simulator, start it again with `--scenario clear`, and ask again: every segment becomes `low` and the delay drops to about ten seconds (T019).

Stop the simulator and wait just over a minute: the same answer comes back labelled `stale_fallback` with `is_live: false` (T018).

Use `simulate_users.py` or `simulate_feed.py`, not both at once. They use the same car names, and mixing them gives nonsense speeds.

## Polling a traffic source (T089)

Instead of users sending pings, the service can fetch them from a source by itself.

Set `TRAFFIC_SOURCE_URL` before starting the service and it polls that URL every 5 seconds. When PostgreSQL is configured, replicas coordinate through a shared PostgreSQL advisory lock: only the replica holding the lock polls the source. Other replicas remain on standby and can take over if the active replica stops or loses its database connection.

Without `TRAFFIC_SOURCE_URL`, polling is disabled. User-submitted location updates continue to work normally.

Terminal 1, the simulated source:

```powershell
.\.venv\Scripts\python.exe services/live-traffic/simulate_feed.py --scenario jam
```

Terminal 2, for a standalone local service:

```powershell
$env:TRAFFIC_SOURCE_URL = "http://127.0.0.1:8091/feed"
```

For Docker Compose, configure `TRAFFIC_SOURCE_URL` in the service environment using a hostname reachable from the container network. Do not use `127.0.0.1` to refer to another container.

The source must answer `GET` with JSON like this (`timestamp` is optional):

```json
{
  "locations": [
    {
      "user_id": "car-1",
      "latitude": 42.988,
      "longitude": -81.2515,
      "timestamp": "2026-11-11T20:15:00Z"
    }
  ]
}
```

How the data flows:

```text
POST /v1/traffic/locations --\
                              >-- local broker --> subscriber --> PostgreSQL
feed poller (one replica) ---/                              shared location store
                                                              |
                                                    all S1 replicas read
```

Each published event has `event_type`, `user_id`, `latitude`, `longitude`, `timestamp`, `source` (`user` or `feed`) and `published_at`. Invalid or too-old items from the source are skipped. The same ping delivered twice is stored once.

**Source outage:** stop `simulate_feed.py`. `GET /v1/traffic/status` shows `source_status: unavailable` within one poll for the active poller. Other replicas may show `standby`. The cached data keeps being served; once it is more than 60 seconds old, congestion answers are labelled `stale_fallback`. Start the feed again and the next successful poll returns the active poller's status to `ok`.

## Endpoints

Coordinates are WGS84 decimal degrees. Distances are metres. Speeds are km/h. Times are UTC in ISO 8601 format. Invalid input returns FastAPI's standard 422 response.

All endpoints except `/`, `/health` and `/ready` require a valid Keycloak RS256 bearer token.

### POST /v1/traffic/locations

One location update from an active user.

```json
{
  "user_id": "anon-123",
  "latitude": 42.988,
  "longitude": -81.2515,
  "timestamp": "2026-11-11T20:15:00Z"
}
```

`timestamp` is optional; the arrival time is used when it is missing. A timestamp more than 5 seconds in the future or more than 10 minutes old is rejected. Returns 201.

The submitted `user_id` is scoped to the authenticated JWT subject, preventing two different authenticated users from writing to the same logical device identity.

### GET /v1/traffic/congestion/area

Query parameters: `latitude`, `longitude`, `radius_m` (more than 0, at most 50000).

### GET /v1/traffic/congestion/route

Query parameters: `start_latitude`, `start_longitude`, `end_latitude`, `end_longitude`. The two points must be different and at most 50 km apart. The response has one entry per route piece in `segments`, plus `normal_travel_seconds`, `estimated_travel_seconds` and `estimated_delay_seconds` for the whole route.

### GET /v1/traffic/status

No parameters. Reports the traffic source and the cache.

| Field | Meaning |
|---|---|
| `source_status` | `not_configured`, `waiting`, `standby`, `ok` or `unavailable` |
| `last_poll_at`, `last_successful_poll_at` | when the source was last asked, and last answered |
| `consecutive_failures` | failed polls in a row; 0 when the source is healthy |
| `events_published` | location updates published on this instance's local broker since the service started |
| `cached_users` | users with at least one ping in the shared database cache |
| `newest_ping_at`, `newest_ping_age_seconds` | the newest ping in the shared cache |
| `cache_status` | `live` (newest ping at most 60 s old), `stale` or `empty` |

`events_published` is an instance-local metric, not a cluster-wide total. The source status also describes the local instance's polling state; only one replica should be actively polling when the shared advisory lock is functioning.

### Fields in both congestion responses

| Field | Meaning |
|---|---|
| `congestion_level` | `low`, `moderate`, `high`, `severe` or `unknown` |
| `average_speed_kmh` | average speed of the users counted, or null |
| `users_counted` | how many users the answer is based on (area response and each segment) |
| `data_status` | `live`, `stale_fallback` or `no_data` |
| `is_live` | true only when `data_status` is `live` |
| `newest_data_at`, `data_age_seconds` | time and age of the newest ping used |
| `timing_ms` | `retrieve_ms` and `calculate_ms` measured for this request |

Responses never contain user IDs or individual user locations.

## How congestion is calculated

1. For each user, take their two newest pings. Speed = distance between them / time between them.
2. Keep the users inside the area asked about and average their speeds.
3. Compare the average with the free-flow speed of 50 km/h:

| Average speed | Level |
|---|---|
| 37.5 km/h or more (75%+) | low |
| 25 to 37.4 km/h (50%+) | moderate |
| 12.5 to 24.9 km/h (25%+) | high |
| under 12.5 km/h | severe |
| no users | unknown |

For a route, each piece gets its own level. The delay of a piece is the time to cross it at the current average speed minus the time to cross it at 50 km/h. A piece with no users is `unknown`, adds no delay, and is counted in `segments_without_data`.

## Data freshness (T018)

- **live:** every ping used is at most 60 seconds old.
- **stale_fallback:** there was no live data for the place asked about, so pings up to 10 minutes old were used. `is_live` is false.
- **no_data:** nothing usable. The level is `unknown`, never a guess.

All numbers above are constants in `app/settings.py`.

## Tests

From the repository root (run separately from other services, because each service has a top-level package named `app`):

```powershell
.\.venv\Scripts\python.exe -m pytest -c services/live-traffic/pytest.ini services/live-traffic/tests -q
```

Results are recorded in [docs/Testing-T011-T020.md](../../docs/Testing-T011-T020.md) and [docs/Testing-T089-T104.md](../../docs/Testing-T089-T104.md).

## Acceptance run (T104)

With the service running, this checks every S1 requirement through the real API and writes the expected and actual results to `docs/evidence/S1-acceptance-results.md`:

```powershell
.\.venv\Scripts\python.exe services/live-traffic/acceptance_test.py
```

Use `--concurrency N` to set the T020 number once T002 has agreed it (default 50).

## Load test (S1 part of T095)

With the service running:

```powershell
.\.venv\Scripts\python.exe services/live-traffic/load_test.py --rps 20 --repeats 3
.\.venv\Scripts\python.exe services/live-traffic/load_test.py --rps 200 --repeats 3
```

It prints achieved requests per second, errors and p50/p95/p99 response times for one instance. It does not start or measure extra replicas.

## Container

The root Docker Compose configuration defines the S1 service, its dedicated PostgreSQL database, persistent database volume, registry dependency and Keycloak configuration.

Build the image:

```powershell
docker build `
    -f "services/live-traffic/Dockerfile" `
    -t smart-city-live-traffic:local `
    "services"
```

Start or recreate S1 after building:

```powershell
docker compose up -d --no-build --force-recreate live-traffic
```

Check its status and readiness:

```powershell
docker compose ps live-traffic
Invoke-RestMethod 'http://127.0.0.1:8081/ready'
```

The standalone `docker run` example is intentionally omitted: the production-style S1 container expects the shared PostgreSQL database and Keycloak configuration supplied by Compose.
