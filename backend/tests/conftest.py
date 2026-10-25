"""Shared fixtures. Database tests run against a dedicated Postgres database
(sentinel_test, created by docker/postgres/init) and are skipped if it isn't reachable."""

import os
from pathlib import Path

import pytest

# Must precede the first protobuf import anywhere in the test session (test modules import
# qdrant-client / transformers before ocr_service gets the chance) — see ocr_service.py.
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://sentinel:sentinel@localhost:5432/sentinel_test"
)
# Must be set before app settings are first read (get_settings is lru_cached), so that
# app-level tests (e.g. create_app()) never touch the development database or collection.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["QDRANT_COLLECTION"] = "test_document_chunks"
os.environ["APP_ENV"] = "test"
# Uploads are processed inside the request; tests/test_ingestion_queue.py covers the worker path.
os.environ["INGEST_MODE"] = "inline"
os.environ["JWT_SECRET_KEY"] = "test-only-signing-key-0123456789abcdef-not-a-real-secret"

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.models import User, Workspace, WorkspaceMember  # noqa: E402
from app.db.session import create_session_factory  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent


def alembic_config() -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("%", "%%"))
    return config


@pytest.fixture(scope="session")
def db_engine():
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    try:
        with engine.connect():
            pass
    except OperationalError:
        pytest.skip("Postgres test database is not reachable (docker compose up -d postgres)")

    # Start from an empty schema every run, exercising the full migration chain both ways.
    config = alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(db_engine):
    yield create_session_factory(db_engine)
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with db_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture(scope="session")
def app(db_engine):
    """The full application, started once per session (startup loads every ML model)."""
    from qdrant_client import QdrantClient

    try:
        QdrantClient(host="localhost", port=6333, timeout=5).delete_collection(os.environ["QDRANT_COLLECTION"])
    except Exception:  # noqa: BLE001
        pytest.skip("Qdrant is not running on localhost:6333 (docker compose up -d qdrant)")

    from app.main import create_app

    application = create_app()
    with TestClient(application):  # runs the lifespan once
        yield application


@pytest.fixture
def client_factory(app, session_factory):
    """Fresh clients (separate cookie jars) against the shared app. Depending on
    session_factory wipes the database after each test. HTTPS base URL: the session
    cookies are Secure, so the client would not send them over plain http."""

    def _new() -> TestClient:
        return TestClient(app, base_url="https://testserver")

    return _new


@pytest.fixture
def client(client_factory) -> TestClient:
    return client_factory()


@pytest.fixture
def make_workspace(session_factory):
    """Create a user + owned workspace; returns the workspace ID."""
    counter = {"n": 0}

    def _make(name: str = "Test workspace") -> str:
        counter["n"] += 1
        with session_factory.begin() as session:
            user = User(email=f"user{counter['n']}@example.test", password_hash="!")
            session.add(user)
            session.flush()
            workspace = Workspace(name=name, owner_id=user.id)
            session.add(workspace)
            session.flush()
            session.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
            return workspace.id

    return _make
