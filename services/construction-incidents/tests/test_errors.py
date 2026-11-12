
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.errors import install_errors


@pytest.fixture
def client():
    test_app = FastAPI()
    install_errors(test_app)

    class ExampleRequest(BaseModel):
        name: str

    @test_app.get("/success")
    def success():
        return {"status": "ok"}

    @test_app.get("/http-error")
    def http_error():
        raise HTTPException(
            status_code=404,
            detail="Incident not found",
        )

    @test_app.post("/validation")
    def validation(body: ExampleRequest):
        return body

    @test_app.post("/malformed")
    def malformed(body: ExampleRequest):
        return body

    @test_app.get("/unexpected-error")
    def unexpected_error():
        raise RuntimeError("Sensitive internal error")

    return TestClient(
        test_app,
        raise_server_exceptions=False,
    )


def test_success_response_includes_request_id(client):
    response = client.get("/success")

    assert response.status_code == 200
    assert response.headers.get("X-Request-ID")


def test_valid_incoming_request_id_is_preserved(client):
    request_id = "test-request-123"

    response = client.get(
        "/success",
        headers={"X-Request-ID": request_id},
    )

    assert response.headers["X-Request-ID"] == request_id


def test_invalid_incoming_request_id_is_replaced(client):
    response = client.get(
        "/success",
        headers={"X-Request-ID": "invalid request id"},
    )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] != "invalid request id"


def test_http_exception_uses_standard_error_envelope(client):
    response = client.get("/http-error")

    assert response.status_code == 404

    body = response.json()

    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["message"] == "Incident not found"
    assert body["error"]["retryable"] is False
    assert body["error"]["source"] == "service"
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]
    assert isinstance(body["error"]["details"], list)


def test_validation_error_returns_422(client):
    response = client.post(
        "/validation",
        json={},
    )

    assert response.status_code == 422

    body = response.json()

    assert body["error"]["code"] == "VALIDATION_FAILED"
    assert body["error"]["message"] == "Request validation failed"
    assert len(body["error"]["details"]) > 0
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]


def test_malformed_json_returns_400(client):
    response = client.post(
        "/malformed",
        content='{"name":',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400

    body = response.json()

    assert body["error"]["code"] == "BAD_REQUEST"
    assert body["error"]["message"] == "Malformed request structure"


def test_unexpected_error_returns_generic_500(client):
    response = client.get("/unexpected-error")

    assert response.status_code == 500

    body = response.json()

    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["message"] == (
        "Unexpected construction and incidents service error"
    )
    assert "Sensitive internal error" not in response.text
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]
