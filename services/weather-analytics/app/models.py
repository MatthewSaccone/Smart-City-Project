"""S5 v1 response and query models (shared/api-contracts/s5-weather-air-quality.openapi.yaml)."""

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import (AfterValidator, AwareDatetime, BaseModel, BeforeValidator,
                      ConfigDict, Field, PlainSerializer)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _instant(value):
    # Reject date-only strings and anything that is not an RFC 3339 instant.
    if not isinstance(value, (str, datetime)) or (isinstance(value, str) and "T" not in value.upper()):
        raise ValueError("Use a timezone-aware RFC 3339 instant")
    return value


def iso_z(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


Timestamp = Annotated[
    AwareDatetime,
    BeforeValidator(_instant),
    AfterValidator(lambda value: value.astimezone(timezone.utc)),
    PlainSerializer(iso_z, return_type=str),
]

Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]
Resolution = Literal["hourly", "daily"]
ConditionCode = Literal["clear", "cloudy", "rain", "snow", "mixed_precipitation", "fog",
                        "thunderstorm", "windy", "unknown"]
DataStatus = Literal["live", "cached", "historical", "simulated", "unavailable"]
TrendMetric = Literal["temperature_c", "relative_humidity_pct", "pm2_5_ug_m3", "aqhi"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


# --------------------------------------------------------------------------- queries

class PointQuery(Model):
    latitude: Latitude
    longitude: Longitude


class PageQuery(Model):
    limit: int = Field(default=25, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    sort_order: Literal["asc", "desc"] = "asc"
    resolution: Resolution = "hourly"


class ForecastQuery(PointQuery, PageQuery):
    start_at: Timestamp
    end_at: Timestamp
    sort_by: Literal["valid_from", "valid_to"] = "valid_from"


class TrendQuery(PointQuery, PageQuery):
    metric: TrendMetric
    start_at: Timestamp
    end_at: Timestamp
    sort_by: Literal["period_start", "period_end"] = "period_start"


class GuidanceQuery(PointQuery):
    travel_at: Timestamp | None = None


# --------------------------------------------------------------------------- responses

class Location(Model):
    latitude: Latitude
    longitude: Longitude


class Pagination(Model):
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int | None = Field(ge=0)
    has_more: bool


class Provenance(Model):
    observed_at: Timestamp | None
    source_updated_at: Timestamp | None
    service_updated_at: Timestamp | None
    is_live: bool
    is_stale: bool
    source: str = Field(min_length=1)
    data_status: DataStatus


class WeatherMeasurements(Model):
    temperature_c: float | None
    feels_like_c: float | None
    relative_humidity_pct: float | None = Field(ge=0, le=100)
    precipitation_rate_mm_h: float | None = Field(ge=0)
    wind_speed_m_s: float | None = Field(ge=0)
    wind_gust_m_s: float | None = Field(ge=0)
    visibility_m: float | None = Field(ge=0)
    condition_code: ConditionCode


class CurrentWeatherResponse(Model):
    location: Location
    as_of: Timestamp
    weather: WeatherMeasurements
    provenance: Provenance


class ForecastInterval(Model):
    valid_from: Timestamp
    valid_to: Timestamp
    temperature_c: float | None
    precipitation_probability_pct: float | None = Field(ge=0, le=100)
    wind_speed_m_s: float | None = Field(ge=0)
    condition_code: ConditionCode


class ForecastResponse(Model):
    location: Location
    as_of: Timestamp
    items: list[ForecastInterval]
    pagination: Pagination
    resolution: Resolution
    issued_at: Timestamp | None
    source_updated_at: Timestamp | None
    service_updated_at: Timestamp | None
    is_live: bool
    is_stale: bool
    source: str = Field(min_length=1)
    data_status: DataStatus


class AirQualityIndex(Model):
    system: Literal["ca_aqhi"] = "ca_aqhi"
    value: float = Field(ge=0)
    category: Literal["low", "moderate", "high", "very_high"]


class PollutantReading(Model):
    pollutant: Literal["pm2_5", "pm10", "o3", "no2", "so2", "co"]
    concentration: float = Field(ge=0)
    unit: Literal["ug_m3", "ppb", "ppm"]


class AirQualityResponse(Model):
    location: Location
    as_of: Timestamp
    air_quality_index: AirQualityIndex | None
    pollutants: list[PollutantReading]
    provenance: Provenance


class TrendBucket(Model):
    period_start: Timestamp
    period_end: Timestamp
    value: float | None
    unit: Literal["c", "pct", "ug_m3", "aqhi"]
    sample_count: int = Field(ge=0)


class TrendResponse(Model):
    location: Location
    metric: TrendMetric
    resolution: Resolution
    items: list[TrendBucket]
    pagination: Pagination
    coverage_status: Literal["complete", "partial", "unavailable"]
    dataset_updated_at: Timestamp | None
    as_of: Timestamp
    source: str = Field(min_length=1)
    is_live: bool
    is_stale: bool
    data_status: Literal["historical"]


class TravelHazard(Model):
    hazard_type: Literal["reduced_visibility", "heavy_precipitation", "strong_wind", "icy_conditions",
                         "extreme_heat", "extreme_cold", "poor_air_quality"]
    severity: Literal["moderate", "high"]
    description: str = Field(min_length=1, max_length=500)


class TravelInput(Model):
    dataset: Literal["weather_observation", "weather_forecast", "air_quality"]
    reference_at: Timestamp | None
    is_stale: bool


class TravelGuidanceResponse(Model):
    location: Location
    travel_at: Timestamp
    as_of: Timestamp
    assessment_status: Literal["assessed", "insufficient_data"]
    risk_level: Literal["low", "moderate", "high", "unknown"]
    hazards: list[TravelHazard]
    recommendations: list[str]
    rule_set_version: str = Field(min_length=1)
    inputs: list[TravelInput]


class ErrorDetail(Model):
    field: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class Error(Model):
    code: str
    message: str
    request_id: str
    details: list[ErrorDetail]
    retryable: bool
    source: Literal["service", "upstream", "gateway"]


class ErrorEnvelope(Model):
    error: Error


class HealthResponse(Model):
    service: Literal["weather-analytics"] = "weather-analytics"
    status: Literal["healthy"] = "healthy"


class ReadyResponse(Model):
    service: Literal["weather-analytics"] = "weather-analytics"
    status: Literal["ready"] = "ready"
