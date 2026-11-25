"""phase11: independent per-role self-registration counters

Adds `registration_counters` -- one row per self-registerable role
(PATIENT/FAMILY/DONOR/HOSPITAL), each tracking how many accounts have been
created through POST /auth/register since this migration ran. This is a
purely additive migration: it does not touch `users` or any other existing
table, does not delete or relabel any existing account, and does not
retroactively count the database's existing seeded/synthetic users (see
LIFELINK_PHASE_11_REPORT.md for the rationale). Each of the four rows
starts at count=0 regardless of how many users of that role already exist.

There is deliberately no row for ADMIN -- ADMIN cannot self-register
(`app.schemas.auth.REGISTERABLE_ROLES`), so no counter for it is ever read
or written.

Revision ID: ca362e8cb9cc
Revises: b7c8d9e0f1a2
Create Date: 2026-09-13
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "ca362e8cb9cc"
down_revision: str | None = "b7c8d9e0f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# create_type=False: `user_role` already exists (created in 0001_initial_auth_schema).
_USER_ROLE = postgresql.ENUM(
    "PATIENT", "FAMILY", "DONOR", "HOSPITAL", "ADMIN",
    name="user_role", create_type=False,
)

_SELF_REGISTERABLE_ROLES = ("PATIENT", "FAMILY", "DONOR", "HOSPITAL")


def upgrade() -> None:
    op.create_table(
        "registration_counters",
        sa.Column("role", _USER_ROLE, primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
    )
    counters = sa.table(
        "registration_counters",
        sa.column("role", _USER_ROLE),
        sa.column("count", sa.Integer()),
    )
    op.bulk_insert(
        counters,
        [{"role": role, "count": 0} for role in _SELF_REGISTERABLE_ROLES],
    )


def downgrade() -> None:
    op.drop_table("registration_counters")
