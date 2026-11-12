"""Tests for the Live Traffic service (S1): tasks T011 to T020, T089 and T096.

Each test name starts with the task it gives evidence for.

Run from the repository root:
    python -m pytest -c services/live-traffic/pytest.ini services/live-traffic/tests -q
"""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

import app.ingestion as ingestion
import app.main as main
import app.routes as routes
from app.broker import Broker
from app.analytics import (
    area_congestion,
    build_speed_samples,
    congestion_level,
    distance_m,
    route_congestion,
)
from app.store import LocationStore


# A fixed "current time" so the calculation tests give the same answer every run.
NOW = datetime(2026, 11, 11, 15, 0, 0, tzinfo=timezone.utc)

# One degree of latitude is about this many metres everywhere on Earth.
METRES_PER_DEGREE_LAT = 111194.93

# A test location, and a point exactly 1000 m north of it.
LAT = 43.0
LON = -81.25
LAT_1000M_NORTH = LAT + 1000 / METRES_PER_DEGREE_LAT

# T020: the number of requests sent at the same time.
# Replace this with the number the team agreed in T002.
CONCURRENT_REQUESTS = 50


def north_of(latitude, metres):
    """The latitude that is this many metres north of the given latitude."""
    return latitude + metres / METRES_PER_DEGREE_LAT


def moving_user(speed_kmh, latitude=LAT, longitude=LON, seconds_ago=5, now=NOW):
    """Two pings, 10 seconds apart, for a user driving north at speed_kmh.

    The newest ping is at (latitude, longitude) and is seconds_ago old.
    """
    metres_in_10_seconds = (speed_kmh / 3.6) * 10
    return [
        {
            "latitude": north_of(latitude, -metres_in_10_seconds),
            "longitude": longitude,
            "timestamp": now - timedelta(seconds=seconds_ago + 10),
        },
        {
            "latitude": latitude,
            "longitude": longitude,
            "timestamp": now - timedelta(seconds=seconds_ago),
        },
    ]


def post_moving_user(client, user_id, speed_kmh, latitude=LAT, longitude=LON,
                     seconds_ago=1):
    """Send two pings through the real API for a user driving at speed_kmh."""
    pings = moving_user(speed_kmh, latitude, longitude, seconds_ago,
                        now=datetime.now(timezone.utc))
    for ping in pings:
        response = client.post("/v1/traffic/locations", json={
            "user_id": user_id,
            "latitude": ping["latitude"],
            "longitude": ping["longitude"],
            "timestamp": ping["timestamp"].isoformat(),
        })
        assert response.status_code == 201


@pytest.fixture
def client(monkeypatch):
    """A test client with the registry switched off and an empty store."""
    monkeypatch.setattr(main, "REGISTRY_URL", None)
    routes.store.clear()
    with TestClient(main.app) as client:
        yield client
    routes.store.clear()


AREA = {"latitude": LAT, "longitude": LON, "radius_m": 300}
ROUTE = {
    "start_latitude": LAT, "start_longitude": LON,
    "end_latitude": LAT_1000M_NORTH, "end_longitude": LON,
}


# ---------- small building blocks ----------

def test_distance_is_in_metres():
    assert distance_m(LAT, LON, LAT, LON) == 0
    assert distance_m(LAT, LON, LAT_1000M_NORTH, LON) == pytest.approx(1000, abs=1)


def test_congestion_level_boundaries():
    # Free-flow speed is 50 km/h, so the cut-offs are 37.5, 25 and 12.5 km/h.
    assert congestion_level(50) == "low"
    assert congestion_level(37.5) == "low"
    assert congestion_level(37.4) == "moderate"
    assert congestion_level(25) == "moderate"
    assert congestion_level(24.9) == "high"
    assert congestion_level(12.5) == "high"
    assert congestion_level(12.4) == "severe"
    assert congestion_level(0) == "severe"
    assert congestion_level(None) == "unknown"


# ---------- T011: analyse time and location data to get congestion ----------

