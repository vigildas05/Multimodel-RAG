"""Layer 5 (app security): rate limits, CORS, security headers, request size limits and
error hygiene."""

import time
import uuid

import pytest
import redis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import CSRF_HEADER, TEST_PASSWORD, fetch_csrf, signup, unique_email, upload_pdf
from pydantic import BaseModel

from app.config import get_settings
from app.core.exceptions import AppError, ConfigurationError
from app.core.rate_limit import RateLimiter, RateLimitPolicy
from app.main import create_app, install_http_security, validate_security_settings

TEST_REDIS_URL = "redis://localhost:6379/15"
ORIGIN = "http://localhost:5173"


class Echo(BaseModel):
    text: str


class InternalFailure(AppError):
    status_code = 503
    error_type = "dependency_down"


def mini_app(**overrides) -> FastAPI:
    """The real middleware stack on a bare app, plus test-only routes."""
    settings = get_settings().model_copy(update={"RATE_LIMIT_ENABLED": False, **overrides})
    app = FastAPI()
    install_http_security(app, settings)

    @app.get("/ok")
    async def ok():
        return {"ok": True}

    @app.get("/boom")
    async def boom():
        raise RuntimeError("database password=hunter2 at C:\\secret\\config.py")

    @app.get("/dependency-down")
    async def dependency_down():
        raise InternalFailure("The search service is unavailable.", detail="connect to 10.0.0.7:6333 refused")

    @app.post("/echo")
    async def echo(body: Echo):
        return {"length": len(body.text)}

    return app


def csrf_client(app) -> TestClient:
    """A client that passes the CSRF check, so tests reach the layers behind it."""
    client = TestClient(app)
    client.cookies.set("sentinel_csrf", "test-token")
    client.headers[CSRF_HEADER] = "test-token"
    return client


def with_client_ip(app, ip: str):
    async def asgi(scope, receive, send):
        if scope["type"] == "http":
            scope = {**scope, "client": (ip, 50000)}
        await app(scope, receive, send)

    return asgi


# --- security headers & request IDs ------------------------------------------------------


@pytest.mark.parametrize("path,status", [("/ok", 200), ("/missing", 404), ("/boom", 500)])
def test_security_headers_on_every_response(path, status):
    resp = TestClient(mini_app(), raise_server_exceptions=False).get(path)
    assert resp.status_code == status
    h = resp.headers
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in h["Content-Security-Policy"]
    assert h["Referrer-Policy"] == "no-referrer"
    assert h["Cache-Control"] == "no-store"
    assert "Strict-Transport-Security" not in h  # only in production


def test_hsts_in_production():
    resp = TestClient(mini_app(APP_ENV="production")).get("/ok")
    assert resp.headers["Strict-Transport-Security"].startswith("max-age=63072000")


def test_security_headers_on_real_app_errors(client):
    resp = client.get("/documents")  # 401
    assert resp.status_code == 401 and resp.headers["X-Frame-Options"] == "DENY"


def test_request_ids():
    client = TestClient(mini_app())
    generated = client.get("/ok").headers["X-Request-ID"]
    assert len(generated) == 32
    assert client.get("/ok", headers={"X-Request-ID": "trace-12345678"}).headers["X-Request-ID"] == "trace-12345678"
    injected = client.get("/ok", headers={"X-Request-ID": "bad id\r\nSet-Cookie: x=1"}).headers["X-Request-ID"]
    assert injected != "bad id" and len(injected) == 32


# --- error hygiene ----------------------------------------------------------------------


def test_unexpected_errors_are_generic_500s_with_a_request_id():
    resp = TestClient(mini_app(), raise_server_exceptions=False).get("/boom")
    assert resp.status_code == 500
    body = resp.json()
    assert body["error_type"] == "internal_error"
    assert "hunter2" not in resp.text and "secret" not in resp.text and "RuntimeError" not in resp.text
    assert body["request_id"] == resp.headers["X-Request-ID"]


def test_server_error_details_hidden_outside_development():
    resp = TestClient(mini_app(APP_ENV="test")).get("/dependency-down")
    assert resp.status_code == 503 and resp.json()["detail"] is None and "10.0.0.7" not in resp.text
    assert resp.json()["message"] == "The search service is unavailable."


