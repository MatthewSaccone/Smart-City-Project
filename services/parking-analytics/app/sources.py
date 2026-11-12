"""Internal aggregate-source boundary. No public raw-observation ingestion endpoint."""

import json
from pathlib import Path

from app.analytics import estimate_occupancy
from app.models import ContractModel, OccupancyEstimate


class FeedSnapshot(ContractModel):
    source: str
    simulated: bool
    items: list[OccupancyEstimate]


class FixtureSource:
    def __init__(self, observations, as_of):
        self.observations = observations
        self.as_of = as_of

    def load(self, facilities):
        return FeedSnapshot(source="simulated_fixture", simulated=True,
                            items=estimate_occupancy(facilities, self.observations, self.as_of))


class UnavailableSource:
    def load(self, facilities):
        raise TimeoutError("Occupancy source unavailable")


class FileSource:
    """Local demo adapter for previously calculated SIMULATED aggregates only."""

    def __init__(self, path):
        self.path = Path(path)

    def load(self, facilities):
        snapshot = FeedSnapshot.model_validate(json.loads(self.path.read_text(encoding="utf-8")))
        if not snapshot.simulated:
            raise ValueError("The file demo adapter cannot certify a live source")
        return snapshot
