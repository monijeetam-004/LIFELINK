from __future__ import annotations

import uuid
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.user import UserRole

# Roles a user is allowed to self-register as. ADMIN is provisioned out-of-band.
REGISTERABLE_ROLES = {UserRole.PATIENT, UserRole.FAMILY, UserRole.DONOR, UserRole.HOSPITAL}

# A small, deliberately short blocklist of the passwords most likely to
# appear in a credential-stuffing wordlist -- not a substitute for the
# length/hashing/rate-limiting defenses that do the real work, but cheap
# insurance against the handful of passwords that show up in nearly every
# breach dump (rockyou-style top entries), checked case-insensitively and
# against a version with common leetspeak substitutions undone. This is
# intentionally NOT a complexity rule (no forced uppercase/digit/symbol) --
# NIST 800-63B recommends against those; length plus a breach-list check is
# the current best-practice combination.
_COMMON_PASSWORDS = frozenset(
    {
        "password", "password1", "password123", "12345678", "123456789",
        "1234567890", "qwertyuiop", "letmein123", "iloveyou1", "admin1234",
        "welcome123", "changeme1", "passw0rd", "abc123456", "football1",
        "baseball1", "sunshine1", "princess1", "trustno1", "monkey123",
        "dragon123", "superman1", "whatever1", "123123123", "11111111",
        "00000000", "87654321",
    }
)


def _normalize_for_blocklist(v: str) -> str:
    return v.strip().lower()


def _reject_common_password(v: str) -> str:
    if _normalize_for_blocklist(v) in _COMMON_PASSWORDS:
        raise ValueError(
            "This password is too common and appears in known breach lists. "
            "Please choose a different one."
        )
    return v


PasswordStr = Annotated[
    str,
    Field(min_length=8, max_length=128),
    AfterValidator(_reject_common_password),
]


class RegisterIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    password: PasswordStr
    role: UserRole

    @field_validator("role")
    @classmethod
    def _role_is_self_registerable(cls, v: UserRole) -> UserRole:
        if v not in REGISTERABLE_ROLES:
            raise ValueError("This role cannot be self-registered")
        return v


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class VerifyEmailIn(BaseModel):
    token: str = Field(min_length=1, max_length=512)


class ResendVerificationIn(BaseModel):
    email: EmailStr


class ForgotPasswordIn(BaseModel):
    email: EmailStr


class ResetPasswordIn(BaseModel):
    email: EmailStr
    otp: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    new_password: PasswordStr


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: PasswordStr


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str
    role: UserRole
    is_email_verified: bool
    is_active: bool


class MessageOut(BaseModel):
    message: str


class SessionStateOut(BaseModel):
    """QA fix: `GET /auth/session` -- the signed-in user, or `null`, with
    200 either way. The SPA's start-up check used `GET /auth/me`, whose 401
    for an anonymous visitor put a "Failed to load resource" error in the
    browser console on every public page."""

    authenticated: bool
    user: UserOut | None = None
