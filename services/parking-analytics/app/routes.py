from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

from app.analytics import estimate_occupancy
from app.auth import require_estimator
from app.models import (
    AvailabilityStatus, ErrorResponse, EstimateRequest, EstimateResponse,
    FacilityDetailResponse, FacilityStatus, Pagination, PricingOverview, SearchItem, SearchResponse,
)
from app.service import OCCUPANCY_FRESH_SECONDS, stale


ERROR_RESPONSES = {code: {"model": ErrorResponse} for code in
                   (400, 401, 403, 404, 422, 429, 500, 502, 503, 504)}
router = APIRouter(prefix="/v1/parking", tags=["Parking"], responses=ERROR_RESPONSES)


def validate_query(request, allowed):
    if any(key not in allowed or len(request.query_params.getlist(key)) > 1 for key in request.query_params):
        raise HTTPException(422, "Unsupported or repeated query parameter")


@router.get("/facilities", response_model=SearchResponse, operation_id="searchParkingFacilities")
def search_facilities(
    request: Request,
    min_lat: float = Query(ge=-90, le=90, allow_inf_nan=False),
    min_lon: float = Query(ge=-180, le=180, allow_inf_nan=False),
    max_lat: float = Query(ge=-90, le=90, allow_inf_nan=False),
    max_lon: float = Query(ge=-180, le=180, allow_inf_nan=False),
    facility_status: FacilityStatus | None = None,
    availability_status: AvailabilityStatus | None = None,
    sort_by: Literal["name", "facility_id"] = "name",
    sort_order: Literal["asc", "desc"] = "asc",
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """Search centers, filter and stable-sort before pagination; no source I/O per request."""
    validate_query(request, {"min_lat", "min_lon", "max_lat", "max_lon", "facility_status",
                             "availability_status", "sort_by", "sort_order", "limit", "offset"})
    if min_lat > max_lat or min_lon > max_lon or max_lat - min_lat > 2 or max_lon - min_lon > 2:
        raise HTTPException(422, "Bounds must be ordered and span at most two degrees per axis")
    service = request.app.state.parking
    service.require_catalog()
    now = service.clock()
    results = []
    for facility in service.facilities:
        if not (min_lat <= facility.latitude <= max_lat and min_lon <= facility.longitude <= max_lon):
            continue
        detail = service.detail(facility, now)
        if facility_status is not None and facility.facility_status != facility_status:
            continue
        if availability_status is not None and detail.availability_status != availability_status:
            continue
        price = detail.pricing
        starting_rate = price.rates[0] if price.rates else None
        overview = PricingOverview(**price.model_dump(exclude={"rates", "additional_fees", "tax_included"}),
                                   starting_rate_amount=starting_rate.amount if starting_rate else None,
                                   starting_rate_unit=starting_rate.unit if starting_rate else None)
        results.append(SearchItem(**facility.model_dump(), availability_status=detail.availability_status,
                                  occupancy=detail.occupancy, pricing=overview))
    results.sort(key=lambda item: (getattr(item, sort_by), item.facility_id), reverse=sort_order == "desc")
    total = len(results)
    return SearchResponse(items=results[offset:offset + limit], as_of=now, facility_source="simulated_fixture",
                          pagination=Pagination(limit=limit, offset=offset, total=total, has_more=offset + limit < total))


@router.get("/facilities/{facility_id}", response_model=FacilityDetailResponse, operation_id="getParkingFacility")
def facility_detail(request: Request, facility_id: str = Path(min_length=1, max_length=64,
                                                             pattern=r"^[a-z0-9][a-z0-9_-]*$")):
    validate_query(request, set())
    service = request.app.state.parking
    service.require_catalog()
    facility = next((item for item in service.facilities if item.facility_id == facility_id), None)
    if facility is None:
        raise HTTPException(404, "Parking facility not found")
    return service.detail(facility, service.clock())


@router.post("/occupancy/estimate", response_model=EstimateResponse, operation_id="estimateParkingOccupancy",
             dependencies=[Depends(require_estimator)])
def calculate_occupancy(request: Request, payload: EstimateRequest):
    """Internal computation only: does not write public snapshots or persist raw observations."""
    validate_query(request, set())
    if payload.as_of > request.state.received_at + timedelta(seconds=60):
        raise HTTPException(422, "as_of must not be more than 60 seconds after receipt")
    service = request.app.state.parking
    service.require_catalog()
    items = estimate_occupancy(service.facilities, payload.observations, payload.as_of)
    times = [item.latest_observation_at for item in items if item.latest_observation_at is not None]
    is_stale = not times or any(stale(time, service.clock(), OCCUPANCY_FRESH_SECONDS) for time in times)
    return EstimateResponse(items=items, as_of=payload.as_of, is_stale=is_stale)
