"""phase 35: blood component column, unit identity code, and donation
traceability link on blood_units

Revision ID: b1c2d3e4f5a6
Revises: a9b0c1d2e3f4
Create Date: 2026-10-05
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: str | None = "a9b0c1d2e3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Short, non-PII abbreviations used only to build a readable unit_code --
# never used for anything else, and reversible from the row's own
# blood_type/component columns anyway (the code is a convenience label,
# not a second source of truth).
_BLOOD_TYPE_SHORT = {
    "A_POS": "APOS", "A_NEG": "ANEG", "B_POS": "BPOS", "B_NEG": "BNEG",
    "AB_POS": "ABPOS", "AB_NEG": "ABNEG", "O_POS": "OPOS", "O_NEG": "ONEG",
}
_COMPONENT_SHORT = {
    "WHOLE_BLOOD": "WB", "RED_BLOOD_CELLS": "RBC", "PLASMA": "FFP", "PLATELETS": "PLT",
}


def _unit_code(blood_type: str, component: str) -> str:
    return (
        f"LL-{_BLOOD_TYPE_SHORT[blood_type]}-{_COMPONENT_SHORT[component]}-"
        f"{uuid.uuid4().hex[:8].upper()}"
    )


def upgrade() -> None:
    bind = op.get_bind()

    # New enum on an EXISTING table (blood_units already exists from
    # Phase 34). Unlike `op.create_table` (whose default create_type=True
    # auto-creates a brand-new enum as part of the CREATE TABLE
    # statement, the Phase 34 precedent), `op.add_column` against an
    # existing table does NOT auto-create the enum type -- confirmed by
    # running this migration and getting `UndefinedObject: type
    # "blood_component" does not exist` before this explicit .create()
    # call was added. So here, unlike Phase 34's table-creation case, the
    # type genuinely must be created explicitly first.
    blood_component = sa.Enum(
        "WHOLE_BLOOD", "RED_BLOOD_CELLS", "PLASMA", "PLATELETS", name="blood_component"
    )
    blood_component.create(bind, checkfirst=True)

    # 1. component -- nullable=True at first (existing rows have none yet);
    #    backfilled below, then tightened to NOT NULL.
    op.add_column(
        "blood_units",
        sa.Column(
            "component", blood_component, nullable=True,
            server_default="WHOLE_BLOOD",
        ),
    )
    # 2. donation_id -- nullable FK, no backfill needed (every existing
    #    unit honestly has no known donation link -- see model docstring).
    op.add_column(
        "blood_units",
        sa.Column(
            "donation_id", sa.Uuid(),
            sa.ForeignKey("donations.id", ondelete="SET NULL"), nullable=True,
        ),
    )
    op.create_index("ix_blood_units_donation_id", "blood_units", ["donation_id"])
    # 3. unit_code -- nullable at first; backfilled below with a generated
    #    code per existing row, then tightened to NOT NULL + UNIQUE.
    op.add_column(
        "blood_units", sa.Column("unit_code", sa.String(length=40), nullable=True)
    )

    # --------------------------------------------------------------------- #
    # Backfill existing rows: component='WHOLE_BLOOD' (the honest-default
    # value; server_default above already covers any row inserted
    # concurrently during this migration, but existing rows need an
    # explicit UPDATE since server_default only applies to new inserts)
    # and a generated unit_code per row.
    # --------------------------------------------------------------------- #
    op.execute(sa.text("UPDATE blood_units SET component = 'WHOLE_BLOOD' WHERE component IS NULL"))

    blood_units_table = sa.table(
        "blood_units",
        sa.column("id", sa.Uuid()),
        sa.column("blood_type", sa.String()),
        sa.column("component", sa.String()),
        sa.column("unit_code", sa.String()),
    )
    rows = bind.execute(
        sa.select(blood_units_table.c.id, blood_units_table.c.blood_type)
    ).all()
    for unit_id, blood_type in rows:
        bind.execute(
            sa.update(blood_units_table)
            .where(blood_units_table.c.id == unit_id)
            .values(unit_code=_unit_code(blood_type, "WHOLE_BLOOD"))
        )

    op.alter_column("blood_units", "component", nullable=False)
    op.alter_column("blood_units", "unit_code", nullable=False)
    op.create_unique_constraint("uq_blood_units_unit_code", "blood_units", ["unit_code"])

    # --------------------------------------------------------------------- #
    # Replace the Phase 34 composite index with the component-aware one
    # every real selection query now actually issues (hospital_id,
    # blood_type, component, state together) -- see blood_unit.py.
    # --------------------------------------------------------------------- #
    op.drop_index("ix_blood_units_hospital_type_state", table_name="blood_units")
    op.create_index(
        "ix_blood_units_hospital_type_component_state",
        "blood_units",
        ["hospital_id", "blood_type", "component", "state"],
    )

    # --------------------------------------------------------------------- #
    # hospital_inventory / hospital_inventory_logs -- the aggregate layer
    # Phase 34 already made provably consistent with per-unit BloodUnit
    # rows now needs the same component dimension, for the same reason:
    # the aggregate's key (hospital_id, blood_type) was always implicitly
    # "...and WHOLE_BLOOD" -- this makes that explicit rather than letting
    # a RED_BLOOD_CELLS/PLASMA/PLATELETS unit silently fall into the same
    # bucket as whole blood. The existing uq_hospital_inventory_type
    # constraint is replaced with one that includes component; every
    # existing row backfills to WHOLE_BLOOD, so every pre-existing
    # (hospital_id, blood_type) row keeps meaning exactly what it always
    # meant and the GET/PUT /hospitals/me/inventory contract (which only
    # ever reads/writes the WHOLE_BLOOD row -- see inventory_service.py)
    # is untouched byte-for-byte.
    # --------------------------------------------------------------------- #
    op.add_column(
        "hospital_inventory",
        sa.Column("component", blood_component, nullable=True, server_default="WHOLE_BLOOD"),
    )
    op.execute(
        sa.text("UPDATE hospital_inventory SET component = 'WHOLE_BLOOD' WHERE component IS NULL")
    )
    op.alter_column("hospital_inventory", "component", nullable=False)
    op.drop_constraint("uq_hospital_inventory_type", "hospital_inventory", type_="unique")
    op.create_unique_constraint(
        "uq_hospital_inventory_type_component",
        "hospital_inventory",
        ["hospital_id", "blood_type", "component"],
    )

    op.add_column(
        "hospital_inventory_logs",
        sa.Column("component", blood_component, nullable=True, server_default="WHOLE_BLOOD"),
    )
    op.execute(
        sa.text(
            "UPDATE hospital_inventory_logs SET component = 'WHOLE_BLOOD' WHERE component IS NULL"
        )
    )
    op.alter_column("hospital_inventory_logs", "component", nullable=False)


def downgrade() -> None:
    op.alter_column("hospital_inventory_logs", "component", nullable=True)
    op.drop_column("hospital_inventory_logs", "component")

    op.drop_constraint("uq_hospital_inventory_type_component", "hospital_inventory", type_="unique")
    op.create_unique_constraint(
        "uq_hospital_inventory_type", "hospital_inventory", ["hospital_id", "blood_type"]
    )
    op.drop_column("hospital_inventory", "component")

    op.create_index(
        "ix_blood_units_hospital_type_state", "blood_units", ["hospital_id", "blood_type", "state"]
    )
    op.drop_index(
        "ix_blood_units_hospital_type_component_state", table_name="blood_units"
    )
    op.drop_constraint("uq_blood_units_unit_code", "blood_units", type_="unique")
    op.drop_column("blood_units", "unit_code")
    op.drop_index("ix_blood_units_donation_id", table_name="blood_units")
    op.drop_column("blood_units", "donation_id")
    op.drop_column("blood_units", "component")
    bind = op.get_bind()
    sa.Enum(name="blood_component").drop(bind, checkfirst=True)
