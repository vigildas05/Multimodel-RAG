import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.auth.csrf import csrf_middleware
from app.config import get_settings
from app.core.exception_handlers import register_exception_handlers
from app.core.exceptions import DatabaseUnavailableError
from app.core.logging_config import configure_logging
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


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Multimodal RAG Research Assistant", version="0.1.0", lifespan=lifespan)

    # Registered before CORS so CORS is the outer layer and CSRF rejections still carry
    # CORS headers (otherwise the browser reports an opaque network error instead).
    app.middleware("http")(csrf_middleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

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
