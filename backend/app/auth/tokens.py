"""Access tokens (short-lived HS256 JWTs) and opaque refresh / CSRF tokens."""

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from app.config import Settings
from app.core.exceptions import AuthenticationError, ConfigurationError

logger = logging.getLogger("app.auth.tokens")

# Pinned: never let the token header choose the algorithm ("none", or RS/HS confusion).
JWT_ALGORITHM = "HS256"
MIN_SECRET_BYTES = 32


@dataclass(frozen=True)
class AccessTokenClaims:
    user_id: str
    workspace_id: str


def resolve_jwt_secret(settings: Settings) -> str:
    """Return the signing key, refusing weak or missing keys in production."""
    secret = settings.JWT_SECRET_KEY
    if secret:
        if len(secret.encode()) < MIN_SECRET_BYTES:
            raise ConfigurationError(f"JWT_SECRET_KEY must be at least {MIN_SECRET_BYTES} bytes.")
        return secret
    if settings.APP_ENV == "production":
        raise ConfigurationError("JWT_SECRET_KEY must be set in production.")
    logger.warning("JWT_SECRET_KEY is not set; using a random per-process key (sessions end on restart).")
    return secrets.token_urlsafe(48)


def create_access_token(user_id: str, workspace_id: str, secret: str, issuer: str, ttl: timedelta) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "wid": workspace_id,
        "typ": "access",
        "iss": issuer,
        "aud": issuer,
        "iat": now,
        "exp": now + ttl,
        "jti": secrets.token_hex(8),
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str, secret: str, issuer: str) -> AccessTokenClaims:
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[JWT_ALGORITHM],
            audience=issuer,
            issuer=issuer,
            options={"require": ["exp", "iat", "sub", "wid", "typ"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Your session is invalid or has expired.") from exc
    if payload["typ"] != "access":
        raise AuthenticationError("Your session is invalid or has expired.")
    return AccessTokenClaims(user_id=payload["sub"], workspace_id=payload["wid"])


def new_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(raw_token: str) -> str:
    """Refresh tokens are high-entropy random values, so a plain SHA-256 (no salt or slow
    KDF) is enough to make a leaked token table useless."""
    return hashlib.sha256(raw_token.encode()).hexdigest()
