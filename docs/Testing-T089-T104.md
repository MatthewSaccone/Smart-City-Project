# T089 and T104 implementation and verification, with S1 parts of T095, T096, T099

Owner: Matthew C. Verification date: November 11, 2026 (America/Toronto).

These tasks were Unassigned in the task sheet. They were picked up because they build
directly on the S1 Live Traffic code from T011–T020.

> The numbers below come from one development run in a Linux environment. Re-run the
> commands on the demo machine and replace the numbers before using them as final evidence.

## Which of T084–T110 this covers

| Task | Covered | Notes |
|---|---|---|
| T089 Traffic ingestion, broker and timestamped cache | Yes | Feed poller, in-process broker, cache with timestamps, labelled stale data on outage |
| T104 Execute all S1 acceptance tests | Yes | Script run against a live service; expected and actual recorded for T011–T020 |
| T095 Evaluate S1 normal and peak workload scaling | S1 part only | 20 and 200 requests/s measured on one instance. Scale-out timing needs T087 |
| T096 Verify freshness, source outages and pub/sub recovery | S1 part only | 60 s boundary, stale labels, outage and recovery for S1. Parking, weather and transit belong to their owners |
| T099 Capture annotated operational evidence | S1 part only | `docs/evidence/S1-acceptance-results.md` lists scenario, time, requirement, expected and observed |

