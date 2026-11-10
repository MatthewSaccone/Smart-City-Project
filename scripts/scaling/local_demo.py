"""Opt-in process actuator and load harness for T009; no shared deployments touched."""

import argparse
import asyncio
from collections import Counter
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time

import httpx

from policy import Controller, Metrics, Policy
from routing import RegistryRouter


ROOT = Path(__file__).resolve().parents[2]


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def percentile95(values):
    return sorted(values)[math.ceil(len(values) * 0.95) - 1] if values else None


class Processes:
    """Own only the processes launched by this run, using ephemeral ports/storage."""

    def __init__(self, directory: Path, service_id: str):
        self.directory = directory
        self.service_id = service_id
        self.api_key = secrets.token_urlsafe(32)
        self.registry_url = f"http://127.0.0.1:{free_port()}"
        self.processes = []
        self.workers = {}
        self.startups = []

    def spawn(self, name, app_dir, url, registry=False):
        env = os.environ.copy()
        # Never connect the demo to an inherited/shared database or registry.
        env.pop("REGISTRY_DATABASE_URL", None)
        env.pop("TEST_REGISTRY_DATABASE_URL", None)
        env["REGISTRY_DATABASE_PATH"] = str(self.directory / "registry.db")
        env["REGISTRY_API_KEY"] = self.api_key
        env["REGISTRY_ADVERTISED_ENDPOINT"] = url
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONPATH"] = os.pathsep.join([str(app_dir), str(ROOT / "services")])
        if registry:
            env.pop("REGISTRY_URL", None)
        else:
            env["REGISTRY_URL"] = self.registry_url
        log = (self.directory / f"{name}.log").open("w", encoding="utf-8")
        try:
            process = subprocess.Popen(
                [sys.executable, "-B", "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                 "--port", url.rsplit(":", 1)[1], "--log-level", "warning", "--no-access-log"],
                cwd=app_dir, env=env, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        finally:
            log.close()
        self.processes.append(process)
        return process

    async def wait_health(self, client, process, url):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Demo process exited with status {process.returncode}")
            try:
                response = await client.get(url + "/health")
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
        raise TimeoutError(f"Demo process not healthy within 30 seconds: {url}")

    async def start_registry(self, client):
        process = self.spawn("registry", ROOT / "service-registry", self.registry_url, registry=True)
        await self.wait_health(client, process, self.registry_url)

    async def start_worker(self, client, router):
        started = time.monotonic()
        url = f"http://127.0.0.1:{free_port()}"
        process = self.spawn(f"worker-{len(self.processes)}", ROOT / "services" / self.service_id, url)
        await self.wait_health(client, process, url)
        deadline = started + 30
        while time.monotonic() < deadline:
            await router.refresh()
            matching = [item for item in router.instances if item["endpoint"].rstrip("/") == url]
            if matching:
                identity = matching[0]["instance_id"]
                self.workers[identity] = process
                elapsed = round(time.monotonic() - started, 3)
                self.startups.append(elapsed)
                print(json.dumps({"event": "worker_discovered", "instance_id": identity,
                                  "startup_to_discovery_seconds": elapsed}), flush=True)
                return
            await asyncio.sleep(0.1)
        raise TimeoutError("Worker did not become discoverable within 30 seconds")

    async def stop_worker(self, client, router):
        identity = next(reversed(self.workers))
        # Called between fully awaited batches: no in-flight requests remain.
        router.draining.add(identity)
        response = await client.delete(f"{self.registry_url}/v1/instances/{identity}",
                                      headers={"X-Registry-API-Key": self.api_key})
        response.raise_for_status()
        process = self.workers.pop(identity)
        process.terminate()
        await asyncio.to_thread(process.wait, timeout=10)
        await router.refresh()

    async def close(self):
        for process in reversed(self.processes):
            if process.poll() is None:
                process.terminate()
        for process in reversed(self.processes):
            try:
                await asyncio.to_thread(process.wait, timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait, timeout=10)


async def run(args):
    policy = replace(Policy.load(Path(__file__).with_name("policy.json")), service_id=args.service)
    controller = Controller(policy)
    latencies, errors, distribution = [], [], Counter()
    actions = []
    with tempfile.TemporaryDirectory(prefix="scms-t009-") as temp:
        processes = Processes(Path(temp), policy.service_id)
        try:
            async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
                router = RegistryRouter(client, processes.registry_url, policy.service_id)
                await processes.start_registry(client)
                for _ in range(policy.min_replicas):
                    await processes.start_worker(client, router)
                started = time.monotonic()
                while time.monotonic() - started < args.seconds:
                    await router.refresh()
                    window_started = time.monotonic()
                    window_latencies, window_errors = [], []

                    async def request_after(delay):
                        await asyncio.sleep(delay)
                        before = time.monotonic()
                        try:
                            identity, response = await router.get("/")
                            response.raise_for_status()
                            distribution[identity] += 1
                        except (httpx.HTTPError, LookupError) as exc:
                            window_errors.append(type(exc).__name__)
                        finally:
                            window_latencies.append((time.monotonic() - before) * 1000)

                    count = int(args.rps * policy.sample_seconds)
                    # A paced workload, not a burst of count simultaneous requests.
                    await asyncio.gather(*(request_after(i / args.rps) for i in range(count)),
                                         asyncio.sleep(policy.sample_seconds))
                    elapsed = time.monotonic() - window_started
                    now = time.monotonic() - started
                    metrics = Metrics(now, count / elapsed, percentile95(window_latencies),
                                      len(window_errors) / count)
                    decision = controller.evaluate(metrics, len(processes.workers), now)
                    print(json.dumps({"event": "sample", "elapsed_seconds": round(now, 3),
                        "measured_rps": round(metrics.request_rate, 2), "p95_ms": round(metrics.p95_ms, 2),
                        "errors": len(window_errors), "replicas": len(processes.workers),
                        "desired_replicas": decision.replicas, "reason": decision.reason}), flush=True)
                    latencies.extend(window_latencies)
                    errors.extend(window_errors)
                    if decision.replicas != len(processes.workers):
                        actions.append({"at_seconds": round(now, 3), "desired": decision.replicas})
                    while len(processes.workers) < decision.replicas:
                        await processes.start_worker(client, router)
                    while len(processes.workers) > decision.replicas:
                        await processes.stop_worker(client, router)
                print(json.dumps({"mode": "local_process_prototype", "service": policy.service_id,
                    "workload": "starter root endpoint; NOT traffic analytics acceptance load",
                    "requests": len(latencies), "errors": len(errors), "p95_ms": percentile95(latencies),
                    "routed_instances": dict(distribution), "scaling_actions": actions,
                    "startup_to_discovery_seconds": processes.startups,
                    "asr5_acceptance_verified": False}, indent=2), flush=True)
                if errors:
                    raise RuntimeError(f"Local demo recorded {len(errors)} request errors")
        except BaseException:
            for log in Path(temp).glob("*.log"):
                print(f"{log.name}:\n{log.read_text(encoding='utf-8')[-4000:]}", file=sys.stderr)
            raise
        finally:
            await processes.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", choices=["live-traffic", "parking-analytics"], default="live-traffic")
    parser.add_argument("--rps", type=int, default=45)
    parser.add_argument("--seconds", type=int, default=30)
    options = parser.parse_args()
    if not 1 <= options.rps <= 200 or not 15 <= options.seconds <= 300:
        parser.error("Use rps 1..200 and seconds 15..300")
    asyncio.run(run(options))
