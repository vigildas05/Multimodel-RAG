"""CSRF protection for cookie-authenticated requests.

SameSite=Lax cookies already stop most cross-site form posts. The CSRF token covers what
SameSite doesn't: requests from *same-site* origins (e.g. a compromised sibling subdomain)
and browsers with weaker SameSite handling.

Scheme: the server keeps a random token in an httpOnly cookie and hands the same value to
the frontend in a response body (GET /auth/csrf, login, register). Every state-changing
request must echo it in the X-CSRF-Token header. A cross-origin page can't read the token
(strict CORS) and can't add a custom header to a cross-origin request without a preflight
that CORS rejects.
"""

import hmac
import logging

from fastapi import Request
from fastapi.responses import JSONResponse

from app.auth.cookies import CSRF_COOKIE

logger = logging.getLogger("app.auth.csrf")

CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Requests that authenticate by other means: the Razorpay webhook is verified by an HMAC
# signature over its body (and carries no cookies).
CSRF_EXEMPT_PATHS: frozenset[str] = frozenset({"/webhooks/razorpay"})


async def csrf_middleware(request: Request, call_next):
    if request.method in SAFE_METHODS or request.url.path in CSRF_EXEMPT_PATHS:
        return await call_next(request)

    cookie_token = request.cookies.get(CSRF_COOKIE)
    header_token = request.headers.get(CSRF_HEADER)
    if not cookie_token or not header_token or not hmac.compare_digest(cookie_token, header_token):
        logger.warning("CSRF check failed for %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=403,
            content={
                "error_type": "csrf_failed",
                "message": "Missing or invalid CSRF token.",
                "detail": f"Fetch a token from GET /auth/csrf and send it in the {CSRF_HEADER} header.",
            },
        )
    return await call_next(request)
