from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from datetime import datetime

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utcnow
from app.db.session import SessionLocal
from app.models.user import User, UserRole
from app.services import audit_service, session_service


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


_CREDENTIALS_EXC = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
)


def _load_user_from_request(request: Request, db: Session, *, touch: bool = True) -> User | None:
    sid = request.cookies.get(settings.session_cookie_name)
    if not sid:
        return None
    sess = session_service.get_session(sid)
    if not sess:
        return None
    try:
        user = db.get(User, uuid.UUID(sess["user_id"]))
    except (KeyError, ValueError):
        return None
    if user is None or not user.is_active:
        return None
    issued_at = datetime.fromisoformat(sess["issued_at"])
    # Password changed after this session was issued -> session is stale.
    if user.password_changed_at and user.password_changed_at > issued_at:
        session_service.delete_session(sid)
        return None
    # Absolute lifetime cap, independent of the sliding idle timeout below:
    # even a session that is touched on every single request cannot live
    # past this, bounding how long a stolen session id stays usable.
    age_seconds = (utcnow() - issued_at).total_seconds()
    if age_seconds > settings.session_absolute_ttl_seconds:
        session_service.delete_session(sid)
        return None
    # Idle/sliding timeout: a request this far into an active session extends
    # it, so an actively-used session is never logged out mid-use -- only
    # `session_ttl_seconds` of inactivity does that (see session_service).
    # Phase F: the real-time stream re-validates its session periodically
    # with touch=False -- an open browser tab must not keep an otherwise
    # idle session alive forever just by holding an event stream open.
    if touch:
        session_service.touch_session(sid, user.id)
    request.state.session_id = sid
    return user


# Routes an account whose email address is not yet verified may still use:
# who am I, sign out, and change its password. Verifying (POST
# /auth/verify-email) and resending the link need no session at all.
_UNVERIFIED_ALLOWED_PATHS = frozenset({"/auth/me", "/auth/logout", "/auth/change-password"})


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = _load_user_from_request(request, db)
    if user is None:
        raise _CREDENTIALS_EXC
    # Hardening: email verification is enforced HERE, not only by the
    # frontend sending unverified users to /verify-email. Before this, an
    # unverified account could call every API route its role allowed.
    if not user.is_email_verified and request.url.path not in _UNVERIFIED_ALLOWED_PATHS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Verify your email address to continue",
            headers={"X-LifeLink-Reason": "email-unverified"},
        )
    return user


def get_optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    return _load_user_from_request(request, db)


def require_roles(*roles: UserRole) -> Callable[..., User]:
    """Server-side role gate. ``user.role`` always comes from the DB row the
    session resolved (see ``get_current_user``) -- never from anything the
    client sends -- so this cannot be bypassed by a manually constructed
    request claiming a different role.

    Every rejection is audited (``authz.role_denied``): unlike a plain 403
    to the caller, a rejected role check is itself a security-relevant event
    (e.g. a non-admin probing an admin endpoint, or a role probing an
    endpoint scoped to a different one) worth a durable record, the same way
    ``auth_service.authenticate`` already audits a failed login rather than
    only rejecting it.
    """
    allowed = set(roles)

    def _dep(
        request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)
    ) -> User:
        if user.role not in allowed:
            audit_service.record(
                db,
                "authz.role_denied",
                actor_user_id=user.id,
                target_type="route",
                request=request,
                meta={
                    "path": request.url.path,
                    "method": request.method,
                    "actor_role": user.role.value,
                    "required_roles": sorted(r.value for r in allowed),
                },
            )
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this resource",
            )
        return user

    return _dep


def verify_origin(request: Request) -> None:
    """Lightweight CSRF defense for cookie-authenticated state changes.

    Browsers always send ``Origin`` on cross-site POSTs; if present it must be
    in the allow-list. Non-browser clients (no Origin) are allowed through —
    they cannot be the target of a CSRF attack.
    """
    origin = request.headers.get("origin")
    if origin is None:
        return
    if origin not in settings.cors_origin_list:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Origin not allowed"
        )
