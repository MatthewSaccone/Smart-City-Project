"""Only test code substitutes a verified identity; production JWT checks remain enabled."""
import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))
S7_ROOT = Path(__file__).resolve().parents[1]
if str(S7_ROOT) not in sys.path:
    sys.path.insert(0,str(S7_ROOT))

from app.main import app
from app.auth import require_reporter
from app.models import to_iso, utc_now
from shared.auth import Principal


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('S7_DB_PATH',str(tmp_path/'gasoline.sqlite3'))
    monkeypatch.delenv('REGISTRY_URL',raising=False)
    app.dependency_overrides.clear()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def catalog(client):
    stations = [
        dict(station_id='example-1',name='Alpha Fuel',address='1 Example Road, London, ON',
             latitude=43.0000,longitude=-81.2000,available_fuel_types=['regular_87','diesel']),
        dict(station_id='example-2',name='Bravo Fuel',address='2 Example Road, London, ON',
             latitude=43.0010,longitude=-81.2010,available_fuel_types=['regular_87','premium_91']),
        dict(station_id='example-3',name='Charlie Fuel',address='3 Example Road, London, ON',
             latitude=43.0020,longitude=-81.2010,available_fuel_types=['regular_87']),
    ]
    client.app.state.gasoline.import_stations(stations,'synthetic_test_fixture',to_iso(utc_now()))
    return stations


@pytest.fixture
def reporter(client):
    app.dependency_overrides[require_reporter] = lambda: Principal(
        subject='test-subject',roles=frozenset({'gasoline:report'}))
    yield
    app.dependency_overrides.pop(require_reporter,None)
