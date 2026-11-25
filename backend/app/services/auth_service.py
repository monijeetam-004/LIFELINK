"""Authentication flows: register, login, email verification, password reset.

Functions raise ``HTTPException`` for client-facing errors and return ORM
objects on success. The caller (route) owns cookie handling and ``db.commit()``.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    constant_time_equals,
    generate_otp,
    generate_token,
    hash_password,
    password_needs_rehash,
    sign,
    verify_password,
)
from app.core.time import utcnow
from app.models.email_verification_token import EmailVerificationToken
from app.models.password_reset_otp import PasswordResetOTP
from app.models.registration_counter import RegistrationCounter
from app.models.user import User, UserRole
from app.schemas.auth import RegisterIn
from app.services import audit_service, email_service, session_service


def normalize_email(email: str) -> str:
    return email.strip().lower()


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == normalize_email(email)))


# --------------------------------------------------------------------------- #
# Register + email verification
# --------------------------------------------------------------------------- #
def _issue_verification_token(db: Session, user: User) -> str:
    # Supersede any outstanding tokens.
    db.query(EmailVerificationToken).filter(
        EmailVerificationToken.user_id == user.id,
        EmailVerificationToken.consumed_at.is_(None),
    ).update({EmailVerificationToken.consumed_at: utcnow()}, synchronize_session=False)

    raw = generate_token()
    db.add(
        EmailVerificationToken(
            user_id=user.id,
            token_hash=sign(raw),
            expires_at=utcnow() + timedelta(seconds=settings.email_verification_ttl_seconds),
        )
    )
    return raw


def _reserve_registration_slot(db: Session, role: UserRole) -> None:
    """Phase 11 -- enforce this role's independent self-registration cap.

    Four separate limits (PATIENT/FAMILY/DONOR/HOSPITAL), each capped at
    `settings.max_accounts`, NOT one shared pool and NOT a
    `SELECT COUNT(*) FROM users` query -- see
    `app.models.registration_counter.RegistrationCounter`'s docstring for
    why. This must be safe against two concurrent registrations for the
    *same* role both observing "99, still room" and both succeeding, so:

    1. Race-safe upsert (`INSERT ... ON CONFLICT DO NOTHING`) guarantees a
       counter row exists for this role without a prior existence check
       that could itself race. In real deployments the Phase 11 migration
       already created all four rows at 0; this only matters for an
       environment that builds its schema without running migrations
       (e.g. the test suite's `Base.metadata.create_all`).
    2. `SELECT ... FOR UPDATE` locks that row for the rest of this
       transaction -- the same pattern Phase 10 established for
       `BloodRequest`/inventory rows. A second concurrent call for the same
       role blocks here until the first commits (or rolls back), then
       re-reads the now-current count, so the invariant
       `count <= settings.max_accounts` holds under real concurrent load.

    Raises 409 and leaves the counter untouched if the role is already at
    capacity; otherwise increments it by one. Does not commit -- the caller
    (`register()`) commits once, together with the new `User` row, exactly
    like every other mutation in this codebase.
    """
    db.execute(
        pg_insert(RegistrationCounter)
        .values(role=role, count=0)
        .on_conflict_do_nothing(index_elements=["role"])
    )
    counter = db.execute(
        select(RegistrationCounter).where(RegistrationCounter.role == role).with_for_update()
    ).scalar_one()
    if counter.count >= settings.max_accounts:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{role.value} account limit reached. Maximum {settings.max_accounts} "
                f"{role.value} accounts are currently allowed."
            ),
        )
    counter.count += 1


def register(db: Session, payload: RegisterIn, request: Request) -> User:
    email = normalize_email(payload.email)
    if get_user_by_email(db, email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        )
    _reserve_registration_slot(db, payload.role)

    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name.strip(),
        role=payload.role,
        is_email_verified=False,
        is_active=True,
        password_changed_at=utcnow(),
    )
    # QA fix: two simultaneous registrations of the same address both pass
    # the pre-check above; the loser's INSERT hit the unique index and the
    # request ended in HTTP 500. Now it gets the same 409 as the pre-check
    # (and its transaction -- including the registration-slot increment --
    # is rolled back by the caller).
    try:
        with db.begin_nested():
            db.add(user)
            db.flush()
    except IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        ) from None

    raw_token = _issue_verification_token(db, user)
    email_service.send_verification_email(user.email, user.full_name, raw_token)
    audit_service.record(
        db, "user.register", actor_user_id=user.id, target_type="user",
        target_id=user.id, request=request, meta={"role": user.role.value},
    )
    return user


def verify_email(db: Session, token: str, request: Request) -> User:
    row = db.scalar(
        select(EmailVerificationToken).where(
            EmailVerificationToken.token_hash == sign(token)
        )
    )
    if row is None or row.consumed_at is not None or row.expires_at < utcnow():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This verification link is invalid or has expired",
        )
    user = db.get(User, row.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid token")

    row.consumed_at = utcnow()
    user.is_email_verified = True
    audit_service.record(
        db, "auth.email_verified", actor_user_id=user.id, target_type="user",
        target_id=user.id, request=request,
    )
    return user


def resend_verification(db: Session, email: str, request: Request) -> None:
    user = get_user_by_email(db, email)
    if user is not None and not user.is_email_verified:
        raw_token = _issue_verification_token(db, user)
        email_service.send_verification_email(user.email, user.full_name, raw_token)
        audit_service.record(
            db, "auth.verification_resent", actor_user_id=user.id,
            target_type="user", target_id=user.id, request=request,
        )


# --------------------------------------------------------------------------- #
# Login
# --------------------------------------------------------------------------- #
def authenticate(db: Session, email: str, password: str, request: Request) -> User:
    generic = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
    )
    user = get_user_by_email(db, email)
    if user is None or not verify_password(user.password_hash, password):
        audit_service.record(
            db, "user.login_failed", target_type="user",
            target_id=user.id if user else None, request=request,
            meta={"email": normalize_email(email)},
        )
        db.commit()  # persist the security event even though the request fails
        raise generic
    if not user.is_active:
        audit_service.record(
            db, "user.login_denied_inactive", actor_user_id=user.id,
            target_type="user", target_id=user.id, request=request,
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="This account has been disabled"
        )
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)

    audit_service.record(
        db, "user.login", actor_user_id=user.id, target_type="user",
        target_id=user.id, request=request,
    )
    return user


# --------------------------------------------------------------------------- #
# Forgot / reset password
# --------------------------------------------------------------------------- #
def request_password_reset(db: Session, email: str, request: Request) -> None:
    user = get_user_by_email(db, email)
    if user is not None and user.is_active:
        db.query(PasswordResetOTP).filter(
            PasswordResetOTP.user_id == user.id,
            PasswordResetOTP.consumed_at.is_(None),
        ).update({PasswordResetOTP.consumed_at: utcnow()}, synchronize_session=False)

        otp = generate_otp()
        db.add(
            PasswordResetOTP(
                user_id=user.id,
                otp_hash=sign(otp),
                expires_at=utcnow()
                + timedelta(seconds=settings.password_reset_otp_ttl_seconds),
            )
        )
        email_service.send_password_reset_otp(user.email, user.full_name, otp)
        audit_service.record(
            db, "auth.password_reset_requested", actor_user_id=user.id,
            target_type="user", target_id=user.id, request=request,
        )


def reset_password(
    db: Session, email: str, otp: str, new_password: str, request: Request
) -> User:
    generic = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="The reset code is invalid or has expired",
    )
    user = get_user_by_email(db, email)
    if user is None:
        raise generic

    # Phase 25: this row is read, then mutated (attempts += 1, or
    # consumed_at set) and committed later in this same function -- every
    # other mutate-after-read row in this codebase (RegistrationCounter,
    # BloodRequest, Emergency, FamilyLink, inventory, VerificationRequest,
    # TransportRoute, Donation-for-certificate) locks the row with
    # `with_for_update()` for exactly this reason. Without it, concurrent
    # wrong-OTP guesses race: each reads the same stale `attempts`, each
    # independently computes `attempts + 1`, and the last COMMIT wins (a
    # lost update) -- so `password_reset_max_attempts` can be bypassed by
    # guessing in parallel instead of serially. `with_for_update()` makes
    # concurrent requests against the same row serialize, so the counter
    # accumulates correctly and the lockout can't be raced past. See
    # tests/test_phase25_qa_hardening.py for the reproduction.
    row = db.scalar(
        select(PasswordResetOTP)
        .where(
            PasswordResetOTP.user_id == user.id,
            PasswordResetOTP.consumed_at.is_(None),
        )
        .order_by(PasswordResetOTP.created_at.desc())
        .with_for_update()
    )
    if row is None or row.expires_at < utcnow():
        raise generic
    if row.attempts >= settings.password_reset_max_attempts:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many incorrect attempts. Request a new code.",
        )
    if not constant_time_equals(row.otp_hash, sign(otp)):
        row.attempts += 1
        audit_service.record(
            db, "auth.password_reset_failed", actor_user_id=user.id,
            target_type="user", target_id=user.id, request=request,
        )
        db.commit()
        raise generic

    row.consumed_at = utcnow()
    user.password_hash = hash_password(new_password)
    user.password_changed_at = utcnow()
    revoked = session_service.delete_user_sessions(user.id)
    audit_service.record(
        db, "auth.password_reset", actor_user_id=user.id, target_type="user",
        target_id=user.id, request=request,
    )
    audit_service.record(
        db, "auth.sessions_invalidated", actor_user_id=user.id, target_type="user",
        target_id=user.id, request=request, meta={"revoked_sessions": revoked},
    )
    return user
