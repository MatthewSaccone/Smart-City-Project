"""Isolated S6 real-HTTP baseline and 50-request arrival/mixed workloads."""

import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from importlib.metadata import version, PackageNotFoundError
import json
import logging
import math
import os
from pathlib import Path
import platform
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

import httpx
from jsonschema import Draft202012Validator, FormatChecker
import yaml

SERVICE = Path(__file__).resolve().parents[1]
ROOT = SERVICE.parents[1]
sys.path.insert(0, str(SERVICE.parent))
from shared.testing_identity import EphemeralIdentity
from shared.service_tokens import ServiceTokenClient
AREA = {"latitude": 43.0096, "longitude": -81.2737, "radius_m": 1500}
ARRIVALS = "/v1/transit/stops/stop-102/arrivals"
CASES = [(ARRIVALS, {}, "ArrivalsResponse"),
    ("/v1/transit/stops", AREA, "StopsResponse"), ("/v1/transit/routes", AREA, "RoutesResponse"),
    ("/v1/transit/delays", {"route_id": "route-5"}, "DelaysResponse"),
    ("/v1/transit/disruptions", {"route_id": "route-5"}, "DisruptionsResponse"),
    ("/v1/transit/occupancy", {"route_id": "route-5"}, "OccupancyResponse")]


def package_version(name):
    try:
        return version(name)
    except PackageNotFoundError:
        return "not installed"


def summarize(samples):
    values = sorted(samples)
    return {"average_ms": round(statistics.mean(values), 3), "p50_ms": round(statistics.median(values), 3),
            "p95_ms": round(values[math.ceil(.95*len(values))-1], 3), "max_ms": round(max(values), 3)} if values else None


async def measure(base_url, *, rounds=3, token=None):
    if urlsplit(base_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Load testing is restricted to an operator-owned loopback test instance")
    if token is None:
        token = await asyncio.to_thread(ServiceTokenClient.from_env().get_token)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    contract = yaml.safe_load((ROOT / "shared/api-contracts/s6-public-transit.openapi.yaml").read_text(encoding="utf-8"))
    report = {"measured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "environment": {"client_python": platform.python_version(), "client_os": platform.platform(), "logical_cpus": os.cpu_count(),
            "client_packages": {name: package_version(name) for name in ("httpx", "httpcore", "anyio", "sniffio")}},
        "conditions": {"concurrency": 50, "rounds": rounds, "baseline_requests": 20, "http_timeout_seconds": 5,
            "workers": 1, "dataset": "169 simulated daily trips, 4 stops, 3 routes, historical simulated updates",
            "external_apis_used": False, "warmup_requests": 6, "persistent_connections": True},
        "production_scalability_verified": False}
    async with httpx.AsyncClient(base_url=base_url, timeout=5, trust_env=False, headers={"Authorization": "Bearer " + token},
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=50)) as client:
        for path, params, schema in CASES:
            response = await client.get(path, params=params)
            response.raise_for_status()
            Draft202012Validator({"$ref": "#/components/schemas/" + schema, "components": contract["components"]},
                                 format_checker=FormatChecker()).validate(response.json())
            assert response.headers["X-Request-ID"]
        report["contract_preflight"] = "passed: all six real HTTP responses match adopted OpenAPI"

        async def workload(cases, concurrent):
            samples, arrival_samples, errors = [], [], []
            statuses = {}
            peak = active = timeouts = 0
            arrival_total = arrivals_met = successes = total = 0

            async def request(case, gate):
                nonlocal active, peak, timeouts, total, successes, arrival_total, arrivals_met
                path, params, _ = case
                await gate.wait()
                active += 1
                peak = max(peak, active)
                total += 1
                is_arrival = path == ARRIVALS
                arrival_total += int(is_arrival)
                started = time.perf_counter()
                status = None
                try:
                    response = await client.get(path, params=params)
                    status = response.status_code
                    statuses[str(status)] = statuses.get(str(status), 0) + 1
                    if status != 200:
                        errors.append({"path": path, "status": status})
                    else:
                        successes += 1
                except httpx.TimeoutException:
                    timeouts += 1
                    errors.append({"path": path, "error": "timeout"})
                except httpx.HTTPError as exc:
                    errors.append({"path": path, "error": type(exc).__name__})
                finally:
                    elapsed = (time.perf_counter() - started) * 1000
                    samples.append(elapsed)
                    if is_arrival:
                        arrival_samples.append(elapsed)
                        arrivals_met += int(status == 200 and elapsed <= 1500)
                    active -= 1

            waves = rounds if concurrent else 20
            for wave in range(waves):
                gate = asyncio.Event()
                width = 50 if concurrent else 1
                tasks = [asyncio.create_task(request(cases[(wave*width+index) % len(cases)], gate)) for index in range(width)]
                await asyncio.sleep(0)
                gate.set()
                await asyncio.gather(*tasks)
            return {"requests": total, "successful_responses": successes, "http_status_counts": statuses,
                "http_errors": sum(count for status, count in statuses.items() if status != "200"),
                "timeouts": timeouts, "errors": errors, "peak_in_flight": peak,
                "latency": summarize(samples), "arrival_latency": summarize(arrival_samples),
                "arrival_requests": arrival_total, "arrivals_within_1500_ms": arrivals_met,
                "all_arrivals_met_1500_ms": arrival_total > 0 and arrivals_met == arrival_total,
                "wave_size": 50 if concurrent else 1, "waves": waves}

        report["baseline"] = await workload(CASES[:1], False)
        report["concurrent_arrivals"] = await workload(CASES[:1], True)
        report["concurrent_mixed"] = await workload(CASES, True)
    report["t055_local_pass"] = all(report[name]["all_arrivals_met_1500_ms"] for name in
                                    ("baseline", "concurrent_arrivals", "concurrent_mixed"))
    report["t061_local_pass"] = report["t055_local_pass"] and all(
        report[name]["peak_in_flight"] == 50 and not report[name]["errors"]
        for name in ("concurrent_arrivals", "concurrent_mixed"))
    return report


