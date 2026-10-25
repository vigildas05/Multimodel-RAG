"""Auth flows through the real API: cookies, refresh rotation and reuse detection,
logout, CSRF, roles and deactivation."""

from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy import select, update

from app.auth.cookies import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE
from app.db.models import RefreshToken, SecurityEvent, User
from helpers import CSRF_HEADER, TEST_PASSWORD, fetch_csrf, login, set_cookie_headers, signup, unique_email


def _events(session_factory, event_type: str) -> list[SecurityEvent]:
    with session_factory() as session:
        return list(session.scalars(select(SecurityEvent).where(SecurityEvent.event_type == event_type)))


# --- registration -----------------------------------------------------------------


def test_register_sets_hardened_cookies(client):
    fetch_csrf(client)
    resp = client.post("/auth/register", json={"email": unique_email(), "password": TEST_PASSWORD})
    assert resp.status_code == 201

    cookies = set_cookie_headers(resp)
    for name in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE):
        flags = cookies[name].lower()
        assert "httponly" in flags and "secure" in flags and "samesite=lax" in flags, flags
    assert "path=/auth" in cookies[REFRESH_COOKIE].lower()

    body = resp.json()
    assert body["user"]["role"] == "user"
    assert body["csrf_token"]
    # Tokens travel only in cookies, never in the JSON body.
    assert "access_token" not in resp.text and "refresh_token" not in resp.text


def test_register_then_me(client):
    user = signup(client)
    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json() == user


def test_password_is_stored_as_argon2id(client, session_factory):
    email = unique_email()
    signup(client, email)
    with session_factory() as session:
        stored = session.scalar(select(User.password_hash).where(User.email == email))
    assert stored.startswith("$argon2id$")
    assert TEST_PASSWORD not in stored


def test_duplicate_email_rejected_case_insensitively(client_factory):
    email = unique_email()
    signup(client_factory(), email)

    other = client_factory()
    fetch_csrf(other)
    resp = other.post("/auth/register", json={"email": email.upper(), "password": TEST_PASSWORD})
    assert resp.status_code == 409
    assert resp.json()["error_type"] == "email_taken"


def test_weak_password_and_bad_email_rejected(client):
    fetch_csrf(client)
    short = client.post("/auth/register", json={"email": unique_email(), "password": "short"})
    assert short.status_code == 422
    bad_email = client.post("/auth/register", json={"email": "not-an-email", "password": TEST_PASSWORD})
    assert bad_email.status_code == 422


def test_register_ignores_client_supplied_role(client, session_factory):
    fetch_csrf(client)
    email = unique_email()
    resp = client.post("/auth/register", json={"email": email, "password": TEST_PASSWORD, "role": "admin"})
    assert resp.status_code == 201
    assert resp.json()["user"]["role"] == "user"


# --- login ------------------------------------------------------------------------


def test_login_success_and_generic_failures(client_factory, session_factory):
    email = unique_email()
    signup(client_factory(), email)

    ok = login(client_factory(), email)
    assert ok.status_code == 200

    wrong_password = login(client_factory(), email, "definitely the wrong one")
    unknown_email = login(client_factory(), unique_email("ghost"))
    assert wrong_password.status_code == unknown_email.status_code == 401
    # Same message either way, so the endpoint doesn't reveal which emails exist.
    assert wrong_password.json()["message"] == unknown_email.json()["message"]

    failures = _events(session_factory, "auth_failure")
    assert len(failures) == 2
    assert all("@" not in str(e.details) for e in failures)  # no raw emails in the log


def test_deactivated_user_is_locked_out_immediately(client, session_factory):
    email = unique_email()
    signup(client, email)
    with session_factory.begin() as session:
        session.execute(update(User).where(User.email == email).values(is_active=False))

    # The still-unexpired access token stops working on the very next request.
    assert client.get("/auth/me").status_code == 401
    assert login(client, email).status_code == 401


# --- access token handling ----------------------------------------------------------


def test_protected_route_requires_session(client):
    resp = client.get("/documents")
    assert resp.status_code == 401
    assert resp.json()["error_type"] == "not_authenticated"


