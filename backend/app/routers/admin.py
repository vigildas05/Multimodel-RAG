import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.auth.dependencies import require_admin
from app.core.exceptions import NotFoundError
from app.db.models import SECURITY_SEVERITIES, User
from app.schemas.admin import RedteamReportList, SecurityEventPage
from app.schemas.auth import AdminUserListResponse, AdminUserOut
from app.services.admin_service import DEFAULT_REPORT, EventFilters

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/users", response_model=AdminUserListResponse)
async def list_users(request: Request, limit: int = Query(default=100, ge=1, le=500)) -> AdminUserListResponse:
    def _load() -> list[User]:
        with request.app.state.session_factory() as session:
            return list(session.scalars(select(User).order_by(User.created_at.desc()).limit(limit)))

    users = await run_in_threadpool(_load)
    return AdminUserListResponse(
        users=[
            AdminUserOut(
                user_id=u.id,
                email=u.email,
                role=u.role,
                is_active=u.is_active,
                is_demo=u.is_demo,
                created_at=u.created_at.isoformat(),
                last_login_at=u.last_login_at.isoformat() if u.last_login_at else None,
            )
            for u in users
        ]
    )


@router.get("/security-events", response_model=SecurityEventPage)
async def security_events(
    request: Request,
    event_type: str | None = Query(default=None, max_length=48),
    source: str | None = Query(default=None, max_length=32),
    severity: str | None = Query(default=None, pattern="^(" + "|".join(SECURITY_SEVERITIES) + ")$"),
    user_id: uuid.UUID | None = None,
    workspace_id: uuid.UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
) -> SecurityEventPage:
    filters = EventFilters(
        event_type=event_type,
        source=source,
        severity=severity,
        user_id=str(user_id) if user_id else None,
        workspace_id=str(workspace_id) if workspace_id else None,
        since=since,
        until=until,
    )
    total, events = await run_in_threadpool(request.app.state.admin_service.security_events, filters, limit, offset)
    return SecurityEventPage(total=total, limit=limit, offset=offset, events=events)


@router.get("/security-summary")
async def security_summary(request: Request, days: int = Query(default=14, ge=1, le=90)) -> dict[str, Any]:
    return await run_in_threadpool(request.app.state.admin_service.security_summary, days)


@router.get("/usage")
async def usage(request: Request, days: int = Query(default=14, ge=1, le=90)) -> dict[str, Any]:
    return await run_in_threadpool(request.app.state.admin_service.usage, days)


@router.get("/redteam/reports", response_model=RedteamReportList)
async def redteam_reports(request: Request) -> RedteamReportList:
    return RedteamReportList(reports=await run_in_threadpool(request.app.state.admin_service.redteam_reports))


@router.get("/redteam/latest")
async def redteam_latest(
    request: Request, name: str = Query(default=DEFAULT_REPORT, pattern="^[a-z0-9_]{1,64}$")
) -> dict[str, Any]:
    report = await run_in_threadpool(request.app.state.admin_service.redteam_report, name)
    if report is None:
        raise NotFoundError("Red-team report not found.")
    return report
