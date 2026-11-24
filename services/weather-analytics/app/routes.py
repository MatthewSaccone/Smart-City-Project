"""The five public, read-only S5 v1 operations."""

from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.models import (AirQualityResponse, CurrentWeatherResponse, ErrorEnvelope, ForecastQuery,
                        ForecastResponse, GuidanceQuery, PointQuery, TravelGuidanceResponse, TrendQuery,
                        TrendResponse)

ERRORS = {code: {"model": ErrorEnvelope} for code in (400, 404, 422, 429, 500, 502, 503, 504)}

router = APIRouter(prefix="/v1", responses=ERRORS)


def service(request: Request):
    return request.app.state.weather


@router.get("/weather/current", response_model=CurrentWeatherResponse, operation_id="getCurrentWeather",
            tags=["Weather"])
async def current_weather(request: Request, query: Annotated[PointQuery, Query()]):
    return await service(request).current(query.latitude, query.longitude)


@router.get("/weather/forecast", response_model=ForecastResponse, operation_id="getWeatherForecast",
            tags=["Weather"])
async def weather_forecast(request: Request, query: Annotated[ForecastQuery, Query()]):
    return await service(request).forecast(query)


@router.get("/environment/air-quality", response_model=AirQualityResponse, operation_id="getAirQuality",
            tags=["Environment"])
async def air_quality(request: Request, query: Annotated[PointQuery, Query()]):
    return await service(request).air_quality(query.latitude, query.longitude)


@router.get("/environment/trends", response_model=TrendResponse, operation_id="getEnvironmentalTrends",
            tags=["Environment"])
async def environmental_trends(request: Request, query: Annotated[TrendQuery, Query()]):
    return await service(request).trends(query)


@router.get("/weather/travel-guidance", response_model=TravelGuidanceResponse,
            operation_id="getWeatherTravelGuidance", tags=["Guidance"])
async def travel_guidance(request: Request, query: Annotated[GuidanceQuery, Query()]):
    return await service(request).guidance(query)
