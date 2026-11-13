import os

import pytest
from fastapi.testclient import TestClient

import app.main as main
from shared.testing_identity import EphemeralIdentity


@pytest.fixture(scope="session")
def identity():
    with EphemeralIdentity() as authority:
        yield authority


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch, identity):
    monkeypatch.setattr(main, "REGISTRY_URL", None)
    for name in tuple(os.environ):
        if name.startswith(("PARKING_", "JWT_")):
            monkeypatch.delenv(name, raising=False)
    for name, value in identity.environment().items():
        monkeypatch.setenv(name, value)
    identity.available = True
    from test_parking import ESTIMATE_HEADERS
    monkeypatch.setitem(ESTIMATE_HEADERS, "Authorization", "Bearer " + identity.token(permissions=["parking:estimate"]))


@pytest.fixture
def client(identity):
    with TestClient(main.app, headers={"Authorization": "Bearer " + identity.token()}) as client:
        yield client
