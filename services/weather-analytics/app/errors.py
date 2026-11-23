"""Platform error envelope and X-Request-ID correlation (docs/api/API-Standards.md)."""

import logging
import re
import uuid

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

LOG = logging.getLogger("weather")

CODES = {
    400: "BAD_REQUEST", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 422: "VALIDATION_FAILED",
    429: "RATE_LIMITED", 500: "INTERNAL_ERROR", 502: "UPSTREAM_FAILURE",
    503: "SERVICE_UNAVAILABLE", 504: "UPSTREAM_TIMEOUT",
}
REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")


class ServiceError(Exception):
    """A contract-level failure with an HTTP status and envelope fields."""

    def __init__(self, status: int, message: str, *, details=None, source: str = "service"):
        super().__init__(message)
        self.status = status
        self.message = message
        self.details = details or []
        self.source = source


def validation_error(field: str, reason: str, message: str) -> ServiceError:
    return ServiceError(422, message, details=[{"field": field, "reason": reason}])


def error_response(request, status, message, *, details=None, source="service", headers=None):
    request_id = getattr(request.state, "request_id", None) or uuid.uuid4().hex
    return JSONResponse(
        status_code=status,
        content={"error": {
            "code": CODES.get(status, "INTERNAL_ERROR"),
            "message": message,
            "request_id": request_id,
            "details": details or [],
            "retryable": status in {429, 502, 503, 504},
            "source": source,
        }},
        headers={**(headers or {}), "X-Request-ID": request_id},
    )


def install_errors(app):
    @app.middleware("http")
    async def correlate(request, call_next):
        incoming = request.headers.get("X-Request-ID", "")
        request.state.request_id = incoming if REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
        try:
            has_body = request.headers.get("content-length", "0") != "0" or "transfer-encoding" in request.headers
            if request.method == "GET" and request.url.path.startswith("/v1/") and has_body:
                response = error_response(request, 400, "GET weather operations do not accept a request body")
            elif any(len(request.query_params.getlist(key)) > 1 for key in request.query_params):
                key = next(k for k in request.query_params if len(request.query_params.getlist(k)) > 1)
                response = error_response(request, 422, "Repeated query parameters are not supported",
                                          details=[{"field": key, "reason": "repeated"}])
            else:
                response = await call_next(request)
        except Exception as exc:  # pragma: no cover - defensive last resort
            LOG.exception("request_id=%s unhandled %s", request.state.request_id, type(exc).__name__)
            response = error_response(request, 500, "Unexpected weather service error")
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(ServiceError)
    async def service_error(request, exc: ServiceError):
        return error_response(request, exc.status, exc.message, details=exc.details, source=exc.source)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error_response(request, exc.status_code, str(exc.detail), headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def request_validation(request, exc):
        details = []
        for item in exc.errors():
            loc = [str(part) for part in item.get("loc", ()) if part not in ("query", "header", "body")]
            details.append({"field": ".".join(loc) or "request", "reason": item.get("type", "invalid")})
        return error_response(request, 422, "Request validation failed", details=details)
