"""Verify S2 against an isolated real Keycloak; never modify the team's running realm.

Uses a unique Compose project, random loopback ports, temporary credentials/realm,
and explicitly labeled historical fixtures. Removes only this project's resources.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
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


ROOT = Path(__file__).resolve().parents[3]
SERVICE = ROOT / "services/traffic-predictor"
ISSUER = "http://s2-auth-test.invalid/realms/smart-city"
QUERY = {"latitude": 43.0096, "longitude": -81.2737, "timezone": "America/Toronto",
         "start_at": "2026-11-01T00:00:00Z", "end_at": "2026-11-10T00:00:00Z"}


def verify():
    project = "scms-s2-auth-" + uuid.uuid4().hex[:10]
    secret = secrets.token_urlsafe(32)
    env = os.environ.copy()
    env.update({name: secrets.token_hex(24) for name in (
        "REGISTRY_API_KEY", "POSTGRES_PASSWORD", "INCIDENTS_POSTGRES_PASSWORD",
        "KEYCLOAK_DB_PASSWORD", "KEYCLOAK_ADMIN_PASSWORD")})
    env.update({"APP_ENV": "development", "JWT_ALLOW_INSECURE_JWKS": "true",
                "JWT_ISSUER": ISSUER, "JWT_AUDIENCE": "smart-city-api",
                "JWT_JWKS_URL": "http://keycloak:8080/realms/smart-city/protocol/openid-connect/certs",
                "JWT_LEEWAY_SECONDS": "30", "JWT_JWKS_TIMEOUT_SECONDS": "2", "JWT_ROLE_CLAIM": "roles"})
    report = {"verified_at": datetime.now(timezone.utc).isoformat(),
              "data": "synthetic historical fixture; no prediction model approved",
              "identity_provider": "isolated Keycloak 26.4.7", "production_verified": False}
    with tempfile.TemporaryDirectory(prefix="scms-s2-auth-") as directory:
        realm_path = Path(directory) / "realm.json"
        realm_path.write_text(json.dumps({
            "realm": "smart-city", "enabled": True, "sslRequired": "none",
            "clients": [{
                "clientId": "s2-test-caller", "enabled": True, "protocol": "openid-connect",
                "publicClient": False, "secret": secret, "serviceAccountsEnabled": True,
                "standardFlowEnabled": False, "directAccessGrantsEnabled": False,
                "protocolMappers": [{
                    "name": "api-audience", "protocol": "openid-connect", "protocolMapper": "oidc-audience-mapper",
                    "config": {"included.custom.audience": "smart-city-api", "access.token.claim": "true",
                               "id.token.claim": "false"},
                }],
            }],
        }), encoding="utf-8")
        fixtures = SERVICE / "tests/fixtures"
        mount = lambda path, target: json.dumps(path.as_posix() + ":" + target + ":ro")
        override = Path(directory) / "compose.yaml"
        override.write_text("\n".join([
            "services:",
            "  keycloak:", "    command: [start-dev, --http-port=8080, --import-realm]",
            "    environment:", "      KC_HOSTNAME: http://s2-auth-test.invalid",
            "    ports: !override", "      - '127.0.0.1::8080'",
            "    volumes:", "      - " + mount(realm_path, "/opt/keycloak/data/import/realm.json"),
            "  service-registry:", "    ports: !override", "      - '127.0.0.1::8080'",
            "  traffic-predictor:", "    ports: !override", "      - '127.0.0.1::8080'",
            "    environment:", "      S2_HISTORY_CSV: /fixtures/EXAMPLE_ONLY_historical_traffic.csv",
            "      S2_HISTORY_METADATA: /fixtures/EXAMPLE_ONLY_history_metadata.json",
            "    volumes:", "      - " + mount(fixtures, "/fixtures"), "",
        ]), encoding="utf-8")
        command = ["docker", "compose", "--project-name", project, "-f", str(ROOT / "docker-compose.yml"),
                   "-f", str(override)]

        def compose(*args, capture=False):
            return subprocess.run([*command, *args], cwd=ROOT, env=env, text=True, check=True,
                                  stdout=subprocess.PIPE if capture else None).stdout

        def base_url(service):
            return "http://" + compose("port", service, "8080", capture=True).strip()

        def wait_http(client, url, timeout=150):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    if client.get(url).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(.25)
            raise TimeoutError("Isolated authentication test service did not start")

        try:
            compose("up", "--build", "--detach", "keycloak", "traffic-predictor")
            identity_url, s2_url, registry_url = [base_url(name) for name in (
                "keycloak", "traffic-predictor", "service-registry")]
            with httpx.Client(timeout=5, trust_env=False) as client:
                wait_http(client, identity_url + "/realms/smart-city/.well-known/openid-configuration")
                wait_http(client, s2_url + "/ready")
                token_response = client.post(identity_url + "/realms/smart-city/protocol/openid-connect/token",
                    data={"grant_type": "client_credentials", "client_id": "s2-test-caller", "client_secret": secret})
                token_response.raise_for_status()
                headers = {"Authorization": "Bearer " + token_response.json()["access_token"]}
                path = "/v1/traffic/history/patterns"
                denied = client.get(s2_url + path, params=QUERY)
                assert denied.status_code == 401 and denied.headers["WWW-Authenticate"] == "Bearer"
                result = client.get(s2_url + path, params=QUERY, headers=headers)
                assert result.status_code == 200, "Keycloak JWT was not accepted by S2"
                assert result.json()["data_status"] == "historical" and result.json()["is_live"] is False
                discovery = client.get(registry_url + "/v1/services/traffic-predictor")
                assert discovery.status_code == 200
                report.update({"missing_token_status": 401, "keycloak_token_status": 200,
                               "registration_verified": True})

                def history_request(_):
                    started = time.perf_counter()
                    response = client.get(s2_url + path, params=QUERY, headers=headers)
                    return response.status_code, (time.perf_counter() - started) * 1000

                with ThreadPoolExecutor(max_workers=25) as pool:
                    results = list(pool.map(history_request, range(75)))
                assert all(status == 200 for status, _ in results)
                timings = sorted(elapsed for _, elapsed in results)
                report["authenticated_history_http"] = {
                    "requests": 75, "client_workers": 25, "errors": 0,
                    "p95_ms": round(timings[71], 3), "max_ms": round(timings[-1], 3),
                    "prediction_performance_verified": False,
                }
                compose("stop", "--timeout", "5", "keycloak")
                assert client.get(s2_url + path, params=QUERY, headers=headers).status_code == 200
                report["unexpired_jwks_cache_survives_outage"] = True
                # Restart only our S2 container to discard cached signing keys.
                compose("restart", "--timeout", "5", "traffic-predictor")
                s2_url = base_url("traffic-predictor")
                wait_http(client, s2_url + "/ready", timeout=30)
                unavailable = client.get(s2_url + path, params=QUERY, headers=headers)
                assert unavailable.status_code == 503 and unavailable.json()["error"]["retryable"] is True
                assert client.get(s2_url + "/health").status_code == 200
                report["uncached_jwks_outage_status"] = 503
                report["health_and_data_readiness_remain_available"] = True
        finally:
            compose("down", "--volumes", "--remove-orphans", "--timeout", "5")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = verify()
    encoded = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded)
