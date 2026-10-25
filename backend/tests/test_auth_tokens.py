"""Unit tests for token and password primitives (no database needed)."""

import base64
import json
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.auth.passwords import hash_password, validate_password_policy, verify_password
from app.auth.tokens import create_access_token, decode_access_token, resolve_jwt_secret
from app.config import Settings
from app.core.exceptions import AuthenticationError, ConfigurationError, ValidationFailedError

SECRET = "unit-test-secret-0123456789abcdef0123456789"
ISSUER = "sentinel-rag"


def _b64(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def _claims(**overrides) -> dict:
    now = datetime.now(UTC)
    claims = {
        "sub": "user-1",
        "wid": "ws-1",
        "typ": "access",
        "iss": ISSUER,
        "aud": ISSUER,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return claims


def test_round_trip():
    token = create_access_token("user-1", "ws-1", SECRET, ISSUER, timedelta(minutes=5))
    claims = decode_access_token(token, SECRET, ISSUER)
    assert (claims.user_id, claims.workspace_id) == ("user-1", "ws-1")


def test_rejects_token_signed_with_other_key():
    token = jwt.encode(_claims(), "attacker-key-0123456789abcdef0123456789", algorithm="HS256")
    with pytest.raises(AuthenticationError):
        decode_access_token(token, SECRET, ISSUER)


def test_rejects_alg_none():
    unsigned = f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64({'sub': 'admin', 'wid': 'x', 'typ': 'access'})}."
    with pytest.raises(AuthenticationError):
        decode_access_token(unsigned, SECRET, ISSUER)


def test_rejects_expired_token():
    past = datetime.now(UTC) - timedelta(hours=1)
    token = jwt.encode(_claims(iat=past, exp=past + timedelta(minutes=1)), SECRET, algorithm="HS256")
    with pytest.raises(AuthenticationError):
        decode_access_token(token, SECRET, ISSUER)


@pytest.mark.parametrize("override", [{"aud": "other-app"}, {"iss": "other-app"}, {"typ": "refresh"}])
def test_rejects_wrong_audience_issuer_or_type(override):
    token = jwt.encode(_claims(**override), SECRET, algorithm="HS256")
    with pytest.raises(AuthenticationError):
        decode_access_token(token, SECRET, ISSUER)


def test_rejects_token_missing_workspace_claim():
    claims = _claims()
    del claims["wid"]
    with pytest.raises(AuthenticationError):
        decode_access_token(jwt.encode(claims, SECRET, algorithm="HS256"), SECRET, ISSUER)


def test_production_requires_strong_secret():
    with pytest.raises(ConfigurationError):
        resolve_jwt_secret(Settings(APP_ENV="production", JWT_SECRET_KEY=""))
    with pytest.raises(ConfigurationError):
        resolve_jwt_secret(Settings(APP_ENV="production", JWT_SECRET_KEY="too-short"))
    assert resolve_jwt_secret(Settings(APP_ENV="production", JWT_SECRET_KEY=SECRET)) == SECRET


def test_development_generates_ephemeral_secret():
    first = resolve_jwt_secret(Settings(APP_ENV="development", JWT_SECRET_KEY=""))
    second = resolve_jwt_secret(Settings(APP_ENV="development", JWT_SECRET_KEY=""))
    assert len(first) >= 32 and first != second


def test_password_hash_is_argon2id_and_verifies():
    hashed = hash_password("a long enough passphrase")
    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, "a long enough passphrase")
    assert not verify_password(hashed, "a long enough passphrasE")


@pytest.mark.parametrize("stored", [None, "!", "not-a-hash"])
def test_verify_against_missing_or_unusable_hash_is_false(stored):
    assert verify_password(stored, "whatever") is False


@pytest.mark.parametrize(
    "password,email",
    [("short", "a@example.com"), ("x" * 129, "a@example.com"), ("Person@Example.com", "person@example.com")],
)
def test_password_policy_rejects(password, email):
    with pytest.raises(ValidationFailedError):
        validate_password_policy(password, email, min_length=12)
