
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models import (
    IncidentCreateRequest,
    IncidentLocation,
    IncidentListQuery,
    IncidentPatchRequest,
)


def valid_create_payload():
    return {
        "incident_type": "construction",
        "location": {
            "latitude": 43.0096,
            "longitude": -81.2737,
        },
        "description": (
            "Road construction is blocking the westbound lane."
        ),
    }


def test_valid_incident_create():
    request = IncidentCreateRequest(
        **valid_create_payload()
    )

    assert request.incident_type == "construction"
    assert request.location.latitude == 43.0096
    assert request.location.longitude == -81.2737


def test_all_incident_types_are_supported():
    for incident_type in (
        "construction",
        "accident",
        "other_traffic",
    ):
        payload = valid_create_payload()
        payload["incident_type"] = incident_type

        request = IncidentCreateRequest(**payload)

        assert request.incident_type == incident_type


def test_invalid_incident_type_is_rejected():
    payload = valid_create_payload()
    payload["incident_type"] = "fire"

    with pytest.raises(ValidationError):
        IncidentCreateRequest(**payload)


@pytest.mark.parametrize(
    "latitude",
    [-90, 90],
)
def test_valid_latitude_boundaries(latitude):
    location = IncidentLocation(
        latitude=latitude,
        longitude=0,
    )

    assert location.latitude == latitude


@pytest.mark.parametrize(
    "longitude",
    [-180, 180],
)
def test_valid_longitude_boundaries(longitude):
    location = IncidentLocation(
        latitude=0,
        longitude=longitude,
    )

    assert location.longitude == longitude


@pytest.mark.parametrize(
    "latitude",
    [-90.01, 90.01],
)
def test_invalid_latitude_is_rejected(latitude):
    with pytest.raises(ValidationError):
        IncidentLocation(
            latitude=latitude,
            longitude=0,
        )


@pytest.mark.parametrize(
    "longitude",
    [-180.01, 180.01],
)
def test_invalid_longitude_is_rejected(longitude):
    with pytest.raises(ValidationError):
        IncidentLocation(
            latitude=0,
            longitude=longitude,
        )


@pytest.mark.parametrize(
    "coordinate",
    [float("nan"), float("inf"), float("-inf")],
)
def test_non_finite_coordinates_are_rejected(coordinate):
    with pytest.raises(ValidationError):
        IncidentLocation(
            latitude=coordinate,
            longitude=0,
        )


def test_description_is_trimmed():
    payload = valid_create_payload()
    payload["description"] = "   Road construction blocks traffic.   "

    request = IncidentCreateRequest(**payload)

    assert request.description == (
        "Road construction blocks traffic."
    )


@pytest.mark.parametrize(
    "description",
    [
        "",
        "         ",
        "Short",
    ],
)
def test_short_or_blank_description_is_rejected(description):
    payload = valid_create_payload()
    payload["description"] = description

    with pytest.raises(ValidationError):
        IncidentCreateRequest(**payload)


def test_description_over_2000_characters_is_rejected():
    payload = valid_create_payload()
    payload["description"] = "A" * 2001

    with pytest.raises(ValidationError):
        IncidentCreateRequest(**payload)


def test_client_cannot_supply_owner_id():
    payload = valid_create_payload()
    payload["owner_id"] = "attacker"

    with pytest.raises(ValidationError):
        IncidentCreateRequest(**payload)


@pytest.mark.parametrize(
    "forbidden_field,value",
    [
        ("owner", "attacker"),
        ("owner_id", "attacker"),
        ("user_id", "attacker"),
        ("status", "resolved"),
        ("submitted_at", "2026-11-10T18:00:00Z"),
        ("updated_at", "2026-11-10T18:00:00Z"),
    ],
)
def test_server_managed_fields_are_rejected(
    forbidden_field,
    value,
):
    payload = valid_create_payload()
    payload[forbidden_field] = value

    with pytest.raises(ValidationError):
        IncidentCreateRequest(**payload)


def test_timezone_aware_observed_at_is_accepted():
    payload = valid_create_payload()
    payload["observed_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=10)
    )

    request = IncidentCreateRequest(**payload)

    assert request.observed_at.tzinfo is not None


def test_naive_observed_at_is_rejected():
    payload = valid_create_payload()
    payload["observed_at"] = datetime.now()

    with pytest.raises(ValidationError):
        IncidentCreateRequest(**payload)


def test_observed_at_more_than_five_minutes_in_future_is_rejected():
    payload = valid_create_payload()
    payload["observed_at"] = (
        datetime.now(timezone.utc) + timedelta(minutes=6)
    )

    with pytest.raises(ValidationError):
        IncidentCreateRequest(**payload)


def test_observed_at_within_five_minutes_in_future_is_accepted():
    payload = valid_create_payload()
    payload["observed_at"] = (
        datetime.now(timezone.utc) + timedelta(minutes=2)
    )

    request = IncidentCreateRequest(**payload)

    assert request.observed_at is not None


def test_empty_patch_is_rejected():
    with pytest.raises(ValidationError):
        IncidentPatchRequest()


def test_patch_with_description_is_valid():
    request = IncidentPatchRequest(
        description="The road is now fully reopened."
    )

    assert request.description == (
        "The road is now fully reopened."
    )


def test_patch_with_status_is_valid():
    request = IncidentPatchRequest(status="resolved")

    assert request.status == "resolved"


def test_patch_cannot_set_withdrawn_status():
    with pytest.raises(ValidationError):
        IncidentPatchRequest(status="withdrawn")


def test_patch_cannot_change_owner():
    with pytest.raises(ValidationError):
        IncidentPatchRequest(
            description="The road is now fully reopened.",
            owner_id="attacker",
        )


def test_patch_cannot_set_null_description():
    with pytest.raises(ValidationError):
        IncidentPatchRequest(description=None)


def test_patch_rejects_short_description():
    with pytest.raises(ValidationError):
        IncidentPatchRequest(description="Short")


def test_list_query_defaults():
    query = IncidentListQuery(
        latitude=43.0096,
        longitude=-81.2737,
        radius_m=1500,
    )

    assert query.status == "active"
    assert query.sort_by == "submitted_at"
    assert query.sort_order == "desc"
    assert query.limit == 25
    assert query.offset == 0


def test_list_query_accepts_maximum_radius():
    query = IncidentListQuery(
        latitude=43,
        longitude=-81,
        radius_m=25000,
    )

    assert query.radius_m == 25000


@pytest.mark.parametrize(
    "radius",
    [0, -1, 25000.01],
)
def test_list_query_rejects_invalid_radius(radius):
    with pytest.raises(ValidationError):
        IncidentListQuery(
            latitude=43,
            longitude=-81,
            radius_m=radius,
        )


def test_list_query_rejects_non_finite_radius():
    with pytest.raises(ValidationError):
        IncidentListQuery(
            latitude=43,
            longitude=-81,
            radius_m=float("inf"),
        )


def test_list_query_rejects_invalid_limit():
    with pytest.raises(ValidationError):
        IncidentListQuery(
            latitude=43,
            longitude=-81,
            radius_m=1000,
            limit=101,
        )


def test_list_query_rejects_negative_offset():
    with pytest.raises(ValidationError):
        IncidentListQuery(
            latitude=43,
            longitude=-81,
            radius_m=1000,
            offset=-1,
        )


def test_list_query_rejects_invalid_sort_field():
    with pytest.raises(ValidationError):
        IncidentListQuery(
            latitude=43,
            longitude=-81,
            radius_m=1000,
            sort_by="owner_id",
        )
