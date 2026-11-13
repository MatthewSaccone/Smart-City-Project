"""Bounded S2 aggregate cache. Cached source timestamps are never refreshed."""

from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
from threading import RLock

from .errors import ServiceError
from .models import HistoryPatternsResponse

LOG = logging.getLogger(__name__)


def utc_now():
    return datetime.now(timezone.utc)


class HistoryCache:
    def __init__(self, namespace, *, path=None, max_entries=128, ttl=30, retention=86400, clock=utc_now):
        self.namespace = namespace
        self.max_entries, self.ttl, self.retention = max_entries, ttl, retention
        self.clock = clock
        self.lock = RLock()
        self.revision = None
        self.hits = self.misses = self.fallbacks = 0
        self.persistent = bool(path)
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path or ":memory:", check_same_thread=False, timeout=1)
        self.db.execute("CREATE TABLE IF NOT EXISTS history_cache (namespace TEXT, key TEXT, revision TEXT, "
                        "saved_at REAL, used_at REAL, payload TEXT, PRIMARY KEY(namespace,key))")
        # A dedicated cache path is required per service instance. Older
        # source/config namespaces are disposable and must not accumulate.
        self.db.execute("DELETE FROM history_cache WHERE namespace != ?", (namespace,))
        self.db.commit()
        self._prune()

    def _prune(self):
        now = self.clock().timestamp()
        with self.lock, self.db:
            self.db.execute("DELETE FROM history_cache WHERE saved_at < ? OR saved_at > ?", (now - self.retention, now))
            self.db.execute("DELETE FROM history_cache WHERE rowid IN (SELECT rowid FROM history_cache "
                            "ORDER BY used_at DESC LIMIT -1 OFFSET ?)", (self.max_entries,))

    @staticmethod
    def key(query):
        # Relative windows end at request time. Reusing them would silently
        # substitute an earlier interval, so only explicit windows are cached.
        if query.start_at is None or query.end_at is None:
            return None
        return hashlib.sha256(json.dumps(query.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()

    def read(self, key):
        if key is None:
            return None
        with self.lock:
            self._prune()
            row = self.db.execute("SELECT revision,saved_at,payload FROM history_cache WHERE namespace=? AND key=?",
                                  (self.namespace, key)).fetchone()
            if row is None:
                return None
            try:
                value = HistoryPatternsResponse.model_validate_json(row[2])
            except ValueError:
                with self.db:
                    self.db.execute("DELETE FROM history_cache WHERE namespace=? AND key=?", (self.namespace, key))
                return None
            with self.db:
                self.db.execute("UPDATE history_cache SET used_at=? WHERE namespace=? AND key=?",
                                (self.clock().timestamp(), self.namespace, key))
            return row[0], row[1], value

    def write(self, key, value, revision):
        if key is None:
            return
        encoded = value.model_dump_json()
        if len(encoded.encode()) > 65536:
            return
        now = self.clock().timestamp()
        with self.lock, self.db:
            if revision != self.revision:
                return  # Invalidation raced this computation; do not warm old data.
            self.db.execute("INSERT OR REPLACE INTO history_cache VALUES (?,?,?,?,?,?)",
                            (self.namespace, key, revision, now, now, encoded))
        self._prune()

    def invalidate(self, revision):
        with self.lock:
            self.revision = revision

    def count(self):
        with self.lock:
            self._prune()
            return self.db.execute("SELECT COUNT(*) FROM history_cache").fetchone()[0]

    def close(self):
        with self.lock:
            self.db.close()


class CachedHistory:
    def __init__(self, source, cache, *, available=lambda: True, clock=utc_now):
        self.source, self.cache, self.available, self.clock = source, cache, available, clock

    def historical_patterns_with_cache(self, query):
        key = self.cache.key(query)
        try:
            cached = self.cache.read(key)
        except sqlite3.Error as exc:
            LOG.warning("S2 aggregate cache read failed: %s", type(exc).__name__)
            cached = None
        now = self.clock()
        revision = self.cache.revision
        if (cached and revision is not None and cached[0] == revision
                and now.timestamp() - cached[1] <= self.cache.ttl and self.available()):
            self.cache.hits += 1
            value, state = cached[2], "HIT"
        else:
            self.cache.misses += 1
            try:
                value = self.source.historical_patterns(query)
            except ServiceError as exc:
                if exc.status_code not in {502, 503, 504} or cached is None:
                    raise
                self.cache.fallbacks += 1
                value, state = cached[2], "STALE"
            else:
                state = "MISS" if key else "BYPASS"
                if not self.available() or revision != self.cache.revision:
                    state = "STALE"
                else:
                    try:
                        self.cache.write(key, value, revision)
                    except sqlite3.Error as exc:
                        LOG.warning("S2 aggregate cache write failed: %s", type(exc).__name__)
        value = value.model_copy(deep=True, update={
            "as_of": now,
            "is_stale": value.is_stale or now > value.dataset_refresh_due_at or state == "STALE",
        })
        return value, state

    def historical_patterns(self, query):
        return self.historical_patterns_with_cache(query)[0]