@asynccontextmanager
async def local_server():
    with EphemeralIdentity() as identity:
        async with local_server_with_identity(identity) as url:
            yield url, identity.token(roles=["service"], permissions=["s6:read"])


@asynccontextmanager
async def local_server_with_identity(identity):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = {key: value for key, value in os.environ.items() if not key.startswith("TRANSIT_")}
    env.update({"PYTHONPATH": os.pathsep.join((str(SERVICE), str(SERVICE.parent))), "REGISTRY_URL": "", **identity.environment()})
    with tempfile.TemporaryDirectory(prefix="scms-s6-http-") as folder:
        env["TRANSIT_DATABASE_PATH"] = str(Path(folder) / "transit.sqlite")
        log_path = Path(folder) / "server.log"
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                "--port", str(port), "--workers", "1", "--no-access-log", "--log-level", "warning"], env=env, cwd=SERVICE,
                stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        url = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + 30
            async with httpx.AsyncClient(timeout=1, trust_env=False) as client:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("S6 exited: " + log_path.read_text(encoding="utf-8")[-1500:])
                    try:
                        if (await client.get(url + "/ready")).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(.1)
                else:
                    raise TimeoutError("S6 did not become ready within 30 seconds")
            yield url
        finally:
            if process.poll() is None:
                if os.name == "nt":
                    await asyncio.to_thread(subprocess.run, ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    process.terminate()
            await asyncio.to_thread(process.wait, timeout=10)


async def main(args):
    if args.spawn_local:
        async with local_server() as (url, token):
            report = await measure(url, rounds=args.rounds, token=token)
    else:
        report = await measure(args.base_url, rounds=args.rounds)
    encoded = json.dumps(report, indent=2)
    if args.output:
        Path(args.output).write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    if not report["t055_local_pass"] or not report["t061_local_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    targets = parser.add_mutually_exclusive_group(required=True)
    targets.add_argument("--spawn-local", action="store_true")
    targets.add_argument("--base-url")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--output")
    args = parser.parse_args()
    if not 1 <= args.rounds <= 20:
        parser.error("rounds must be between 1 and 20")
    asyncio.run(main(args))
