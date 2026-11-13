from datetime import timedelta
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
import pytest
import yaml

from app.models import utc_now
from test_parking import BOUNDS, ESTIMATE_HEADERS


ROOT = Path(__file__).resolve().parents[3]
CONTRACT = yaml.safe_load((ROOT / "shared/api-contracts/s4-parking-analytics.openapi.yaml").read_text(encoding="utf-8"))


def validate(response, schema):
    Draft202012Validator({"$ref": f"#/components/schemas/{schema}", "components": CONTRACT["components"]},
                         format_checker=FormatChecker()).validate(response.json())
    assert response.headers["X-Request-ID"]


@pytest.mark.parametrize("path, schema", [
    ("/v1/parking/facilities", "FacilitySearchResponse"),
    ("/v1/parking/facilities/demo-central", "FacilityDetailResponse"),
    ("/v1/parking/facilities/demo-north", "FacilityDetailResponse"),
    ("/health", "HealthResponse"), ("/ready", "ReadinessResponse"),
    ("/v1/parking/ingestion/status", "IngestionStatus"),
])
def test_success_responses_match_adopted_openapi(client, path, schema):
    response = client.get(path, params=BOUNDS if path.endswith("facilities") else None)
    assert response.status_code == 200
    validate(response, schema)


def test_estimation_matches_contract_and_is_private_computation(client):
    before = client.get("/v1/parking/facilities/demo-central").json()
    response = client.post("/v1/parking/occupancy/estimate", headers=ESTIMATE_HEADERS,
                           json={"as_of": "2026-11-09T12:00:00-05:00", "observations": []})
    assert response.status_code == 200
    validate(response, "EstimateResponse")
    assert response.json()["as_of"].endswith("Z")
    assert response.json()["is_stale"] is True
    assert client.get("/v1/parking/facilities/demo-central").json()["occupancy"] == before["occupancy"]


def test_details_pricing_unknown_and_original_timestamps(client):
    detail = client.get("/v1/parking/facilities/demo-central").json()
    assert detail["pricing"]["rates"][0]["amount"] == 3.5
    assert isinstance(detail["pricing"]["rates"][0]["amount"], (int, float))
    assert detail["pricing"]["additional_fees"][0]["amount"] == 1
    assert detail["pricing"]["source_updated_at"] is None
    assert detail["pricing"]["is_stale"] is True and detail["pricing"]["is_live"] is False
    assert detail["occupancy"]["is_stale"] is True
    assert detail["availability_status"] == "unknown"
    assert detail["as_of"] != detail["occupancy"]["observed_at"]
    east = client.get("/v1/parking/facilities/demo-east").json()
    assert east["pricing"]["source_updated_at"] is None and east["pricing"]["is_stale"] is True
    north = client.get("/v1/parking/facilities/demo-north").json()
    assert north["availability_status"] == "closed"
    assert north["pricing"]["currency"] is None and north["pricing"]["rates"] == []
    assert north["occupancy"]["estimated_occupied_spaces"] is None


@pytest.mark.parametrize("identifier,status", [("missing-lot", 404), ("INVALID!", 422), ("x" * 65, 422)])
def test_detail_identifier_errors(client, identifier, status):
    response = client.get(f"/v1/parking/facilities/{identifier}", headers={"X-Request-ID": "demo-correlation"})
    assert response.status_code == status
    validate(response, "ErrorResponse")
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"] == "demo-correlation"


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1},
    {"sort_by": "capacity"}, {"facility_status": "invented"}, {"unsupported": 1},
    {"max_lat": 46}, {"max_lon": -75}])
def test_contract_search_constraints(client, params):
    response = client.get("/v1/parking/facilities", params=BOUNDS | params)
    assert response.status_code == 422
    validate(response, "ErrorResponse")