def test_server_error_details_shown_in_development(monkeypatch):
    monkeypatch.setattr(get_settings(), "APP_ENV", "development")
    assert "10.0.0.7" in TestClient(mini_app()).get("/dependency-down").json()["detail"]


def test_validation_errors_do_not_echo_input(client):
    fetch_csrf(client)
    marker = "MY-SECRET-PASSWORD-" + uuid.uuid4().hex
    resp = client.post("/auth/register", json={"email": "not-an-email", "password": marker * 10})
    assert resp.status_code == 422 and resp.json()["error_type"] == "validation_failed"
    assert marker not in resp.text


# --- request size limits ------------------------------------------------------------------


def test_oversized_json_is_rejected_before_parsing():
    resp = csrf_client(mini_app(MAX_JSON_BODY_KB=1)).post("/echo", json={"text": "x" * 5000})
    assert resp.status_code == 413 and resp.json()["error_type"] == "payload_too_large"


def test_oversized_streamed_body_without_content_length_is_rejected():
    def chunks():
        for _ in range(10):
            yield b"x" * 1024

    resp = csrf_client(mini_app(MAX_JSON_BODY_KB=4)).post(
        "/echo", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 413


def test_small_bodies_pass():
    assert csrf_client(mini_app()).post("/echo", json={"text": "hello"}).json() == {"length": 5}


def test_upload_route_uses_upload_limit(client, monkeypatch):
    signup(client)
    upload_pdf(client, "small enough")  # well over 64 KB JSON limit is irrelevant for uploads
    resp = client.post(
        "/documents/upload",
        files={"file": ("big.pdf", b"%PDF-1.4\n" + b"0" * (30 * 1024 * 1024), "application/pdf")},
    )
    assert resp.status_code == 413


# --- CORS ---------------------------------------------------------------------------------


def _preflight(client, origin, method="POST", headers="content-type,x-csrf-token"):
    return client.options(
        "/ok",
        headers={"Origin": origin, "Access-Control-Request-Method": method, "Access-Control-Request-Headers": headers},
    )


def test_cors_allows_the_configured_origin_with_credentials():
    resp = _preflight(TestClient(mini_app(CORS_ORIGINS=ORIGIN)), ORIGIN)
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == ORIGIN
    assert resp.headers["access-control-allow-credentials"] == "true"


@pytest.mark.parametrize(
    "origin,method,headers",
    [
        ("https://evil.example", "POST", "content-type"),  # unknown origin
        ("http://localhost:5173.evil.example", "POST", "content-type"),  # look-alike origin
        (ORIGIN, "PUT", "content-type"),  # method the API doesn't use
        (ORIGIN, "POST", "x-evil-header"),  # header the API doesn't accept
    ],
)
def test_cors_rejects_everything_else(origin, method, headers):
    resp = _preflight(TestClient(mini_app(CORS_ORIGINS=ORIGIN)), origin, method, headers)
    assert resp.status_code == 400 or resp.headers.get("access-control-allow-origin") != origin


def test_simple_request_from_unknown_origin_gets_no_cors_headers():
    resp = TestClient(mini_app(CORS_ORIGINS=ORIGIN)).get("/ok", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in resp.headers


# --- startup configuration checks --------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"CORS_ORIGINS": "*"},
        {"APP_ENV": "production", "CORS_ORIGINS": "http://app.example.com"},
        {"APP_ENV": "production", "CORS_ORIGINS": "https://app.example.com", "COOKIE_SECURE": False},
        {"APP_ENV": "production", "CORS_ORIGINS": "https://app.example.com", "COOKIE_SAMESITE": "none"},
    ],
)
def test_insecure_configurations_refuse_to_start(overrides):
    with pytest.raises(ConfigurationError):
        validate_security_settings(get_settings().model_copy(update=overrides))


def test_production_hides_api_docs(monkeypatch):
    monkeypatch.setattr(get_settings(), "APP_ENV", "production")
    monkeypatch.setattr(get_settings(), "CORS_ORIGINS", "https://app.example.com")
    client = TestClient(create_app())  # no lifespan: only routing is exercised
    assert client.get("/docs").status_code == 404 and client.get("/openapi.json").status_code == 404


