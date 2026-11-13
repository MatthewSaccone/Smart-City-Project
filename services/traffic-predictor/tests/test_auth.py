"""JWT trust, HTTP enforcement, key rotation and failure behavior using real RSA/JWKS."""

from pathlib import Path
from types import SimpleNamespace
import time

from fastapi.testclient import TestClient
import pytest
import yaml

from app import auth, main
from app.routes import get_backend, get_history_store


ANALYTICS = [
    ("GET", "/v1/traffic/history/patterns"),
    ("POST", "/v1/traffic/predictions"),
    ("GET", "/v1/traffic/predictions/high-congestion-periods"),
    ("GET", "/v1/traffic/predictions/areas"),
    ("GET", "/v1/traffic/ingestion/status"),
]


@pytest.fixture
def client(jwt_configuration):
    with TestClient(main.app) as client:
        yield client


@pytest.fixture
def verifier(jwt_configuration):
    return auth.TokenVerifier(auth.JWTSettings.from_env())


def test_valid_signed_service_identity_and_user_identity(verifier, make_token):
    assert verifier.verify_token(make_token()).subject == "service-account-test-client"
    principal = verifier.verify_token(make_token(sub="end-user-123", roles=["user", "moderator"]))
    assert principal.subject == "end-user-123"
    assert principal.roles == frozenset({"user", "moderator"})


@pytest.mark.parametrize("claim", ["exp", "iat", "iss", "aud", "sub"])
def test_required_claims(verifier, make_token, claim):
    with pytest.raises(auth.InvalidAccessToken):
        verifier.verify_token(make_token(omit=[claim]))


@pytest.mark.parametrize("overrides", [
    {"iss": "https://untrusted.example.test/"}, {"aud": "other-api"},
    {"sub": ""}, {"sub": "  "}, {"sub": 123},
    {"exp": 0}, {"exp": None}, {"iat": None},
    {"iat": int(time.time()) + 3600}, {"nbf": int(time.time()) + 3600},
])
def test_invalid_claims(verifier, make_token, overrides):
    with pytest.raises(auth.InvalidAccessToken):
        verifier.verify_token(make_token(**overrides))


def test_forged_signature_rejected(verifier, make_token):
    with pytest.raises(auth.InvalidAccessToken):
        verifier.verify_token(make_token(key_index=1))


@pytest.mark.parametrize("token", ["not-a-jwt", "a.b.c", "x" * 16385])
def test_malformed_token_does_not_fetch_signing_keys(verifier, jwt_configuration, token):
    with pytest.raises(auth.InvalidAccessToken):
        verifier.verify_token(token)
    assert jwt_configuration["requests"] == 0


def test_algorithm_is_fixed_before_key_lookup(verifier, make_token, jwt_configuration):
    with pytest.raises(auth.InvalidAccessToken):
        verifier.verify_token(make_token(algorithm="HS256"))
    assert jwt_configuration["requests"] == 0


def test_unknown_key_is_rejected(verifier, make_token):
    with pytest.raises(auth.InvalidAccessToken):
        verifier.verify_token(make_token(kid="untrusted-key"))


def test_cached_keys_and_rotation(verifier, jwt_configuration, make_token):
    verifier.verify_token(make_token())
    assert jwt_configuration["requests"] == 1
    verifier.verify_token(make_token(sub="other-service"))
    assert jwt_configuration["requests"] == 1
    jwt_configuration["key_indexes"] = [1]
    # Expire the set deterministically, without waiting five minutes or relying
    # on unknown-kid refresh cooldown differences between PyJWT versions.
    verifier._jwks_client.jwk_set_cache.put(None)
    assert verifier.verify_token(make_token(key_index=1, kid="test-key-1"))
    with pytest.raises(auth.InvalidAccessToken):
        verifier.verify_token(make_token())


def test_outage_uses_only_unexpired_cached_keys(verifier, jwt_configuration, make_token):
    verifier.verify_token(make_token())
    jwt_configuration["unavailable"] = True
    assert verifier.verify_token(make_token())
    verifier._jwks_client.jwk_set_cache.put(None)
    with pytest.raises(auth.IdentityProviderUnavailable):
        verifier.verify_token(make_token())


@pytest.mark.parametrize("method,path", ANALYTICS)
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Basic untrusted"},
                                      {"X-Registry-API-Key": "registry-only"},
                                      {"X-User-ID": "spoofed", "X-Roles": "moderator"}])
