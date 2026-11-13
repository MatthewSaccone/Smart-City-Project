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

from psycopg import connect
from psycopg.rows import dict_row
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



class PostgresLocationStore:
    """Shared PostgreSQL-backed location storage for service replicas."""

    def __init__(self, database):
        self.database = database

    def add_ping(self, user_id, latitude, longitude, timestamp, now):
        """Insert a ping, ignoring duplicates, and prune expired records."""
        with connect(
            self.database.database_url,
            connect_timeout=self.database.CONNECT_TIMEOUT_SECONDS,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO traffic_location_pings
                        (user_id, latitude, longitude, timestamp)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (user_id, timestamp) DO NOTHING
                    """,
                    (user_id, latitude, longitude, timestamp),
                )

                cursor.execute(
                    """
                    DELETE FROM traffic_location_pings
                    WHERE timestamp < %s
                    """,
                    (now - timedelta(seconds=CACHE_WINDOW_SECONDS),),
                )

    def get_all_pings(self, now):
        """Return unexpired pings grouped by user, oldest first."""
        with connect(
            self.database.database_url,
            connect_timeout=self.database.CONNECT_TIMEOUT_SECONDS,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    DELETE FROM traffic_location_pings
                    WHERE timestamp < %s
                    """,
                    (now - timedelta(seconds=CACHE_WINDOW_SECONDS),),
                )

                cursor.execute(
                    """
                    SELECT user_id, latitude, longitude, timestamp
                    FROM traffic_location_pings
                    WHERE timestamp >= %s
                    ORDER BY user_id, timestamp
                    """,
                    (now - timedelta(seconds=CACHE_WINDOW_SECONDS),),
                )

                rows = cursor.fetchall()

        pings_by_user = {}

        for row in rows:
            pings_by_user.setdefault(row["user_id"], []).append(
                {
                    "latitude": row["latitude"],
                    "longitude": row["longitude"],
                    "timestamp": row["timestamp"],
                }
            )

        return pings_by_user

    def clear(self):
        """Delete all location pings."""
        with connect(
            self.database.database_url,
            connect_timeout=self.database.CONNECT_TIMEOUT_SECONDS,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM traffic_location_pings")
