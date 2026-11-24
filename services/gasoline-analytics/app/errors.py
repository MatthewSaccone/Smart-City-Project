"""Platform-standard error envelope and trusted request correlation."""
import logging
import re
import uuid

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.database import CatalogUnavailable, IdempotencyConflict, UnknownStation
from app.models import utc_now

logger = logging.getLogger('gasoline-analytics')
CODES = {400:'BAD_REQUEST',401:'AUTHENTICATION_REQUIRED',403:'FORBIDDEN',
         404:'NOT_FOUND',409:'RESOURCE_CONFLICT',422:'VALIDATION_FAILED',
         429:'RATE_LIMITED',500:'INTERNAL_ERROR',502:'UPSTREAM_FAILURE',
         503:'SERVICE_UNAVAILABLE',504:'UPSTREAM_TIMEOUT'}


def error_response(request, status, message, details=None, headers=None, code=None):
    rid = request.state.request_id
    return JSONResponse(status_code=status,content={'error':{
        'code':code or CODES.get(status,'BAD_REQUEST'),'message':message,
        'request_id':rid,'details':details or [],
        'retryable': status in {429,502,503,504},'source':'service',
    }},headers={**(headers or {}),'X-Request-ID':rid})


def install_errors(app):
    @app.middleware('http')
    async def correlate(request: Request, call_next):
        incoming = request.headers.get('X-Request-ID','')
        request.state.request_id = incoming if re.fullmatch(r'[A-Za-z0-9._:-]{1,128}',incoming) else uuid.uuid4().hex
        request.state.received_at = utc_now()
        try:
            if request.method == 'GET' and (
                request.url.path in ('/v1/gas-stations', '/v1/gas-prices/comparisons')
                or request.url.path.startswith('/v1/gas-stations/')
            ):
                if request.headers.get('content-length') not in (None,'0') or 'transfer-encoding' in request.headers:
                    response = error_response(request,400,'GET S7 operations do not accept a body')
                else:
                    response = await call_next(request)
            else:
                response = await call_next(request)
        except Exception as exc:
            logger.error('request_id=%s error_type=%s',request.state.request_id,type(exc).__name__)
            response = error_response(request,500,'Unexpected gasoline analytics service error')
        response.headers['X-Request-ID'] = request.state.request_id
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        return error_response(request,exc.status_code,str(exc.detail),headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        details = [{'field':'.'.join(map(str,error['loc'])),'reason':error['type']} for error in exc.errors()]
        malformed = any(error['type'] in {'json_invalid','model_type','model_attributes_type'} for error in exc.errors())
        return error_response(request,400 if malformed else 422,
                              'Malformed request structure' if malformed else 'Request validation failed',details)

    @app.exception_handler(CatalogUnavailable)
    async def catalog_error(request, exc):
        return error_response(request,503,str(exc))

    @app.exception_handler(UnknownStation)
    async def unknown_station(request, exc):
        return error_response(request,404,'Gas station not found')

    @app.exception_handler(IdempotencyConflict)
    async def replay_conflict(request, exc):
        return error_response(request,409,'Idempotency-Key reused with different report content',
                              code='IDEMPOTENCY_KEY_CONFLICT')
