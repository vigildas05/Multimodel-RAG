import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.exceptions import AppError

logger = logging.getLogger("app.errors")


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        logger.warning("%s: %s (%s)", exc.error_type, exc.message, exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_type": exc.error_type,
                "message": exc.message,
                "detail": exc.detail,
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception on %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "error_type": "internal_error",
                "message": "An unexpected error occurred.",
                "detail": str(exc),
            },
        )
