"""Razorpay TEST MODE flow: server-side pricing, checkout signature, signed webhooks as the
only source of credits, idempotency against retries/duplicates, and the live-key guard."""

import hashlib
import hmac
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from helpers import signup
from sqlalchemy import select, update

from app.billing.credits import CreditService
from app.billing.razorpay import (
    PACKS,
    validate_payment_settings,
    verify_checkout_signature,
    verify_webhook_signature,
)
from app.billing.service import BillingService
from app.config import get_settings
from app.core.exceptions import ConfigurationError
from app.db.models import Payment, SecurityEvent, User

KEY_ID, KEY_SECRET, WEBHOOK_SECRET = "rzp_test_abc123", "test_key_secret", "test_webhook_secret"


class FakeRazorpay:
    def __init__(self):
        self.orders = []

    def create_order(self, amount_paise, currency, receipt, notes):
        order = {"id": f"order_{uuid.uuid4().hex[:14]}", "amount": amount_paise, "currency": currency}
        self.orders.append({**order, "receipt": receipt, "notes": notes})
        return order


@pytest.fixture
def razorpay(app, session_factory, monkeypatch):
    settings = get_settings().model_copy(
        update={"RAZORPAY_KEY_ID": KEY_ID, "RAZORPAY_KEY_SECRET": KEY_SECRET, "RAZORPAY_WEBHOOK_SECRET": WEBHOOK_SECRET}
    )
    fake = FakeRazorpay()
    monkeypatch.setattr(app.state, "billing_service", BillingService(session_factory, settings, client=fake))
    return fake


def checkout_signature(order_id, payment_id, secret=KEY_SECRET):
    return hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


def webhook(client, order_id, payment_id, amount, event="payment.captured", currency="INR", event_id=None, secret=WEBHOOK_SECRET):
    body = json.dumps(
        {
            "event": event,
            "payload": {"payment": {"entity": {"id": payment_id, "order_id": order_id, "amount": amount, "currency": currency}}},
        }
    ).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    headers = {"X-Razorpay-Signature": signature, "X-Razorpay-Event-Id": event_id or f"evt_{uuid.uuid4().hex[:12]}", "Content-Type": "application/json"}
    # A plain client: webhooks carry no cookies or CSRF token.
    return client.post("/webhooks/razorpay", content=body, headers=headers)


@pytest.fixture
def buyer(client, session_factory):
    user = signup(client)
    return client, user["user_id"], CreditService(session_factory)


def order(client, pack_id="standard"):
    resp = client.post("/billing/orders", json={"pack_id": pack_id})
    assert resp.status_code == 200, resp.text
    return resp.json()


def events(session_factory, event_type):
    with session_factory() as session:
        return list(session.scalars(select(SecurityEvent).where(SecurityEvent.event_type == event_type)))


# --- configuration guard ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides,ok",
    [
        ({}, True),  # payments off
        ({"RAZORPAY_KEY_ID": KEY_ID, "RAZORPAY_KEY_SECRET": "s", "RAZORPAY_WEBHOOK_SECRET": "w"}, True),
        ({"RAZORPAY_KEY_ID": "rzp_live_abc", "RAZORPAY_KEY_SECRET": "s", "RAZORPAY_WEBHOOK_SECRET": "w"}, False),
        ({"RAZORPAY_KEY_ID": KEY_ID, "RAZORPAY_KEY_SECRET": "s", "RAZORPAY_WEBHOOK_SECRET": "w", "PAYMENTS_TEST_MODE": False}, False),
        ({"RAZORPAY_KEY_ID": "abc", "RAZORPAY_KEY_SECRET": "s", "RAZORPAY_WEBHOOK_SECRET": "w"}, False),
        ({"RAZORPAY_KEY_ID": KEY_ID, "RAZORPAY_KEY_SECRET": "s"}, False),  # no webhook secret
    ],
    ids=["disabled", "test-keys", "live-key", "test-mode-off", "not-a-test-key", "missing-webhook-secret"],
)
def test_only_test_mode_can_start(overrides, ok):
    settings = get_settings().model_copy(update={"RAZORPAY_KEY_ID": "", "RAZORPAY_KEY_SECRET": "", "RAZORPAY_WEBHOOK_SECRET": "", **overrides})
    if ok:
        validate_payment_settings(settings)
    else:
        with pytest.raises(ConfigurationError):
            validate_payment_settings(settings)


