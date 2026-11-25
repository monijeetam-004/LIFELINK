"""phase12: emergency/SOS engine -- deadline, quantity, idempotency,
target facility, and versioned resource pathways

Purely additive:
  * `emergencies` gets four new nullable columns (`units_needed`,
    `deadline`, `idempotency_key`, `target_facility_id`) plus a unique
    constraint on (created_by_user_id, idempotency_key) -- Postgres
    treats NULL as distinct from NULL in a UNIQUE constraint, so this
    only ever rejects a genuine same-actor, same-key replay and never
    affects the ~1000+ existing emergency rows that have no key at all.
  * a new `emergency_pathways` table: one row per persisted, *versioned*
    resource-pathway selection for an emergency (see
    LIFELINK_PHASE_12_REPORT.md and app/models/emergency_pathway.py for
    why this is a new table rather than a change to the pre-existing
    `graph_pathway_logs` query-log table, which is untouched here).
  * `hospital_inventory_logs` gets one new nullable `emergency_id` column.
    The pre-existing `request_id` column is FK'd specifically to
    `blood_requests.id` -- it cannot carry an Emergency's id without
    violating that constraint, so emergency-triggered reservations are
    traced through this new, parallel, equally real column instead
    (app.services.inventory_service gains an optional `emergency_id`
    kwarg on reserve_stock/release_reservation/allocate_stock; nothing
    about its existing `request_id`/BloodRequest behavior changes).

No existing table's data is altered, no existing column is dropped or
retyped, no existing row is touched.

Revision ID: d4e5f6a7b8c9
Revises: ca362e8cb9cc
Create Date: 2026-09-12
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "ca362e8cb9cc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- emergencies: four new nullable columns ----
    op.add_column("emergencies", sa.Column("units_needed", sa.Integer(), nullable=True))
    op.add_column(
        "emergencies", sa.Column("deadline", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "emergencies", sa.Column("idempotency_key", sa.String(length=200), nullable=True)
    )
    op.add_column(
        "emergencies",
        sa.Column(
            "target_facility_id",
            sa.Uuid(),
            sa.ForeignKey("hospital_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        "uq_emergency_idempotency_key", "emergencies", ["created_by_user_id", "idempotency_key"]
    )

    # ---- emergency_pathways: new enum + new table ----
    # Let `create_table` create the enum type itself (its default
    # create_type=True), rather than pre-creating it separately -- doing
    # both in the same transaction double-creates it and fails.
    emergency_pathway_status = sa.Enum(
        "ACTIVE", "SUPERSEDED", name="emergency_pathway_status"
    )

    op.create_table(
        "emergency_pathways",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "emergency_id",
            sa.Uuid(),
            sa.ForeignKey("emergencies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "status", emergency_pathway_status, nullable=False, server_default="ACTIVE"
        ),
        sa.Column(
            "origin_facility_id",
            sa.Uuid(),
            sa.ForeignKey("hospital_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "selected_facility_id",
            sa.Uuid(),
            sa.ForeignKey("hospital_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "pathway_source",
            postgresql.ENUM(
                "GRAPH", "HAVERSINE_FALLBACK", name="graph_pathway_source", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("distance_km", sa.Float(), nullable=True),
        sa.Column("transit_minutes", sa.Integer(), nullable=True),
        sa.Column("hops", sa.Integer(), nullable=True),
        sa.Column(
            "graph_available", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("reason", sa.String(length=300), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_reason", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_emergency_pathways_emergency_id", "emergency_pathways", ["emergency_id"]
    )
    op.create_index(
        "ix_emergency_pathways_status", "emergency_pathways", ["status"]
    )
    op.create_index(
        "uq_emergency_pathway_one_active",
        "emergency_pathways",
        ["emergency_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )

    # ---- hospital_inventory_logs: parallel emergency_id trace column ----
    op.add_column(
        "hospital_inventory_logs",
        sa.Column(
            "emergency_id",
            sa.Uuid(),
            sa.ForeignKey("emergencies.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_hospital_inventory_logs_emergency_id",
        "hospital_inventory_logs",
        ["emergency_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_hospital_inventory_logs_emergency_id", table_name="hospital_inventory_logs"
    )
    op.drop_column("hospital_inventory_logs", "emergency_id")

    op.drop_index("uq_emergency_pathway_one_active", table_name="emergency_pathways")
    op.drop_index("ix_emergency_pathways_status", table_name="emergency_pathways")
    op.drop_index("ix_emergency_pathways_emergency_id", table_name="emergency_pathways")
    op.drop_table("emergency_pathways")
    sa.Enum(name="emergency_pathway_status").drop(op.get_bind(), checkfirst=True)

    op.drop_constraint("uq_emergency_idempotency_key", "emergencies", type_="unique")
    op.drop_column("emergencies", "target_facility_id")
    op.drop_column("emergencies", "idempotency_key")
    op.drop_column("emergencies", "deadline")
    op.drop_column("emergencies", "units_needed")
