"""The traffic calculations for the Live Traffic service (S1).

The idea in one paragraph:

    A user sends their location every few seconds. If we know where a user
    was a moment ago and where they are now, we know how fast they are
    moving (speed = distance / time). If the users in an area are moving
    much slower than a normal road speed, the area is congested.

Nothing in this file talks to the network or to the store. Every function
takes its inputs as arguments and returns a result, which makes it easy to test.
"""

import math

from app.settings import (
    CACHE_WINDOW_SECONDS,
    FREE_FLOW_SPEED_KMH,
    LIVE_WINDOW_SECONDS,
    MAX_REALISTIC_SPEED_KMH,
    MAX_SEGMENTS,
    MIN_SEARCH_RADIUS_M,
    MIN_SPEED_KMH,
    SEGMENT_LENGTH_M,
)


EARTH_RADIUS_M = 6371000


def distance_m(lat1, lon1, lat2, lon2):
    """Distance in metres between two points on Earth (the Haversine formula)."""
    lat1 = math.radians(lat1)
    lon1 = math.radians(lon1)
    lat2 = math.radians(lat2)
    lon2 = math.radians(lon2)

    lat_difference = lat2 - lat1
    lon_difference = lon2 - lon1

    a = (
        math.sin(lat_difference / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(lon_difference / 2) ** 2
    )
    # Rounding errors can push "a" a hair above 1, which would crash asin.
    a = min(1.0, a)

    return EARTH_RADIUS_M * 2 * math.asin(math.sqrt(a))


def congestion_level(average_speed_kmh):
    """Turn an average speed into a word: low, moderate, high or severe."""
    if average_speed_kmh is None:
        return "unknown"

    ratio = average_speed_kmh / FREE_FLOW_SPEED_KMH

    if ratio >= 0.75:
        return "low"
    if ratio >= 0.50:
        return "moderate"
    if ratio >= 0.25:
        return "high"
    return "severe"


def build_speed_samples(pings_by_user, now, max_age_seconds):
    """Work out the current speed of every user (T011).

    Returns a list with one "speed sample" per user:
        {"latitude", "longitude", "speed_kmh", "timestamp"}

    Only pings that are at most max_age_seconds old are used (T018).
    A user needs at least two such pings, because one point has no speed.
    """
    samples = []

    for pings in pings_by_user.values():
        recent = []
        for ping in pings:
            age_seconds = (now - ping["timestamp"]).total_seconds()
            if age_seconds <= max_age_seconds:
                recent.append(ping)

        if len(recent) < 2:
            continue

        # The pings are sorted oldest first, so the last two are the newest.
        previous = recent[-2]
        latest = recent[-1]

        seconds = (latest["timestamp"] - previous["timestamp"]).total_seconds()
        if seconds <= 0:
            continue

        metres = distance_m(
            previous["latitude"], previous["longitude"],
            latest["latitude"], latest["longitude"],
        )
        speed_kmh = (metres / seconds) * 3.6

        if speed_kmh > MAX_REALISTIC_SPEED_KMH:
            # Nobody drives this fast in a city. Treat it as a GPS glitch.
            continue

        samples.append({
            "latitude": latest["latitude"],
            "longitude": latest["longitude"],
            "speed_kmh": speed_kmh,
            "timestamp": latest["timestamp"],
        })

    return samples


def find_samples_near(samples, latitude, longitude, radius_m):
    """Keep only the samples inside a circle (T017)."""
    nearby = []

    for sample in samples:
        distance = distance_m(
            latitude, longitude, sample["latitude"], sample["longitude"],
        )
        if distance <= radius_m:
            nearby.append(sample)

    return nearby


def average_speed(samples):
    """Average speed of a list of samples, or None if the list is empty.

    The answer is rounded to one decimal place, so the speed we show and
    the speed we use to pick the congestion level are always the same number.
    """
    if len(samples) == 0:
        return None

    total = 0.0
    for sample in samples:
        total = total + sample["speed_kmh"]

    return round(total / len(samples), 1)


def newest_timestamp(samples):
    """The time of the most recent sample, or None if the list is empty."""
    newest = None

    for sample in samples:
        if newest is None or sample["timestamp"] > newest:
            newest = sample["timestamp"]

    return newest


def freshness_fields(samples, data_status, now):
    """The fields that tell the caller how fresh the answer is (T018)."""
    newest = newest_timestamp(samples)

    if newest is None:
        age_seconds = None
    else:
        age_seconds = round((now - newest).total_seconds(), 1)

    return {
        "data_status": data_status,
        "is_live": data_status == "live",
        "newest_data_at": newest,
        "data_age_seconds": age_seconds,
        "calculated_at": now,
    }


def area_congestion(pings_by_user, latitude, longitude, radius_m, now):
    """Congestion inside a circle around a location (T011, T017, T018)."""

    # Step 1: try to answer with live data (the last 60 seconds).
    samples = build_speed_samples(pings_by_user, now, LIVE_WINDOW_SECONDS)
    nearby = find_samples_near(samples, latitude, longitude, radius_m)
    data_status = "live"

    # Step 2: nothing live here, so fall back to older cached data and say so.
    if len(nearby) == 0:
        samples = build_speed_samples(pings_by_user, now, CACHE_WINDOW_SECONDS)
        nearby = find_samples_near(samples, latitude, longitude, radius_m)
        data_status = "stale_fallback"

    # Step 3: still nothing, so we honestly do not know.
    if len(nearby) == 0:
        data_status = "no_data"

    speed = average_speed(nearby)

    result = {
        "latitude": latitude,
        "longitude": longitude,
        "radius_m": radius_m,
        "congestion_level": congestion_level(speed),
        "average_speed_kmh": speed,
        "free_flow_speed_kmh": FREE_FLOW_SPEED_KMH,
        "users_counted": len(nearby),
    }
    result.update(freshness_fields(nearby, data_status, now))

    return result


def build_segments(samples, start_lat, start_lon, end_lat, end_lon,
                   segment_count, segment_length_m):
    """Cut the straight line from start to end into pieces and analyse each one.

    For every piece we work out its congestion level (T013) and how many
    extra seconds it costs compared with an empty road (T015).

    Returns two things: the list of segments, and the samples that were used.
    """
    search_radius_m = max(segment_length_m / 2, MIN_SEARCH_RADIUS_M)
    normal_seconds = segment_length_m / (FREE_FLOW_SPEED_KMH / 3.6)

    segments = []
    used_samples = []

    for number in range(segment_count):
        # The middle of this piece, as a fraction of the whole route (0 to 1).
        middle = (number + 0.5) / segment_count
        middle_lat = start_lat + (end_lat - start_lat) * middle
        middle_lon = start_lon + (end_lon - start_lon) * middle

        nearby = find_samples_near(samples, middle_lat, middle_lon, search_radius_m)
        used_samples = used_samples + nearby
        speed = average_speed(nearby)

        if speed is None:
            # No users here, so we cannot claim any delay.
            travel_seconds = normal_seconds
        else:
            speed_for_delay = max(speed, MIN_SPEED_KMH)
            travel_seconds = segment_length_m / (speed_for_delay / 3.6)
            # Traffic faster than normal does not count as a negative delay.
            travel_seconds = max(travel_seconds, normal_seconds)

        segments.append({
            "segment_number": number + 1,
            "middle_latitude": round(middle_lat, 6),
            "middle_longitude": round(middle_lon, 6),
            "length_m": round(segment_length_m, 1),
            "congestion_level": congestion_level(speed),
            "average_speed_kmh": speed,
            "users_counted": len(nearby),
            "travel_seconds": round(travel_seconds, 1),
            "delay_seconds": round(travel_seconds - normal_seconds, 1),
        })

    return segments, used_samples


def count_segments_with_data(segments):
    count = 0
    for segment in segments:
        if segment["users_counted"] > 0:
            count = count + 1
    return count


def route_congestion(pings_by_user, start_lat, start_lon, end_lat, end_lon, now):
    """Congestion and delay between two points (T013, T015, T018).

    The "route" is the straight line between the two points. The service has
    no road map, so it cannot follow real streets.
    """
    total_distance_m = distance_m(start_lat, start_lon, end_lat, end_lon)

    # Decide how many pieces to cut the route into (at least 1, at most 20).
    segment_count = math.ceil(total_distance_m / SEGMENT_LENGTH_M)
    segment_count = max(1, segment_count)
    segment_count = min(MAX_SEGMENTS, segment_count)
    segment_length_m = total_distance_m / segment_count

    # Step 1: try to answer with live data (the last 60 seconds).
    samples = build_speed_samples(pings_by_user, now, LIVE_WINDOW_SECONDS)
    segments, used_samples = build_segments(
        samples, start_lat, start_lon, end_lat, end_lon,
        segment_count, segment_length_m,
    )
    data_status = "live"

    # Step 2: no live data anywhere on the route, so fall back to cached data.
    if count_segments_with_data(segments) == 0:
        samples = build_speed_samples(pings_by_user, now, CACHE_WINDOW_SECONDS)
        segments, used_samples = build_segments(
            samples, start_lat, start_lon, end_lat, end_lon,
            segment_count, segment_length_m,
        )
        data_status = "stale_fallback"

    # Step 3: still nothing, so we honestly do not know.
    if count_segments_with_data(segments) == 0:
        data_status = "no_data"

    # Add up the whole route.
    normal_seconds = total_distance_m / (FREE_FLOW_SPEED_KMH / 3.6)
    delay_seconds = 0.0
    covered_m = 0.0
    covered_seconds = 0.0

    for segment in segments:
        delay_seconds = delay_seconds + segment["delay_seconds"]

        if segment["users_counted"] > 0:
            covered_m = covered_m + segment_length_m
            covered_seconds = covered_seconds + segment["travel_seconds"]

    # Average speed over the parts of the route where we have data.
    if covered_seconds > 0:
        route_speed = round((covered_m / covered_seconds) * 3.6, 1)
    else:
        route_speed = None

    result = {
        "start_latitude": start_lat,
        "start_longitude": start_lon,
        "end_latitude": end_lat,
        "end_longitude": end_lon,
        "distance_m": round(total_distance_m, 1),
        "congestion_level": congestion_level(route_speed),
        "average_speed_kmh": route_speed,
        "free_flow_speed_kmh": FREE_FLOW_SPEED_KMH,
        "normal_travel_seconds": round(normal_seconds, 1),
        "estimated_travel_seconds": round(normal_seconds + delay_seconds, 1),
        "estimated_delay_seconds": round(delay_seconds, 1),
        "segments_without_data": segment_count - count_segments_with_data(segments),
        "segments": segments,
    }
    result.update(freshness_fields(used_samples, data_status, now))

    return result
