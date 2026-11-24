from shared.auth import configure_openapi, protect_service, JWTSettings, TokenVerifier
import logging
import os
import asyncio

from contextlib import asynccontextmanager

from fastapi import FastAPI

from shared.registry_client import RegistryClient
from app.errors import install_errors
from app.models import HealthResponse, ReadyResponse
from app.repository import Repository
from app.routes import router
from app.service import TransitService


logging.basicConfig(level=logging.INFO)

SERVICE_ID = "transit-analytics"

SERVICE_ENDPOINT = os.getenv(
    "REGISTRY_ADVERTISED_ENDPOINT",
    "http://localhost:8080",
)

REGISTRY_URL = os.getenv("REGISTRY_URL")
REGISTRY_API_KEY = os.getenv("REGISTRY_API_KEY")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.token_verifier = TokenVerifier(JWTSettings.from_env())
    registry_client = None
    repository = Repository()
    app.state.repository = repository
    app.state.database = repository.database
    app.state.transit = TransitService(repository)
    await asyncio.to_thread(repository.refresh)

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
    repository.start()

    try:
        yield

    finally:
        try:
            await asyncio.to_thread(repository.close)
        finally:
            if registry_client is not None:
                await registry_client.stop()


app = FastAPI(dependencies=[protect_service("s6")],
    title="Public Transit Analytics",
    version="1.0.0",
    lifespan=lifespan,
)
app.include_router(router)
install_errors(app)


@app.get("/health", response_model=HealthResponse, operation_id="getHealth")
async def health():
    return HealthResponse()


@app.get("/ready", response_model=ReadyResponse, operation_id="getReadiness")
async def ready():
    app.state.transit.context()
    return ReadyResponse()


@app.get("/")
def root():
    return {
        "service": SERVICE_ID,
        "message": "Public Transit Analytics service is running",
    }


configure_openapi(app)
