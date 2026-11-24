"""Data contracts for S7, following the adopted OpenAPI v1 specification."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (AfterValidator, AwareDatetime, BaseModel, BeforeValidator,
                      ConfigDict, Field, PlainSerializer, WithJsonSchema, field_validator, model_validator)


FuelType = Literal["regular_87", "midgrade_89", "premium_91", "premium_93", "diesel"]
FUEL_TYPES = frozenset(("regular_87", "midgrade_89", "premium_91", "premium_93", "diesel"))


def utc_now():
    return datetime.now(timezone.utc)


def to_iso(value: datetime):
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def validate_timestamp(value):
    if not isinstance(value, str) or "T" not in value.upper():
        raise ValueError("Use an ISO 8601 timestamp with timezone information")
    return value


Timestamp = Annotated[AwareDatetime, BeforeValidator(validate_timestamp),
                      AfterValidator(lambda value: value.astimezone(timezone.utc))]
Money = Annotated[
    Decimal,
    Field(gt=0, le=20, multiple_of=Decimal("0.001"), allow_inf_nan=False),
    PlainSerializer(float, return_type=float, when_used="json"),
    WithJsonSchema({"type":"number","exclusiveMinimum":0,"maximum":20,"multipleOf":0.001}),
]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PriceReportCreateRequest(ContractModel):
    fuel_type: FuelType
    price_per_litre: Money
    currency: Literal["CAD"]
    observed_at: Timestamp = None

    @field_validator("price_per_litre", mode="before")
    @classmethod
    def reject_numeric_strings(cls, value):
        # JSON body prices must be actual JSON numbers, not numeric strings.
        if isinstance(value, (str, bool)):
            raise ValueError('price_per_litre must be a JSON number')
        return value

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null(cls, value):
        # OpenAPI marks observed_at optional, not nullable.
        if isinstance(value, dict) and 'observed_at' in value and value['observed_at'] is None:
            raise ValueError('observed_at must be omitted or contain a timezone-aware timestamp')
        return value


class PriceQuote(ContractModel):
    fuel_type: FuelType
    price_per_litre: Money
    currency: Literal["CAD"] = "CAD"
    price_unit: Literal["CAD/L"] = "CAD/L"
    price_report_id: str
    submitted_at: str
    observed_at: str | None
    freshness_basis: Literal["observed_at", "submitted_at"]
    verification_status: Literal["unverified", "reviewed"] = "unverified"
    source: Literal["user_report"] = "user_report"
    data_status: Literal["user_report"] = "user_report"
    is_live: Literal[False] = False
    is_stale: bool


class StationSearchItem(ContractModel):
    station_id: str
    name: str
    address: str
    latitude: float
    longitude: float
    distance_m: float
    available_fuel_types: list[FuelType]
    prices: list[PriceQuote]
    last_accepted_report_at: str | None
    price_coverage_status: Literal["available", "partial", "stale_only", "unavailable"]
    is_stale: bool


class StationDetail(StationSearchItem):
    distance_m: None = None
    as_of: str
    source: Literal["station_catalog_and_user_reports"] = "station_catalog_and_user_reports"
    data_status: Literal["user_report", "unavailable"]
    is_live: Literal[False] = False


class Pagination(ContractModel):
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int | None
    has_more: bool


class StationSearchResponse(ContractModel):
    items: list[StationSearchItem]
    pagination: Pagination
    as_of: str
    source: Literal["station_catalog_and_user_reports"] = "station_catalog_and_user_reports"
    data_status: Literal["user_report", "unavailable"]
    is_live: Literal[False] = False
    is_stale: bool


class ComparisonItem(ContractModel):
    station_id: str
    name: str
    distance_m: float
    quote: PriceQuote


class ComparisonSummary(ContractModel):
    eligible_station_count: int = Field(ge=0)
    min_price_per_litre: Money | None
    max_price_per_litre: Money | None
    mean_price_per_litre: Money | None
    spread_per_litre: float | None
    comparison_status: Literal["available", "insufficient_data"]


class ComparisonResponse(ContractModel):
    fuel_type: FuelType
    currency: Literal["CAD"] = "CAD"
    price_unit: Literal["CAD/L"] = "CAD/L"
    items: list[ComparisonItem]
    pagination: Pagination
    summary: ComparisonSummary
    as_of: str
    source: Literal["user_reports"] = "user_reports"
    data_status: Literal["user_report", "unavailable"]
    is_live: Literal[False] = False
    is_stale: bool


class PriceReportResponse(ContractModel):
    price_report_id: str
    station_id: str
    fuel_type: FuelType
    price_per_litre: Money
    currency: Literal["CAD"] = "CAD"
    price_unit: Literal["CAD/L"] = "CAD/L"
    observed_at: str | None
    submitted_at: str
    verification_status: Literal["unverified"] = "unverified"
    source: Literal["user_report"] = "user_report"
    data_status: Literal["user_report"] = "user_report"
    is_live: Literal[False] = False
    is_stale: bool = False


class ErrorDetail(ContractModel):
    field: str
    reason: str


class Error(ContractModel):
    code: str
    message: str
    request_id: str
    details: list[ErrorDetail]
    retryable: bool
    source: Literal["service"] = "service"


class ErrorEnvelope(ContractModel):
    error: Error


class HealthResponse(ContractModel):
    service: Literal["gasoline-analytics"] = "gasoline-analytics"
    status: Literal["healthy"] = "healthy"


class ReadyResponse(ContractModel):
    service: Literal["gasoline-analytics"] = "gasoline-analytics"
    status: Literal["ready"] = "ready"
