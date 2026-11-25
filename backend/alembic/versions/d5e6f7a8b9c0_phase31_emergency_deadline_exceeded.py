"""phase31: emergency deadline_exceeded_at (deadline gets a real consequence)

Revision ID: d5e6f7a8b9c0
Revises: b8c9d0e1f2a3
Create Date: 2026-09-18

Domain-correctness fix (LIFELINK_PHASE_31_DOMAIN_AUDIT.md, "Emergency
escalation logic"): `emergencies.deadline` was write-only from an
operational standpoint -- the only code that ever read it was
`_slack_seconds()` in app/api/routes/emergencies.py, purely to compute a
display value. Nothing in the system noticed, recorded, or told anyone
when a deadline actually passed while the emergency was still open.

This migration adds one new nullable column, `deadline_exceeded_at`, set
exactly once (by app.services.emergency_lifecycle.flag_deadline_exceeded_
if_needed) the first time any read or mutating request notices the
deadline has passed on a still-open emergency. It deliberately does NOT
touch `status` -- see that function's docstring and Section 9 of the
Phase 31 spec: a deadline must have a meaningful, durable, queryable
consequence (this column + an EmergencyEvent + an AuditLog entry + a
best-effort notification to the creator) without the system falsely
representing itself as making an autonomous clinical/operational
decision on a human's behalf.

Purely additive (new nullable column, no backfill, no data loss) --
verified upgrade -> downgrade -> upgrade-again against the real dev
database.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: str | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "emergencies",
        sa.Column("deadline_exceeded_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("emergencies", "deadline_exceeded_at")
