"""Load explicitly simulated demo inputs packaged by the existing Dockerfile."""

import json
from pathlib import Path

from app.models import EstimateRequest, Facility, PricingDetails


def load_demo():
    data = json.loads((Path(__file__).parent / "fixtures" / "parking.json").read_text(encoding="utf-8"))
    if data["source"] != "simulated_fixture":
        raise ValueError("Demo data must be labeled simulated_fixture")
    facilities = [Facility.model_validate(item) for item in data["facilities"]]
    if len({item.facility_id for item in facilities}) != len(facilities):
        raise ValueError("Duplicate facility IDs in fixture")
    request = EstimateRequest.model_validate({"as_of": data["as_of"], "observations": data["observations"]})
    return facilities, request.observations, request.as_of


def load_prices(updated_at):
    data = json.loads((Path(__file__).parent / "fixtures" / "pricing.json").read_text(encoding="utf-8"))
    if data["source"] != "simulated_tariffs":
        raise ValueError("Demo tariffs must identify their simulated source")
    return {facility_id: PricingDetails.model_validate({**values,
        "source": data["source"], "data_status": "simulated", "is_live": False,
        "is_stale": True, "service_updated_at": updated_at,
    }) for facility_id, values in data["facilities"].items()}
