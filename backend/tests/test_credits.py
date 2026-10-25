"""Credit ledger: derived balances, atomic spends (no overspend under concurrency),
idempotent refunds, append-only enforcement, and charging for queries and uploads."""

import io
from concurrent.futures import ThreadPoolExecutor

import fitz
import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError

from app.billing.credits import CreditService, add_entry
from app.config import get_settings
from app.core.exceptions import InsufficientCreditsError
from app.db.models import CreditLedger
from helpers import signup, start_demo, upload_pdf
from test_demo import template  # noqa: F401  (fixture)
from test_security_gateway import RecordingLLM


def _set_balance(session_factory, user_id: str, target: int) -> None:
    credits = CreditService(session_factory)
    delta = target - credits.balance(user_id)
    if delta:
        with session_factory.begin() as session:
            add_entry(session, user_id, delta, "adjustment", "test_setup")


@pytest.fixture
def account(client, session_factory):
    user = signup(client)
    return client, user["user_id"], CreditService(session_factory)


# --- ledger -----------------------------------------------------------------------------


def test_signup_bonus_is_granted_once_and_balance_is_derived(account, session_factory):
    _, user_id, credits = account
    with session_factory() as session:
        entries = list(session.scalars(select(CreditLedger).where(CreditLedger.user_id == user_id)))
    assert [(e.kind, e.reason, e.amount) for e in entries] == [("grant", "signup_bonus", get_settings().SIGNUP_BONUS_CREDITS)]
    assert credits.balance(user_id) == sum(e.amount for e in entries)


def test_concurrent_spends_never_overspend(account, session_factory):
    _, user_id, credits = account
    _set_balance(session_factory, user_id, 5)

    def attempt(_):
        try:
            credits.spend(user_id, 1, "query")
            return True
        except InsufficientCreditsError:
            return False

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(attempt, range(30)))
    assert results.count(True) == 5
    assert credits.balance(user_id) == 0


def test_refunds_are_idempotent(account):
    _, user_id, credits = account
    before = credits.balance(user_id)
    charge = credits.spend(user_id, 3, "image_processing", "document", "doc-1")
    assert credits.balance(user_id) == before - 3
    assert credits.refund(charge) is True
    assert credits.refund(charge) is False  # retry: no second refund
    assert credits.refund_reference(user_id, "document", "doc-1", "processing_failed") is False
    assert credits.balance(user_id) == before


def test_ledger_is_append_only(account, session_factory):
    _, user_id, _ = account
    with pytest.raises(DBAPIError, match="append-only"):
        with session_factory.begin() as session:
            session.execute(update(CreditLedger).where(CreditLedger.user_id == user_id).values(amount=1_000_000))


def test_spend_sign_constraint(account, session_factory):
    _, user_id, _ = account
    with pytest.raises(DBAPIError):
        with session_factory.begin() as session:
            session.add(CreditLedger(user_id=user_id, amount=50, kind="spend", reason="sneaky"))


# --- charging queries -------------------------------------------------------------------


@pytest.fixture
def llm(app, monkeypatch):
    fake = RecordingLLM()
    monkeypatch.setattr(app.state, "llm_provider", fake)
    return fake


def test_chat_costs_one_credit(account, llm):
    client, user_id, credits = account
    upload_pdf(client, "Enzymes lower activation energy.")
    before = credits.balance(user_id)
    assert client.post("/chat", json={"question": "What do enzymes do?"}).status_code == 200
    assert credits.balance(user_id) == before - get_settings().CREDIT_COST_QUERY


def test_failed_chat_is_refunded(account, llm):
    client, user_id, credits = account
    before = credits.balance(user_id)
    assert client.post("/chat", json={"question": "anything"}).status_code == 404  # no documents
    assert credits.balance(user_id) == before


def test_insufficient_credits_blocks_before_the_llm_and_keeps_the_quota(account, llm, session_factory):
    client, user_id, credits = account
    upload_pdf(client, "Enzymes lower activation energy.")
    _set_balance(session_factory, user_id, 0)
    resp = client.post("/chat", json={"question": "What do enzymes do?"})
    assert resp.status_code == 402 and resp.json()["error_type"] == "insufficient_credits"
    assert llm.calls == 0
    assert client.get("/usage/me").json()["queries_today"] == 0  # quota charge rolled back too


# --- charging uploads -------------------------------------------------------------------


def _pdf(text="Some text about cells.") -> bytes:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    return doc.tobytes()


def test_upload_is_charged_by_type(account, monkeypatch):
    client, user_id, credits = account
    monkeypatch.setattr(get_settings(), "CREDIT_COST_PDF", 2)
    before = credits.balance(user_id)
    upload_pdf(client, "A normal document.")
    assert credits.balance(user_id) == before - 2


def test_failed_processing_is_refunded(account, monkeypatch):
    client, user_id, credits = account
    monkeypatch.setattr(get_settings(), "CREDIT_COST_PDF", 2)
    monkeypatch.setattr(get_settings(), "MAX_PDF_PAGES", 0)  # every PDF now fails in the worker
    before = credits.balance(user_id)
    resp = client.post("/documents/upload", files={"file": ("a.pdf", io.BytesIO(_pdf()), "application/pdf")})
    assert resp.json()["document"]["status"] == "failed"
    assert credits.balance(user_id) == before


def test_upload_without_enough_credits_is_rejected_and_nothing_stored(account, session_factory, monkeypatch):
    client, user_id, credits = account
    monkeypatch.setattr(get_settings(), "CREDIT_COST_PDF", 5)
    _set_balance(session_factory, user_id, 4)
    resp = client.post("/documents/upload", files={"file": ("a.pdf", io.BytesIO(_pdf()), "application/pdf")})
    assert resp.status_code == 402
    assert client.get("/documents").json()["documents"] == []
    assert credits.balance(user_id) == 4


def test_rejected_files_are_free(account):
    client, user_id, credits = account
    before = credits.balance(user_id)
    assert client.post("/documents/upload", files={"file": ("x.png", b"MZ\x90\x00not an image", "image/png")}).status_code == 422
    assert credits.balance(user_id) == before


def test_demo_users_get_demo_credits(client, session_factory, template):
    user = start_demo(client).json()["user"]
    assert CreditService(session_factory).balance(user["user_id"]) == get_settings().DEMO_BONUS_CREDITS
