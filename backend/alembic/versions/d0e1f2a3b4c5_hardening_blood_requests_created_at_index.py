"""Hardening: index blood_requests.created_at.

Measured on the volume database (81k requests): the admin request list
("newest 50", optionally by status) and the analytics per-day counts were a
parallel sequential scan + sort over the whole table, growing linearly with
request history. See docs/LIFELINK_HARDENING_REPORT.md for before/after
EXPLAIN ANALYZE.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d0e1f2a3b4c5"
down_revision: str | None = "c9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_blood_requests_created_at", "blood_requests", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_blood_requests_created_at", table_name="blood_requests")
