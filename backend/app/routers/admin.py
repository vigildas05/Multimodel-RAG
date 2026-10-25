from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.auth.dependencies import require_admin
from app.db.models import User
from app.schemas.auth import AdminUserListResponse, AdminUserOut

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
