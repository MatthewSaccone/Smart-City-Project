from datetime import datetime, timezone
import json
import os
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

import app.main as main
import app.repository as repository

NOW = datetime(2026, 11, 22, 12, tzinfo=timezone.utc)
AREA = {"latitude": 43.0096, "longitude": -81.2737, "radius_m": 1500}


@pytest.fixture(autouse=True)
def configuration(tmp_path, monkeypatch):
    for name in tuple(os.environ):
        if name.startswith("TRANSIT_"):
            monkeypatch.delenv(name, raising=False)
    clock = SimpleNamespace(now=NOW)
    monkeypatch.setattr(repository, "utc_now", lambda: clock.now)
    monkeypatch.setattr(main, "REGISTRY_URL", None)
    for kind in ("schedule", "live"):
        path = tmp_path / (kind + ".json")
        path.write_bytes((repository.FIXTURES / (kind + ".json")).read_bytes())
        monkeypatch.setenv("TRANSIT_" + kind.upper() + "_FILE", str(path))
    monkeypatch.setenv("TRANSIT_DATABASE_PATH", str(tmp_path / "transit.sqlite"))
    monkeypatch.setenv("TRANSIT_REFRESH_SECONDS", "3600")
    return clock


@pytest.fixture
def client(identity):
    with TestClient(main.app, headers={"Authorization": "Bearer " + identity.token()}) as client:
        yield client


@pytest.fixture
def fresh(client):
    repo = client.app.state.repository
    schedule = json.loads(repo.schedule_path.read_text())
    schedule.update(simulated=False, source="controlled_test_schedule")
    live = json.loads(repo.live_path.read_text())
    live = json.loads(json.dumps(live).replace("2026-11-10", "2026-11-22"))
    live.update(simulated=False, source="controlled_test_updates")
    repo.schedule_path.write_text(json.dumps(schedule))
    repo.live_path.write_text(json.dumps(live))
    repo.refresh()
    return live


@pytest.fixture
def publish(client):
    def update(payload):
        repo = client.app.state.repository
        repo.live_path.write_text(json.dumps(payload))
        repo.refresh()
    return update


@pytest.fixture(scope="session")
def identity():
    from shared.testing_identity import EphemeralIdentity
    with EphemeralIdentity() as authority:
        yield authority

@pytest.fixture(autouse=True)
def jwt_configuration(monkeypatch, identity):
    for key, value in identity.environment().items():
        monkeypatch.setenv(key, value)

