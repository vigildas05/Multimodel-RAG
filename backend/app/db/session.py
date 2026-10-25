"""Engine and session factory. Sync SQLAlchemy (psycopg 3) to match the rest of the
backend, which already pushes blocking work through run_in_threadpool."""

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings


def create_db_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.DATABASE_URL,
        pool_pre_ping=True,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    # expire_on_commit=False: repositories return detached ORM objects / dataclasses
    # after the transaction closes, so attributes must stay loaded.
    return sessionmaker(bind=engine, expire_on_commit=False)
