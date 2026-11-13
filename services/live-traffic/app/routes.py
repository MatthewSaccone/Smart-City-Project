
"""The HTTP endpoints of the Live Traffic service (S1)."""

import hashlib
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from app.analytics import area_congestion, distance_m, route_congestion
from app.auth import Principal, get_current_principal
from app.ingestion import (
    find_timestamp_problem,
    get_ingestion_status,
    publish_location,
    store,
)
from app.models import (
    AreaCongestion,
    IngestionStatus,
    LocationPing,
    PingAccepted,
    RouteCongestion,
)
from app.settings import MAX_RADIUS_M, MAX_ROUTE_DISTANCE_M


router = APIRouter(prefix="/v1/traffic", tags=["Live Traffic (S1)"])


def milliseconds_between(start, end):
    return round((end - start) * 1000, 3)


def scoped_user_id(subject: str, device_id: str) -> str:
    """Create a stable, opaque participant ID scoped to an authenticated user."""
    identity = f"{subject}\0{device_id}".encode("utf-8")
    return hashlib.sha256(identity).hexdigest()


@router.post("/locations", response_model=PingAccepted, status_code=201)
def submit_location(
    ping: LocationPing,
    principal: Principal = Depends(get_current_principal),
):
    """Save a location update for an authenticated user's device."""
    now = datetime.now(timezone.utc)

    timestamp = ping.timestamp if ping.timestamp is not None else now

    problem = find_timestamp_problem(timestamp, now)
    if problem is not None:
        raise HTTPException(422, problem)

    participant_id = scoped_user_id(principal.subject, ping.user_id)

    publish_location(
        participant_id,
        ping.latitude,
        ping.longitude,
        timestamp,
        "user",
        now,
    )

    return {"accepted": True, "timestamp": timestamp}


@router.get("/status", response_model=IngestionStatus)
def get_status(
    principal: Principal = Depends(get_current_principal),
):
    """Is the traffic source answering, and how fresh is the cached data?"""
    return get_ingestion_status(datetime.now(timezone.utc))


@router.get("/congestion/area", response_model=AreaCongestion)
def get_area_congestion(
    latitude: float = Query(ge=-90, le=90, allow_inf_nan=False),
    longitude: float = Query(ge=-180, le=180, allow_inf_nan=False),
    radius_m: float = Query(gt=0, le=MAX_RADIUS_M, allow_inf_nan=False),
    principal: Principal = Depends(get_current_principal),
):
    """Congestion within radius_m metres of a location."""
    started = time.perf_counter()
    now = datetime.now(timezone.utc)

    pings_by_user = store.get_all_pings(now)
    retrieved = time.perf_counter()

    result = area_congestion(pings_by_user, latitude, longitude, radius_m, now)
    calculated = time.perf_counter()

    result["timing_ms"] = {
        "retrieve_ms": milliseconds_between(started, retrieved),
        "calculate_ms": milliseconds_between(retrieved, calculated),
    }
    return result


@router.get("/congestion/route", response_model=RouteCongestion)
def get_route_congestion(
    start_latitude: float = Query(ge=-90, le=90, allow_inf_nan=False),
    start_longitude: float = Query(ge=-180, le=180, allow_inf_nan=False),
    end_latitude: float = Query(ge=-90, le=90, allow_inf_nan=False),
    end_longitude: float = Query(ge=-180, le=180, allow_inf_nan=False),
    principal: Principal = Depends(get_current_principal),
):
    """Congestion and estimated delay along the straight line between two points."""
    started = time.perf_counter()
    now = datetime.now(timezone.utc)

    distance = distance_m(
        start_latitude,
        start_longitude,
        end_latitude,
        end_longitude,
    )

    if distance < 1:
        raise HTTPException(422, "start and end must be different points")

    if distance > MAX_ROUTE_DISTANCE_M:
        raise HTTPException(422, "start and end are too far apart (limit is 50 km)")

    pings_by_user = store.get_all_pings(now)
    retrieved = time.perf_counter()

    result = route_congestion(
        pings_by_user,
        start_latitude,
        start_longitude,
        end_latitude,
        end_longitude,
        now,
    )
    calculated = time.perf_counter()

    result["timing_ms"] = {
        "retrieve_ms": milliseconds_between(started, retrieved),
        "calculate_ms": milliseconds_between(retrieved, calculated),
    }
    return result
