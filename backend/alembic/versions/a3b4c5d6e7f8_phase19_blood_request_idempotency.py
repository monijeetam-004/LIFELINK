"""phase19: blood request idempotency key + payload hash

Revision ID: a3b4c5d6e7f8
Revises: f4a5b6c7d8e9
Create Date: 2026-09-15

Mirrors the Phase 12 emergency-creation idempotency design
(`uq_emergency_idempotency_key`) applied to `blood_requests`: two additive,
nullable columns plus a unique constraint scoped to the actor
(`requester_user_id`), not global -- two different users may coincidentally
pick the same client-generated key without colliding, and Postgres treats
every NULL in a unique constraint as distinct, so the many pre-existing
rows with no idempotency key at all are unaffected.

`idempotency_payload_hash` is new relative to the emergency precedent: the
emergency path never detects a same-key/different-payload replay, but
Phase 19 explicitly requires that mismatch to be rejected rather than
silently served from the first payload -- see
LIFELINK_PHASE_19_IDEMPOTENCY_AUDIT.md for the full rationale. The column
stores a SHA-256 hex digest of the semantically meaningful request fields,
never the raw payload -- nothing sensitive is duplicated by this column
beyond what `blood_requests` already stores in its own columns.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a3b4c5d6e7f8"
down_revision: str | None = "f4a5b6c7d8e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "blood_requests",
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "blood_requests",
        sa.Column("idempotency_payload_hash", sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_blood_request_idempotency_key",
        "blood_requests",
        ["requester_user_id", "idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_blood_request_idempotency_key", "blood_requests", type_="unique")
    op.drop_column("blood_requests", "idempotency_payload_hash")
    op.drop_column("blood_requests", "idempotency_key")
