from pydantic import BaseModel, ConfigDict, Field


class PackOut(BaseModel):
    id: str
    name: str
    credits: int
    amount_paise: int
    price_inr: str


class LedgerEntryOut(BaseModel):
    amount: int
    kind: str
    reason: str
    created_at: str


class PaymentOut(BaseModel):
    order_id: str
    pack_id: str
    credits: int
    amount_paise: int
    currency: str
    status: str
    test_mode: bool
    created_at: str


class BillingOverview(BaseModel):
    test_mode: bool
    payments_enabled: bool
    razorpay_key_id: str | None  # public key, needed by Razorpay Checkout
    balance: int
    costs: dict[str, int]
    packs: list[PackOut]
    history: list[LedgerEntryOut]
    payments: list[PaymentOut]


class CreateOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")  # no client-supplied amount can sneak in

    pack_id: str = Field(min_length=1, max_length=32)


class OrderOut(BaseModel):
    order_id: str
    amount_paise: int
    currency: str
    key_id: str
    pack_id: str
    credits: int


class VerifyCheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    razorpay_order_id: str = Field(min_length=1, max_length=64)
    razorpay_payment_id: str = Field(min_length=1, max_length=64)
    razorpay_signature: str = Field(min_length=1, max_length=128)


class VerifyCheckoutResponse(BaseModel):
    status: str  # "verified" (credits pending the webhook) | "captured" (credited)
    balance: int


class AdminPaymentsOverview(BaseModel):
    test_mode: bool
    by_status: dict[str, dict[str, int]]
    recent: list[PaymentOut]
