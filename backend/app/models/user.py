from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class UserRole(str, enum.Enum):
    PATIENT = "PATIENT"
    FAMILY = "FAMILY"
    DONOR = "DONOR"
    HOSPITAL = "HOSPITAL"
    ADMIN = "ADMIN"


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", native_enum=True), nullable=False
    )
    is_email_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Admin suspend/reactivate. suspended_reason is required by the API whenever
    # is_active is set False, and is cleared on reactivation.
    suspended_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suspended_by_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.email} role={self.role.value}>"
