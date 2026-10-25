"""Schema-level tests: migrations match the ORM models, and DB constraints hold."""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.exc import IntegrityError

from app.db.base import Base
from app.db.bootstrap import LOCAL_SYSTEM_EMAIL, ensure_local_workspace
from app.db.models import Document, User


def test_migrations_match_models(db_engine):
    with db_engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    assert diff == [], f"Models and migrations have drifted; run `alembic revision --autogenerate`: {diff}"


def test_email_is_unique(session_factory):
    with session_factory.begin() as session:
        session.add(User(email="dup@example.test", password_hash="!"))
    with pytest.raises(IntegrityError):
        with session_factory.begin() as session:
            session.add(User(email="dup@example.test", password_hash="!"))


def test_invalid_role_rejected(session_factory):
    with pytest.raises(IntegrityError):
        with session_factory.begin() as session:
            session.add(User(email="root@example.test", password_hash="!", role="superuser"))


def test_invalid_document_status_rejected(session_factory, make_workspace):
    workspace_id = make_workspace()
    with pytest.raises(IntegrityError):
        with session_factory.begin() as session:
            session.add(Document(workspace_id=workspace_id, filename="a.pdf", file_type="pdf", status="hacked"))


def test_document_requires_existing_workspace(session_factory):
    with pytest.raises(IntegrityError):
        with session_factory.begin() as session:
            session.add(
                Document(
                    workspace_id="00000000-0000-0000-0000-000000000000",
                    filename="a.pdf",
                    file_type="pdf",
                    status="ready",
                )
            )


def test_local_workspace_bootstrap_is_idempotent(session_factory):
    first = ensure_local_workspace(session_factory)
    second = ensure_local_workspace(session_factory)
    assert first == second

    with session_factory() as session:
        users = session.query(User).filter(User.email == LOCAL_SYSTEM_EMAIL).all()
    assert len(users) == 1
    assert users[0].is_active is False
