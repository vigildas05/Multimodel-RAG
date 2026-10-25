"""Razorpay integration primitives: server-side packs, signature checks, a minimal API
client, and the configuration guard that keeps this project in TEST MODE.

Razorpay signatures (https://razorpay.com/docs/payments/server-integration/):
- Checkout callback: HMAC-SHA256(key_secret, f"{order_id}|{payment_id}"), hex.
- Webhook:          HMAC-SHA256(webhook_secret, raw_request_body), hex, in X-Razorpay-Signature.
"""

import hashlib
import hmac
from dataclasses import dataclass

import httpx

from app.core.exceptions import ConfigurationError, PaymentError

RAZORPAY_API = "https://api.razorpay.com/v1"


@dataclass(frozen=True)
class CreditPack:
    id: str
    name: str
    credits: int
    amount_paise: int  # INR paise; the ONLY source of a payment's amount

    @property
    def price_inr(self) -> str:
        return f"{self.amount_paise / 100:.0f}"


# Server-side price list. The client sends only a pack ID.
PACKS: dict[str, CreditPack] = {
    p.id: p
    for p in (
        CreditPack("starter", "Starter", 50, 4900),
        CreditPack("standard", "Standard", 200, 14900),
        CreditPack("pro", "Pro", 500, 29900),
    )
}


def _hmac_hex(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_checkout_signature(order_id: str, payment_id: str, signature: str, key_secret: str) -> bool:
    expected = _hmac_hex(key_secret, f"{order_id}|{payment_id}".encode())
    return bool(signature) and hmac.compare_digest(expected, signature)


def verify_webhook_signature(raw_body: bytes, signature: str | None, webhook_secret: str) -> bool:
    if not signature or not webhook_secret:
        return False
    return hmac.compare_digest(_hmac_hex(webhook_secret, raw_body), signature)


def validate_payment_settings(settings) -> None:
    """Refuse to start unless payments are off, or fully in TEST MODE with test keys."""
    key_id = settings.RAZORPAY_KEY_ID
    configured = any((key_id, settings.RAZORPAY_KEY_SECRET, settings.RAZORPAY_WEBHOOK_SECRET))
    if not configured:
        return  # payments disabled: the billing page says so
    if not settings.PAYMENTS_TEST_MODE:
        raise ConfigurationError("Refusing to start: PAYMENTS_TEST_MODE=false is not supported; this project only runs in Razorpay test mode.")
    if key_id.startswith("rzp_live_"):
        raise ConfigurationError("Refusing to start: a LIVE Razorpay key (rzp_live_...) was provided while PAYMENTS_TEST_MODE=true.")
    if not key_id.startswith("rzp_test_"):
        raise ConfigurationError("Refusing to start: RAZORPAY_KEY_ID must be a test key (rzp_test_...).")
    if not settings.RAZORPAY_KEY_SECRET or not settings.RAZORPAY_WEBHOOK_SECRET:
        raise ConfigurationError("Refusing to start: RAZORPAY_KEY_SECRET and RAZORPAY_WEBHOOK_SECRET are both required.")


def payments_enabled(settings) -> bool:
    return bool(settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET and settings.RAZORPAY_WEBHOOK_SECRET)


class RazorpayClient:
    def __init__(self, key_id: str, key_secret: str, transport: httpx.BaseTransport | None = None):
        self._auth = (key_id, key_secret)
        self._transport = transport  # tests inject a mock

    def create_order(self, amount_paise: int, currency: str, receipt: str, notes: dict) -> dict:
        try:
            with httpx.Client(base_url=RAZORPAY_API, auth=self._auth, timeout=15, transport=self._transport) as client:
                resp = client.post(
                    "/orders", json={"amount": amount_paise, "currency": currency, "receipt": receipt, "notes": notes}
                )
        except httpx.HTTPError as exc:
            raise PaymentError("Could not reach the payment provider. Please try again.", detail=type(exc).__name__) from exc
        if resp.status_code != 200:
            raise PaymentError("The payment provider rejected the order.", detail=resp.text[:200])
        return resp.json()
