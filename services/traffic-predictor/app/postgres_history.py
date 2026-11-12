"""PostgreSQL-backed implementation of S2 T021 historical weekday/hour patterns.

Uses real Toronto Midblock road studies and 15-minute speed observations. It
returns *descriptive historical estimates*, it does not fabricate future forecasts.
"""

import logging
import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .errors import ServiceError
from .history import congestion_level
from .models import DAY_NAMES, HistoricalPattern, HistoryPatternsResponse, HistoryQuery, Pagination

LOG = logging.getLogger(__name__)
EARTH_RADIUS_M = 6_371_008.8
DATASET_ID = "toronto_midblock_2025_2029"

# Filtering by an approximate bounding rectangle first makes the existing
# (latitude, longitude) index useful. The Haversine filter then rejects points
# within the rectangle but outside the actual radius.
GREAT_CIRCLE_SQL = """
(2.0 * 6371008.8 * ASIN(SQRT(LEAST(1.0, GREATEST(0.0,
    POWER(SIN(RADIANS(s.latitude - %s) / 2.0), 2.0)
    + COS(RADIANS(%s)) * COS(RADIANS(s.latitude))
      * POWER(SIN(RADIANS(s.longitude - %s) / 2.0), 2.0)
))))) <= %s
"""

NEARBY_SQL = f"""
SELECT s.count_id, s.avg_85th_percentile_speed_kph AS reference_kph
FROM traffic_studies AS s
WHERE s.latitude BETWEEN %s AND %s
  AND s.longitude BETWEEN %s AND %s
  AND {GREAT_CIRCLE_SQL}
"""

PATTERNS_SQL = f"""
WITH nearby AS (
    {NEARBY_SQL}
), qualified AS (
    SELECT
        EXTRACT(ISODOW FROM o.started_at AT TIME ZONE %s)::int - 1 AS weekday,
        EXTRACT(HOUR FROM o.started_at AT TIME ZONE %s)::int AS local_hour,
        GREATEST(0.0, LEAST(1.0,
            1.0 - o.estimated_avg_speed_kph / n.reference_kph
        )) AS score
    FROM speed_observations AS o
    JOIN nearby AS n ON n.count_id = o.count_id
    WHERE o.started_at >= %s AND o.started_at < %s
      AND o.vehicle_count > 0
      AND o.estimated_avg_speed_kph IS NOT NULL
      AND n.reference_kph IS NOT NULL AND n.reference_kph > 0
)
SELECT weekday, local_hour, COUNT(*) AS observations, AVG(score) AS mean_score
FROM qualified
GROUP BY weekday, local_hour
"""

METADATA_SQL = """
SELECT imported_at, refresh_due_at, latest_observation_at
FROM traffic_import_metadata
WHERE dataset_id = %s
"""


def bounding_box(latitude: float, longitude: float, radius_m: float):
    """Approximate rectangle only; precise filtering occurs in SQL."""
    latitude_delta = radius_m / 110_574.0
    longitude_delta = radius_m / max(1.0, 111_320.0 * abs(math.cos(math.radians(latitude))))
    # Toronto's dataset never crosses the antimeridian. For queries near it,
    # the bbox cannot intersect Toronto, so this remains correct for this source.
    return (
        max(-90.0, latitude - latitude_delta),
        min(90.0, latitude + latitude_delta),
        longitude - longitude_delta,
        longitude + longitude_delta,
    )


def nearby_params(query: HistoryQuery):
    lat_low, lat_high, lon_low, lon_high = bounding_box(
        query.latitude, query.longitude, query.radius_m
    )
    return (
        lat_low, lat_high, lon_low, lon_high,
        query.latitude, query.latitude, query.longitude, query.radius_m,
    )


