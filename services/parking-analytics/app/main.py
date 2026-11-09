import logging
import os

from contextlib import asynccontextmanager

from fastapi import FastAPI

from shared.registry_client import RegistryClient


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
    title="Parking Analytics",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
def health():
    return {
        "service": SERVICE_ID,
        "status": "ok",
    }


@app.get("/")
def root():
    return {
        "service": SERVICE_ID,
        "message": "Starter service is running",
    }
