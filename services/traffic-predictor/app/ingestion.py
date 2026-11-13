"""S2 data refresh, PostgreSQL pub/sub reconciliation and aggregate caching."""

from dataclasses import dataclass
import hashlib
import logging
import math
import os
from pathlib import Path
import random
import sqlite3
from threading import Event, RLock, Thread
import time
from typing import Literal
from pydantic import AwareDatetime

from .data_events import DATASET_CHANNEL, DatasetUpdated
from .errors import ServiceError
from .history import HistoryStore
from .history_cache import CachedHistory, HistoryCache, utc_now
from .postgres_history import DATASET_ID, METADATA_SQL
from .models import ContractModel


LOG = logging.getLogger(__name__)


class IngestionStatus(ContractModel):
    mode: Literal["disabled", "csv", "postgres"]
    source_status: Literal["unconfigured", "available", "unavailable"]
    broker: Literal["disabled", "file_refresh", "postgres_listen_notify"] = "disabled"
    broker_connected: bool = False
    last_successful_refresh_at: AwareDatetime | None = None
    dataset: DatasetUpdated | None = None
    dataset_updates: int = 0
    notifications_received: int = 0
    notifications_rejected: int = 0
    consecutive_failures: int = 0
    retry_seconds: float = 0
    cache_entries: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    cache_fallbacks: int = 0
    cache_persistent: bool = False


@dataclass(frozen=True)
class IngestionSettings:
    poll_seconds: float = 5
    retry_max_seconds: float = 60
    cache_ttl: float = 30
    cache_retention: float = 86400
    cache_entries: int = 128
    cache_path: str | None = None

    @classmethod
    def from_env(cls):
        value = cls(
            poll_seconds=float(os.getenv("S2_REFRESH_SECONDS", "5")),
            retry_max_seconds=float(os.getenv("S2_RETRY_MAX_SECONDS", "60")),
            cache_ttl=float(os.getenv("S2_CACHE_TTL_SECONDS", "30")),
            cache_retention=float(os.getenv("S2_CACHE_RETENTION_SECONDS", "86400")),
            cache_entries=int(os.getenv("S2_CACHE_MAX_ENTRIES", "128")),
            cache_path=os.getenv("S2_CACHE_PATH") or None,
        )
        if (not all(math.isfinite(number) for number in (value.poll_seconds, value.retry_max_seconds,
                                                        value.cache_ttl, value.cache_retention))
                or not .1 <= value.poll_seconds <= 3600
                or not value.poll_seconds <= value.retry_max_seconds <= 3600
                or not 0 <= value.cache_ttl <= value.cache_retention <= 604800
                or not 1 <= value.cache_entries <= 4096):
            raise ValueError("Invalid S2 ingestion/cache limits")
        return value


