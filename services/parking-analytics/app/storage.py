"""Optional dedicated S4 PostgreSQL snapshots and transactional update hints."""

from app.datasets import CHANNEL, DatasetChanged, StoredFeed, revision, validate_feed


SCHEMA = """
CREATE TABLE IF NOT EXISTS parking_datasets (
    stream TEXT PRIMARY KEY CHECK (stream IN ('catalog','pricing','occupancy')),
    revision TEXT NOT NULL,
    payload JSONB NOT NULL CHECK (octet_length(payload::text) <= 8388608),
    received_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
)
"""


class ParkingDataStore:
    def __init__(self, database_url):
        self.database_url = database_url

    def connect(self):
        import psycopg
        return psycopg.connect(self.database_url, autocommit=True, connect_timeout=2,
                               options="-c statement_timeout=2000")

    def initialize(self):
        with self.connect() as connection:
            connection.execute(SCHEMA)

    def publish(self, stream, payload):
        feed = validate_feed(stream, payload)
        event = DatasetChanged(stream=stream, revision=revision(feed))
        with self.connect() as connection, connection.transaction():
            row = connection.execute("""INSERT INTO parking_datasets(stream,revision,payload)
                VALUES (%s,%s,%s::jsonb) ON CONFLICT(stream) DO UPDATE
                SET revision=excluded.revision,payload=excluded.payload,received_at=clock_timestamp()
                WHERE parking_datasets.revision != excluded.revision RETURNING received_at""",
                (stream, event.revision, feed.model_dump_json())).fetchone()
            if row:
                connection.execute("SELECT pg_notify(%s,%s)", (CHANNEL, event.model_dump_json()))
        return bool(row)

    @staticmethod
    def read(connection):
        rows = connection.execute("SELECT stream,payload,received_at FROM parking_datasets").fetchall()
        return {stream: StoredFeed(payload=payload, received_at=received) for stream, payload, received in rows}