def test_signature_helpers():
    sig = checkout_signature("order_1", "pay_1")
    assert verify_checkout_signature("order_1", "pay_1", sig, KEY_SECRET)
    assert not verify_checkout_signature("order_1", "pay_2", sig, KEY_SECRET)
    assert not verify_checkout_signature("order_1", "pay_1", "", KEY_SECRET)
    body = b'{"event":"payment.captured"}'
    good = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(body, good, WEBHOOK_SECRET)
    assert not verify_webhook_signature(body + b" ", good, WEBHOOK_SECRET)
    assert not verify_webhook_signature(body, None, WEBHOOK_SECRET)


# --- orders ------------------------------------------------------------------------------------


def test_order_amount_comes_from_the_server(buyer, razorpay):
    client, user_id, _ = buyer
    data = order(client, "pro")
    assert data["amount_paise"] == PACKS["pro"].amount_paise and data["credits"] == PACKS["pro"].credits
    assert razorpay.orders[0]["amount"] == PACKS["pro"].amount_paise
    assert razorpay.orders[0]["notes"]["user_id"] == user_id
    assert data["key_id"] == KEY_ID


def test_client_supplied_amount_is_rejected(buyer, razorpay):
    client, _, _ = buyer
    resp = client.post("/billing/orders", json={"pack_id": "pro", "amount": 1})
    assert resp.status_code == 422 and razorpay.orders == []


def test_unknown_pack_and_demo_users_and_disabled_payments(buyer, razorpay, app, session_factory, monkeypatch):
    client, user_id, _ = buyer
    assert client.post("/billing/orders", json={"pack_id": "free-credits"}).status_code == 400
    with session_factory.begin() as session:
        session.execute(update(User).where(User.id == user_id).values(is_demo=True))
    assert client.post("/billing/orders", json={"pack_id": "starter"}).status_code == 403
    with session_factory.begin() as session:
        session.execute(update(User).where(User.id == user_id).values(is_demo=False))
    monkeypatch.setattr(app.state, "billing_service", BillingService(session_factory, get_settings()))  # no keys
    resp = client.post("/billing/orders", json={"pack_id": "starter"})
    assert resp.status_code == 503 and resp.json()["error_type"] == "payments_unavailable"


def test_orders_require_csrf(buyer, razorpay):
    client, _, _ = buyer
    token = client.headers.pop("X-CSRF-Token")
    assert client.post("/billing/orders", json={"pack_id": "starter"}).status_code == 403
    client.headers["X-CSRF-Token"] = token


# --- checkout callback ------------------------------------------------------------------------


def test_verified_checkout_grants_nothing_by_itself(buyer, razorpay):
    client, user_id, credits = buyer
    before = credits.balance(user_id)
    o = order(client)
    resp = client.post(
        "/billing/verify",
        json={"razorpay_order_id": o["order_id"], "razorpay_payment_id": "pay_1", "razorpay_signature": checkout_signature(o["order_id"], "pay_1")},
    )
    assert resp.status_code == 200 and resp.json() == {"status": "verified", "balance": before}


def test_forged_checkout_signature_is_rejected_and_logged(buyer, razorpay, session_factory):
    client, _, _ = buyer
    o = order(client)
    resp = client.post(
        "/billing/verify",
        json={"razorpay_order_id": o["order_id"], "razorpay_payment_id": "pay_1", "razorpay_signature": "0" * 64},
    )
    assert resp.status_code == 400
    assert events(session_factory, "payment_signature_invalid")


def test_cannot_verify_someone_elses_order(client_factory, razorpay):
    alice, bob = client_factory(), client_factory()
    signup(alice)
    signup(bob)
    o = order(alice)
    resp = bob.post(
        "/billing/verify",
        json={"razorpay_order_id": o["order_id"], "razorpay_payment_id": "pay_1", "razorpay_signature": checkout_signature(o["order_id"], "pay_1")},
    )
    assert resp.status_code == 400


# --- webhooks: the only source of credits ---------------------------------------------------------


