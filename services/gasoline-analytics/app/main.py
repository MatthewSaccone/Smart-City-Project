"""S7 Gasoline Price Analytics: registry-integrated public reads and JWT writes."""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from shared.registry_client import RegistryClient

from app.auth import configure_auth
from app.database import GasolineDatabase
from app.errors import install_errors
from app.models import HealthResponse, ReadyResponse, ErrorEnvelope
from app.routes import router

logging.basicConfig(level=logging.INFO)
SERVICE_ID = 'gasoline-analytics'
SERVICE_ENDPOINT = os.getenv('REGISTRY_ADVERTISED_ENDPOINT','http://localhost:8080')
REGISTRY_URL = os.getenv('REGISTRY_URL')
REGISTRY_API_KEY = os.getenv('REGISTRY_API_KEY')


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_auth(app)
    app.state.gasoline = GasolineDatabase(os.getenv('S7_DB_PATH','/app/data/gasoline.sqlite3'))
    registry_client = None
    if REGISTRY_URL:
        if not REGISTRY_API_KEY:
            raise RuntimeError('REGISTRY_API_KEY must be configured when REGISTRY_URL is provided')
        registry_client = RegistryClient(
            service_id=SERVICE_ID,endpoint=SERVICE_ENDPOINT,
            registry_url=REGISTRY_URL,api_key=REGISTRY_API_KEY,
            heartbeat_interval=5.0,
            metadata={'environment':os.getenv('APP_ENV','development')},
        )
        await registry_client.start()
    app.state.registry_client = registry_client
    try:
        yield
    finally:
        if registry_client is not None:
            await registry_client.stop()


app = FastAPI(title='Gasoline Price Analytics',version='1.0.0',lifespan=lifespan)
app.include_router(router)
install_errors(app)


@app.get('/health',response_model=HealthResponse,operation_id='getHealth',tags=['Operations'])
def health():
    return HealthResponse()


@app.get('/ready',response_model=ReadyResponse,operation_id='getReadiness',
         tags=['Operations'],responses={503:{'model':ErrorEnvelope}})
def ready(request: Request):
    request.app.state.gasoline.require_catalog()
    return ReadyResponse()


@app.get('/',include_in_schema=False)
def root():
    return {'service':SERVICE_ID,'message':'Gasoline Price Analytics service is running'}
