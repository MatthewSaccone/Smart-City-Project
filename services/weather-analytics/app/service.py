"""S5 domain service: provider caching, QR3 outage fallback, freshness and all five reads."""

from __future__ import annotations

import asyncio
import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app import analytics
from app.errors import ServiceError, validation_error
from app.models import iso_z
from app.providers import Provider, ProviderError, ProviderInvalid, ProviderTimeout
from app.store import Store

LOG = logging.getLogger("weather")

FRESH = {"weather": timedelta(minutes=30), "air": timedelta(minutes=60), "forecast": timedelta(hours=6)}
HORIZON = timedelta(days=7)
HISTORY = timedelta(days=30)
GRACE = timedelta(seconds=60)          # tolerated clock skew for "now" boundaries
CURRENT_WINDOW = timedelta(hours=1)    # travel_at within this uses the current observation
TREND_UNITS = {"temperature_c": "c", "relative_humidity_pct": "pct", "pm2_5_ug_m3": "ug_m3", "aqhi": "aqhi"}
TREND_KIND = {"temperature_c": "weather", "relative_humidity_pct": "weather", "pm2_5_ug_m3": "air", "aqhi": "air"}


def parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


@dataclass
class Entry:
    bundle: dict
    hourly: list[dict]
    retrieved_at: datetime
    refreshed: bool                    # fetched successfully by this process
    failed_at: datetime | None = None
    history_days: int = 0


@dataclass
class Settings:
    refresh: timedelta = timedelta(minutes=5)
    backoff: timedelta = timedelta(seconds=30)
    timeout: float = 1.5
    bbox: tuple[float, float, float, float] = (41.6, -95.2, 56.9, -74.3)


@dataclass
class Failure:
    error: ProviderError
    at: datetime


