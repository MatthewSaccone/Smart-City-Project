import logging
import os
import asyncio

from contextlib import asynccontextmanager

from fastapi import FastAPI

from shared.registry_client import RegistryClient
from app.routes import router
from app.auth import configure_auth
from app.errors import install_errors
from app.models import HealthResponse, ReadinessResponse
from app.service import ParkingService
from app.ingestion import ParkingIngestion


logging.basicConfig(level=logging.INFO)

SERVICE_ID = "parking-analytics"

SERVICE_ENDPOINT = os.getenv(
    "REGISTRY_ADVERTISED_ENDPOINT",
    "http://localhost:8080",
)

REGISTRY_URL = os.getenv("REGISTRY_URL")
REGISTRY_API_KEY = os.getenv("REGISTRY_API_KEY")


@asynccontextmanager
async def lifespan(app: FastAPI):
    registry_client = None
    configure_auth(app)
    app.state.parking = ParkingService(cache_path=os.getenv("PARKING_CACHE_PATH"))
    app.state.ingestion = ParkingIngestion(app.state.parking)
    await asyncio.to_thread(app.state.ingestion.refresh_once)

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

    app.state.ingestion.start()

    try:
        yield

    finally:
        try:
            await asyncio.to_thread(app.state.ingestion.close)
        finally:
            if registry_client is not None:
                await registry_client.stop()


app = FastAPI(
    title="Parking Analytics",
    version="1.1.0",
    lifespan=lifespan,
)

app.include_router(router)
install_errors(app)


@app.get("/health", response_model=HealthResponse, operation_id="getHealth")
def health():
    return HealthResponse()


@app.get("/ready", response_model=ReadinessResponse, operation_id="getReadiness")
def ready():
    app.state.parking.require_catalog()
    return ReadinessResponse()


@app.get("/")
def root():
    return {
        "service": SERVICE_ID,
        "message": "Parking Analytics service is running",
    }
