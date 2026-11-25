from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuditLog(Base):
    """Append-only, tamper-EVIDENT audit trail (Phase E). Rows are written
    only via `app.services.audit_service.record` and are sealed into a
    SHA-256 hash chain shortly after their transaction commits -- see
    app.core.audit_chain for exactly what that does and does not prove.
    Not immutable: an append-only trigger (app.core.audit_guard) refuses
    UPDATE/DELETE/TRUNCATE of entries, but the table owner can disable it;
    the chain makes such changes detectable by `verify_chain`, and signed
    checkpoints outside the database detect a full recompute."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        # The sealer's work queue: only rows not yet chained. Partial, so it
        # stays tiny however large the table grows.
        Index(
            "ix_audit_logs_unsealed", "created_at", "id",
            postgresql_where=text("chain_seq IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    # Phase E -- hash chain. All four are NULL until the row is sealed
    # (normally milliseconds after its transaction commits); business
    # transactions never write them. `chain_seq` is unique: 1, 2, 3, ...
    # in seal order, with no gaps in an intact chain.
    chain_seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True, unique=True)
    prev_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entry_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hash_version: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    sealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# Hardening: install the append-only guard whenever the table is created by
# `metadata.create_all` (test databases). Production schemas get it from
# migration c9d0e1f2a3b4. See app.core.audit_guard.
def _install_append_only_guard(target, connection, **kw) -> None:
    from app.core import audit_guard

    if connection.dialect.name != "postgresql":
        return
    for stmt in audit_guard.CREATE_SQL:
        connection.execute(text(stmt))


event.listen(AuditLog.__table__, "after_create", _install_append_only_guard)
