"""S6 v1 response models and bounded, provider-normalized input datasets."""

from datetime import date, datetime, timezone
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import (AfterValidator, AwareDatetime, BaseModel, BeforeValidator,
                      ConfigDict, Field, PlainSerializer, model_validator)


def utc_now():
    return datetime.now(timezone.utc)


def instant(value):
    if not isinstance(value, (str, datetime)) or isinstance(value, str) and "T" not in value.upper():
        raise ValueError("Use a timezone-aware RFC 3339 instant")
    return value


def nonblank(value):
    if not value.strip():
        raise ValueError("Identifiers must not be blank")
    return value


Timestamp = Annotated[AwareDatetime, BeforeValidator(instant),
    AfterValidator(lambda value: value.astimezone(timezone.utc)),
    PlainSerializer(lambda value: value.isoformat().replace("+00:00", "Z"), return_type=str)]
Identifier = Annotated[str, Field(min_length=1, max_length=128), AfterValidator(nonblank)]
Mode = Literal["bus", "tram", "train"]
Coverage = Literal["complete", "partial", "unavailable"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Pagination(Model):
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)
    has_more: bool


class StopDefinition(Model):
    stop_id: Identifier
    name: str = Field(min_length=1, max_length=256)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class Stop(StopDefinition):
    distance_m: float = Field(ge=0)
    modes: list[Mode]
    route_ids: list[Identifier]


class Route(Model):
    route_id: Identifier
    short_name: str = Field(min_length=1, max_length=64)
    long_name: str | None = Field(default=None, max_length=256)
    mode: Mode
    agency_id: Identifier | None


class TimeEstimate(Model):
    scheduled_at: Timestamp
    estimated_at: Timestamp | None
    display_at: Timestamp
    basis: Literal["schedule", "live_estimate"]
    delay_seconds: int | None


class ArrivalDeparture(Model):
    trip_id: Identifier
    route_id: Identifier
    stop_id: Identifier
    vehicle_id: Identifier | None
    destination: str | None = Field(max_length=256)
    arrival: TimeEstimate | None
    departure: TimeEstimate | None


class Provenance(Model):
    last_live_update_at: Timestamp | None
    source_updated_at: Timestamp | None
    service_updated_at: Timestamp | None
    is_live: bool
    is_stale: bool
    live_update_status: Literal["fresh", "stale", "unavailable"]
    data_status: Literal["live", "scheduled", "cached", "simulated", "unavailable"]
    source: str = Field(min_length=1, max_length=128)


class DelayItem(Model):
    trip_id: Identifier
    route_id: Identifier
    stop_id: Identifier | None
    vehicle_id: Identifier | None
    event_type: Literal["arrival", "departure"]
    event_at: Timestamp
    delay_seconds: int = Field(ge=1)


class Disruption(Model):
    disruption_id: Identifier
    kind: Literal["disruption", "cancellation", "outage", "detour"]
    status: Literal["active", "planned", "resolved"]
    message: str = Field(min_length=1, max_length=1000)
    route_ids: list[Identifier] = Field(max_length=10000)
    stop_ids: list[Identifier] = Field(max_length=10000)
    starts_at: Timestamp
    ends_at: Timestamp | None
    updated_at: Timestamp

    @model_validator(mode="after")
    def coherent(self):
        if self.ends_at is not None and self.ends_at < self.starts_at:
            raise ValueError("Disruption end precedes start")
        if len(set(self.route_ids)) != len(self.route_ids) or len(set(self.stop_ids)) != len(self.stop_ids):
            raise ValueError("Duplicate disruption references")
        return self


class Occupancy(Model):
    route_id: Identifier
    vehicle_id: Identifier | None
    occupancy_level: Literal["low", "moderate", "high", "full", "unknown"]
    passenger_count: int | None = Field(ge=0)
    capacity: int | None = Field(ge=1)
    occupancy_percent: float | None = Field(ge=0, le=100)
    observed_at: Timestamp

    @model_validator(mode="after")
    def coherent(self):
        if self.passenger_count is not None and self.capacity is not None:
            if self.passenger_count > self.capacity:
                raise ValueError("Occupancy exceeds capacity")
            if self.occupancy_percent is not None and abs(self.occupancy_percent - 100 * self.passenger_count / self.capacity) > .1:
                raise ValueError("Inconsistent occupancy percentage")
        return self


T = TypeVar("T")


class Collection(Model, Generic[T]):
    items: list[T]
    pagination: Pagination
    as_of: Timestamp


class ScheduleResponse(Collection[T], Generic[T]):
    source: str
    source_updated_at: Timestamp | None
    data_status: Literal["scheduled"] = "scheduled"
    is_live: Literal[False] = False
    is_stale: Literal[False] = False
    schedule_valid_through: Timestamp


class LiveResponse(Collection[T], Generic[T]):
    coverage_status: Coverage
    provenance: Provenance


class ArrivalsResponse(Collection[ArrivalDeparture]):
    stop_id: Identifier
    provenance: Provenance


