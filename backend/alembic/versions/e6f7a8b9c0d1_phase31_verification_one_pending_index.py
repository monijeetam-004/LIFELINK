"""phase31: at most one PENDING verification request per (user, kind)

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-09-18

Concurrency-safety fix (LIFELINK_PHASE_31_DOMAIN_AUDIT.md, "Verification
lifecycle" / "Concurrency and race-condition audit"): `POST /verifications`
(app/api/routes/verifications.py::submit_verification) only ever enforced
"no other PENDING request of this kind for this user" with a pre-flight
SELECT -- a genuine check-then-insert TOCTOU race, not merely a theoretical
one, since nothing stopped two concurrent submissions (a double-click, two
browser tabs, a client retry racing the original) from both reading no
PENDING row and both inserting one.

This migration adds a partial unique index -- the same pattern already used
in this codebase for `uq_emergency_pathway_one_active`
(d4e5f6a7b8c9_phase12_emergency_sos_engine.py) -- covering only PENDING
rows, so APPROVED/REJECTED history for the same (user, kind) is completely
unaffected: a user can resubmit after a rejection, or submit again after an
old approval, exactly as before. Only ever having *two PENDING rows at
once* for the same (user, kind) becomes impossible at the database level.

No data migration needed: if any duplicate PENDING rows already exist in a
real deployment (never observed in this codebase's own dev/test data, but
not assumed away either), creating the index would fail loudly with a clear
Postgres error naming the conflicting rows -- exactly the honest failure
mode wanted here, rather than silently deleting or merging data during a
migration.

Verified upgrade -> downgrade -> upgrade-again against the real dev
database.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e6f7a8b9c0d1"
down_revision: str | None = "d5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_verification_one_pending_per_kind",
        "verification_requests",
        ["user_id", "kind"],
        unique=True,
        postgresql_where="status = 'PENDING'",
    )


def downgrade() -> None:
    op.drop_index("uq_verification_one_pending_per_kind", table_name="verification_requests")