def test_forged_access_token_rejected(client):
    user = signup(client)
    now = datetime.now(timezone.utc)
    forged = jwt.encode(
        {
            "sub": user["user_id"],
            "wid": user["workspace_id"],
            "typ": "access",
            "iss": "sentinel-rag",
            "aud": "sentinel-rag",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        "guessed-secret-0123456789abcdef0123456789",
        algorithm="HS256",
    )
    client.cookies.set(ACCESS_COOKIE, forged)
    assert client.get("/auth/me").status_code == 401


def test_access_token_for_foreign_workspace_rejected(client_factory):
    """A validly signed token naming a workspace the user isn't a member of is refused."""
    victim = signup(client_factory())
    attacker_client = client_factory()
    signup(attacker_client)

    from app.auth.tokens import create_access_token
    from app.config import get_settings

    settings = get_settings()
    token = create_access_token(
        attacker_client.get("/auth/me").json()["user_id"],
        victim["workspace_id"],
        settings.JWT_SECRET_KEY,
        settings.JWT_ISSUER,
        timedelta(minutes=5),
    )
    attacker_client.cookies.set(ACCESS_COOKIE, token)
    assert attacker_client.get("/auth/me").status_code == 401


# --- refresh rotation ---------------------------------------------------------------


def test_refresh_rotates_tokens(client):
    signup(client)
    old_refresh = client.cookies.get(REFRESH_COOKIE)

    resp = client.post("/auth/refresh")
    assert resp.status_code == 200
    new_refresh = client.cookies.get(REFRESH_COOKIE)
    assert new_refresh and new_refresh != old_refresh
    client.headers[CSRF_HEADER] = resp.json()["csrf_token"]
    assert client.get("/auth/me").status_code == 200


def test_refresh_token_reuse_revokes_family(client, session_factory):
    signup(client)
    stolen = client.cookies.get(REFRESH_COOKIE)

    # Legitimate client rotates first...
    first = client.post("/auth/refresh")
    assert first.status_code == 200
    legit_current = client.cookies.get(REFRESH_COOKIE)
    client.headers[CSRF_HEADER] = first.json()["csrf_token"]

    # ...then the attacker replays the old token: rejected, and the family is burned.
    client.cookies.set(REFRESH_COOKIE, stolen, path="/auth")
    replay = client.post("/auth/refresh")
    assert replay.status_code == 401

    client.cookies.set(REFRESH_COOKIE, legit_current, path="/auth")
    assert client.post("/auth/refresh").status_code == 401

    events = _events(session_factory, "refresh_token_reuse")
    assert len(events) == 1 and events[0].severity == "high"


def test_expired_refresh_token_rejected(client, session_factory):
    signup(client)
    with session_factory.begin() as session:
        session.execute(update(RefreshToken).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    resp = client.post("/auth/refresh")
    assert resp.status_code == 401
    # The failed refresh clears the dead session cookies.
    cleared = set_cookie_headers(resp)
    assert ACCESS_COOKIE in cleared and REFRESH_COOKIE in cleared


def test_refresh_without_cookie_rejected(client):
    fetch_csrf(client)
    assert client.post("/auth/refresh").status_code == 401


# --- logout -------------------------------------------------------------------------


def test_logout_revokes_refresh_token(client, session_factory):
    signup(client)
    refresh_cookie = client.cookies.get(REFRESH_COOKIE)

    resp = client.post("/auth/logout")
    assert resp.status_code == 204
    assert client.get("/auth/me").status_code == 401  # cookies cleared

    with session_factory() as session:
        assert session.scalar(select(RefreshToken).where(RefreshToken.revoked_at.is_(None))) is None

    # Replaying the pre-logout refresh token doesn't resurrect the session.
    client.cookies.set(REFRESH_COOKIE, refresh_cookie, path="/auth")
    assert client.post("/auth/refresh").status_code == 401


# --- CSRF ---------------------------------------------------------------------------


def test_state_changing_requests_require_csrf(client):
    signup(client)
    token = client.headers.pop(CSRF_HEADER)

    missing = client.post("/auth/logout")
    assert missing.status_code == 403 and missing.json()["error_type"] == "csrf_failed"

    wrong = client.post("/auth/logout", headers={CSRF_HEADER: "forged-" + token})
    assert wrong.status_code == 403

    # Safe methods don't need it; with the right token the request goes through.
    assert client.get("/auth/me").status_code == 200
    assert client.post("/auth/logout", headers={CSRF_HEADER: token}).status_code == 204


def test_login_requires_csrf(client):
    resp = client.post("/auth/login", json={"email": unique_email(), "password": TEST_PASSWORD})
    assert resp.status_code == 403


# --- roles --------------------------------------------------------------------------


def test_admin_routes_require_admin_role(client, session_factory):
    email = unique_email()
    signup(client, email)
    assert client.get("/admin/users").status_code == 403

    # Role comes from the database on every request, so promotion applies immediately.
    with session_factory.begin() as session:
        session.execute(update(User).where(User.email == email).values(role="admin"))
    resp = client.get("/admin/users")
    assert resp.status_code == 200
    listed = resp.json()["users"]
    assert [u["email"] for u in listed] == [email]
    assert "password_hash" not in resp.text
