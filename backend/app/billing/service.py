"""Checkout flow (Razorpay TEST MODE):

1. create_order: the server picks the amount from the pack and creates the Razorpay order.
2. The browser opens Razorpay Checkout for that order.
3. verify_checkout: the server checks the checkout signature. No credits yet: the
   browser could be lying about success, and the signature only proves Razorpay saw it.
4. handle_webhook: credits are granted ONLY here, from Razorpay's signed server-to-server
   `payment.captured` (or `order.paid`) event, after checking the amount and currency
   match the order. Every event is recorded once; every purchase is credited once.
"""

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.auth.service import AuthenticatedUser
from app.billing.credits import add_entry
from app.billing.razorpay import PACKS, RazorpayClient, payments_enabled, verify_checkout_signature, verify_webhook_signature
from app.config import Settings
from app.core.exceptions import PaymentError, PaymentsUnavailableError, PermissionDeniedError
from app.db.audit import add_audit_log, add_security_event, record_security_event
from app.db.models import Payment, WebhookEvent

logger = logging.getLogger("app.billing")

CREDITING_EVENTS = {"payment.captured", "order.paid"}


class WebhookSignatureError(PaymentError):
    status_code = 400
    error_type = "invalid_signature"


@dataclass(frozen=True)
class OrderInfo:
    order_id: str
    amount_paise: int
    currency: str
    key_id: str
    pack_id: str
    credits: int


