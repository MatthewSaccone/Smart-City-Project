"""S2 percentile-75 statistical baseline and derived forecast endpoints.

The City of Toronto midblock dataset is observational; model predictions are
experimental until the separately documented T026 review approves them.
Development preview must be opted into explicitly; production remains gated.
"""
from __future__ import annotations

import logging
import math
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .errors import ServiceError
from .history import congestion_level, distance_m
from .models import (AreaPredictionsResponse, AreaQuery, HighCongestionPeriodsResponse,
                     HighPeriodsQuery, PointPredictionRequest, PointPredictionResponse)
from .postgres_history import bounding_box

LOG = logging.getLogger(__name__)
TORONTO_TZ = ZoneInfo("America/Toronto")
MIN_SAMPLES = 5
SEARCH_RADIUS_M = 1000
GRID_SIZES_MDEG = (20, 100)
CONGESTION_RANK = {"low": 0, "moderate": 1, "high": 2, "unknown": 3}


def score_for_speed(speed_kph: float, reference_kph: float) -> float:
    if (not math.isfinite(speed_kph) or speed_kph < 0
            or not math.isfinite(reference_kph) or reference_kph <= 0):
        raise ValueError("speeds must be finite, measured >=0 and reference >0")
    return max(0.0, min(1.0, 1.0 - speed_kph / reference_kph))


def cell_for(latitude: float, longitude: float, size_mdeg: int) -> tuple[int, int]:
    return (math.floor(latitude * 1000 / size_mdeg),
            math.floor(longitude * 1000 / size_mdeg))


def select_bucket(rows: list[tuple], latitude: float, longitude: float,
                  min_samples: int = MIN_SAMPLES):
    """Choose 75th-percentile bucket at finest sufficient spatial resolution."""
    for grid_size in GRID_SIZES_MDEG:
        lat_cell, lon_cell = cell_for(latitude, longitude, grid_size)
        for size, lat, lon, score, count in rows:
            if ((size, lat, lon) == (grid_size, lat_cell, lon_cell)
                    and count >= min_samples and score is not None):
                return float(score), int(count), grid_size
    return None


