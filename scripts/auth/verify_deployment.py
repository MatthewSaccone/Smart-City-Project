"""Non-destructive HTTP smoke test for an already configured local deployment.

Use separate service-account app passwords per tested service. This script never
starts/stops containers, writes city records, or prints credentials.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

import httpx
import jwt

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services"))
from shared.service_tokens import ServiceTokenClient

CASES = {
    "s1": (8081, "/v1/traffic/status", {}),
    "s2": (8082, "/v1/traffic/ingestion/status", {}),
    "s3": (8083, "/v1/incidents", {"latitude": 43.0096, "longitude": -81.2737, "radius_m": 1500}),
    "s4": (8084, "/v1/parking/ingestion/status", {}),
    "s5": (8085, "/v1/weather/current", {"latitude": 43.0096, "longitude": -81.2737}),
    "s6": (8086, "/v1/transit/stops", {"latitude": 43.0096, "longitude": -81.2737, "radius_m": 1500}),
    "s7": (8087, "/auth/check", {}),
}


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def identity(service):
    prefix = service.upper() + "_"
    return ServiceTokenClient(os.environ["AUTHENTIK_TOKEN_URL"], os.getenv("JWT_AUDIENCE", "smart-city-api"),
        os.environ[prefix + "AUTHENTIK_SERVICE_USERNAME"], os.environ[prefix + "AUTHENTIK_SERVICE_APP_PASSWORD"])


def verify(services=None, output=None, wait_expiry=False):
    services = services or list(CASES)
    report = {"identity_provider": "Authentik", "production_verified": False, "services": {}}
    issuer = os.environ.get("JWT_ISSUER", "http://localhost:8088/application/o/smart-city/")
    base = os.environ.get("AUTHENTIK_PUBLIC_URL", "http://localhost:8088").rstrip("/")
    with httpx.Client(timeout=20, trust_env=False, follow_redirects=False) as client:
        discovery = client.get(issuer + ".well-known/openid-configuration"); discovery.raise_for_status()
        config = discovery.json()
        check(config["issuer"] == issuer and config["jwks_uri"].startswith(base + "/"), "Discovery issuer/JWKS mismatch")
        keys = jwt.PyJWKClient(config["jwks_uri"])
        tokens = {service: identity(service) for service in services}
        for service in services:
            port, path, query = CASES[service]
            token = tokens[service].get_token()
            check(tokens[service].get_token() == token, f"{service}: token cache did not reuse token")
            claims = jwt.decode(token, keys.get_signing_key_from_jwt(token).key, algorithms=["RS256"],
                issuer=issuer, audience=os.getenv("JWT_AUDIENCE", "smart-city-api"),
                leeway=int(os.getenv("JWT_LEEWAY_SECONDS", "0")),
                options={"require": ["iss", "aud", "exp", "sub", "scope"]})
            check("service" in claims.get("roles", []) and f"{service}:read" in claims.get("permissions", []), f"{service}: provision exact read permission")
            check(not ({"user", "administrator", "moderator"} & set(claims.get("roles", []))), f"{service}: excessive roles")
            headers = {"Authorization": "Bearer " + token}
            record = {}
            for prefix, url in [("direct", f"http://localhost:{port}"), ("gateway", f"http://localhost:8091/{service}")]:
                check(client.get(url + "/health").status_code == 200, f"{service} {prefix}: public health failed")
                ready = client.get(url + "/ready")
                check(ready.status_code in {200, 503}, f"{service} {prefix}: readiness unexpectedly inaccessible")
                check(client.get(url + path, params=query).status_code == 401, f"{service} {prefix}: missing token not rejected")
                check(client.get(url + path, params=query, headers={"Authorization": "Bearer invalid"}).status_code == 401, f"{service} {prefix}: invalid token not rejected")
                parts = token.split("."); sig = parts[2]; parts[2] = ("A" if sig[0] != "A" else "B") + sig[1:]
                check(client.get(url + path, params=query, headers={"Authorization": "Bearer " + ".".join(parts)}).status_code == 401, f"{service} {prefix}: tampered signature not rejected")
                valid = client.get(url + path, params=query, headers=headers)
                check(valid.status_code == 200, f"{service} {prefix}: expected 200, got {valid.status_code}; check data availability and permissions")
                record[prefix] = {"health": 200, "ready": ready.status_code, "missing_token": 401, "malformed": 401, "invalid_signature": 401, "valid": 200}
            if service == "s4":
                estimate = client.post(f"http://localhost:{port}/v1/parking/occupancy/estimate", json={"observations": []}, headers=headers)
                check(estimate.status_code == 403, "S4 reader must not have estimator permission")
                if os.getenv("S4_ESTIMATOR_AUTHENTIK_SERVICE_USERNAME"):
                    estimator = identity("s4_estimator")
                    result = client.post(f"http://localhost:{port}/v1/parking/occupancy/estimate", json={"observations": []}, headers=estimator.headers())
                    check(result.status_code == 200, "S4 estimator failed")
                    record["estimator"] = 200
                record["reader_estimator_denial"] = 403
            if wait_expiry:
                time.sleep(max(0, claims["exp"] - time.time() + 1))
                check(client.get(f"http://localhost:{port}" + path, params=query, headers=headers).status_code == 401, f"{service}: expired token accepted")
                check(tokens[service].get_token() != token, f"{service}: expired token was reused")
                record["expired_token"] = 401
            report["services"][service] = record
        # A token scoped solely to one service must not grant access to another.
        if len(services) > 1:
            for index, service in enumerate(services):
                other = services[(index + 1) % len(services)]
                port, path, query = CASES[other]
                check(client.get(f"http://localhost:{port}" + path, params=query, headers=tokens[service].headers()).status_code == 403, f"{service}: unauthorized access to {other}")
            report["cross_service_denial"] = True
        spec = client.get("http://localhost:8091/openapi.json"); spec.raise_for_status()
        scheme = spec.json()["components"]["securitySchemes"]["SmartCityOAuth"]["flows"]["authorizationCode"]
        check(scheme["authorizationUrl"] == base + "/application/o/authorize/" and scheme["tokenUrl"] == base + "/application/o/token/", "Swagger OAuth endpoint mismatch")
        for service in services:
            check("/" + service + CASES[service][1] in spec.json()["paths"], f"{service}: missing gateway schema")
        report["swagger_configuration"] = "passed (interactive login is manual)"
    if output:
        Path(output).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", action="append", choices=CASES)
    parser.add_argument("--output")
    parser.add_argument("--wait-expiry", action="store_true", help="Wait for real access-token expiry for each tested service")
    args = parser.parse_args()
    verify(args.service, args.output, args.wait_expiry)
