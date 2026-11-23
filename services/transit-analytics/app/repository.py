"""Bounded background refresh and independently persisted schedule/live snapshots."""

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import logging
import math
import os
from pathlib import Path
from threading import Event, Lock, Thread

from app.database import Database, MAX_BYTES, fingerprint
from app.models import LiveDataset, ScheduleDataset, utc_now

LOG = logging.getLogger("transit")
FIXTURES = Path(__file__).parent / "fixtures"


@dataclass(frozen=True)
class Snapshot:
    data: ScheduleDataset | LiveDataset
    received_at: datetime
    revision: str
    schedule_revision: str | None = None


class Repository:
    def __init__(self, *, clock=None):
        self.clock = clock or utc_now
        self.schedule_path = Path(os.getenv("TRANSIT_SCHEDULE_FILE", str(FIXTURES / "schedule.json")))
        self.live_path = Path(os.getenv("TRANSIT_LIVE_FILE", str(FIXTURES / "live.json")))
        self.live_mode = os.getenv("TRANSIT_LIVE_MODE", "file")
        if self.live_mode not in {"file", "unavailable"}:
            raise ValueError("TRANSIT_LIVE_MODE must be file or unavailable")
        self.interval = float(os.getenv("TRANSIT_REFRESH_SECONDS", "5"))
        self.retention = float(os.getenv("TRANSIT_LIVE_RETENTION_SECONDS", "86400"))
        if not math.isfinite(self.interval) or not .1 <= self.interval <= 3600:
            raise ValueError("Invalid transit refresh interval")
        if not math.isfinite(self.retention) or not 0 < self.retention <= 604800:
            raise ValueError("Invalid transit live retention")
        self.namespace = hashlib.sha256(repr((str(self.schedule_path.resolve()), str(self.live_path.resolve()))).encode()).hexdigest()
        self.database = Database(os.getenv("TRANSIT_DATABASE_PATH", "data/transit.sqlite"))
        self.lock, self.stop_event, self.thread = Lock(), Event(), None
        self.schedule = self.live = None
        self.live_available = False
        self.schedule_available = False
        for kind, payload, received_at in self.database.read(self.namespace):
            try:
                self.install(kind, json.loads(payload), datetime.fromisoformat(received_at), persist=False)
            except Exception as exc:
                LOG.warning("Retained transit %s rejected: %s", kind, type(exc).__name__)

    def snapshots(self):
        with self.lock:
            return self.schedule, self.live, self.live_available

    def install(self, kind, payload, received_at, *, persist=True):
        data = (ScheduleDataset if kind == "schedule" else LiveDataset).model_validate(payload)
        now = self.clock()
        if received_at.tzinfo is None or received_at > now or data.source_updated_at and data.source_updated_at > now:
            raise ValueError("Invalid transit update timestamp")
        if kind == "live":
            schedule = self.schedule
            if schedule is None or data.schedule_version != schedule.data.version:
                raise ValueError("Live input must reference the installed schedule version")
            stops = {item.stop_id for item in schedule.data.stops}
            routes = {item.route_id for item in schedule.data.routes}
            trips = {item.trip_id: item for item in schedule.data.trips}
            for feed in (data.trips, data.disruptions, data.occupancy):
                if feed is not None and feed.updated_at and feed.updated_at > now:
                    raise ValueError("Future feed update")
            if data.trips:
                seen = set()
                for item in data.trips.items:
                    trip = trips.get(item.trip_id)
                    key = (item.trip_id, item.stop_id, item.service_date)
                    if trip is None or item.stop_id not in {stop.stop_id for stop in trip.stop_times} or key in seen:
                        raise ValueError("Invalid or duplicated trip update")
                    seen.add(key)
                    if item.updated_at and item.updated_at > now:
                        raise ValueError("Future trip update")
            if data.disruptions:
                ids = set()
                for item in data.disruptions.items:
                    if set(item.route_ids) - routes or set(item.stop_ids) - stops or item.disruption_id in ids or item.updated_at > now:
                        raise ValueError("Invalid disruption input")
                    ids.add(item.disruption_id)
            if data.occupancy:
                ids = set()
                for item in data.occupancy.items:
                    key = (item.route_id, item.vehicle_id)
                    if item.route_id not in routes or key in ids or item.observed_at > now:
                        raise ValueError("Invalid occupancy input")
                    ids.add(key)
        digest = fingerprint(data)
        bound_revision = self.schedule.revision if kind == "live" and self.schedule else None
        with self.lock:
            old = getattr(self, kind)
            if old is not None and old.revision == digest:
                if old.schedule_revision != bound_revision:
                    setattr(self, kind, Snapshot(old.data, old.received_at, digest, bound_revision))
                return
        if persist:
            self.database.save(self.namespace, kind, data, received_at)
        with self.lock:
            setattr(self, kind, Snapshot(data, received_at, digest, bound_revision))

    def refresh(self):
        for kind, path in (("schedule", self.schedule_path), ("live", self.live_path)):
            available = False
            try:
                if kind == "live" and self.live_mode == "unavailable":
                    raise TimeoutError("Live adapter disabled")
                with path.open("rb") as source:
                    raw = source.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise ValueError("Transit file exceeds byte limit")
                self.install(kind, json.loads(raw), self.clock())
                available = True
            except Exception as exc:
                LOG.warning("Transit %s refresh unavailable: %s", kind, type(exc).__name__)
            with self.lock:
                setattr(self, kind + "_available", available)

    def start(self):
        def run():
            while not self.stop_event.wait(self.interval):
                self.refresh()
        self.thread = Thread(target=run, name="s6-dataset-refresh", daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                raise RuntimeError("Transit refresh worker did not stop")
