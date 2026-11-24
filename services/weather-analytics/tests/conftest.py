"""Shared fixtures: a controllable fake provider and a fixed, movable clock."""

import asyncio
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

import app.main as main
from app.models import iso_z
from app.providers import Provider, ProviderInvalid, ProviderTimeout, ProviderUnavailable
from app.service import Settings, WeatherService
from app.store import Store

NOW = datetime(2026, 11, 23, 18, 0, tzinfo=timezone.utc)
POINT = {"latitude": 43.0096, "longitude": -81.2737}


def z(value: datetime) -> str:
    return iso_z(value)


def weather_bundle(now: datetime, *, past_days=2, **current) -> dict:
    observed = now - timedelta(minutes=5)
    base = now.replace(minute=0) - timedelta(days=past_days)
    hourly = []
    for i in range((past_days + 8) * 24):
        at = base + timedelta(hours=i)
        hourly.append({"time": z(at), "temperature_c": 5.0 + (i % 24) / 4, "relative_humidity_pct": 70.0,
                       "precipitation_probability_pct": 20.0, "precipitation_mm": 0.0,
                       "wind_speed_m_s": 4.0, "weather_code": 3})
    values = {"observed_at": z(observed), "temperature_c": 7.5, "feels_like_c": 5.4,
              "relative_humidity_pct": 72.0, "precipitation_rate_mm_h": 0.0, "wind_speed_m_s": 4.2,
              "wind_gust_m_s": 7.1, "visibility_m": 9000.0, "weather_code": 3}
    values.update(current)
    return {"source": "test_weather_feed", "simulated": False, "retrieved_at": z(now),
            "source_updated_at": z(now - timedelta(minutes=4)), "current": values, "hourly": hourly}


def air_bundle(now: datetime, *, past_days=2, **current) -> dict:
    observed = now.replace(minute=0)
    base = observed - timedelta(days=past_days)
    hourly = [{"time": z(base + timedelta(hours=i)), "pm2_5": 10.0, "o3": 60.0, "no2": 30.0}
              for i in range(past_days * 24 + 24)]
    values = {"observed_at": z(observed), "pm2_5": 12.5, "pm10": 20.0, "o3": 60.0, "no2": 30.0,
              "so2": 5.0, "co": 250.0}
    values.update(current)
    return {"source": "test_air_feed", "simulated": False, "retrieved_at": z(now),
            "source_updated_at": None, "current": values, "hourly": hourly}


class FakeProvider(Provider):
    """Returns configurable bundles or failures; counts calls."""

    name = "test_provider"

    def __init__(self, clock):
        self.clock = clock
        self.mode = {"weather": "ok", "air": "ok"}
        self.overrides = {"weather": {}, "air": {}}
        self.delay = 0.0
        self.calls = {"weather": 0, "air": 0}
        self.past_days = []

    async def _serve(self, kind, past_days):
        self.calls[kind] += 1
        self.past_days.append(past_days)
        if self.delay:
            await asyncio.sleep(self.delay)
        mode = self.mode[kind]
        if mode == "timeout":
            raise ProviderTimeout("test")
        if mode == "down":
            raise ProviderUnavailable("test")
        if mode == "invalid":
            raise ProviderInvalid("test")
        if mode == "hang":
            await asyncio.sleep(30)
        factory = weather_bundle if kind == "weather" else air_bundle
        return copy.deepcopy(factory(self.clock.now, past_days=past_days, **self.overrides[kind]))

    async def weather(self, latitude, longitude, *, now, past_days=2):
        return await self._serve("weather", past_days)

    async def air_quality(self, latitude, longitude, *, now, past_days=2):
        return await self._serve("air", past_days)


@pytest.fixture
def clock():
    return SimpleNamespace(now=NOW)


@pytest.fixture
def provider(clock):
    return FakeProvider(clock)


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "weather.sqlite")


@pytest.fixture
def make_service(provider, clock, db_path):
    def build(**settings):
        return WeatherService(provider, Store(db_path), clock=lambda: clock.now,
                              settings=Settings(**{"timeout": 0.5, **settings}))
    return build


@pytest.fixture
def client(monkeypatch, make_service, tmp_path):
    monkeypatch.setattr(main, "REGISTRY_URL", None)
    monkeypatch.setenv("WEATHER_PROVIDER", "unavailable")
    monkeypatch.setenv("WEATHER_DATABASE_PATH", str(tmp_path / "unused.sqlite"))
    with TestClient(main.app) as test_client:
        test_client.app.state.weather = make_service()
        yield test_client
