"""Independent catalog/pricing and aggregate occupancy, with honest outage fallback."""

import logging
import os
import threading

from fastapi import HTTPException

from app.cache import AggregateCache, CachedSnapshot
from app.catalog import load_demo, load_prices
from app.models import FacilityDetailResponse, OccupancySnapshot, PricingDetails, utc_now
from app.sources import FileSource, FixtureSource, UnavailableSource


logger = logging.getLogger("parking")
OCCUPANCY_FRESH_SECONDS = 300
PRICE_FRESH_SECONDS = 86400


def stale(timestamp, now, threshold):
    # Unknown/future timestamps cannot establish freshness.
    return timestamp is None or not 0 <= (now - timestamp).total_seconds() <= threshold


class ParkingService:
    def __init__(self, *, source=None, cache_path=None, clock=utc_now):
        self.clock = clock
        self.lock = threading.RLock()
        self.facilities = []
        self.prices = {}
        self.source = source
        self.catalog_available = False
        self.feed_available = False
        self.snapshot = None
        self.cache = None
        try:
            self.facilities, observations, as_of = load_demo()
            self.catalog_available = True
            if self.source is None:
                mode = os.getenv("PARKING_OCCUPANCY_MODE", "fixture")
                if mode == "fixture":
                    self.source = FixtureSource(observations, as_of)
                elif mode == "unavailable":
                    self.source = UnavailableSource()
                elif mode == "file":
                    self.source = FileSource(os.environ["PARKING_OCCUPANCY_FILE"])
                else:
                    raise ValueError("Invalid PARKING_OCCUPANCY_MODE")
        except Exception as exc:
            logger.warning("Catalog/source setup failed: %s", type(exc).__name__)
            self.source = UnavailableSource()
        try:
            self.prices = load_prices(self.clock())
        except Exception as exc:
            logger.warning("Tariff load unavailable: %s", type(exc).__name__)
        try:
            self.cache = AggregateCache(cache_path)
            self.snapshot = self.cache.read()
            if self.snapshot is not None:
                self._validate_feed(self.snapshot)
        except Exception as exc:
            logger.warning("Aggregate cache unavailable: %s", type(exc).__name__)
            self.snapshot = None
            self.cache = AggregateCache()

    def require_catalog(self):
        if not self.catalog_available:
            raise HTTPException(503, "Parking facility catalog unavailable")

    def _validate_feed(self, feed):
        facilities = {item.facility_id: item for item in self.facilities}
        if len({item.facility_id for item in feed.items}) != len(feed.items):
            raise ValueError("Duplicate aggregate facility IDs")
        for item in feed.items:
            facility = facilities.get(item.facility_id)
            if facility is None:
                raise ValueError("Unknown aggregate facility")
            occupied, available = item.estimated_occupied_spaces, item.estimated_available_spaces
            if occupied is None:
                if available is not None or item.occupancy_ratio is not None or item.congestion != "unknown":
                    raise ValueError("Inconsistent unknown aggregate")
            elif (occupied > facility.capacity or available != facility.capacity - occupied
                  or item.occupancy_ratio is None
                  or abs(item.occupancy_ratio - occupied / facility.capacity) > 0.0001):
                raise ValueError("Aggregate does not match catalog capacity")

    def refresh(self):
        if not self.catalog_available:
            return
        try:
            feed = self.source.load(self.facilities)
            self._validate_feed(feed)
            with self.lock:
                if self.snapshot is None or feed.model_dump() != self.snapshot.model_dump(exclude={"service_updated_at"}):
                    snapshot = CachedSnapshot(**feed.model_dump(), service_updated_at=self.clock())
                    self.cache.write(snapshot)
                    self.snapshot = snapshot
                self.feed_available = True
        except Exception as exc:
            with self.lock:
                self.feed_available = False
            logger.warning("Occupancy refresh unavailable: %s", type(exc).__name__)

    def detail(self, facility, now):
        with self.lock:
            cached = self.snapshot
            item = next((item for item in cached.items if item.facility_id == facility.facility_id), None) if cached else None
            if item is None or item.estimated_occupied_spaces is None:
                occupancy = OccupancySnapshot()
            else:
                is_stale = stale(item.latest_observation_at, now, OCCUPANCY_FRESH_SECONDS)
                kind = "simulated" if cached.simulated else "live" if self.feed_available and not is_stale else "cached"
                occupancy = OccupancySnapshot(
                    estimated_occupied_spaces=item.estimated_occupied_spaces,
                    estimated_available_spaces=item.estimated_available_spaces,
                    occupancy_ratio=item.occupancy_ratio, congestion=item.congestion,
                    observed_at=item.latest_observation_at, service_updated_at=cached.service_updated_at,
                    source=cached.source, data_status=kind, is_live=kind == "live", is_stale=is_stale,
                )
            price = self.prices.get(facility.facility_id, PricingDetails())
            price = price.model_copy(update={"is_stale": stale(price.source_updated_at, now, PRICE_FRESH_SECONDS)})
        availability = "unknown"
        if facility.facility_status == "closed":
            availability = "closed"
        elif (facility.facility_status == "open" and occupancy.data_status == "live" and not occupancy.is_stale
              and occupancy.estimated_available_spaces is not None):
            availability = "available" if occupancy.estimated_available_spaces > 0 else "full"
        return FacilityDetailResponse(facility=facility, availability_status=availability,
                                      occupancy=occupancy, pricing=price, as_of=now,
                                      facility_source="simulated_fixture")