class WeatherService:
    def __init__(self, provider: Provider, store: Store, *, settings: Settings | None = None, clock=None):
        self.provider = provider
        self.store = store
        self.settings = settings or Settings()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.entries: dict[tuple[str, str], Entry] = {}
        self.failures: dict[tuple[str, str], Failure] = {}
        self.locks: defaultdict[tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)
        for kind, cell, bundle in store.load_bundles():
            try:
                self.entries[(kind, cell)] = self._entry(bundle, refreshed=False)
            except Exception as exc:  # corrupt row: ignore rather than fail startup
                LOG.warning("Ignoring persisted %s bundle for %s: %s", kind, cell, type(exc).__name__)

    # ------------------------------------------------------------------ plumbing

    @staticmethod
    def cell(latitude: float, longitude: float) -> str:
        """~1 km grid cell used as the cache / persistence key."""
        return f"{round(latitude, 2):.2f},{round(longitude, 2):.2f}"

    @staticmethod
    def _entry(bundle: dict, *, refreshed: bool) -> Entry:
        hourly = sorted(({**row, "time": parse(row["time"])} for row in bundle["hourly"]),
                        key=lambda row: row["time"])
        parse(bundle["current"]["observed_at"])  # validates presence/format
        return Entry(bundle, hourly, parse(bundle["retrieved_at"]), refreshed)

    def check_coverage(self, latitude: float, longitude: float) -> None:
        for name, value in (("latitude", latitude), ("longitude", longitude)):
            if not math.isfinite(value):
                raise validation_error(name, "not_finite", "Coordinates must be finite numbers")
        south, west, north, east = self.settings.bbox
        if not (south <= latitude <= north and west <= longitude <= east):
            raise ServiceError(404, "Location outside service coverage")

    async def _fetch(self, kind: str, latitude: float, longitude: float, past_days: int) -> Entry:
        now = self.clock()
        call = self.provider.weather if kind == "weather" else self.provider.air_quality
        try:
            bundle = await asyncio.wait_for(call(latitude, longitude, now=now, past_days=past_days),
                                            timeout=self.settings.timeout)
            entry = self._entry(bundle, refreshed=True)
        except asyncio.TimeoutError as exc:
            raise ProviderTimeout("budget exceeded") from exc
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderInvalid(type(exc).__name__) from exc
        if entry.retrieved_at > now + GRACE or parse(bundle["current"]["observed_at"]) > now + GRACE:
            raise ProviderInvalid("future timestamp")
        cell = self.cell(latitude, longitude)
        await asyncio.to_thread(self.store.save_bundle, kind, cell, bundle)
        await asyncio.to_thread(self.store.save_samples, cell, self._samples(kind, entry, now),
                                bundle["source"], now)
        entry.history_days = past_days
        return entry

    def _samples(self, kind: str, entry: Entry, now: datetime) -> list[tuple[str, datetime, float]]:
        past = [row for row in entry.hourly if row["time"] <= now]
        rows: list[tuple[str, datetime, float]] = []
        if kind == "weather":
            for row in past:
                for metric in ("temperature_c", "relative_humidity_pct"):
                    if row.get(metric) is not None:
                        rows.append((metric, row["time"], row[metric]))
        else:
            for row in past:
                if row.get("pm2_5") is not None:
                    rows.append(("pm2_5_ug_m3", row["time"], row["pm2_5"]))
                value = analytics.aqhi_from_hourly(past, row["time"])
                if value is not None:
                    rows.append(("aqhi", row["time"], value))
        return rows

    async def bundle(self, kind: str, latitude: float, longitude: float, *, past_days: int = 2):
        """Return (entry, mode) where mode is 'live' or 'cached' (QR3 fallback)."""
        key = (kind, self.cell(latitude, longitude))

        def usable():
            now = self.clock()
            entry = self.entries.get(key)
            if entry and entry.refreshed and now - entry.retrieved_at < self.settings.refresh \
                    and entry.history_days >= past_days:
                return entry, "live"
            failure = self.failures.get(key)
            if failure and now - failure.at < self.settings.backoff:
                if entry:
                    return entry, "cached"
                raise self._upstream(failure.error)
            return None

        if (hit := usable()) is not None:
            return hit
        async with self.locks[key]:
            if (hit := usable()) is not None:
                return hit
            try:
                entry = await self._fetch(kind, latitude, longitude, past_days)
            except ProviderError as exc:
                LOG.warning("%s provider failure for %s: %s %s", kind, key[1], type(exc).__name__, exc)
                self.failures[key] = Failure(exc, self.clock())
                if key in self.entries:
                    return self.entries[key], "cached"
                raise self._upstream(exc) from exc
            previous = self.entries.get(key)
            entry.history_days = max(past_days, previous.history_days if previous and previous.refreshed else 0)
            self.entries[key] = entry
            self.failures.pop(key, None)
            return entry, "live"

    @staticmethod
    def _upstream(exc: ProviderError) -> ServiceError:
        return ServiceError(exc.status, exc.message + " and no cached data is available",
                            source="upstream" if exc.status in (502, 504) else "service")

    def _status(self, entry: Entry, mode: str, reference: datetime | None, freshness: timedelta) -> dict:
        now = self.clock()
        stale = reference is None or now - reference > freshness
        if mode == "cached":
            status = "cached"
        elif entry.bundle.get("simulated"):
            status = "simulated"
        else:
            status = "live"
        return {"is_live": status == "live" and not stale, "is_stale": stale,
                "source": entry.bundle["source"], "data_status": status}

    # ------------------------------------------------------------------ FR1 / FR3 / QR2 / QR3

    async def current(self, latitude: float, longitude: float) -> dict:
        self.check_coverage(latitude, longitude)
        entry, mode = await self.bundle("weather", latitude, longitude)
        current = entry.bundle["current"]
        observed = parse(current["observed_at"])
        weather = {key: current.get(key) for key in (
            "temperature_c", "feels_like_c", "relative_humidity_pct", "precipitation_rate_mm_h",
            "wind_speed_m_s", "wind_gust_m_s", "visibility_m")}
        weather = {k: (round(v, 2) if v is not None else None) for k, v in weather.items()}
        weather["condition_code"] = analytics.condition_from_wmo(current.get("weather_code"),
                                                                 current.get("wind_speed_m_s"))
        return {
            "location": {"latitude": latitude, "longitude": longitude},
            "as_of": self.clock(),
            "weather": weather,
            "provenance": {
                "observed_at": observed,
                "source_updated_at": parse(entry.bundle.get("source_updated_at")),
                "service_updated_at": entry.retrieved_at,
                **self._status(entry, mode, observed, FRESH["weather"]),
            },
        }

    # ------------------------------------------------------------------ FR2

    def _forecast_rows(self, entry: Entry, resolution: str) -> list[dict]:
        rows = [{
            "valid_from": row["time"], "valid_to": row["time"] + timedelta(hours=1),
            "temperature_c": row.get("temperature_c"),
            "precipitation_probability_pct": row.get("precipitation_probability_pct"),
            "wind_speed_m_s": row.get("wind_speed_m_s"),
            "precipitation_mm": row.get("precipitation_mm"),
            "condition_code": analytics.condition_from_wmo(row.get("weather_code"), row.get("wind_speed_m_s")),
        } for row in entry.hourly]
        if resolution == "hourly":
            return rows
        days: dict = defaultdict(list)
        for row in rows:
            days[row["valid_from"].date()].append(row)
        daily = []
        for day, items in sorted(days.items()):
            start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)

            def values(key):
                return [item[key] for item in items if item[key] is not None]
            temperatures = values("temperature_c")
            daily.append({
                "valid_from": start, "valid_to": start + timedelta(days=1),
                "temperature_c": round(sum(temperatures) / len(temperatures), 2) if temperatures else None,
                "precipitation_probability_pct": max(values("precipitation_probability_pct"), default=None),
                "wind_speed_m_s": max(values("wind_speed_m_s"), default=None),
                "condition_code": analytics.worst_condition(
                    [item["condition_code"] for item in items if item["condition_code"] != "unknown"]),
            })
        return daily

    async def forecast(self, query) -> dict:
        self.check_coverage(query.latitude, query.longitude)
        now = self.clock()
        if query.start_at < now - GRACE:
            raise validation_error("start_at", "must_not_be_in_past", "start_at must be at or after the current time")
        if query.end_at <= query.start_at:
            raise validation_error("end_at", "must_be_after_start_at", "End time must be after start time.")
        if query.end_at > now + HORIZON:
            raise validation_error("end_at", "beyond_7_day_horizon", "end_at must be within seven days")
        entry, mode = await self.bundle("weather", query.latitude, query.longitude)
        items = [row for row in self._forecast_rows(entry, query.resolution)
                 if row["valid_from"] < query.end_at and row["valid_to"] > query.start_at]
        secondary = "valid_to" if query.sort_by == "valid_from" else "valid_from"
        items.sort(key=lambda row: (row[query.sort_by], row[secondary]), reverse=query.sort_order == "desc")
        page = items[query.offset:query.offset + query.limit]
        for row in page:
            row.pop("precipitation_mm", None)
            for key in ("temperature_c", "precipitation_probability_pct", "wind_speed_m_s"):
                if row[key] is not None:
                    row[key] = round(row[key], 2)
        issued = parse(entry.bundle.get("source_updated_at")) or entry.retrieved_at
        return {
            "location": {"latitude": query.latitude, "longitude": query.longitude},
            "as_of": now, "items": page,
            "pagination": {"limit": query.limit, "offset": query.offset, "total": len(items),
                           "has_more": query.offset + len(page) < len(items)},
            "resolution": query.resolution,
            "issued_at": issued,
            "source_updated_at": parse(entry.bundle.get("source_updated_at")),
            "service_updated_at": entry.retrieved_at,
            **self._status(entry, mode, issued, FRESH["forecast"]),
        }

    # ------------------------------------------------------------------ FR3 air quality

    async def air_quality(self, latitude: float, longitude: float) -> dict:
        self.check_coverage(latitude, longitude)
        entry, mode = await self.bundle("air", latitude, longitude)
        current = entry.bundle["current"]
        observed = parse(current["observed_at"])
        pollutants = [{"pollutant": key, "concentration": analytics.to_contract_unit(key, current[key]),
                       "unit": analytics.UNITS[key]}
                      for key in ("pm2_5", "pm10", "o3", "no2", "so2", "co") if current.get(key) is not None]
        value = analytics.aqhi_from_hourly(entry.hourly, observed)
        index = None if value is None else {"system": "ca_aqhi", "value": float(round(value)),
                                            "category": analytics.aqhi_category(value)}
        return {
            "location": {"latitude": latitude, "longitude": longitude},
            "as_of": self.clock(),
            "air_quality_index": index,
            "pollutants": pollutants,
            "provenance": {
                "observed_at": observed,
                "source_updated_at": parse(entry.bundle.get("source_updated_at")),
                "service_updated_at": entry.retrieved_at,
                **self._status(entry, mode, observed, FRESH["air"]),
            },
        }

    # ------------------------------------------------------------------ trends

    async def trends(self, query) -> dict:
        self.check_coverage(query.latitude, query.longitude)
        now = self.clock()
        if query.end_at <= query.start_at:
            raise validation_error("end_at", "must_be_after_start_at", "End time must be after start time.")
        if query.end_at > now + GRACE:
            raise validation_error("end_at", "must_not_be_in_future", "end_at must not be after the current time")
        if query.start_at < now - HISTORY:
            raise validation_error("start_at", "older_than_30_days", "start_at must be within the past 30 days")
        if query.end_at - query.start_at > HORIZON:
            raise validation_error("end_at", "range_exceeds_7_days", "Trend range must be at most seven days")
        end = min(query.end_at, now)
        cell = self.cell(query.latitude, query.longitude)
        kind = TREND_KIND[query.metric]

        step = timedelta(hours=1) if query.resolution == "hourly" else timedelta(days=1)
        first = query.start_at.replace(minute=0, second=0, microsecond=0)
        if query.resolution == "daily":
            first = first.replace(hour=0)
        points, updated, sources, earliest = await asyncio.to_thread(
            self.store.samples, cell, query.metric, first, end)
        if earliest is None or earliest > first:
            # Backfill history from the provider once; an outage only makes coverage partial.
            days = min(31, math.ceil((now - query.start_at).total_seconds() / 86400) + 1)
            try:
                await self.bundle(kind, query.latitude, query.longitude, past_days=days)
                points, updated, sources, earliest = await asyncio.to_thread(
                    self.store.samples, cell, query.metric, first, end)
            except ServiceError as exc:
                LOG.info("Trend backfill unavailable for %s: %s", cell, exc.message)

        # Buckets are whole UTC hours/days; each hourly sample is stamped at its hour start.
        cursor, buckets = first, []
        while cursor < query.end_at:
            values = [value for at, value in points if cursor <= at < cursor + step]
            buckets.append({"period_start": cursor, "period_end": cursor + step,
                            "value": round(sum(values) / len(values), 2) if values else None,
                            "unit": TREND_UNITS[query.metric], "sample_count": len(values)})
            cursor += step
        filled = sum(1 for bucket in buckets if bucket["sample_count"])
        coverage = "complete" if filled == len(buckets) else ("unavailable" if filled == 0 else "partial")
        secondary = "period_end" if query.sort_by == "period_start" else "period_start"
        buckets.sort(key=lambda b: (b[query.sort_by], b[secondary]), reverse=query.sort_order == "desc")
        page = buckets[query.offset:query.offset + query.limit]
        return {
            "location": {"latitude": query.latitude, "longitude": query.longitude},
            "metric": query.metric, "resolution": query.resolution, "items": page,
            "pagination": {"limit": query.limit, "offset": query.offset, "total": len(buckets),
                           "has_more": query.offset + len(page) < len(buckets)},
            "coverage_status": coverage,
            "dataset_updated_at": parse(updated),
            "as_of": now,
            "source": ",".join(sources) or self.provider.name,
            "is_live": False, "is_stale": False, "data_status": "historical",
        }

    # ------------------------------------------------------------------ FR4 / FR5

    async def guidance(self, query) -> dict:
        self.check_coverage(query.latitude, query.longitude)
        now = self.clock()
        travel_at = query.travel_at or now
        if travel_at < now - GRACE:
            raise validation_error("travel_at", "must_not_be_in_past", "travel_at must not be in the past")
        if travel_at > now + HORIZON:
            raise validation_error("travel_at", "beyond_7_day_horizon", "travel_at must be within seven days")

        inputs: list[dict] = []
        conditions: dict | None = None
        aqhi_value = None
        try:
            entry, mode = await self.bundle("weather", query.latitude, query.longitude)
        except ServiceError:
            entry = None
        if entry is not None and travel_at - now <= CURRENT_WINDOW:
            current = entry.bundle["current"]
            observed = parse(current["observed_at"])
            stale = now - observed > FRESH["weather"]
            inputs.append({"dataset": "weather_observation", "reference_at": observed, "is_stale": stale})
            if not stale:
                conditions = {**current, "condition_code": analytics.condition_from_wmo(
                    current.get("weather_code"), current.get("wind_speed_m_s"))}
            try:
                air, _ = await self.bundle("air", query.latitude, query.longitude)
                air_observed = parse(air.bundle["current"]["observed_at"])
                air_stale = now - air_observed > FRESH["air"]
                inputs.append({"dataset": "air_quality", "reference_at": air_observed, "is_stale": air_stale})
                if not air_stale:
                    aqhi_value = analytics.aqhi_from_hourly(air.hourly, air_observed)
            except ServiceError:
                pass  # air quality is supplementary, never essential
        elif entry is not None:
            issued = parse(entry.bundle.get("source_updated_at")) or entry.retrieved_at
            stale = now - issued > FRESH["forecast"]
            inputs.append({"dataset": "weather_forecast", "reference_at": issued, "is_stale": stale})
            row = next((r for r in self._forecast_rows(entry, "hourly")
                        if r["valid_from"] <= travel_at < r["valid_to"]), None)
            if row is not None and not stale:
                hour_rate = row.get("precipitation_mm")
                conditions = {**row, "precipitation_rate_mm_h": hour_rate}

        base = {"location": {"latitude": query.latitude, "longitude": query.longitude},
                "travel_at": travel_at, "as_of": now, "rule_set_version": analytics.RULE_SET_VERSION,
                "inputs": inputs}
        if conditions is None:
            return {**base, "assessment_status": "insufficient_data", "risk_level": "unknown", "hazards": [],
                    "recommendations": [
                        "Not enough recent weather data is available to assess travel conditions for this "
                        "time and place; the risk is unknown, not low.", analytics.DISCLAIMER]}
        hazards = analytics.assess(conditions, aqhi_value)
        return {**base, "assessment_status": "assessed", "risk_level": analytics.risk_level(hazards),
                "hazards": hazards, "recommendations": analytics.recommendations(hazards)}

    # ------------------------------------------------------------------ readiness

    def ready(self) -> bool:
        if any(kind == "weather" for kind, _ in self.entries):
            return True
        if self.provider.name == "unavailable":
            return False
        now = self.clock()
        recent = [f for (kind, _), f in self.failures.items() if kind == "weather"]
        return not recent or any(now - f.at >= self.settings.backoff for f in recent)


__all__ = ["WeatherService", "Settings", "iso_z"]
