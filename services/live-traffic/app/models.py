"""The shapes of the data going in and out of the Live Traffic service.

These classes do two jobs:
1. FastAPI uses them to check incoming data and reject bad requests.
2. They show up in the automatic API page at /docs, which is our contract.

Conventions: coordinates are WGS84 decimal degrees, distances are metres,
speeds are km/h, and every time is UTC in ISO 8601 format.
"""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field


class LocationPing(BaseModel):
    """One location update from an active user."""

    # An anonymous ID. It only needs to be the same for the same user.
    user_id: str = Field(min_length=1, max_length=128)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    # Optional. If it is left out, the service uses the time the ping arrived.
    timestamp: AwareDatetime | None = None


class PingAccepted(BaseModel):
    accepted: bool
    timestamp: AwareDatetime


class Timing(BaseModel):
    """How long the service took, so the speed requirements can be checked."""

    retrieve_ms: float
    calculate_ms: float


class AreaCongestion(BaseModel):
    latitude: float
    longitude: float
    radius_m: float
    congestion_level: Literal["low", "moderate", "high", "severe", "unknown"]
    average_speed_kmh: float | None
    free_flow_speed_kmh: float
    users_counted: int
    data_status: Literal["live", "stale_fallback", "no_data"]
    is_live: bool
    newest_data_at: AwareDatetime | None
    data_age_seconds: float | None
    calculated_at: AwareDatetime
    timing_ms: Timing


class RouteSegment(BaseModel):
    segment_number: int
    middle_latitude: float
    middle_longitude: float
    length_m: float
    congestion_level: Literal["low", "moderate", "high", "severe", "unknown"]
    average_speed_kmh: float | None
    users_counted: int
    travel_seconds: float
    delay_seconds: float


class RouteCongestion(BaseModel):
    start_latitude: float
    start_longitude: float
    end_latitude: float
    end_longitude: float
    distance_m: float
    congestion_level: Literal["low", "moderate", "high", "severe", "unknown"]
    average_speed_kmh: float | None
    free_flow_speed_kmh: float
    normal_travel_seconds: float
    estimated_travel_seconds: float
    estimated_delay_seconds: float
    segments_without_data: int
    segments: list[RouteSegment]
    data_status: Literal["live", "stale_fallback", "no_data"]
    is_live: bool
    newest_data_at: AwareDatetime | None
    data_age_seconds: float | None
    calculated_at: AwareDatetime
    timing_ms: Timing


class IngestionStatus(BaseModel):
    """The state of the traffic source and the cache (T089)."""

    source_configured: bool
    source_status: Literal["not_configured", "waiting", "ok", "unavailable"]
    poll_interval_seconds: float
    last_poll_at: AwareDatetime | None
    last_successful_poll_at: AwareDatetime | None
    consecutive_failures: int
    events_published: int
    cached_users: int
    newest_ping_at: AwareDatetime | None
    newest_ping_age_seconds: float | None
    cache_status: Literal["live", "stale", "empty"]
    checked_at: AwareDatetime
