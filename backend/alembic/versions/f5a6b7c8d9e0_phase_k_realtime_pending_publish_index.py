"""Phase K: realtime publisher indexes (pending-publish read, retention delete).

Evidence (docs/LIFELINK_PHASE_K_PERFORMANCE_REPORT.md): on the Phase K
volume database the publisher's `seq IS NOT NULL AND published_at IS NULL
ORDER BY seq LIMIT 2000` used the unique seq index and filtered every
already-published row (8.7-10.0 ms per committing write, growing with
retention). The partial index contains only rows awaiting publication.
The retention delete (`created_at < cutoff AND published_at IS NOT NULL`)
was a sequential scan of the whole table (47 ms at 251k rows).

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "f5a6b7c8d9e0"
down_revision: str | None = "e4f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_realtime_events_created_at", "realtime_events", ["created_at"])
    op.create_index(
        "ix_realtime_events_pending_publish", "realtime_events", ["seq"],
        postgresql_where=sa.text("seq IS NOT NULL AND published_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_realtime_events_pending_publish", table_name="realtime_events")
    op.drop_index("ix_realtime_events_created_at", table_name="realtime_events")
