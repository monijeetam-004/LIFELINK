from __future__ import annotations

import re
import uuid

from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.core.time import utcnow
from app.db.session import SessionLocal
from app.main import app
from app.models.user import User, UserRole

_PW = "a decent passphrase"


def seed_verified_user(
    email: str, password: str = _PW, role: str = "PATIENT", full_name: str = "Seed User"
) -> uuid.UUID:
    """Insert an already-verified user directly (bypasses self-registration rules)."""
    db = SessionLocal()
    try:
        user = User(
            email=email.lower(),
            password_hash=hash_password(password),
            full_name=full_name,
            role=UserRole(role),
            is_email_verified=True,
            is_active=True,
            password_changed_at=utcnow(),
        )
        db.add(user)
        db.commit()
        return user.id
    finally:
        db.close()


def auth_client(role: str = "PATIENT", email: str | None = None, name: str | None = None):
    """A fresh TestClient logged in as a freshly-seeded verified user of ``role``.

    Returns ``(client, email)``.
    """
    email = email or f"{role.lower()}-{uuid.uuid4().hex[:8]}@example.com"
    seed_verified_user(email, _PW, role, name or f"{role.title()} User")
    client = TestClient(app, base_url="http://testserver")
    client.headers["Origin"] = "http://localhost:5173"  # satisfies verify_origin
    resp = client.post("/auth/login", json={"email": email, "password": _PW})
    assert resp.status_code == 200, resp.text
    return client, email


def register(client: TestClient, **overrides) -> dict:
    payload = {
        "full_name": "Test User",
        "email": "user@example.com",
        "password": "correct horse battery",
        "role": "PATIENT",
    }
    payload.update(overrides)
    resp = client.post("/auth/register", json=payload)
    return resp


def extract_verification_token(mailbox: list[dict]) -> str:
    html = mailbox[-1]["html"]
    match = re.search(r"verify-email\?token=([A-Za-z0-9_\-]+)", html)
    assert match, f"no verification token in email: {html}"
    return match.group(1)


def extract_otp(mailbox: list[dict]) -> str:
    html = mailbox[-1]["html"]
    match = re.search(r">\s*(\d{6})\s*<", html)
    assert match, f"no OTP in email: {html}"
    return match.group(1)


def verify_user(client: TestClient, mailbox: list[dict]) -> None:
    token = extract_verification_token(mailbox)
    resp = client.post("/auth/verify-email", json={"token": token})
    assert resp.status_code == 200, resp.text


def verify_facility_of(email: str) -> uuid.UUID:
    """Mark the hospital profile owned by ``email`` as verified -- the state
    an operating facility is in after an admin approves its verification
    request (tests that exercise the approval flow itself use
    /admin/verifications instead). Only a verified facility can respond to
    an emergency or be a pathway source (gap elimination #7)."""
    from sqlalchemy import select

    from app.services.domain_service import get_or_create_hospital_profile

    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == email.lower()))
        hp = get_or_create_hospital_profile(db, user)
        hp.verified = True
        db.commit()
        return hp.id
    finally:
        db.close()


def verified_hospital_client(name: str | None = None):
    """``auth_client("HOSPITAL")`` whose facility is already verified."""
    client, email = auth_client("HOSPITAL", name=name)
    verify_facility_of(email)
    return client, email


def stock_units(facility_id, blood_type: str, units: int, component: str = "WHOLE_BLOOD",
                db=None) -> None:
    """Set a facility's on-hand stock of (blood_type, component) to exactly
    ``units`` through the real inventory service (a RECONCILE stock-take),
    so real per-unit BloodUnit rows exist -- the pathway engine counts
    units, never a bare aggregate number."""
    from app.models.enums import BloodComponent, BloodType
    from app.services import inventory_service

    own = db is None
    db = db or SessionLocal()
    try:
        inventory_service.reconcile_inventory(
            db, uuid.UUID(str(facility_id)), BloodType(blood_type), units,
            actor_user_id=None, note="test fixture stock-take",
            component=BloodComponent(component),
        )
        db.commit()
    finally:
        if own:
            db.close()
