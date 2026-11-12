"""How location data gets into the service (T089).

There are two ways in, and both end at the same place:

    a user's POST request ----\\
                               >--> publish on the broker --> save_location --> store
    the feed poller ----------/

The feed poller is a background worker. Every 5 seconds it asks a traffic
source (a URL) for the newest locations and publishes each one.

If the source stops answering, nothing new is published. The pings already in
the store get older, and after 60 seconds the answers are labelled
"stale_fallback" instead of "live". When the source comes back, the next poll
works again and the answers go back to "live".

The poller is switched off unless the TRAFFIC_SOURCE_URL setting is given.
"""

import logging
import os
import threading
from datetime import datetime, timedelta, timezone

import httpx
from pydantic import ValidationError

from app.broker import Broker
from app.models import LocationPing
from app.settings import (
    CACHE_WINDOW_SECONDS,
    LIVE_WINDOW_SECONDS,
    LOCATION_TOPIC,
    MAX_FUTURE_SECONDS,
    POLL_INTERVAL_SECONDS,
    POLL_TIMEOUT_SECONDS,
)
from app.store import LocationStore


logger = logging.getLogger("live_traffic_ingestion")

# One store (the timestamped cache) and one broker for the whole service.
store = LocationStore()
broker = Broker()


def save_location(message):
    """The subscriber: every published location update is saved in the store."""
    store.add_ping(
        message["user_id"],
        message["latitude"],
        message["longitude"],
        message["timestamp"],
        message["published_at"],
    )


broker.subscribe(LOCATION_TOPIC, save_location)


def find_timestamp_problem(timestamp, now):
    """Return a message if the timestamp cannot be accepted, otherwise None."""
    if timestamp > now + timedelta(seconds=MAX_FUTURE_SECONDS):
        return "timestamp is in the future"

    if timestamp < now - timedelta(seconds=CACHE_WINDOW_SECONDS):
        return "timestamp is too old to be useful"

    return None


def publish_location(user_id, latitude, longitude, timestamp, source, now):
    """Publish one location update. This is the event every subscriber receives."""
    message = {
        "event_type": "location_update",
        "user_id": user_id,
        "latitude": latitude,
        "longitude": longitude,
        "timestamp": timestamp,       # when the user was at this location
        "source": source,             # "user" (a POST request) or "feed" (the poller)
        "published_at": now,          # when the service published this event
    }
    broker.publish(LOCATION_TOPIC, message)


class FeedPoller:
    """Asks a traffic source for new locations every few seconds."""

    def __init__(self, source_url):
        self.source_url = source_url

        if source_url:
            self.source_status = "waiting"      # no poll has finished yet
        else:
            self.source_status = "not_configured"

        self.last_poll_at = None
        self.last_successful_poll_at = None
        self.consecutive_failures = 0

        self.thread = None
        self.stop_event = threading.Event()

    def fetch_locations(self):
        """Download the newest locations from the source. Raises an error on failure."""
        response = httpx.get(self.source_url, timeout=POLL_TIMEOUT_SECONDS)
        response.raise_for_status()

        locations = response.json()["locations"]
        if not isinstance(locations, list):
            raise ValueError("the source did not send a list of locations")

        return locations

    def poll_once(self):
        """Do one poll. Returns how many location updates were published."""
        now = datetime.now(timezone.utc)
        self.last_poll_at = now

        try:
            locations = self.fetch_locations()
        except Exception as error:
            # The source is down, slow, or sent something we cannot read.
            self.consecutive_failures = self.consecutive_failures + 1
            self.source_status = "unavailable"
            logger.warning("Traffic source poll failed: %s", error)
            return 0

        self.consecutive_failures = 0
        self.source_status = "ok"
        self.last_successful_poll_at = now

        published = 0

        for item in locations:
            # Check each location the same way a POST request is checked.
            try:
                ping = LocationPing.model_validate(item)
            except ValidationError:
                continue

            if ping.timestamp is None:
                timestamp = now
            else:
                timestamp = ping.timestamp

            if find_timestamp_problem(timestamp, now) is not None:
                continue

            publish_location(ping.user_id, ping.latitude, ping.longitude,
                             timestamp, "feed", now)
            published = published + 1

        return published

    def run_forever(self):
        """The loop the background thread runs: poll, wait, poll, wait..."""
        while not self.stop_event.is_set():
            try:
                self.poll_once()
            except Exception:
                logger.exception("Unexpected error while polling")

            # Wait for the next poll, but wake up at once if stop() is called.
            self.stop_event.wait(POLL_INTERVAL_SECONDS)

    def start(self):
        self.stop_event.clear()
        self.thread = threading.Thread(target=self.run_forever, daemon=True)
        self.thread.start()
        logger.info("Feed poller started for %s", self.source_url)

    def stop(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=POLL_TIMEOUT_SECONDS + 1)
            self.thread = None


# Created when the service starts. main.py starts it only if a URL was given.
poller = FeedPoller(os.getenv("TRAFFIC_SOURCE_URL"))


def get_ingestion_status(now):
    """A summary of the traffic source and the cache, for the status endpoint."""
    pings_by_user = store.get_all_pings(now)

    # Find the newest ping in the whole cache.
    newest = None
    for pings in pings_by_user.values():
        latest = pings[-1]["timestamp"]
        if newest is None or latest > newest:
            newest = latest

    if newest is None:
        newest_age = None
        cache_status = "empty"
    else:
        newest_age = round((now - newest).total_seconds(), 1)
        if newest_age <= LIVE_WINDOW_SECONDS:
            cache_status = "live"
        else:
            cache_status = "stale"

    return {
        "source_configured": poller.source_url is not None and poller.source_url != "",
        "source_status": poller.source_status,
        "poll_interval_seconds": POLL_INTERVAL_SECONDS,
        "last_poll_at": poller.last_poll_at,
        "last_successful_poll_at": poller.last_successful_poll_at,
        "consecutive_failures": poller.consecutive_failures,
        "events_published": broker.published_count,
        "cached_users": len(pings_by_user),
        "newest_ping_at": newest,
        "newest_ping_age_seconds": newest_age,
        "cache_status": cache_status,
        "checked_at": now,
    }
