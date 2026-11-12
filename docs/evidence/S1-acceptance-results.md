# S1 Live Traffic acceptance results (T104)

Produced by `services/live-traffic/acceptance_test.py` against a running service.

| | |
|---|---|
| Run started (UTC) | 2026-11-11 20:46:40 |
| Service | http://127.0.0.1:8081 |
| Machine | Linux-6.18.44-fc-v378-x86_64-with-glibc2.39, 2 CPU cores |
| Python | 3.13.18 |
| Test longitude for this run | 125.9516 |
| Result | **10 of 10 passed** |

| Task | Requirement | Scenario | Expected | Actual | Time (UTC) | Result |
|---|---|---|---|---|---|---|
| T011 | S1 FR1 | Two users at 5 and 7 km/h in one area; two users at 46 and 50 km/h in another | slow area: severe, 6.0 km/h; fast area: low, 48.0 km/h | slow area: severe, 6.0 km/h; fast area: low, 48.0 km/h | 20:46:40 | PASS |
| T012 | S1 QR1 | 20 requests (10 area, 10 route) with 200 active users on the route | every calculation takes under 1000 ms | slowest calculation: 0.69 ms | 20:46:40 | PASS |
| T013 | S1 FR2 | 1000 m between two points; a 10 km/h user in the first half, a 50 km/h user in the second | 2 segments: severe, low | 2 segments: severe, low | 20:46:40 | PASS |
| T014 | S1 QR2 | 20 requests (10 area, 10 route) with 200 active users on the route | location data is retrieved in under 500 ms every time | slowest retrieval: 0.32 ms | 20:46:40 | PASS |
| T015 | S1 FR2.1 | 1000 m route where traffic moves at 25 km/h (half the 50 km/h free-flow speed) | normal 72 s, estimated 144 s, delay 72 s (within 2 s) | normal 72.0 s, estimated 144.0 s, delay 72.0 s | 20:46:40 | PASS |
| T016 | S1 QR3 | 20 requests (10 area, 10 route) with 200 active users on the route | the whole request, including sending the answer back, takes under 1000 ms | slowest whole request: 2 ms | 20:46:40 | PASS |
| T017 | S1 FR3 | One user 100 m away and one user 2000 m away; ask with radius 300 m, then 3000 m | radius 300 m counts 1 user; radius 3000 m counts 2 users | radius 300 m counts 1 user(s); radius 3000 m counts 2 user(s) | 20:46:40 | PASS |
| T018 | S1 QR4 | Area A: newest data 40 s old. Area B: newest data 80 s old. Area C: no data | A: live, is_live true; B: stale_fallback, is_live false; C: no_data, unknown | A: live, is_live true; B: stale_fallback, is_live false; C: no_data, unknown | 20:46:40 | PASS |
| T019 | S1 FR4 | Two users crawl at 6 km/h, then the same users send newer pings at 50 km/h | before: severe with a delay over 300 s; after: low with a delay under 5 s | before: severe, delay 528.0 s; after: low, delay 0.0 s | 20:46:40 | PASS |
| T020 | S1 QR5 | 50 requests sent at the same moment (placeholder number until T002 is agreed) | all 50 succeed and each takes under 1000 ms | 50 of 50 succeeded; slowest took 54 ms | 20:46:41 | PASS |

All location data in this run was test data sent by the script. It is not real traffic.
