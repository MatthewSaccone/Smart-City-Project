"""Real S6 HTTP, registry heartbeats, persisted fallback and isolated Docker load tests."""

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

from benchmark import ARRIVALS, AREA, ROOT, SERVICE, measure


def verify(output=None):
    project = "scms-s6-verify-" + uuid.uuid4().hex[:10]
    env = os.environ.copy()
    env.update({key: secrets.token_hex(24) for key in ("REGISTRY_API_KEY", "POSTGRES_PASSWORD",
        "TRAFFIC_POSTGRES_PASSWORD", "INCIDENTS_POSTGRES_PASSWORD", "KEYCLOAK_DB_PASSWORD", "KEYCLOAK_ADMIN_PASSWORD")})
    env.update({"TRANSIT_REFRESH_SECONDS": ".2", "TRANSIT_LIVE_MODE": "file", "TRANSIT_LIVE_RETENTION_SECONDS": "86400"})
    report = {"verified_at": datetime.now(timezone.utc).isoformat(), "production_verified": False,
              "dataset": "explicitly simulated S6 schedule and historical synthetic observations"}
    with tempfile.TemporaryDirectory(prefix="scms-s6-compose-") as folder:
        override = Path(folder) / "verify.yml"
        mount = json.dumps((ROOT / "service-registry/tests").as_posix() + ":/tests:ro")
        override.write_text("\n".join(["services:", "  transit-analytics:", "    ports: !override",
            "      - '127.0.0.1::8080'", "  service-registry:", "    ports: !override",
            "      - '127.0.0.1::8080'", "  registry-1:", "    volumes:", "      - " + mount, ""]), encoding="utf-8")
        command = ["docker", "compose", "-p", project, "-f", str(ROOT / "docker-compose.yml"),
                   "-f", str(SERVICE / "compose.transit.yml"), "-f", str(override)]

        def compose(*args, capture=False):
            return subprocess.run([*command, *args], cwd=ROOT, env=env, check=True, text=True,
                                  stdout=subprocess.PIPE if capture else None).stdout

        def address(service):
            return "http://" + compose("port", service, "8080", capture=True).strip()

        try:
            # Bootstrap one existing registry replica before the second joins,
            # keeping S6 verification independent of concurrent registry DDL.
            compose("up", "--build", "--detach", "--wait", "registry-1")
            compose("up", "--build", "--detach", "transit-analytics")
            transit_url, registry_url = address("transit-analytics"), address("service-registry")
            with httpx.Client(timeout=5, trust_env=False) as client:
                def wait_for(predicate, description, timeout=60):
                    deadline = time.monotonic() + timeout
                    while time.monotonic() < deadline:
                        try:
                            if predicate(): return
                        except httpx.HTTPError:
                            pass
                        time.sleep(.1)
                    raise TimeoutError(description)

                def registrations():
                    response = client.get(registry_url + "/v1/services/transit-analytics")
                    return response.json().get("instances", []) if response.status_code == 200 else []

                wait_for(lambda: client.get(transit_url + "/ready").status_code == 200, "S6 not ready")
                assert client.get(transit_url + "/health").json() == {"service": "transit-analytics", "status": "healthy"}
                wait_for(lambda: len(registrations()) == 1, "S6 registration missing")
                first = registrations()[0]
                wait_for(lambda: any(item["instance_id"] == first["instance_id"] and
                    item["last_heartbeat_at"] > first["last_heartbeat_at"] for item in registrations()), "S6 heartbeat missing", timeout=15)
                report["registration_and_five_second_heartbeat"] = "passed"
                report["performance"] = asyncio.run(measure(transit_url))
                assert report["performance"]["t055_local_pass"] and report["performance"]["t061_local_pass"]
                report["docker_resources"] = subprocess.run(["docker", "info", "--format", "CPUs={{.NCPU}}; MemoryBytes={{.MemTotal}}"],
                    check=True, text=True, capture_output=True).stdout.strip()
                schedule = client.get(transit_url + "/v1/transit/stops", params=AREA).json()
                arrivals = client.get(transit_url + ARRIVALS).json()
                occupancy = client.get(transit_url + "/v1/transit/occupancy", params={"route_id": "route-5"}).json()
                # Change only files inside this randomly named disposable S6
                # container. Its persistent database must survive the restart.
                remove_sources = "from pathlib import Path; [p.rename(p.with_suffix('.offline')) for p in Path('app/fixtures').glob('*.json')]"
                compose("exec", "-T", "transit-analytics", "python", "-c", remove_sources)
                compose("restart", "--timeout", "10", "transit-analytics")
                transit_url = address("transit-analytics")
                wait_for(lambda: client.get(transit_url + "/ready").status_code == 200, "S6 persisted schedule failed")
                after = client.get(transit_url + "/v1/transit/stops", params=AREA).json()
                assert after["items"] == schedule["items"] and after["source_updated_at"] == schedule["source_updated_at"]
                fallback = client.get(transit_url + ARRIVALS).json()
                assert fallback["items"] and not fallback["provenance"]["is_live"]
                assert fallback["provenance"]["service_updated_at"] == arrivals["provenance"]["service_updated_at"]
                assert all(event["basis"] == "schedule" and event["estimated_at"] is None
                    for row in fallback["items"] for event in (row["arrival"], row["departure"]) if event)
                retained = client.get(transit_url + "/v1/transit/occupancy", params={"route_id": "route-5"}).json()
                assert retained["items"] == occupancy["items"]
                assert retained["provenance"]["last_live_update_at"] == occupancy["provenance"]["last_live_update_at"]
                report["missing_sources_and_restart_use_persisted_schedule_and_truthful_cached_observations"] = "passed"
                check = "import os,pytest; os.environ['TEST_REGISTRY_DATABASE_URL']=os.environ['REGISTRY_DATABASE_URL']; raise SystemExit(pytest.main(['/tests','-q','-p','no:cacheprovider']))"
                report["registry_regression"] = compose("exec", "-T", "registry-1", "python", "-c", check, capture=True).strip()
                wait_for(lambda: len(registrations()) == 1, "S6 did not re-register after restart")
                instance = registrations()[0]["instance_id"]
                compose("stop", "--timeout", "10", "transit-analytics")
                wait_for(lambda: all(item["instance_id"] != instance for item in registrations()), "S6 did not deregister", timeout=5)
                report["graceful_shutdown_and_deregistration"] = "passed"
                report["scope"] = "Isolated S6 and existing registry dependencies only; no teammate containers, live APIs or shared data modified"
        except Exception:
            try:
                diagnostics = compose("logs", "--no-color", "--tail", "40", "transit-analytics", "registry-1", "registry-2", capture=True)
                for key, value in env.items():
                    if value and any(part in key.upper() for part in ("PASSWORD", "SECRET", "API_KEY", "TOKEN", "DATABASE_URL")):
                        diagnostics = diagnostics.replace(value, "<redacted>")
                print(diagnostics)
            except Exception:
                pass
            raise
        finally:
            compose("down", "--volumes", "--remove-orphans", "--timeout", "5")
    if output:
        Path(output).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    verify(parser.parse_args().output)
