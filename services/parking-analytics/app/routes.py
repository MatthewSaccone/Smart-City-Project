from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from app.analytics import estimate_occupancy
from app.catalog import load_demo
from app.models import EstimateRequest, EstimateResponse, SearchItem, SearchResponse


router = APIRouter(prefix="/v1/parking", tags=["Parking T039 / T041"])
FACILITIES, DEMO_OBSERVATIONS, DEMO_AS_OF = load_demo()


@router.get("/facilities", response_model=SearchResponse)
def search_facilities(
    min_lat: float = Query(ge=-90, le=90, allow_inf_nan=False),
    min_lon: float = Query(ge=-180, le=180, allow_inf_nan=False),
    max_lat: float = Query(ge=-90, le=90, allow_inf_nan=False),
    max_lon: float = Query(ge=-180, le=180, allow_inf_nan=False),
):
    """Search facility centers in inclusive bounds; occupancy is a simulated replay."""
    if min_lat > max_lat or min_lon > max_lon:
        raise HTTPException(422, "Bounds must be ordered; split areas crossing the antimeridian")
    # Always assign observations across the whole catalog, even for a narrow search.
    estimates = {item.facility_id: item for item in estimate_occupancy(
        FACILITIES, DEMO_OBSERVATIONS, DEMO_AS_OF,
    )}
    return SearchResponse(as_of=DEMO_AS_OF, facilities=[
        SearchItem(**facility.model_dump(), occupancy=estimates[facility.facility_id])
        for facility in FACILITIES
        if min_lat <= facility.latitude <= max_lat and min_lon <= facility.longitude <= max_lon
    ])


@router.post("/occupancy/estimate", response_model=EstimateResponse)
def calculate_occupancy(request: EstimateRequest):
    """Calculate a batch estimate; inputs are neither persisted nor asserted live."""
    as_of = request.as_of or datetime.now(timezone.utc)
    return EstimateResponse(source="submitted_observations", as_of=as_of,
                            occupancy=estimate_occupancy(FACILITIES, request.observations, as_of))
