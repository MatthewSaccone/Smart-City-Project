
import logging
import os

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from shared.registry_client import RegistryClient
from app.auth import JWTSettings, TokenVerifier
import app.ingestion as ingestion
import app.routes as routes

from app.database import Database
from app.store import PostgresLocationStore


logging.basicConfig(level=logging.INFO)

SERVICE_ID = "live-traffic"

SERVICE_ENDPOINT = os.getenv(
    "REGISTRY_ADVERTISED_ENDPOINT",
    "http://localhost:8081",
)

REGISTRY_URL = os.getenv("REGISTRY_URL")
REGISTRY_API_KEY = os.getenv("REGISTRY_API_KEY")

S1_DATABASE_URL = os.getenv("S1_DATABASE_URL")


@asynccontextmanager
async def lifespan(app: FastAPI):
    registry_client = None
    poller_started = False
    database = None

    # Initialize trusted JWT verification.
    jwt_settings = JWTSettings.from_env()
    app.state.token_verifier = TokenVerifier(jwt_settings)

    # Initialize shared PostgreSQL storage when configured.
    if S1_DATABASE_URL:
        database = Database(S1_DATABASE_URL)
        database.initialize()

        postgres_store = PostgresLocationStore(database)

        # Both ingestion and HTTP routes must use the same store.
        ingestion.poller.set_database(database)
        ingestion.store = postgres_store
        routes.store = postgres_store

    app.state.database = database

    try:
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
                    "environment": os.getenv("APP_ENV", "development"),
                },
            )

            await registry_client.start()

        app.state.registry_client = registry_client

        if ingestion.poller.source_url:
            ingestion.poller.start()
            poller_started = True

        yield

    finally:
        try:
            if poller_started:
                ingestion.poller.stop()
        finally:
            if registry_client is not None:
                await registry_client.stop()


app = FastAPI(
    title="Live Traffic Analytics",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(routes.router)


@app.get("/health")
def health():
    # Liveness: the process is running.
    return {
        "service": SERVICE_ID,
        "status": "ok",
    }


@app.get("/ready")
def readiness():
    # Readiness requires both trusted authentication and shared storage.
    verifier: TokenVerifier = app.state.token_verifier

    if not verifier.configured:
        raise HTTPException(
            status_code=503,
            detail="Trusted token verification is not configured",
        )

    if not verifier.check_available():
        raise HTTPException(
            status_code=503,
            detail="Trusted identity verification is unavailable",
        )

    database: Database = app.state.database

    if database is None or not database.check_available():
        raise HTTPException(
            status_code=503,
            detail="Live Traffic database is unavailable",
        )

    return {
        "service": SERVICE_ID,
        "status": "ready",
    }


@app.get("/")
def root():
    return {
        "service": SERVICE_ID,
        "message": "Live Traffic Analytics is running",
    }
