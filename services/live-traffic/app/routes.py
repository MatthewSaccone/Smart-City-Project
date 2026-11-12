"""The HTTP endpoints of the Live Traffic service (S1).

    POST /v1/traffic/locations          a user sends where they are
    GET  /v1/traffic/congestion/area    congestion inside a radius (T017)
    GET  /v1/traffic/congestion/route   congestion and delay between two points (T013, T015)
    GET  /v1/traffic/status             is the traffic source working, is the cache fresh (T089)

Each GET does the same three things:
    1. get the stored pings          (must take under 500 ms  - T014)
    2. run the calculation           (must take under 1000 ms - T012)
    3. send the answer back          (must take under 1000 ms - T016)

The answer is worked out again on every request, so it always uses the
newest pings (T019).
"""

import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from app.analytics import area_congestion, distance_m, route_congestion
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


@router.post("/locations", response_model=PingAccepted, status_code=201)
def submit_location(ping: LocationPing):
    """Save one location update from an active user."""
    now = datetime.now(timezone.utc)

    if ping.timestamp is None:
        timestamp = now
    else:
        timestamp = ping.timestamp

    problem = find_timestamp_problem(timestamp, now)
    if problem is not None:
        raise HTTPException(422, problem)

    # Publish it. The subscriber in ingestion.py saves it in the store.
    publish_location(ping.user_id, ping.latitude, ping.longitude, timestamp, "user", now)

    return {"accepted": True, "timestamp": timestamp}


@router.get("/status", response_model=IngestionStatus)
def get_status():
    """Is the traffic source answering, and how fresh is the cached data?"""
    return get_ingestion_status(datetime.now(timezone.utc))


@router.get("/congestion/area", response_model=AreaCongestion)
def get_area_congestion(
    latitude: float = Query(ge=-90, le=90, allow_inf_nan=False),
    longitude: float = Query(ge=-180, le=180, allow_inf_nan=False),
    radius_m: float = Query(gt=0, le=MAX_RADIUS_M, allow_inf_nan=False),
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
):
    """Congestion and estimated delay along the straight line between two points."""
    started = time.perf_counter()
    now = datetime.now(timezone.utc)

    distance = distance_m(start_latitude, start_longitude, end_latitude, end_longitude)

    if distance < 1:
        raise HTTPException(422, "start and end must be different points")

    if distance > MAX_ROUTE_DISTANCE_M:
        raise HTTPException(422, "start and end are too far apart (limit is 50 km)")

    pings_by_user = store.get_all_pings(now)
    retrieved = time.perf_counter()

    result = route_congestion(
        pings_by_user, start_latitude, start_longitude, end_latitude, end_longitude, now,
    )
    calculated = time.perf_counter()

    result["timing_ms"] = {
        "retrieve_ms": milliseconds_between(started, retrieved),
        "calculate_ms": milliseconds_between(retrieved, calculated),
    }
    return result