class HealthResponse(Model):
    service: Literal["transit-analytics"] = "transit-analytics"
    status: Literal["healthy"] = "healthy"


class ReadyResponse(Model):
    service: Literal["transit-analytics"] = "transit-analytics"
    status: Literal["ready"] = "ready"


class ErrorDetail(Model):
    field: str
    reason: str


class Error(Model):
    code: str
    message: str
    request_id: str
    details: list[ErrorDetail]
    retryable: bool
    source: Literal["service", "upstream", "gateway"]


class ErrorEnvelope(Model):
    error: Error


class Agency(Model):
    agency_id: Identifier
    name: str = Field(min_length=1, max_length=256)


class Calendar(Model):
    service_id: Identifier
    start_date: date
    end_date: date
    weekdays: list[int] = Field(min_length=1, max_length=7)
    added_dates: list[date] = Field(default_factory=list, max_length=3660)
    removed_dates: list[date] = Field(default_factory=list, max_length=3660)

    @model_validator(mode="after")
    def coherent(self):
        if self.start_date > self.end_date or any(day not in range(7) for day in self.weekdays):
            raise ValueError("Invalid service calendar")
        if len(set(self.weekdays)) != len(self.weekdays) or set(self.added_dates) & set(self.removed_dates):
            raise ValueError("Conflicting calendar dates")
        return self

    def operates(self, day):
        return day not in self.removed_dates and (day in self.added_dates or
            self.start_date <= day <= self.end_date and day.weekday() in self.weekdays)


class StopTime(Model):
    stop_id: Identifier
    arrival_seconds: int | None = Field(ge=0, lt=172800)
    departure_seconds: int | None = Field(ge=0, lt=172800)

    @model_validator(mode="after")
    def coherent(self):
        if self.arrival_seconds is None and self.departure_seconds is None:
            raise ValueError("At least one scheduled event is required")
        if self.arrival_seconds is not None and self.departure_seconds is not None and self.departure_seconds < self.arrival_seconds:
            raise ValueError("Departure precedes arrival")
        return self


class Trip(Model):
    trip_id: Identifier
    route_id: Identifier
    service_id: Identifier
    destination: str | None = Field(default=None, max_length=256)
    stop_times: list[StopTime] = Field(min_length=1, max_length=1000)


class ScheduleDataset(Model):
    schema_version: Literal[1] = 1
    version: Identifier
    source: Identifier
    simulated: bool
    # Input calendars and seconds since midnight are normalized to UTC upstream.
    timezone: Literal["UTC"] = "UTC"
    source_updated_at: Timestamp | None
    valid_from: Timestamp
    valid_through: Timestamp
    agencies: list[Agency] = Field(min_length=1, max_length=100)
    routes: list[Route] = Field(min_length=1, max_length=10000)
    stops: list[StopDefinition] = Field(min_length=1, max_length=10000)
    calendars: list[Calendar] = Field(min_length=1, max_length=1000)
    trips: list[Trip] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def references(self):
        groups = ((self.agencies, "agency_id"), (self.routes, "route_id"), (self.stops, "stop_id"),
                  (self.calendars, "service_id"), (self.trips, "trip_id"))
        ids = []
        for items, key in groups:
            values = {getattr(item, key) for item in items}
            if len(values) != len(items):
                raise ValueError("Duplicate normalized identifier")
            ids.append(values)
        agencies, routes, stops, calendars, _ = ids
        if self.valid_from >= self.valid_through:
            raise ValueError("Invalid schedule horizon")
        if any(route.agency_id not in agencies for route in self.routes if route.agency_id is not None):
            raise ValueError("Unknown agency")
        for trip in self.trips:
            if trip.route_id not in routes or trip.service_id not in calendars:
                raise ValueError("Unknown trip reference")
            previous = -1
            for item in trip.stop_times:
                times = [value for value in (item.arrival_seconds, item.departure_seconds) if value is not None]
                if item.stop_id not in stops or min(times) < previous:
                    raise ValueError("Invalid stop sequence")
                previous = max(times)
        return self


class TripUpdate(Model):
    trip_id: Identifier
    stop_id: Identifier
    service_date: date
    vehicle_id: Identifier | None
    updated_at: Timestamp | None
    arrival_estimated_at: Timestamp | None
    departure_estimated_at: Timestamp | None
    cancelled: bool = False

    @model_validator(mode="after")
    def ordered_events(self):
        if self.arrival_estimated_at and self.departure_estimated_at and self.departure_estimated_at < self.arrival_estimated_at:
            raise ValueError("Estimated departure precedes arrival")
        return self


class Feed(Model, Generic[T]):
    updated_at: Timestamp | None
    coverage_status: Literal["complete", "partial"]
    items: list[T] = Field(max_length=10000)


class LiveDataset(Model):
    schema_version: Literal[1] = 1
    schedule_version: Identifier
    source: Identifier
    simulated: bool
    source_updated_at: Timestamp | None
    trips: Feed[TripUpdate] | None = None
    disruptions: Feed[Disruption] | None = None
    occupancy: Feed[Occupancy] | None = None
