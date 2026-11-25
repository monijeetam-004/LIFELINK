"""E2E only -- issue a fresh email-verification token for an account and
print the RAW token, standing in for "open the verification email".

    python -m scripts.e2e_issue_verification_token someone@example.com

Production hardening made email verification mandatory server-side, so an
E2E spec that registers a brand-new account must verify it before acting.
The spec then calls the REAL `POST /auth/verify-email` with this token, so
the verification endpoint itself is exercised -- this script only replaces
reading the mailbox (no SMTP/Mailpit in the E2E environment). It uses the
same issuing code as "resend" (previous tokens are superseded) and needs the
server's SECRET_KEY and DATABASE_URL. Refuses ENVIRONMENT=production.
"""

from __future__ import annotations

import sys

from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.user import User
from app.services.auth_service import _issue_verification_token


def main(argv: list[str]) -> int:
    if settings.is_production:
        print("refusing: ENVIRONMENT=production", file=sys.stderr)
        return 1
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == argv[0].strip().lower()))
        if user is None:
            print(f"no such account: {argv[0]}", file=sys.stderr)
            return 1
        raw = _issue_verification_token(db, user)
        db.commit()
        print(raw)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
