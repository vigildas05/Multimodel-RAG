"""Session cookies. All are httpOnly: the frontend never reads them. It receives the CSRF
token in response bodies instead (see app.auth.csrf)."""

from fastapi import Response

from app.config import Settings

ACCESS_COOKIE = "sentinel_access"
REFRESH_COOKIE = "sentinel_refresh"
CSRF_COOKIE = "sentinel_csrf"

# The refresh token is only ever sent to the auth endpoints, never to the rest of the API.
REFRESH_COOKIE_PATH = "/auth"


def _set(response: Response, settings: Settings, name: str, value: str, max_age: int, path: str = "/") -> None:
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        path=path,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
    )


def set_session_cookies(response: Response, settings: Settings, access_token: str, refresh_token: str) -> None:
    _set(response, settings, ACCESS_COOKIE, access_token, settings.ACCESS_TOKEN_TTL_MINUTES * 60)
    _set(
        response,
        settings,
        REFRESH_COOKIE,
        refresh_token,
        settings.REFRESH_TOKEN_TTL_DAYS * 86400,
        path=REFRESH_COOKIE_PATH,
    )


def set_csrf_cookie(response: Response, settings: Settings, csrf_token: str) -> None:
    _set(response, settings, CSRF_COOKIE, csrf_token, settings.REFRESH_TOKEN_TTL_DAYS * 86400)


def clear_session_cookies(response: Response, settings: Settings) -> None:
    for name, path in ((ACCESS_COOKIE, "/"), (REFRESH_COOKIE, REFRESH_COOKIE_PATH)):
        response.delete_cookie(
            name, path=path, httponly=True, secure=settings.COOKIE_SECURE, samesite=settings.COOKIE_SAMESITE
        )