def test_t011_speed_is_worked_out_from_time_and_location():
    # 100 m in 10 seconds is 10 m/s, which is 36 km/h.
    samples = build_speed_samples({"u1": moving_user(36)}, NOW, 60)
    assert len(samples) == 1
    assert samples[0]["speed_kmh"] == pytest.approx(36, abs=0.1)


def test_t011_slow_users_mean_congestion_and_fast_users_do_not():
    slow = {"u1": moving_user(5), "u2": moving_user(7)}
    fast = {"u1": moving_user(48), "u2": moving_user(52)}

    slow_result = area_congestion(slow, LAT, LON, 300, NOW)
    fast_result = area_congestion(fast, LAT, LON, 300, NOW)

    assert slow_result["congestion_level"] == "severe"
    assert slow_result["average_speed_kmh"] == pytest.approx(6, abs=0.1)
    assert slow_result["users_counted"] == 2
    assert fast_result["congestion_level"] == "low"


def test_t011_one_ping_is_not_enough_to_know_a_speed():
    one_ping = {"u1": moving_user(30)[-1:]}
    assert build_speed_samples(one_ping, NOW, 60) == []


def test_t011_impossible_speeds_are_ignored_as_gps_errors():
    assert build_speed_samples({"u1": moving_user(500)}, NOW, 60) == []


# ---------- T017: congestion within a radius ----------

def test_t017_only_users_inside_the_radius_are_counted():
    pings = {
        "near": moving_user(10, latitude=north_of(LAT, 100)),
        "far": moving_user(50, latitude=north_of(LAT, 2000)),
    }

    small = area_congestion(pings, LAT, LON, 300, NOW)
    assert small["users_counted"] == 1
    assert small["congestion_level"] == "severe"

    large = area_congestion(pings, LAT, LON, 3000, NOW)
    assert large["users_counted"] == 2
    assert large["average_speed_kmh"] == pytest.approx(30, abs=0.1)


# ---------- T018: only the last 60 seconds counts as live ----------

def test_t018_data_exactly_60_seconds_old_is_still_live():
    pings = {"u1": moving_user(20, seconds_ago=50)}  # pings are 60 s and 50 s old
    result = area_congestion(pings, LAT, LON, 300, NOW)
    assert result["data_status"] == "live"
    assert result["is_live"] is True
    assert result["data_age_seconds"] == 50


def test_t018_older_data_is_labelled_fallback_and_never_live():
    pings = {"u1": moving_user(20, seconds_ago=51)}  # pings are 61 s and 51 s old
    result = area_congestion(pings, LAT, LON, 300, NOW)
    assert result["data_status"] == "stale_fallback"
    assert result["is_live"] is False
    assert result["congestion_level"] == "high"
    assert result["data_age_seconds"] == 51


def test_t018_live_data_is_preferred_over_older_data():
    pings = {
        "old": moving_user(5, seconds_ago=200),
        "new": moving_user(45, seconds_ago=5),
    }
    result = area_congestion(pings, LAT, LON, 300, NOW)
    assert result["data_status"] == "live"
    assert result["users_counted"] == 1
    assert result["congestion_level"] == "low"


def test_t018_no_data_gives_unknown_not_a_guess():
    result = area_congestion({}, LAT, LON, 300, NOW)
    assert result["data_status"] == "no_data"
    assert result["is_live"] is False
    assert result["congestion_level"] == "unknown"
    assert result["average_speed_kmh"] is None
    assert result["newest_data_at"] is None


def test_t018_store_forgets_pings_older_than_the_cache_window():
    store = LocationStore()
    store.add_ping("u1", LAT, LON, NOW - timedelta(seconds=601), NOW)
    store.add_ping("u2", LAT, LON, NOW - timedelta(seconds=599), NOW)
    assert list(store.get_all_pings(NOW).keys()) == ["u2"]


# ---------- T013 and T015: congestion and delay between two points ----------

def test_t013_route_between_two_points_is_split_into_segments():
    # A 1000 m route becomes two 500 m segments, with middles at 250 m and 750 m.
    pings = {
        "a": moving_user(10, latitude=north_of(LAT, 250)),
        "b": moving_user(50, latitude=north_of(LAT, 750)),
    }
    result = route_congestion(pings, LAT, LON, LAT_1000M_NORTH, LON, NOW)

    assert result["distance_m"] == pytest.approx(1000, abs=1)
    assert len(result["segments"]) == 2
    assert result["segments"][0]["congestion_level"] == "severe"
    assert result["segments"][1]["congestion_level"] == "low"
    assert result["data_status"] == "live"