class HistoryPipeline:
    def __init__(self, store, *, database_url=None, csv_paths=None, settings=None, clock=utc_now):
        self.store, self.database_url, self.csv_paths = store, database_url, csv_paths
        self.settings, self.clock = settings or IngestionSettings.from_env(), clock
        identity = database_url or repr(tuple(str(Path(path).resolve()) for path in csv_paths or ()))
        namespace = hashlib.sha256(identity.encode()).hexdigest()
        try:
            self.cache = HistoryCache(namespace, path=self.settings.cache_path,
                                      max_entries=self.settings.cache_entries, ttl=self.settings.cache_ttl,
                                      retention=self.settings.cache_retention, clock=clock)
        except (sqlite3.Error, OSError) as exc:
            LOG.warning("S2 persistent aggregate cache unavailable: %s", type(exc).__name__)
            self.cache = HistoryCache(namespace, max_entries=self.settings.cache_entries,
                                      ttl=self.settings.cache_ttl, retention=self.settings.cache_retention, clock=clock)
        self.cached = CachedHistory(self, self.cache, available=lambda: self.source_ok, clock=clock)
        self.lock, self.stop_event = RLock(), Event()
        self.thread = None
        self.source_ok = False
        self.connected = False
        self.last_success_at = None
        self.dataset = None
        self.updates = self.notifications = self.rejected_notifications = self.failures = 0
        self.retry_seconds = self.settings.poll_seconds
        self._file_signature = None

    def accept_dataset(self, event):
        if event.dataset_updated_at > self.clock():
            raise ValueError("Dataset update time is in the future")
        with self.lock:
            changed = self.dataset is None or self.dataset.revision != event.revision
            # Metadata queried from S2 storage, not notification ordering, is
            # authoritative. Repeated notifications cause no duplicate work.
            if changed:
                self.cache.invalidate(event.revision)
                self.updates += 1
            self.dataset = event
            self.source_ok = True
            self.last_success_at = self.clock()
            self.failures = 0
            self.retry_seconds = self.settings.poll_seconds

    def refresh_csv(self):
        before = tuple((Path(path).stat().st_mtime_ns, Path(path).stat().st_size) for path in self.csv_paths)
        if before == self._file_signature and self.dataset is not None:
            self.accept_dataset(self.dataset)
            return
        store = HistoryStore.from_files(*self.csv_paths)
        if not store.observations:
            raise ValueError("Historical dataset contains no observations")
        after = tuple((Path(path).stat().st_mtime_ns, Path(path).stat().st_size) for path in self.csv_paths)
        if before != after:
            raise ValueError("Historical files changed during refresh")
        # A changed CSV must change the revision even if a producer forgot to
        # update metadata. File fingerprint is a version, not a fabricated time.
        digest = hashlib.sha256()
        for path in self.csv_paths:
            with open(path, "rb") as handle:
                for block in iter(lambda: handle.read(65536), b""):
                    digest.update(block)
        event = DatasetUpdated(dataset_id="normalized_csv_" + digest.hexdigest(),
                               dataset_updated_at=store.dataset_updated_at,
                               dataset_refresh_due_at=store.dataset_refresh_due_at,
                               latest_observation_at=max((item.observed_at for item in store.observations), default=None))
        if any(item.observed_at > self.clock() for item in store.observations):
            raise ValueError("Historical observations cannot be in the future")
        with self.lock:
            self.accept_dataset(event)
            self.store = store
            self._file_signature = after

    def refresh_postgres(self, connection):
        with connection.cursor() as cursor:
            cursor.execute(METADATA_SQL, (DATASET_ID,))
            metadata = cursor.fetchone()
        if metadata is None:
            raise ValueError("Historical dataset metadata unavailable")
        self.accept_dataset(DatasetUpdated(dataset_id=DATASET_ID, dataset_updated_at=metadata[0],
                                           dataset_refresh_due_at=metadata[1], latest_observation_at=metadata[2]))

    def failed(self, exc):
        with self.lock:
            self.source_ok = False
            self.failures += 1
            self.retry_seconds = min(self.settings.retry_max_seconds,
                                     self.settings.poll_seconds * 2 ** min(self.failures - 1, 12))
        LOG.warning("S2 dataset refresh unavailable: %s", type(exc).__name__)

    def refresh_once(self):
        try:
            if self.csv_paths:
                self.refresh_csv()
            elif self.database_url:
                import psycopg
                with psycopg.connect(self.database_url, autocommit=True, connect_timeout=2,
                                     options="-c statement_timeout=2000") as connection:
                    self.refresh_postgres(connection)
        except Exception as exc:
            self.failed(exc)

    def _run(self):
        while not self.stop_event.is_set():
            try:
                if self.csv_paths:
                    self.refresh_csv()
                    self.stop_event.wait(self.settings.poll_seconds)
                else:
                    import psycopg
                    with psycopg.connect(self.database_url, autocommit=True, connect_timeout=2,
                                         options="-c statement_timeout=2000") as connection:
                        connection.execute(f"LISTEN {DATASET_CHANNEL}")
                        self.connected = True
                        self.refresh_postgres(connection)
                        last_check = time.monotonic()
                        while not self.stop_event.is_set():
                            notices = list(connection.notifies(timeout=min(self.settings.poll_seconds, .5), stop_after=1))
                            if notices:
                                for notice in notices:
                                    try:
                                        event = DatasetUpdated.model_validate_json(notice.payload)
                                        if event.dataset_id != DATASET_ID:
                                            raise ValueError("Unexpected S2 dataset")
                                        self.notifications += 1
                                    except ValueError:
                                        self.rejected_notifications += 1
                            # Reconcile on notification and periodically: NOTIFY
                            # is a hint, not a durable event log.
                            if notices or time.monotonic() - last_check >= self.settings.poll_seconds:
                                self.refresh_postgres(connection)
                                last_check = time.monotonic()
            except Exception as exc:
                self.connected = False
                self.failed(exc)
                # Finite backoff with jitter; Event.wait makes shutdown prompt.
                self.stop_event.wait(self.retry_seconds * random.uniform(.8, 1))
        self.connected = False

    def start(self):
        if self.database_url or self.csv_paths:
            self.thread = Thread(target=self._run, name="s2-dataset-refresh", daemon=True)
            self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                raise RuntimeError("S2 dataset worker did not stop")
        self.cache.close()

    def historical_patterns(self, query):
        with self.lock:
            store = self.store
            retained = self.last_success_at is not None and (
                self.clock() - self.last_success_at).total_seconds() <= self.settings.cache_retention
            if store is None or self.csv_paths and not self.source_ok and not retained:
                raise ServiceError(503, "SERVICE_UNAVAILABLE", "No retained historical dataset is available.", retryable=True)
        return store.historical_patterns(query)

    def historical_patterns_with_cache(self, query):
        return self.cached.historical_patterns_with_cache(query)

    def is_ready(self):
        if self.source_ok:
            return True
        try:
            return self.cache.count() > 0
        except sqlite3.Error:
            return False

    def status(self):
        with self.lock:
            return {"mode": "postgres" if self.database_url else "csv",
                    "source_status": "available" if self.source_ok else "unavailable",
                    "broker": "postgres_listen_notify" if self.database_url else "file_refresh",
                    "broker_connected": self.connected,
                    "last_successful_refresh_at": self.last_success_at,
                    "dataset": self.dataset,
                    "dataset_updates": self.updates, "notifications_received": self.notifications,
                    "notifications_rejected": self.rejected_notifications,
                    "consecutive_failures": self.failures, "retry_seconds": self.retry_seconds,
                    "cache_entries": self.cache.count(), "cache_hits": self.cache.hits,
                    "cache_misses": self.cache.misses, "cache_fallbacks": self.cache.fallbacks,
                    "cache_persistent": self.cache.persistent}
