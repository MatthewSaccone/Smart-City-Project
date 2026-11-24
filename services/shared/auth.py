"""Shared Authentik resource-server authentication.

Services validate access tokens; they never issue them or trust caller identity
headers. No local token bypass is provided.
"""

from dataclasses import dataclass, replace
import os
from urllib.parse import urlparse

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from jwt.exceptions import InvalidTokenError, PyJWKClientConnectionError, PyJWKClientError


bearer_scheme = HTTPBearer(auto_error=False, scheme_name="BearerAuth")


class IdentityProviderUnavailable(Exception):
    """Trusted signing keys cannot currently be obtained."""


class InvalidAccessToken(Exception):
    """The supplied token cannot establish a trusted identity."""


@dataclass(frozen=True)
class JWTSettings:
    issuer: str | None
    audience: str | None
    jwks_url: str | None
    role_claim: str = "roles"
    leeway_seconds: int = 0
    jwks_timeout_seconds: int = 5
    moderator_role: str = "moderator"
    allow_insecure_jwks: bool = False

    @property
    def configured(self) -> bool:
        return bool(self.issuer and self.audience and self.jwks_url)

    @classmethod
    def from_env(cls):
        def optional(name):
            return os.getenv(name, "").strip() or None

        settings = cls(
            issuer=optional("JWT_ISSUER"), audience=optional("JWT_AUDIENCE"),
            jwks_url=optional("JWT_JWKS_URL"),
            role_claim=os.getenv("JWT_ROLE_CLAIM", "roles").strip(),
            moderator_role=os.getenv("JWT_MODERATOR_ROLE", "moderator").strip(),
            leeway_seconds=int(os.getenv("JWT_LEEWAY_SECONDS", "0")),
            jwks_timeout_seconds=int(os.getenv("JWT_JWKS_TIMEOUT_SECONDS", "5")),
        )
        if any((settings.issuer, settings.audience, settings.jwks_url)) and not settings.configured:
            raise ValueError("Configure JWT_ISSUER, JWT_AUDIENCE and JWT_JWKS_URL together")
        if not settings.role_claim:
            raise ValueError("JWT_ROLE_CLAIM cannot be empty")
        if not 0 <= settings.leeway_seconds <= 60:
            raise ValueError("JWT_LEEWAY_SECONDS must be between 0 and 60")
        if not 1 <= settings.jwks_timeout_seconds <= 10:
            raise ValueError("JWT_JWKS_TIMEOUT_SECONDS must be between 1 and 10")
        insecure = os.getenv("JWT_ALLOW_INSECURE_JWKS", "false").strip().lower()
        if insecure not in {"true", "false"}:
            raise ValueError("JWT_ALLOW_INSECURE_JWKS must be 'true' or 'false'")
        if settings.configured:
            parsed = urlparse(settings.jwks_url)
            if not parsed.netloc or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
                raise ValueError("JWT_JWKS_URL must be an absolute URL without credentials or fragment")
            local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
            development_authentik = (
                parsed.scheme == "http" and parsed.hostname == "authentik-server" and parsed.port == 9000
                and insecure == "true" and os.getenv("APP_ENV", "production").strip().lower() == "development"
            )
            if parsed.scheme != "https" and not (local_http or development_authentik):
                raise ValueError("JWT_JWKS_URL must use HTTPS, localhost HTTP, or explicitly enabled development Authentik")
        return replace(settings, allow_insecure_jwks=insecure == "true")


def _string_set(value):
    if isinstance(value, str):
        return frozenset({value})
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return frozenset(value)
    return frozenset()


@dataclass(frozen=True)
class Principal:
    subject: str
    roles: frozenset[str]
    permissions: frozenset[str] = frozenset()

    moderator_role: str = "moderator"

    @property
    def is_moderator(self):
        return "administrator" in self.roles or self.moderator_role in self.roles

    def has_permission(self, permission):
        return permission in self.permissions


