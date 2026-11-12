
import math

from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


IncidentType = Literal[
    "construction",
    "accident",
    "other_traffic",
]

IncidentStatus = Literal[
    "active",
    "resolved",
    "completed",
    "inaccurate",
    "withdrawn",
]

PublicIncidentStatus = Literal[
    "active",
    "resolved",
    "completed",
    "inaccurate",
]

PatchIncidentStatus = Literal[
    "active",
    "resolved",
    "completed",
    "inaccurate",
]

SortBy = Literal[
    "submitted_at",
    "updated_at",
    "distance_from_center_m",
]

SortOrder = Literal["asc", "desc"]

ListStatus = Literal[
    "active",
    "resolved",
    "completed",
    "inaccurate",
    "all",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IncidentLocation(StrictModel):
    latitude: float = Field(
        strict=True,
        ge=-90,
        le=90,
    )
    longitude: float = Field(
        strict=True,
        ge=-180,
        le=180,
    )

    @field_validator("latitude", "longitude")
    @classmethod
    def validate_finite_coordinates(cls, value):
        if not math.isfinite(value):
            raise ValueError("Coordinate must be finite")

        return value


class IncidentCreateRequest(StrictModel):
    incident_type: IncidentType
    location: IncidentLocation
    description: str = Field(
        min_length=10,
        max_length=2000,
    )
    observed_at: datetime | None = None

    @field_validator("description", mode="before")
    @classmethod
    def trim_description(cls, value):
        if isinstance(value, str):
            return value.strip()

        return value

    @field_validator("observed_at", mode="before")
    @classmethod
    def validate_observed_at_format(cls, value):
        if value is None or isinstance(value, datetime):
            return value

        if not isinstance(value, str):
            raise ValueError(
                "observed_at must be an ISO 8601 timestamp string"
            )

        return value

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(cls, value):
        if value is None:
            return value

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "observed_at must include a timezone"
            )

        now = datetime.now(timezone.utc)

        if value > now + timedelta(minutes=5):
            raise ValueError(
                "observed_at cannot be more than "
                "five minutes in the future"
            )

        return value.astimezone(timezone.utc)


class IncidentPatchRequest(StrictModel):
    incident_type: IncidentType | None = None
    location: IncidentLocation | None = None
    description: str | None = Field(
        default=None,
        min_length=10,
        max_length=2000,
    )
    status: PatchIncidentStatus | None = None

    @field_validator("description", mode="before")
    @classmethod
    def trim_description(cls, value):
        if isinstance(value, str):
            return value.strip()

        return value

    @model_validator(mode="after")
    def validate_patch_fields(self):
        if not self.model_fields_set:
            raise ValueError(
                "At least one mutable field must be provided"
            )

        for field_name in self.model_fields_set:
            if getattr(self, field_name) is None:
                raise ValueError(
                    f"{field_name} cannot be null"
                )

        return self


class IncidentListQuery(StrictModel):
    latitude: float = Field(
        ge=-90,
        le=90,
    )
    longitude: float = Field(
        ge=-180,
        le=180,
    )
    radius_m: float = Field(
        gt=0,
        le=25000,
    )
    incident_type: IncidentType | None = None
    status: ListStatus = "active"
    sort_by: SortBy = "submitted_at"
    sort_order: SortOrder = "desc"
    limit: int = Field(
        default=25,
        ge=1,
        le=100,
    )
    offset: int = Field(
        default=0,
        ge=0,
    )

    @field_validator("latitude", "longitude", "radius_m")
    @classmethod
    def validate_finite_query_values(cls, value):
        if not math.isfinite(value):
            raise ValueError("Value must be finite")

        return value


class IncidentReportResponse(StrictModel):
    incident_id: UUID
    incident_type: IncidentType
    status: PublicIncidentStatus
    location: IncidentLocation
    description: str
    observed_at: datetime | None
    submitted_at: datetime
    updated_at: datetime

    source: Literal["user_report"] = "user_report"
    is_live: Literal[False] = False
    is_stale: bool
    data_status: Literal["user_report"] = "user_report"
    as_of: datetime


class IncidentListItem(StrictModel):
    incident_id: UUID
    incident_type: IncidentType
    status: PublicIncidentStatus
    location: IncidentLocation
    submitted_at: datetime
    updated_at: datetime

    source: Literal["user_report"] = "user_report"
    is_live: Literal[False] = False
    is_stale: bool
    data_status: Literal["user_report"] = "user_report"
    distance_from_center_m: float = Field(
        ge=0,
    )


class Pagination(StrictModel):
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int | None = Field(ge=0)
    has_more: bool


class IncidentListResponse(StrictModel):
    items: list[IncidentListItem]
    pagination: Pagination
    as_of: datetime


class HealthResponse(StrictModel):
    service: Literal["construction-incidents"]
    status: Literal["healthy"]


class ReadyResponse(StrictModel):
    service: Literal["construction-incidents"]
    status: Literal["ready"]
