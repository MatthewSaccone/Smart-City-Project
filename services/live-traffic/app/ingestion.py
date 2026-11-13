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
    """Polls a traffic source only while holding the shared PostgreSQL lock."""

    ADVISORY_LOCK_ID = 4471001

    def __init__(self, source_url, database=None):
        self.source_url = source_url
        self.database = database
        self.lock_connection = None

        if source_url:
            self.source_status = "waiting"
        else:
            self.source_status = "not_configured"

        self.last_poll_at = None
        self.last_successful_poll_at = None
        self.consecutive_failures = 0

        self.thread = None
        self.stop_event = threading.Event()

    def set_database(self, database):
        """Configure the shared database used for poller coordination."""
        self.database = database

    def fetch_locations(self):
        """Download the newest locations from the source."""
        response = httpx.get(self.source_url, timeout=POLL_TIMEOUT_SECONDS)
        response.raise_for_status()

        locations = response.json()["locations"]
        if not isinstance(locations, list):
            raise ValueError("the source did not send a list of locations")

        return locations

    def _release_lock(self):
        """Close the lock-owning session, releasing its advisory lock."""
        if self.lock_connection is not None:
            try:
                self.lock_connection.close()
            except Exception:
                logger.exception("Failed to close feed-poller lock connection")
            finally:
                self.lock_connection = None

    def _check_lock(self):
        """Check that the PostgreSQL session holding our lock is alive."""
        if self.database is None:
            return True

        if self.lock_connection is None:
            return False

        try:
            with self.lock_connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                return cursor.fetchone() == (1,)
        except Exception:
            logger.warning("Feed-poller database lock connection was lost")
            self._release_lock()
            return False

    def _ensure_lock(self):
        """Acquire the polling lock or remain a standby replica."""
        if self.database is None:
            return True

        if self._check_lock():
            return True

        try:
            connection = self.database.try_acquire_advisory_lock(
                self.ADVISORY_LOCK_ID
            )
            if connection is None:
                self.source_status = "standby"
                return False

            self.lock_connection = connection
            self.source_status = "waiting"
            logger.info("Acquired shared feed-poller advisory lock")
            return True

        except Exception:
            self.source_status = "unavailable"
            logger.exception("Could not acquire feed-poller advisory lock")
            return False

    def poll_once(self):
        """Do one poll, provided this replica owns the polling lock."""
        if not self._check_lock():
            return 0

        now = datetime.now(timezone.utc)
        self.last_poll_at = now

        try:
            locations = self.fetch_locations()
        except Exception as error:
            self.consecutive_failures += 1
            self.source_status = "unavailable"
            logger.warning("Traffic source poll failed: %s", error)
            return 0

        # Do not publish data if the database session was lost during fetching.
        if not self._check_lock():
            return 0

        self.consecutive_failures = 0
        self.source_status = "ok"
        self.last_successful_poll_at = now

        published = 0

        for item in locations:
            try:
                ping = LocationPing.model_validate(item)
            except ValidationError:
                continue

            timestamp = ping.timestamp if ping.timestamp is not None else now

            if find_timestamp_problem(timestamp, now) is not None:
                continue

            publish_location(
                ping.user_id,
                ping.latitude,
                ping.longitude,
                timestamp,
                "feed",
                now,
            )
            published += 1

        return published

    def run_forever(self):
        """Acquire the lock when available, then poll at the configured interval."""
        try:
            while not self.stop_event.is_set():
                if self._ensure_lock():
                    try:
                        self.poll_once()
                    except Exception:
                        logger.exception("Unexpected error while polling")

                self.stop_event.wait(POLL_INTERVAL_SECONDS)
        finally:
            self._release_lock()

    def start(self):
        if self.thread is not None and self.thread.is_alive():
            return

        self.stop_event.clear()
        self.thread = threading.Thread(
            target=self.run_forever,
            daemon=True,
        )
        self.thread.start()
        logger.info("Feed poller started for %s", self.source_url)

    
    def stop(self):
        self.stop_event.set()

        if self.thread is not None:
            self.thread.join(timeout=POLL_TIMEOUT_SECONDS + 2)

            if self.thread.is_alive():
                logger.warning(
                    "Feed poller has not stopped yet; "
                    "its worker will release the advisory lock on exit"
                )
                return

            self.thread = None

        self._release_lock()


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
