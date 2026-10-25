from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from app.auth.dependencies import get_current_user
from app.auth.service import AuthenticatedUser
from app.dependencies import get_quota_service
from app.schemas.usage import UsageResponse
from app.services.quota_service import QuotaService

router = APIRouter(prefix="/usage", tags=["usage"], dependencies=[Depends(get_current_user)])


@router.get("/me", response_model=UsageResponse)
async def my_usage(
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    quota: QuotaService = Depends(get_quota_service),
) -> UsageResponse:
    today = await run_in_threadpool(quota.usage_today, user)
    return UsageResponse(
        queries_today=today.used,
        daily_query_limit=today.limit,
        is_demo=user.is_demo,
        workspace_expires_at=user.workspace_expires_at.isoformat() if user.workspace_expires_at else None,
        credit_balance=await run_in_threadpool(request.app.state.credit_service.balance, user.user_id),
    )
