"""S6-local implementation of the platform error and request-correlation contract."""

import logging
import re
import uuid

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.models import utc_now

LOG = logging.getLogger("transit")
CODES = {400: "BAD_REQUEST", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 422: "VALIDATION_FAILED",
         429: "RATE_LIMITED", 500: "INTERNAL_ERROR", 502: "UPSTREAM_FAILURE",
         503: "SERVICE_UNAVAILABLE", 504: "UPSTREAM_TIMEOUT"}


def error_response(request, status, message, details=None, headers=None):
    return JSONResponse(status_code=status, content={"error": {
        "code": CODES.get(status, "BAD_REQUEST"), "message": message,
        "request_id": request.state.request_id, "details": details or [],
        "retryable": status in {429, 502, 503, 504}, "source": "service",
    }}, headers={**(headers or {}), "X-Request-ID": request.state.request_id})


def install_errors(app):
    @app.middleware("http")
    async def correlate(request, call_next):
        incoming = request.headers.get("X-Request-ID", "")
        request.state.request_id = incoming if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", incoming) else uuid.uuid4().hex
        request.state.received_at = utc_now()
        try:
            if (request.url.path.startswith("/v1/transit") and request.method == "GET" and
                    (request.headers.get("content-length", "0") != "0" or "transfer-encoding" in request.headers)):
                response = error_response(request, 400, "GET transit operations do not accept a body")
            elif any(len(request.query_params.getlist(key)) > 1 for key in request.query_params):
                response = error_response(request, 422, "Repeated query parameters are not supported")
            else:
                response = await call_next(request)
        except Exception as exc:
            LOG.error("request_id=%s error_type=%s", request.state.request_id, type(exc).__name__)
            response = error_response(request, 500, "Unexpected transit service error")
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error_response(request, exc.status_code, str(exc.detail), headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        details = [{"field": ".".join(map(str, item["loc"])), "reason": item["type"]} for item in exc.errors()]
        return error_response(request, 422, "Request validation failed", details)
