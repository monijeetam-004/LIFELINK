from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.api.cookies import clear_session_cookie, set_session_cookie
from app.api.deps import get_current_user, get_db, get_optional_user, verify_origin
from app.core.config import settings
from app.core.ratelimit import enforce_rate_limit, reset_rate_limit
from app.core.security import hash_password, verify_password
from app.core.time import utcnow
from app.models.user import User
from app.schemas.auth import (
    ChangePasswordIn,
    ForgotPasswordIn,
    LoginIn,
    MessageOut,
    RegisterIn,
    ResendVerificationIn,
    ResetPasswordIn,
    SessionStateOut,
    UserOut,
    VerifyEmailIn,
)
from app.services import audit_service, auth_service, notification_service, session_service

router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[Depends(verify_origin)])


def _client_key(request: Request) -> str:
    return audit_service.client_ip(request) or "unknown"


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterIn, request: Request, db: Session = Depends(get_db)) -> User:
    limit, window = settings.rl_register_ip
    enforce_rate_limit("register:ip", _client_key(request), limit, window)
    user = auth_service.register(db, payload, request)
    db.commit()
    return user


@router.post("/login", response_model=UserOut)
def login(
    payload: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)
) -> User:
    ip_limit, ip_window = settings.rl_login_ip
    acct_limit, acct_window = settings.rl_login_account
    email_key = auth_service.normalize_email(payload.email)
    enforce_rate_limit("login:ip", _client_key(request), ip_limit, ip_window)
    enforce_rate_limit("login:account", email_key, acct_limit, acct_window)

    user = auth_service.authenticate(db, payload.email, payload.password, request)
    sid = session_service.create_session(
        user.id, audit_service.client_ip(request), audit_service.user_agent(request)
    )
    set_session_cookie(response, sid)
    reset_rate_limit("login:account", email_key)
    db.commit()
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> Response:
    sid = request.cookies.get(settings.session_cookie_name)
    if sid:
        sess = session_service.get_session(sid)
        session_service.delete_session(sid)
        if sess:
            actor_id = uuid.UUID(sess["user_id"])
            audit_service.record(
                db, "user.logout", actor_user_id=actor_id,
                target_type="user", target_id=actor_id, request=request,
            )
            db.commit()
    clear_session_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.get("/session", response_model=SessionStateOut)
def session_state(user: User | None = Depends(get_optional_user)) -> SessionStateOut:
    """Public: who is signed in on this browser, if anyone (200 either way).
    Reveals nothing an anonymous caller does not already know (their own
    cookie's user, or nothing). Unverified accounts are returned too, so the
    SPA can route them to /verify-email; every other route still refuses
    them server-side."""
    if user is None:
        return SessionStateOut(authenticated=False)
    return SessionStateOut(authenticated=True, user=UserOut.model_validate(user))


@router.post("/verify-email", response_model=MessageOut)
def verify_email(
    payload: VerifyEmailIn, request: Request, db: Session = Depends(get_db)
) -> MessageOut:
    auth_service.verify_email(db, payload.token, request)
    db.commit()
    return MessageOut(message="Your email address has been verified. You can now sign in.")


@router.post(
    "/verify-email/resend",
    response_model=MessageOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def resend_verification(
    payload: ResendVerificationIn, request: Request, db: Session = Depends(get_db)
) -> MessageOut:
    limit, window = settings.rl_verify_resend_account
    enforce_rate_limit(
        "verify_resend:account", auth_service.normalize_email(payload.email), limit, window
    )
    auth_service.resend_verification(db, payload.email, request)
    db.commit()
    return MessageOut(
        message="If that account exists and is unverified, a new verification email is on its way."
    )


@router.post("/forgot-password", response_model=MessageOut, status_code=status.HTTP_202_ACCEPTED)
def forgot_password(
    payload: ForgotPasswordIn, request: Request, db: Session = Depends(get_db)
) -> MessageOut:
    ip_limit, ip_window = settings.rl_forgot_ip
    acct_limit, acct_window = settings.rl_forgot_account
    email_key = auth_service.normalize_email(payload.email)
    enforce_rate_limit("forgot:ip", _client_key(request), ip_limit, ip_window)
    enforce_rate_limit("forgot:account", email_key, acct_limit, acct_window)

    auth_service.request_password_reset(db, payload.email, request)
    db.commit()
    return MessageOut(
        message="If an account exists for that email, a reset code has been sent."
    )


@router.post("/change-password", response_model=UserOut)
def change_password(
    payload: ChangePasswordIn,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    limit, window = settings.rl_change_password_account
    enforce_rate_limit("change_password:account", str(user.id), limit, window)

    if not verify_password(user.password_hash, payload.current_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
        )

    user.password_hash = hash_password(payload.new_password)
    user.password_changed_at = utcnow()
    db.flush()

    # Bumping password_changed_at invalidates every session issued before now
    # -- including this request's own session -- so issue a fresh one before
    # the response goes out, mirroring login()'s session creation + cookie.
    old_sid = request.cookies.get(settings.session_cookie_name)
    if old_sid:
        session_service.delete_session(old_sid)
    new_sid = session_service.create_session(
        user.id, audit_service.client_ip(request), audit_service.user_agent(request)
    )
    set_session_cookie(response, new_sid)

    audit_service.record(
        db, "user.password_changed", actor_user_id=user.id,
        target_type="user", target_id=user.id, request=request,
    )
    notification_service.notify(
        db, user.id,
        type="account.password_changed",
        title="Your password was changed",
        body="If this wasn't you, please contact support immediately.",
    )
    db.commit()
    return user


@router.post("/reset-password", response_model=MessageOut)
def reset_password(
    payload: ResetPasswordIn, request: Request, db: Session = Depends(get_db)
) -> MessageOut:
    limit, window = settings.rl_reset_account
    enforce_rate_limit(
        "reset:account", auth_service.normalize_email(payload.email), limit, window
    )
    auth_service.reset_password(
        db, payload.email, payload.otp, payload.new_password, request
    )
    db.commit()
    return MessageOut(
        message="Your password has been updated. Please sign in with your new password."
    )
