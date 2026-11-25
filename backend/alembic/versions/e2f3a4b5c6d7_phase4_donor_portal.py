"""phase4: donor portal - NOT_NOW response, availability_state, alert prefs

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-09-11
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e2f3a4b5c6d7"
down_revision: str | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AVAILABILITY_STATES = ("AVAILABLE", "UNAVAILABLE", "NOT_NOW")


def upgrade() -> None:
    # New enum value must be added outside a transaction block.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE donor_response_status ADD VALUE IF NOT EXISTS 'NOT_NOW'")

    donor_availability_state = sa.Enum(
        *_AVAILABILITY_STATES, name="donor_availability_state"
    )
    donor_availability_state.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "donor_profiles",
        sa.Column(
            "availability_state",
            donor_availability_state,
            nullable=False,
            server_default="AVAILABLE",
        ),
    )
    op.add_column(
        "donor_profiles",
        sa.Column(
            "alert_min_urgency",
            sa.Enum("ROUTINE", "URGENT", "CRITICAL", name="urgency"),
            nullable=False,
            server_default="ROUTINE",
        ),
    )
    op.add_column(
        "donor_profiles",
        sa.Column("alert_radius_km", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("donor_profiles", "alert_radius_km")
    op.drop_column("donor_profiles", "alert_min_urgency")
    op.drop_column("donor_profiles", "availability_state")

    op.execute("DROP TYPE donor_availability_state")

    # NOT_NOW on donor_response_status is left in place -- Postgres can't drop
    # a single enum value, and rebuilding the type (as the phase-9 migration
    # does for family_link_status) would fail here if any DonorResponse rows
    # already use NOT_NOW, which is the expected state after this phase ships.
