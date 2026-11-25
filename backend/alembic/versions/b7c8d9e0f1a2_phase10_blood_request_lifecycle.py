"""phase10: blood request lifecycle -- cancelled_at / expired_at timestamps

`blood_request_status` already had all five states (PENDING, MATCHING,
FULFILLED, CANCELLED, EXPIRED) since the original 0002 migration -- no enum
change needed here. This migration only adds the two timestamp columns that
formalize CANCELLED / EXPIRED the same way `fulfilled_at` already formalizes
FULFILLED.

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-09-12
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7c8d9e0f1a2"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "blood_requests",
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "blood_requests",
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("blood_requests", "expired_at")
    op.drop_column("blood_requests", "cancelled_at")
