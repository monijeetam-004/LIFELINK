"""phase 6: donation certificates

Revision ID: 0005_phase6
Revises: 0004_phase5
Create Date: 2026-09-06
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_phase6"
down_revision: str | None = "0004_phase5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "certificates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("donation_id", sa.Uuid(),
                  sa.ForeignKey("donations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("donor_user_id", sa.Uuid(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("serial", sa.String(40), nullable=False),
        sa.Column("verify_code", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(400), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("donation_id", name="uq_certificates_donation"),
        sa.UniqueConstraint("serial", name="uq_certificates_serial"),
        sa.UniqueConstraint("verify_code", name="uq_certificates_verify_code"),
    )
    op.create_index("ix_certificates_donation_id", "certificates", ["donation_id"])
    op.create_index("ix_certificates_donor_user_id", "certificates", ["donor_user_id"])
    op.create_index("ix_certificates_verify_code", "certificates", ["verify_code"])


def downgrade() -> None:
    op.drop_table("certificates")
