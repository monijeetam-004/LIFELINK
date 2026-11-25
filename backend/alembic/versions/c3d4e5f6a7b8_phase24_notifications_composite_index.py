"""phase24: notifications composite indexes for list/unread queries

Revision ID: c3d4e5f6a7b8
Revises: a3b4c5d6e7f8
Create Date: 2026-09-15

Evidence (LIFELINK_PHASE_24_PERFORMANCE_AUDIT.md, section "Database
Findings"): against the real dev database's `notifications` table
(47,497 rows accumulated from prior phases' test runs), `EXPLAIN
(ANALYZE, BUFFERS)` showed:

  * GET /notifications (unread_only=false) -- the existing single-column
    index on `created_at` cannot also filter `user_id`, and the
    single-column index on `user_id` cannot also order by `created_at`,
    so the planner picks a `Bitmap Heap Scan` on `user_id` and then
    sorts in memory. Fine at this row count, but degrades linearly as
    the table grows because the sort work is proportional to that
    user's total row count, not to `limit`.
  * GET /notifications?unread_only=true -- confirmed materially worse:
    the planner scans essentially the whole `created_at` index and
    filters `user_id`/`read_at` afterwards ("Rows Removed by Filter"
    ~47,000 of ~47,500 rows for a typical user), because neither
    existing single-column index can serve a
    `user_id = ? AND read_at IS NULL ORDER BY created_at DESC` query
    without a full-table-scale filter step.
  * GET /notifications/unread-count and POST /notifications/read-all
    share the same `user_id = ? AND read_at IS NULL` predicate.

Two indexes added, matching exactly the query shapes above -- no
speculative fourth index for a query shape nothing calls today:

  1. `ix_notifications_user_created` -- (user_id, created_at DESC).
     Serves the `unread_only=false` list query directly (index-only
     scan of exactly `limit` rows, no in-memory sort).
  2. `ix_notifications_user_unread_created` -- a PARTIAL index on
     (user_id, created_at DESC) WHERE read_at IS NULL. Serves the
     `unread_only=true` list query, `/unread-count`, and `/read-all` --
     all three only ever care about the unread subset, so a partial
     index is both smaller and cheaper to maintain than a full one
     that included `read_at`, and PostgreSQL's planner already knows to
     use a partial index for a query whose WHERE clause matches its
     predicate.

The pre-existing single-column indexes on `user_id` and `created_at`
are left in place (not dropped): dropping them was not evidenced by
any query plan showing them as harmful, and removing them without
evidence would violate this phase's own "no premature optimization"
rule just as much as adding an unevidenced index would.

Verified upgrade -> downgrade -> upgrade-again against the real dev
database, and before/after EXPLAIN (ANALYZE, BUFFERS) comparison
recorded in LIFELINK_PHASE_24_PERFORMANCE_REPORT.md section 7.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "a3b4c5d6e7f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_notifications_user_created",
        "notifications",
        ["user_id", "created_at"],
        unique=False,
        postgresql_ops={"created_at": "DESC"},
    )
    op.create_index(
        "ix_notifications_user_unread_created",
        "notifications",
        ["user_id", "created_at"],
        unique=False,
        postgresql_ops={"created_at": "DESC"},
        postgresql_where="read_at IS NULL",
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_user_unread_created", table_name="notifications")
    op.drop_index("ix_notifications_user_created", table_name="notifications")
