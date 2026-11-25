"""phase31: certificate revocation (revoked_at/by/reason)

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-09-18

Domain-correctness fix (LIFELINK_PHASE_31_DOMAIN_AUDIT.md, "Certificate
trust model"): once issued, a Certificate row had no way to ever be
invalidated -- no revoked/valid/unknown distinction anywhere, so an admin
who discovered a certificate was issued for a donation later found
fraudulent or erroneous had no mechanism to say so, and the public
`GET /certificates/verify/{code}` endpoint could not distinguish "this
code never existed" from "this was a real certificate that is no longer
trustworthy".

Three new nullable columns, purely additive:
  * revoked_at            -- when it was revoked (None = never revoked)
  * revoked_by_user_id    -- which admin revoked it (SET NULL on delete,
                              matching every other actor-reference FK in
                              this codebase, e.g. AuditLog.actor_user_id)
  * revocation_reason     -- required at the API layer when revoking, but
                              nullable at the DB layer like every other
                              free-text reason/note column here

No backfill needed -- every existing certificate is unrevoked by
definition, which is exactly what NULL already means.

Verified upgrade -> downgrade -> upgrade-again against the real dev
database.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f7a8b9c0d1e2"
down_revision: str | None = "e6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "certificates", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "certificates", sa.Column("revoked_by_user_id", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "certificates", sa.Column("revocation_reason", sa.String(length=500), nullable=True)
    )
    op.create_foreign_key(
        "fk_certificates_revoked_by_user_id",
        "certificates",
        "users",
        ["revoked_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_certificates_revoked_by_user_id", "certificates", type_="foreignkey"
    )
    op.drop_column("certificates", "revocation_reason")
    op.drop_column("certificates", "revoked_by_user_id")
    op.drop_column("certificates", "revoked_at")
