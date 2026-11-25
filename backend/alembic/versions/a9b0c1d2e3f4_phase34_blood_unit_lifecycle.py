"""phase 34: real per-unit blood_units table + state machine, backfilled
from existing hospital_inventory aggregate counts

Revision ID: a9b0c1d2e3f4
Revises: f7a8b9c0d1e2
Create Date: 2026-10-01
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a9b0c1d2e3f4"
down_revision: str | None = "f7a8b9c0d1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Referencing the existing shared enum -- create_type=False + full member
# list, per the a1b2c3d4e5f6 migration's own convention.
blood_type = postgresql.ENUM(
    "A_POS", "A_NEG", "B_POS", "B_NEG", "AB_POS", "AB_NEG", "O_POS", "O_NEG",
    name="blood_type", create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()

    # Brand-new enums on a brand-new table: let `create_table` create each
    # type itself (its default create_type=True) rather than pre-creating
    # it separately -- doing both in the same transaction double-creates
    # it and fails. Matches d4e5f6a7b8c9's own documented precedent for
    # exactly this situation.
    blood_unit_state = sa.Enum(
        "AVAILABLE", "RESERVED", "ALLOCATED", "EXPIRED", name="blood_unit_state"
    )
    blood_unit_provenance = sa.Enum(
        "REAL", "LEGACY_BACKFILL", name="blood_unit_provenance"
    )

    op.create_table(
        "blood_units",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "hospital_id", sa.Uuid(),
            sa.ForeignKey("hospital_profiles.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("blood_type", blood_type, nullable=False),
        sa.Column(
            "state", blood_unit_state, nullable=False, server_default="AVAILABLE",
        ),
        sa.Column(
            "provenance", blood_unit_provenance, nullable=False, server_default="REAL",
        ),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "reserved_for_request_id", sa.Uuid(),
            sa.ForeignKey("blood_requests.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "reserved_for_emergency_id", sa.Uuid(),
            sa.ForeignKey("emergencies.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "last_log_id", sa.Uuid(),
            sa.ForeignKey("hospital_inventory_logs.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_blood_units_hospital_id", "blood_units", ["hospital_id"])
    op.create_index("ix_blood_units_state", "blood_units", ["state"])
    op.create_index(
        "ix_blood_units_hospital_type_state", "blood_units", ["hospital_id", "blood_type", "state"]
    )
    op.create_index("ix_blood_units_expires_at", "blood_units", ["expires_at"])
    op.create_index(
        "ix_blood_units_reserved_for_request_id", "blood_units", ["reserved_for_request_id"]
    )
    op.create_index(
        "ix_blood_units_reserved_for_emergency_id", "blood_units", ["reserved_for_emergency_id"]
    )

    # --------------------------------------------------------------------- #
    # Backfill: one BloodUnit row per physical unit the existing aggregate
    # HospitalInventory counters already claim to hold, for every
    # (hospital, blood_type) pair with units > 0.
    #
    # Rule 23 / report section 18/23: we CANNOT know the true collection
    # date or true individual age of any of this pre-existing stock -- it
    # was only ever recorded as a plain integer. We do NOT invent a
    # plausible-looking history. Every backfilled row is explicitly
    # provenance=LEGACY_BACKFILL, received_at=this migration's own run
    # time (never an earlier guessed date), and expires_at=NULL (no
    # shelf-life clock is started from an unknown true age -- see
    # BloodUnitProvenance's docstring and report section 6).
    #
    # Of each row's `units`, the first `reserved_units` backfilled rows are
    # created RESERVED (matching the pre-existing aggregate's own
    # "reserved_units <= units" invariant) and the rest AVAILABLE. Which
    # *specific* units are "the reserved ones" is arbitrary -- the
    # aggregate never recorded that distinction at the per-unit level
    # either, so there is nothing more specific to preserve. No
    # reserved_for_request_id/emergency_id is set for these backfilled
    # reservations (unknowable which request/emergency, if any, the
    # original RESERVE action was for) -- release_reservation/
    # allocate_stock's FEFO/"prefer" selection falls back to plain FEFO
    # order for these exactly as it would for any unit with no matching
    # context, so this is never a correctness problem, only a traceability
    # gap this migration honestly leaves undisguised rather than guessing.
    # --------------------------------------------------------------------- #
    hospital_inventory = sa.table(
        "hospital_inventory",
        sa.column("hospital_id", sa.Uuid()),
        sa.column("blood_type", blood_type),
        sa.column("units", sa.Integer()),
        sa.column("reserved_units", sa.Integer()),
    )
    blood_units_table = sa.table(
        "blood_units",
        sa.column("id", sa.Uuid()),
        sa.column("hospital_id", sa.Uuid()),
        sa.column("blood_type", blood_type),
        sa.column("state", blood_unit_state),
        sa.column("provenance", blood_unit_provenance),
        sa.column("received_at", sa.DateTime(timezone=True)),
        sa.column("expires_at", sa.DateTime(timezone=True)),
        sa.column("state_changed_at", sa.DateTime(timezone=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    now = datetime.now(UTC)
    rows = bind.execute(
        sa.select(
            hospital_inventory.c.hospital_id,
            hospital_inventory.c.blood_type,
            hospital_inventory.c.units,
            hospital_inventory.c.reserved_units,
        )
    ).all()
    to_insert: list[dict] = []
    for hospital_id, bt, units, reserved in rows:
        if not units or units <= 0:
            continue
        reserved = reserved or 0
        for i in range(units):
            to_insert.append(
                {
                    "id": uuid.uuid4(),
                    "hospital_id": hospital_id,
                    "blood_type": bt,
                    "state": "RESERVED" if i < reserved else "AVAILABLE",
                    "provenance": "LEGACY_BACKFILL",
                    "received_at": now,
                    "expires_at": None,
                    "state_changed_at": now,
                    "created_at": now,
                    "updated_at": now,
                }
            )
    if to_insert:
        bind.execute(sa.insert(blood_units_table), to_insert)


def downgrade() -> None:
    op.drop_index("ix_blood_units_reserved_for_emergency_id", table_name="blood_units")
    op.drop_index("ix_blood_units_reserved_for_request_id", table_name="blood_units")
    op.drop_index("ix_blood_units_expires_at", table_name="blood_units")
    op.drop_index("ix_blood_units_hospital_type_state", table_name="blood_units")
    op.drop_index("ix_blood_units_state", table_name="blood_units")
    op.drop_index("ix_blood_units_hospital_id", table_name="blood_units")
    op.drop_table("blood_units")
    bind = op.get_bind()
    sa.Enum(name="blood_unit_provenance").drop(bind, checkfirst=True)
    sa.Enum(name="blood_unit_state").drop(bind, checkfirst=True)
