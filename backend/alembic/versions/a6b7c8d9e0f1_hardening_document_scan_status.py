"""Hardening: every stored verification document carries a scan_status.

Documents stored before malware scanning existed were never inspected
(their metadata says `scanned_by: "noop"`). They are marked PENDING_SCAN, so
they are withheld from download and approval until a scan passes
(`python -m scripts.rescan_documents`). Downgrade removes the key again.

Revision ID: a6b7c8d9e0f1
Revises: f5a6b7c8d9e0
"""

from __future__ import annotations

import json

import sqlalchemy as sa

from alembic import op

revision: str = "a6b7c8d9e0f1"
down_revision: str | None = "f5a6b7c8d9e0"
branch_labels = None
depends_on = None


def _rewrite(transform) -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, documents FROM verification_requests")).all()
    for rid, docs in rows:
        if isinstance(docs, str):
            docs = json.loads(docs)
        new = [transform(dict(d)) for d in (docs or [])]
        if new != docs:
            conn.execute(
                sa.text(
                    "UPDATE verification_requests SET documents = CAST(:d AS json) WHERE id = :i"
                ),
                {"d": json.dumps(new), "i": rid},
            )


def upgrade() -> None:
    def add(d: dict) -> dict:
        d.setdefault("scan_status", "PENDING_SCAN")
        return d

    _rewrite(add)


def downgrade() -> None:
    def strip(d: dict) -> dict:
        for k in ("scan_status", "scan_attempts", "scanned_at", "scan_detail"):
            d.pop(k, None)
        return d

    _rewrite(strip)
