"""Isolated real Keycloak, S4 PostgreSQL/events, registry, HTTP timing and outage checks."""

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import uuid

import httpx

ROOT = Path(__file__).resolve().parents[3]
SERVICE = ROOT / "services/parking-analytics"
sys.path.insert(0, str(SERVICE))
from app.datasets import CHANNEL, revision, validate_feed
from app.storage import ParkingDataStore
from benchmark import measure


def verify(output=None):
    project = "scms-s4-auth-data-" + uuid.uuid4().hex[:10]
    env = os.environ.copy()
    env.update({name: secrets.token_hex(24) for name in ("REGISTRY_API_KEY", "POSTGRES_PASSWORD",
        "INCIDENTS_POSTGRES_PASSWORD", "KEYCLOAK_DB_PASSWORD", "KEYCLOAK_ADMIN_PASSWORD", "PARKING_DB_PASSWORD")})
    env.update({"APP_ENV": "development", "JWT_ALLOW_INSECURE_JWKS": "true",
        "JWT_ISSUER": "http://s4-auth-test.invalid/realms/smart-city", "JWT_AUDIENCE": "smart-city-api",
        "JWT_JWKS_URL": "http://keycloak:8080/realms/smart-city/protocol/openid-connect/certs",
        "JWT_ROLE_CLAIM": "roles", "JWT_LEEWAY_SECONDS": "30", "JWT_JWKS_TIMEOUT_SECONDS": "2",
        "PARKING_REFRESH_SECONDS": "0.2", "PARKING_RETRY_MAX_SECONDS": "1",
        "PARKING_CACHE_RETENTION_SECONDS": "86400"})
    client_secret, reader_secret = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    report = {"verified_at": datetime.now(timezone.utc).isoformat(), "production_verified": False,
              "data": "explicitly simulated parking catalog, tariffs and occupancy"}
    with tempfile.TemporaryDirectory(prefix="scms-s4-auth-data-") as directory:
        audience = {"name": "api-audience", "protocol": "openid-connect", "protocolMapper": "oidc-audience-mapper",
            "config": {"included.custom.audience": "smart-city-api", "access.token.claim": "true", "id.token.claim": "false"}}
        permission = {"name": "internal-estimate", "protocol": "openid-connect", "protocolMapper": "oidc-hardcoded-claim-mapper",
            "config": {"claim.name": "permissions", "claim.value": "parking:estimate", "jsonType.label": "String",
                       "access.token.claim": "true", "id.token.claim": "false"}}
        clients = [{"clientId": name, "enabled": True, "protocol": "openid-connect", "publicClient": False,
                    "secret": secret, "serviceAccountsEnabled": True, "standardFlowEnabled": False,
                    "directAccessGrantsEnabled": False, "protocolMappers": mappers}
                   for name, secret, mappers in (("s4-test-caller", client_secret, [audience, permission]),
                                                ("s4-test-reader", reader_secret, [audience]))]
        realm_path = Path(directory) / "realm.json"
        realm_path.write_text(json.dumps({"realm": "smart-city", "enabled": True, "sslRequired": "none",
                                         "clients": clients}), encoding="utf-8")
        mount = lambda path, target: json.dumps(path.as_posix() + ":" + target + ":ro")
        override = Path(directory) / "verify.yaml"
        override.write_text("\n".join([
            "services:", "  keycloak:", "    command: [start-dev, --http-port=8080, --import-realm]",
            "    environment:", "      KC_HOSTNAME: http://s4-auth-test.invalid",
            "    ports: !override", "      - '127.0.0.1::8080'", "    volumes:",
            "      - " + mount(realm_path, "/opt/keycloak/data/import/realm.json"),
            "  parking-analytics:", "    ports: !override", "      - '127.0.0.1::8080'",
            "  parking-postgres:", "    ports:", "      - '127.0.0.1::5432'",
            "  service-registry:", "    ports: !override", "      - '127.0.0.1::8080'",
            "  registry-1:", "    volumes:", "      - " + mount(ROOT / "service-registry/tests", "/tests"), "",
        ]), encoding="utf-8")
        command = ["docker", "compose", "-p", project, "-f", str(ROOT / "docker-compose.yml"),
                   "-f", str(SERVICE / "compose.parking.yml"), "-f", str(SERVICE / "compose.parking.db.yml"),
                   "-f", str(override)]

        def compose(*args, capture=False):
            return subprocess.run([*command, *args], cwd=ROOT, env=env, check=True, text=True,
                                  stdout=subprocess.PIPE if capture else None).stdout

        def base_url(service):
            return "http://" + compose("port", service, "8080", capture=True).strip()

        try:
            compose("up", "--build", "--detach", "keycloak", "parking-analytics")
            identity_url, parking_url, registry_url = [base_url(name) for name in ("keycloak", "parking-analytics", "service-registry")]
            with httpx.Client(timeout=5, trust_env=False) as client:
                def wait_for(predicate, description, timeout=150):
                    deadline = time.monotonic() + timeout
                    while time.monotonic() < deadline:
                        try:
                            if predicate():
                                return
                        except httpx.HTTPError:
                            pass
                        time.sleep(.1)
                    raise TimeoutError(description)

                wait_for(lambda: client.get(parking_url + "/health").status_code == 200, "S4 process unavailable")
                wait_for(lambda: client.get(identity_url + "/realms/smart-city/.well-known/openid-configuration").status_code == 200,
                         "Test Keycloak realm unavailable")
                compose("exec", "-T", "parking-analytics", "python", "tools/import_dataset.py", "--seed-demo")
                wait_for(lambda: client.get(parking_url + "/ready").status_code == 200, "S4 datasets unavailable")

                def token(name, secret):
                    response = client.post(identity_url + "/realms/smart-city/protocol/openid-connect/token",
                        data={"grant_type": "client_credentials", "client_id": name, "client_secret": secret})
                    response.raise_for_status()
                    return response.json()["access_token"]

                estimator = token("s4-test-caller", client_secret)
                reader = token("s4-test-reader", reader_secret)
                headers = {"Authorization": "Bearer " + estimator}
                detail_path, status_path = "/v1/parking/facilities/demo-central", "/v1/parking/ingestion/status"
                assert client.get(parking_url + detail_path).status_code == 401
                assert client.post(parking_url + "/v1/parking/occupancy/estimate", json={"observations": []},
                    headers={"Authorization": "Bearer " + reader}).status_code == 403
                assert client.post(parking_url + "/v1/parking/occupancy/estimate", json={"observations": []}, headers=headers).status_code == 200
                report["keycloak_read_auth_and_estimator_permission"] = "passed"

                def status():
                    response = client.get(parking_url + status_path, headers=headers)
                    response.raise_for_status()
                    return response.json()

                wait_for(lambda: status()["broker_connected"] and all(status()["sources"].values()), "S4 subscriber unavailable")
                before = client.get(parking_url + detail_path, headers=headers).json()
                discovery = client.get(registry_url + "/v1/services/parking-analytics")
                assert discovery.status_code == 200
                first = discovery.json()["instances"][0]
                time.sleep(5.5)
                after = client.get(registry_url + "/v1/services/parking-analytics").json()
                assert next(item for item in after["instances"] if item["instance_id"] == first["instance_id"])["last_heartbeat_at"] > first["last_heartbeat_at"]
                report["registry_registration_and_heartbeat"] = "passed"
                report["performance"] = asyncio.run(measure(parking_url, estimator))
                assert report["performance"]["t040_local_pass"] and report["performance"]["t044_local_pass"]

                port = compose("port", "parking-postgres", "5432", capture=True).strip().rsplit(":", 1)[1]
                store = ParkingDataStore(f"postgresql://parking_analytics:{env['PARKING_DB_PASSWORD']}@127.0.0.1:{port}/parking_analytics")
                with store.connect() as connection:
                    records = store.read(connection)
                pricing = records["pricing"].payload
                pricing["facilities"]["demo-central"]["rates"][0]["amount"] = 4.25
                assert store.publish("pricing", pricing)
                expected = revision(validate_feed("pricing", pricing))
                wait_for(lambda: status()["revisions"]["pricing"] == expected, "Tariff event was not consumed")
                changed = client.get(parking_url + detail_path, headers=headers).json()
                assert changed["pricing"]["rates"][0]["amount"] == 4.25
                assert changed["occupancy"]["service_updated_at"] == before["occupancy"]["service_updated_at"]
                assert not store.publish("pricing", pricing)
                report["independent_tariff_event_and_idempotent_publication"] = "passed"

                rejected = status()["notifications_rejected"]
                with store.connect() as connection:
                    connection.execute("SELECT pg_notify(%s,%s)", (CHANNEL, '{"schema_version":99}'))
                    catalog = records["catalog"].payload
                    catalog["facilities"][0]["name"] = "Simulated updated Central Parking"
                    feed = validate_feed("catalog", catalog)
                    connection.execute("UPDATE parking_datasets SET payload=%s::jsonb,revision=%s,received_at=clock_timestamp() WHERE stream='catalog'",
                                       (feed.model_dump_json(), revision(feed)))
                wait_for(lambda: status()["notifications_rejected"] > rejected, "Malformed hint not rejected")
                wait_for(lambda: status()["revisions"]["catalog"] == revision(feed), "Metadata-only change not reconciled")
                report["malformed_event_and_missed_hint_reconciliation"] = "passed"

                check = "import os,pytest; os.environ['TEST_REGISTRY_DATABASE_URL']=os.environ['REGISTRY_DATABASE_URL']; raise SystemExit(pytest.main(['/tests','-q','-p','no:cacheprovider']))"
                report["registry_regression"] = compose("exec", "-T", "registry-1", "python", "-c", check, capture=True).strip()
                baseline = client.get(parking_url + detail_path, headers=headers).json()
                compose("stop", "--timeout", "5", "parking-postgres")
                wait_for(lambda: not any(status()["sources"].values()), "S4 database outage not observed", timeout=30)
                compose("restart", "--timeout", "5", "parking-analytics")
                parking_url = base_url("parking-analytics")
                wait_for(lambda: client.get(parking_url + "/ready").status_code == 200, "Persisted S4 catalog did not recover", timeout=30)
                fallback = client.get(parking_url + detail_path, headers=headers).json()
                assert fallback["facility"] == baseline["facility"]
                assert fallback["pricing"]["rates"] == baseline["pricing"]["rates"]
                assert fallback["pricing"]["service_updated_at"] == baseline["pricing"]["service_updated_at"]
                assert fallback["occupancy"]["service_updated_at"] == baseline["occupancy"]["service_updated_at"]
                assert fallback["occupancy"]["is_live"] is False and fallback["availability_status"] == "unknown"
                report["postgres_outage_restart_catalog_tariffs_occupancy_preserved"] = "passed"
                compose("stop", "--timeout", "5", "keycloak")
                assert client.get(parking_url + detail_path, headers=headers).status_code == 200
                compose("restart", "--timeout", "5", "parking-analytics")
                parking_url = base_url("parking-analytics")
                wait_for(lambda: client.get(parking_url + "/ready").status_code == 200, "S4 fallback unavailable", timeout=30)
                assert client.get(parking_url + detail_path, headers=headers).status_code == 503
                report["cached_jwks_then_fail_closed_without_signing_keys"] = "passed"
                report["scope"] = "Isolated S4, its own PostgreSQL, test Keycloak and registry dependencies; no S1/S2/S3 or team containers changed"
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
