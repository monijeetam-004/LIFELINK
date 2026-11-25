"""Phase 18 Playwright E2E seed script.

Creates a small, fixed set of already-verified users directly in whatever
database DATABASE_URL points at (the real `lifelink` dev database when run
for E2E, never the pytest suite's `lifelink_test`) -- exactly the same
`seed_verified_user` helper the backend's own pytest suite has used since
early phases, just invoked as a standalone script so the Node/Playwright
side has known credentials to log in with through the real UI. This does
NOT fabricate verified state the app wouldn't otherwise reach through
normal use -- it's the same shortcut the test suite takes to avoid
re-testing the (already covered) email-verification flow on every run.

Idempotent: safe to run multiple times against the same database (re-seeds
by deleting any row with the same email first).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.core.config import settings
from app.core.security import hash_password
from app.core.time import utcnow
from app.db.session import SessionLocal
from app.models.user import User, UserRole

PASSWORD = "e2e decent passphrase 2026"

USERS = [
    ("e2e.donor@example.com", "DONOR", "E2E Donor"),
    ("e2e.patient@example.com", "PATIENT", "E2E Patient"),
    ("e2e.hospital@example.com", "HOSPITAL", "E2E Hospital"),
    ("e2e.family@example.com", "FAMILY", "E2E Family"),
    ("e2e.admin@example.com", "ADMIN", "E2E Admin"),
]


def main() -> None:
    # Phase 28: same fail-closed production guard as scripts/seed_demo_data.py
    # -- this script both DELETES and inserts users with a single shared,
    # publicly-visible-in-this-file password under whatever DATABASE_URL
    # happens to be set, which must never be able to touch a real
    # production database, accidentally or otherwise.
    if settings.is_production and os.environ.get("SEED_DEMO_ALLOW_PROD") != "yes":
        print(
            "Refusing to run the E2E seed: ENVIRONMENT=production and "
            "SEED_DEMO_ALLOW_PROD is not 'yes'. This resets (or creates) "
            "5 fixed-password accounts and must never target a real "
            "production database.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    db = SessionLocal()
    try:
        # Hardening: users referenced by sealed audit entries cannot be
        # hard-deleted (append-only audit guard), so existing fixture users
        # are reset in place instead of deleted and recreated. CI and the
        # local E2E script always start from a fresh database anyway.
        for email, role, full_name in USERS:
            user = db.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(email=email)
                db.add(user)
            user.password_hash = hash_password(PASSWORD)
            user.full_name = full_name
            user.role = UserRole(role)
            user.is_email_verified = True
            user.is_active = True
            user.password_changed_at = utcnow()
            user.suspended_reason = None
            user.suspended_at = None
            user.suspended_by_user_id = None
        db.commit()
        print(f"Seeded {len(USERS)} E2E users.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
