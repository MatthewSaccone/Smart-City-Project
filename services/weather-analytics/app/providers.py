"""External weather / air-quality provider adapters.

Every adapter returns *normalized bundles* (plain JSON-serializable dicts) so the
service can persist them for the QR3 outage fallback without caring which
provider produced them. Raw units are normalized here for weather; pollutant
concentrations are returned in ug/m3 and converted to contract units by the
service layer.

Providers:
  * ``open_meteo``  - https://open-meteo.com (free, no API key). Default.
  * ``simulated``   - deterministic synthetic data for offline demos and tests.
                      Always labelled ``simulated`` in responses.
  * ``unavailable`` - every call fails; used to exercise the outage fallback.
"""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.models import iso_z

WEATHER_HOURLY = ("temperature_2m", "relative_humidity_2m", "precipitation_probability",
                  "precipitation", "weather_code", "wind_speed_10m")
WEATHER_CURRENT = ("temperature_2m", "relative_humidity_2m", "apparent_temperature", "precipitation",
                   "weather_code", "wind_speed_10m", "wind_gusts_10m", "visibility")
AIR_CURRENT = ("pm2_5", "pm10", "ozone", "nitrogen_dioxide", "sulphur_dioxide", "carbon_monoxide")
AIR_HOURLY = ("pm2_5", "ozone", "nitrogen_dioxide")
AIR_KEYS = {"pm2_5": "pm2_5", "pm10": "pm10", "ozone": "o3", "nitrogen_dioxide": "no2",
            "sulphur_dioxide": "so2", "carbon_monoxide": "co"}


class ProviderError(Exception):
    status = 503
    message = "Weather provider unavailable"


class ProviderTimeout(ProviderError):
    status = 504
    message = "Weather provider timed out"


class ProviderUnavailable(ProviderError):
    status = 503
    message = "Weather provider unavailable"


class ProviderInvalid(ProviderError):
    status = 502
    message = "Weather provider returned unusable data"


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _nonneg(value: Any) -> float | None:
    value = _number(value)
    return None if value is None or value < 0 else value


def _humidity(value: Any) -> float | None:
    value = _number(value)
    return None if value is None or not 0 <= value <= 100 else value


def _code(value: Any) -> int | None:
    value = _number(value)
    return int(value) if value is not None else None


def _instant(value: Any) -> datetime:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProviderInvalid("timestamp missing")
    return datetime.fromtimestamp(int(value), tz=timezone.utc)


class Provider:
    name = "provider"
    simulated = False

    async def weather(self, latitude: float, longitude: float, *, now: datetime, past_days: int = 2) -> dict:
        raise NotImplementedError

    async def air_quality(self, latitude: float, longitude: float, *, now: datetime, past_days: int = 2) -> dict:
        raise NotImplementedError

    async def close(self) -> None:
        return None


