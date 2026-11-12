"""S4 models conforming to the adopted T008 v1 contract."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    AfterValidator, BeforeValidator, AwareDatetime, BaseModel, ConfigDict, Field,
    PlainSerializer, model_validator,
)


def utc_now():
    return datetime.now(timezone.utc)


def iso_timestamp(value):
    if not isinstance(value, (str, datetime)) or isinstance(value, str) and "T" not in value.upper():
        raise ValueError("Timestamp must be a timezone-aware ISO 8601 instant")
    return value


Timestamp = Annotated[AwareDatetime, BeforeValidator(iso_timestamp),
                      AfterValidator(lambda value: value.astimezone(timezone.utc))]
Money = Annotated[Decimal, Field(ge=0, multiple_of=Decimal("0.01"), allow_inf_nan=False),
                  PlainSerializer(float, return_type=float, when_used="json")]
FacilityStatus = Literal["open", "closed", "unknown"]
AvailabilityStatus = Literal["available", "full", "closed", "unknown"]
DataStatus = Literal["live", "cached", "simulated", "unavailable"]
Congestion = Literal["unknown", "low", "moderate", "high", "full"]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Facility(ContractModel):
    facility_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=160)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    capacity: int = Field(gt=0)
    geofence_radius_m: float | None = Field(default=None, gt=0, le=500, allow_inf_nan=False)
    facility_status: FacilityStatus = "unknown"


class Observation(ContractModel):
    subject_id: str = Field(min_length=1, max_length=128, pattern=r"\S")
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False, strict=True)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False, strict=True)
    observed_at: Timestamp


class EstimateRequest(ContractModel):
    observations: list[Observation] = Field(max_length=10000)
    as_of: Timestamp = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def reject_conflicting_observations(self):
        seen = {}
        for observation in self.observations:
            key = (observation.subject_id, observation.observed_at)
            position = (observation.latitude, observation.longitude)
            if key in seen and seen[key] != position:
                raise ValueError("A subject cannot have conflicting locations at one timestamp")
            seen[key] = position
        return self


class OccupancyEstimate(ContractModel):
    facility_id: str
    estimated_occupied_spaces: int | None = Field(ge=0)
    estimated_available_spaces: int | None = Field(ge=0)
    occupancy_ratio: float | None = Field(ge=0, le=1, allow_inf_nan=False)
    congestion: Congestion
    qualifying_subjects: int = Field(ge=0)
    capacity_exceeded: bool
    latest_observation_at: Timestamp | None


class EstimateResponse(ContractModel):
    items: list[OccupancyEstimate]
    as_of: Timestamp
    method: Literal["stationary_unique_subjects_v1"] = "stationary_unique_subjects_v1"
    source: Literal["submitted_observations"] = "submitted_observations"
    is_live: Literal[False] = False
    is_stale: bool
    data_status: Literal["computed"] = "computed"


class SnapshotMetadata(ContractModel):
    service_updated_at: Timestamp | None = None
    is_live: bool = False
    is_stale: bool = True
    source: str = "unavailable"
    data_status: DataStatus = "unavailable"


class OccupancySnapshot(SnapshotMetadata):
    estimated_occupied_spaces: int | None = Field(default=None, ge=0)
    estimated_available_spaces: int | None = Field(default=None, ge=0)
    occupancy_ratio: float | None = Field(default=None, ge=0, le=1)
    congestion: Congestion = "unknown"
    observed_at: Timestamp | None = None


class ParkingRate(ContractModel):
    rate_id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=160)
    amount: Money
    unit: Literal["hour", "day", "entry"]


class ParkingFee(ContractModel):
    fee_id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=160)
    amount: Money


class PricingDetails(SnapshotMetadata):
    currency: Literal["CAD"] | None = None
    rates: list[ParkingRate] = Field(default_factory=list, max_length=100)
    additional_fees: list[ParkingFee] = Field(default_factory=list, max_length=100)
    tax_included: bool | None = None
    source_updated_at: Timestamp | None = None


class PricingOverview(SnapshotMetadata):
    currency: Literal["CAD"] | None = None
    starting_rate_amount: Money | None = None
    starting_rate_unit: Literal["hour", "day", "entry"] | None = None
    source_updated_at: Timestamp | None = None


class SearchItem(Facility):
    availability_status: AvailabilityStatus
    occupancy: OccupancySnapshot
    pricing: PricingOverview


class Pagination(ContractModel):
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int | None = Field(ge=0)
    has_more: bool


class SearchResponse(ContractModel):
    items: list[SearchItem]
    pagination: Pagination
    as_of: Timestamp
    facility_source: str


class FacilityDetailResponse(ContractModel):
    facility: Facility
    availability_status: AvailabilityStatus
    occupancy: OccupancySnapshot
    pricing: PricingDetails
    as_of: Timestamp
    facility_source: str


class HealthResponse(ContractModel):
    service: Literal["parking-analytics"] = "parking-analytics"
    status: Literal["healthy"] = "healthy"


class ReadinessResponse(ContractModel):
    service: Literal["parking-analytics"] = "parking-analytics"
    status: Literal["ready"] = "ready"


class ErrorDetail(ContractModel):
    field: str
    reason: str


class ErrorPayload(ContractModel):
    code: str
    message: str
    request_id: str
    details: list[ErrorDetail]
    retryable: bool
    source: Literal["service", "upstream", "gateway"]


class ErrorResponse(ContractModel):
    error: ErrorPayload