class TokenVerifier:
    def __init__(self, settings: JWTSettings):
        self.settings = settings
        # Cache the JWKS for five minutes. Individual key caching has no TTL;
        # leaving it disabled lets removed keys expire with the JWKS cache.
        self._jwks_client = PyJWKClient(
            settings.jwks_url, cache_keys=False, cache_jwk_set=True, lifespan=300,
            timeout=settings.jwks_timeout_seconds,
        ) if settings.configured else None

    @property
    def configured(self):
        return self.settings.configured and self._jwks_client is not None

    def check_available(self):
        try:
            return self.configured and bool(self._jwks_client.get_signing_keys())
        except Exception:
            return False

    def verify_token(self, token: str) -> Principal:
        if not self.configured:
            raise IdentityProviderUnavailable()
        try:
            if len(token) > 16384:
                raise InvalidAccessToken()
            header = jwt.get_unverified_header(token)
            # Algorithms and key service come from trusted server settings,
            # never from unverified jku/x5u/token claims.
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str) or not header["kid"]:
                raise InvalidAccessToken()
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token, signing_key.key, algorithms=["RS256"],
                issuer=self.settings.issuer, audience=self.settings.audience,
                leeway=self.settings.leeway_seconds,
                options={"require": ["exp", "iat", "iss", "aud", "sub", "scope"]},
            )
        except PyJWKClientConnectionError as exc:
            raise IdentityProviderUnavailable() from exc
        except (InvalidTokenError, PyJWKClientError, TypeError, ValueError) as exc:
            raise InvalidAccessToken() from exc
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            raise InvalidAccessToken()
        scope = claims.get("scope")
        if not isinstance(scope, str) or "smart-city" not in scope.split():
            raise InvalidAccessToken()
        permissions = _string_set(claims.get("permissions", []))
        return Principal(
            subject=subject,
            roles=_string_set(claims.get(self.settings.role_claim, [])),
            permissions=permissions, moderator_role=self.settings.moderator_role,
        )


def authenticate(request: Request, credentials: HTTPAuthorizationCredentials | None) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(401, "A valid bearer access token is required", headers={"WWW-Authenticate": "Bearer"})
    cached = getattr(request.state, "verified_principal", None)
    if cached is not None:
        return cached
    verifier = getattr(request.app.state, "token_verifier", None)
    if verifier is None:
        raise HTTPException(503, "Trusted identity verification is unavailable")
    try:
        principal = verifier.verify_token(credentials.credentials)
        request.state.verified_principal = principal
        return principal
    except IdentityProviderUnavailable:
        raise HTTPException(503, "Trusted identity verification is temporarily unavailable") from None
    except InvalidAccessToken:
        raise HTTPException(401, "The bearer access token is invalid or expired",
                            headers={"WWW-Authenticate": "Bearer"}) from None


def get_current_principal(request: Request,
                          credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)) -> Principal:
    # A synchronous dependency keeps JWKS retrieval off the ASGI event loop.
    if request.url.path in {"/", "/health", "/ready"} and request.method in {"GET", "HEAD"}:
        return None
    return authenticate(request, credentials)


def require_moderator(principal: Principal = Depends(get_current_principal)):
    if not principal.is_moderator:
        raise HTTPException(403, "Moderator permission is required")
    return principal


def protect_service(service_id):
    """Default-deny every API route except explicitly public operations."""
    def authorize(request: Request, principal: Principal = Depends(get_current_principal)):
        if request.url.path in {"/", "/health", "/ready"} and request.method in {"GET", "HEAD"}:
            return
        path, method = request.url.path, request.method
        roles = principal.roles
        if "service" in roles:
            if roles & {"administrator", "user", "moderator", principal.moderator_role}:
                raise HTTPException(403, "Conflicting identity roles")
            action = "read" if method in {"GET", "HEAD"} else "write"
            if service_id == "s2" and path == "/v1/traffic/predictions" and method == "POST":
                action = "read"
            if service_id == "s4" and path == "/v1/parking/occupancy/estimate" and method == "POST":
                if "parking:estimate" in principal.permissions:
                    return
                raise HTTPException(403, "Estimator permission required")
            if f"{service_id}:{action}" not in principal.permissions:
                raise HTTPException(403, "Service permission required")
            return
        if service_id == "s4" and path == "/v1/parking/occupancy/estimate" and method == "POST":
            raise HTTPException(403, "Estimator service identity required")
        if "administrator" in roles:
            return
        if not (roles & {"user", "moderator"} or principal.is_moderator):
            raise HTTPException(403, "Application role required")
        if method in {"GET", "HEAD"} or (method == "POST" and (service_id, path) in {
            ("s1", "/v1/traffic/locations"), ("s2", "/v1/traffic/predictions"), ("s3", "/v1/incidents")
        }) or (service_id == "s3" and method in {"PATCH", "DELETE"} and path.startswith("/v1/incidents/")):
            return
        raise HTTPException(403, "Operation permission required")
    return Depends(authorize)


def configure_openapi(app):
    original = app.openapi
    def openapi():
        spec = original()
        for path, operations in spec.get("paths", {}).items():
            for method, operation in operations.items():
                if method not in {"get", "head", "post", "patch", "put", "delete", "options"}:
                    continue
                if path in {"/", "/health", "/ready"} and method in {"get", "head"}:
                    operation["security"] = []
                else:
                    operation["security"] = [{"BearerAuth": []}]
                    responses = operation.setdefault("responses", {})
                    for code, desc in (("401", "Missing or invalid access token"), ("403", "Insufficient permissions"), ("503", "Identity verification or service unavailable")):
                        responses.setdefault(code, {"description": desc})
        return spec
    app.openapi = openapi
