"""S2 T010: refresh, event invalidation, bounded caching and truthful fallback."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
import pytest
import yaml

from app.data_events import DATASET_CHANNEL, DatasetUpdated, publish_dataset_updated
from app.errors import ServiceError
from app.history import HistoryStore
from app.history_cache import CachedHistory, HistoryCache
from app.ingestion import HistoryPipeline, IngestionSettings
from app.main import app
from app.models import HistoryQuery

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 12, 17, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


def query(**values):
    return HistoryQuery.model_validate({"latitude": 43.0096, "longitude": -81.2737,
        "timezone": "America/Toronto", "start_at": "2026-11-01T00:00:00Z",
        "end_at": "2026-11-10T00:00:00Z", **values})


class Source:
    def __init__(self):
        self.calls = 0
        self.failure = None
        self.store = HistoryStore.from_files(FIXTURES / "EXAMPLE_ONLY_historical_traffic.csv",
                                            FIXTURES / "EXAMPLE_ONLY_history_metadata.json")

    def historical_patterns(self, query):
        self.calls += 1
        if self.failure:
            raise self.failure
        return self.store.historical_patterns(query)


@pytest.fixture
def cache_setup():
    clock, source = Clock(), Source()
    cache = HistoryCache("test", ttl=10, retention=60, max_entries=2, clock=clock)
    cache.invalidate("revision-1")
    cached = CachedHistory(source, cache, clock=clock)
    yield clock, source, cache, cached
    cache.close()


def test_cache_hit_preserves_source_dates_and_returns_independent_models(cache_setup):
    clock, source, cache, cached = cache_setup
    original, state = cached.historical_patterns_with_cache(query())
    assert state == "MISS"
    clock.advance(5)
    result, state = cached.historical_patterns_with_cache(query())
    assert state == "HIT" and source.calls == 1
    assert result.as_of == clock()
    assert result.dataset_updated_at == original.dataset_updated_at
    assert result.dataset_refresh_due_at == original.dataset_refresh_due_at
    result.items.clear()
    assert cached.historical_patterns(query()).items


def test_outage_fallback_never_rewrites_dates_or_extends_retention(cache_setup):
    clock, source, cache, cached = cache_setup
    original = cached.historical_patterns(query())
    clock.advance(11)
    source.failure = ServiceError(503, "SERVICE_UNAVAILABLE", "outage", retryable=True)
    result, state = cached.historical_patterns_with_cache(query())
    assert state == "STALE" and result.is_stale and not result.is_live
    assert result.data_status == "historical"
    assert result.dataset_updated_at == original.dataset_updated_at
    assert result.dataset_refresh_due_at == original.dataset_refresh_due_at
    clock.advance(50)
    with pytest.raises(ServiceError):
        cached.historical_patterns(query())


def test_ttl_refresh_and_event_invalidation(cache_setup):
    clock, source, cache, cached = cache_setup
    cached.historical_patterns(query())
    clock.advance(11)
    assert cached.historical_patterns_with_cache(query())[1] == "MISS"
    assert source.calls == 2
    cache.invalidate("revision-2")
    assert cached.historical_patterns_with_cache(query())[1] == "MISS"
    assert source.calls == 3


def test_error_statuses_and_unseen_queries_are_not_substituted(cache_setup):
    clock, source, cache, cached = cache_setup
    cached.historical_patterns(query())
    clock.advance(11)
    source.failure = ServiceError(404, "COVERAGE_NOT_FOUND", "no coverage")
    with pytest.raises(ServiceError) as error:
        cached.historical_patterns(query())
    assert error.value.status_code == 404
    source.failure = ServiceError(503, "SERVICE_UNAVAILABLE", "outage")
    with pytest.raises(ServiceError):
        cached.historical_patterns(query(radius_m=600))


def test_relative_windows_bypass_cache(cache_setup):
    clock, source, cache, cached = cache_setup
    relative = query(start_at=None, end_at=None)
    assert cached.historical_patterns_with_cache(relative)[1] == "BYPASS"
    assert cached.historical_patterns_with_cache(relative)[1] == "BYPASS"
    assert source.calls == 2 and cache.count() == 0


def test_cache_bounds_and_future_timestamp_eviction(cache_setup):
    clock, source, cache, cached = cache_setup
    for limit in (1, 2, 3, 4):
        cached.historical_patterns(query(limit=limit))
    assert cache.count() == 2
    clock.advance(-5)
    assert cache.count() == 0


def test_source_freshness_deadline_is_evaluated_on_cache_hits(cache_setup):
    clock, source, cache, cached = cache_setup
    original = source.historical_patterns(query())
    source.historical_patterns = lambda _: original.model_copy(update={
        "is_stale": False, "dataset_refresh_due_at": NOW + timedelta(seconds=5)})
    cached.historical_patterns(query())
    clock.advance(5)
    assert not cached.historical_patterns(query()).is_stale
    clock.advance(1)
    result, state = cached.historical_patterns_with_cache(query())
    assert state == "HIT" and result.is_stale


def test_invalidation_race_does_not_cache_old_computation(cache_setup):
    clock, source, cache, cached = cache_setup
    original = source.historical_patterns

    def invalidate_during_query(request):
        cache.invalidate("r2")
        return original(request)

    source.historical_patterns = invalidate_during_query
    result, state = cached.historical_patterns_with_cache(query())
    assert state == "STALE" and result.is_stale and cache.count() == 0


def test_corrupt_cached_payload_is_recomputed(cache_setup):
    clock, source, cache, cached = cache_setup
    cached.historical_patterns(query())
    with cache.db:
        cache.db.execute("UPDATE history_cache SET payload='broken'")
    assert cached.historical_patterns_with_cache(query())[1] == "MISS"
    assert source.calls == 2


def test_cache_concurrent_requests_are_safe(cache_setup):
    clock, source, cache, cached = cache_setup
    cached.historical_patterns(query())
    with ThreadPoolExecutor(max_workers=25) as pool:
        results = list(pool.map(lambda _: cached.historical_patterns(query()), range(75)))
    assert len(results) == 75 and all(result.items for result in results)
    assert source.calls == 1


def test_cache_restart_and_source_isolation(tmp_path):
    clock, source = Clock(), Source()
    path = tmp_path / "cache.sqlite"
    first = HistoryCache("source-a", path=path, clock=clock)
    first.invalidate("r1")
    CachedHistory(source, first, clock=clock).historical_patterns(query())
    first.close()
    source.failure = ServiceError(503, "SERVICE_UNAVAILABLE", "outage")
    second = HistoryCache("source-a", path=path, clock=clock)
    value, state = CachedHistory(source, second, clock=clock).historical_patterns_with_cache(query())
    assert state == "STALE" and value.is_stale
    second.close()
    other = HistoryCache("source-b", path=path, clock=clock)
    assert other.count() == 0
    other.close()


def test_event_validation_and_parameterized_publication():
    calls = []
    cursor = SimpleNamespace(execute=lambda *args: calls.append(args))
    published = publish_dataset_updated(cursor, dataset_id="history-v1", updated_at=NOW,
                                         refresh_due_at=NOW + timedelta(days=1), latest_observation_at=None)
    sql, parameters = calls[0]
    assert sql == "SELECT pg_notify(%s, %s)" and parameters[0] == DATASET_CHANNEL
    assert DatasetUpdated.model_validate_json(parameters[1]) == published
    with pytest.raises(ValueError):
        DatasetUpdated.model_validate({**published.model_dump(), "schema_version": 2})
    with pytest.raises(ValueError):
        DatasetUpdated.model_validate({**published.model_dump(), "dataset_refresh_due_at": NOW})


@pytest.fixture
def csv_pipeline(tmp_path):
    paths = [tmp_path / "observations.csv", tmp_path / "metadata.json"]
    paths[0].write_bytes((FIXTURES / "EXAMPLE_ONLY_historical_traffic.csv").read_bytes())
    paths[1].write_bytes((FIXTURES / "EXAMPLE_ONLY_history_metadata.json").read_bytes())
    pipeline = HistoryPipeline(None, csv_paths=paths, settings=IngestionSettings(poll_seconds=.1))
    pipeline.refresh_once()
    yield pipeline, paths
    pipeline.close()


def test_csv_refresh_is_idempotent_and_changed_file_invalidates_cache(csv_pipeline):
    pipeline, paths = csv_pipeline
    original = pipeline.historical_patterns_with_cache(query(day_of_week="monday", local_hour=8))[0]
    pipeline.refresh_once()
    assert pipeline.updates == 1
    paths[0].write_text(paths[0].read_text() + "new-segment,43.0096,-81.2737,2026-11-09T13:51:00Z,1,50\n")
    pipeline.refresh_once()
    changed, state = pipeline.historical_patterns_with_cache(query(day_of_week="monday", local_hour=8))
    assert pipeline.updates == 2 and state == "MISS"
    assert changed.items[0].observation_count == original.items[0].observation_count + 1
    assert changed.dataset_updated_at == original.dataset_updated_at


def test_invalid_refresh_preserves_last_good_dataset_and_recovers(csv_pipeline):
    pipeline, paths = csv_pipeline
    original_bytes = paths[0].read_bytes()
    original = pipeline.historical_patterns_with_cache(query())[0]
    paths[0].write_text("invalid,csv\n")
    pipeline.refresh_once()
    assert not pipeline.source_ok
    value, state = pipeline.historical_patterns_with_cache(query())
    assert state == "STALE" and value.is_stale
    assert value.dataset_updated_at == original.dataset_updated_at
    paths[0].write_bytes(original_bytes)
    pipeline.refresh_once()
    assert pipeline.source_ok
    assert pipeline.historical_patterns_with_cache(query())[1] == "HIT"


def test_missing_source_on_startup_can_use_persisted_aggregates(tmp_path):
    paths = (FIXTURES / "EXAMPLE_ONLY_historical_traffic.csv", FIXTURES / "EXAMPLE_ONLY_history_metadata.json")
    local = [tmp_path / "o.csv", tmp_path / "m.json"]
    for source, target in zip(paths, local):
        target.write_bytes(source.read_bytes())
    settings = IngestionSettings(cache_path=str(tmp_path / "cache.sqlite"))
    pipeline = HistoryPipeline(None, csv_paths=local, settings=settings)
    pipeline.refresh_once()
    pipeline.historical_patterns_with_cache(query())
    pipeline.close()
    local[0].unlink()
    restored = HistoryPipeline(None, csv_paths=local, settings=settings)
    try:
        restored.refresh_once()
        assert restored.is_ready()
        value, state = restored.historical_patterns_with_cache(query())
        assert state == "STALE" and value.is_stale
    finally:
        restored.close()


def test_worker_stops_and_retry_backoff_is_bounded(csv_pipeline):
    pipeline, paths = csv_pipeline
    paths[0].unlink()
    pipeline.start()
    deadline = time.monotonic() + 2
    while pipeline.failures == 0 and time.monotonic() < deadline:
        time.sleep(.01)
    assert pipeline.failures > 0
    for _ in range(20):
        pipeline.failed(OSError("unavailable"))
    assert pipeline.retry_seconds == 60
    pipeline.stop_event.set()
    pipeline.thread.join(timeout=1)
    assert not pipeline.thread.is_alive()


def test_corrupt_optional_cache_degrades_to_memory(tmp_path):
    path = tmp_path / "cache.sqlite"
    path.write_text("not a database")
    pipeline = HistoryPipeline(Source().store, settings=IngestionSettings(cache_path=str(path)))
    try:
        assert pipeline.cache.persistent is False
        assert path.read_text() == "not a database"
    finally:
        pipeline.close()


@pytest.mark.parametrize("setting,value", [("S2_REFRESH_SECONDS", "nan"), ("S2_REFRESH_SECONDS", "0"),
    ("S2_CACHE_TTL_SECONDS", "-1"), ("S2_CACHE_MAX_ENTRIES", "0"),
    ("S2_CACHE_RETENTION_SECONDS", "inf"), ("S2_RETRY_MAX_SECONDS", "1")])
def test_invalid_limits_fail_startup(monkeypatch, setting, value):
    monkeypatch.setenv(setting, value)
    with pytest.raises(ValueError):
        IngestionSettings.from_env()


def test_authenticated_status_and_cache_header(monkeypatch, auth_headers):
    monkeypatch.setenv("S2_HISTORY_CSV", str(FIXTURES / "EXAMPLE_ONLY_historical_traffic.csv"))
    monkeypatch.setenv("S2_HISTORY_METADATA", str(FIXTURES / "EXAMPLE_ONLY_history_metadata.json"))
    with TestClient(app) as client:
        assert client.get("/v1/traffic/ingestion/status").status_code == 401
        status = client.get("/v1/traffic/ingestion/status", headers=auth_headers)
        assert status.status_code == 200 and status.json()["source_status"] == "available"
        contract = yaml.safe_load((Path(__file__).resolve().parents[3] /
            "shared/api-contracts/s2-traffic-predictor.openapi.yaml").read_text(encoding="utf-8"))
        validator = Draft202012Validator({"$ref": "#/components/schemas/IngestionStatus",
            "components": contract["components"]}, format_checker=FormatChecker())
        validator.validate(status.json())
        assert "observations.csv" not in status.text and "postgresql://" not in status.text
        params = query().model_dump(mode="json", exclude_none=True)
        result = client.get("/v1/traffic/history/patterns", params=params, headers=auth_headers)
        assert result.status_code == 200 and result.headers["X-S2-Cache"] == "MISS"
        assert client.get("/v1/traffic/history/patterns", params=params,
                          headers=auth_headers).headers["X-S2-Cache"] == "HIT"


def test_disabled_status_matches_published_schema():
    # Authentication is exercised in test_authenticated_status_and_cache_header;
    # this checks that the disabled configuration is fully representable too.
    from app.ingestion import IngestionStatus
    value = IngestionStatus(mode="disabled", source_status="unconfigured").model_dump(mode="json")
    contract = yaml.safe_load((Path(__file__).resolve().parents[3] /
        "shared/api-contracts/s2-traffic-predictor.openapi.yaml").read_text(encoding="utf-8"))
    Draft202012Validator({"$ref": "#/components/schemas/IngestionStatus", "components": contract["components"]},
                         format_checker=FormatChecker()).validate(value)


def test_empty_refresh_keeps_previous_dataset(csv_pipeline):
    pipeline, paths = csv_pipeline
    previous = pipeline.dataset.revision
    paths[0].write_text("segment_id,latitude,longitude,observed_at,speed_kmh,free_flow_speed_kmh\n")
    pipeline.refresh_once()
    assert not pipeline.source_ok and pipeline.dataset.revision == previous
