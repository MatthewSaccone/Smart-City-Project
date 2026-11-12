import json

import pytest
from fastapi.testclient import TestClient

import app.main as main


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch):
    monkeypatch.setattr(main, "REGISTRY_URL", None)
    for name in ("PARKING_CACHE_PATH", "PARKING_OCCUPANCY_FILE", "PARKING_OCCUPANCY_MODE", "PARKING_REFRESH_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PARKING_INTERNAL_CLIENTS", json.dumps([
        {"token": "test-estimator-token-long-enough", "permissions": ["parking:estimate"]},
        {"token": "test-reader-token-long-enough", "permissions": []},
    ]))


@pytest.fixture
def client():
    with TestClient(main.app) as client:
        yield client
