from datetime import datetime, timedelta, timezone
import json

import pytest

from app.catalog import load_demo
from app.models import PricingDetails
from app.service import ParkingService
from app.sources import FixtureSource, UnavailableSource
from test_s4_contract import validate
from test_parking import BOUNDS


NOW = datetime(2026, 11, 11, 18, tzinfo=timezone.utc)


class TestFeed:
    __test__ = False

    def __init__(self):
        self.failure = False
        facilities, observations, as_of = load_demo()
        feed = FixtureSource(observations, as_of).load(facilities)
        self.feed = feed.model_copy(update={"source": "test_provider", "simulated": False,
            "items": [item.model_copy(update={"latest_observation_at": NOW})
                      if item.latest_observation_at else item for item in feed.items]})

    def load(self, facilities):
        if self.failure:
            raise TimeoutError("Test-only feed outage")
        return self.feed


def live_service(cache_path=None):
    source = TestFeed()
    service = ParkingService(source=source, cache_path=cache_path, clock=lambda: NOW)
    service.refresh()
    return service, source


def test_outage_keeps_cached_occupancy_rates_and_catalog_and_recovers(client):
    service, source = live_service()
    client.app.state.parking = service
    before = client.get("/v1/parking/facilities/demo-central").json()
    assert before["availability_status"] == "available" and before["occupancy"]["data_status"] == "live"
    source.failure = True
    service.refresh()
    detail = client.get("/v1/parking/facilities/demo-central")
    assert detail.status_code == 200
    validate(detail, "FacilityDetailResponse")
    body = detail.json()
    assert body["occupancy"]["data_status"] == "cached" and body["occupancy"]["is_live"] is False
    assert body["occupancy"]["observed_at"] == before["occupancy"]["observed_at"]
    assert body["occupancy"]["service_updated_at"] == before["occupancy"]["service_updated_at"]
    assert body["pricing"] == before["pricing"]
    assert body["availability_status"] == "unknown"
    assert client.get("/v1/parking/facilities", params=BOUNDS).status_code == 200
    assert client.get("/ready").status_code == 200
    source.failure = False
    service.refresh()
    assert client.get("/v1/parking/facilities/demo-central").json()["availability_status"] == "available"


def test_no_live_feed_and_no_cache_is_explicitly_unknown(client):
    service = ParkingService(source=UnavailableSource())
    service.refresh()
    client.app.state.parking = service
    body = client.get("/v1/parking/facilities/demo-central").json()
    assert body["occupancy"]["data_status"] == "unavailable"
    assert body["occupancy"]["estimated_available_spaces"] is None
    assert body["occupancy"]["observed_at"] is None
    assert body["pricing"]["rates"] and body["facility"]["latitude"] == 43.6532
    assert client.get("/ready").status_code == 200


def test_persisted_aggregate_survives_restart_and_never_contains_raw_subjects(tmp_path):
    path = str(tmp_path / "parking.sqlite")
    service, _ = live_service(path)
    before = service.detail(service.facilities[0], NOW)
    restarted = ParkingService(source=UnavailableSource(), cache_path=path, clock=lambda: NOW)
    restarted.refresh()
    after = restarted.detail(restarted.facilities[0], NOW)
    assert after.occupancy.estimated_occupied_spaces == before.occupancy.estimated_occupied_spaces == 2
    assert after.occupancy.service_updated_at == before.occupancy.service_updated_at
    assert after.occupancy.data_status == "cached"
    payload = restarted.cache.read().model_dump_json()
    assert "subject_id" not in payload and "demo-a" not in payload and "observations" not in payload


@pytest.mark.parametrize("seconds,is_stale,kind", [(300, False, "live"), (301, True, "cached")])
def test_occupancy_freshness_boundaries(seconds, is_stale, kind):
    service, _ = live_service()
    detail = service.detail(service.facilities[0], NOW + timedelta(seconds=seconds))
    assert detail.occupancy.is_stale is is_stale
    assert detail.occupancy.data_status == kind
    assert detail.occupancy.observed_at == NOW


@pytest.mark.parametrize("age,is_stale", [(86400, False), (86401, True), (None, True), (-1, True)])
def test_pricing_freshness_is_independent_and_never_fabricates_source_time(age, is_stale):
    service, _ = live_service()
    timestamp = NOW - timedelta(seconds=age) if age is not None else None
    service.prices["demo-central"] = service.prices["demo-central"].model_copy(update={"source_updated_at": timestamp})
    detail = service.detail(service.facilities[0], NOW)
    assert detail.pricing.is_stale is is_stale
    assert detail.pricing.source_updated_at == timestamp
    assert detail.occupancy.is_stale is False


def test_missing_catalog_fails_readiness_and_reads_but_not_liveness(client):
    client.app.state.parking.catalog_available = False
    for path in ("/ready", "/v1/parking/facilities/demo-central"):
        response = client.get(path)
        assert response.status_code == 503
        validate(response, "ErrorResponse")
    assert client.get("/health").status_code == 200


def test_unchanged_feed_does_not_rewrite_update_timestamp():
    service, _ = live_service()
    timestamp = service.snapshot.service_updated_at
    service.clock = lambda: NOW + timedelta(seconds=20)
    service.refresh()
    assert service.snapshot.service_updated_at == timestamp


def test_missing_tariffs_do_not_prevent_occupancy_or_readiness(client):
    service, _ = live_service()
    service.prices = {}
    client.app.state.parking = service
    detail = client.get("/v1/parking/facilities/demo-central")
    validate(detail, "FacilityDetailResponse")
    assert detail.json()["pricing"]["currency"] is None
    assert detail.json()["occupancy"]["data_status"] == "live"
    assert client.get("/ready").status_code == 200
