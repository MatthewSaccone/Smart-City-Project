"""S5 Weather & Air Quality Analytics (T046-T053)."""

import logging
import os
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI

from shared.registry_client import RegistryClient
from app.errors import ServiceError, install_errors
from app.models import HealthResponse, ReadyResponse
from app.providers import build_provider
from app.routes import router
from app.service import Settings, WeatherService
from app.store import Store

logging.basicConfig(level=logging.INFO)

SERVICE_ID = "weather-analytics"
SERVICE_ENDPOINT = os.getenv("REGISTRY_ADVERTISED_ENDPOINT", "http://localhost:8080")
REGISTRY_URL = os.getenv("REGISTRY_URL")
REGISTRY_API_KEY = os.getenv("REGISTRY_API_KEY")


def settings_from_env() -> Settings:
    bbox = tuple(float(part) for part in os.getenv("WEATHER_COVERAGE_BBOX", "41.6,-95.2,56.9,-74.3").split(","))
    if len(bbox) != 4 or not (bbox[0] < bbox[2] and bbox[1] < bbox[3]):
        raise ValueError("WEATHER_COVERAGE_BBOX must be min_lat,min_lon,max_lat,max_lon")
    timeout = float(os.getenv("WEATHER_PROVIDER_TIMEOUT_SECONDS", "1.5"))
    if not 0.1 <= timeout <= 1.8:
        raise ValueError("WEATHER_PROVIDER_TIMEOUT_SECONDS must be 0.1-1.8 to respect the 2000 ms target")
    return Settings(
        refresh=timedelta(seconds=float(os.getenv("WEATHER_REFRESH_SECONDS", "300"))),
        backoff=timedelta(seconds=float(os.getenv("WEATHER_RETRY_BACKOFF_SECONDS", "30"))),
        timeout=timeout,
        bbox=bbox,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = settings_from_env()
    provider = build_provider(os.getenv("WEATHER_PROVIDER", "open_meteo"), timeout=settings.timeout)
    store = Store(os.getenv("WEATHER_DATABASE_PATH", "data/weather.sqlite"))
    app.state.weather = WeatherService(provider, store, settings=settings)

    registry_client = None
    if REGISTRY_URL:
        if not REGISTRY_API_KEY:
            raise RuntimeError("REGISTRY_API_KEY must be configured when REGISTRY_URL is provided")
        registry_client = RegistryClient(
            service_id=SERVICE_ID,
            endpoint=SERVICE_ENDPOINT,
            registry_url=REGISTRY_URL,
            api_key=REGISTRY_API_KEY,
            heartbeat_interval=5.0,
            metadata={"environment": os.getenv("APP_ENV", "development"),
                      "provider": provider.name},
        )
        await registry_client.start()
    app.state.registry_client = registry_client

    try:
        yield
    finally:
        try:
            await provider.close()
        finally:
            if registry_client is not None:
                await registry_client.stop()


app = FastAPI(title="Weather and Air Quality Analytics", version="1.0.0", lifespan=lifespan)
app.include_router(router)
install_errors(app)


@app.get("/health", response_model=HealthResponse, operation_id="getHealth", tags=["Operations"])
async def health():
    return HealthResponse()


@app.get("/ready", response_model=ReadyResponse, operation_id="getReadiness", tags=["Operations"])
async def ready():
    if not app.state.weather.ready():
        raise ServiceError(503, "No weather data or provider is currently available")
    return ReadyResponse()


@app.get("/", include_in_schema=False)
def root():
    return {"service": SERVICE_ID, "message": "Weather and Air Quality Analytics is running", "docs": "/docs"}
