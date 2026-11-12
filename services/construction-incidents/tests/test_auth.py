
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.exceptions import PyJWKClientConnectionError

import app.auth as auth


@pytest.fixture
def rsa_keys():
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    return private_key, private_key.public_key()


@pytest.fixture
def settings():
    return auth.JWTSettings(
        issuer="https://identity.example.test/",
        audience="smart-city-api",
        jwks_url="https://identity.example.test/.well-known/jwks.json",
        role_claim="roles",
        moderator_role="s3_moderator",
    )


@pytest.fixture
def verifier(monkeypatch, settings, rsa_keys):
    private_key, public_key = rsa_keys

    class FakeJWKClient:
        def __init__(self, url, **kwargs):
            self.url = url

        def get_signing_key_from_jwt(self, token):
            return SimpleNamespace(key=public_key)

        def get_signing_keys(self):
            return [SimpleNamespace(key=public_key)]

    monkeypatch.setattr(auth, "PyJWKClient", FakeJWKClient)

    return auth.TokenVerifier(settings), private_key


def make_token(private_key, **overrides):
    now = datetime.now(timezone.utc)

    claims = {
        "iss": "https://identity.example.test/",
        "aud": "smart-city-api",
        "sub": "user-123",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "roles": ["user"],
    }

    claims.update(overrides)

    return jwt.encode(
        claims,
        private_key,
        algorithm="RS256",
        headers={"kid": "test-signing-key"},
    )


def test_valid_token_returns_verified_principal(verifier):
    token_verifier, private_key = verifier
    token = make_token(private_key)

    principal = token_verifier.verify_token(token)

    assert principal.subject == "user-123"
    assert principal.roles == frozenset({"user"})
    assert principal.is_moderator is False


def test_moderator_role_is_read_from_verified_claim(verifier):
    token_verifier, private_key = verifier
    token = make_token(
        private_key,
        roles=["user", "s3_moderator"],
    )

    principal = token_verifier.verify_token(token)

    assert principal.subject == "user-123"
    assert principal.is_moderator is True


def test_missing_subject_is_rejected(verifier):
    token_verifier, private_key = verifier
    token = make_token(private_key, sub="")

    with pytest.raises(auth.InvalidAccessToken):
        token_verifier.verify_token(token)


def test_wrong_issuer_is_rejected(verifier):
    token_verifier, private_key = verifier
    token = make_token(
        private_key,
        iss="https://untrusted.example.test/",
    )

    with pytest.raises(auth.InvalidAccessToken):
        token_verifier.verify_token(token)


def test_wrong_audience_is_rejected(verifier):
    token_verifier, private_key = verifier
    token = make_token(
        private_key,
        aud="another-api",
    )

    with pytest.raises(auth.InvalidAccessToken):
        token_verifier.verify_token(token)


def test_expired_token_is_rejected(verifier):
    token_verifier, private_key = verifier
    now = datetime.now(timezone.utc)

    token = make_token(
        private_key,
        iat=int((now - timedelta(minutes=10)).timestamp()),
        exp=int((now - timedelta(minutes=5)).timestamp()),
    )

    with pytest.raises(auth.InvalidAccessToken):
        token_verifier.verify_token(token)


