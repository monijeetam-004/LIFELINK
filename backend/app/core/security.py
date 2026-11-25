from __future__ import annotations

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import settings

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except (InvalidHashError, ValueError):
        return True


def generate_token(nbytes: int = 32) -> str:
    """URL-safe random token for email verification / session ids."""
    return secrets.token_urlsafe(nbytes)


def generate_otp() -> str:
    """Six-digit numeric one-time passcode."""
    return f"{secrets.randbelow(1_000_000):06d}"


def sign(value: str) -> str:
    """Keyed HMAC-SHA256 digest, used to store tokens/OTPs at rest.

    We never persist raw tokens or OTPs — only ``sign(value)``.
    """
    return hmac.new(
        settings.secret_key.encode("utf-8"), value.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)
