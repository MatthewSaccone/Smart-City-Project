"""Parking-owned schemas. These do not change the shared contract templates."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class Facility(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    facility_id: str
    name: str
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    capacity: int = Field(gt=0)
    geofence_radius_m: float = Field(gt=0, le=500, allow_inf_nan=False)


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    subject_id: str = Field(min_length=1, max_length=128, pattern=r"\S")
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    observed_at: AwareDatetime


class EstimateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observations: list[Observation] = Field(max_length=10000)
    as_of: AwareDatetime | None = None

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


class OccupancyEstimate(BaseModel):
    facility_id: str
    estimated_occupied_spaces: int | None
    estimated_available_spaces: int | None
    occupancy_ratio: float | None
    congestion: Literal["unknown", "low", "moderate", "high", "full"]
    qualifying_subjects: int
    capacity_exceeded: bool
    latest_observation_at: AwareDatetime | None


class EstimateResponse(BaseModel):
    source: Literal["simulated_fixture", "submitted_observations"]
    facility_source: Literal["simulated_fixture"] = "simulated_fixture"
    is_live: bool = False
    as_of: AwareDatetime
    method: str = "stationary_unique_subjects_v1"
    occupancy: list[OccupancyEstimate]


class SearchItem(Facility):
    occupancy: OccupancyEstimate


class SearchResponse(BaseModel):
    source: Literal["simulated_fixture"] = "simulated_fixture"
    is_live: bool = False
    as_of: AwareDatetime
    facilities: list[SearchItem]