# --- rate limiting --------------------------------------------------------------------------


@pytest.fixture
def limiter():
    try:
        redis.Redis.from_url(TEST_REDIS_URL).ping()
    except redis.ConnectionError:
        pytest.skip("Redis is not running on localhost:6379")
    return RateLimiter(TEST_REDIS_URL, enabled=True, key_prefix=f"rl-test-{uuid.uuid4().hex[:8]}")


@pytest.fixture
def limits(app, limiter, monkeypatch):
    """Enable rate limiting on the shared app with tiny limits for one test."""
    monkeypatch.setattr(app.state, "rate_limiter", limiter)
    policies = dict(app.state.rate_limit_policies)
    monkeypatch.setattr(app.state, "rate_limit_policies", policies)

    def set_limit(name: str, limit: int, window: int = 60):
        policies[name] = RateLimitPolicy(name, limit, window)

    return set_limit


def test_sliding_window(limiter):
    policy = RateLimitPolicy("unit", limit=2, window_seconds=1)
    assert limiter.hit(policy, "a").allowed and limiter.hit(policy, "a").allowed
    blocked = limiter.hit(policy, "a")
    assert not blocked.allowed and blocked.retry_after_seconds >= 1
    assert limiter.hit(policy, "b").allowed  # other subjects unaffected
    time.sleep(1.1)
    assert limiter.hit(policy, "a").allowed


def test_limiter_fails_open_when_redis_is_down():
    down = RateLimiter("redis://localhost:6390/0", enabled=True)
    assert down.hit(RateLimitPolicy("x", 1, 60), "s").allowed
    assert down.hit(RateLimitPolicy("x", 1, 60), "s").allowed


def test_login_is_limited_per_ip(client, limits):
    limits("login_ip", 3)
    fetch_csrf(client)
    statuses = [
        client.post("/auth/login", json={"email": unique_email(), "password": "wrong password here"}).status_code
        for _ in range(4)
    ]
    assert statuses == [401, 401, 401, 429]
    blocked = client.post("/auth/login", json={"email": unique_email(), "password": "x"})
    assert int(blocked.headers["Retry-After"]) >= 1 and blocked.json()["error_type"] == "rate_limited"


def test_login_is_limited_per_account_across_ips(app, client, limits):
    email = unique_email()
    signup(client, email)
    limits("login_account", 2, 3600)
    results = []
    for i in range(3):
        c = TestClient(with_client_ip(app, f"203.0.113.{i}"), base_url="https://testserver")
        fetch_csrf(c)
        results.append(c.post("/auth/login", json={"email": email, "password": "wrong guess " + str(i)}).status_code)
    assert results == [401, 401, 429]


def test_chat_is_limited_per_user_without_consuming_quota(app, client_factory, limits, monkeypatch):
    from test_security_gateway import RecordingLLM

    monkeypatch.setattr(app.state, "llm_provider", RecordingLLM())
    limits("chat_user", 2)
    alice, bob = client_factory(), client_factory()
    for c in (alice, bob):
        signup(c)
        upload_pdf(c, "Enzymes lower activation energy.")
    codes = [alice.post("/chat", json={"question": "What do enzymes do?"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    assert alice.get("/usage/me").json()["queries_today"] == 2  # the throttled call wasn't charged
    assert bob.post("/chat", json={"question": "What do enzymes do?"}).status_code == 200


def test_global_per_ip_limit_exempts_health(app, limits):
    limits("global_ip", 3)
    c = TestClient(with_client_ip(app, "198.51.100.9"), base_url="https://testserver")
    codes = [c.get("/auth/csrf").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    assert c.get("/health").status_code == 200
    other = TestClient(with_client_ip(app, "198.51.100.10"), base_url="https://testserver")
    assert other.get("/auth/csrf").status_code == 200


def test_register_is_limited_per_ip(client, limits):
    limits("register_ip", 1, 3600)
    fetch_csrf(client)
    first = client.post("/auth/register", json={"email": unique_email(), "password": TEST_PASSWORD})
    assert first.status_code == 201
    client.headers[CSRF_HEADER] = first.json()["csrf_token"]
    assert client.post("/auth/register", json={"email": unique_email(), "password": TEST_PASSWORD}).status_code == 429