def test_filter_sort_pagination_and_unknown_is_not_available(client):
    bounds = BOUNDS | {"max_lat": 43.67}
    response = client.get("/v1/parking/facilities", params=bounds | {"limit": 1, "offset": 1})
    assert response.json()["items"][0]["facility_id"] == "demo-east"
    assert response.json()["pagination"] == {"limit": 1, "offset": 1, "total": 3, "has_more": True}
    response = client.get("/v1/parking/facilities", params=bounds | {"facility_status": "closed"})
    assert [item["facility_id"] for item in response.json()["items"]] == ["demo-north"]
    response = client.get("/v1/parking/facilities", params=bounds | {"availability_status": "available"})
    assert response.json()["items"] == []
    response = client.get("/v1/parking/facilities", params=bounds | {"sort_by": "facility_id", "sort_order": "desc"})
    assert [item["facility_id"] for item in response.json()["items"]] == ["demo-north", "demo-east", "demo-central"]


@pytest.mark.parametrize("caller,status", [("anonymous", 401), ("invalid", 401), ("registry", 401), ("reader", 403)])
def test_estimator_authentication_and_permission(client, caller, status):
    headers = {"Authorization": ""}
    if caller == "invalid":
        headers["Authorization"] = "Bearer invalid"
    elif caller == "registry":
        headers["X-Registry-API-Key"] = "test-key"
    elif caller == "reader":
        headers["Authorization"] = client.headers["Authorization"]
    response = client.post("/v1/parking/occupancy/estimate", json={"observations": []}, headers=headers)
    assert response.status_code == status
    validate(response, "ErrorResponse")


def test_malformed_unknown_fields_future_reference_and_body_limits(client):
    response = client.post("/v1/parking/occupancy/estimate", content="{bad", headers={
        **ESTIMATE_HEADERS, "Content-Type": "application/json"})
    assert response.status_code == 400
    validate(response, "ErrorResponse")
    for payload in ({"observations": [], "unknown": 1},
                    {"observations": [], "as_of": (utc_now() + timedelta(seconds=120)).isoformat()}):
        response = client.post("/v1/parking/occupancy/estimate", json=payload, headers=ESTIMATE_HEADERS)
        assert response.status_code == 422
        validate(response, "ErrorResponse")
    observation = {"subject_id": "sample", "latitude": 43, "longitude": -79, "observed_at": "2026-11-09T17:00:00Z"}
    assert client.post("/v1/parking/occupancy/estimate", headers=ESTIMATE_HEADERS,
                       json={"observations": [observation] * 10001}).status_code == 422


def test_detail_does_not_accept_undeclared_inputs(client):
    assert client.get("/v1/parking/facilities/demo-central?limit=1").status_code == 422
    assert client.request("GET", "/v1/parking/facilities/demo-central", content='{}').status_code == 400
    assert client.get("/v1/parking/facilities", params=list(BOUNDS.items()) + [("min_lat", 43.65)]).status_code == 422


def test_request_ids_and_validation_errors_do_not_echo_private_inputs(client):
    response = client.post("/v1/parking/occupancy/estimate", headers={**ESTIMATE_HEADERS, "X-Request-ID": "bad id"},
                           json={"observations": [{"subject_id": "private-pseudonym", "latitude": 999}]})
    assert response.status_code == 422
    assert "private-pseudonym" not in response.text
    assert response.headers["X-Request-ID"] != "bad id"


def test_generated_operation_ids_and_service_security(client):
    schema = client.get("/openapi.json").json()
    assert schema["paths"]["/v1/parking/facilities"]["get"]["operationId"] == "searchParkingFacilities"
    assert schema["paths"]["/v1/parking/facilities/{facility_id}"]["get"]["operationId"] == "getParkingFacility"
    assert schema["paths"]["/v1/parking/occupancy/estimate"]["post"]["security"] == [{"ServiceBearer": []}]
    for path in ("/v1/parking/facilities", "/v1/parking/facilities/{facility_id}", "/v1/parking/ingestion/status"):
        assert schema["paths"][path]["get"]["security"] == [{"ServiceBearer": []}]
        assert CONTRACT["paths"][path]["get"]["security"] == [{"ServiceBearer": []}]


def test_incompatible_body_and_noncontract_timestamp_types(client):
    assert client.post("/v1/parking/occupancy/estimate", json=[], headers=ESTIMATE_HEADERS).status_code == 400
    for timestamp in (1234567890, "1234567890", None):
        assert client.post("/v1/parking/occupancy/estimate", json={"observations": [], "as_of": timestamp},
                           headers=ESTIMATE_HEADERS).status_code == 422