def sort_patterns(patterns: list[HistoricalPattern], query: HistoryQuery):
    # Python's stable sort allows weekday/hour to remain the tie-breaker.
    patterns.sort(key=lambda p: (DAY_NAMES.index(p.day_of_week), p.local_hour))
    reverse = query.sort_order == "desc"
    if query.sort_by == "day_of_week":
        patterns.sort(key=lambda p: DAY_NAMES.index(p.day_of_week), reverse=reverse)
    elif query.sort_by == "local_hour":
        patterns.sort(key=lambda p: p.local_hour, reverse=reverse)
    else:
        # Missing buckets last in both directions, regardless of sort order.
        patterns.sort(key=lambda p: p.average_congestion_score if p.average_congestion_score is not None else
                      (float("-inf") if reverse else float("inf")), reverse=reverse)
    return patterns


class PostgresHistoryStore:
    def __init__(self, database_url: str, connection_factory=None):
        if not database_url:
            raise ValueError("S2_DATABASE_URL is required")
        self.database_url = database_url
        self._connection_factory = connection_factory

    def _connect(self):
        if self._connection_factory is not None:
            return self._connection_factory()
        try:
            import psycopg
        except ImportError as exc:
            raise ServiceError(503, "SERVICE_UNAVAILABLE", "PostgreSQL driver not installed.",
                               retryable=True) from exc
        return psycopg.connect(self.database_url, connect_timeout=4,
                               options="-c statement_timeout=8000")

    @staticmethod
    def _database_error(exc):
        # Log the underlying exception privately but never expose the DSN or
        # database internals through the public API.
        LOG.warning("S2 history database failure: %s", type(exc).__name__)
        return ServiceError(503, "SERVICE_UNAVAILABLE",
                            "Historical traffic database is unavailable or not initialized.",
                            retryable=True)

    def is_ready(self) -> bool:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(METADATA_SQL, (DATASET_ID,))
                return cur.fetchone() is not None
        except ServiceError:
            return False
        except Exception as exc:
            LOG.warning("S2 history readiness check failed: %s", type(exc).__name__)
            return False

    def historical_patterns(self, query: HistoryQuery) -> HistoryPatternsResponse:
        now = datetime.now(timezone.utc)
        start_at, end_at = query.history_window(now)
        tz = ZoneInfo(query.timezone)
        groups = {}
        params = nearby_params(query)
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(METADATA_SQL, (DATASET_ID,))
                metadata = cur.fetchone()
                if metadata is None:
                    raise ServiceError(503, "SERVICE_UNAVAILABLE",
                                       "The Toronto historical dataset has not been imported.",
                                       retryable=True)

                cur.execute(f"SELECT EXISTS({NEARBY_SQL})", params)
                has_coverage = cur.fetchone()[0]
                if not has_coverage:
                    raise ServiceError(404, "COVERAGE_NOT_FOUND",
                                       "No historical traffic study covers the requested location.")

                cur.execute(PATTERNS_SQL, (*params, query.timezone, query.timezone, start_at, end_at))
                for weekday, local_hour, count, average in cur.fetchall():
                    groups[(int(weekday), int(local_hour))] = (int(count), float(average))
        except ServiceError:
            raise
        except Exception as exc:
            raise self._database_error(exc) from exc

        days = [DAY_NAMES.index(query.day_of_week)] if query.day_of_week else range(7)
        hours = [query.local_hour] if query.local_hour is not None else range(24)
        patterns = []
        for weekday in days:
            for hour in hours:
                count, average = groups.get((weekday, hour), (0, None))
                patterns.append(HistoricalPattern(
                    day_of_week=DAY_NAMES[weekday], local_hour=hour,
                    average_congestion_score=average,
                    congestion_level=congestion_level(average),
                    observation_count=count,
                ))

        sort_patterns(patterns, query)
        page = patterns[query.offset:query.offset + query.limit]
        imported_at, refresh_due_at, _latest_observation_at = metadata
        return HistoryPatternsResponse(
            items=page,
            pagination=Pagination(limit=query.limit, offset=query.offset,
                                  total=len(patterns),
                                  has_more=query.offset + len(page) < len(patterns)),
            timezone=tz.key,
            history_start_at=start_at,
            history_end_at=end_at,
            dataset_updated_at=imported_at,
            dataset_refresh_due_at=refresh_due_at,
            as_of=now,
            is_stale=now > refresh_due_at,
        )
