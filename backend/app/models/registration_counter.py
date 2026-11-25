"""Phase 11 -- independent per-role self-registration counters.

One row per self-registerable role (PATIENT/FAMILY/DONOR/HOSPITAL -- see
`app.schemas.auth.REGISTERABLE_ROLES`), each capped independently at
`settings.max_accounts`. This is deliberately NOT a `SELECT COUNT(*) FROM
users WHERE role = ...` query: the dev/staging database already carries
~1000+ seeded/synthetic users (from `scripts/seed_demo_data.py`), and those
must never consume a slot in this new limit. A row here only ever changes
inside `app.services.auth_service.register()` -- the one and only code path
that increments it -- so it counts exactly what the spec asks it to count:
new accounts created through the public `POST /auth/register` endpoint
since this feature shipped, nothing else.

There is intentionally no row for ADMIN: `RegisterIn`'s validator already
rejects `role=ADMIN` before `register()` is ever reached, so no counter for
it is ever read or written.
"""
from __future__ import annotations

from sqlalchemy import Enum, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.user import UserRole


class RegistrationCounter(Base):
    __tablename__ = "registration_counters"

    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", native_enum=True), primary_key=True
    )
    count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RegistrationCounter {self.role.value}={self.count}>"
