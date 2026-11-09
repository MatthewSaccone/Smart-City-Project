import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Response, status
from pydantic import ValidationError

from app.models import RegistrationRequest, InstanceResponse, DiscoveryResponse
from app.repository import RegistryRepository

HEARTBEAT_INTERVAL_SECONDS = int(os.getenv("REGISTRY_HEARTBEAT_INTERVAL_SECONDS", "5"))
EXPIRY_SCAN_SECONDS = float(os.getenv("REGISTRY_EXPIRY_SCAN_SECONDS", "1"))
DATABASE_PATH = os.getenv("REGISTRY_DATABASE_PATH", "./data/registry.db")

def create_app(repository: RegistryRepository | None = None) -> FastAPI:
    repo = repository or RegistryRepository(DATABASE_PATH, HEARTBEAT_INTERVAL_SECONDS)

    async def expiry_loop():
        while True:
            await asyncio.sleep(EXPIRY_SCAN_SECONDS)
            repo.expire(datetime.now(timezone.utc))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(expiry_loop())
        yield
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    app = FastAPI(
        title="Smart City Service Registry",
        description="Runtime registration and discovery for Smart City microservices.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.registry = repo

    @app.get("/health")
    def health():
        return {"service": "service-registry", "status": "healthy"}

    @app.get("/ready")
    def ready():
        try:
            repo.count()
            return {"service": "service-registry", "status": "ready", "storage": "available"}
        except Exception:
            raise HTTPException(status_code=503, detail="Registry storage unavailable")

    @app.post("/v1/instances/register", response_model=InstanceResponse, status_code=status.HTTP_201_CREATED)
    def register(payload: RegistrationRequest):
        return repo.register(payload.model_dump(mode="json"), datetime.now(timezone.utc))

    @app.post("/v1/instances/{instance_id}/heartbeat", response_model=InstanceResponse)
    def heartbeat(instance_id: str):
        record = repo.heartbeat(instance_id, datetime.now(timezone.utc))
        if record is None:
            raise HTTPException(status_code=404, detail="Instance is not registered")
        return record

    @app.delete("/v1/instances/{instance_id}", status_code=status.HTTP_204_NO_CONTENT)
    def deregister(instance_id: str):
        if not repo.deregister(instance_id):
            raise HTTPException(status_code=404, detail="Instance is not registered")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/v1/services")
    def list_services():
        return {"services": repo.list_services(datetime.now(timezone.utc))}

    @app.get("/v1/services/{service_id}", response_model=DiscoveryResponse)
    def discover(service_id: str):
        instances = repo.get_service(service_id, datetime.now(timezone.utc))
        if not instances:
            raise HTTPException(status_code=503, detail=f"No healthy instances available for service '{service_id}'")
        return {"service_id": service_id, "instances": instances, "discovered_at": datetime.now(timezone.utc)}

    return app

app = create_app()