def test_t015_delay_is_extra_time_compared_with_an_empty_road():
    # 1000 m at the normal 50 km/h takes 72 s. At 25 km/h it takes 144 s.
    pings = {
        "a": moving_user(25, latitude=north_of(LAT, 250)),
        "b": moving_user(25, latitude=north_of(LAT, 750)),
    }
    result = route_congestion(pings, LAT, LON, LAT_1000M_NORTH, LON, NOW)

    assert result["normal_travel_seconds"] == pytest.approx(72, abs=0.5)
    assert result["estimated_travel_seconds"] == pytest.approx(144, abs=0.5)
    assert result["estimated_delay_seconds"] == pytest.approx(72, abs=0.5)
    assert result["congestion_level"] == "moderate"


def test_t015_fast_traffic_has_zero_delay_not_negative_delay():
    pings = {
        "a": moving_user(80, latitude=north_of(LAT, 250)),
        "b": moving_user(80, latitude=north_of(LAT, 750)),
    }
    result = route_congestion(pings, LAT, LON, LAT_1000M_NORTH, LON, NOW)
    assert result["estimated_delay_seconds"] == 0


def test_t015_segments_without_users_are_reported_and_add_no_delay():
    pings = {"a": moving_user(25, latitude=north_of(LAT, 250))}
    result = route_congestion(pings, LAT, LON, LAT_1000M_NORTH, LON, NOW)

    assert result["segments_without_data"] == 1
    assert result["segments"][1]["congestion_level"] == "unknown"
    assert result["segments"][1]["delay_seconds"] == 0
    assert result["estimated_delay_seconds"] == pytest.approx(36, abs=0.5)


def test_t015_route_with_no_data_is_unknown():
    result = route_congestion({}, LAT, LON, LAT_1000M_NORTH, LON, NOW)
    assert result["data_status"] == "no_data"
    assert result["congestion_level"] == "unknown"
    assert result["estimated_delay_seconds"] == 0


# ---------- the HTTP endpoints ----------

def test_t017_area_endpoint(client):
    post_moving_user(client, "u1", 10)
    body = client.get("/v1/traffic/congestion/area", params=AREA).json()
    assert body["congestion_level"] == "severe"
    assert body["users_counted"] == 1
    assert body["is_live"] is True


def test_t013_t015_route_endpoint(client):
    post_moving_user(client, "a", 25, latitude=north_of(LAT, 250))
    post_moving_user(client, "b", 25, latitude=north_of(LAT, 750))
    body = client.get("/v1/traffic/congestion/route", params=ROUTE).json()
    assert body["congestion_level"] == "moderate"
    assert body["estimated_delay_seconds"] == pytest.approx(72, abs=1)
    assert len(body["segments"]) == 2


# ---------- T019: estimates update when new data arrives ----------

def test_t019_estimates_change_as_new_location_data_arrives(client):
    # 30 seconds ago both users were crawling.
    post_moving_user(client, "a", 6, latitude=north_of(LAT, 250), seconds_ago=30)
    post_moving_user(client, "b", 6, latitude=north_of(LAT, 750), seconds_ago=30)

    before_area = client.get("/v1/traffic/congestion/area", params=AREA).json()
    before_route = client.get("/v1/traffic/congestion/route", params=ROUTE).json()
    assert before_area["congestion_level"] == "severe"
    assert before_route["estimated_delay_seconds"] > 300

    # The same two users now send newer pings: they are moving freely.
    post_moving_user(client, "a", 50, latitude=north_of(LAT, 250))
    post_moving_user(client, "b", 50, latitude=north_of(LAT, 750))

    after_area = client.get("/v1/traffic/congestion/area", params=AREA).json()
    after_route = client.get("/v1/traffic/congestion/route", params=ROUTE).json()
    assert after_area["congestion_level"] == "low"
    assert after_route["estimated_delay_seconds"] < 5
    assert after_area["newest_data_at"] > before_area["newest_data_at"]


