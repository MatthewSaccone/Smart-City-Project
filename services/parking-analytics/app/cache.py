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
