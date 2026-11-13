"""Versioned S4-owned inputs. No raw user observations are accepted here."""

import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from app.models import ContractModel, Facility, ParkingFee, ParkingRate, Timestamp
from app.sources import FeedSnapshot


Stream = Literal["catalog", "pricing", "occupancy"]
CHANNEL = "s4_parking_updated_v1"
MAX_PAYLOAD_BYTES = 8 * 1024 * 1024


class CatalogFeed(ContractModel):
    schema_version: Literal[1] = 1
    source: str = Field(min_length=1, max_length=160)
    simulated: bool
    source_updated_at: Timestamp | None = None
    facilities: list[Facility] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({item.facility_id for item in self.facilities}) != len(self.facilities):
            raise ValueError("Duplicate catalog IDs")
        return self


class Tariff(ContractModel):
    currency: Literal["CAD"] | None = None
    rates: list[ParkingRate] = Field(default_factory=list, max_length=100)
    additional_fees: list[ParkingFee] = Field(default_factory=list, max_length=100)
    tax_included: bool | None = None
    source_updated_at: Timestamp | None = None

    @model_validator(mode="after")
    def coherent_rates(self):
        if (self.rates or self.additional_fees) and self.currency is None:
            raise ValueError("Known tariffs require a currency")
        if len({item.rate_id for item in self.rates}) != len(self.rates) or len({item.fee_id for item in self.additional_fees}) != len(self.additional_fees):
            raise ValueError("Duplicate tariff IDs")
        return self


class PricingFeed(ContractModel):
    schema_version: Literal[1] = 1
    source: str = Field(min_length=1, max_length=160)
    simulated: bool
    facilities: dict[str, Tariff] = Field(max_length=10000)


def validate_feed(stream: Stream, payload):
    model = {"catalog": CatalogFeed, "pricing": PricingFeed, "occupancy": FeedSnapshot}.get(stream)
    if model is None:
        raise ValueError("Unknown S4 stream")
    feed = model.model_validate(payload)
    if len(feed.model_dump_json().encode()) > MAX_PAYLOAD_BYTES:
        raise ValueError("S4 payload exceeds byte limit")
    return feed


def revision(feed):
    return hashlib.sha256(json.dumps(feed.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()


class DatasetChanged(ContractModel):
    schema_version: Literal[1] = 1
    stream: Stream
    revision: str = Field(pattern="^[a-f0-9]{64}$")


class StoredFeed(ContractModel):
    payload: dict
    received_at: Timestamp
