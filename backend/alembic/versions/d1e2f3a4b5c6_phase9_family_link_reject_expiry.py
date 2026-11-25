"""phase9: family link REJECTED/EXPIRED statuses + expires_at

Revision ID: d1e2f3a4b5c6
Revises: 4a9d4f2ed50b
Create Date: 2026-09-11
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d1e2f3a4b5c6"
down_revision: str | None = "4a9d4f2ed50b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_LINK_STATES = ("REJECTED", "EXPIRED")
_BASE_LINK_STATES = ("PENDING", "ACCEPTED", "REVOKED")


def upgrade() -> None:
    # New enum values must be added outside a transaction block.
    with op.get_context().autocommit_block():
        for value in _NEW_LINK_STATES:
            op.execute(f"ALTER TYPE family_link_status ADD VALUE IF NOT EXISTS '{value}'")

    op.add_column(
        "family_links",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("family_links", "expires_at")

    # Rebuild family_link_status without the phase-9 values (fails if rows use them).
    op.execute("ALTER TYPE family_link_status RENAME TO family_link_status_old")
    op.execute(
        "CREATE TYPE family_link_status AS ENUM ("
        + ", ".join(f"'{v}'" for v in _BASE_LINK_STATES)
        + ")"
    )
    op.execute(
        "ALTER TABLE family_links ALTER COLUMN status TYPE family_link_status "
        "USING status::text::family_link_status"
    )
    op.execute("DROP TYPE family_link_status_old")
