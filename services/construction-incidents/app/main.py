
import logging
import os

from contextlib import asynccontextmanager
from uuid import UUID

from typing import Annotated

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Response,
)
from fastapi import Request as FastAPIRequest

from app.auth import (
    JWTSettings,
    Principal,
    TokenVerifier,
    get_current_principal,
)
from app.database import Database
from app.errors import error_response, install_errors
from app.models import (
    HealthResponse,
    IncidentCreateRequest,
    IncidentListQuery,
    IncidentListResponse,
    IncidentPatchRequest,
    IncidentReportResponse,
    ReadyResponse,
)
from app.repository import (
    IdempotencyConflict,
    IncidentForbidden,
    IncidentNotFound,
    IncidentRepository,
    InvalidStatusTransition,
)
from shared.registry_client import RegistryClient


logging.basicConfig(level=logging.INFO)

SERVICE_ID = "construction-incidents"

SERVICE_ENDPOINT = os.getenv(
    "REGISTRY_ADVERTISED_ENDPOINT",
    "http://localhost:8080",
)

REGISTRY_URL = os.getenv("REGISTRY_URL")
REGISTRY_API_KEY = os.getenv("REGISTRY_API_KEY")


@asynccontextmanager
async def lifespan(app: FastAPI):
    registry_client = None

    # Initialize S3's dedicated PostGIS database.
    database = Database.from_env()
    database.initialize()

    app.state.database = database
    app.state.repository = IncidentRepository(database)

    # Initialize trusted JWT verification.
    # No local authentication bypass is provided.
    jwt_settings = JWTSettings.from_env()
    app.state.token_verifier = TokenVerifier(jwt_settings)

    if REGISTRY_URL:
        if not REGISTRY_API_KEY:
            raise RuntimeError(
                "REGISTRY_API_KEY must be configured "
                "when REGISTRY_URL is provided"
            )

        registry_client = RegistryClient(
            service_id=SERVICE_ID,
            endpoint=SERVICE_ENDPOINT,
            registry_url=REGISTRY_URL,
            api_key=REGISTRY_API_KEY,
            heartbeat_interval=5.0,
            metadata={
                "environment": os.getenv(
                    "APP_ENV",
                    "development",
                ),
            },
        )

        await registry_client.start()

    app.state.registry_client = registry_client

    try:
        yield
    finally:
        if registry_client is not None:
            await registry_client.stop()


app = FastAPI(
    title="Construction and Incident Reporting",
    version="1.0.0",
    lifespan=lifespan,
)

install_errors(app)


@app.exception_handler(IdempotencyConflict)
async def handle_idempotency_conflict(
    request: FastAPIRequest,
    exc: IdempotencyConflict,
):
    return error_response(
        request,
        409,
        "The idempotency key was already used with a different request",
        code="IDEMPOTENCY_KEY_CONFLICT",
    )


@app.exception_handler(IncidentNotFound)
async def handle_incident_not_found(
    request: FastAPIRequest,
    exc: IncidentNotFound,
):
    return error_response(
        request,
        404,
        "The requested incident was not found",
    )


@app.exception_handler(IncidentForbidden)
async def handle_incident_forbidden(
    request: FastAPIRequest,
    exc: IncidentForbidden,
):
    return error_response(
        request,
        403,
        "You are not authorized to modify this incident",
    )


@app.exception_handler(InvalidStatusTransition)
async def handle_invalid_status_transition(
    request: FastAPIRequest,
    exc: InvalidStatusTransition,
):
    return error_response(
        request,
        422,
        str(exc),
    )


@app.get(
    "/health",
    response_model=HealthResponse,
    operation_id="getHealth",
)
def health():
    return {
        "service": SERVICE_ID,
        "status": "healthy",
    }


@app.get(
    "/ready",
    response_model=ReadyResponse,
    operation_id="getReadiness",
)
def ready():
    if not app.state.database.check_available():
        raise HTTPException(
            status_code=503,
            detail="Construction and incidents database is unavailable",
        )

    if not app.state.token_verifier.check_available():
        raise HTTPException(
            status_code=503,
            detail="Trusted token verification is unavailable",
        )

    return {
        "service": SERVICE_ID,
        "status": "ready",
    }


@app.get(
    "/v1/incidents",
    response_model=IncidentListResponse,
    operation_id="listIncidents",
)
def list_incidents(
    query: Annotated[IncidentListQuery, Query()],
):
    return app.state.repository.list_incidents(query)


@app.get(
    "/v1/incidents/{incident_id}",
    response_model=IncidentReportResponse,
    operation_id="getIncident",
)
def get_incident(incident_id: UUID):
    incident = app.state.repository.get_incident(incident_id)

    if incident is None:
        raise HTTPException(
            status_code=404,
            detail="The requested incident was not found",
        )

    return incident



@app.post(
    "/v1/incidents",
    response_model=IncidentReportResponse,
    status_code=201,
    operation_id="createIncident",
)
def create_incident(
    request: IncidentCreateRequest,
    response: Response,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]{8,128}$",
    ),
    principal: Principal = Depends(get_current_principal),
):
    status_code, response_body, _replayed = (
        app.state.repository.create_incident(
            request=request,
            owner_id=principal.subject,
            idempotency_key=idempotency_key,
        )
    )

    response.status_code = status_code
    response.headers["Location"] = (
        f"/v1/incidents/{response_body['incident_id']}"
    )

    return response_body


@app.patch(
    "/v1/incidents/{incident_id}",
    response_model=IncidentReportResponse,
    operation_id="updateIncident",
)
def update_incident(
    incident_id: UUID,
    patch: IncidentPatchRequest,
    principal: Principal = Depends(get_current_principal),
):
    return app.state.repository.update_incident(
        incident_id=incident_id,
        patch=patch,
        actor_id=principal.subject,
        is_moderator=principal.is_moderator,
    )


@app.delete(
    "/v1/incidents/{incident_id}",
    status_code=204,
    response_class=Response,
    operation_id="withdrawIncident",
)
def withdraw_incident(
    incident_id: UUID,
    principal: Principal = Depends(get_current_principal),
):
    app.state.repository.withdraw_incident(
        incident_id=incident_id,
        actor_id=principal.subject,
        is_moderator=principal.is_moderator,
    )

    return Response(status_code=204)


@app.get("/")
def root():
    return {
        "service": SERVICE_ID,
        "message": "Construction and Incident Reporting service is running",
    }
