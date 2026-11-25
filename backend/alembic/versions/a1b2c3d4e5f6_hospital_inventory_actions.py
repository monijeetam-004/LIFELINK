"""Phase 5 hospital portal: inventory reservations/audit log + hospital
request ALLOCATED/FULFILLED handling states.

Revision ID: a1b2c3d4e5f6
Revises: f3a4b5c6d7e8
Create Date: 2026-09-11
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f3a4b5c6d7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INVENTORY_ACTION = ("ADD", "RESERVE", "ALLOCATE", "RELEASE", "EXPIRE", "RECONCILE")
# blood_type is an existing shared enum type (created by 0002_phase2);
# create_type=False stops Alembic from re-issuing CREATE TYPE for it (see
# 4a9d4f2ed50b_phase8_graph_engine_schema_transport_.py for the same
# pattern -- an empty-members Enum(create_type=False) still emits a bare
# `CREATE TYPE blood_type AS ENUM ()` on table creation, so the member
# list has to be given in full even though create_type=False is set).
_BLOOD_TYPE = postgresql.ENUM(
    "A_POS", "A_NEG", "B_POS", "B_NEG", "AB_POS", "AB_NEG", "O_POS", "O_NEG",
    name="blood_type", create_type=False,
)


def upgrade() -> None:
    # New enum values on an existing type must be added outside a
    # transaction block (see e2f3a4b5c6d7_phase4_donor_portal.py for the
    # same pattern).
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE hospital_response_status ADD VALUE IF NOT EXISTS 'ALLOCATED'")
        op.execute("ALTER TYPE hospital_response_status ADD VALUE IF NOT EXISTS 'FULFILLED'")

    op.add_column(
        "hospital_inventory",
        sa.Column("reserved_units", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_hospital_inventory_units_non_negative", "hospital_inventory", "units >= 0"
    )
    op.create_check_constraint(
        "ck_hospital_inventory_reserved_non_negative",
        "hospital_inventory",
        "reserved_units >= 0",
    )
    op.create_check_constraint(
        "ck_hospital_inventory_reserved_le_units",
        "hospital_inventory",
        "reserved_units <= units",
    )

    # Brand-new enum type on a brand-new table: declared inline so
    # op.create_table's own DDL compiler creates it exactly once (see
    # f3a4b5c6d7e8_sms_notifications.py for why explicit pre-create isn't
    # used here).
    op.create_table(
        "hospital_inventory_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "hospital_id",
            sa.Uuid(),
            sa.ForeignKey("hospital_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("blood_type", _BLOOD_TYPE, nullable=False),
        sa.Column(
            "action",
            sa.Enum(*_INVENTORY_ACTION, name="hospital_inventory_action"),
            nullable=False,
        ),
        sa.Column("delta_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delta_reserved", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("resulting_units", sa.Integer(), nullable=False),
        sa.Column("resulting_reserved", sa.Integer(), nullable=False),
        sa.Column(
            "actor_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "request_id",
            sa.Uuid(),
            sa.ForeignKey("blood_requests.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("note", sa.String(300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_hospital_inventory_logs_hospital_id", "hospital_inventory_logs", ["hospital_id"]
    )
    op.create_index(
        "ix_hospital_inventory_logs_request_id", "hospital_inventory_logs", ["request_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_hospital_inventory_logs_request_id", table_name="hospital_inventory_logs")
    op.drop_index("ix_hospital_inventory_logs_hospital_id", table_name="hospital_inventory_logs")
    op.drop_table("hospital_inventory_logs")
    sa.Enum(name="hospital_inventory_action").drop(op.get_bind(), checkfirst=True)

    op.drop_constraint(
        "ck_hospital_inventory_reserved_le_units", "hospital_inventory", type_="check"
    )
    op.drop_constraint(
        "ck_hospital_inventory_reserved_non_negative", "hospital_inventory", type_="check"
    )
    op.drop_constraint(
        "ck_hospital_inventory_units_non_negative", "hospital_inventory", type_="check"
    )
    op.drop_column("hospital_inventory", "reserved_units")

    # Postgres cannot drop a single enum value -- ALLOCATED/FULFILLED stay
    # defined on hospital_response_status after downgrade, exactly like
    # donor_response_status.NOT_NOW does after e2f3a4b5c6d7's downgrade.
    # Harmless: nothing on the downgraded schema can write them.
