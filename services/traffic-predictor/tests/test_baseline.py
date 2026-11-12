"""Candidate baseline logic tests; these do not require live PostgreSQL."""
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path

import pytest
from app.baseline import (cell_for, score_for_speed, select_bucket, aligned_slots, merge_periods,
                          PostgresBaselinePredictor, TORONTO_TZ)
from app.errors import ServiceError
from app.models import PointPredictionRequest, HighPeriodsQuery, AreaQuery

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_s2_baseline.py"
spec = importlib.util.spec_from_file_location("build_s2_baseline", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_congestion_scores_and_grid_are_deterministic():
    assert score_for_speed(40.0, 50.0) == pytest.approx(.2)
    assert score_for_speed(55.0, 50.0) == 0.0
    assert score_for_speed(0.0, 50.0) == 1.0
    assert cell_for(43.736355, -79.43891, 20) == (2186, -3972)
    with pytest.raises(ValueError):
        score_for_speed(30, 0)


def test_fallback_to_coarse_cell_when_fine_cell_sparse():
    lat, lon = 43.736355, -79.43891
    a,b = cell_for(lat, lon, 20)
    c,d = cell_for(lat, lon, 100)
    rows = [(20,a,b,.9,2), (100,c,d,.3,50)]
    assert select_bucket(rows, lat,lon) == (.3,50,100)
    rows[0] = (20,a,b,.75,7)
    assert select_bucket(rows,lat,lon) == (.75,7,20)
    assert select_bucket([],lat,lon) is None


def test_diagnostic_macro_f1_and_coverage():
    result = module.diagnostic_metrics([
        ("low","low",3), ("moderate","high",1), ("high", None,2),
    ])
    assert result["test_observations"] == 6
    assert result["evaluated_observations"] == 4
    assert result["coverage"] == pytest.approx(4/6)
    assert result["per_class"]["low"]["f1"] == 1.0
    assert result["per_class"]["high"]["recall"] == 0.0


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.kind = ""
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, args):
        self.conn.calls.append((sql, args))
        if "FROM s2_baseline_models" in sql: self.kind = "meta"
        elif "FROM traffic_studies" in sql: self.kind = "roads"
        elif "FROM s2_baseline_buckets" in sql: self.kind = "buckets"
        else: raise AssertionError("unexpected SQL")
    def fetchone(self):
        if self.kind == "meta": return self.conn.metadata
        raise AssertionError("fetchone called for non-meta")
    def fetchall(self):
        if self.kind == "roads": return self.conn.roads
        if self.kind == "buckets": return self.conn.buckets
        raise AssertionError("fetchall called for other SQL")


class FakeConn:
    def __init__(self, metadata, roads=None, buckets=None):
        self.metadata = metadata
        self.roads = roads if roads is not None else [(3015417, 43.736355, -79.43891)]
        self.buckets = buckets if buckets is not None else []
        self.calls = []
    def __enter__(self): return self
    def __exit__(self,*a): return False
    def cursor(self): return FakeCursor(self)


def make_request():
    return PointPredictionRequest(location={"latitude":43.736355,"longitude":-79.43891},
                                  forecast_time=datetime.now(timezone.utc)+timedelta(hours=4))


def test_unapproved_model_is_unavailable_even_with_buckets():
    conn = FakeConn((datetime.now(timezone.utc)-timedelta(days=1),
                     datetime.now(timezone.utc)+timedelta(days=30), False))
    backend = PostgresBaselinePredictor("postgres://fake", "candidate", lambda:conn)
    assert backend.is_ready() is False
    with pytest.raises(ServiceError) as exc:
        backend.predict_point(make_request())
    assert exc.value.status_code == 503


def test_prediction_when_approved_and_historical_bucket_present():
    now = datetime.now(timezone.utc)
    payload = make_request()
    lat, lon = cell_for(43.736355,-79.43891,20)
    local = payload.forecast_time.astimezone(TORONTO_TZ)
    conn = FakeConn((now-timedelta(days=1),now+timedelta(days=30),True),
                    buckets=[(20,lat,lon,local.weekday(),local.hour,.65,20)])
    backend = PostgresBaselinePredictor("postgres://fake", "accepted-v1", lambda:conn)
    assert backend.is_ready() is True
    output = backend.predict_point(payload)
    assert output.prediction.congestion_level == "high"
    assert output.prediction.estimated_congestion_score == pytest.approx(.65)
    assert output.model_version == "accepted-v1"
    assert output.source == "historical_traffic_model"
    assert output.is_live is False
    assert len(conn.calls) == 4  # readiness metadata; prediction metadata, roads, buckets


def test_no_road_coverage_returns_404():
    now = datetime.now(timezone.utc)
    conn = FakeConn((now-timedelta(days=1),now+timedelta(days=30),True), roads=[])
    backend = PostgresBaselinePredictor("postgres://fake", "accepted-v1", lambda:conn)
    with pytest.raises(ServiceError) as exc:
        backend.predict_point(make_request())
    assert exc.value.status_code == 404