def test_non_rs256_token_is_rejected(verifier):
    token_verifier, _ = verifier

    token = jwt.encode(
        {
            "iss": "https://identity.example.test/",
            "aud": "smart-city-api",
            "sub": "user-123",
            "iat": int(datetime.now(timezone.utc).timestamp()),
            "exp": int(
                (datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp()
            ),
        },
        "not-a-trusted-rsa-key",
        algorithm="HS256",
    )

    with pytest.raises(auth.InvalidAccessToken):
        token_verifier.verify_token(token)


def test_unconfigured_verifier_fails_closed():
    settings = auth.JWTSettings(
        issuer=None,
        audience=None,
        jwks_url=None,
    )

    verifier = auth.TokenVerifier(settings)

    assert verifier.configured is False
    assert verifier.check_available() is False

    with pytest.raises(auth.IdentityProviderUnavailable):
        verifier.verify_token("some-token")


def test_jwks_connection_failure_fails_closed(
    monkeypatch,
    settings,
):
    class UnavailableJWKClient:
        def __init__(self, url, **kwargs):
            pass

        def get_signing_key_from_jwt(self, token):
            raise PyJWKClientConnectionError("simulated connection failure")

        def get_signing_keys(self):
            raise PyJWKClientConnectionError("simulated connection failure")

    monkeypatch.setattr(
        auth,
        "PyJWKClient",
        UnavailableJWKClient,
    )

    verifier = auth.TokenVerifier(settings)

    assert verifier.check_available() is False

    with pytest.raises(auth.IdentityProviderUnavailable):
        verifier.verify_token("some-token")


def test_moderator_dependency_rejects_regular_user():
    principal = auth.Principal(
        subject="user-123",
        roles=frozenset({"user"}),
        moderator_role="s3_moderator",
    )

    with pytest.raises(Exception) as exc:
        auth.require_moderator(principal)

    assert getattr(exc.value, "status_code", None) == 403


def test_moderator_dependency_accepts_moderator():
    principal = auth.Principal(
        subject="moderator-123",
        roles=frozenset({"user", "s3_moderator"}),
        moderator_role="s3_moderator",
    )

    assert auth.require_moderator(principal) is principal


def test_keycloak_http_jwks_rejected_without_explicit_development_opt_in(
    monkeypatch,
):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("JWT_ISSUER", "http://localhost:8088/realms/smart-city")
    monkeypatch.setenv("JWT_AUDIENCE", "smart-city-api")
    monkeypatch.setenv(
        "JWT_JWKS_URL",
        "http://keycloak:8080/realms/smart-city/protocol/openid-connect/certs",
    )
    monkeypatch.setenv("JWT_ALLOW_INSECURE_JWKS", "false")

    with pytest.raises(ValueError, match="must use HTTPS"):
        auth.JWTSettings.from_env()


def test_keycloak_http_jwks_allowed_with_explicit_development_opt_in(
    monkeypatch,
):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("JWT_ISSUER", "http://localhost:8088/realms/smart-city")
    monkeypatch.setenv("JWT_AUDIENCE", "smart-city-api")
    monkeypatch.setenv(
        "JWT_JWKS_URL",
        "http://keycloak:8080/realms/smart-city/protocol/openid-connect/certs",
    )
    monkeypatch.setenv("JWT_ALLOW_INSECURE_JWKS", "true")

    settings = auth.JWTSettings.from_env()

    assert settings.allow_insecure_jwks is True
    assert settings.jwks_url.startswith("http://keycloak:8080/")


def test_keycloak_http_jwks_rejected_outside_development_even_with_opt_in(
    monkeypatch,
):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_ISSUER", "http://localhost:8088/realms/smart-city")
    monkeypatch.setenv("JWT_AUDIENCE", "smart-city-api")
    monkeypatch.setenv(
        "JWT_JWKS_URL",
        "http://keycloak:8080/realms/smart-city/protocol/openid-connect/certs",
    )
    monkeypatch.setenv("JWT_ALLOW_INSECURE_JWKS", "true")

    with pytest.raises(ValueError, match="must use HTTPS"):
        auth.JWTSettings.from_env()


def test_keycloak_http_jwks_rejected_when_app_env_is_unset(
    monkeypatch,
):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("JWT_ISSUER", "http://localhost:8088/realms/smart-city")
    monkeypatch.setenv("JWT_AUDIENCE", "smart-city-api")
    monkeypatch.setenv(
        "JWT_JWKS_URL",
        "http://keycloak:8080/realms/smart-city/protocol/openid-connect/certs",
    )
    monkeypatch.setenv("JWT_ALLOW_INSECURE_JWKS", "true")

    with pytest.raises(ValueError, match="must use HTTPS"):
        auth.JWTSettings.from_env()
