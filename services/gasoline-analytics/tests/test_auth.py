from fastapi.testclient import TestClient
from app.main import app
from shared.testing_identity import EphemeralIdentity

def test_s7_authentication(monkeypatch):
    with EphemeralIdentity() as identity:
        for k,v in identity.environment().items(): monkeypatch.setenv(k,v)
        with TestClient(app) as client:
            assert client.get('/').status_code==200
            assert client.get('/health').status_code==200
            assert client.get('/ready').status_code==200
            assert client.get('/auth/check').status_code==401
            assert client.get('/auth/check',headers={'Authorization':'Bearer '+identity.token()}).status_code==200
            assert client.get('/auth/check',headers={'Authorization':'Bearer '+identity.token(roles=['service'],permissions=['s1:read'])}).status_code==403
