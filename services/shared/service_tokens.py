"""Cached Authentik app-password credentials for trusted machine callers.

Create one instance per identity and reuse it for the lifetime of the process.
No credentials are read at import time; no tokens or secrets are logged.
"""
import math
import os
import threading
import time
from urllib.parse import urlsplit

import httpx


class ServiceTokenError(RuntimeError):
    pass


class ServiceTokenClient:
    def __init__(self, token_url, client_id, username, app_password, *, transport=None, clock=time.monotonic, issuer=None):
        url = urlsplit(token_url)
        local = os.getenv("APP_ENV") == "development" and url.hostname in {
            "localhost", "127.0.0.1", "authentik-server"}
        if (not url.hostname or url.username or url.password or url.fragment or url.query
                or (url.scheme != "https" and not (url.scheme == "http" and local))):
            raise ValueError("Machine token endpoint must use HTTPS (explicit development HTTP allowed)")
        if not all((client_id, username, app_password)):
            raise ValueError("Machine identity credentials are required")
        self.url, self.client_id, self.username = token_url, client_id, username
        self._headers = {}
        if url.scheme == "http" and url.hostname == "authentik-server":
            # The application-specific issuer is derived from the request host.
            # Preserve the canonical development origin when calling internal DNS.
            canonical = urlsplit(issuer or "")
            if canonical.scheme != "http" or canonical.hostname not in {"localhost", "127.0.0.1"} or canonical.username or canonical.password:
                raise ValueError("Internal development token requests require the canonical local JWT_ISSUER")
            self._headers["Host"] = canonical.netloc
        self._password = app_password
        self._transport, self._clock = transport, clock
        self._lock = threading.Lock()
        self._token, self._refresh_at = None, 0

    @classmethod
    def from_env(cls):
        return cls(os.environ["AUTHENTIK_TOKEN_URL"], os.getenv("JWT_AUDIENCE", "smart-city-api"),
                   os.environ["AUTHENTIK_SERVICE_USERNAME"], os.environ["AUTHENTIK_SERVICE_APP_PASSWORD"],
                   issuer=os.getenv("JWT_ISSUER"))

    def get_token(self):
        with self._lock:
            now = self._clock()
            if self._token and now < self._refresh_at:
                return self._token
            try:
                with httpx.Client(timeout=5, follow_redirects=False, trust_env=False, transport=self._transport) as client:
                    response = client.post(self.url, headers=self._headers, data={"grant_type": "client_credentials",
                        "client_id": self.client_id, "username": self.username,
                        "password": self._password, "scope": "smart-city"})
                    response.raise_for_status()
                    data = response.json()
                token = data["access_token"]
                ttl = float(data["expires_in"])
                if (not isinstance(token, str) or not token or not math.isfinite(ttl) or ttl <= 0
                        or data.get("token_type", "").lower() != "bearer"):
                    raise ValueError("Invalid token response")
                self._token = token
                self._refresh_at = now + ttl - min(30, ttl / 5)
                return token
            except (httpx.HTTPError, KeyError, TypeError, ValueError, AttributeError):
                raise ServiceTokenError("Unable to acquire machine access token") from None

    def headers(self):
        return {"Authorization": "Bearer " + self.get_token()}


class MachineAuth(httpx.Auth):
    """Attach a cached access token to a synchronous HTTPX client."""
    def __init__(self, tokens):
        self.tokens = tokens

    def auth_flow(self, request):
        request.headers.update(self.tokens.headers())
        yield request
