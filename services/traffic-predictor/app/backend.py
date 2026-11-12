"""Prediction backend interface for S2.

The model/data pipeline is an independent implementation of this Protocol.
"""

from typing import Protocol

from .models import (
    AreaPredictionsResponse, AreaQuery, HighCongestionPeriodsResponse,
    HighPeriodsQuery, HistoryPatternsResponse, HistoryQuery,
    PointPredictionRequest, PointPredictionResponse,
)


class TrafficBackend(Protocol):
    def is_ready(self) -> bool:
        """True only with adequate historical data and a validated, unexpired model."""
        ...

    def historical_patterns(self, query: HistoryQuery) -> HistoryPatternsResponse:
        ...

    def predict_point(self, request: PointPredictionRequest) -> PointPredictionResponse:
        ...

    def high_congestion_periods(self, query: HighPeriodsQuery) -> HighCongestionPeriodsResponse:
        ...

    def area_predictions(self, query: AreaQuery) -> AreaPredictionsResponse:
        ...
