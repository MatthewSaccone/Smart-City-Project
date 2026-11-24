"""S7 HTTP operations; names, methods and response shapes follow T008 OpenAPI."""
from datetime import timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request

from app.auth import require_reporter
from app.database import CatalogUnavailable
from app.models import (ComparisonResponse, ErrorEnvelope, FUEL_TYPES, FuelType,
                        Money, PriceReportCreateRequest, PriceReportResponse,
                        StationDetail, StationSearchResponse, to_iso)
from app.service import (comparison_summary, make_stations, nearby, pagination,
                         price_for, response_data_status, sort_stations)

router = APIRouter(tags=['Gasoline Prices'])
ERRORS = {status:{'model':ErrorEnvelope} for status in (400,401,403,404,409,422,429,500,502,503,504)}


def validate_query(request, allowed):
    if any(key not in allowed or len(request.query_params.getlist(key)) > 1 for key in request.query_params):
        raise HTTPException(422,'Unknown or repeated query parameter')


def verify_filters(fuel_type, min_price, max_price, sort_by):
    if (min_price is not None or max_price is not None) and fuel_type is None:
        raise HTTPException(422,'A fuel_type is required with price filters')
    if min_price is not None and max_price is not None and min_price > max_price:
        raise HTTPException(422,'min_price_per_litre must not exceed max_price_per_litre')
    if sort_by in ('price_per_litre','submitted_at') and fuel_type is None:
        raise HTTPException(422,'Sorting by price or submission time requires fuel_type')


def require_ready(request):
    request.app.state.gasoline.require_catalog()
    return request.app.state.gasoline


@router.get('/v1/gas-stations', operation_id='searchGasStations',
            response_model=StationSearchResponse, responses=ERRORS)
def search_gas_stations(
    request: Request,
    latitude: float = Query(ge=-90, le=90, allow_inf_nan=False),
    longitude: float = Query(ge=-180, le=180, allow_inf_nan=False),
    radius_m: float = Query(gt=0, le=25000, allow_inf_nan=False),
    fuel_type: FuelType | None = None,
    min_price_per_litre: Money | None = None,
    max_price_per_litre: Money | None = None,
    include_stale: bool = True,
    sort_by: Literal['distance_m','name','price_per_litre','submitted_at'] = 'distance_m',
    sort_order: Literal['asc','desc'] = 'asc',
    limit: int = Query(default=25,ge=1,le=100),
    offset: int = Query(default=0,ge=0),
):
    validate_query(request,{'latitude','longitude','radius_m','fuel_type','min_price_per_litre',
                            'max_price_per_litre','include_stale','sort_by','sort_order','limit','offset'})
    verify_filters(fuel_type,min_price_per_litre,max_price_per_litre,sort_by)
    db = require_ready(request)
    as_of = request.state.received_at
    stations = nearby(db,latitude,longitude,radius_m,as_of,include_stale,fuel_type)
    if fuel_type:
        stations = [s for s in stations if fuel_type in s['available_fuel_types']]
    if min_price_per_litre is not None or max_price_per_litre is not None:
        def matches(station):
            quote = price_for(station,fuel_type)
            if quote is None:
                return False
            value = Decimal(str(quote['price_per_litre']))
            return (min_price_per_litre is None or value >= min_price_per_litre) and (
                max_price_per_litre is None or value <= max_price_per_litre)
        stations = [s for s in stations if matches(s)]
    sort_stations(stations,sort_by,sort_order,fuel_type)
    selected, page = pagination(stations,limit,offset)
    return {'items':selected,'pagination':page,'as_of':to_iso(as_of),
            'data_status':response_data_status(selected),
            'is_stale':any(s['is_stale'] for s in selected)}


@router.get('/v1/gas-stations/{station_id}', operation_id='getGasStation',
            response_model=StationDetail, responses=ERRORS)
def get_gas_station(
    request: Request,
    station_id: str = Path(min_length=1,max_length=128),
    include_stale: bool = True,
):
    validate_query(request,{'include_stale'})
    db = require_ready(request)
    now = request.state.received_at
    stations = make_stations(db.get_station_rows(station_id=station_id),now,include_stale)
    if not stations:
        raise HTTPException(404,'Gas station not found')
    result = stations[0]
    result.update(as_of=to_iso(now),data_status=response_data_status(stations))
    return result


@router.get('/v1/gas-prices/comparisons', operation_id='compareGasPrices',
            response_model=ComparisonResponse,responses=ERRORS)
def compare_gas_prices(
    request: Request,
    latitude: float = Query(ge=-90,le=90,allow_inf_nan=False),
    longitude: float = Query(ge=-180,le=180,allow_inf_nan=False),
    radius_m: float = Query(gt=0,le=25000,allow_inf_nan=False),
    fuel_type: FuelType = Query(),
    include_stale: bool = False,
    sort_by: Literal['price_per_litre','distance_m','submitted_at'] = 'price_per_litre',
    sort_order: Literal['asc','desc'] = 'asc',
    limit: int = Query(default=25,ge=1,le=100),
    offset: int = Query(default=0,ge=0),
):
    validate_query(request,{'latitude','longitude','radius_m','fuel_type','include_stale',
                            'sort_by','sort_order','limit','offset'})
    db = require_ready(request)
    now = request.state.received_at
    stations = nearby(db,latitude,longitude,radius_m,now,include_stale,fuel_type)
    eligible = [s for s in stations if price_for(s,fuel_type) is not None]
    sort_stations(eligible,sort_by,sort_order,fuel_type)
    all_comparisons = [{'station_id':s['station_id'],'name':s['name'],
                        'distance_m':s['distance_m'],'quote':price_for(s,fuel_type)} for s in eligible]
    summary = comparison_summary(all_comparisons)
    selected,page = pagination(all_comparisons,limit,offset)
    return {'fuel_type':fuel_type,'items':selected,'pagination':page,'summary':summary,
            'as_of':to_iso(now),'data_status':'user_report' if eligible else 'unavailable',
            'is_stale':any(item['quote']['is_stale'] for item in selected)}


@router.post('/v1/gas-stations/{station_id}/price-reports',operation_id='createGasPriceReport',
             status_code=201,response_model=PriceReportResponse,responses=ERRORS)
def create_gas_price_report(
    request: Request,
    payload: PriceReportCreateRequest,
    station_id: str = Path(min_length=1,max_length=128),
    idempotency_key: str = Header(pattern=r'^[A-Za-z0-9_-]{8,128}$'),
    principal=Depends(require_reporter),
):
    validate_query(request,set())
    now = request.state.received_at
    if payload.observed_at is not None and not (
        now - timedelta(days=7) <= payload.observed_at <= now + timedelta(minutes=5)
    ):
        raise HTTPException(422,'observed_at must be within 7 days past or 5 minutes future')
    db = require_ready(request)
    try:
        return db.create_report(station_id,principal,idempotency_key,payload,now)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from None
