"""Acceptance checks for S1 Live Traffic, run against a REAL running service (T104).

For every S1 requirement (T011 to T020) this script sets up a small scenario
through the public API, writes down what was expected and what actually
happened, and saves the result as a Markdown evidence file (T099).

Start the service first, then run (from the repository root):

    python services/live-traffic/acceptance_test.py

Options:
    --url           where the service is (default http://127.0.0.1:8081)
    --concurrency   how many requests to send at once for T020 (default 50)
    --output        where to save the evidence file

The test users are placed far away from the simulated street, at a random
longitude chosen for each run, so an earlier run cannot affect a later one.
"""

import argparse
import os
import platform
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx


METRES_PER_DEGREE_LAT = 111194.93

# Filled in by main().
client = None
run_id = ""
results = []          # one dictionary per requirement checked
all_timings = []      # (retrieve_ms, calculate_ms, whole_request_ms) for every GET


def north_of(latitude, metres):
    """The latitude that is this many metres north of the given latitude."""
    return latitude + metres / METRES_PER_DEGREE_LAT


def record(task, requirement, scenario, expected, actual, passed):
    """Write down the outcome of one check."""
    results.append({
        "task": task,
        "requirement": requirement,
        "scenario": scenario,
        "expected": expected,
        "actual": actual,
        "passed": passed,
        "checked_at": datetime.now(timezone.utc).strftime("%H:%M:%S"),
    })
    if passed:
        print("PASS", task, requirement)
    else:
        print("FAIL", task, requirement, "- expected:", expected, "- actual:", actual)


def send_moving_user(name, speed_kmh, latitude, longitude, seconds_ago=1):
    """Send two pings, 10 seconds apart, for a user driving north at speed_kmh."""
    now = datetime.now(timezone.utc)
    metres_in_10_seconds = (speed_kmh / 3.6) * 10

    pings = [
        (north_of(latitude, -metres_in_10_seconds), now - timedelta(seconds=seconds_ago + 10)),
        (latitude, now - timedelta(seconds=seconds_ago)),
    ]

    for ping_latitude, timestamp in pings:
        response = client.post("/v1/traffic/locations", json={
            "user_id": "acceptance-" + run_id + "-" + name,
            "latitude": ping_latitude,
            "longitude": longitude,
            "timestamp": timestamp.isoformat(),
        })
        response.raise_for_status()


def timed_get(path, params):
    """Send a GET request and remember how long everything took."""
    started = time.perf_counter()
    response = client.get(path, params=params)
    whole_request_ms = (time.perf_counter() - started) * 1000
    response.raise_for_status()

    body = response.json()
    timing = body["timing_ms"]
    all_timings.append((timing["retrieve_ms"], timing["calculate_ms"], whole_request_ms))
    return body


def get_area(latitude, longitude, radius_m):
    return timed_get("/v1/traffic/congestion/area", {
        "latitude": latitude, "longitude": longitude, "radius_m": radius_m})


def get_route(latitude, longitude):
    """A 1000 m route going north from the given point."""
    return timed_get("/v1/traffic/congestion/route", {
        "start_latitude": latitude, "start_longitude": longitude,
        "end_latitude": north_of(latitude, 1000), "end_longitude": longitude})


# ---------- the checks, one per functional requirement ----------

def check_t011(lat, lon):
    send_moving_user("t011-slow-1", 5, lat, lon)
    send_moving_user("t011-slow-2", 7, lat, lon)
    send_moving_user("t011-fast-1", 46, north_of(lat, 5000), lon)
    send_moving_user("t011-fast-2", 50, north_of(lat, 5000), lon)

    slow = get_area(lat, lon, 300)
    fast = get_area(north_of(lat, 5000), lon, 300)

    record(
        "T011", "S1 FR1",
        "Two users at 5 and 7 km/h in one area; two users at 46 and 50 km/h in another",
        "slow area: severe, 6.0 km/h; fast area: low, 48.0 km/h",
        "slow area: {}, {} km/h; fast area: {}, {} km/h".format(
            slow["congestion_level"], slow["average_speed_kmh"],
            fast["congestion_level"], fast["average_speed_kmh"]),
        slow["congestion_level"] == "severe" and fast["congestion_level"] == "low"
        and abs(slow["average_speed_kmh"] - 6) <= 0.2
        and abs(fast["average_speed_kmh"] - 48) <= 0.2,
    )


def check_t013(lat, lon):
    send_moving_user("t013-slow", 10, north_of(lat, 250), lon)
    send_moving_user("t013-fast", 50, north_of(lat, 750), lon)

    route = get_route(lat, lon)
    levels = []
    for segment in route["segments"]:
        levels.append(segment["congestion_level"])

    record(
        "T013", "S1 FR2",
        "1000 m between two points; a 10 km/h user in the first half, a 50 km/h user in the second",
        "2 segments: severe, low",
        "{} segments: {}".format(len(levels), ", ".join(levels)),
        levels == ["severe", "low"],
    )


