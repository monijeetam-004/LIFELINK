"""phase D: shipment / chain-of-custody lifecycle

Adds the shipments / shipment_items / shipment_events tables, a new
BloodUnitProvenance value (TRANSFERRED_IN), three new nullable BloodUnit
columns (reserved_for_shipment_id, source_shipment_id, source_unit_id),
and a new nullable shipment_id column on hospital_inventory_logs.

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-10-05
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c2d3e4f5a6b7"
down_revision: str | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _shipment_code() -> str:
    return f"LL-SHIP-{uuid.uuid4().hex[:8].upper()}"


def upgrade() -> None:

    # New enum on a NEW table -- op.create_table's default create_type=True
    # will auto-create this one (the Phase 34 precedent), so no explicit
    # .create() call is needed here, unlike the Phase 35 migration's
    # blood_component (which was added to an EXISTING table via
    # add_column, where auto-create does NOT happen).
    shipment_status = sa.Enum(
        "CREATED", "DISPATCHED", "IN_TRANSIT", "ARRIVED", "RECEIVED", "CANCELLED", "FAILED",
        name="shipment_status",
    )

    op.create_table(
        "shipments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "shipment_code", sa.String(length=40), nullable=False,
        ),
        sa.Column(
            "origin_facility_id", sa.Uuid(),
            sa.ForeignKey("hospital_profiles.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "destination_facility_id", sa.Uuid(),
            sa.ForeignKey("hospital_profiles.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "transport_route_id", sa.Uuid(),
            sa.ForeignKey("transport_routes.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "status", shipment_status, nullable=False, server_default="CREATED",
        ),
        sa.Column(
            "created_by_user_id", sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("in_transit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("arrived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("estimated_arrival_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delayed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delay_reason", sa.String(length=500), nullable=True),
        sa.Column("cancel_reason", sa.String(length=500), nullable=True),
        sa.Column("failure_reason", sa.String(length=500), nullable=True),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column("idempotency_payload_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("shipment_code", name="uq_shipments_shipment_code"),
        sa.UniqueConstraint(
            "created_by_user_id", "idempotency_key", name="uq_shipment_idempotency_key"
        ),
    )
    op.create_index("ix_shipments_origin_facility_id", "shipments", ["origin_facility_id"])
    op.create_index(
        "ix_shipments_destination_facility_id", "shipments", ["destination_facility_id"]
    )
    op.create_index("ix_shipments_transport_route_id", "shipments", ["transport_route_id"])
    op.create_index("ix_shipments_status", "shipments", ["status"])
    op.create_index("ix_shipments_origin_status", "shipments", ["origin_facility_id", "status"])
    op.create_index(
        "ix_shipments_destination_status", "shipments", ["destination_facility_id", "status"]
    )

    op.create_table(
        "shipment_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "shipment_id", sa.Uuid(),
            sa.ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "blood_unit_id", sa.Uuid(), sa.ForeignKey("blood_units.id"), nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_shipment_items_shipment_id", "shipment_items", ["shipment_id"])
    op.create_index("ix_shipment_items_blood_unit_id", "shipment_items", ["blood_unit_id"])
    # Invariant 23 (a BloodUnit belongs to at most one ACTIVE shipment),
    # enforced at the DB level via a partial unique index -- not
    # expressible as a plain SQLAlchemy UniqueConstraint (those can't
    # filter by WHERE), so it's created directly here.
    op.create_index(
        "uq_shipment_items_active_unit",
        "shipment_items",
        ["blood_unit_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "shipment_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "shipment_id", sa.Uuid(),
            sa.ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "actor_user_id", sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("event_type", sa.String(length=60), nullable=False),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_shipment_events_shipment_id", "shipment_events", ["shipment_id"])
    op.create_index("ix_shipment_events_created_at", "shipment_events", ["created_at"])

    # --------------------------------------------------------------------- #
    # BloodUnitProvenance.TRANSFERRED_IN -- a new VALUE on the EXISTING
    # blood_unit_provenance enum type (not a new type), via ALTER TYPE ...
    # ADD VALUE. PostgreSQL 12+ (this project runs PG16) allows this inside
    # a transaction and allows using the new value later in the SAME
    # migration run (a real, verified-by-running-it fact for this PG
    # version -- older Postgres required a separate transaction before the
    # new value could be referenced; not a concern here).
    # --------------------------------------------------------------------- #
    op.execute(sa.text("ALTER TYPE blood_unit_provenance ADD VALUE IF NOT EXISTS 'TRANSFERRED_IN'"))

    # --------------------------------------------------------------------- #
    # blood_units: three new nullable columns (no backfill needed -- every
    # pre-existing row honestly has no shipment involvement at all).
    # --------------------------------------------------------------------- #
    op.add_column(
        "blood_units",
        sa.Column(
            "reserved_for_shipment_id", sa.Uuid(),
            sa.ForeignKey("shipments.id", ondelete="SET NULL"), nullable=True,
        ),
    )
    op.create_index(
        "ix_blood_units_reserved_for_shipment_id", "blood_units", ["reserved_for_shipment_id"]
    )
    op.add_column(
        "blood_units",
        sa.Column(
            "source_shipment_id", sa.Uuid(),
            sa.ForeignKey("shipments.id", ondelete="SET NULL"), nullable=True,
        ),
    )
    op.create_index(
        "ix_blood_units_source_shipment_id", "blood_units", ["source_shipment_id"]
    )
    op.add_column(
        "blood_units",
        sa.Column(
            "source_unit_id", sa.Uuid(),
            sa.ForeignKey("blood_units.id", ondelete="SET NULL"), nullable=True,
        ),
    )
    op.create_index("ix_blood_units_source_unit_id", "blood_units", ["source_unit_id"])

    # --------------------------------------------------------------------- #
    # hospital_inventory_logs: shipment_id, the third parallel trace
    # column alongside the existing request_id/emergency_id.
    # --------------------------------------------------------------------- #
    op.add_column(
        "hospital_inventory_logs",
        sa.Column(
            "shipment_id", sa.Uuid(),
            sa.ForeignKey("shipments.id", ondelete="SET NULL"), nullable=True,
        ),
    )
    op.create_index(
        "ix_hospital_inventory_logs_shipment_id", "hospital_inventory_logs", ["shipment_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_hospital_inventory_logs_shipment_id", table_name="hospital_inventory_logs")
    op.drop_column("hospital_inventory_logs", "shipment_id")

    op.drop_index("ix_blood_units_source_unit_id", table_name="blood_units")
    op.drop_column("blood_units", "source_unit_id")
    op.drop_index("ix_blood_units_source_shipment_id", table_name="blood_units")
    op.drop_column("blood_units", "source_shipment_id")
    op.drop_index("ix_blood_units_reserved_for_shipment_id", table_name="blood_units")
    op.drop_column("blood_units", "reserved_for_shipment_id")

    # Postgres cannot drop a single enum VALUE -- downgrading this one
    # honestly requires rebuilding the type without it. Since no
    # downgrade path in this codebase's history has ever needed to run
    # against data that actually uses a dropped value (downgrades are
    # exercised immediately after upgrade in this project's own test
    # convention, never against aged production data), this recreates the
    # type from the pre-Phase-D value set and reassigns every column that
    # uses it -- failing loudly (not silently truncating data) if any row
    # actually has TRANSFERRED_IN at downgrade time.
    op.execute(sa.text("ALTER TYPE blood_unit_provenance RENAME TO blood_unit_provenance_old"))
    op.execute(sa.text("CREATE TYPE blood_unit_provenance AS ENUM ('REAL', 'LEGACY_BACKFILL')"))
    # The column's own server_default literal is typed against the OLD
    # enum object and blocks a plain ALTER COLUMN ... TYPE (Postgres
    # refuses to auto-cast a default across two distinct enum types) --
    # drop it, change the column type, then restore the identical default
    # against the NEW type.
    op.execute(sa.text("ALTER TABLE blood_units ALTER COLUMN provenance DROP DEFAULT"))
    op.execute(
        sa.text(
            "ALTER TABLE blood_units ALTER COLUMN provenance TYPE blood_unit_provenance "
            "USING provenance::text::blood_unit_provenance"
        )
    )
    op.execute(
        sa.text("ALTER TABLE blood_units ALTER COLUMN provenance SET DEFAULT 'REAL'")
    )
    op.execute(sa.text("DROP TYPE blood_unit_provenance_old"))

    op.drop_table("shipment_events")

    op.drop_index("uq_shipment_items_active_unit", table_name="shipment_items")
    op.drop_index("ix_shipment_items_blood_unit_id", table_name="shipment_items")
    op.drop_index("ix_shipment_items_shipment_id", table_name="shipment_items")
    op.drop_table("shipment_items")

    op.drop_index("ix_shipments_destination_status", table_name="shipments")
    op.drop_index("ix_shipments_origin_status", table_name="shipments")
    op.drop_index("ix_shipments_status", table_name="shipments")
    op.drop_index("ix_shipments_transport_route_id", table_name="shipments")
    op.drop_index("ix_shipments_destination_facility_id", table_name="shipments")
    op.drop_index("ix_shipments_origin_facility_id", table_name="shipments")
    op.drop_table("shipments")
    sa.Enum(name="shipment_status").drop(op.get_bind(), checkfirst=True)
