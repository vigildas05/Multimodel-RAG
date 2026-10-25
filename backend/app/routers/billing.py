from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from app.auth.dependencies import get_current_user, require_admin
from app.auth.service import AuthenticatedUser
from app.billing.razorpay import PACKS
from app.config import Settings
from app.core.rate_limit import limit_by_user
from app.dependencies import get_app_settings
from app.schemas.billing import (
    AdminPaymentsOverview,
    BillingOverview,
    CreateOrderRequest,
    LedgerEntryOut,
    OrderOut,
    PackOut,
    PaymentOut,
    VerifyCheckoutRequest,
    VerifyCheckoutResponse,
)

router = APIRouter(tags=["billing"])


def _payment_out(p) -> PaymentOut:
    return PaymentOut(
        order_id=p.razorpay_order_id,
        pack_id=p.pack_id,
        credits=p.credits,
        amount_paise=p.amount_paise,
        currency=p.currency,
        status=p.status,
        test_mode=p.test_mode,
        created_at=p.created_at.isoformat(),
    )


@router.get("/billing", response_model=BillingOverview, dependencies=[Depends(get_current_user)])
async def billing_overview(
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
) -> BillingOverview:
    state = request.app.state
    balance = await run_in_threadpool(state.credit_service.balance, user.user_id)
    history = await run_in_threadpool(state.credit_service.history, user.user_id)
    payments = await run_in_threadpool(state.billing_service.payments_for, user.user_id)
    return BillingOverview(
        test_mode=settings.PAYMENTS_TEST_MODE,
        payments_enabled=state.billing_service.enabled,
        razorpay_key_id=settings.RAZORPAY_KEY_ID or None,
        balance=balance,
        costs={
            "query": settings.CREDIT_COST_QUERY,
            "pdf": settings.CREDIT_COST_PDF,
            "image": settings.CREDIT_COST_IMAGE,
            "audio": settings.CREDIT_COST_AUDIO,
        },
        packs=[PackOut(id=p.id, name=p.name, credits=p.credits, amount_paise=p.amount_paise, price_inr=p.price_inr) for p in PACKS.values()],
        history=[LedgerEntryOut(amount=e.amount, kind=e.kind, reason=e.reason, created_at=e.created_at.isoformat()) for e in history],
        payments=[_payment_out(p) for p in payments],
    )


@router.post("/billing/orders", response_model=OrderOut, dependencies=[Depends(limit_by_user("billing_user"))])
async def create_order(
    body: CreateOrderRequest, request: Request, user: AuthenticatedUser = Depends(get_current_user)
) -> OrderOut:
    order = await run_in_threadpool(request.app.state.billing_service.create_order, user, body.pack_id)
    return OrderOut(**order.__dict__)


@router.post("/billing/verify", response_model=VerifyCheckoutResponse, dependencies=[Depends(limit_by_user("billing_user"))])
async def verify_checkout(
    body: VerifyCheckoutRequest, request: Request, user: AuthenticatedUser = Depends(get_current_user)
) -> VerifyCheckoutResponse:
    state = request.app.state
    status = await run_in_threadpool(
        state.billing_service.verify_checkout, user, body.razorpay_order_id, body.razorpay_payment_id, body.razorpay_signature
    )
    return VerifyCheckoutResponse(status=status, balance=await run_in_threadpool(state.credit_service.balance, user.user_id))


@router.post("/webhooks/razorpay")
async def razorpay_webhook(request: Request) -> dict:
    """Public, CSRF-exempt: authenticated by Razorpay's HMAC signature over the raw body."""
    raw = await request.body()
    outcome = await run_in_threadpool(
        request.app.state.billing_service.handle_webhook,
        raw,
        request.headers.get("x-razorpay-signature"),
        request.headers.get("x-razorpay-event-id"),
    )
    return {"status": "ok", "outcome": outcome}


@router.get("/admin/payments", response_model=AdminPaymentsOverview, dependencies=[Depends(require_admin)])
async def admin_payments(request: Request, settings: Settings = Depends(get_app_settings)) -> AdminPaymentsOverview:
    overview = await run_in_threadpool(request.app.state.billing_service.overview)
    return AdminPaymentsOverview(
        test_mode=settings.PAYMENTS_TEST_MODE,
        by_status=overview["by_status"],
        recent=[_payment_out(p) for p in overview["recent"]],
    )