class BillingService:
    def __init__(self, session_factory: sessionmaker[Session], settings: Settings, client: RazorpayClient | None = None):
        self._session_factory = session_factory
        self._settings = settings
        self.enabled = payments_enabled(settings)
        self._client = client or (RazorpayClient(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET) if self.enabled else None)

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise PaymentsUnavailableError("Payments are not configured on this server.")

    # --- 1. order -------------------------------------------------------------------------

    def create_order(self, user: AuthenticatedUser, pack_id: str) -> OrderInfo:
        self._require_enabled()
        if user.is_demo:
            raise PermissionDeniedError("Demo accounts can't buy credits. Create a free account first.")
        pack = PACKS.get(pack_id)
        if pack is None:
            raise PaymentError("Unknown credit pack.")
        receipt = f"rcpt_{uuid.uuid4().hex[:20]}"
        order = self._client.create_order(
            amount_paise=pack.amount_paise,  # from the server's price list, never the client
            currency="INR",
            receipt=receipt,
            notes={"user_id": user.user_id, "pack_id": pack.id},
        )
        if order.get("amount") != pack.amount_paise or order.get("currency") != "INR":
            raise PaymentError("The payment provider returned an unexpected order.")
        with self._session_factory.begin() as session:
            session.add(
                Payment(
                    user_id=user.user_id,
                    razorpay_order_id=order["id"],
                    pack_id=pack.id,
                    credits=pack.credits,
                    amount_paise=pack.amount_paise,
                    currency="INR",
                    test_mode=self._settings.PAYMENTS_TEST_MODE,
                )
            )
            add_audit_log(session, "billing.order_created", actor_user_id=user.user_id, target_type="order", target_id=order["id"], details={"pack_id": pack.id})
        return OrderInfo(order["id"], pack.amount_paise, "INR", self._settings.RAZORPAY_KEY_ID, pack.id, pack.credits)

    # --- 3. checkout callback ------------------------------------------------------------------

    def verify_checkout(self, user: AuthenticatedUser, order_id: str, payment_id: str, signature: str) -> str:
        """Returns the payment status: 'verified' (credits pending the webhook) or 'captured'."""
        self._require_enabled()
        with self._session_factory.begin() as session:
            payment = session.scalar(
                select(Payment).where(Payment.razorpay_order_id == order_id, Payment.user_id == user.user_id).with_for_update()
            )
            if payment is None:
                raise PaymentError("Order not found.")
            if not verify_checkout_signature(order_id, payment_id, signature, self._settings.RAZORPAY_KEY_SECRET):
                add_security_event(
                    session, "payment_signature_invalid", "billing", severity="high", user_id=user.user_id,
                    details={"order_id": order_id},
                )
                failed = True
            else:
                failed = False
                if payment.razorpay_payment_id is None:
                    payment.razorpay_payment_id = payment_id
                if payment.status == "created":
                    payment.status = "verified"
                status = payment.status
        if failed:
            raise PaymentError("Payment verification failed.")
        return status

    # --- 4. webhook -------------------------------------------------------------------------------

    def handle_webhook(self, raw_body: bytes, signature: str | None, event_id: str | None) -> str:
        """Process one webhook delivery; returns an outcome label. Raises only for a bad
        signature (400, so Razorpay's dashboard shows it); everything else is acknowledged
        with 200 so Razorpay doesn't keep retrying deliveries we've decided about."""
        self._require_enabled()
        if not verify_webhook_signature(raw_body, signature, self._settings.RAZORPAY_WEBHOOK_SECRET):
            record_security_event(
                self._session_factory, "webhook_signature_invalid", "billing", severity="high",
                details={"provider": "razorpay", "bytes": len(raw_body)},
            )
            raise WebhookSignatureError("Invalid webhook signature.")

        payload = json.loads(raw_body)
        event_type = payload.get("event", "unknown")
        body_hash = hashlib.sha256(raw_body).hexdigest()
        event_key = event_id or f"sha256:{body_hash}"

        with self._session_factory.begin() as session:
            inserted = session.execute(
                insert(WebhookEvent)
                .values(provider="razorpay", event_id=event_key, event_type=event_type, payload_sha256=body_hash, outcome="processing")
                .on_conflict_do_nothing(index_elements=[WebhookEvent.event_id])
                .returning(WebhookEvent.id)
            ).scalar_one_or_none()
            if inserted is None:
                return "duplicate"
            outcome = self._apply_event(session, event_type, payload)
            session.get(WebhookEvent, inserted).outcome = outcome
        logger.info("Razorpay webhook %s (%s): %s", event_key, event_type, outcome)
        return outcome

    def _apply_event(self, session: Session, event_type: str, payload: dict) -> str:
        entity = (((payload.get("payload") or {}).get("payment") or {}).get("entity")) or {}
        order_id, payment_id = entity.get("order_id"), entity.get("id")
        if event_type not in CREDITING_EVENTS | {"payment.failed"}:
            return "ignored"
        if not order_id or not payment_id:
            return "malformed"

        payment = session.scalar(select(Payment).where(Payment.razorpay_order_id == order_id).with_for_update())
        if payment is None:
            add_security_event(session, "payment_unknown_order", "billing", severity="medium", details={"order_id": order_id, "event": event_type})
            return "unknown_order"

        if event_type == "payment.failed":
            if payment.status != "captured":
                payment.status = "failed"
            return "marked_failed"

        if entity.get("amount") != payment.amount_paise or entity.get("currency") != payment.currency:
            add_security_event(
                session, "payment_amount_mismatch", "billing", severity="high", user_id=payment.user_id,
                details={"order_id": order_id, "expected": payment.amount_paise, "got": entity.get("amount"), "currency": entity.get("currency")},
            )
            return "amount_mismatch"
        if payment.razorpay_payment_id not in (None, payment_id):
            add_security_event(
                session, "payment_id_mismatch", "billing", severity="high", user_id=payment.user_id,
                details={"order_id": order_id},
            )
            return "payment_id_mismatch"

        credited = add_entry(
            session, payment.user_id, payment.credits, "purchase", "purchase",
            reference_type="payment", reference_id=payment.id, idempotency_key=f"purchase:{payment_id}",
        )
        payment.razorpay_payment_id = payment_id
        payment.status = "captured"
        if credited:
            add_audit_log(session, "billing.credits_purchased", actor_user_id=payment.user_id, target_type="payment", target_id=payment.id, details={"credits": payment.credits})
        return "credited" if credited else "already_credited"

    # --- reads -----------------------------------------------------------------------------------

    def payments_for(self, user_id: str, limit: int = 20) -> list[Payment]:
        with self._session_factory() as session:
            return list(session.scalars(select(Payment).where(Payment.user_id == user_id).order_by(Payment.created_at.desc()).limit(limit)))

    def overview(self, limit: int = 100) -> dict:
        with self._session_factory() as session:
            rows = session.execute(select(Payment.status, func.count(), func.coalesce(func.sum(Payment.amount_paise), 0), func.coalesce(func.sum(Payment.credits), 0)).group_by(Payment.status)).all()
            recent = list(session.scalars(select(Payment).order_by(Payment.created_at.desc()).limit(limit)))
        return {
            "by_status": {s: {"count": c, "amount_paise": a, "credits": cr} for s, c, a, cr in rows},
            "recent": recent,
        }
