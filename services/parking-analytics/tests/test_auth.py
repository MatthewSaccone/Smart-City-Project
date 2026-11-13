"""S4 JWT access and estimator permission tests against a real local RSA/JWKS issuer."""

from fastapi.testclient import TestClient
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
import pytest

from app import main
from test_parking import BOUNDS


@pytest.mark.parametrize("method,path", [
    ("GET", "/v1/parking/facilities"), ("GET", "/v1/parking/facilities/demo-central"),
    ("GET", "/v1/parking/ingestion/status"), ("POST", "/v1/parking/occupancy/estimate")])
@pytest.mark.parametrize("headers", [{}, {"X-Registry-API-Key": "registry"}, {"X-User-ID": "spoofed"}])
def test_all_parking_operations_require_verified_identity(method, path, headers):
    with TestClient(main.app) as anonymous:
        response = anonymous.request(method, path, headers=headers)
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
        assert response.headers["X-Request-ID"]
        assert anonymous.get("/health").status_code == anonymous.get("/ready").status_code == 200


@pytest.mark.parametrize("claims", [{"aud": "wrong"}, {"iss": "wrong"}, {"exp": 0}, {"sub": ""}])
def test_untrusted_or_expired_tokens_rejected(client, identity, claims):
    token = identity.token(**claims)
    response = client.get("/v1/parking/facilities", params=BOUNDS, headers={"Authorization": "Bearer " + token})
    assert response.status_code == 401 and token not in response.text


@pytest.mark.parametrize("claims", [
    {"permissions": ["parking:estimate"]}, {"scope": "openid parking:estimate"}, {"roles": ["parking:estimate"]}])
def test_estimator_accepts_only_signed_permission_claims(client, identity, claims):
    response = client.post("/v1/parking/occupancy/estimate", json={"observations": []},
                           headers={"Authorization": "Bearer " + identity.token(**claims)})
    assert response.status_code == 200


def test_reader_cannot_elevate_permissions_with_headers_or_malformed_claim(client, identity):
    response = client.post("/v1/parking/occupancy/estimate", json={"observations": []},
                           headers={"X-Permissions": "parking:estimate", "X-Roles": "parking:estimate"})
    assert response.status_code == 403
    malformed = identity.token(permissions={"parking:estimate": True})
    # The helper converts iterable permissions to a list; sign the malformed
    # object explicitly so this tests the verifier rather than helper coercion.
    claims = jwt.decode(malformed, options={"verify_signature": False})
    claims["permissions"] = {"parking:estimate": True}
    token = jwt.encode(claims, identity.key, algorithm="RS256", headers={"kid": "ephemeral-key"})
    assert client.post("/v1/parking/occupancy/estimate", json={"observations": []},
                       headers={"Authorization": "Bearer " + token}).status_code == 403


def test_forged_signature_fails(client, identity):
    claims = jwt.decode(identity.token(), options={"verify_signature": False})
    forged_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(claims, forged_key, algorithm="RS256", headers={"kid": "ephemeral-key"})
    assert client.get("/v1/parking/facilities/demo-central", headers={"Authorization": "Bearer " + token}).status_code == 401


def test_identity_outage_and_unconfigured_issuer_fail_closed(identity, monkeypatch):
    identity.available = False
    with TestClient(main.app) as caller:
        token = identity.token()
        response = caller.get("/v1/parking/facilities/demo-central", headers={"Authorization": "Bearer " + token})
        assert response.status_code == 503 and response.json()["error"]["retryable"]
        assert caller.get("/ready").status_code == 200
    for name in ("JWT_ISSUER", "JWT_AUDIENCE", "JWT_JWKS_URL"):
        monkeypatch.delenv(name)
    monkeypatch.setenv("PARKING_INTERNAL_CLIENTS", '[{"token":"old-opaque-demo-token","permissions":["parking:estimate"]}]')
    with TestClient(main.app) as caller:
        assert caller.get("/v1/parking/facilities/demo-central", headers={"Authorization": "Bearer old-opaque-demo-token"}).status_code == 503


def test_cached_signing_keys_survive_only_until_cache_expiry(client, identity):
    url = "/v1/parking/facilities/demo-central"
    assert client.get(url).status_code == 200
    identity.available = False
    assert client.get(url).status_code == 200
    client.app.state.token_verifier._jwks_client.jwk_set_cache.put(None)
    assert client.get(url).status_code == 503
