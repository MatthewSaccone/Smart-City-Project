"""S5-owned SQLite persistence (ASR6: not shared with the registry or other services).

* ``bundles``  - latest normalized weather / air-quality bundle per location cell.
                 Survives restarts so the QR3 fallback still has data after a redeploy.
* ``samples``  - hourly historical values per cell and metric, used for trends.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock

from app.models import iso_z

RETENTION = timedelta(days=35)


class Store:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = Lock()
        with closing(self._connect()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS bundles (
                kind TEXT NOT NULL CHECK(kind IN ('weather','air')),
                cell TEXT NOT NULL, payload TEXT NOT NULL, retrieved_at TEXT NOT NULL,
                PRIMARY KEY(kind, cell))""")
            db.execute("""CREATE TABLE IF NOT EXISTS samples (
                cell TEXT NOT NULL, metric TEXT NOT NULL, at TEXT NOT NULL, value REAL NOT NULL,
                source TEXT NOT NULL, ingested_at TEXT NOT NULL,
                PRIMARY KEY(cell, metric, at))""")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=2, check_same_thread=False)

    def load_bundles(self) -> list[tuple[str, str, dict]]:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT kind, cell, payload FROM bundles").fetchall()
        return [(kind, cell, json.loads(payload)) for kind, cell, payload in rows]

    def save_bundle(self, kind: str, cell: str, bundle: dict) -> None:
        with self.lock, closing(self._connect()) as db, db:
            db.execute("""INSERT INTO bundles VALUES (?,?,?,?) ON CONFLICT(kind, cell)
                DO UPDATE SET payload=excluded.payload, retrieved_at=excluded.retrieved_at""",
                       (kind, cell, json.dumps(bundle, separators=(",", ":")), bundle["retrieved_at"]))

    def save_samples(self, cell: str, rows: list[tuple[str, datetime, float]], source: str,
                     ingested_at: datetime) -> None:
        if not rows:
            return
        stamp = iso_z(ingested_at)
        with self.lock, closing(self._connect()) as db, db:
            db.executemany("""INSERT INTO samples VALUES (?,?,?,?,?,?) ON CONFLICT(cell, metric, at)
                DO UPDATE SET value=excluded.value, source=excluded.source, ingested_at=excluded.ingested_at""",
                           [(cell, metric, iso_z(at), value, source, stamp) for metric, at, value in rows])
            db.execute("DELETE FROM samples WHERE at < ?", (iso_z(ingested_at - RETENTION),))

    def samples(self, cell: str, metric: str, start: datetime, end: datetime):
        """Return [(at, value)], latest ingestion time and source labels for [start, end)."""
        with closing(self._connect()) as db:
            rows = db.execute("""SELECT at, value, source, ingested_at FROM samples
                WHERE cell=? AND metric=? AND at >= ? AND at < ? ORDER BY at""",
                              (cell, metric, iso_z(start), iso_z(end))).fetchall()
            earliest = db.execute("SELECT MIN(at) FROM samples WHERE cell=? AND metric=?",
                                  (cell, metric)).fetchone()[0]
        points = [(datetime.fromisoformat(at.replace("Z", "+00:00")), value) for at, value, _, _ in rows]
        updated = max((row[3] for row in rows), default=None)
        sources = sorted({row[2] for row in rows})
        earliest_at = datetime.fromisoformat(earliest.replace("Z", "+00:00")) if earliest else None
        return points, updated, sources, earliest_at
