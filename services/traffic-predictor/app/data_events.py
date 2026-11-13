"""S2-owned dataset-change hints. Historical metadata remains authoritative."""

from datetime import datetime
import hashlib
import json
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from .models import ContractModel


DATASET_CHANNEL = "s2_dataset_updated_v1"


class DatasetUpdated(ContractModel):
    schema_version: Literal[1] = 1
    dataset_id: str = Field(min_length=1, max_length=128)
    dataset_updated_at: AwareDatetime
    dataset_refresh_due_at: AwareDatetime
    latest_observation_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def ordered(self):
        if self.dataset_refresh_due_at <= self.dataset_updated_at:
            raise ValueError("Dataset refresh deadline must follow its update")
        return self

    @property
    def revision(self):
        encoded = json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()
        return hashlib.sha256(encoded).hexdigest()


def publish_dataset_updated(cursor, *, dataset_id: str, updated_at: datetime,
                            refresh_due_at: datetime, latest_observation_at: datetime | None):
    event = DatasetUpdated(dataset_id=dataset_id, dataset_updated_at=updated_at,
                           dataset_refresh_due_at=refresh_due_at,
                           latest_observation_at=latest_observation_at)
    # Delivered only if the caller commits its metadata transaction. Payloads
    # contain no database URLs, credentials, road records or personal traces.
    cursor.execute("SELECT pg_notify(%s, %s)", (DATASET_CHANNEL, event.model_dump_json()))
    return event
