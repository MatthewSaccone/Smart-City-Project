"""Ephemeral RSA/JWKS identity for isolated tests and local benchmark processes.

This helper is never used by the application to issue or bypass credentials.
"""

from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt


class EphemeralIdentity:
    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.available = True
        self.requests = 0
        authority = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                authority.requests += 1
                self.send_response(200 if authority.available else 503)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                public = jwt.algorithms.RSAAlgorithm.to_jwk(authority.key.public_key(), as_dict=True)
                self.wfile.write(json.dumps({"keys": [{**public, "kid": "ephemeral-key", "alg": "RS256", "use": "sig"}]}).encode())

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.issuer = self.base_url + "/test-issuer"
        self.thread = Thread(target=lambda: self.server.serve_forever(poll_interval=.02), daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def environment(self):
        return {"JWT_ISSUER": self.issuer, "JWT_AUDIENCE": "smart-city-api",
                "JWT_JWKS_URL": self.base_url + "/jwks", "JWT_JWKS_TIMEOUT_SECONDS": "1",
                "JWT_ROLE_CLAIM": "roles", "JWT_LEEWAY_SECONDS": "0", "JWT_ALLOW_INSECURE_JWKS": "false"}

    def token(self, *, permissions=(), **overrides):
        now = datetime.now(timezone.utc)
        claims = {"iss": self.issuer, "aud": "smart-city-api", "sub": "service-account-test",
                  "iat": int(now.timestamp()), "exp": int((now + timedelta(minutes=10)).timestamp()),
                  "roles": ["user"], "permissions": list(permissions)}
        claims.update(overrides)
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "ephemeral-key"})
