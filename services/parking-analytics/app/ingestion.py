"""S4 catalog, tariffs and occupancy refresh with bounded retry and pub/sub hints."""

from dataclasses import dataclass
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import random
from threading import Event, Thread
import time

from pydantic import Field

from app.cache import CachedSnapshot
from app.catalog import load_demo, load_prices
from app.datasets import CHANNEL, CatalogFeed, DatasetChanged, PricingFeed, StoredFeed, Tariff, revision, validate_feed
from app.models import ContractModel, PricingDetails, Timestamp
from app.sources import UnavailableSource
from app.storage import ParkingDataStore

LOG = logging.getLogger("parking.ingestion")
STREAMS = ("catalog", "pricing", "occupancy")


class PartialRefreshError(Exception):
    """Only one or more independent streams failed validation/retrieval."""


@dataclass(frozen=True)
class Settings:
    interval: float = 5
    retry_max: float = 60
    retention: float = 86400

    @classmethod
    def from_env(cls):
        value = cls(float(os.getenv("PARKING_REFRESH_SECONDS", "5")),
                    float(os.getenv("PARKING_RETRY_MAX_SECONDS", "60")),
                    float(os.getenv("PARKING_CACHE_RETENTION_SECONDS", "86400")))
        if (not all(math.isfinite(number) for number in (value.interval, value.retry_max, value.retention))
                or not .1 <= value.interval <= 3600 or not value.interval <= value.retry_max <= 3600
                or not 0 < value.retention <= 604800):
            raise ValueError("Invalid S4 refresh/retry/retention settings")
        return value


class IngestionStatus(ContractModel):
    mode: str
    sources: dict[str, bool]
    revisions: dict[str, str]
    last_success_at: dict[str, Timestamp]
    updates: int = Field(ge=0)
    notifications_received: int = Field(ge=0)
    notifications_rejected: int = Field(ge=0)
    broker_connected: bool
    consecutive_failures: int = Field(ge=0)
    retry_seconds: float
    persistent_cache: bool


