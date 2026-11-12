"""T021: deterministic, provider-independent analysis of historical road traffic.

The input CSV is a *normalized* source format, not a direct export. 
All observations are timestamped, validated, de-duplicated and aggregated by
the requested local weekday/hour.
"""

import csv
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .errors import ServiceError
from .models import DAY_NAMES, HistoricalPattern, HistoryPatternsResponse, HistoryQuery, Pagination

EARTH_RADIUS_M = 6_371_008.8
# Initial explicit classification policy. Review alongside T026/model evaluation.
LOW_MAX = 0.30
HIGH_MIN = 0.60


def parse_instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include an explicit UTC offset")
    return parsed.astimezone(timezone.utc)


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between WGS84 coordinate pairs, in metres."""
    a1, a2 = math.radians(lat1), math.radians(lat2)
    da, dl = a2 - a1, math.radians(lon2 - lon1)
    hav = math.sin(da / 2) ** 2 + math.cos(a1) * math.cos(a2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1, math.sqrt(hav)))


@dataclass(frozen=True)
class TrafficObservation:
    segment_id: str
    latitude: float
    longitude: float
    observed_at: datetime
    speed_kmh: float
    free_flow_speed_kmh: float

    @classmethod
    def from_csv_row(cls, row: dict[str, str]) -> "TrafficObservation":
        lat = float(row["latitude"])
        lon = float(row["longitude"])
        speed = float(row["speed_kmh"])
        free_flow = float(row["free_flow_speed_kmh"])
        if not (all(map(math.isfinite, (lat, lon, speed, free_flow)))
                and -90 <= lat <= 90 and -180 <= lon <= 180
                and speed >= 0 and free_flow > 0):
            raise ValueError("invalid coordinates, observed speed, or free-flow speed")
        segment_id = row["segment_id"].strip()
        if not segment_id:
            raise ValueError("segment_id must not be empty")
        return cls(segment_id, lat, lon, parse_instant(row["observed_at"]), speed, free_flow)

    @property
    def congestion_score(self) -> float:
        # Slow relative to typical uncongested speed -> larger congestion score.
        return max(0.0, min(1.0, 1.0 - self.speed_kmh / self.free_flow_speed_kmh))


def congestion_level(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score < LOW_MAX:
        return "low"
    if score < HIGH_MIN:
        return "moderate"
    return "high"


class HistoryStore:
    """In-memory baseline. Replace with indexed/persisted queries for large datasets."""

    def __init__(self, observations: list[TrafficObservation],
                 dataset_updated_at: datetime, dataset_refresh_due_at: datetime):
        if dataset_updated_at.tzinfo is None or dataset_refresh_due_at.tzinfo is None:
            raise ValueError("dataset metadata timestamps must be timezone-aware")
        if dataset_refresh_due_at <= dataset_updated_at:
            raise ValueError("dataset_refresh_due_at must exceed dataset_updated_at")
        keys: dict[tuple[str, datetime], TrafficObservation] = {}
        for obs in observations:
            key = (obs.segment_id, obs.observed_at)
            if key in keys and keys[key] != obs:
                raise ValueError(f"conflicting duplicate observation: {key}")
            keys[key] = obs
        self.observations = tuple(keys.values())
        self.dataset_updated_at = dataset_updated_at.astimezone(timezone.utc)
        self.dataset_refresh_due_at = dataset_refresh_due_at.astimezone(timezone.utc)

    @classmethod
    def from_files(cls, csv_path: str | Path, metadata_path: str | Path) -> "HistoryStore":
        with open(csv_path, newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            expected = {"segment_id", "latitude", "longitude", "observed_at",
                        "speed_kmh", "free_flow_speed_kmh"}
            if not expected.issubset(set(reader.fieldnames or [])):
                raise ValueError(f"Historical CSV must contain columns: {sorted(expected)}")
            observations = []
            for line_number, row in enumerate(reader, start=2):
                try:
                    observations.append(TrafficObservation.from_csv_row(row))
                except (ValueError, KeyError, TypeError) as exc:
                    raise ValueError(f"Invalid historical CSV row {line_number}: {exc}") from exc
        with open(metadata_path, encoding="utf-8") as handle:
            metadata = json.load(handle)
        return cls(observations,
                   parse_instant(metadata["dataset_updated_at"]),
                   parse_instant(metadata["dataset_refresh_due_at"]))

    def historical_patterns(self, query: HistoryQuery) -> HistoryPatternsResponse:
        now = datetime.now(timezone.utc)
        start_at, end_at = query.history_window(now)
        tz = ZoneInfo(query.timezone)
        nearby = [obs for obs in self.observations if distance_m(
            query.latitude, query.longitude, obs.latitude, obs.longitude) <= query.radius_m]
        if not nearby:
            raise ServiceError(404, "COVERAGE_NOT_FOUND",
                               "No historical road observations cover the requested area.")

        values: dict[tuple[int, int], list[float]] = defaultdict(list)
        for obs in nearby:
            if start_at <= obs.observed_at < end_at:
                local = obs.observed_at.astimezone(tz)
                values[(local.weekday(), local.hour)].append(obs.congestion_score)

        # Include explicitly missing weekday/hour buckets as unknown, not as score=0.
        days = [DAY_NAMES.index(query.day_of_week)] if query.day_of_week else range(7)
        hours = [query.local_hour] if query.local_hour is not None else range(24)
        rows = []
        for weekday in days:
            for hour in hours:
                scores = values.get((weekday, hour), [])
                average = sum(scores) / len(scores) if scores else None
                rows.append(HistoricalPattern(
                    day_of_week=DAY_NAMES[weekday], local_hour=hour,
                    average_congestion_score=average,
                    congestion_level=congestion_level(average),
                    observation_count=len(scores),
                ))

        # Stable tie-breakers: weekday then hour, regardless of selected main sort.
        rows.sort(key=lambda x: (DAY_NAMES.index(x.day_of_week), x.local_hour))
        if query.sort_by == "day_of_week":
            rows.sort(key=lambda x: DAY_NAMES.index(x.day_of_week),
                      reverse=query.sort_order == "desc")
        elif query.sort_by == "local_hour":
            rows.sort(key=lambda x: x.local_hour, reverse=query.sort_order == "desc")
        else:
            rows.sort(key=lambda x: (x.average_congestion_score is None,
                                   (-x.average_congestion_score if query.sort_order == "desc"
                                    else x.average_congestion_score)
                                   if x.average_congestion_score is not None else 0))

        total = len(rows)
        page = rows[query.offset:query.offset + query.limit]
        return HistoryPatternsResponse(
            items=page,
            pagination=Pagination(limit=query.limit, offset=query.offset,
                                  total=total, has_more=query.offset + len(page) < total),
            timezone=query.timezone,
            history_start_at=start_at, history_end_at=end_at,
            dataset_updated_at=self.dataset_updated_at,
            dataset_refresh_due_at=self.dataset_refresh_due_at,
            as_of=now,
            is_stale=now > self.dataset_refresh_due_at,
        )
