"""Unified Donor Priority Score (2026-10-31): record how each contacted donor
was ranked.

Revision ID: a7b8c9d0e1f2
Revises: f9a0b1c2d3e4
Create Date: 2026-10-31

Additive only. donor_contact_attempts gains:

* ranking_strategy -- UNIFIED_PRIORITY or one of the four matching-engine
  strategies used for the batch;
* scoring_model -- "UNIFIED_PRIORITY/<version>#<config fingerprint>";
* score_snapshot -- the factor values behind the score (no personal data).

Existing rows keep NULL (they were ranked by the superseded
CONFIDENCE_SHORTLIST score; nothing is back-filled with a guess).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "f9a0b1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("donor_contact_attempts",
                  sa.Column("ranking_strategy", sa.String(length=32), nullable=True))
    op.add_column("donor_contact_attempts",
                  sa.Column("scoring_model", sa.String(length=80), nullable=True))
    op.add_column("donor_contact_attempts",
                  sa.Column("score_snapshot", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("donor_contact_attempts", "score_snapshot")
    op.drop_column("donor_contact_attempts", "scoring_model")
    op.drop_column("donor_contact_attempts", "ranking_strategy")
