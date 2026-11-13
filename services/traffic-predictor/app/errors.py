"""Application error format shared across S2 routes."""

import re
from uuid import uuid4

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .models import ErrorCore, ErrorDetail, ErrorEnvelope

REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class ServiceError(Exception):
    def __init__(self, status_code, code, message, retryable=False, source="service"):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable
        self.source = source


def request_id_from_header(value: str | None) -> str:
    """Accept safe correlation IDs; otherwise assign a new opaque ID."""
    if value is not None and REQUEST_ID_RE.fullmatch(value):
        return value
    return f"req-{uuid4().hex}"


def error_response(request: Request, status_code: int, code: str, message: str,
                   details=None, retryable=False, source="service", headers=None) -> JSONResponse:
    request_id = getattr(request.state, "request_id", f"req-{uuid4().hex}")
    body = ErrorEnvelope(error=ErrorCore(
        code=code, message=message, request_id=request_id,
        details=details or [], retryable=retryable, source=source,
    ))
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"),
                        headers={**(headers or {}), "X-Request-ID": request_id})


async def service_error_handler(request: Request, exc: ServiceError):
    return error_response(request, exc.status_code, exc.code, exc.message,
                          retryable=exc.retryable, source=exc.source)


async def validation_error_handler(request: Request, exc: RequestValidationError):
    # Malformed JSON syntax is different from values failing the request schema.
    bad_json = any(item.get("type") == "json_invalid" for item in exc.errors())
    details = [ErrorDetail(
        field=".".join(str(part) for part in item.get("loc", [])),
        reason=item.get("msg", "Invalid input"),
    ) for item in exc.errors()]
    return error_response(
        request,
        400 if bad_json else 422,
        "BAD_REQUEST" if bad_json else "VALIDATION_FAILED",
        "Malformed JSON request." if bad_json else "The submitted values violate documented constraints.",
        details=details,
    )


async def http_error_handler(request: Request, exc: HTTPException):
    codes = {400:"BAD_REQUEST", 401:"AUTHENTICATION_REQUIRED", 403:"FORBIDDEN", 404:"NOT_FOUND",
             409:"CONFLICT", 422:"VALIDATION_FAILED", 429:"RATE_LIMITED", 500:"INTERNAL_ERROR",
             502:"UPSTREAM_FAILURE", 503:"SERVICE_UNAVAILABLE", 504:"UPSTREAM_TIMEOUT"}
    return error_response(request, exc.status_code, codes.get(exc.status_code, "HTTP_ERROR"),
                          str(exc.detail), retryable=exc.status_code in {429,502,503,504}, headers=exc.headers)


async def unexpected_error_handler(request: Request, exc: Exception):
    # Do not expose traceback, backend credentials, or source-provider responses.
    return error_response(request, 500, "INTERNAL_ERROR", "An unexpected service error occurred.")