def test_signed_capture_webhook_grants_credits(buyer, razorpay, session_factory):
    client, user_id, credits = buyer
    before = credits.balance(user_id)
    o = order(client, "standard")
    resp = webhook(client, o["order_id"], "pay_A", o["amount_paise"])
    assert resp.status_code == 200 and resp.json()["outcome"] == "credited"
    assert credits.balance(user_id) == before + PACKS["standard"].credits
    with session_factory() as session:
        payment = session.scalar(select(Payment).where(Payment.razorpay_order_id == o["order_id"]))
    assert payment.status == "captured" and payment.razorpay_payment_id == "pay_A"


def test_retried_and_duplicate_webhooks_never_double_credit(buyer, razorpay):
    client, user_id, credits = buyer
    o = order(client, "starter")
    before = credits.balance(user_id)
    assert webhook(client, o["order_id"], "pay_B", o["amount_paise"], event_id="evt_same").json()["outcome"] == "credited"
    assert webhook(client, o["order_id"], "pay_B", o["amount_paise"], event_id="evt_same").json()["outcome"] == "duplicate"
    # A different event for the same payment (Razorpay also sends order.paid).
    assert webhook(client, o["order_id"], "pay_B", o["amount_paise"], event="order.paid").json()["outcome"] == "already_credited"
    assert credits.balance(user_id) == before + PACKS["starter"].credits


def test_concurrent_duplicate_deliveries_credit_once(buyer, razorpay):
    client, user_id, credits = buyer
    o = order(client, "starter")
    before = credits.balance(user_id)
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda i: webhook(client, o["order_id"], "pay_C", o["amount_paise"]).json()["outcome"], range(8)))
    assert outcomes.count("credited") == 1
    assert credits.balance(user_id) == before + PACKS["starter"].credits


def test_unsigned_or_forged_webhooks_are_rejected(buyer, razorpay, session_factory):
    client, user_id, credits = buyer
    o = order(client)
    before = credits.balance(user_id)
    resp = webhook(client, o["order_id"], "pay_D", o["amount_paise"], secret="attacker-guess")
    assert resp.status_code == 400
    body = json.dumps({"event": "payment.captured"}).encode()
    assert client.post("/webhooks/razorpay", content=body, headers={"Content-Type": "application/json"}).status_code == 400
    assert credits.balance(user_id) == before
    assert len(events(session_factory, "webhook_signature_invalid")) == 2


def test_amount_mismatch_grants_nothing(buyer, razorpay, session_factory):
    client, user_id, credits = buyer
    o = order(client, "pro")
    before = credits.balance(user_id)
    assert webhook(client, o["order_id"], "pay_E", 100).json()["outcome"] == "amount_mismatch"
    assert webhook(client, o["order_id"], "pay_F", o["amount_paise"], currency="USD").json()["outcome"] == "amount_mismatch"
    assert credits.balance(user_id) == before
    assert events(session_factory, "payment_amount_mismatch")


def test_unknown_order_and_failed_payment(buyer, razorpay, session_factory):
    client, _, _ = buyer
    assert webhook(client, "order_nonexistent", "pay_G", 4900).json()["outcome"] == "unknown_order"
    o = order(client)
    assert webhook(client, o["order_id"], "pay_H", o["amount_paise"], event="payment.failed").json()["outcome"] == "marked_failed"
    with session_factory() as session:
        assert session.scalar(select(Payment.status).where(Payment.razorpay_order_id == o["order_id"])) == "failed"


# --- read APIs ----------------------------------------------------------------------------------


def test_billing_overview_and_admin_payments(buyer, razorpay, session_factory):
    client, user_id, _ = buyer
    o = order(client, "starter")
    webhook(client, o["order_id"], "pay_I", o["amount_paise"])
    overview = client.get("/billing").json()
    assert overview["test_mode"] is True and overview["payments_enabled"] is True
    assert [p["status"] for p in overview["payments"]] == ["captured"]
    assert overview["history"][0]["kind"] == "purchase"
    assert {p["id"] for p in overview["packs"]} == set(PACKS)

    assert client.get("/admin/payments").status_code == 403
    with session_factory.begin() as session:
        session.execute(update(User).where(User.id == user_id).values(role="admin"))
    admin = client.get("/admin/payments").json()
    assert admin["by_status"]["captured"]["credits"] == PACKS["starter"].credits
