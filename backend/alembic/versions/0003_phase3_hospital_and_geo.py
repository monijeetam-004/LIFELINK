"""phase 3: hospital profile/inventory/responses, donor + request geo

Revision ID: 0003_phase3
Revises: 0002_phase2
Create Date: 2026-09-06
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_phase3"
down_revision: str | None = "0002_phase2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

blood_type = postgresql.ENUM(
    "A_POS", "A_NEG", "B_POS", "B_NEG", "AB_POS", "AB_NEG", "O_POS", "O_NEG",
    name="blood_type", create_type=False,
)
hospital_response_status = postgresql.ENUM(
    "ACKNOWLEDGED", "PREPARING", "DISPATCHED", "DECLINED",
    name="hospital_response_status", create_type=False,
)


def _ts() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )


def upgrade() -> None:
    hospital_response_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "hospital_profiles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("license_no", sa.String(80), nullable=True),
        sa.Column("phone", sa.String(40), nullable=True),
        sa.Column("address", sa.String(300), nullable=True),
        sa.Column("city", sa.String(120), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        *_ts(),
    )
    op.create_index("ix_hospital_profiles_user_id", "hospital_profiles", ["user_id"], unique=True)
    op.create_index("ix_hospital_profiles_city", "hospital_profiles", ["city"])

    op.create_table(
        "hospital_inventory",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("hospital_id", sa.Uuid(),
                  sa.ForeignKey("hospital_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("blood_type", blood_type, nullable=False),
        sa.Column("units", sa.Integer(), nullable=False, server_default=sa.text("0")),
        *_ts(),
        sa.UniqueConstraint("hospital_id", "blood_type", name="uq_hospital_inventory_type"),
    )
    op.create_index("ix_hospital_inventory_hospital_id", "hospital_inventory", ["hospital_id"])

    op.create_table(
        "hospital_request_responses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("hospital_id", sa.Uuid(),
                  sa.ForeignKey("hospital_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_id", sa.Uuid(),
                  sa.ForeignKey("blood_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", hospital_response_status, nullable=False),
        sa.Column("note", sa.String(500), nullable=True),
        *_ts(),
        sa.UniqueConstraint("hospital_id", "request_id", name="uq_hospital_request_response"),
    )
    op.create_index("ix_hospital_request_responses_hospital_id",
                    "hospital_request_responses", ["hospital_id"])
    op.create_index("ix_hospital_request_responses_request_id",
                    "hospital_request_responses", ["request_id"])

    op.add_column("donor_profiles", sa.Column("latitude", sa.Float(), nullable=True))
    op.add_column("donor_profiles", sa.Column("longitude", sa.Float(), nullable=True))
    op.add_column("donor_profiles", sa.Column(
        "share_location", sa.Boolean(), nullable=False, server_default=sa.text("false")))

    op.add_column("blood_requests", sa.Column("latitude", sa.Float(), nullable=True))
    op.add_column("blood_requests", sa.Column("longitude", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("blood_requests", "longitude")
    op.drop_column("blood_requests", "latitude")
    op.drop_column("donor_profiles", "share_location")
    op.drop_column("donor_profiles", "longitude")
    op.drop_column("donor_profiles", "latitude")
    op.drop_table("hospital_request_responses")
    op.drop_table("hospital_inventory")
    op.drop_table("hospital_profiles")
    hospital_response_status.drop(op.get_bind(), checkfirst=True)
