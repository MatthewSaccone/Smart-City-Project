"""Send requests to Live Traffic at a steady rate and measure it (S1 part of T095).

Example: the normal load (20 requests per second) and the peak load (200):

    python services/live-traffic/load_test.py --rps 20
    python services/live-traffic/load_test.py --rps 200

What it measures: achieved requests per second, errors, and response times
(p50, p95, p99, slowest). "p95" means 95% of requests were at least this fast.

What it does NOT measure: scaling. It talks to ONE running instance. The
scale-out timing in T095 needs the autoscaling work (T087) to exist first.

Keep --seconds at 40 or less. The test users are loaded once at the start,
and after 60 seconds their data would no longer count as live.
"""

import argparse
import os
import platform
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx


METRES_PER_DEGREE_LAT = 111194.93
WARMUP_REQUESTS = 20

# The test users always sit at this spot, far away from the simulated street.
# The same 200 user IDs are reused on every run, so the store does not keep growing.
latitude = 20.0
longitude = 0.0

# Filled in by main().
client = None


def north_of(start_latitude, metres):
    return start_latitude + metres / METRES_PER_DEGREE_LAT


def load_users(count):
    """Put count moving users (2 pings each) on a 1000 m stretch."""
    now = datetime.now(timezone.utc)

    for number in range(count):
        user_latitude = north_of(latitude, number % 1000)
        # 20 km/h is 55.6 m in 10 seconds.
        pings = [
            (north_of(user_latitude, -55.6), now - timedelta(seconds=11)),
            (user_latitude, now - timedelta(seconds=1)),
        ]
        for ping_latitude, timestamp in pings:
            response = client.post("/v1/traffic/locations", json={
                "user_id": "load-test-" + str(number),
                "latitude": ping_latitude,
                "longitude": longitude,
                "timestamp": timestamp.isoformat(),
            })
            response.raise_for_status()


def one_request(number):
    """Send one request. Even numbers ask for an area, odd numbers for a route."""
    started = time.perf_counter()

    try:
        if number % 2 == 0:
            response = client.get("/v1/traffic/congestion/area", params={
                "latitude": latitude, "longitude": longitude, "radius_m": 300})
        else:
            response = client.get("/v1/traffic/congestion/route", params={
                "start_latitude": latitude, "start_longitude": longitude,
                "end_latitude": north_of(latitude, 1000), "end_longitude": longitude})
        succeeded = response.status_code == 200
    except httpx.HTTPError:
        succeeded = False

    return succeeded, (time.perf_counter() - started) * 1000


def percentile(sorted_values, percent):
    """The value that percent% of the list is less than or equal to."""
    position = int(len(sorted_values) * percent / 100)
    position = min(position, len(sorted_values) - 1)
    return sorted_values[position]


def run_once(rps, seconds):
    """Send rps requests every second for the given number of seconds."""
    total = rps * seconds
    gap = 1 / rps
    futures = []

    with ThreadPoolExecutor(max_workers=200) as pool:
        started = time.perf_counter()

        for number in range(total):
            # Wait until it is this request's turn, so the rate stays steady.
            wait = started + number * gap - time.perf_counter()
            if wait > 0:
                time.sleep(wait)
            futures.append(pool.submit(one_request, number))

        outcomes = []
        for future in futures:
            outcomes.append(future.result())

        elapsed = time.perf_counter() - started

    errors = 0
    times = []
    for succeeded, elapsed_ms in outcomes:
        if not succeeded:
            errors = errors + 1
        times.append(elapsed_ms)
    times.sort()

    return {
        "sent": total,
        "errors": errors,
        "achieved_rps": total / elapsed,
        "p50": percentile(times, 50),
        "p95": percentile(times, 95),
        "p99": percentile(times, 99),
        "slowest": times[-1],
    }


def main():
    global client

    parser = argparse.ArgumentParser(description="Steady-rate load test for S1 Live Traffic.")
    parser.add_argument("--url", default="http://127.0.0.1:8081")
    parser.add_argument("--rps", type=int, default=20, help="requests per second")
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--users", type=int, default=200, help="active users to load first")
    parser.add_argument("--repeats", type=int, default=1)
    args = parser.parse_args()

    client = httpx.Client(base_url=args.url, timeout=10,
                          limits=httpx.Limits(max_connections=200))

    print("Machine:", platform.platform(), "-", os.cpu_count(), "CPU cores - Python",
          platform.python_version())
    print("Target:", args.url, "-", args.rps, "requests/s for", args.seconds, "s -",
          args.users, "active users -", WARMUP_REQUESTS, "warm-up requests (not counted)")
    print()
    print("| Run | Target rps | Achieved rps | Sent | Errors | p50 ms | p95 ms | p99 ms | Slowest ms |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|")

    for run_number in range(1, args.repeats + 1):
        # Send fresh pings for the test users so their data counts as live.
        load_users(args.users)

        for number in range(WARMUP_REQUESTS):
            one_request(number)

        result = run_once(args.rps, args.seconds)

        print("| {} | {} | {:.1f} | {} | {} | {:.0f} | {:.0f} | {:.0f} | {:.0f} |".format(
            run_number, args.rps, result["achieved_rps"], result["sent"], result["errors"],
            result["p50"], result["p95"], result["p99"], result["slowest"]))


if __name__ == "__main__":
    main()
