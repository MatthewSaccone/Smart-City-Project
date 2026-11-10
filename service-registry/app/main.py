
import asyncio
import os
import secrets

from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Response,
    status,
)

from app.models import (
    RegistrationRequest,
    InstanceResponse,
    DiscoveryResponse,
)
from app.repository import RegistryRepository
from app.postgres_repository import PostgresRegistryRepository


HEARTBEAT_INTERVAL_SECONDS = int(
    os.getenv("REGISTRY_HEARTBEAT_INTERVAL_SECONDS", "5")
)

EXPIRY_SCAN_SECONDS = float(
    os.getenv("REGISTRY_EXPIRY_SCAN_SECONDS", "1")
)

DATABASE_PATH = os.getenv(
    "REGISTRY_DATABASE_PATH",
    "./data/registry.db",
)

DATABASE_URL = os.getenv("REGISTRY_DATABASE_URL")


def create_app(
    repository: RegistryRepository | PostgresRegistryRepository | None = None,
    api_key: str | None = None,
) -> FastAPI:
    if EXPIRY_SCAN_SECONDS <= 0:
        raise ValueError("REGISTRY_EXPIRY_SCAN_SECONDS must be positive")

    if repository is not None:
        repo = repository
    elif DATABASE_URL:
        repo = PostgresRegistryRepository(
            DATABASE_URL,
            HEARTBEAT_INTERVAL_SECONDS,
        )
    else:
        repo = RegistryRepository(
            DATABASE_PATH,
            HEARTBEAT_INTERVAL_SECONDS,
        )

    # Explicitly supplied keys are useful for isolated tests.
    # Otherwise, load the key from the environment.
    registry_api_key = (
        api_key
        if api_key is not None
        else os.getenv("REGISTRY_API_KEY")
    )

    async def expiry_loop():
        while True:
            await asyncio.sleep(EXPIRY_SCAN_SECONDS)
            repo.expire(datetime.now(timezone.utc))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(expiry_loop())

        try:
            yield
        finally:
            task.cancel()

            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(
        title="Smart City Service Registry",
        description=(
            "Runtime registration and discovery for "
            "Smart City microservices."
        ),
        version="0.2.0",
        lifespan=lifespan,
    )

    app.state.registry = repo

    def require_registry_key(
        supplied_key: str | None = Header(
            default=None,
            alias="X-Registry-API-Key",
        ),
    ):
        if not registry_api_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Registry management authentication is not configured",
            )

        if (
            supplied_key is None
            or not secrets.compare_digest(
                supplied_key,
                registry_api_key,
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing registry API key",
                headers={"WWW-Authenticate": "ApiKey"},
            )

    @app.get("/health")
    def health():
        return {
            "service": "service-registry",
            "status": "healthy",
        }

    @app.get("/ready")
    def ready():
        try:
            repo.count()

            return {
                "service": "service-registry",
                "status": "ready",
                "storage": "available",
            }

        except Exception:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Registry storage unavailable",
            )

    @app.post(
        "/v1/instances/register",
        response_model=InstanceResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_registry_key)],
    )
    def register(payload: RegistrationRequest):
        try:
            return repo.register(
                payload.model_dump(mode="json"),
                datetime.now(timezone.utc),
            )

        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            )

    @app.post(
        "/v1/instances/{instance_id}/heartbeat",
        response_model=InstanceResponse,
        dependencies=[Depends(require_registry_key)],
    )
    def heartbeat(instance_id: str):
        record = repo.heartbeat(
            instance_id,
            datetime.now(timezone.utc),
        )

        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    "Instance is not registered or its lease has expired. "
                    "Register the instance again."
                ),
            )

        return record

    @app.delete(
        "/v1/instances/{instance_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(require_registry_key)],
    )
    def deregister(instance_id: str):
        if not repo.deregister(instance_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Instance is not registered",
            )

        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/v1/services")
    def list_services():
        return {
            "services": repo.list_services(
                datetime.now(timezone.utc)
            )
        }

    @app.get(
        "/v1/services/{service_id}",
        response_model=DiscoveryResponse,
    )
    def discover(service_id: str):
        instances = repo.get_service(
            service_id,
            datetime.now(timezone.utc),
        )

        if not instances:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    f"No healthy instances available for service "
                    f"'{service_id}'"
                ),
            )

        return {
            "service_id": service_id,
            "instances": instances,
            "discovered_at": datetime.now(timezone.utc),
        }

    return app


app = create_app()
