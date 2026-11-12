"""S2 public API data models.

Contract: shared/api-contracts/s2-traffic-predictor.openapi.yaml
The classes in this module define data shapes, not the prediction algorithm.
"""

from datetime import datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


DAY_NAMES = (
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"
)
DayName = Literal[
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"
]
CongestionLevel = Literal["low", "moderate", "high", "unknown"]
SortOrder = Literal["asc", "desc"]
MAX_FORECAST_DAYS = 7


def utc_z(value: datetime) -> str:
    """Serialize a timezone-aware datetime as ISO 8601 UTC with a trailing Z."""
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class ContractModel(BaseModel):
    """Shared S2 JSON rules: no undocumented fields and standardized UTC output."""

    model_config = ConfigDict(
        extra="forbid",
        json_encoders={datetime: utc_z},
    )


def validate_forecast_time(value: datetime) -> datetime:
    now = datetime.now(timezone.utc)
    if not now < value <= now + timedelta(days=MAX_FORECAST_DAYS):
        raise ValueError("forecast_time must be in the future and within seven days")
    return value


class Coordinate(ContractModel):
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)


class Pagination(ContractModel):
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int | None = Field(ge=0)
    has_more: bool


class HistoricalPattern(ContractModel):
    day_of_week: DayName
    local_hour: int = Field(ge=0, le=23)
    average_congestion_score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    congestion_level: CongestionLevel
    observation_count: int = Field(ge=0)


class HistoryPatternsResponse(ContractModel):
    items: list[HistoricalPattern]
    pagination: Pagination
    timezone: str
    history_start_at: AwareDatetime
    history_end_at: AwareDatetime
    dataset_updated_at: AwareDatetime
    dataset_refresh_due_at: AwareDatetime
    as_of: AwareDatetime
    source: Literal["historical_traffic_dataset"] = "historical_traffic_dataset"
    is_live: Literal[False] = False
    is_stale: bool
    data_status: Literal["historical"] = "historical"


class PointPredictionRequest(ContractModel):
    location: Coordinate
    forecast_time: AwareDatetime

    @field_validator("forecast_time")
    @classmethod
    def require_future_forecast(cls, value: datetime) -> datetime:
        return validate_forecast_time(value)


class PointPrediction(ContractModel):
    location: Coordinate
    forecast_time: AwareDatetime
    congestion_level: CongestionLevel
    estimated_congestion_score: float | None = Field(ge=0, le=1, allow_inf_nan=False)


class PredictionMetadata(ContractModel):
    generated_at: AwareDatetime
    model_version: str = Field(min_length=1)
    training_data_end_at: AwareDatetime
    model_valid_until: AwareDatetime
    as_of: AwareDatetime
    source: Literal["historical_traffic_model"] = "historical_traffic_model"
    is_live: Literal[False] = False
    is_stale: bool
    data_status: Literal["predicted"] = "predicted"


class PointPredictionResponse(PredictionMetadata):
    prediction: PointPrediction


class HighCongestionPeriod(ContractModel):
    start_at: AwareDatetime
    end_at: AwareDatetime
    congestion_level: Literal["high"] = "high"


class HighCongestionPeriodsResponse(PredictionMetadata):
    items: list[HighCongestionPeriod]
    pagination: Pagination


class AreaPredictionItem(ContractModel):
    segment_id: str = Field(min_length=1)
    center: Coordinate
    distance_from_center_m: float = Field(ge=0, allow_inf_nan=False)
    forecast_time: AwareDatetime
    congestion_level: CongestionLevel
    estimated_congestion_score: float | None = Field(ge=0, le=1, allow_inf_nan=False)


class AreaPredictionsResponse(PredictionMetadata):
    items: list[AreaPredictionItem]
    pagination: Pagination


class ErrorDetail(ContractModel):
    field: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ErrorCore(ContractModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    message: str
    request_id: str
    details: list[ErrorDetail]
    retryable: bool
    source: Literal["service", "upstream", "gateway"]


class ErrorEnvelope(ContractModel):
    error: ErrorCore


class HealthResponse(ContractModel):
    service: Literal["traffic-predictor"] = "traffic-predictor"
    status: Literal["healthy"] = "healthy"


class ReadyResponse(ContractModel):
    service: Literal["traffic-predictor"] = "traffic-predictor"
    status: Literal["ready"] = "ready"


# Query parameter models are internal input DTOs for the three GET analytics routes.
# They keep cross-field checks in one place, alongside the JSON body validation.
class SearchQuery(ContractModel):
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    radius_m: float = Field(gt=0, le=10000, allow_inf_nan=False)


class PagingQuery(ContractModel):
    limit: int = Field(default=25, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class HistoryQuery(SearchQuery, PagingQuery):
    radius_m: float = Field(default=500, gt=0, le=10000, allow_inf_nan=False)
    timezone: str
    day_of_week: DayName | None = None
    local_hour: int | None = Field(default=None, ge=0, le=23)
    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None
    sort_by: Literal["day_of_week", "local_hour", "average_congestion_score"] = "day_of_week"
    sort_order: SortOrder = "asc"

    @field_validator("timezone")
    @classmethod
    def valid_iana_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value

    @model_validator(mode="after")
    def validate_history_window(self):
        if (self.start_at is None) != (self.end_at is None):
            raise ValueError("start_at and end_at must be supplied together")
        if self.start_at is not None:
            if not self.start_at < self.end_at:
                raise ValueError("start_at must precede end_at")
            if self.end_at > datetime.now(timezone.utc):
                raise ValueError("historical end_at cannot be in the future")
            if self.end_at - self.start_at > timedelta(days=365):
                raise ValueError("historical range cannot exceed 365 days")
        return self

    def history_window(self, now: datetime) -> tuple[datetime, datetime]:
        if self.start_at is not None:
            return self.start_at, self.end_at
        return now - timedelta(days=90), now


class HighPeriodsQuery(SearchQuery, PagingQuery):
    start_at: AwareDatetime
    end_at: AwareDatetime
    sort_by: Literal["start_at", "end_at"] = "start_at"
    sort_order: SortOrder = "asc"

    @model_validator(mode="after")
    def validate_window(self):
        now = datetime.now(timezone.utc)
        if not now < self.start_at < self.end_at <= now + timedelta(days=7):
            raise ValueError("search window must be future, increasing, and within seven days")
        if self.end_at - self.start_at > timedelta(hours=48):
            raise ValueError("high-congestion search window cannot exceed 48 hours")
        return self


class AreaQuery(SearchQuery, PagingQuery):
    forecast_time: AwareDatetime
    sort_by: Literal[
        "distance_from_center_m", "segment_id", "congestion_level"
    ] = "distance_from_center_m"
    sort_order: SortOrder = "asc"

    @field_validator("forecast_time")
    @classmethod
    def require_future_forecast(cls, value: datetime) -> datetime:
        return validate_forecast_time(value)
