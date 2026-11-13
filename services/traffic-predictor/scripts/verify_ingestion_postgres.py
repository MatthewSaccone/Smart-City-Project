"""S2 T010 integration against disposable PostgreSQL, with two independent consumers."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import uuid
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
SERVICE = ROOT / "services/traffic-predictor"
sys.path.insert(0, str(SERVICE))

import psycopg
from app.data_events import DATASET_CHANNEL
from app.ingestion import HistoryPipeline, IngestionSettings
from app.models import HistoryQuery
from app.postgres_history import PostgresHistoryStore
from app.errors import ServiceError


def wait_for(predicate, description, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    raise TimeoutError(description)


def verify():
    project = "scms-s2-ingest-" + uuid.uuid4().hex[:10]
    env = os.environ.copy()
    env["S2_DB_PASSWORD"] = secrets.token_hex(24)
    report = {"verified_at": datetime.now(timezone.utc).isoformat(),
              "data": "synthetic road observations in isolated S2-owned PostgreSQL",
              "consumers": 2, "production_verified": False}
    with tempfile.TemporaryDirectory(prefix="scms-s2-ingestion-") as directory:
        override = Path(directory) / "ports.yaml"
        override.write_text("services:\n  s2-postgres:\n    ports: !override\n      - '127.0.0.1::5432'\n")
        command = ["docker", "compose", "-p", project, "-f", str(ROOT / "docker-compose.s2-db.yml"),
                   "-f", str(override)]

        def compose(*args, capture=False):
            return subprocess.run([*command, *args], cwd=ROOT, env=env, check=True, text=True,
                                  stdout=subprocess.PIPE if capture else None).stdout

        pipelines = []
        try:
            compose("up", "-d", "--wait", "--wait-timeout", "60")
            port = compose("port", "s2-postgres", "5432", capture=True).strip().rsplit(":", 1)[1]
            dsn = f"postgresql://traffic_predictor:{env['S2_DB_PASSWORD']}@127.0.0.1:{port}/traffic_predictor"
            module_spec = importlib.util.spec_from_file_location("t010_importer", SERVICE / "scripts/import_toronto_midblock.py")
            importer = importlib.util.module_from_spec(module_spec)
            module_spec.loader.exec_module(importer)
            with psycopg.connect(dsn) as conn:
                conn.execute((SERVICE / "sql/001_toronto_midblock.sql").read_text())
                conn.execute("""INSERT INTO traffic_studies VALUES
                    (1,'ATR_SPEED_VOLUME',100,'synthetic-test-road',43.736355,-79.43891,
                     '2026-11-02','2026-11-09',20,50,1)""")
                conn.execute("""INSERT INTO speed_observations VALUES
                    (1,1,'2026-11-02T13:00:00Z','2026-11-02T13:15:00Z','WB','{}',1,20,'America/Toronto')""")
                importer.record_import_metadata(conn, ZoneInfo("America/Toronto"), 90)
            settings = IngestionSettings(poll_seconds=.2, retry_max_seconds=1,
                                          cache_path=str(Path(directory) / "aggregates.sqlite"))
            first = HistoryPipeline(PostgresHistoryStore(dsn), database_url=dsn, settings=settings)
            second = HistoryPipeline(PostgresHistoryStore(dsn), database_url=dsn,
                                      settings=IngestionSettings(poll_seconds=.2, retry_max_seconds=1))
            pipelines.extend((first, second))
            for pipeline in pipelines:
                pipeline.start()
            wait_for(lambda: all(p.connected and p.source_ok for p in pipelines), "Consumers did not subscribe")
            query = HistoryQuery(latitude=43.736355, longitude=-79.43891, timezone="America/Toronto",
                                 day_of_week="monday", local_hour=8,
                                 start_at="2026-11-01T00:00:00Z", end_at="2026-11-10T00:00:00Z")
            for pipeline in pipelines:
                value, state = pipeline.historical_patterns_with_cache(query)
                assert state == "MISS" and value.items[0].average_congestion_score == .6
                assert pipeline.historical_patterns_with_cache(query)[1] == "HIT"
            with psycopg.connect(dsn) as conn:
                conn.execute("UPDATE speed_observations SET estimated_avg_speed_kph=40")
                importer.record_import_metadata(conn, ZoneInfo("America/Toronto"), 90)
            wait_for(lambda: all(p.notifications >= 1 and p.updates >= 2 for p in pipelines), "Dataset event was not consumed")
            for pipeline in pipelines:
                updated, state = pipeline.historical_patterns_with_cache(query)
                assert state == "MISS" and abs(updated.items[0].average_congestion_score - .2) < .00001
            report["committed_import_invalidated_both_consumers"] = True
            counts = [p.updates for p in pipelines]
            with psycopg.connect(dsn) as conn:
                conn.execute("SELECT pg_notify(%s, %s)", (DATASET_CHANNEL, first.dataset.model_dump_json()))
            wait_for(lambda: all(p.notifications >= 2 for p in pipelines), "Duplicate event not received")
            assert [p.updates for p in pipelines] == counts
            report["duplicate_events_are_idempotent"] = True
            with psycopg.connect(dsn) as conn:
                conn.execute("SELECT pg_notify(%s, %s)", (DATASET_CHANNEL, '{"schema_version":999}'))
            wait_for(lambda: all(p.rejected_notifications >= 1 for p in pipelines), "Malformed event not rejected")
            report["malformed_events_rejected"] = True
            # A legacy writer may update metadata without publishing a hint.
            with psycopg.connect(dsn) as conn:
                conn.execute("UPDATE traffic_import_metadata SET imported_at=clock_timestamp(), "
                             "refresh_due_at=clock_timestamp()+interval '90 days'")
            wait_for(lambda: all(p.updates > counts[i] for i, p in enumerate(pipelines)),
                     "Periodic reconciliation missed a metadata-only change")
            report["metadata_only_update_reconciled_without_notification"] = True
            counts = [p.updates for p in pipelines]
            # Take one subscriber offline, update metadata, and reconnect it.
            first.stop_event.set()
            first.thread.join(timeout=5)
            assert not first.thread.is_alive()
            with psycopg.connect(dsn) as conn:
                importer.record_import_metadata(conn, ZoneInfo("America/Toronto"), 90)
            first.stop_event.clear()
            first.start()
            wait_for(lambda: first.source_ok and first.updates > counts[0], "Reconnect did not reconcile missed update")
            report["missed_event_reconciled_from_durable_metadata"] = True
            original, _ = first.historical_patterns_with_cache(query)

            def read(_):
                started = time.perf_counter()
                value, state = first.historical_patterns_with_cache(query)
                assert value.items and state == "HIT"
                return (time.perf_counter() - started) * 1000

            with ThreadPoolExecutor(max_workers=25) as pool:
                timings = sorted(pool.map(read, range(75)))
            report["cached_history_calls"] = {"requests": 75, "client_workers": 25,
                                               "max_ms": round(timings[-1], 3), "errors": 0,
                                               "includes_http": False}
            compose("stop", "--timeout", "5", "s2-postgres")
            wait_for(lambda: not first.source_ok, "Outage not detected")
            fallback, state = first.historical_patterns_with_cache(query)
            assert state == "STALE" and fallback.is_stale and fallback.is_live is False
            assert fallback.dataset_updated_at == original.dataset_updated_at
            assert fallback.dataset_refresh_due_at == original.dataset_refresh_due_at
            report["outage_preserves_aggregate_and_source_dates"] = True
            first.close()
            pipelines.remove(first)
            restored = HistoryPipeline(PostgresHistoryStore(dsn), database_url=dsn, settings=settings)
            pipelines.append(restored)
            fallback, state = restored.historical_patterns_with_cache(query)
            assert state == "STALE" and fallback.dataset_updated_at == original.dataset_updated_at
            report["persistent_cache_restart_fallback_verified"] = True
            try:
                restored.historical_patterns_with_cache(query.model_copy(update={"radius_m": 999}))
            except ServiceError as exc:
                assert exc.status_code == 503
            else:
                raise AssertionError("An uncached query must not receive unrelated data")
        finally:
            for pipeline in pipelines:
                pipeline.close()
            compose("down", "--volumes", "--remove-orphans", "--timeout", "5")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = verify()
    encoded = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded)
