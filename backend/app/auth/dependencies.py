"""Auth dependencies. Every non-public router depends on get_current_user (enforced by
tests/test_route_protection.py), and admin routes on require_admin."""

from fastapi import Depends, Request
from starlette.concurrency import run_in_threadpool

from app.auth.cookies import ACCESS_COOKIE
from app.auth.service import AuthenticatedUser, AuthService, ClientInfo
from app.core.exceptions import AuthenticationError, PermissionDeniedError


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


def get_client_info(request: Request) -> ClientInfo:
    return ClientInfo(
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )


async def get_current_user(
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
) -> AuthenticatedUser:
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        raise AuthenticationError("You need to sign in to do that.")
    return await run_in_threadpool(auth_service.resolve_access_token, token)


async def require_admin(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
    if user.role != "admin":
        raise PermissionDeniedError("Administrator access is required.")
    return user