# ---------- T012, T014, T016: speed of one request ----------

def fill_store_with_users(count):
    """Put count moving users straight into the store (2 pings each)."""
    now = datetime.now(timezone.utc)
    for number in range(count):
        # Spread the users along the 1000 m test route.
        latitude = north_of(LAT, number % 1000)
        for ping in moving_user(20, latitude, seconds_ago=1, now=now):
            routes.store.add_ping("user-" + str(number), ping["latitude"],
                                  ping["longitude"], ping["timestamp"], now)


@pytest.mark.parametrize("path, params", [
    ("/v1/traffic/congestion/area", AREA),
    ("/v1/traffic/congestion/route", ROUTE),
])
def test_t012_t014_t016_response_times_with_2000_active_users(client, path, params):
    fill_store_with_users(2000)

    started = time.perf_counter()
    response = client.get(path, params=params)
    total_ms = (time.perf_counter() - started) * 1000

    assert response.status_code == 200
    timing = response.json()["timing_ms"]
    assert timing["retrieve_ms"] < 500      # T014 (S1 QR2)
    assert timing["calculate_ms"] < 1000    # T012 (S1 QR1)
    # T016 (S1 QR3): the whole request is under 1000 ms, so sending the
    # answer back after the calculation is under 1000 ms as well.
    assert total_ms < 1000


# ---------- T020: many requests at the same time ----------

def test_t020_response_time_holds_with_concurrent_requests(client):
    fill_store_with_users(500)

    def one_request(number):
        if number % 2 == 0:
            path, params = "/v1/traffic/congestion/area", AREA
        else:
            path, params = "/v1/traffic/congestion/route", ROUTE
        started = time.perf_counter()
        response = client.get(path, params=params)
        return response.status_code, (time.perf_counter() - started) * 1000

    with ThreadPoolExecutor(max_workers=CONCURRENT_REQUESTS) as pool:
        results = list(pool.map(one_request, range(CONCURRENT_REQUESTS)))

    for status_code, elapsed_ms in results:
        assert status_code == 200
        assert elapsed_ms < 1000


# ---------- T089: broker, feed poller and timestamped cache ----------

def feed_item(user_id, ping):
    """One location the way a traffic source sends it (plain JSON values)."""
    return {
        "user_id": user_id,
        "latitude": ping["latitude"],
        "longitude": ping["longitude"],
        "timestamp": ping["timestamp"].isoformat(),
    }


def make_test_poller(monkeypatch):
    """A poller the test controls, also used by the status endpoint."""
    poller = ingestion.FeedPoller("http://feed.test/feed")
    monkeypatch.setattr(ingestion, "poller", poller)
    return poller


def source_is_down():
    raise ConnectionError("the traffic source is not answering")


def test_t089_broker_delivers_each_message_to_every_subscriber_of_the_topic():
    broker = Broker()
    first = []
    second = []
    broker.subscribe("traffic", first.append)
    broker.subscribe("traffic", second.append)

    broker.publish("traffic", {"n": 1})
    broker.publish("something-else", {"n": 2})

    assert first == [{"n": 1}]
    assert second == [{"n": 1}]
    assert broker.published_count == 2


def test_t089_one_broken_subscriber_does_not_stop_the_others():
    broker = Broker()
    received = []

    def broken(message):
        raise RuntimeError("this subscriber always fails")

    broker.subscribe("traffic", broken)
    broker.subscribe("traffic", received.append)
    broker.publish("traffic", "hello")

    assert received == ["hello"]


def test_t089_posted_ping_reaches_the_cache_through_the_broker(client):
    before = ingestion.broker.published_count
    client.post("/v1/traffic/locations", json={
        "user_id": "u1", "latitude": LAT, "longitude": LON})

    assert ingestion.broker.published_count == before + 1
    assert "u1" in routes.store.get_all_pings(datetime.now(timezone.utc))


