"""Deterministic, stateless estimates from recent time/location observations."""

from collections import defaultdict
from datetime import datetime, timedelta
from math import asin, cos, radians, sin, sqrt

from app.models import Facility, Observation, OccupancyEstimate


WINDOW_SECONDS = 120
FRESH_SECONDS = 60
MIN_DWELL_SECONDS = 30
MAX_GAP_SECONDS = 60


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1, lat2, lon2 = map(radians, (lat1, lon1, lat2, lon2))
    a = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 6371000 * 2 * asin(sqrt(min(1.0, max(0.0, a))))


def estimate_occupancy(
    facilities: list[Facility], observations: list[Observation], as_of: datetime,
) -> list[OccupancyEstimate]:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must include a timezone")
    cutoff = as_of - timedelta(seconds=WINDOW_SECONDS)
    subjects = defaultdict(dict)
    for observation in observations:
        if cutoff <= observation.observed_at <= as_of:
            # Identical repeated samples do not create extra dwell time or vehicles.
            subjects[observation.subject_id][observation.observed_at] = observation

    def assignment(observation):
        candidates = []
        for facility in facilities:
            distance = distance_m(observation.latitude, observation.longitude,
                                  facility.latitude, facility.longitude)
            if facility.geofence_radius_m is not None and distance <= facility.geofence_radius_m:
                candidates.append((distance, facility.facility_id))
        return min(candidates)[1] if candidates else None

    counted = defaultdict(list)
    for samples in subjects.values():
        ordered = sorted(samples.values(), key=lambda item: item.observed_at)
        latest = ordered[-1]
        if (as_of - latest.observed_at).total_seconds() > FRESH_SECONDS:
            continue
        facility_id = assignment(latest)
        if facility_id is None:
            continue
        earliest = latest
        for previous in reversed(ordered[:-1]):
            if (earliest.observed_at - previous.observed_at).total_seconds() > MAX_GAP_SECONDS:
                break
            if assignment(previous) != facility_id:
                break
            earliest = previous
        if (latest.observed_at - earliest.observed_at).total_seconds() >= MIN_DWELL_SECONDS:
            counted[facility_id].append(latest.observed_at)

    results = []
    for facility in facilities:
        timestamps = counted[facility.facility_id]
        count = len(timestamps)
        occupied = min(count, facility.capacity) if count else None
        ratio = occupied / facility.capacity if occupied is not None else None
        congestion = ("unknown" if ratio is None else "full" if ratio >= 1 else
                      "high" if ratio >= 0.8 else "moderate" if ratio >= 0.5 else "low")
        results.append(OccupancyEstimate(
            facility_id=facility.facility_id,
            estimated_occupied_spaces=occupied,
            estimated_available_spaces=facility.capacity - occupied if occupied is not None else None,
            occupancy_ratio=ratio, congestion=congestion,
            qualifying_subjects=count, capacity_exceeded=count > facility.capacity,
            latest_observation_at=max(timestamps) if timestamps else None,
        ))
    return results
