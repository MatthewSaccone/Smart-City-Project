"""S2 HTTP routes; the prediction backend is intentionally injected separately.

The OpenAPI contract is the source of truth for public request/response shapes.
No real forecasting result is emitted unless a validated backend is available.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from .ingestion import HistoryPipeline, IngestionStatus
from .auth import get_current_principal

from .backend import TrafficBackend
from .history import HistoryStore
from .postgres_history import PostgresHistoryStore
from .errors import ServiceError
from .models import (
    AreaPredictionsResponse, AreaQuery, HighCongestionPeriodsResponse,
    HighPeriodsQuery, HistoryPatternsResponse, HistoryQuery,
    PointPredictionRequest, PointPredictionResponse,
)

router = APIRouter(dependencies=[Depends(get_current_principal)])


def get_history_store(request: Request) -> HistoryStore | PostgresHistoryStore | HistoryPipeline:
    store = getattr(request.app.state, "history_store", None)
    if store is None:
        raise ServiceError(
            503, "SERVICE_UNAVAILABLE",
            "No approved historical traffic dataset is configured.",
            retryable=True,
        )
    return store


def get_backend(request: Request) -> TrafficBackend:
    backend = getattr(request.app.state, "traffic_backend", None)
    if backend is None or not backend.is_ready():
        raise ServiceError(
            503,
            "SERVICE_UNAVAILABLE",
            "A validated prediction model or required dataset is unavailable.",
            retryable=True,
        )
    return backend


@router.get(
    "/v1/traffic/history/patterns",
    response_model=HistoryPatternsResponse,
    operation_id="listHistoricalPatterns",
    tags=["History"],
)
def list_historical_patterns(
    query: Annotated[HistoryQuery, Query()],
    store: Annotated[HistoryStore | PostgresHistoryStore | HistoryPipeline, Depends(get_history_store)],
    response: Response,
):
    if isinstance(store, HistoryPipeline):
        value, state = store.historical_patterns_with_cache(query)
        response.headers["X-S2-Cache"] = state
        return value
    return store.historical_patterns(query)


@router.get("/v1/traffic/ingestion/status", response_model=IngestionStatus,
            operation_id="getTrafficIngestionStatus", tags=["Operations"])
def ingestion_status(request: Request):
    pipeline = getattr(request.app.state, "history_pipeline", None)
    if pipeline is None:
        return {"mode": "disabled", "source_status": "unconfigured"}
    return pipeline.status()


@router.post(
    "/v1/traffic/predictions",
    response_model=PointPredictionResponse,
    operation_id="predictPointCongestion",
    tags=["Predictions"],
)
def predict_point_congestion(
    payload: PointPredictionRequest,
    backend: Annotated[TrafficBackend, Depends(get_backend)],
):
    return backend.predict_point(payload)


@router.get(
    "/v1/traffic/predictions/high-congestion-periods",
    response_model=HighCongestionPeriodsResponse,
    operation_id="listHighCongestionPeriods",
    tags=["Predictions"],
)
def list_high_congestion_periods(
    query: Annotated[HighPeriodsQuery, Query()],
    backend: Annotated[TrafficBackend, Depends(get_backend)],
):
    return backend.high_congestion_periods(query)


@router.get(
    "/v1/traffic/predictions/areas",
    response_model=AreaPredictionsResponse,
    operation_id="listAreaPredictions",
    tags=["Predictions"],
)
def list_area_predictions(
    query: Annotated[AreaQuery, Query()],
    backend: Annotated[TrafficBackend, Depends(get_backend)],
):
    return backend.area_predictions(query)