def test_t089_polled_locations_are_published_cached_and_analysed(client, monkeypatch):
    poller = make_test_poller(monkeypatch)
    older, newer = moving_user(10, seconds_ago=1, now=datetime.now(timezone.utc))

    # Two polls, like two visits to the traffic source.
    poller.fetch_locations = lambda: [feed_item("feed-car", older)]
    assert poller.poll_once() == 1
    poller.fetch_locations = lambda: [feed_item("feed-car", newer)]
    assert poller.poll_once() == 1

    area = client.get("/v1/traffic/congestion/area", params=AREA).json()
    assert area["congestion_level"] == "severe"
    assert area["is_live"] is True

    status = client.get("/v1/traffic/status").json()
    assert status["source_status"] == "ok"
    assert status["consecutive_failures"] == 0
    assert status["cached_users"] == 1
    assert status["cache_status"] == "live"
    assert status["last_successful_poll_at"] is not None


def test_t089_bad_or_too_old_feed_items_are_skipped(client, monkeypatch):
    poller = make_test_poller(monkeypatch)
    now = datetime.now(timezone.utc)
    poller.fetch_locations = lambda: [
        {"user_id": "good", "latitude": LAT, "longitude": LON},
        {"user_id": "bad-latitude", "latitude": 500, "longitude": LON},
        {"latitude": LAT, "longitude": LON},  # no user_id
        {"user_id": "too-old", "latitude": LAT, "longitude": LON,
         "timestamp": (now - timedelta(hours=1)).isoformat()},
        "not even a dictionary",
    ]

    assert poller.poll_once() == 1
    assert list(routes.store.get_all_pings(now).keys()) == ["good"]


def test_t089_the_same_ping_delivered_twice_is_cached_once(client, monkeypatch):
    poller = make_test_poller(monkeypatch)
    now = datetime.now(timezone.utc)
    item = feed_item("feed-car", moving_user(10, seconds_ago=1, now=now)[1])

    poller.fetch_locations = lambda: [item]
    poller.poll_once()
    poller.poll_once()

    assert len(routes.store.get_all_pings(now)["feed-car"]) == 1


def test_t089_t096_source_outage_is_reported_and_old_data_is_labelled_stale(
        client, monkeypatch):
    poller = make_test_poller(monkeypatch)

    # The last data received before the outage is now 90 and 80 seconds old.
    older, newer = moving_user(10, seconds_ago=80, now=datetime.now(timezone.utc))
    poller.fetch_locations = lambda: [feed_item("feed-car", older),
                                      feed_item("feed-car", newer)]
    poller.poll_once()

    # The source goes down. Two polls in a row fail.
    poller.fetch_locations = source_is_down
    assert poller.poll_once() == 0
    assert poller.poll_once() == 0

    status = client.get("/v1/traffic/status").json()
    assert status["source_status"] == "unavailable"
    assert status["consecutive_failures"] == 2
    assert status["cache_status"] == "stale"

    area = client.get("/v1/traffic/congestion/area", params=AREA).json()
    assert area["data_status"] == "stale_fallback"
    assert area["is_live"] is False
    assert area["congestion_level"] == "severe"


def test_t096_service_recovers_when_the_source_comes_back(client, monkeypatch):
    poller = make_test_poller(monkeypatch)

    poller.fetch_locations = source_is_down
    poller.poll_once()
    assert client.get("/v1/traffic/status").json()["source_status"] == "unavailable"

    # The source is back and sends fresh data on the next two polls.
    older, newer = moving_user(45, seconds_ago=1, now=datetime.now(timezone.utc))
    poller.fetch_locations = lambda: [feed_item("feed-car", older)]
    poller.poll_once()
    poller.fetch_locations = lambda: [feed_item("feed-car", newer)]
    poller.poll_once()

    status = client.get("/v1/traffic/status").json()
    assert status["source_status"] == "ok"
    assert status["consecutive_failures"] == 0

    area = client.get("/v1/traffic/congestion/area", params=AREA).json()
    assert area["data_status"] == "live"
    assert area["congestion_level"] == "low"


