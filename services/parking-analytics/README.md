# Parking Analytics — T039 and T041

Implements geographic facility search and occupancy estimates calculated from time/location
observations. The catalog and bundled observations are **simulated**, not a live parking feed.
No prices, rates, facility-detail endpoint (T043), GUI, ingestion pipeline or shared contracts
are implemented or changed here. Parking-owned schemas are exposed in `/openapi.json`;
the central API document currently contains templates, not an established parking schema.

## Run locally (PowerShell, repository root)

Use Python 3.11+ (the existing Dockerfile uses 3.12). If needed, create an isolated environment:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r services/parking-analytics/requirements-dev.txt
$env:PYTHONPATH = (Resolve-Path .\services).Path
$env:REGISTRY_URL = ""
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir services/parking-analytics --host 127.0.0.1 --port 8084
```

The services parent must be on `PYTHONPATH` for the existing `shared.registry_client` import.
Open <http://127.0.0.1:8084/docs>. The existing `/` and `/health` endpoints are retained.
Registration is optional locally. When enabling it, set `REGISTRY_URL`, `REGISTRY_API_KEY`
and `REGISTRY_ADVERTISED_ENDPOINT` to addresses reachable by the intended consumer.
The existing registration, five-second heartbeat and shutdown behavior are unchanged.

## T039: geographic search

```powershell
Invoke-RestMethod 'http://127.0.0.1:8084/v1/parking/facilities?min_lat=43.65&min_lon=-79.39&max_lat=43.66&max_lon=-79.37' | ConvertTo-Json -Depth 8
```

`GET /v1/parking/facilities` requires `min_lat`, `min_lon`, `max_lat`, `max_lon` in WGS84
degrees. It includes facility **centers** on the rectangle edges, returns 200 with an empty
list when nothing matches, and returns FastAPI's existing 422 `detail` errors for missing,
nonfinite, out-of-range or reversed bounds. Antimeridian areas must be split into two queries.
It does not perform polygon overlap, radius search, geocoding or space reservation.

The example returns `demo-central` and `demo-east`. Each result includes calculated
occupancy; a full facility or unknown estimate is explicitly visible. Facilities are listed
even when full or unknown, rather than claiming that every catalog entry has free spaces.
`estimated_available_spaces` is an inference, not a guarantee of an available parking space.

Search replays the bundled observations at **2026-11-09T17:00:00Z** and returns
`source: simulated_fixture`, `is_live: false`, and that exact `as_of`. The timestamps are
never silently advanced to make old fixture data look live. Expected estimates: Central
2/4 (moderate), East 2/2 (full), North unknown when included in a wider search.

## T041: calculate occupancy from observations

`POST /v1/parking/occupancy/estimate` accepts up to 10,000 observations:

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

To submit the clearly simulated fixture in another PowerShell window:

```powershell
$demo = Get-Content services/parking-analytics/app/fixtures/parking.json -Raw | ConvertFrom-Json
$body = @{ as_of = $demo.as_of; observations = $demo.observations } | ConvertTo-Json -Depth 6
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8084/v1/parking/occupancy/estimate' -ContentType 'application/json' -Body $body | ConvertTo-Json -Depth 8
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
batch must contain its own dwell history. Responses identify `submitted_observations`,
the simulated facility source and `is_live: false`; this API does not certify input provenance.
Connecting an approved source/storage adapter belongs to the team's ingestion/contracts
work. Do not expose real identifiable location histories through this unauthenticated demo.

## Tests and container

From the repository root, run this suite separately from the registry's suite because
both services use a top-level Python package named `app`:

```powershell
.\.venv\Scripts\python.exe -m pytest -c services/parking-analytics/pytest.ini services/parking-analytics/tests -q
docker build -t scms-parking-t039-t041 -f services/parking-analytics/Dockerfile services
docker run --rm -p 8084:8080 scms-parking-t039-t041
```

The fixture is under `app/fixtures/`, so the existing Dockerfile includes it without any
build changes. Root Compose remains unchanged and still requires `REGISTRY_API_KEY`
and `POSTGRES_PASSWORD` in local environment configuration. The latter is currently
missing from the root `.env.example`; provide it locally before using Compose.
