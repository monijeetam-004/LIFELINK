"""phase13: family link reason + responded_at

Revision ID: f4a5b6c7d8e9
Revises: d4e5f6a7b8c9
Create Date: 2026-09-12
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f4a5b6c7d8e9"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "family_links",
        sa.Column("reason", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "family_links",
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("family_links", "responded_at")
    op.drop_column("family_links", "reason")