def test_t089_background_thread_keeps_polling_until_it_is_stopped(monkeypatch):
    monkeypatch.setattr(ingestion, "POLL_INTERVAL_SECONDS", 0.02)
    poller = ingestion.FeedPoller("http://feed.test/feed")
    polls = []
    poller.fetch_locations = lambda: polls.append("poll") or []

    poller.start()
    time.sleep(0.3)
    poller.stop()
    count_after_stop = len(polls)
    time.sleep(0.1)

    assert count_after_stop >= 3
    assert len(polls) == count_after_stop  # no more polls after stop()


def test_t089_status_when_no_source_is_configured(client):
    status = client.get("/v1/traffic/status").json()
    assert status["source_configured"] is False
    assert status["source_status"] == "not_configured"
    assert status["cache_status"] == "empty"
    assert status["newest_ping_at"] is None


def test_t089_poller_starts_and_stops_with_the_service_only_when_configured(monkeypatch):
    monkeypatch.setattr(main, "REGISTRY_URL", None)

    configured = Mock(source_url="http://feed.test/feed")
    monkeypatch.setattr(main, "poller", configured)
    with TestClient(main.app):
        configured.start.assert_called_once()
    configured.stop.assert_called_once()

    not_configured = Mock(source_url=None)
    monkeypatch.setattr(main, "poller", not_configured)
    with TestClient(main.app):
        pass
    not_configured.start.assert_not_called()


# ---------- bad input is rejected ----------

@pytest.mark.parametrize("changes", [
    {"latitude": 91}, {"longitude": -181}, {"radius_m": 0}, {"radius_m": -5},
    {"radius_m": 50001}, {"latitude": "NaN"}, {"radius_m": "abc"},
])
def test_invalid_area_requests_are_rejected(client, changes):
    response = client.get("/v1/traffic/congestion/area", params=AREA | changes)
    assert response.status_code == 422


def test_missing_parameters_are_rejected(client):
    assert client.get("/v1/traffic/congestion/area").status_code == 422
    assert client.get("/v1/traffic/congestion/route").status_code == 422


def test_route_needs_two_different_points_that_are_not_too_far_apart(client):
    same_point = ROUTE | {"end_latitude": LAT}
    too_far = ROUTE | {"end_latitude": LAT + 1}  # about 111 km away
    assert client.get("/v1/traffic/congestion/route", params=same_point).status_code == 422
    assert client.get("/v1/traffic/congestion/route", params=too_far).status_code == 422


@pytest.mark.parametrize("changes", [
    {"latitude": 91}, {"longitude": 181}, {"user_id": ""},
    {"timestamp": "2026-11-11T15:00:00"},  # no time zone
    {"timestamp": "2999-01-01T00:00:00Z"},  # in the future
    {"timestamp": "2020-01-01T00:00:00Z"},  # far too old
])
def test_invalid_location_pings_are_rejected(client, changes):
    ping = {"user_id": "u1", "latitude": LAT, "longitude": LON}
    assert client.post("/v1/traffic/locations", json=ping | changes).status_code == 422


def test_ping_without_timestamp_uses_the_arrival_time(client):
    before = datetime.now(timezone.utc)
    response = client.post("/v1/traffic/locations", json={
        "user_id": "u1", "latitude": LAT, "longitude": LON})
    assert response.status_code == 201
    saved = datetime.fromisoformat(response.json()["timestamp"])
    assert before <= saved <= datetime.now(timezone.utc)


# ---------- the starter behaviour still works ----------

def test_existing_health_and_root_endpoints_still_work(client):
    assert client.get("/health").json() == {"service": "live-traffic", "status": "ok"}
    assert client.get("/").json()["service"] == "live-traffic"


def test_existing_registry_registration_is_unchanged(monkeypatch):
    registry = Mock(start=AsyncMock(), stop=AsyncMock())
    constructor = Mock(return_value=registry)
    monkeypatch.setattr(main, "REGISTRY_URL", "http://registry:8080")
    monkeypatch.setattr(main, "REGISTRY_API_KEY", "test-key")
    monkeypatch.setattr(main, "RegistryClient", constructor)

    with TestClient(main.app):
        registry.start.assert_awaited_once()
        assert constructor.call_args.kwargs["service_id"] == "live-traffic"
        assert constructor.call_args.kwargs["heartbeat_interval"] == 5.0

    registry.stop.assert_awaited_once()
