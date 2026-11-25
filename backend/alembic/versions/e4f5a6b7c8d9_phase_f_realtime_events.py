"""phase F: real-time event outbox (realtime_events)

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-10-05

No backfill: the outbox records changes from now on. Clients that connect
with no cursor simply start live; there is no historical event stream to
reconstruct, and inventing one from audit/history rows would publish
"events" nobody's screen ever missed.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e4f5a6b7c8d9"
down_revision: str | None = "d3e4f5a6b7c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "realtime_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("resource_type", sa.String(40), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("clock_timestamp()")),
        sa.Column("seq", sa.BigInteger(), nullable=True, unique=True),
        sa.Column("audience_user_ids", postgresql.ARRAY(sa.Uuid()), nullable=True),
        sa.Column("audience_roles", postgresql.ARRAY(sa.String(20)), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_realtime_events_unpublished", "realtime_events", ["created_at", "id"],
        postgresql_where=sa.text("seq IS NULL"),
    )
    op.create_index(
        "ix_realtime_events_audience_users", "realtime_events", ["audience_user_ids"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_realtime_events_audience_users", table_name="realtime_events")
    op.drop_index("ix_realtime_events_unpublished", table_name="realtime_events")
    op.drop_table("realtime_events")
