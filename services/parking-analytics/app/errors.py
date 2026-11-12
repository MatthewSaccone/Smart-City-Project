"""Parking-local implementation of the adopted common error/request-ID contract."""

import logging
import re
import uuid

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.models import utc_now


logger = logging.getLogger("parking")
CODES = {400: "BAD_REQUEST", 401: "AUTHENTICATION_REQUIRED", 403: "FORBIDDEN",
         404: "NOT_FOUND", 409: "RESOURCE_CONFLICT", 422: "VALIDATION_FAILED",
         429: "RATE_LIMITED", 500: "INTERNAL_ERROR", 502: "UPSTREAM_FAILURE",
         503: "SERVICE_UNAVAILABLE", 504: "UPSTREAM_TIMEOUT"}


def error_response(request, status, message, details=None, headers=None):
    request_id = request.state.request_id
    return JSONResponse(status_code=status, content={"error": {
        "code": CODES.get(status, "BAD_REQUEST"), "message": message,
        "request_id": request_id, "details": details or [],
        "retryable": status in {429, 502, 503, 504}, "source": "service",
    }}, headers={**(headers or {}), "X-Request-ID": request_id})


def install_errors(app):
    @app.middleware("http")
    async def correlate(request: Request, call_next):
        incoming = request.headers.get("X-Request-ID", "")
        request.state.request_id = (incoming if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", incoming)
                                    else uuid.uuid4().hex)
        request.state.received_at = utc_now()
        try:
            if (request.method == "GET" and request.url.path.startswith("/v1/parking/")
                    and (request.headers.get("content-length", "0") != "0" or "transfer-encoding" in request.headers)):
                response = error_response(request, 400, "GET parking operations do not accept a body")
            else:
                response = await call_next(request)
        except Exception as exc:
            # Do not log exception bodies, credentials or raw observation inputs.
            logger.error("request_id=%s error_type=%s", request.state.request_id, type(exc).__name__)
            response = error_response(request, 500, "Unexpected parking service error")
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        return error_response(request, exc.status_code, str(exc.detail), headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        malformed = any(error["type"] == "json_invalid" or
                        error["loc"] == ("body",) and error["type"] in {"model_attributes_type", "model_type"}
                        for error in exc.errors())
        # Only field paths and error types: no submitted pseudonyms/coordinates/tokens.
        details = [{"field": ".".join(map(str, error["loc"])), "reason": error["type"]}
                   for error in exc.errors()]
        return error_response(request, 400 if malformed else 422,
                              "Malformed request structure" if malformed else "Request validation failed", details)