def test_30_minute_utc_aligned_clipped_slots_and_merge():
    start = datetime(2026, 11, 13, 12, 12, tzinfo=timezone.utc)
    end = datetime(2026, 11, 13, 13, 17, tzinfo=timezone.utc)
    slots = list(aligned_slots(start, end))
    assert slots == [
        (start, datetime(2026, 11, 13, 12, 30, tzinfo=timezone.utc)),
        (datetime(2026, 11, 13, 12, 30, tzinfo=timezone.utc),
         datetime(2026, 11, 13, 13, 0, tzinfo=timezone.utc)),
        (datetime(2026, 11, 13, 13, 0, tzinfo=timezone.utc), end),
    ]
    assert merge_periods(slots) == [(start, end)]


def test_area_predictions_with_unknown_rows_and_pagination():
    now = datetime.now(timezone.utc)
    when = now + timedelta(hours=4)
    local = when.astimezone(TORONTO_TZ)
    c1, c2 = cell_for(43.736355, -79.43891, 20)
    conn = FakeConn((now-timedelta(days=1),now+timedelta(days=30),True),
                    roads=[(101,43.736355,-79.43891), (102,43.7365,-79.439)],
                    buckets=[(20,c1,c2,local.weekday(),local.hour,.65,10)])
    backend = PostgresBaselinePredictor('postgres://fake','p75-v1',lambda:conn)
    result = backend.area_predictions(AreaQuery(latitude=43.736355,longitude=-79.43891,
                                               radius_m=500,forecast_time=when,
                                               limit=1,offset=0))
    assert result.pagination.total == 2
    assert result.pagination.has_more is True
    assert result.items[0].estimated_congestion_score == pytest.approx(.65)
    assert result.items[0].congestion_level == 'high'
    assert result.items[0].segment_id == '101'


def test_area_unknown_when_no_historical_bucket():
    now = datetime.now(timezone.utc)
    conn = FakeConn((now-timedelta(days=1),now+timedelta(days=30),True))
    backend = PostgresBaselinePredictor('postgres://fake','p75-v1',lambda:conn)
    result = backend.area_predictions(AreaQuery(latitude=43.736355,longitude=-79.43891,
                                               radius_m=500,forecast_time=now+timedelta(hours=3)))
    assert result.items[0].congestion_level == 'unknown'
    assert result.items[0].estimated_congestion_score is None


def test_high_periods_merge_two_adjacent_slots():
    now = datetime.now(timezone.utc)
    start = (now + timedelta(hours=3)).replace(minute=0,second=0,microsecond=0)
    end = start + timedelta(hours=1)
    local = start.astimezone(TORONTO_TZ)
    c1,c2 = cell_for(43.736355,-79.43891,20)
    conn = FakeConn((now-timedelta(days=1),now+timedelta(days=30),True),
                    buckets=[(20,c1,c2,local.weekday(),local.hour,.71,8)])
    backend = PostgresBaselinePredictor('postgres://fake','p75-v1',lambda:conn)
    result = backend.high_congestion_periods(HighPeriodsQuery(latitude=43.736355,
                      longitude=-79.43891,radius_m=500,start_at=start,end_at=end))
    assert result.pagination.total == 1
    assert result.items[0].start_at == start
    assert result.items[0].end_at == end
    assert result.items[0].congestion_level == 'high'


def test_high_periods_no_coverage_is_not_misreported_as_no_high():
    now = datetime.now(timezone.utc)
    start = now + timedelta(hours=2)
    conn = FakeConn((now-timedelta(days=1),now+timedelta(days=30),True))
    backend = PostgresBaselinePredictor('postgres://fake','p75-v1',lambda:conn)
    with pytest.raises(ServiceError) as exc:
        backend.high_congestion_periods(HighPeriodsQuery(latitude=43.736355,
          longitude=-79.43891,radius_m=500,start_at=start,end_at=start+timedelta(minutes=30)))
    assert exc.value.status_code == 503


def test_unapproved_model_can_only_be_previewed_with_explicit_opt_in():
    now = datetime.now(timezone.utc)
    conn = FakeConn((now-timedelta(days=1),now+timedelta(days=30),False))
    blocked = PostgresBaselinePredictor('postgres://fake','p75-v1',lambda:conn,
                                        experimental_preview=False)
    preview = PostgresBaselinePredictor('postgres://fake','p75-v1',lambda:conn,
                                        experimental_preview=True)
    assert blocked.is_ready() is False
    assert preview.is_ready() is True


def test_percentile_training_and_evaluation_reference_new_column():
    assert 'PERCENTILE_CONT(0.75)' in module.TRAIN_BUCKETS
    assert 'p75_congestion_score' in module.TRAIN_BUCKETS
    assert 'fine.p75_congestion_score' in module.EVALUATE_SQL
    assert module.P75_MIGRATION.exists()
