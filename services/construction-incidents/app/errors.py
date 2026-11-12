
"""Request correlation and standard API error responses for S3."""

import logging
import re
import uuid

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


logger = logging.getLogger("construction-incidents")

ERROR_CODES = {
    400: "BAD_REQUEST",
    401: "AUTHENTICATION_REQUIRED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "RESOURCE_CONFLICT",
    422: "VALIDATION_FAILED",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    502: "UPSTREAM_FAILURE",
    503: "SERVICE_UNAVAILABLE",
    504: "UPSTREAM_TIMEOUT",
}


def error_response(
    request: Request,
    status_code: int,
    message: str,
    details: list | None = None,
    headers: dict | None = None,
    code: str | None = None,
):
    request_id = getattr(request.state, "request_id", uuid.uuid4().hex)

    response_headers = dict(headers or {})
    response_headers["X-Request-ID"] = request_id

    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code or ERROR_CODES.get(
                    status_code,
                    "INTERNAL_ERROR",
                ),
                "message": message,
                "request_id": request_id,
                "details": details or [],
                "retryable": status_code in {429, 502, 503, 504},
                "source": "service",
            }
        },
        headers=response_headers,
    )


def install_errors(app):
    @app.middleware("http")
    async def correlate_request(request: Request, call_next):
        incoming = request.headers.get("X-Request-ID", "")

        if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", incoming):
            request.state.request_id = incoming
        else:
            request.state.request_id = uuid.uuid4().hex

        try:
            response = await call_next(request)
        except Exception as exc:
            # Never log tokens, request bodies, or sensitive user information.
            logger.error(
                "request_id=%s error_type=%s",
                request.state.request_id,
                type(exc).__name__,
            )
            response = error_response(
                request,
                500,
                "Unexpected construction and incidents service error",
            )

        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException):
        return error_response(
            request,
            exc.status_code,
            str(exc.detail),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        exc: RequestValidationError,
    ):
        malformed = any(
            error["type"] == "json_invalid"
            for error in exc.errors()
        )

        details = [
            {
                "field": ".".join(str(part) for part in error["loc"]),
                "reason": error["type"],
            }
            for error in exc.errors()
        ]

        return error_response(
            request,
            400 if malformed else 422,
            (
                "Malformed request structure"
                if malformed
                else "Request validation failed"
            ),
            details=details,
        )