Not covered, because they depend on other people's components: T084, T085, T093 (registry
failover and replication), T087, T090 (cloud deployment and autoscaling), T088, T094
(circuit breakers and GUI partial views), T091, T092 (integration of other services), T097
(incident and gasoline persistence), T105–T110 (other services' acceptance tests), T086,
T098, T100–T103 (team architecture views, final report, slides and delivery).

## T089: what was built

```text
POST /v1/traffic/locations --\
                              >-- publish "traffic.locations" --> subscriber --> store (cache)
feed poller (every 5 s) -----/
```

| Part | Where | Behaviour |
|---|---|---|
| Poll | `FeedPoller` in `app/ingestion.py` | Background thread. Every 5 s it fetches `TRAFFIC_SOURCE_URL` with a 3 s timeout. Off when the setting is missing |
| Publish | `publish_location` in `app/ingestion.py`, `app/broker.py` | Each valid location becomes one event on the `traffic.locations` topic. POST requests publish the same event |
| Consume and cache | `save_location` in `app/ingestion.py`, `app/store.py` | The subscriber stores each event with its timestamp. Duplicates are stored once. Pings are dropped after 10 minutes |
| Outage | `poll_once`, `GET /v1/traffic/status` | A failed poll sets `source_status: unavailable` and counts failures. Cached data older than 60 s is served as `stale_fallback`, never as live |
| Recovery | `run_forever` | The poller keeps trying every 5 s. The first good poll resets the status to `ok` |

Assumption to confirm: the T010 design document is not in the repository. The polling
interval (5 s), the event fields and the choice of an in-process broker are simple defaults
and should be checked against T010. The interval and topic name are in `app/settings.py`.

Limit: the broker is inside one service instance. It does not carry events between
instances or to other services. Replicas that poll the same source hold the same polled data.

## Changed files

All under `services/live-traffic/` except this record and the evidence file:

- `app/broker.py`, `app/ingestion.py`: new.
- `app/settings.py`: three ingestion settings added.
- `app/store.py`: ignores a ping that is delivered twice.
- `app/models.py`: `IngestionStatus` added.
- `app/routes.py`: POST now publishes through the broker; `GET /v1/traffic/status` added.
- `app/main.py`: starts and stops the poller when a source is configured.
- `simulate_feed.py`, `acceptance_test.py`, `load_test.py`: new tools.
- `tests/test_live_traffic.py`: 11 tests added.
- `README.md`: updated.
- `docs/evidence/S1-acceptance-results.md`: produced by the acceptance script.

No other service, the registry, the shared client, HAProxy, Compose or the GUI was changed.

## Automated tests

Environment: Linux, 2 CPU cores, Python 3.13.18, FastAPI 0.162.4, Pydantic 2.14.3,
HTTPX 0.28.1, pytest 8.4.2.

`python -m pytest -c services/live-traffic/pytest.ini services/live-traffic/tests -q`:
**52 passed** (41 from T011–T020, 11 new).

| Task | New tests |
|---|---|
| T089 | broker delivers to every subscriber of a topic; one broken subscriber does not stop the others; a POSTed ping reaches the cache through the broker; polled locations are published, cached and analysed; bad and too-old feed items are skipped; a duplicate ping is cached once; the background thread polls until stopped; status with no source configured; poller starts and stops with the service only when configured |
| T089, T096 | source outage is reported, failures are counted, and old data is labelled `stale_fallback` |
| T096 | after an outage the next good polls return the status to `ok` and answers to `live` |

The 60-second boundary tests for T096 are the existing T018 tests (60 s old is live, 61 s
old is `stale_fallback`, no data is `unknown`).

## T089 and T096: real outage and recovery run

`simulate_feed.py` on port 8091, the service on port 8081 with
`TRAFFIC_SOURCE_URL=http://127.0.0.1:8091/feed`. Route asked: the simulated street, south to north.

| Moment | `/v1/traffic/status` | Route answer |
|---|---|---|
| Feed running for 13 s (`jam`) | `ok`, 0 failures, 8 users, cache `live`, newest ping 2.7 s old | `live`; segments `severe, severe, low, low`; delay 351.4 s |
| 7 s after the feed was stopped | `unavailable`, 1 failure, cache `live`, newest ping 9.7 s old | still `live` (data is under 60 s old) |
| 69 s after the feed was stopped | `unavailable`, 14 failures, cache `stale`, newest ping 71.7 s old | `stale_fallback`, `is_live: false`, same levels and delay |
| 13 s after the feed was restarted (`clear`) | `ok`, 0 failures, cache `live`, newest ping 4.6 s old | `live`; all segments `low`; delay 5.6 s |

The outage was detected at the next poll, old data was never labelled live after 60 s, and
the service recovered by itself without a restart.

## T104: S1 acceptance run

```text
python services/live-traffic/acceptance_test.py
```

Run against the live service while it was also polling the feed. **10 of 10 passed.** The
full table of scenario, expected value, actual value and time for every requirement is in
[evidence/S1-acceptance-results.md](evidence/S1-acceptance-results.md).

| Task | Requirement | Result |
|---|---|---|
| T011 | S1 FR1 | PASS |
| T012 | S1 QR1 | PASS |
| T013 | S1 FR2 | PASS |
| T014 | S1 QR2 | PASS |
| T015 | S1 FR2.1 | PASS |
| T016 | S1 QR3 | PASS |
| T017 | S1 FR3 | PASS |
| T018 | S1 QR4 | PASS |
| T019 | S1 FR4 | PASS |
| T020 | S1 QR5 | PASS with the placeholder of 50 at-once requests. Re-run with `--concurrency N` when T002 agrees the number |

## T095, S1 part: normal and peak load on one instance

```text
python services/live-traffic/load_test.py --rps 20 --seconds 20 --repeats 3
python services/live-traffic/load_test.py --rps 200 --seconds 20 --repeats 3
```

Hardware: 2 CPU cores, with the load generator and the service **on the same machine**.
One service instance, freshly started, no registry. 20 warm-up requests before each run,
not counted. Half the requests ask for an area and half for a route. All active users sit
on the route being asked about, which is the most expensive case.

200 active users:

| Target | Run | Achieved | Sent | Errors | p50 | p95 | p99 | Slowest |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 20 requests/s | 1 | 20.0 /s | 400 | 0 | 3 ms | 4 ms | 5 ms | 13 ms |
| 20 requests/s | 2 | 20.0 /s | 400 | 0 | 3 ms | 4 ms | 5 ms | 10 ms |
| 20 requests/s | 3 | 20.0 /s | 400 | 0 | 3 ms | 4 ms | 5 ms | 8 ms |
| 200 requests/s | 1 | 200.0 /s | 4000 | 0 | 3 ms | 4 ms | 6 ms | 20 ms |
| 200 requests/s | 2 | 200.0 /s | 4000 | 0 | 3 ms | 6 ms | 12 ms | 21 ms |
| 200 requests/s | 3 | 199.9 /s | 4000 | 0 | 4 ms | 9 ms | 19 ms | 42 ms |

More active users (one 15-second run each):

| Active users | Target | Achieved | Errors | p50 | p95 | Kept up? |
|---:|---|---:|---:|---:|---:|---|
| 1,000 | 100 requests/s | 100.0 /s | 0 | 8 ms | 46 ms | Yes |
| 1,000 | 200 requests/s | 83.6 /s | 1 | 1,831 ms | 5,909 ms | **No** |
| 2,000 | 100 requests/s | 57.3 /s | 0 | 3,390 ms | 5,143 ms | **No** |
| 2,000 | 200 requests/s | 38.8 /s | 7 | 4,832 ms | 6,775 ms | **No** |

Reading: on this machine one instance holds the 200 requests/s peak with 200 active users,
but falls behind once about 1,000 users are active. Each request looks at every cached
user, so the cost grows with the number of users. Peak load with many users therefore needs
more than one instance, which is what T087 provides. The exact limit will differ on other
hardware and when the load generator runs on a separate machine.

Not done for T095: scale-out start within 30 s, new replicas serving within 60 s, and
post-scale p95 within 2 s. Those need autoscaling (T087) and the cloud deployment. The
"about 50 users" check is the T020 row above (50 at-once requests, slowest 54 ms).

## Not run

- `docker build` and `docker compose up` (no Docker daemon in the development environment).
  The Dockerfile and Compose file are unchanged. Polling stays off under Compose because
  `TRAFFIC_SOURCE_URL` is not set there.
- Anything on the cloud deployment or with more than one S1 instance.
- Polling a real traffic provider. Only the simulated feed was used.

## Still required

- Confirm the polling interval, event contract and broker choice against T010.
- The agreed concurrency number from T002.
- T087, then the scale-out measurements for T095.
- The parking, weather and transit parts of T096 from their owners.
- Tell the group these Unassigned rows have been taken, so nobody repeats the work.
