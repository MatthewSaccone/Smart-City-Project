from datetime import timedelta
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
import pytest
import yaml

from conftest import AREA, NOW

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = yaml.safe_load((ROOT / "shared/api-contracts/s6-public-transit.openapi.yaml").read_text(encoding="utf-8"))
CASES = [
    ("/v1/transit/stops", AREA, "StopsResponse"),
    ("/v1/transit/routes", AREA, "RoutesResponse"),
    ("/v1/transit/stops/stop-102/arrivals", {}, "ArrivalsResponse"),
    ("/v1/transit/delays", {"route_id": "route-5"}, "DelaysResponse"),
    ("/v1/transit/disruptions", {"route_id": "route-5"}, "DisruptionsResponse"),
    ("/v1/transit/occupancy", {"route_id": "route-5"}, "OccupancyResponse"),
    ("/health", {}, "HealthResponse"), ("/ready", {}, "ReadyResponse"),
]


def validate(response, schema):
    Draft202012Validator({"$ref": "#/components/schemas/" + schema, "components": CONTRACT["components"]},
                         format_checker=FormatChecker()).validate(response.json())
    assert response.headers["X-Request-ID"]


@pytest.mark.parametrize("path,params,schema", CASES)
def test_every_endpoint_matches_the_adopted_response_schema(client, path, params, schema):
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    validate(response, schema)


@pytest.mark.parametrize("path,params,schema", CASES[:6])
def test_live_test_inputs_also_match_contract(client, fresh, path, params, schema):
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    validate(response, schema)


def test_generated_operations_and_query_names_match_contract(client):
    generated = client.get("/openapi.json").json()
    for path, operations in CONTRACT["paths"].items():
        actual = generated["paths"][path]["get"]
        assert actual["operationId"] == operations["get"]["operationId"]
        expected = set()
        for parameter in operations["get"].get("parameters", []):
            definition = CONTRACT["components"]["parameters"][parameter["$ref"].rsplit("/", 1)[1]]
            if definition["in"] != "header":
                expected.add(definition["name"])
        assert {item["name"] for item in actual.get("parameters", [])} == expected


@pytest.mark.parametrize("changes", [{"latitude": "NaN"}, {"longitude": "inf"}, {"latitude": 91},
    {"longitude": -181}, {"radius_m": 0}, {"radius_m": -1}, {"radius_m": 20001},
    {"radius_m": "no"}, {"mode": "ferry"}, {"limit": 0}, {"limit": 101}, {"offset": -1},
    {"limit": "1.5"}, {"sort_order": "up"}, {"sort_by": "invalid"}, {"unexpected": 1}])
@pytest.mark.parametrize("path", ["stops", "routes"])
def test_area_validation(client, path, changes):
    response = client.get("/v1/transit/" + path, params={**AREA, **changes})
    assert response.status_code == 422
    validate(response, "ErrorEnvelope")


@pytest.mark.parametrize("path,params,_", CASES[:6])
def test_unknown_and_repeated_fields_and_get_bodies(client, path, params, _):
    assert client.get(path, params={**params, "unsupported": 1}).status_code == 422
    pairs = list(params.items()) + [("limit", 1), ("limit", 2)]
    assert client.get(path, params=pairs).status_code == 422
    assert client.request("GET", path, params=params, content=b"{}").status_code == 400


@pytest.mark.parametrize("path,params", [("stops", {}), ("routes", {}), ("delays", {}),
    ("disruptions", {}), ("occupancy", {}), ("delays", {"route_id": " "}),
    ("occupancy", {"vehicle_id": "x" * 129})])
def test_required_scope_and_identifier_validation(client, path, params):
    response = client.get("/v1/transit/" + path, params=params)
    assert response.status_code == 422
    validate(response, "ErrorEnvelope")


@pytest.mark.parametrize("params", [{"start_at": "2026-11-22T12:00:00"}, {"start_at": "1234567890"},
    {"start_at": "2026-11-22T11:54:59Z"}, {"start_at": "2026-11-23T12:00:01Z"},
    {"window_minutes": 0}, {"window_minutes": 181}, {"route_id": " "}, {"sort_by": "scheduled_at"}])
def test_arrival_time_and_query_validation(client, params):
    response = client.get("/v1/transit/stops/stop-102/arrivals", params=params)
    assert response.status_code == 422
    validate(response, "ErrorEnvelope")