def check_t015(lat, lon):
    send_moving_user("t015-a", 25, north_of(lat, 250), lon)
    send_moving_user("t015-b", 25, north_of(lat, 750), lon)

    route = get_route(lat, lon)

    record(
        "T015", "S1 FR2.1",
        "1000 m route where traffic moves at 25 km/h (half the 50 km/h free-flow speed)",
        "normal 72 s, estimated 144 s, delay 72 s (within 2 s)",
        "normal {} s, estimated {} s, delay {} s".format(
            route["normal_travel_seconds"], route["estimated_travel_seconds"],
            route["estimated_delay_seconds"]),
        abs(route["estimated_delay_seconds"] - 72) <= 2,
    )


def check_t017(lat, lon):
    send_moving_user("t017-near", 10, north_of(lat, 100), lon)
    send_moving_user("t017-far", 50, north_of(lat, 2000), lon)

    small = get_area(lat, lon, 300)
    large = get_area(lat, lon, 3000)

    record(
        "T017", "S1 FR3",
        "One user 100 m away and one user 2000 m away; ask with radius 300 m, then 3000 m",
        "radius 300 m counts 1 user; radius 3000 m counts 2 users",
        "radius 300 m counts {} user(s); radius 3000 m counts {} user(s)".format(
            small["users_counted"], large["users_counted"]),
        small["users_counted"] == 1 and large["users_counted"] == 2,
    )


def check_t018(lat, lon):
    send_moving_user("t018-fresh", 20, lat, lon, seconds_ago=40)
    send_moving_user("t018-old", 20, north_of(lat, 5000), lon, seconds_ago=80)

    fresh = get_area(lat, lon, 300)
    old = get_area(north_of(lat, 5000), lon, 300)
    empty = get_area(north_of(lat, 10000), lon, 300)

    record(
        "T018", "S1 QR4",
        "Area A: newest data 40 s old. Area B: newest data 80 s old. Area C: no data",
        "A: live, is_live true; B: stale_fallback, is_live false; C: no_data, unknown",
        "A: {}, is_live {}; B: {}, is_live {}; C: {}, {}".format(
            fresh["data_status"], str(fresh["is_live"]).lower(),
            old["data_status"], str(old["is_live"]).lower(),
            empty["data_status"], empty["congestion_level"]),
        fresh["data_status"] == "live" and fresh["is_live"] is True
        and old["data_status"] == "stale_fallback" and old["is_live"] is False
        and empty["data_status"] == "no_data" and empty["congestion_level"] == "unknown",
    )


def check_t019(lat, lon):
    send_moving_user("t019-a", 6, north_of(lat, 250), lon, seconds_ago=30)
    send_moving_user("t019-b", 6, north_of(lat, 750), lon, seconds_ago=30)
    before = get_route(lat, lon)

    # The same two users send newer pings showing they now move freely.
    send_moving_user("t019-a", 50, north_of(lat, 250), lon)
    send_moving_user("t019-b", 50, north_of(lat, 750), lon)
    after = get_route(lat, lon)

    record(
        "T019", "S1 FR4",
        "Two users crawl at 6 km/h, then the same users send newer pings at 50 km/h",
        "before: severe with a delay over 300 s; after: low with a delay under 5 s",
        "before: {}, delay {} s; after: {}, delay {} s".format(
            before["congestion_level"], before["estimated_delay_seconds"],
            after["congestion_level"], after["estimated_delay_seconds"]),
        before["congestion_level"] == "severe" and before["estimated_delay_seconds"] > 300
        and after["congestion_level"] == "low" and after["estimated_delay_seconds"] < 5
        and after["newest_data_at"] > before["newest_data_at"],
    )


# ---------- the checks for the quality requirements ----------

def check_speed_requirements(lat, lon, users):
    """T012, T014 and T016, measured with many active users in the area."""
    for number in range(users):
        send_moving_user("load-" + str(number), 20, north_of(lat, number % 1000), lon)

    all_timings.clear()
    for _ in range(10):
        get_area(lat, lon, 300)
        get_route(lat, lon)

    slowest_retrieve = max(timing[0] for timing in all_timings)
    slowest_calculate = max(timing[1] for timing in all_timings)
    slowest_whole = max(timing[2] for timing in all_timings)
    scenario = "20 requests (10 area, 10 route) with {} active users on the route".format(users)

    record("T012", "S1 QR1", scenario,
           "every calculation takes under 1000 ms",
           "slowest calculation: {:.2f} ms".format(slowest_calculate),
           slowest_calculate < 1000)

    record("T014", "S1 QR2", scenario,
           "location data is retrieved in under 500 ms every time",
           "slowest retrieval: {:.2f} ms".format(slowest_retrieve),
           slowest_retrieve < 500)

    record("T016", "S1 QR3", scenario,
           "the whole request, including sending the answer back, takes under 1000 ms",
           "slowest whole request: {:.0f} ms".format(slowest_whole),
           slowest_whole < 1000)


