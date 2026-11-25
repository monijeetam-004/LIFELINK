"""Phase F -- the real-time event outbox.

One row per "resource X changed" fact, written in the SAME transaction as
the change itself (see app.services.realtime_service), so an event can
never describe a change that was rolled back, and a committed change can
never lose its event. Rows carry no PII: just a type, a resource type and
id. Clients treat an event as a hint to refetch that resource through the
normal, authorized API -- the database stays the source of truth.

`seq`, `audience_*` and `published_at` are filled in AFTER commit by the
publisher, under an advisory lock, in commit order (same pattern as the
Phase E audit-chain sealer): `seq` is therefore gap-free in publish order,
which is what makes `Last-Event-ID` replay on reconnect correct.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, Uuid, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RealtimeEvent(Base):
    __tablename__ = "realtime_events"
    __table_args__ = (
        Index(
            "ix_realtime_events_unpublished", "created_at", "id",
            postgresql_where=text("seq IS NULL"),
        ),
        Index("ix_realtime_events_audience_users", "audience_user_ids", postgresql_using="gin"),
        # Phase K: the publisher's "sequenced but not yet published" read
        # (ORDER BY seq) walked the unique seq index over every retained,
        # already-published row (31,655 rows filtered per commit at 250k
        # events). This partial index holds only the in-flight rows.
        # Phase K: retention delete (`created_at < cutoff`) was a full
        # sequential scan (47 ms at 251k rows).
        Index("ix_realtime_events_created_at", "created_at"),
        Index(
            "ix_realtime_events_pending_publish", "seq",
            postgresql_where=text("seq IS NOT NULL AND published_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )
    # Assigned by the publisher after commit; unique, gap-free, publish order.
    seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True, unique=True)
    # Computed by the publisher from the same visibility rules the REST API
    # enforces (app.services.realtime_audience). Stored so a reconnecting
    # client's replay is filtered exactly like the live stream was.
    audience_user_ids: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(Uuid), nullable=True
    )
    audience_roles: Mapped[list[str] | None] = mapped_column(ARRAY(String(20)), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
