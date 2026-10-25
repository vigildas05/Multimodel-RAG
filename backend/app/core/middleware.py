"""HTTP hardening middleware: request IDs, security headers, body size limits and the
global per-IP rate limit."""

import contextvars
import json
import logging
import re
import time
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.core.rate_limit import client_ip

logger = logging.getLogger("app.core.middleware")
access_logger = logging.getLogger("app.access")

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

# The API only ever returns JSON, so its responses can carry the strictest CSP.
API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
# Swagger/ReDoc pages (development only) load scripts from a CDN.
_DOCS_PATHS = ("/docs", "/redoc")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-site",
}
HSTS = "max-age=63072000; includeSubDomains"


class RequestIdLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def security_middleware(settings):
    """Assigns a request ID and adds security headers to every response, errors included."""

    async def middleware(request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception as exc:  # noqa: BLE001
                # Handled here (not only by the app's handler, which Starlette runs outside all
                # middleware) so the generic 500 still gets the request ID and security headers.
                from app.core.exception_handlers import unexpected_error_response

                response = unexpected_error_response(request, exc)
            _record_access(request, response.status_code, time.perf_counter() - started)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = request_id
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if not request.url.path.startswith(_DOCS_PATHS):
            response.headers.setdefault("Content-Security-Policy", API_CSP)
        # Responses may contain user data and session info: never cache them.
        response.headers.setdefault("Cache-Control", "no-store")
        if settings.APP_ENV == "production":
            response.headers.setdefault("Strict-Transport-Security", HSTS)
        return response

    return middleware


def _record_access(request: Request, status_code: int, seconds: float) -> None:
    from app.core.metrics import observe_request, route_label

    observe_request(request, status_code, seconds)
    # Path without the query string (it can carry search text); never headers or bodies.
    access_logger.info(
        "%s %s %s %.0fms", request.method, request.url.path, status_code, seconds * 1000,
        extra={
            "http_method": request.method,
            "http_path": request.url.path,
            "http_route": route_label(request),
            "http_status": status_code,
            "duration_ms": round(seconds * 1000, 1),
            "client_ip": client_ip(request),
        },
    )


def global_rate_limit_middleware():
    """Coarse per-IP limit on every request, before any routing or body parsing."""

    async def middleware(request: Request, call_next):
        if request.method == "OPTIONS" or request.url.path.startswith("/health"):
            return await call_next(request)
        limiter = request.app.state.rate_limiter
        policy = request.app.state.rate_limit_policies["global_ip"]
        result = await run_in_threadpool(limiter.hit, policy, client_ip(request))
        if not result.allowed:
            return JSONResponse(
                status_code=429,
                content={"error_type": "rate_limited", "message": "Too many requests. Please slow down.", "detail": None},
                headers={"Retry-After": str(result.retry_after_seconds)},
            )
        return await call_next(request)

    return middleware


class BodySizeLimitMiddleware:
    """Pure ASGI middleware capping request bodies: by Content-Length up front, and by
    counting bytes for streamed bodies that don't declare a length. Upload routes get
    the upload limit; everything else (JSON) a small one."""

    def __init__(self, app, max_json_bytes: int, max_upload_bytes: int, upload_paths: tuple[str, ...]):
        self.app = app
        self.max_json_bytes = max_json_bytes
        self.max_upload_bytes = max_upload_bytes
        self.upload_paths = upload_paths

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = self.max_upload_bytes if scope["path"] in self.upload_paths else self.max_json_bytes

        declared = dict(scope.get("headers", [])).get(b"content-length")
        if declared is not None:
            try:
                too_big = int(declared) > limit
            except ValueError:
                too_big = True
            if too_big:
                return await self._reject(send, limit)

        # Streamed body without a usable length: count bytes as they arrive. On overflow,
        # end the body early and replace whatever the app answers with a 413. (Raising
        # instead doesn't work: frameworks turn body-read exceptions into a generic 400.)
        received = 0
        too_large = False
        replaced = False

        async def counting_receive():
            nonlocal received, too_large
            if too_large:
                return {"type": "http.request", "body": b"", "more_body": False}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    too_large = True
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        async def guarded_send(message):
            nonlocal replaced
            if too_large:
                if not replaced:
                    replaced = True
                    await self._reject(send, limit)
                return
            await send(message)

        await self.app(scope, counting_receive, guarded_send)

    @staticmethod
    async def _reject(send, limit: int):
        body = json.dumps(
            {
                "error_type": "payload_too_large",
                "message": f"Request body too large (limit {limit // 1024} KB).",
                "detail": None,
            }
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})