class ParkingIngestion:
    def __init__(self, service, *, settings=None, database_url=None):
        self.service = service
        self.settings = settings or Settings.from_env()
        self.service.occupancy_retention = self.settings.retention
        self.database_url = database_url or os.getenv("PARKING_DATABASE_URL")
        self.database = ParkingDataStore(self.database_url) if self.database_url else None
        identity = self.database_url or repr((os.getenv("PARKING_CATALOG_FILE"),
                    os.getenv("PARKING_PRICING_FILE"), os.getenv("PARKING_OCCUPANCY_FILE")))
        self.namespace = hashlib.sha256(identity.encode()).hexdigest()
        self.sources = {name: False for name in STREAMS}
        self.revisions, self.last_success = {}, {}
        self.updates = self.notifications = self.rejected = self.failures = 0
        self.connected = False
        self.retry_seconds = self.settings.interval
        self.stop_event, self.thread = Event(), None
        try:
            retained = service.cache.read_streams(self.namespace)
        except Exception as exc:
            LOG.warning("S4 retained stream unavailable: %s", type(exc).__name__)
            retained = {}
        for stream in STREAMS:
            if stream in retained:
                try:
                    self.install(stream, retained[stream], persist=False, available=False)
                except Exception as exc:
                    LOG.warning("S4 retained %s unavailable: %s", stream, type(exc).__name__)

    def install(self, stream, stored, *, persist=True, available=True):
        feed = validate_feed(stream, stored.payload)
        if stored.received_at > self.service.clock():
            raise ValueError("Future S4 receipt timestamp")
        fingerprint = revision(feed)
        with self.service.lock:
            changed = self.revisions.get(stream) != fingerprint
            if changed:
                if stream == "catalog":
                    if feed.source_updated_at and feed.source_updated_at > self.service.clock():
                        raise ValueError("Future catalog update")
                    self.service.facilities = feed.facilities
                    self.service.catalog_available = True
                    self.service.catalog_source = ("simulated:" + feed.source
                        if feed.simulated and "simulat" not in feed.source.lower() else feed.source)
                    if self.service.snapshot:
                        try:
                            self.service._validate_feed(self.service.snapshot)
                        except ValueError:
                            self.service.snapshot = None
                            self.service.feed_available = False
                elif stream == "pricing":
                    ids = {item.facility_id for item in self.service.facilities}
                    if set(feed.facilities) - ids:
                        raise ValueError("Unknown tariff facility")
                    if any(item.source_updated_at and item.source_updated_at > self.service.clock()
                           for item in feed.facilities.values()):
                        raise ValueError("Future tariff update")
                    self.service.prices = {facility_id: PricingDetails(**item.model_dump(),
                        source=feed.source, service_updated_at=stored.received_at,
                        data_status="simulated" if feed.simulated else "live", is_live=not feed.simulated)
                        for facility_id, item in feed.facilities.items()}
                else:
                    self.service._validate_feed(feed)
                    self.service.snapshot = CachedSnapshot(**feed.model_dump(), service_updated_at=stored.received_at)
                self.revisions[stream] = fingerprint
                self.updates += 1
                if persist:
                    try:
                        self.service.cache.write_stream(self.namespace, stream, feed.model_dump(mode="json"), stored.received_at)
                    except Exception as exc:
                        LOG.warning("S4 stream persistence unavailable: %s", type(exc).__name__)
            self.sources[stream] = available
            if available:
                self.last_success[stream] = self.service.clock()
            if stream == "occupancy":
                self.service.feed_available = available
            elif stream == "pricing":
                self.service.pricing_available = available

    def unavailable(self, stream):
        with self.service.lock:
            self.sources[stream] = False
            if stream == "occupancy":
                self.service.feed_available = False
            elif stream == "pricing":
                self.service.pricing_available = False

    def local_feed(self, stream):
        filename = os.getenv({"catalog": "PARKING_CATALOG_FILE", "pricing": "PARKING_PRICING_FILE"}[stream])
        if filename:
            if Path(filename).stat().st_size > 8388608:
                raise ValueError("S4 file exceeds byte limit")
            feed = validate_feed(stream, json.loads(Path(filename).read_text(encoding="utf-8")))
            if not feed.simulated:
                raise ValueError("Local file adapters require explicitly simulated data")
            return feed
        if stream == "catalog":
            facilities, _, _ = load_demo()
            return CatalogFeed(source="simulated_fixture", simulated=True, facilities=facilities)
        prices = load_prices(self.service.clock())
        return PricingFeed(source="simulated_tariffs", simulated=True, facilities={key:
            Tariff(**{name: getattr(value, name) for name in Tariff.model_fields}) for key, value in prices.items()})

    def reconcile(self, connection=None):
        failures = 0
        try:
            records = self.database.read(connection) if self.database else None
        except Exception:
            for stream in STREAMS:
                self.unavailable(stream)
            raise
        for stream in STREAMS:
            try:
                if records is not None:
                    if stream not in records:
                        raise ValueError("S4 stream has not been published")
                    self.install(stream, records[stream])
                elif stream == "occupancy":
                    self.service.refresh()
                    if not self.service.feed_available:
                        raise ValueError("Occupancy source unavailable")
                    self.install(stream, StoredFeed(payload=self.service.snapshot.model_dump(
                        mode="json", exclude={"service_updated_at"}), received_at=self.service.snapshot.service_updated_at))
                else:
                    feed = self.local_feed(stream)
                    self.install(stream, StoredFeed(payload=feed.model_dump(mode="json"), received_at=self.service.clock()))
            except Exception as exc:
                failures += 1
                self.unavailable(stream)
                LOG.warning("S4 %s refresh unavailable: %s", stream, type(exc).__name__)
        if failures:
            raise PartialRefreshError("One or more S4 streams unavailable")
        self.failures = 0
        self.retry_seconds = self.settings.interval

    def failed(self, exc):
        self.failures += 1
        self.retry_seconds = min(self.settings.retry_max, self.settings.interval * 2 ** min(self.failures - 1, 12))
        LOG.warning("S4 reconciliation degraded: %s", type(exc).__name__)

    def refresh_once(self):
        try:
            if self.database:
                with self.database.connect() as connection:
                    self.reconcile(connection)
            else:
                self.reconcile()
            self.failures = 0
            self.retry_seconds = self.settings.interval
        except Exception as exc:
            if self.database and not isinstance(exc, PartialRefreshError):
                for stream in STREAMS:
                    self.unavailable(stream)
            self.failed(exc)

    def _run(self):
        while not self.stop_event.is_set():
            try:
                if self.database:
                    with self.database.connect() as connection:
                        connection.execute(f"LISTEN {CHANNEL}")
                        self.connected = True
                        self.reconcile(connection)
                        last_check = time.monotonic()
                        while not self.stop_event.is_set():
                            notices = list(connection.notifies(timeout=min(self.settings.interval, .5), stop_after=1))
                            for notice in notices:
                                try:
                                    DatasetChanged.model_validate_json(notice.payload)
                                    self.notifications += 1
                                except ValueError:
                                    self.rejected += 1
                            if notices or time.monotonic() - last_check >= self.settings.interval:
                                self.reconcile(connection)
                                last_check = time.monotonic()
                else:
                    self.reconcile()
                    self.failures = 0
                    self.stop_event.wait(self.settings.interval)
            except Exception as exc:
                self.connected = False
                if self.database and not isinstance(exc, PartialRefreshError):
                    for stream in STREAMS:
                        self.unavailable(stream)
                self.failed(exc)
                self.stop_event.wait(self.retry_seconds * random.uniform(.8, 1))
        self.connected = False

    def start(self):
        self.thread = Thread(target=self._run, name="s4-data-refresh", daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                raise RuntimeError("S4 refresh worker did not stop")

    def status(self):
        with self.service.lock:
            return IngestionStatus(mode="postgres" if self.database else "fixture_or_file", sources=dict(self.sources),
                revisions=dict(self.revisions), last_success_at=dict(self.last_success), updates=self.updates,
                notifications_received=self.notifications, notifications_rejected=self.rejected,
                broker_connected=self.connected, consecutive_failures=self.failures,
                retry_seconds=self.retry_seconds, persistent_cache=bool(self.service.cache.path))
