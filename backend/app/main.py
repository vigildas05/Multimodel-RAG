import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.auth.csrf import csrf_middleware
from app.config import get_settings
from app.core.exception_handlers import register_exception_handlers
from app.core.exceptions import ConfigurationError, DatabaseUnavailableError
from app.core.logging_config import configure_logging
from app.core.middleware import BodySizeLimitMiddleware, global_rate_limit_middleware, security_middleware
from app.core.rate_limit import RateLimiter, build_policies
from app.db.session import create_db_engine, create_session_factory
from app.routers import admin, auth, chat, documents, search, usage
from app.services.container import build_services

logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()
    logger.info("Starting up: loading models and connecting to services...")

    app.state.db_engine = create_db_engine(settings)
    app.state.session_factory = create_session_factory(app.state.db_engine)
    try:
        with app.state.db_engine.connect() as conn:
            conn.execute(text("SELECT version_num FROM alembic_version"))
    except SQLAlchemyError as exc:
        raise DatabaseUnavailableError(
            "Could not initialize the database. Is Postgres running and migrated?",
            detail="Run `docker compose up -d postgres` and `alembic upgrade head` from backend/.",
        ) from exc
    build_services(settings, app.state.session_factory).attach_to(app.state)

    logger.info("Startup complete.")
    yield
    logger.info("Shutting down.")
    app.state.db_engine.dispose()


def validate_security_settings(settings) -> None:
    """Refuse to start with configurations that would quietly weaken security."""
    problems = []
    origins = settings.cors_origins_list
    if any(o == "*" for o in origins):
        # With credentials, a wildcard makes Starlette reflect any Origin: any site could
        # make authenticated requests on a user's behalf.
        problems.append("CORS_ORIGINS must list explicit origins, not '*'")
    if settings.APP_ENV == "production":
        if not origins or any(not o.startswith("https://") for o in origins):
            problems.append("CORS_ORIGINS must be https:// origins in production")
        if not settings.COOKIE_SECURE:
            problems.append("COOKIE_SECURE must be true in production")
        if settings.COOKIE_SAMESITE.lower() == "none":
            problems.append("COOKIE_SAMESITE=none is not allowed in production")
    if problems:
        raise ConfigurationError("Refusing to start: " + "; ".join(problems))


def install_http_security(app: FastAPI, settings) -> None:
    """Middleware stack, innermost first (Starlette runs the last-added outermost):
    CSRF -> global per-IP rate limit -> body size limit -> request ID + security headers -> CORS.
    CORS is outermost so even rejections carry CORS headers and reach the browser intact."""
    app.state.rate_limiter = RateLimiter(settings.REDIS_URL, enabled=settings.RATE_LIMIT_ENABLED)
    app.state.rate_limit_policies = build_policies(settings)

    app.middleware("http")(csrf_middleware)
    app.middleware("http")(global_rate_limit_middleware())
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_json_bytes=settings.MAX_JSON_BODY_KB * 1024,
        # Multipart overhead on top of the file itself.
        max_upload_bytes=settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024 + 64 * 1024,
        upload_paths=("/documents/upload",),
    )
    app.middleware("http")(security_middleware(settings))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Retry-After"],
        max_age=600,
    )
    register_exception_handlers(app)


def create_app() -> FastAPI:
    settings = get_settings()
    validate_security_settings(settings)
    production = settings.APP_ENV == "production"
    app = FastAPI(
        title="Sentinel RAG",
        version="0.2.0",
        lifespan=lifespan,
        # No public API explorer in production.
        docs_url=None if production else "/docs",
        redoc_url=None if production else "/redoc",
        openapi_url=None if production else "/openapi.json",
    )
    install_http_security(app, settings)

    app.include_router(auth.router)
    app.include_router(admin.router)
    app.include_router(documents.router)
    app.include_router(chat.router)
    app.include_router(search.router)
    app.include_router(usage.router)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
