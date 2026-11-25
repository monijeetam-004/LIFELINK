"""phase26: donation idempotency key + payload hash

Revision ID: b8c9d0e1f2a3
Revises: c3d4e5f6a7b8
Create Date: 2026-09-17

Mirrors the Phase 19 blood_request idempotency design
(`uq_blood_request_idempotency_key`) applied to `donations`: two additive,
nullable columns plus a unique constraint scoped to the actor
(`donor_user_id`), not global -- two different donors may coincidentally
pick the same client-generated key without colliding, and Postgres treats
every NULL in a unique constraint as distinct, so the many pre-existing
rows with no idempotency key at all are unaffected.

Closes the real, evidenced gap documented in
LIFELINK_PHASE_25_TESTING_AUDIT.md / LIFELINK_PHASE_25_TESTING_REPORT.md
(Bugs Found #2): `POST /donors/me/donations` had no idempotency protection
at all, so two concurrent identical submissions created two donation rows.
See LIFELINK_PHASE_26_DONATION_FRONTEND_AUDIT.md for the full design.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "donations",
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "donations",
        sa.Column("idempotency_payload_hash", sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_donation_idempotency_key",
        "donations",
        ["donor_user_id", "idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_donation_idempotency_key", "donations", type_="unique")
    op.drop_column("donations", "idempotency_payload_hash")
    op.drop_column("donations", "idempotency_key")
