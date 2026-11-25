"""phase 5: emergency lifecycle states + responder table

Revision ID: 0004_phase5
Revises: 0003_phase3
Create Date: 2026-09-06
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004_phase5"
down_revision: str | None = "0003_phase3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

user_role = postgresql.ENUM(
    "PATIENT", "FAMILY", "DONOR", "HOSPITAL", "ADMIN",
    name="user_role", create_type=False,
)
donor_response_status = postgresql.ENUM(
    "ACCEPTED", "DECLINED", name="donor_response_status", create_type=False,
)

_NEW_EMERGENCY_STATES = ("MATCHING", "ASSISTANCE_FOUND")
_BASE_EMERGENCY_STATES = ("ACTIVE", "ACKNOWLEDGED", "RESOLVED", "CANCELLED")


def upgrade() -> None:
    # New enum values must be added outside a transaction block.
    with op.get_context().autocommit_block():
        for value in _NEW_EMERGENCY_STATES:
            op.execute(f"ALTER TYPE emergency_status ADD VALUE IF NOT EXISTS '{value}'")

    op.create_table(
        "emergency_responses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("emergency_id", sa.Uuid(),
                  sa.ForeignKey("emergencies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("responder_user_id", sa.Uuid(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("responder_role", user_role, nullable=False),
        sa.Column("status", donor_response_status, nullable=False),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("emergency_id", "responder_user_id",
                            name="uq_emergency_response_pair"),
    )
    op.create_index("ix_emergency_responses_emergency_id",
                    "emergency_responses", ["emergency_id"])
    op.create_index("ix_emergency_responses_responder_user_id",
                    "emergency_responses", ["responder_user_id"])


def downgrade() -> None:
    op.drop_table("emergency_responses")

    # Rebuild emergency_status without the phase-5 values (fails if rows use them).
    op.execute("ALTER TYPE emergency_status RENAME TO emergency_status_old")
    op.execute(
        "CREATE TYPE emergency_status AS ENUM ("
        + ", ".join(f"'{v}'" for v in _BASE_EMERGENCY_STATES)
        + ")"
    )
    op.execute("ALTER TABLE emergencies ALTER COLUMN status DROP DEFAULT")
    op.execute(
        "ALTER TABLE emergencies ALTER COLUMN status TYPE emergency_status "
        "USING status::text::emergency_status"
    )
    op.execute("ALTER TABLE emergencies ALTER COLUMN status SET DEFAULT 'ACTIVE'")
    op.execute("DROP TYPE emergency_status_old")
