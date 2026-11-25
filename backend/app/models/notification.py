from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    # Phase 24: these two composite indexes mirror
    # alembic/versions/c3d4e5f6a7b8_phase24_notifications_composite_index.py
    # exactly (name-for-name, same columns/ops/predicate) so that
    # `Base.metadata.create_all` (used by the test suite) and the Alembic
    # migration (used by real dev/prod databases) produce an identical
    # schema. See that migration's docstring for the EXPLAIN-ANALYZE
    # evidence behind these two specific shapes -- a plain `index=True` on
    # a single column can't express either (both are composite; the second
    # is also partial), so they're declared here as explicit `Index(...)`
    # table args instead.
    __table_args__ = (
        Index(
            "ix_notifications_user_created",
            "user_id",
            text("created_at DESC"),
        ),
        Index(
            "ix_notifications_user_unread_created",
            "user_id",
            text("created_at DESC"),
            postgresql_where=text("read_at IS NULL"),
        ),
    )
