"""Optional S4-owned SQLite cache containing one bounded aggregate snapshot, no traces."""

from contextlib import closing
from pathlib import Path
import sqlite3

from app.models import Timestamp
from app.sources import FeedSnapshot


class CachedSnapshot(FeedSnapshot):
    service_updated_at: Timestamp


class AggregateCache:
    def __init__(self, path=None):
        self.path = path
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("CREATE TABLE IF NOT EXISTS occupancy_cache (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
                db.execute("CREATE TABLE IF NOT EXISTS stream_cache (namespace TEXT, stream TEXT, payload TEXT, "
                           "received_at TEXT, PRIMARY KEY(namespace,stream))")

    def read(self):
        if not self.path:
            return None
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT payload FROM occupancy_cache WHERE id=1").fetchone()
        return CachedSnapshot.model_validate_json(row[0]) if row else None

    def write(self, snapshot):
        if self.path:
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute("INSERT INTO occupancy_cache VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                           (snapshot.model_dump_json(),))

    def read_streams(self, namespace):
        if not self.path:
            return {}
        from app.datasets import StoredFeed
        import json
        with closing(sqlite3.connect(self.path, timeout=1)) as db:
            rows = db.execute("SELECT stream,payload,received_at FROM stream_cache WHERE namespace=?", (namespace,)).fetchall()
        result = {}
        for stream, payload, received in rows:
            try:
                result[stream] = StoredFeed(payload=json.loads(payload), received_at=received)
            except ValueError:
                # One corrupt independent stream must not hide the others.
                continue
        return result

    def write_stream(self, namespace, stream, payload, received_at):
        if not self.path:
            return
        from app.datasets import validate_feed
        feed = validate_feed(stream, payload)
        with closing(sqlite3.connect(self.path, timeout=1)) as db, db:
            # Each instance owns its cache file. Keep three bounded stream
            # snapshots, and remove disposable snapshots from an old source.
            db.execute("DELETE FROM stream_cache WHERE namespace != ?", (namespace,))
            db.execute("INSERT OR REPLACE INTO stream_cache VALUES (?,?,?,?)",
                       (namespace, stream, feed.model_dump_json(), received_at.isoformat()))
