"""Reproducible T040/T044 HTTP measurements, including 25 concurrent estimators."""

import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
import secrets
import socket
import subprocess
import sys
import tempfile
import time

import httpx


SERVICE = Path(__file__).resolve().parents[1]
BOUNDS = "min_lat=43.65&min_lon=-79.39&max_lat=43.66&max_lon=-79.37"
READS = {"populated_search": f"/v1/parking/facilities?{BOUNDS}",
         "empty_search": "/v1/parking/facilities?min_lat=0&min_lon=0&max_lat=1&max_lon=1",
         "facility_detail": "/v1/parking/facilities/demo-central"}


def payload(observation_count):
    fixture = json.loads((SERVICE / "app/fixtures/parking.json").read_text(encoding="utf-8"))
    samples = []
    for index in range(observation_count // 2):
        for timestamp in ("2026-11-09T16:59:10Z", "2026-11-09T16:59:50Z"):
            samples.append({"subject_id": f"benchmark-{index}", "latitude": 43.6532,
                            "longitude": -79.3832, "observed_at": timestamp})
    return {"observations": samples, "as_of": fixture["as_of"]}


def summary(samples):
    ordered = sorted(samples)
    return {"requests": len(ordered), "p50_ms": round(ordered[math.ceil(len(ordered) * .5) - 1], 3),
            "p95_ms": round(ordered[math.ceil(len(ordered) * .95) - 1], 3),
            "max_ms": round(ordered[-1], 3)}


async def measure(base_url, token, observation_count=1000, rounds=3):
    request_body = payload(observation_count)
    errors, sequential, mixed = [], {name: [] for name in READS}, {name: [] for name in READS}
    estimates = []
    active, peak, overlapping_reads = 0, 0, 0
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=base_url, timeout=30, trust_env=False,
                                 limits=httpx.Limits(max_connections=100, max_keepalive_connections=100)) as client:
        (await client.get("/ready")).raise_for_status()
        # Explicit warm-up of every measured operation, including estimation.
        for _ in range(3):
            for path in READS.values():
                (await client.get(path)).raise_for_status()
            (await client.post("/v1/parking/occupancy/estimate", json=request_body, headers=headers)).raise_for_status()

        async def timed(method, path, destination, **kwargs):
            started = time.perf_counter()
            try:
                response = await client.request(method, path, **kwargs)
                response.raise_for_status()
                body = response.json()
                if path.startswith("/v1/parking/facilities/demo-"):
                    assert body["facility"]["facility_id"] == "demo-central"
                else:
                    assert isinstance(body["items"], list)
            except Exception as exc:
                errors.append(type(exc).__name__)
            finally:
                destination.append((time.perf_counter() - started) * 1000)

        for _ in range(10):
            for name, path in READS.items():
                await timed("GET", path, sequential[name])

        for _ in range(rounds):
            gate = asyncio.Event()

            async def estimator():
                nonlocal active, peak
                await gate.wait()
                active += 1
                peak = max(peak, active)
                try:
                    await timed("POST", "/v1/parking/occupancy/estimate", estimates,
                                json=request_body, headers=headers)
                finally:
                    active -= 1

            async def reader():
                nonlocal overlapping_reads
                await gate.wait()
                for name, path in READS.items():
                    if active:
                        overlapping_reads += 1
                    await timed("GET", path, mixed[name])

            tasks = [asyncio.create_task(estimator()) for _ in range(25)]
            tasks.extend(asyncio.create_task(reader()) for _ in range(5))
            gate.set()
            await asyncio.gather(*tasks)
    reads_pass = all(max(samples) <= 2000 for group in (sequential, mixed) for samples in group.values())
    return {"measured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "environment": {"client_python": platform.python_version(), "client_os": platform.platform(),
                        "logical_cpus": os.cpu_count()},
        "conditions": {"source": "simulated inputs", "facilities": 3, "observations_per_estimate": observation_count,
                       "concurrent_estimators": 25, "concurrent_readers": 5, "rounds": rounds,
                       "warmup_rounds": 3, "serial_requests_per_read_operation": 10,
                       "estimator_deadline_ms": None},
        "sequential_reads": {name: summary(samples) for name, samples in sequential.items()},
        "reads_during_estimation": {name: summary(samples) for name, samples in mixed.items()},
        "estimation": summary(estimates), "peak_in_flight_estimates": peak,
        "reads_started_during_estimation": overlapping_reads, "errors": errors,
        "t040_local_pass": reads_pass and not errors,
        "t044_local_pass": reads_pass and not errors and peak == 25 and overlapping_reads > 0,
        "production_scalability_verified": False}


@asynccontextmanager
async def local_server():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    token = secrets.token_urlsafe(32)
    env = os.environ.copy()
    env.update({"PYTHONPATH": os.pathsep.join([str(SERVICE), str(SERVICE.parent)]),
                "REGISTRY_URL": "", "PARKING_INTERNAL_CLIENTS": json.dumps([
                    {"token": token, "permissions": ["parking:estimate"]}]),
                "PARKING_OCCUPANCY_MODE": "fixture"})
    env.pop("PARKING_CACHE_PATH", None)
    with tempfile.TemporaryDirectory(prefix="scms-s4-benchmark-") as directory:
        log_path = Path(directory) / "server.log"
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                "--port", str(port), "--no-access-log", "--log-level", "warning"], env=env, cwd=SERVICE,
                stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        base_url = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + 30
            async with httpx.AsyncClient(timeout=1, trust_env=False) as client:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("Parking process exited: " + log_path.read_text(encoding="utf-8")[-2000:])
                    try:
                        if (await client.get(base_url + "/ready")).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(.1)
                else:
                    raise TimeoutError("Parking server did not become ready within 30 seconds")
            yield base_url, token
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                await asyncio.to_thread(process.wait, timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait, timeout=10)


async def main(args):
    if args.spawn_local:
        async with local_server() as (url, token):
            result = await measure(url, token, args.observations, args.rounds)
    else:
        token = os.getenv(args.token_env)
        if not token:
            raise ValueError(f"Set {args.token_env} to a configured internal estimator credential")
        result = await measure(args.base_url, token, args.observations, args.rounds)
    encoded = json.dumps(result, indent=2)
    if args.output:
        Path(args.output).write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    if not result["t040_local_pass"] or not result["t044_local_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--spawn-local", action="store_true")
    target.add_argument("--base-url")
    parser.add_argument("--token-env", default="PARKING_ESTIMATOR_TOKEN")
    parser.add_argument("--observations", type=int, default=1000)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--output")
    args = parser.parse_args()
    if not 2 <= args.observations <= 10000 or args.observations % 2 or not 1 <= args.rounds <= 20:
        parser.error("Use an even observations count from 2..10000 and rounds 1..20")
    asyncio.run(main(args))
