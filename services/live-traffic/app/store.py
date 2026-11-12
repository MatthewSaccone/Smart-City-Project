"""In-memory storage for the location pings sent by active users.

A "ping" is one location update: where a user was, and when.
Pings are kept in a normal Python dictionary:

    {
        "user-1": [ping, ping, ping],   # oldest first, newest last
        "user-2": [ping, ping],
    }

Nothing is written to disk. If the service restarts, the pings are gone,
which is fine because live traffic data is only useful for a short time.
"""

import threading
from datetime import timedelta

from app.settings import CACHE_WINDOW_SECONDS


def get_timestamp(ping):
    return ping["timestamp"]


class LocationStore:
    def __init__(self):
        self.pings_by_user = {}

        # FastAPI can handle several requests at the same time on different
        # threads. The lock makes sure only one of them changes the
        # dictionary at a time.
        self.lock = threading.Lock()

    def add_ping(self, user_id, latitude, longitude, timestamp, now):
        """Save one new ping for a user."""
        ping = {
            "latitude": latitude,
            "longitude": longitude,
            "timestamp": timestamp,
        }

        with self.lock:
            if user_id not in self.pings_by_user:
                self.pings_by_user[user_id] = []

            pings = self.pings_by_user[user_id]

            # The same ping can be delivered twice. Keep only one copy.
            for existing in pings:
                if existing["timestamp"] == timestamp:
                    return

            pings.append(ping)

            # Pings can arrive out of order, so keep them sorted by time.
            pings.sort(key=get_timestamp)

            self.pings_by_user[user_id] = self.remove_old_pings(pings, now)

    def get_all_pings(self, now):
        """Return a copy of every ping that is still new enough to keep.

        A copy is returned so the calculations can take their time without
        holding the lock and blocking new pings from being saved.
        """
        copy = {}

        with self.lock:
            for user_id in list(self.pings_by_user.keys()):
                kept = self.remove_old_pings(self.pings_by_user[user_id], now)

                if len(kept) == 0:
                    # This user has not sent anything recently. Forget them.
                    del self.pings_by_user[user_id]
                else:
                    self.pings_by_user[user_id] = kept
                    copy[user_id] = list(kept)

        return copy

    def remove_old_pings(self, pings, now):
        """Keep only the pings that are newer than the cache window."""
        oldest_allowed = now - timedelta(seconds=CACHE_WINDOW_SECONDS)
        kept = []

        for ping in pings:
            if ping["timestamp"] >= oldest_allowed:
                kept.append(ping)

        return kept

    def clear(self):
        """Delete everything. Used by the tests to start from empty."""
        with self.lock:
            self.pings_by_user = {}