class OpenMeteoProvider(Provider):
    """Open-Meteo forecast + air-quality APIs. Model data, not station observations."""

    name = "open-meteo"

    def __init__(self, *, timeout: float = 1.5, transport: httpx.AsyncBaseTransport | None = None,
                 forecast_url: str = "https://api.open-meteo.com/v1/forecast",
                 air_url: str = "https://air-quality-api.open-meteo.com/v1/air-quality"):
        self.forecast_url = forecast_url
        self.air_url = air_url
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(timeout), transport=transport,
                                        headers={"User-Agent": "smart-city-weather-analytics/1.0"})

    async def close(self) -> None:
        await self.client.aclose()

    async def _get(self, url: str, params: dict) -> dict:
        try:
            response = await self.client.get(url, params=params)
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(str(exc)) from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderUnavailable(f"HTTP {response.status_code}")
        if response.status_code != 200:
            raise ProviderInvalid(f"HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderInvalid("non-JSON body") from exc
        if not isinstance(payload, dict) or payload.get("error"):
            raise ProviderInvalid("provider error body")
        return payload

    @staticmethod
    def _series(payload: dict, block: str, keys: tuple[str, ...]) -> list[dict]:
        hourly = payload.get(block)
        if not isinstance(hourly, dict) or not isinstance(hourly.get("time"), list):
            raise ProviderInvalid(f"missing {block}")
        times = hourly["time"]
        columns = {}
        for key in keys:
            column = hourly.get(key)
            if column is None:
                column = [None] * len(times)
            if not isinstance(column, list) or len(column) != len(times):
                raise ProviderInvalid(f"misaligned {block}.{key}")
            columns[key] = column
        return [{"time": _instant(t), **{key: columns[key][i] for key in keys}} for i, t in enumerate(times)]

    @staticmethod
    def _check_units(payload: dict, block: str, expected: dict[str, str]) -> None:
        units = payload.get(block)
        if not isinstance(units, dict):
            return
        for key, unit in expected.items():
            if key in units and units[key] != unit:
                raise ProviderInvalid(f"unexpected unit {units[key]} for {key}")

    async def weather(self, latitude, longitude, *, now, past_days=2):
        payload = await self._get(self.forecast_url, {
            "latitude": f"{latitude:.4f}", "longitude": f"{longitude:.4f}",
            "current": ",".join(WEATHER_CURRENT), "hourly": ",".join(WEATHER_HOURLY),
            "wind_speed_unit": "ms", "temperature_unit": "celsius", "precipitation_unit": "mm",
            "timeformat": "unixtime", "timezone": "GMT",
            "past_days": max(0, min(int(past_days), 92)), "forecast_days": 8,
        })
        for block in ("current_units", "hourly_units"):
            self._check_units(payload, block, {"temperature_2m": "°C", "wind_speed_10m": "m/s",
                                               "wind_gusts_10m": "m/s", "precipitation": "mm"})
        current = payload.get("current")
        if not isinstance(current, dict):
            raise ProviderInvalid("missing current")
        interval = _number(current.get("interval")) or 900.0
        precipitation = _nonneg(current.get("precipitation"))
        hourly = [{
            "time": iso_z(row["time"]),
            "temperature_c": _number(row["temperature_2m"]),
            "relative_humidity_pct": _humidity(row["relative_humidity_2m"]),
            "precipitation_probability_pct": _humidity(row["precipitation_probability"]),
            "precipitation_mm": _nonneg(row["precipitation"]),
            "wind_speed_m_s": _nonneg(row["wind_speed_10m"]),
            "weather_code": _code(row["weather_code"]),
        } for row in self._series(payload, "hourly", WEATHER_HOURLY)]
        return {
            "source": self.name, "simulated": False,
            "retrieved_at": iso_z(now), "source_updated_at": None,
            "current": {
                "observed_at": iso_z(_instant(current.get("time"))),
                "temperature_c": _number(current.get("temperature_2m")),
                "feels_like_c": _number(current.get("apparent_temperature")),
                "relative_humidity_pct": _humidity(current.get("relative_humidity_2m")),
                # Open-Meteo reports precipitation summed over the preceding interval.
                "precipitation_rate_mm_h": None if precipitation is None else precipitation * 3600.0 / interval,
                "wind_speed_m_s": _nonneg(current.get("wind_speed_10m")),
                "wind_gust_m_s": _nonneg(current.get("wind_gusts_10m")),
                "visibility_m": _nonneg(current.get("visibility")),
                "weather_code": _code(current.get("weather_code")),
            },
            "hourly": hourly,
        }

    async def air_quality(self, latitude, longitude, *, now, past_days=2):
        payload = await self._get(self.air_url, {
            "latitude": f"{latitude:.4f}", "longitude": f"{longitude:.4f}",
            "current": ",".join(AIR_CURRENT), "hourly": ",".join(AIR_HOURLY),
            "timeformat": "unixtime", "timezone": "GMT",
            "past_days": max(0, min(int(past_days), 92)), "forecast_days": 1,
        })
        for block in ("current_units", "hourly_units"):
            self._check_units(payload, block, {key: "μg/m³" for key in AIR_CURRENT})
        current = payload.get("current")
        if not isinstance(current, dict):
            raise ProviderInvalid("missing current")
        hourly = [{"time": iso_z(row["time"]), **{AIR_KEYS[key]: _nonneg(row[key]) for key in AIR_HOURLY}}
                  for row in self._series(payload, "hourly", AIR_HOURLY)]
        return {
            "source": self.name, "simulated": False,
            "retrieved_at": iso_z(now), "source_updated_at": None,
            "current": {"observed_at": iso_z(_instant(current.get("time"))),
                        **{AIR_KEYS[key]: _nonneg(current.get(key)) for key in AIR_CURRENT}},
            "hourly": hourly,
        }


class SimulatedProvider(Provider):
    """Deterministic synthetic weather so the service can be demonstrated offline."""

    name = "simulated:s5-synthetic-weather"
    simulated = True

    @staticmethod
    def _seed(*parts) -> float:
        digest = hashlib.sha256(repr(parts).encode()).digest()
        return int.from_bytes(digest[:4], "big") / 2**32

    def _hour(self, latitude, longitude, at: datetime) -> dict:
        hour = at.hour + at.minute / 60
        day = at.date().toordinal()
        base = 12 - 0.6 * (latitude - 40) - 8 * math.cos(2 * math.pi * (at.timetuple().tm_yday - 15) / 365)
        temperature = base + 5 * math.sin(2 * math.pi * (hour - 9) / 24) + 3 * (self._seed(day, "t") - 0.5)
        wet = self._seed(day, round(latitude, 1), round(longitude, 1))
        if wet > 0.8:
            code = 71 if temperature <= 0 else 61
        elif wet > 0.72:
            code = 45
        elif wet > 0.4:
            code = 3
        else:
            code = 0
        return {
            "temperature_c": round(temperature, 1),
            "relative_humidity_pct": round(max(20, min(100, 70 + 20 * math.cos(2 * math.pi * hour / 24) + 15 * wet - 7)), 0),
            "precipitation_probability_pct": round(min(100, wet * 100 if code in (61, 71) else wet * 40), 0),
            "precipitation_mm": round(1.5 + 2 * wet, 1) if code in (61, 71) else 0.0,
            "wind_speed_m_s": round(3 + 3 * self._seed(day, int(hour), "w"), 1),
            "weather_code": code,
            "visibility_m": 800.0 if code == 45 else 15000.0,
            "pm2_5": round(5 + 6 * self._seed(day, int(hour), "pm"), 1),
            "pm10": round(10 + 8 * self._seed(day, int(hour), "pm10"), 1),
            "o3": round(40 + 30 * math.sin(math.pi * hour / 24), 1),
            "no2": round(15 + 10 * self._seed(day, int(hour), "no2"), 1),
            "so2": round(2 + 2 * self._seed(day, "so2"), 1),
            "co": round(200 + 50 * self._seed(day, "co"), 1),
        }

    def _hours(self, latitude, longitude, now, past_days, forecast_days):
        start = now.replace(minute=0, second=0, microsecond=0) - timedelta(days=past_days)
        start = start.replace(hour=0)
        count = (past_days + forecast_days) * 24
        return [(start + timedelta(hours=i), self._hour(latitude, longitude, start + timedelta(hours=i)))
                for i in range(count)]

    async def weather(self, latitude, longitude, *, now, past_days=2):
        observed = now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0)
        sample = self._hour(latitude, longitude, observed)
        return {
            "source": self.name, "simulated": True,
            "retrieved_at": iso_z(now), "source_updated_at": iso_z(observed),
            "current": {
                "observed_at": iso_z(observed),
                "temperature_c": sample["temperature_c"],
                "feels_like_c": round(sample["temperature_c"] - 0.3 * sample["wind_speed_m_s"], 1),
                "relative_humidity_pct": sample["relative_humidity_pct"],
                "precipitation_rate_mm_h": sample["precipitation_mm"],
                "wind_speed_m_s": sample["wind_speed_m_s"],
                "wind_gust_m_s": round(sample["wind_speed_m_s"] * 1.6, 1),
                "visibility_m": sample["visibility_m"],
                "weather_code": sample["weather_code"],
            },
            "hourly": [{"time": iso_z(t), **{k: h[k] for k in (
                "temperature_c", "relative_humidity_pct", "precipitation_probability_pct",
                "precipitation_mm", "wind_speed_m_s", "weather_code")}}
                for t, h in self._hours(latitude, longitude, now, past_days, 8)],
        }

    async def air_quality(self, latitude, longitude, *, now, past_days=2):
        observed = now.replace(minute=0, second=0, microsecond=0)
        sample = self._hour(latitude, longitude, observed)
        return {
            "source": self.name, "simulated": True,
            "retrieved_at": iso_z(now), "source_updated_at": iso_z(observed),
            "current": {"observed_at": iso_z(observed),
                        **{key: sample[key] for key in ("pm2_5", "pm10", "o3", "no2", "so2", "co")}},
            "hourly": [{"time": iso_z(t), **{k: h[k] for k in ("pm2_5", "o3", "no2")}}
                       for t, h in self._hours(latitude, longitude, now, past_days, 1)],
        }


class UnavailableProvider(Provider):
    name = "unavailable"

    async def weather(self, latitude, longitude, *, now, past_days=2):
        raise ProviderUnavailable("provider disabled by WEATHER_PROVIDER=unavailable")

    async def air_quality(self, latitude, longitude, *, now, past_days=2):
        raise ProviderUnavailable("provider disabled by WEATHER_PROVIDER=unavailable")


def build_provider(kind: str, *, timeout: float) -> Provider:
    if kind == "open_meteo":
        return OpenMeteoProvider(timeout=timeout)
    if kind == "simulated":
        return SimulatedProvider()
    if kind == "unavailable":
        return UnavailableProvider()
    raise ValueError("WEATHER_PROVIDER must be open_meteo, simulated or unavailable")
