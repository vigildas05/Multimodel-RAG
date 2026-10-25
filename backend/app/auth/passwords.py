"""argon2id password hashing (argon2-cffi defaults: the RFC 9106 low-memory profile,
t=3, m=64 MiB, p=4)."""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.exceptions import ValidationFailedError

# Upper bound keeps a single login request from being used to burn CPU on huge inputs.
PASSWORD_MAX_LENGTH = 128

_hasher = PasswordHasher()

# Logins for unknown emails verify against this, so they cost the same time as a real
# mismatch and response timing doesn't reveal which emails are registered.
_DUMMY_HASH = _hasher.hash("timing-equalization-only")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash or _DUMMY_HASH, password) and stored_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return False


def validate_password_policy(password: str, email: str, min_length: int) -> None:
    """Length-based policy (NIST SP 800-63B): no composition rules, but a real minimum."""
    if len(password) < min_length:
        raise ValidationFailedError(f"Password must be at least {min_length} characters long.")
    if len(password) > PASSWORD_MAX_LENGTH:
        raise ValidationFailedError(f"Password must be at most {PASSWORD_MAX_LENGTH} characters long.")
    if password.strip().lower() == email.strip().lower():
        raise ValidationFailedError("Password must not be the same as your email address.")