def check_t020(lat, lon, concurrency):
    """T020: send many requests at the same moment."""

    def one_request(number):
        started = time.perf_counter()
        if number % 2 == 0:
            response = client.get("/v1/traffic/congestion/area", params={
                "latitude": lat, "longitude": lon, "radius_m": 300})
        else:
            response = client.get("/v1/traffic/congestion/route", params={
                "start_latitude": lat, "start_longitude": lon,
                "end_latitude": north_of(lat, 1000), "end_longitude": lon})
        return response.status_code, (time.perf_counter() - started) * 1000

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        outcomes = list(pool.map(one_request, range(concurrency)))

    successes = 0
    slowest = 0.0
    for status_code, elapsed_ms in outcomes:
        if status_code == 200:
            successes = successes + 1
        slowest = max(slowest, elapsed_ms)

    record(
        "T020", "S1 QR5",
        "{} requests sent at the same moment (placeholder number until T002 is agreed)".format(
            concurrency),
        "all {} succeed and each takes under 1000 ms".format(concurrency),
        "{} of {} succeeded; slowest took {:.0f} ms".format(successes, concurrency, slowest),
        successes == concurrency and slowest < 1000,
    )


# ---------- the report ----------

def write_report(path, url, started_at, longitude):
    passed = 0
    for result in results:
        if result["passed"]:
            passed = passed + 1

    lines = [
        "# S1 Live Traffic acceptance results (T104)",
        "",
        "Produced by `services/live-traffic/acceptance_test.py` against a running service.",
        "",
        "| | |",
        "|---|---|",
        "| Run started (UTC) | " + started_at.strftime("%Y-%m-%d %H:%M:%S") + " |",
        "| Service | " + url + " |",
        "| Machine | " + platform.platform() + ", " + str(os.cpu_count()) + " CPU cores |",
        "| Python | " + platform.python_version() + " |",
        "| Test longitude for this run | " + str(longitude) + " |",
        "| Result | **" + str(passed) + " of " + str(len(results)) + " passed** |",
        "",
        "| Task | Requirement | Scenario | Expected | Actual | Time (UTC) | Result |",
        "|---|---|---|---|---|---|---|",
    ]

    for result in sorted(results, key=lambda item: item["task"]):
        if result["passed"]:
            outcome = "PASS"
        else:
            outcome = "**FAIL**"
        lines.append("| {} | {} | {} | {} | {} | {} | {} |".format(
            result["task"], result["requirement"], result["scenario"],
            result["expected"], result["actual"], result["checked_at"], outcome))

    lines.append("")
    lines.append("All location data in this run was test data sent by the script. It is not real traffic.")
    lines.append("")

    folder = os.path.dirname(path)
    if folder != "":
        os.makedirs(folder, exist_ok=True)

    with open(path, "w", encoding="utf-8") as file:
        file.write("\n".join(lines))

    print()
    print(passed, "of", len(results), "passed. Evidence saved to", path)
    return passed == len(results)


def main():
    global client, run_id

    parser = argparse.ArgumentParser(description="Run the S1 acceptance checks (T104).")
    parser.add_argument("--url", default="http://127.0.0.1:8081")
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--users", type=int, default=200,
                        help="active users to load before the speed checks")
    parser.add_argument("--output", default="docs/evidence/S1-acceptance-results.md")
    args = parser.parse_args()

    started_at = datetime.now(timezone.utc)
    run_id = started_at.strftime("%H%M%S")
    client = httpx.Client(base_url=args.url, timeout=10,
                          limits=httpx.Limits(max_connections=args.concurrency + 10))

    try:
        client.get("/health").raise_for_status()
    except httpx.HTTPError:
        print("Could not reach", args.url, "- is the Live Traffic service running?")
        sys.exit(1)

    # Every check gets its own latitude, about 55 km from the next one.
    longitude = round(random.uniform(-170, 170), 4)

    check_t011(10.0, longitude)
    check_t013(10.5, longitude)
    check_t015(11.0, longitude)
    check_t017(11.5, longitude)
    check_t018(12.0, longitude)
    check_t019(12.5, longitude)
    check_speed_requirements(13.0, longitude, args.users)
    check_t020(13.0, longitude, args.concurrency)

    everything_passed = write_report(args.output, args.url, started_at, longitude)
    if not everything_passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
