"""The six public, read-only operations from the adopted S6 v1 contract."""

from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query, Request
from pydantic import Field, model_validator

from app.models import (ArrivalsResponse, DelayItem, Disruption, Identifier, LiveResponse,
    ErrorEnvelope, Model, Mode, Occupancy, Route, ScheduleResponse, Stop, Timestamp)


class PageQuery(Model):
    limit: int = Field(default=25, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    sort_order: Literal["asc", "desc"] = "asc"


class AreaQuery(PageQuery):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_m: float = Field(gt=0, le=20000)
    mode: Mode | None = None


class StopsQuery(AreaQuery):
    sort_by: Literal["distance_m", "name"] = "distance_m"


class RoutesQuery(AreaQuery):
    sort_by: Literal["short_name", "route_id"] = "short_name"


class ArrivalsQuery(PageQuery):
    start_at: Timestamp | None = None
    window_minutes: int = Field(default=60, ge=1, le=180)
    route_id: Identifier | None = None
    sort_by: Literal["display_at"] = "display_at"


class ScopeQuery(PageQuery):
    route_id: Identifier | None = None
    stop_id: Identifier | None = None
    sort_order: Literal["asc", "desc"] = "desc"

    @model_validator(mode="after")
    def scope_required(self):
        if self.route_id is None and self.stop_id is None:
            raise ValueError("Supply at least one of route_id or stop_id")
        return self


class DelaysQuery(ScopeQuery):
    sort_by: Literal["event_at", "delay_seconds"] = "event_at"


class DisruptionsQuery(ScopeQuery):
    status: Literal["active", "planned", "resolved"] | None = None
    kind: Literal["disruption", "cancellation", "outage", "detour"] | None = None
    sort_by: Literal["updated_at", "starts_at"] = "updated_at"


class OccupancyQuery(PageQuery):
    route_id: Identifier | None = None
    vehicle_id: Identifier | None = None
    sort_by: Literal["observed_at", "route_id"] = "observed_at"
    sort_order: Literal["asc", "desc"] = "desc"

    @model_validator(mode="after")
    def scope_required(self):
        if self.route_id is None and self.vehicle_id is None:
            raise ValueError("Supply at least one of route_id or vehicle_id")
        return self


router = APIRouter(prefix="/v1/transit", tags=["Public Transit"],
    responses={code: {"model": ErrorEnvelope} for code in (400, 404, 422, 429, 500, 502, 503, 504)})


@router.get("/stops", response_model=ScheduleResponse[Stop], operation_id="listTransitStops")
async def stops(request: Request, query: Annotated[StopsQuery, Query()]):
    return request.app.state.transit.search("stops", query)


@router.get("/routes", response_model=ScheduleResponse[Route], operation_id="listTransitRoutes")
async def routes(request: Request, query: Annotated[RoutesQuery, Query()]):
    return request.app.state.transit.search("routes", query)


@router.get("/stops/{stop_id}/arrivals", response_model=ArrivalsResponse, operation_id="getStopArrivals")
async def arrivals(request: Request, stop_id: Annotated[Identifier, Path()], query: Annotated[ArrivalsQuery, Query()]):
    return request.app.state.transit.arrivals(stop_id, query)


@router.get("/delays", response_model=LiveResponse[DelayItem], operation_id="listTransitDelays")
async def delays(request: Request, query: Annotated[DelaysQuery, Query()]):
    return request.app.state.transit.observations("delays", query)


@router.get("/disruptions", response_model=LiveResponse[Disruption], operation_id="listTransitDisruptions")
async def disruptions(request: Request, query: Annotated[DisruptionsQuery, Query()]):
    return request.app.state.transit.observations("disruptions", query)


@router.get("/occupancy", response_model=LiveResponse[Occupancy], operation_id="listTransitOccupancy")
async def occupancy(request: Request, query: Annotated[OccupancyQuery, Query()]):
    return request.app.state.transit.observations("occupancy", query)
