"""T021 PostgreSQL history adapter: query/route tests without Docker.

A real-database smoke test is described in README-S2-Postgres-History.md.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.errors import ServiceError
from app.models import HistoryQuery
from app.postgres_history import (
    DATASET_ID, PostgresHistoryStore, bounding_box, nearby_params,
)


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.last_result = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params):
        self.conn.queries.append((sql, params))
        if self.conn.failure:
            raise OSError("pretend connection interruption")
        if "FROM traffic_import_metadata" in sql:
            self.last_result = self.conn.metadata
        elif "SELECT EXISTS(" in sql:
            self.last_result = (self.conn.coverage,)
        elif "GROUP BY weekday, local_hour" in sql:
            self.last_result = self.conn.aggregates
        else:
            raise AssertionError(f"Unexpected query: {sql}")

    def fetchone(self):
        return self.last_result

    def fetchall(self):
        return self.last_result


class FakeConnection:
    def __init__(self, coverage=True, aggregates=None, metadata=None, failure=False):
        self.coverage = coverage
        self.aggregates = aggregates if aggregates is not None else [(2, 11, 4, 0.4)]
        now = datetime.now(timezone.utc)
        self.metadata = metadata if metadata is not None else (
            now - timedelta(days=1), now + timedelta(days=89),
            datetime(2026, 9, 29, tzinfo=timezone.utc),
        )
        self.failure = failure
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return FakeCursor(self)


def make_store(conn):
    return PostgresHistoryStore("postgresql://demo.invalid/db", connection_factory=lambda: conn)


def make_query(**extra):
    values = dict(latitude=43.736355, longitude=-79.43891, radius_m=500,
                  timezone="America/Toronto",
                  start_at="2026-02-03T00:00:00Z", end_at="2026-02-06T00:00:00Z")
    values.update(extra)
    return HistoryQuery.model_validate(values)


def test_sql_uses_parameterized_filters_and_local_timezone():
    conn = FakeConnection()
    response = make_store(conn).historical_patterns(make_query(day_of_week="wednesday", local_hour=11))
    assert response.pagination.total == 1
    assert response.items[0].day_of_week == "wednesday"
    assert response.items[0].local_hour == 11
    assert response.items[0].observation_count == 4
    assert response.items[0].average_congestion_score == pytest.approx(.4)
    assert response.items[0].congestion_level == "moderate"
    assert response.source == "historical_traffic_dataset"
    assert response.is_live is False
    assert response.is_stale is False
    assert len(conn.queries) == 3
    metadata_sql, metadata_params = conn.queries[0]
    assert metadata_params == (DATASET_ID,)
    agg_sql, agg_params = conn.queries[2]
    assert "AT TIME ZONE %s" in agg_sql
    assert "o.vehicle_count > 0" in agg_sql
    assert "o.started_at >= %s AND o.started_at < %s" in agg_sql
    assert "GREATEST(0.0, LEAST(1.0" in agg_sql
    assert "America/Toronto" in agg_params
    assert "GROUP BY weekday, local_hour" in agg_sql
    assert "America/Toronto" not in agg_sql  # user input is bound, not interpolated


def test_missing_bucket_returns_unknown_not_zero():
    output = make_store(FakeConnection(aggregates=[])).historical_patterns(
        make_query(day_of_week="monday", local_hour=8)
    )
    item = output.items[0]
    assert item.observation_count == 0
    assert item.average_congestion_score is None
    assert item.congestion_level == "unknown"


def test_empty_coverage_is_404_but_existing_location_without_records_is_200():
    with pytest.raises(ServiceError) as exc:
        make_store(FakeConnection(coverage=False)).historical_patterns(make_query())
    assert exc.value.status_code == 404
    output = make_store(FakeConnection(coverage=True, aggregates=[])).historical_patterns(
        make_query(day_of_week="tuesday", local_hour=2)
    )
    assert output.items[0].congestion_level == "unknown"


def test_no_import_metadata_from_none_is_503():
    conn = FakeConnection()
    conn.metadata = None
    assert make_store(conn).is_ready() is False
    with pytest.raises(ServiceError) as exc:
        make_store(conn).historical_patterns(make_query())
    assert exc.value.status_code == 503


def test_failing_database_is_503_without_leaking_details():
    conn = FakeConnection(failure=True)
    assert make_store(conn).is_ready() is False
    with pytest.raises(ServiceError) as exc:
        make_store(conn).historical_patterns(make_query())
    assert exc.value.status_code == 503
    assert "pretend connection" not in exc.value.message


def test_bounding_box_contains_query_center():
    low_lat, high_lat, low_lon, high_lon = bounding_box(43.736355, -79.43891, 500)
    assert low_lat < 43.736355 < high_lat
    assert low_lon < -79.43891 < high_lon
    assert len(nearby_params(make_query())) == 8


def test_sorting_pagination_missing_values():
    groups = [(2, 11, 4, .4), (2, 12, 3, .8), (2, 13, 2, .2)]
    q = make_query(day_of_week="wednesday", sort_by="average_congestion_score",
                   sort_order="desc", limit=2, offset=0)
    out = make_store(FakeConnection(aggregates=groups)).historical_patterns(q)
    assert out.pagination.total == 24
    assert out.pagination.has_more
    assert [x.local_hour for x in out.items] == [12, 11]


def test_http_endpoint_uses_database_adapter(monkeypatch, auth_headers):
    from app.main import app
    from app import main as main_module

    conn = FakeConnection()
    monkeypatch.setenv("S2_DATABASE_URL", "postgresql://demo.invalid/db")
    monkeypatch.delenv("S2_HISTORY_CSV", raising=False)
    monkeypatch.delenv("S2_HISTORY_METADATA", raising=False)
    monkeypatch.delenv("REGISTRY_URL", raising=False)
    monkeypatch.setattr(main_module, "PostgresHistoryStore", lambda dsn: make_store(conn))

    with TestClient(app, headers=auth_headers) as client:
        response = client.get("/v1/traffic/history/patterns", params={
            "latitude": 43.736355, "longitude": -79.43891,
            "timezone": "America/Toronto", "day_of_week": "wednesday", "local_hour": 11,
            "start_at": "2026-02-03T00:00:00Z", "end_at": "2026-02-06T00:00:00Z",
        })
        assert response.status_code == 200, response.text
        assert response.json()["items"][0]["observation_count"] == 4
        assert response.json()["dataset_updated_at"].endswith("Z")
        assert response.headers["X-Request-ID"]
        assert client.get("/ready").status_code == 200
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        blocked = client.post("/v1/traffic/predictions", json={
            "location": {"latitude": 43.736355, "longitude": -79.43891},
            "forecast_time": future,
        })
        assert blocked.status_code == 503