def test_request_ids_and_errors_are_safe(client):
    response = client.get("/v1/transit/stops/not-present/arrivals", headers={"X-Request-ID": "transit-demo:42"})
    assert response.status_code == 404
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"] == "transit-demo:42"
    validate(response, "ErrorEnvelope")
    response = client.get("/health", headers={"X-Request-ID": "bad value"})
    assert response.headers["X-Request-ID"] != "bad value"
    assert client.get("/v1/transit/stops/%20/arrivals").status_code == 422
    assert client.post("/v1/transit/stops", json={}).status_code == 405


def test_geography_routes_modes_filtering_and_empty_results(client):
    stops = client.get("/v1/transit/stops", params=AREA).json()
    assert {item["stop_id"] for item in stops["items"]} == {"stop-102", "stop-103", "stop-104"}
    assert stops["data_status"] == "scheduled" and stops["source"].startswith("simulated:")
    assert not stops["is_live"] and not stops["is_stale"]
    assert stops["source_updated_at"] is None
    routes = client.get("/v1/transit/routes", params=AREA).json()
    assert {item["route_id"] for item in routes["items"]} == {"route-5", "route-12"}
    buses = client.get("/v1/transit/stops", params={**AREA, "mode": "bus"}).json()["items"]
    assert {item["stop_id"] for item in buses} == {"stop-102", "stop-103"}
    for path in ("stops", "routes"):
        assert client.get("/v1/transit/" + path, params={**AREA, "mode": "train"}).json()["items"] == []
        assert client.get("/v1/transit/" + path, params={**AREA, "latitude": 0, "longitude": 0}).json()["items"] == []


def test_filter_sort_then_paginate(client):
    full = client.get("/v1/transit/stops", params={**AREA, "sort_by": "name", "sort_order": "desc"}).json()
    second = client.get("/v1/transit/stops", params={**AREA, "sort_by": "name", "sort_order": "desc", "limit": 1, "offset": 1}).json()
    assert second["items"] == full["items"][1:2]
    assert second["pagination"] == {"limit": 1, "offset": 1, "total": 3, "has_more": True}
    assert client.get("/v1/transit/stops", params={**AREA, "offset": 99}).json()["items"] == []


def test_arrivals_scheduled_window_and_independent_events(client):
    response = client.get("/v1/transit/stops/stop-102/arrivals", params={"window_minutes": 15}).json()
    assert {item["trip_id"] for item in response["items"]} == {"trip-5-1200", "trip-12-1200"}
    assert all(event["estimated_at"] is None and event["basis"] == "schedule" and event["delay_seconds"] is None
               for item in response["items"] for event in (item["arrival"], item["departure"]) if event)
    assert next(item for item in response["items"] if item["route_id"] == "route-12")["arrival"] is None
    assert client.get("/v1/transit/stops/stop-103/arrivals").json()["items"][0]["departure"] is None
    assert client.get("/v1/transit/stops/stop-102/arrivals", params={"route_id": "missing"}).json()["items"] == []
    offset = client.get("/v1/transit/stops/stop-102/arrivals", params={"start_at": "2026-11-22T07:00:00-05:00"})
    assert offset.status_code == 200


def test_positive_delays_and_disruption_and_occupancy_filters(client, fresh):
    delays = client.get("/v1/transit/delays", params={"route_id": "route-5", "stop_id": "stop-102"}).json()
    assert {item["event_type"] for item in delays["items"]} == {"arrival", "departure"}
    assert all(item["delay_seconds"] == 180 and item["vehicle_id"] for item in delays["items"])
    for kind in ("disruption", "cancellation", "outage", "detour"):
        response = client.get("/v1/transit/disruptions", params={"route_id": "route-5", "kind": kind}).json()
        assert len(response["items"]) == 1 and response["items"][0]["kind"] == kind
    assert client.get("/v1/transit/disruptions", params={"route_id": "route-5", "stop_id": "stop-104"}).json()["items"] == []
    occupancy = client.get("/v1/transit/occupancy", params={"vehicle_id": "vehicle-demo-31", "route_id": "route-5"}).json()
    item = occupancy["items"][0]
    assert item["occupancy_level"] == "moderate"
    assert item["passenger_count"] is item["capacity"] is item["occupancy_percent"] is None


def test_ties_use_ascending_ids_even_for_descending_primary_sort(client):
    repo = client.app.state.repository
    payload = json.loads(repo.schedule_path.read_text())
    for item in payload["stops"]:
        item["name"] = "Same name"
    repo.schedule_path.write_text(json.dumps(payload))
    repo.refresh()
    response = client.get("/v1/transit/stops", params={**AREA, "sort_by": "name", "sort_order": "desc"}).json()
    ids = [item["stop_id"] for item in response["items"]]
    assert ids == sorted(ids)
