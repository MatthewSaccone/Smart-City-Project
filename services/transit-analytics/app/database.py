"""S6-owned SQLite snapshots; no registry or other service's domain data."""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

MAX_BYTES = 8 * 1024 * 1024


def fingerprint(model):
    return hashlib.sha256(json.dumps(model.model_dump(mode="json"), sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as connection, connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS transit_datasets (
                namespace TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('schedule','live')),
                revision TEXT NOT NULL, payload TEXT NOT NULL CHECK(length(payload)<=8388608),
                received_at TEXT NOT NULL, PRIMARY KEY(namespace,kind))""")

    def connect(self):
        return sqlite3.connect(self.path, timeout=2)

    def read(self, namespace):
        with closing(self.connect()) as connection:
            return connection.execute("SELECT kind,payload,received_at FROM transit_datasets WHERE namespace=? ORDER BY kind DESC",
                                      (namespace,)).fetchall()

    def save(self, namespace, kind, model, received_at):
        payload = model.model_dump_json()
        if len(payload.encode()) > MAX_BYTES:
            raise ValueError("Normalized transit dataset exceeds byte limit")
        with closing(self.connect()) as connection, connection:
            connection.execute("DELETE FROM transit_datasets WHERE namespace != ?", (namespace,))
            connection.execute("""INSERT INTO transit_datasets VALUES (?,?,?,?,?)
                ON CONFLICT(namespace,kind) DO UPDATE SET revision=excluded.revision,
                payload=excluded.payload,received_at=excluded.received_at
                WHERE transit_datasets.revision != excluded.revision""",
                (namespace, kind, fingerprint(model), payload, received_at.isoformat()))
