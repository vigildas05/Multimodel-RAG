from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.auth.cookies import CSRF_COOKIE, REFRESH_COOKIE, clear_session_cookies, set_csrf_cookie, set_session_cookies
from app.auth.dependencies import get_auth_service, get_client_info, get_current_user
from app.auth.service import AuthenticatedUser, AuthService, ClientInfo, IssuedSession
from app.auth.tokens import new_opaque_token
from app.config import Settings
from app.core.exceptions import AuthenticationError
from app.services.demo_service import DemoService
from app.dependencies import get_app_settings, get_demo_service
from app.schemas.auth import CredentialsRequest, CsrfResponse, SessionResponse, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])

# Routes that must work without a session. Everything else in the app requires one
# (tests/test_route_protection.py fails if a new route forgets its auth dependency).
PUBLIC_ROUTES = {
    ("GET", "/auth/csrf"),
    ("POST", "/auth/register"),
    ("POST", "/auth/login"),
    ("POST", "/auth/refresh"),
    ("POST", "/auth/logout"),
    ("POST", "/auth/demo"),
}


def _user_out(user: AuthenticatedUser) -> UserOut:
    return UserOut(
        user_id=user.user_id,
        email=user.email,
        role=user.role,
        workspace_id=user.workspace_id,
        is_demo=user.is_demo,
    )


def _session_response(response: Response, settings: Settings, issued: IssuedSession) -> SessionResponse:
    set_session_cookies(response, settings, issued.access_token, issued.refresh_token)
    set_csrf_cookie(response, settings, issued.csrf_token)
    return SessionResponse(user=_user_out(issued.user), csrf_token=issued.csrf_token)


@router.get("/csrf", response_model=CsrfResponse)
async def get_csrf_token(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_app_settings),
) -> CsrfResponse:
    """Called by the frontend on boot. Cross-origin pages can't read this response (CORS),
    which is what makes echoing the token in a header meaningful."""
    token = request.cookies.get(CSRF_COOKIE) or new_opaque_token()
    set_csrf_cookie(response, settings, token)
    return CsrfResponse(csrf_token=token)


@router.post("/register", response_model=SessionResponse, status_code=201)
async def register(
    body: CredentialsRequest,
    response: Response,
    settings: Settings = Depends(get_app_settings),
    auth_service: AuthService = Depends(get_auth_service),
    client: ClientInfo = Depends(get_client_info),
) -> SessionResponse:
    issued = await run_in_threadpool(auth_service.register, body.email, body.password, client)
    return _session_response(response, settings, issued)


@router.post("/login", response_model=SessionResponse)
async def login(
    body: CredentialsRequest,
    response: Response,
    settings: Settings = Depends(get_app_settings),
    auth_service: AuthService = Depends(get_auth_service),
    client: ClientInfo = Depends(get_client_info),
) -> SessionResponse:
    issued = await run_in_threadpool(auth_service.login, body.email, body.password, client)
    return _session_response(response, settings, issued)


@router.post("/demo", response_model=SessionResponse, status_code=201)
async def start_demo(
    response: Response,
    settings: Settings = Depends(get_app_settings),
    demo_service: DemoService = Depends(get_demo_service),
    client: ClientInfo = Depends(get_client_info),
) -> SessionResponse:
    """Create a private, expiring guest workspace seeded with the sample documents."""
    issued = await run_in_threadpool(demo_service.start, client)
    return _session_response(response, settings, issued)


@router.post("/refresh", response_model=SessionResponse)
async def refresh(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_app_settings),
    auth_service: AuthService = Depends(get_auth_service),
    client: ClientInfo = Depends(get_client_info),
):
    try:
        issued = await run_in_threadpool(auth_service.refresh, request.cookies.get(REFRESH_COOKIE), client)
    except AuthenticationError as exc:
        # Clear the dead cookies so the browser stops presenting them.
        failure = JSONResponse(
            status_code=exc.status_code,
            content={"error_type": exc.error_type, "message": exc.message, "detail": exc.detail},
        )
        clear_session_cookies(failure, settings)
        return failure
    return _session_response(response, settings, issued)


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    settings: Settings = Depends(get_app_settings),
    auth_service: AuthService = Depends(get_auth_service),
    client: ClientInfo = Depends(get_client_info),
) -> Response:
    await run_in_threadpool(auth_service.logout, request.cookies.get(REFRESH_COOKIE), client)
    response = Response(status_code=204)
    clear_session_cookies(response, settings)
    return response


@router.get("/me", response_model=UserOut)
async def me(user: AuthenticatedUser = Depends(get_current_user)) -> UserOut:
    return _user_out(user)
