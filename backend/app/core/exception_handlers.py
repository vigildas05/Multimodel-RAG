"""Error responses: consistent JSON, no internals.

- Unexpected exceptions become a generic 500 carrying only a request ID; the traceback is
  logged server-side under the same ID.
- AppError `detail` is shown for client errors (4xx: it's guidance, e.g. allowed file
  types) but hidden for server errors (5xx: it may contain library messages, hosts or
  paths) outside development.
- Validation errors don't echo submitted values back (FastAPI's default includes the
  offending input, which can be a password).
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.core.exceptions import AppError, RateLimitedError
from app.core.middleware import request_id_var

logger = logging.getLogger("app.errors")

GENERIC_500 = "An unexpected error occurred. Please try again; if it persists, contact support with the request ID."


def _body(error_type: str, message: str, detail=None) -> dict:
    return {"error_type": error_type, "message": message, "detail": detail, "request_id": request_id_var.get()}


def unexpected_error_response(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse(status_code=500, content=_body("internal_error", GENERIC_500))


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        logger.warning("%s: %s (%s)", exc.error_type, exc.message, exc.detail)
        show_detail = exc.status_code < 500 or get_settings().APP_ENV == "development"
        headers = {"Retry-After": str(exc.retry_after)} if isinstance(exc, RateLimitedError) else None
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(exc.error_type, exc.message, exc.detail if show_detail else None),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        problems = [
            {"loc": [str(part) for part in err.get("loc", []) if part != "body"], "msg": err.get("msg", "invalid")}
            for err in exc.errors()
        ]
        message = "; ".join(f"{'.'.join(p['loc']) or 'request'}: {p['msg']}" for p in problems[:3])
        return JSONResponse(status_code=422, content=_body("validation_failed", message or "Invalid request.", problems))

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        return unexpected_error_response(request, exc)
