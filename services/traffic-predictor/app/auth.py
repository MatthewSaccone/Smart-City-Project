"""S2 resource-server authentication, aligned with S3's Keycloak setup.

S2 validates access tokens; it never issues them or trusts caller identity
headers. No local token bypass is provided.
"""

from dataclasses import dataclass
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
    leeway_seconds: int = 30
    jwks_timeout_seconds: int = 5

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
            leeway_seconds=int(os.getenv("JWT_LEEWAY_SECONDS", "30")),
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
            development_keycloak = (
                parsed.scheme == "http" and parsed.hostname == "keycloak" and parsed.port == 8080
                and insecure == "true" and os.getenv("APP_ENV", "production").strip().lower() == "development"
            )
            if parsed.scheme != "https" and not (local_http or development_keycloak):
                raise ValueError("JWT_JWKS_URL requires HTTPS, localhost HTTP, or explicitly enabled development Keycloak")
        return settings


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
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except PyJWKClientConnectionError as exc:
            raise IdentityProviderUnavailable() from exc
        except (InvalidTokenError, PyJWKClientError, TypeError, ValueError) as exc:
            raise InvalidAccessToken() from exc
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            raise InvalidAccessToken()
        return Principal(
            subject=subject,
            roles=_string_set(claims.get(self.settings.role_claim, [])),
        )


def authenticate(request: Request, credentials: HTTPAuthorizationCredentials | None) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(401, "A valid bearer access token is required", headers={"WWW-Authenticate": "Bearer"})
    verifier = request.app.state.token_verifier
    try:
        return verifier.verify_token(credentials.credentials)
    except IdentityProviderUnavailable:
        raise HTTPException(503, "Trusted identity verification is temporarily unavailable") from None
    except InvalidAccessToken:
        raise HTTPException(401, "The bearer access token is invalid or expired",
                            headers={"WWW-Authenticate": "Bearer"}) from None


def get_current_principal(request: Request,
                          credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)) -> Principal:
    # A synchronous dependency keeps JWKS retrieval off the ASGI event loop.
    return authenticate(request, credentials)