def test_all_analytics_require_auth_before_data_access(client, method, path, headers):
    def forbidden_backend():
        raise AssertionError("Unauthenticated request reached the data backend")
    previous = dict(main.app.dependency_overrides)
    main.app.dependency_overrides[get_backend] = forbidden_backend
    main.app.dependency_overrides[get_history_store] = forbidden_backend
    try:
        response = client.request(method, path, headers={**headers, "X-Request-ID": "auth-check"})
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.headers["X-Request-ID"] == "auth-check"
    assert response.json()["error"] == {
        "code": "AUTHENTICATION_REQUIRED", "message": "A valid bearer access token is required",
        "request_id": "auth-check", "details": [], "retryable": False, "source": "service",
    }


@pytest.mark.parametrize("overrides", [{"aud": "wrong"}, {"iss": "wrong"}, {"exp": 0}])
def test_invalid_token_http_response_is_sanitized(client, make_token, overrides):
    token = make_token(**overrides)
    response = client.get(ANALYTICS[0][1], headers={"Authorization": "Bearer " + token})
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert token not in response.text
    assert "identity.example.test" not in response.text


def test_unconfigured_identity_fails_closed_and_probes_remain_public(make_token):
    with TestClient(main.app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 503  # No data/model, independently of auth.
        assert client.get(ANALYTICS[0][1]).status_code == 401
        result = client.get(ANALYTICS[0][1], headers={"Authorization": "Bearer " + make_token()})
        assert result.status_code == 503
        assert result.json()["error"]["retryable"] is True


def test_identity_outage_returns_503_not_anonymous_access(client, jwt_configuration, make_token):
    jwt_configuration["unavailable"] = True
    response = client.get(ANALYTICS[0][1], headers={"Authorization": "Bearer " + make_token()})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert response.json()["error"]["retryable"] is True
    assert client.get("/health").status_code == 200


def test_authenticated_requests_keep_existing_validation_and_readiness(client, auth_headers):
    assert client.get(ANALYTICS[0][1], headers=auth_headers).status_code == 503
    # Supplying a ready backend lets normal request validation execute.
    previous = dict(main.app.dependency_overrides)
    main.app.dependency_overrides[get_backend] = lambda: SimpleNamespace()
    try:
        assert client.post(ANALYTICS[1][1], headers=auth_headers, json={}).status_code == 422
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)


def test_runtime_and_published_security_agree(client):
    published = yaml.safe_load((Path(__file__).resolve().parents[3] /
                               "shared/api-contracts/s2-traffic-predictor.openapi.yaml").read_text(encoding="utf-8"))
    runtime = client.get("/openapi.json").json()
    for method, path in ANALYTICS:
        assert runtime["paths"][path][method.lower()]["security"] == [{"BearerAuth": []}]
        operation = published["paths"][path][method.lower()]
        assert operation["security"] == [{"BearerAuth": []}]
        assert "401" in operation["responses"]
    for path in ("/health", "/ready"):
        assert not runtime["paths"][path]["get"].get("security")
        assert published["paths"][path]["get"]["security"] == []


@pytest.mark.parametrize("values", [
    {"JWT_JWKS_URL": "http://untrusted.example.test/keys"},
    {"JWT_JWKS_URL": "file:///keys.json"}, {"JWT_JWKS_URL": "https://user:pass@example.test/keys"},
    {"JWT_LEEWAY_SECONDS": "61"}, {"JWT_JWKS_TIMEOUT_SECONDS": "0"},
    {"JWT_ROLE_CLAIM": " "}, {"JWT_ALLOW_INSECURE_JWKS": "yes"}, {"JWT_AUDIENCE": ""},
])
def test_unsafe_or_incomplete_configuration_rejected(jwt_configuration, monkeypatch, values):
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        auth.JWTSettings.from_env()


def test_development_keycloak_http_requires_both_flags(jwt_configuration, monkeypatch):
    monkeypatch.setenv("JWT_JWKS_URL", "http://keycloak:8080/realms/smart-city/protocol/openid-connect/certs")
    monkeypatch.setenv("JWT_ALLOW_INSECURE_JWKS", "true")
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(ValueError):
        auth.JWTSettings.from_env()
    monkeypatch.setenv("APP_ENV", "development")
    assert auth.JWTSettings.from_env().configured
