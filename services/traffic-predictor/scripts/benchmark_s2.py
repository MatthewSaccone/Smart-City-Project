"""Small standard-library HTTP performance check for S2 Traffic Predictor.

Run this in a separate terminal while the real FastAPI + PostgreSQL service runs.
This is an exploratory/local performance test, not a substitute for cloud testing.
"""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import statistics
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SCENARIOS = ("history", "point", "areas", "high-periods")


def utc_iso(value):
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def build_request(base_url, scenario, latitude, longitude, radius_m):
    """Construct a representative real-database request from S2's data models."""
    base_url = base_url.rstrip("/")
    future = datetime.now(timezone.utc) + timedelta(hours=3)
    forecast = utc_iso(future)
    location = {"latitude": latitude, "longitude": longitude}
    if scenario == "history":
        path = "/v1/traffic/history/patterns"
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "radius_m": radius_m,
            "timezone": "America/Toronto",
            "day_of_week": "wednesday",
            "local_hour": 11,
            "start_at": "2026-02-03T00:00:00Z",
            "end_at": "2026-02-06T00:00:00Z",
        }
        method, body = "GET", None
    elif scenario == "point":
        path = "/v1/traffic/predictions"
        params = None
        method = "POST"
        body = json.dumps({"location": location, "forecast_time": forecast}).encode()
    elif scenario == "areas":
        path = "/v1/traffic/predictions/areas"
        params = {**location, "radius_m": radius_m, "forecast_time": forecast}
        method, body = "GET", None
    elif scenario == "high-periods":
        path = "/v1/traffic/predictions/high-congestion-periods"
        params = {
            **location,
            "radius_m": radius_m,
            "start_at": forecast,
            "end_at": utc_iso(future + timedelta(hours=2)),
        }
        method, body = "GET", None
    else:
        raise ValueError(f"Unknown scenario: {scenario}")
    url = base_url + path
    if params is not None:
        url += "?" + urlencode(params)
    return method, url, body


def request_once(method, url, body, timeout_s, token):
    start = time.perf_counter()
    status, error = None, None
    try:
        headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = Request(url, data=body, headers=headers, method=method)
        with urlopen(req, timeout=timeout_s) as response:
            status = response.status
            response.read()  # Measure full HTTP response, not headers only.
    except HTTPError as exc:
        status = exc.code
        try:
            error = exc.read(200).decode("utf-8", errors="replace")
        finally:
            exc.close()
    except (URLError, TimeoutError, OSError) as exc:
        error = str(exc)
    elapsed_ms = 1000 * (time.perf_counter() - start)
    return {"status": status, "elapsed_ms": elapsed_ms, "error": error}


def percentile(values, quantile):
    """Nearest-rank percentile (conservative for small sample sizes)."""
    if not values:
        return None
    ordered = sorted(values)
    import math
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def benchmark(method, url, body, concurrency, count, timeout_s, token):
    # Barrier forces the first wave of workers to begin their requests together.
    barrier = threading.Barrier(concurrency, timeout=15)

    def task(index):
        if index < concurrency:
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                return {"status": None, "elapsed_ms": 0.0, "error": "Could not start synchronized first wave"}
        return request_once(method, url, body, timeout_s, token)

    results = []
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        jobs = [pool.submit(task, index) for index in range(count)]
        for job in as_completed(jobs):
            results.append(job.result())
    wall_s = time.perf_counter() - start
    return results, wall_s


def main():
    parser = argparse.ArgumentParser(description="S2 local HTTP latency/concurrency check")
    parser.add_argument("--scenario", required=True, choices=SCENARIOS)
    parser.add_argument("--base-url", default="http://127.0.0.1:8082")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--requests", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--latitude", type=float, default=43.736355)
    parser.add_argument("--longitude", type=float, default=-79.43891)
    parser.add_argument("--radius-m", type=float, default=1000.0)
    parser.add_argument("--threshold-ms", type=float, default=3000.0)
    parser.add_argument("--output", type=Path, help="Optional JSON report path")
    parser.add_argument("--token-env", default="S2_ACCESS_TOKEN", help="Environment variable holding a valid access token")
    args = parser.parse_args()
    token = os.getenv(args.token_env)
    if not token:
        parser.error(f"Set {args.token_env} to a trusted JWT access token (the token is never printed)")

    if args.concurrency < 1 or args.requests < args.concurrency:
        parser.error("--requests must be >= --concurrency, and both must be positive")
    if args.warmup < 0 or args.timeout <= 0 or args.threshold_ms <= 0:
        parser.error("--warmup must be >= 0, timeout and threshold must be > 0")

    method, url, body = build_request(
        args.base_url, args.scenario, args.latitude, args.longitude, args.radius_m
    )
    print(f"Scenario: {args.scenario}, method: {method}")
    print(f"Endpoint: {url.split('?')[0]}")
    print(f"Requests: {args.requests}, concurrent workers: {args.concurrency}")
    print(f"Warm-up requests (excluded): {args.warmup}")
    print("Make sure you are testing the real PostgreSQL backend, not a fixture.")

    for _ in range(args.warmup):
        sample = request_once(method, url, body, args.timeout, token)
        if sample["status"] is None or not 200 <= sample["status"] < 300:
            print(f"Warm-up was unsuccessful: HTTP {sample['status']} {sample['error'] or ''}")
            print("Fix the endpoint/model readiness before interpreting benchmark results.")
            return 2

    results, wall_s = benchmark(
        method, url, body, args.concurrency, args.requests, args.timeout, token
    )
    successes = [r for r in results if r["status"] is not None and 200 <= r["status"] < 300]
    durations = [r["elapsed_ms"] for r in successes]
    codes = Counter("network-error" if r["status"] is None else str(r["status"]) for r in results)
    slow = sum(r["elapsed_ms"] > args.threshold_ms for r in results)
    summary = {
        "scenario": args.scenario,
        "method": method,
        "endpoint": url.split("?")[0],
        "concurrency": args.concurrency,
        "total_requests": args.requests,
        "successful_2xx": len(successes),
        "failed_or_non_2xx": len(results) - len(successes),
        "status_counts": dict(codes),
        "wall_time_s": round(wall_s, 3),
        "throughput_requests_s": round(args.requests / wall_s, 2) if wall_s > 0 else None,
        "mean_success_ms": round(statistics.mean(durations), 1) if durations else None,
        "p50_success_ms": round(statistics.median(durations), 1) if durations else None,
        "p95_success_ms": round(percentile(durations, 0.95), 1) if durations else None,
        "max_success_ms": round(max(durations), 1) if durations else None,
        "requests_over_threshold_ms": slow,
        "threshold_ms": args.threshold_ms,
        "all_requests_2xx": len(successes) == args.requests,
        "all_requests_under_threshold": slow == 0,
        "first_errors": [
            {"status": r["status"], "error": r["error"]}
            for r in results if not (r["status"] is not None and 200 <= r["status"] < 300)
        ][:3],
    }
    print("\n" + json.dumps(summary, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(f"\nSaved report: {args.output}")
    if len(successes) != args.requests or slow:
        print("\nCHECK: errors or requests exceeding the 3-second target occurred.")
        return 1
    print("\nPASS for this LOCAL sample: all responses 2xx and under the selected threshold.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
