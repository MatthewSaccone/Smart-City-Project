"""Isolated S2 configuration and a real, ephemeral test signing-key server."""

from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from threading import Thread

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
import pytest

from app import main


ISSUER = "https://identity.example.test/realms/smart-city"
AUDIENCE = "smart-city-api"


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch):
    monkeypatch.setattr(main, "REGISTRY_URL", None)
    for name in tuple(os.environ):
        if name.startswith(("S2_CACHE_", "S2_REFRESH_", "S2_RETRY_")):
            monkeypatch.delenv(name, raising=False)
    for name in ("JWT_ISSUER", "JWT_AUDIENCE", "JWT_JWKS_URL", "JWT_ROLE_CLAIM",
                 "JWT_LEEWAY_SECONDS", "JWT_JWKS_TIMEOUT_SECONDS", "JWT_ALLOW_INSECURE_JWKS",
                 "S2_DATABASE_URL", "S2_HISTORY_CSV", "S2_HISTORY_METADATA", "S2_MODEL_VERSION"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(scope="session")
def signing_keys():
    return [rsa.generate_private_key(public_exponent=65537, key_size=2048) for _ in range(2)]


@pytest.fixture
def make_token(signing_keys):
    def issue(*, key_index=0, omit=(), algorithm="RS256", kid="test-key-0", **overrides):
        now = datetime.now(timezone.utc)
        claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": "service-account-test-client",
                  "iat": int(now.timestamp()), "exp": int((now + timedelta(minutes=5)).timestamp()),
                  "roles": ["user"]}
        claims.update(overrides)
        for name in omit:
            claims.pop(name, None)
        key = signing_keys[key_index] if algorithm == "RS256" else "test-secret-at-least-thirty-two-bytes"
        return jwt.encode(claims, key, algorithm=algorithm, headers={"kid": kid})
    return issue


@pytest.fixture
def jwt_configuration(monkeypatch, signing_keys):
    state = {"requests": 0, "unavailable": False, "key_indexes": [0]}

    class JWKSHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["requests"] += 1
            self.send_response(503 if state["unavailable"] else 200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            keys = []
            for index in state["key_indexes"]:
                key = jwt.algorithms.RSAAlgorithm.to_jwk(signing_keys[index].public_key(), as_dict=True)
                keys.append({**key, "kid": f"test-key-{index}", "use": "sig", "alg": "RS256"})
            self.wfile.write(json.dumps({"keys": keys}).encode())

        def log_message(self, *args):
            pass  # No access tokens or request headers in test logs.

    server = ThreadingHTTPServer(("127.0.0.1", 0), JWKSHandler)
    thread = Thread(target=lambda: server.serve_forever(poll_interval=.02), daemon=True)
    thread.start()
    monkeypatch.setenv("JWT_ISSUER", ISSUER)
    monkeypatch.setenv("JWT_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("JWT_JWKS_URL", f"http://127.0.0.1:{server.server_port}/jwks")
    monkeypatch.setenv("JWT_JWKS_TIMEOUT_SECONDS", "1")
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def auth_headers(jwt_configuration, make_token):
    return {"Authorization": "Bearer " + make_token()}
