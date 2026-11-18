"""Optional dedicated S4 PostgreSQL snapshots and transactional update hints."""

from app.datasets import CHANNEL, DatasetChanged, StoredFeed, revision, validate_feed
from app.database import Database


class ParkingDataStore(Database):
    """Validated stream publication and reads using S4's database lifecycle."""

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
