
"""End-user JWT authentication for the Construction & Incidents service.

S3 is a resource server. It does not issue tokens or establish user identities.
Only claims from a successfully verified JWT are used for authorization.
"""

from dataclasses import dataclass
from urllib.parse import urlparse
import os

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from jwt.exceptions import (
    InvalidTokenError,
    PyJWKClientConnectionError,
    PyJWKClientError,
)


bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="bearerAuth",
)


class IdentityProviderUnavailable(Exception):
    """The configured trusted signing-key service cannot be reached."""


class InvalidAccessToken(Exception):
    """The supplied token cannot be accepted as a trusted identity."""


@dataclass(frozen=True)
class JWTSettings:
    issuer: str | None
    audience: str | None
    jwks_url: str | None
    role_claim: str = "roles"
    moderator_role: str = "moderator"
    leeway_seconds: int = 30
    jwks_timeout_seconds: int = 5

    @property
    def configured(self) -> bool:
        return all(
            value and value.strip()
            for value in (self.issuer, self.audience, self.jwks_url)
        )

    @classmethod
    def from_env(cls) -> "JWTSettings":
        settings = cls(
            issuer=os.getenv("JWT_ISSUER") or None,
            audience=os.getenv("JWT_AUDIENCE") or None,
            jwks_url=os.getenv("JWT_JWKS_URL") or None,
            role_claim=os.getenv("JWT_ROLE_CLAIM", "roles"),
            moderator_role=os.getenv("JWT_MODERATOR_ROLE", "moderator"),
            leeway_seconds=int(os.getenv("JWT_LEEWAY_SECONDS", "30")),
            jwks_timeout_seconds=int(
                os.getenv("JWT_JWKS_TIMEOUT_SECONDS", "5")
            ),
        )

        if not 0 <= settings.leeway_seconds <= 60:
            raise ValueError("JWT_LEEWAY_SECONDS must be between 0 and 60")

        if not 1 <= settings.jwks_timeout_seconds <= 10:
            raise ValueError(
                "JWT_JWKS_TIMEOUT_SECONDS must be between 1 and 10"
            )

        if not settings.role_claim.strip():
            raise ValueError("JWT_ROLE_CLAIM cannot be empty")

        if not settings.moderator_role.strip():
            raise ValueError("JWT_MODERATOR_ROLE cannot be empty")

        if settings.configured:
            parsed = urlparse(settings.jwks_url)

            is_local_http = (
                parsed.scheme == "http"
                and parsed.hostname in {"localhost", "127.0.0.1"}
            )

            if parsed.scheme != "https" and not is_local_http:
                raise ValueError(
                    "JWT_JWKS_URL must use HTTPS "
                    "(HTTP is allowed only for localhost)"
                )

            if not parsed.netloc:
                raise ValueError("JWT_JWKS_URL must be an absolute URL")

        return settings


@dataclass(frozen=True)
class Principal:
    """Identity and roles extracted only from a verified access token."""

    subject: str
    roles: frozenset[str]
    moderator_role: str = "moderator"

    @property
    def is_moderator(self) -> bool:
        return self.moderator_role in self.roles


class TokenVerifier:
    """Verifies RS256 access tokens against a configured JWKS endpoint."""

    def __init__(self, settings: JWTSettings):
        self.settings = settings
        self._jwks_client = None

        if settings.configured:
            self._jwks_client = PyJWKClient(
                settings.jwks_url,
                cache_keys=True,
                max_cached_keys=16,
                cache_jwk_set=True,
                lifespan=300,
                timeout=settings.jwks_timeout_seconds,
            )

    @property
    def configured(self) -> bool:
        return self.settings.configured and self._jwks_client is not None

    def check_available(self) -> bool:
        """Check whether trusted signing keys can currently be obtained."""

        if not self.configured:
            return False

        try:
            return bool(self._jwks_client.get_signing_keys())
        except Exception:
            # Do not expose provider responses, URLs, or credentials.
            return False

    def verify_token(self, token: str) -> Principal:
        if not self.configured:
            raise IdentityProviderUnavailable(
                "Trusted token verification is not configured"
            )

        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)

        except PyJWKClientConnectionError as exc:
            raise IdentityProviderUnavailable(
                "Trusted signing keys are unavailable"
            ) from exc

        except InvalidTokenError as exc:
            raise InvalidAccessToken(
                "The bearer access token is malformed"
            ) from exc

        except PyJWKClientError as exc:
            raise InvalidAccessToken(
                "No trusted signing key matched"
            ) from exc

        try:
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                issuer=self.settings.issuer,
                audience=self.settings.audience,
                leeway=self.settings.leeway_seconds,
                options={
                    "require": [
                        "exp",
                        "iat",
                        "iss",
                        "aud",
                        "sub",
                    ],
                },
            )

        except InvalidTokenError as exc:
            raise InvalidAccessToken("Access token validation failed") from exc

        subject = claims.get("sub")

        if not isinstance(subject, str) or not subject.strip():
            raise InvalidAccessToken("Access token subject is invalid")

        raw_roles = claims.get(self.settings.role_claim, [])

        if isinstance(raw_roles, str):
            roles = frozenset({raw_roles})
        elif (
            isinstance(raw_roles, list)
            and all(isinstance(role, str) for role in raw_roles)
        ):
            roles = frozenset(raw_roles)
        else:
            # A malformed role claim must never grant elevated permissions.
            roles = frozenset()

        
        return Principal(
            subject=subject,
            roles=roles,
            moderator_role=self.settings.moderator_role,
        )


def get_current_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(
        bearer_scheme
    ),
) -> Principal:
    """FastAPI dependency for endpoints requiring an authenticated user."""

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="A valid bearer access token is required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    verifier: TokenVerifier = request.app.state.token_verifier

    if not verifier.configured:
        raise HTTPException(
            status_code=503,
            detail="Trusted token verification is not configured",
        )

    try:
        return verifier.verify_token(credentials.credentials)

    except IdentityProviderUnavailable:
        raise HTTPException(
            status_code=503,
            detail="Trusted identity verification is temporarily unavailable",
        )

    except InvalidAccessToken:
        raise HTTPException(
            status_code=401,
            detail="The bearer access token is invalid or expired",
            headers={"WWW-Authenticate": "Bearer"},
        )


def require_moderator(
    principal: Principal = Depends(get_current_principal),
) -> Principal:
    """FastAPI dependency for endpoints restricted to moderators."""

    if not principal.is_moderator:
        raise HTTPException(
            status_code=403,
            detail="Moderator permission is required",
        )

    return principal