def aligned_slots(start: datetime, end: datetime):
    """Yield clipped, contiguous 30-minute UTC slots, including partial ends."""
    current = start.astimezone(timezone.utc)
    stop = end.astimezone(timezone.utc)
    boundary = current.replace(minute=(current.minute // 30) * 30,
                               second=0, microsecond=0) + timedelta(minutes=30)
    while current < stop:
        following = min(boundary, stop)
        yield current, following
        current = following
        boundary += timedelta(minutes=30)


def merge_periods(windows: list[tuple[datetime, datetime]]):
    merged = []
    for start, end in windows:
        if merged and merged[-1][1] == start:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def page(items, query):
    start = query.offset
    end = start + query.limit
    return {
        "items": items[start:end],
        "pagination": {"limit": query.limit, "offset": query.offset,
                       "total": len(items), "has_more": end < len(items)},
    }


class PostgresBaselinePredictor:
    """Fetches versioned, precomputed percentile buckets from S2 PostgreSQL."""

    def __init__(self, database_url: str, model_version: str, connection_factory=None,
                 experimental_preview: bool | None = None):
        if not database_url or not model_version:
            raise ValueError("S2_DATABASE_URL and model version are required")
        self.database_url = database_url
        self.model_version = model_version
        self._connection_factory = connection_factory
        # A deliberately explicit local-only opt-in; cannot bypass production approval.
        self.experimental_preview = (
            os.getenv("APP_ENV", "development").lower() == "development"
            and os.getenv("S2_EXPERIMENTAL_PREVIEW", "0") == "1"
            if experimental_preview is None else experimental_preview
        )

    def _connect(self):
        if self._connection_factory is not None:
            return self._connection_factory()
        import psycopg
        return psycopg.connect(self.database_url, connect_timeout=4,
                               options="-c statement_timeout=3000")

    def _metadata(self, cursor):
        cursor.execute("""
            SELECT training_data_end_at, model_valid_until, approved
            FROM s2_baseline_models WHERE model_version = %s
        """, (self.model_version,))
        return cursor.fetchone()

    def _usable(self, meta, now: datetime):
        return bool(meta and meta[1] > now and (meta[2] or self.experimental_preview))

    def _require_model(self, cursor, now: datetime):
        meta = self._metadata(cursor)
        if not self._usable(meta, now):
            raise ServiceError(503, "SERVICE_UNAVAILABLE",
                               "No approved and unexpired prediction model is available. "
                               "For local diagnostics, explicitly enable the development preview.")
        return meta

    def is_ready(self) -> bool:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                return self._usable(self._metadata(cur), datetime.now(timezone.utc))
        except Exception as exc:
            LOG.warning("S2 prediction readiness error: %s", type(exc).__name__)
            return False

    def _roads(self, cursor, latitude: float, longitude: float, radius_m: float):
        """All distinct measured road segments, with representative centroids."""
        lo_lat, hi_lat, lo_lon, hi_lon = bounding_box(latitude, longitude, radius_m)
        cursor.execute("""
            SELECT centreline_id, AVG(latitude), AVG(longitude)
            FROM traffic_studies
            WHERE latitude BETWEEN %s AND %s AND longitude BETWEEN %s AND %s
            GROUP BY centreline_id
        """, (lo_lat, hi_lat, lo_lon, hi_lon))
        result = []
        for segment, lat, lon in cursor.fetchall():
            lat, lon = float(lat), float(lon)
            dist = distance_m(latitude, longitude, lat, lon)
            if dist <= radius_m:
                result.append((str(segment), lat, lon, dist))
        return result

    def _buckets(self, cursor, roads, instants: list[datetime]):
        """One bounded query serves all segments/times, avoiding per-road SQL calls."""
        if not roads or not instants:
            return {}
        days_hours = {(t.astimezone(TORONTO_TZ).weekday(), t.astimezone(TORONTO_TZ).hour)
                      for t in instants}
        days = sorted({d for d, _ in days_hours})
        hours = sorted({h for _, h in days_hours})
        cells = {}
        for size in GRID_SIZES_MDEG:
            lat_vals, lon_vals = zip(*(cell_for(lat, lon, size) for _, lat, lon, _ in roads))
            cells[size] = (min(lat_vals), max(lat_vals), min(lon_vals), max(lon_vals))
        params = [self.model_version, days, hours]
        clauses = []
        for size in GRID_SIZES_MDEG:
            min_lat, max_lat, min_lon, max_lon = cells[size]
            clauses.append("(grid_size_mdeg = %s AND latitude_cell BETWEEN %s AND %s "
                           "AND longitude_cell BETWEEN %s AND %s)")
            params.extend((size, min_lat, max_lat, min_lon, max_lon))
        cursor.execute("""
            SELECT grid_size_mdeg, latitude_cell, longitude_cell, weekday, local_hour,
                   p75_congestion_score, sample_count
            FROM s2_baseline_buckets
            WHERE model_version = %s AND weekday = ANY(%s) AND local_hour = ANY(%s)
              AND (""" + " OR ".join(clauses) + ")", params)
        return {(size, lat, lon, weekday, hour): (float(score), int(count))
                for size, lat, lon, weekday, hour, score, count in cursor.fetchall()
                if score is not None and count >= MIN_SAMPLES}

    @staticmethod
    def _score(buckets, road, instant: datetime):
        _, lat, lon, _ = road
        local = instant.astimezone(TORONTO_TZ)
        for size in GRID_SIZES_MDEG:
            a, b = cell_for(lat, lon, size)
            value = buckets.get((size, a, b, local.weekday(), local.hour))
            if value is not None:
                return value[0]
        return None

    def _metadata_fields(self, meta, now):
        return {"generated_at": now, "model_version": self.model_version,
                "training_data_end_at": meta[0], "model_valid_until": meta[1],
                "as_of": now, "is_stale": False}

    def predict_point(self, request: PointPredictionRequest) -> PointPredictionResponse:
        now = datetime.now(timezone.utc)
        try:
            with self._connect() as conn, conn.cursor() as cur:
                meta = self._require_model(cur, now)
                roads = self._roads(cur, request.location.latitude, request.location.longitude,
                                    SEARCH_RADIUS_M)
                if not roads:
                    raise ServiceError(404, "COVERAGE_NOT_FOUND",
                                       "No measured Toronto road lies within 1 km of this location.")
                road = min(roads, key=lambda item: item[3])
                buckets = self._buckets(cur, [road], [request.forecast_time])
                score = self._score(buckets, road, request.forecast_time)
                if score is None:
                    raise ServiceError(503, "INSUFFICIENT_HISTORICAL_DATA",
                                       "Not enough comparable weekday/hour observations for this road.")
        except ServiceError:
            raise
        except Exception as exc:
            LOG.warning("S2 prediction database error: %s", type(exc).__name__)
            raise ServiceError(503, "SERVICE_UNAVAILABLE",
                               "S2 prediction storage is unavailable.", retryable=True) from exc
        return PointPredictionResponse.model_validate({
            "prediction": {"location": request.location, "forecast_time": request.forecast_time,
                           "congestion_level": congestion_level(score),
                           "estimated_congestion_score": score},
            **self._metadata_fields(meta, now),
        })

    def area_predictions(self, query: AreaQuery) -> AreaPredictionsResponse:
        now = datetime.now(timezone.utc)
        try:
            with self._connect() as conn, conn.cursor() as cur:
                meta = self._require_model(cur, now)
                roads = self._roads(cur, query.latitude, query.longitude, query.radius_m)
                if not roads:
                    raise ServiceError(404, "COVERAGE_NOT_FOUND",
                                       "No measured Toronto road segments found within the radius.")
                buckets = self._buckets(cur, roads, [query.forecast_time])
        except ServiceError:
            raise
        except Exception as exc:
            LOG.warning("S2 geographic prediction storage error: %s", type(exc).__name__)
            raise ServiceError(503, "SERVICE_UNAVAILABLE", "S2 prediction storage is unavailable.",
                               retryable=True) from exc
        items = []
        for road in roads:
            segment, lat, lon, dist = road
            score = self._score(buckets, road, query.forecast_time)
            items.append({"segment_id": segment, "center": {"latitude": lat, "longitude": lon},
                          "distance_from_center_m": dist, "forecast_time": query.forecast_time,
                          "congestion_level": congestion_level(score) if score is not None else "unknown",
                          "estimated_congestion_score": score})
        if query.sort_by == "congestion_level":
            items.sort(key=lambda x: (CONGESTION_RANK[x["congestion_level"]], x["segment_id"]),
                       reverse=query.sort_order == "desc")
        else:
            items.sort(key=lambda x: (x[query.sort_by], x["segment_id"]),
                       reverse=query.sort_order == "desc")
        return AreaPredictionsResponse.model_validate({
            **page(items, query), **self._metadata_fields(meta, now),
        })

    def high_congestion_periods(self, query: HighPeriodsQuery) -> HighCongestionPeriodsResponse:
        now = datetime.now(timezone.utc)
        slots = list(aligned_slots(query.start_at, query.end_at))
        # Use midpoint to select each 30-minute forecast slot's local weekday/hour.
        instants = [a + (b - a) / 2 for a, b in slots]
        try:
            with self._connect() as conn, conn.cursor() as cur:
                meta = self._require_model(cur, now)
                roads = self._roads(cur, query.latitude, query.longitude, query.radius_m)
                if not roads:
                    raise ServiceError(404, "COVERAGE_NOT_FOUND",
                                       "No measured Toronto road segments found within the radius.")
                buckets = self._buckets(cur, roads, instants)
        except ServiceError:
            raise
        except Exception as exc:
            LOG.warning("S2 high-period prediction storage error: %s", type(exc).__name__)
            raise ServiceError(503, "SERVICE_UNAVAILABLE", "S2 prediction storage is unavailable.",
                               retryable=True) from exc
        high = []
        covered = False
        for (start, end), instant in zip(slots, instants):
            scores = [self._score(buckets, road, instant) for road in roads]
            scores = [score for score in scores if score is not None]
            if scores:
                covered = True
                # Any measured segment in the search area is projected to be high.
                if max(scores) >= 0.60:
                    high.append((start, end))
        if not covered:
            raise ServiceError(503, "INSUFFICIENT_HISTORICAL_DATA",
                               "No historical baseline is available for this area/time window.")
        items = [{"start_at": a, "end_at": b, "congestion_level": "high"}
                 for a, b in merge_periods(high)]
        items.sort(key=lambda item: (item[query.sort_by], item["end_at" if
                                                                query.sort_by == "start_at" else "start_at"]),
                   reverse=query.sort_order == "desc")
        return HighCongestionPeriodsResponse.model_validate({
            **page(items, query), **self._metadata_fields(meta, now),
        })
