"""Isolated verification using the team's real Compose/registry configuration."""

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import uuid

import httpx

from benchmark import measure


ROOT = Path(__file__).resolve().parents[3]


async def verify(output):
    project = "scms-s4-verify-" + uuid.uuid4().hex[:10]
    token = secrets.token_urlsafe(32)
    env = os.environ.copy()
    env.update({"REGISTRY_API_KEY": secrets.token_urlsafe(32), "POSTGRES_PASSWORD": secrets.token_hex(24),
                "PARKING_OCCUPANCY_MODE": "fixture", "PARKING_INTERNAL_CLIENTS": json.dumps([
                    {"token": token, "permissions": ["parking:estimate"]}])})
    report = {}
    with tempfile.TemporaryDirectory(prefix="scms-s4-compose-") as directory:
        override = Path(directory) / "verify.yaml"
        test_mount = json.dumps((ROOT / "service-registry/tests").as_posix() + ":/tests:ro")
        override.write_text("\n".join([
            "services:",
            "  service-registry:", "    ports: !override", "      - '127.0.0.1::8080'",
            "  registry-1:", "    volumes:", "      - " + test_mount,
            "  parking-analytics:", "    ports: !override", "      - '127.0.0.1::8080'", "",
        ]), encoding="utf-8")
        command = ["docker", "compose", "--project-name", project, "-f", str(ROOT / "docker-compose.yml"),
                   "-f", str(ROOT / "services/parking-analytics/compose.parking.yml"), "-f", str(override)]

        def compose(*args, capture=False):
            return subprocess.run([*command, *args], cwd=ROOT, env=env, text=True, check=True,
                                  stdout=subprocess.PIPE if capture else None).stdout

        async def ready(client, url):
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                try:
                    response = await client.get(url + "/ready")
                    if response.status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(.2)
            raise TimeoutError("Parking container did not become ready")

        try:
            # Explicit selection starts only S4 and its registry/PostgreSQL dependencies.
            compose("up", "--build", "--detach", "--wait", "--wait-timeout", "150", "parking-analytics")
            parking_url = "http://" + compose("port", "parking-analytics", "8080", capture=True).strip()
            registry_url = "http://" + compose("port", "service-registry", "8080", capture=True).strip()
            async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
                await ready(client, parking_url)
                before = (await client.get(parking_url + "/v1/parking/facilities/demo-central")).json()
                deadline = time.monotonic() + 15
                while True:
                    discovery = await client.get(registry_url + "/v1/services/parking-analytics")
                    if discovery.status_code == 200:
                        break
                    if time.monotonic() > deadline:
                        raise TimeoutError("Parking did not register")
                    await asyncio.sleep(.2)
                first = discovery.json()["instances"][0]
                assert first["endpoint"].rstrip("/") == "http://parking-analytics:8080"
                await asyncio.sleep(5.5)
                after = (await client.get(registry_url + "/v1/services/parking-analytics")).json()
                second = next(item for item in after["instances"] if item["instance_id"] == first["instance_id"])
                assert second["last_heartbeat_at"] > first["last_heartbeat_at"]
                report["registry_registration_and_heartbeat"] = "passed"

            report["performance"] = await measure(parking_url, token)
            assert report["performance"]["t040_local_pass"] and report["performance"]["t044_local_pass"]

            # The unchanged registry tests run inside an isolated registry container.
            check = "import os,pytest; os.environ['TEST_REGISTRY_DATABASE_URL']=os.environ['REGISTRY_DATABASE_URL']; raise SystemExit(pytest.main(['/tests','-q','-p','no:cacheprovider']))"
            report["registry_regression"] = compose("exec", "-T", "registry-1", "python", "-c", check, capture=True).strip()
            print(report["registry_regression"], flush=True)

            env["PARKING_OCCUPANCY_MODE"] = "unavailable"
            compose("up", "--detach", "--no-deps", "--force-recreate", "parking-analytics")
            # Recreating a container may allocate a different ephemeral host port.
            parking_url = "http://" + compose("port", "parking-analytics", "8080", capture=True).strip()
            async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
                await ready(client, parking_url)
                detail = await client.get(parking_url + "/v1/parking/facilities/demo-central")
                assert detail.status_code == 200
                degraded = detail.json()
                assert degraded["pricing"]["rates"] == before["pricing"]["rates"]
                assert degraded["pricing"]["source_updated_at"] == before["pricing"]["source_updated_at"]
                assert degraded["occupancy"]["estimated_occupied_spaces"] == before["occupancy"]["estimated_occupied_spaces"]
                assert degraded["occupancy"]["service_updated_at"] == before["occupancy"]["service_updated_at"]
                assert degraded["occupancy"]["is_live"] is False and degraded["occupancy"]["is_stale"] is True
                assert degraded["availability_status"] == "unknown"
                search = await client.get(parking_url + "/v1/parking/facilities", params={
                    "min_lat": 43.65, "min_lon": -79.39, "max_lat": 43.66, "max_lon": -79.37})
                assert search.status_code == 200 and len(search.json()["items"]) == 2
                assert (await client.get(parking_url + "/ready")).status_code == 200
                report["outage_restart_with_persisted_aggregate"] = "passed"
                report["degraded_example"] = degraded
            report["completed_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            report["scope"] = "Isolated Compose S4 + PostgreSQL + two registry APIs + HAProxy; no S1 or other domain services"
            if output:
                Path(output).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(report, indent=2), flush=True)
        finally:
            # Remove only this invocation's uniquely named project and volumes.
            compose("down", "--volumes", "--remove-orphans")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    asyncio.run(verify(parser.parse_args().output))
