import logging
import os
import asyncio

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError

from shared.registry_client import RegistryClient
from .auth import JWTSettings, TokenVerifier
from .ingestion import HistoryPipeline

from .routes import router
from .postgres_history import PostgresHistoryStore
from .baseline import PostgresBaselinePredictor
from .errors import (ServiceError, request_id_from_header, service_error_handler,
                     validation_error_handler, http_error_handler, unexpected_error_handler)
from .models import HealthResponse, ReadyResponse


logging.basicConfig(level=logging.INFO)

SERVICE_ID = "traffic-predictor"

SERVICE_ENDPOINT = os.getenv(
    "REGISTRY_ADVERTISED_ENDPOINT",
    "http://localhost:8080",
)

REGISTRY_URL = os.getenv("REGISTRY_URL")
REGISTRY_API_KEY = os.getenv("REGISTRY_API_KEY")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.token_verifier = TokenVerifier(JWTSettings.from_env())
    # Load source-normalized historical data independently of the prediction model.
    # S2 FR1 can work before a validated forecasting model is available.
    history_csv = os.getenv("S2_HISTORY_CSV")
    history_metadata = os.getenv("S2_HISTORY_METADATA")
    if bool(history_csv) != bool(history_metadata):
        raise RuntimeError("Set both S2_HISTORY_CSV and S2_HISTORY_METADATA, or neither")
    database_url = os.getenv("S2_DATABASE_URL")
    if database_url and history_csv:
        raise RuntimeError("Configure either S2_DATABASE_URL or fixture CSV, not both")
    if database_url:
        app.state.history_store = PostgresHistoryStore(database_url)
    else:
        app.state.history_store = None
    pipeline = None
    if database_url or history_csv:
        pipeline = HistoryPipeline(app.state.history_store, database_url=database_url,
                                   csv_paths=(history_csv, history_metadata) if history_csv else None)
        await asyncio.to_thread(pipeline.refresh_once)
        app.state.history_store = pipeline
    app.state.history_pipeline = pipeline
    # Predictions are gated by the approved, unexpired model registry entry.
    model_version = os.getenv("S2_MODEL_VERSION", "").strip()
    app.state.traffic_backend = (
        PostgresBaselinePredictor(database_url, model_version)
        if database_url and model_version else None
    )
    registry_client = None

    if REGISTRY_URL:
        if not REGISTRY_API_KEY:
            if pipeline:
                await asyncio.to_thread(pipeline.close)
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

        try:
            await registry_client.start()
        except BaseException:
            if pipeline:
                await asyncio.to_thread(pipeline.close)
            raise

    app.state.registry_client = registry_client
    if pipeline:
        pipeline.start()

    try:
        yield

    finally:
        if pipeline:
            await asyncio.to_thread(pipeline.close)
        if registry_client is not None:
            await registry_client.stop()


app = FastAPI(
    title="Traffic Predictor",
    version="1.2.0",
    lifespan=lifespan,
)


# Historical endpoints and prediction endpoints have independent dependencies.
app.state.traffic_backend = None
app.state.history_store = None
app.include_router(router)


@app.middleware("http")
async def attach_request_id(request: Request, call_next):
    request.state.request_id = request_id_from_header(request.headers.get("X-Request-ID"))
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


app.add_exception_handler(ServiceError, service_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(HTTPException, http_error_handler)
app.add_exception_handler(Exception, unexpected_error_handler)


@app.get("/health", response_model=HealthResponse, operation_id="getHealth")
def health():
    return HealthResponse()


@app.get("/ready", response_model=ReadyResponse, operation_id="getReadiness")
def ready():
    store = app.state.history_store
    backend = app.state.traffic_backend
    history_ready = store is not None and (
        not hasattr(store, "is_ready") or store.is_ready()
    )
    prediction_ready = backend is not None and backend.is_ready()
    if not (history_ready or prediction_ready):
        raise ServiceError(503, "SERVICE_UNAVAILABLE",
                           "No historical dataset or validated prediction model is available.",
                           retryable=True)
    # Operational readiness for the currently implemented historical endpoint;
    # model-based forecast endpoints continue to return 503 until connected.
    return ReadyResponse()


@app.get("/")
def root():
    return {
        "service": SERVICE_ID,
        "message": "Starter service is running",
    }
