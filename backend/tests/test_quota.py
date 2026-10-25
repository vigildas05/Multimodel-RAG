"""Daily query quota: enforced on chat/search, refunded on failure, atomic under concurrency."""

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from helpers import signup, upload_pdf
from sqlalchemy import select

from app.auth.service import AuthenticatedUser
from app.config import get_settings
from app.core.exceptions import QuotaExceededError
from app.db.models import UsageQuota, Workspace
from app.services.quota_service import QuotaService


def test_quota_blocks_queries_over_the_limit(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "DAILY_QUERY_LIMIT", 2)
    signup(client)
    upload_pdf(client, "Mitochondria are the powerhouse of the cell.")

    for _ in range(2):
        assert client.post("/search", json={"query": "mitochondria"}).status_code == 200
    blocked = client.post("/search", json={"query": "mitochondria"})
    assert blocked.status_code == 429 and blocked.json()["error_type"] == "quota_exceeded"

    usage = client.get("/usage/me").json()
    assert (usage["queries_today"], usage["daily_query_limit"], usage["is_demo"]) == (2, 2, False)


def test_failed_queries_are_refunded(client):
    signup(client)
    # No documents: retrieval raises "no relevant results" after the quota was charged.
    assert client.post("/search", json={"query": "anything"}).status_code == 404
    assert client.get("/usage/me").json()["queries_today"] == 0


def test_quota_is_per_user(client_factory, monkeypatch):
    monkeypatch.setattr(get_settings(), "DAILY_QUERY_LIMIT", 1)
    alice, bob = client_factory(), client_factory()
    for c in (alice, bob):
        signup(c)
        upload_pdf(c, "Photosynthesis happens in chloroplasts.")
    assert alice.post("/search", json={"query": "chloroplasts"}).status_code == 200
    assert alice.post("/search", json={"query": "chloroplasts"}).status_code == 429
    assert bob.post("/search", json={"query": "chloroplasts"}).status_code == 200


def test_concurrent_consumption_never_exceeds_limit(session_factory, make_workspace):
    workspace_id = make_workspace()
    with session_factory() as session:
        owner_id = session.get(Workspace, workspace_id).owner_id
    settings = get_settings().model_copy(update={"DAILY_QUERY_LIMIT": 5})
    quota = QuotaService(session_factory, settings)
    user = AuthenticatedUser(user_id=owner_id, email="x@example.com", role="user", workspace_id=workspace_id, is_demo=False)

    def attempt(_):
        try:
            quota.consume(user)
            return True
        except QuotaExceededError:
            return False

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(attempt, range(25)))

    assert results.count(True) == 5
    with session_factory() as session:
        assert session.scalar(select(UsageQuota.queries).where(UsageQuota.user_id == owner_id)) == 5


def test_zero_limit_blocks_everything(session_factory):
    settings = get_settings().model_copy(update={"DAILY_QUERY_LIMIT": 0})
    user = AuthenticatedUser(
        user_id=str(uuid.uuid4()), email="x@example.com", role="user", workspace_id="w", is_demo=False
    )
    with pytest.raises(QuotaExceededError):
        QuotaService(session_factory, settings).consume(user)
