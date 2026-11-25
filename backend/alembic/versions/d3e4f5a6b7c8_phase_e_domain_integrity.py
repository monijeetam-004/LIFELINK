"""phase E: domain integrity -- fulfilment quantities, donation verification,
verification-document versioning, audit hash chain, shipment reconciliation,
expiry rule provenance.

Every backfill below uses only data that already exists in the database;
nothing is guessed:

  * blood_requests.fulfilled_units -- FULFILLED rows get units_needed (that
    is what FULFILLED always meant); open rows get the real sum of their
    historical ALLOCATE inventory-log deltas, capped at units_needed. Any
    open row that thereby becomes partly/fully met moves to the matching
    status WITH a `blood_request_events` row saying the migration did it
    (never a silent status change).
  * hospital_request_responses.units_allocated -- real per-hospital sum of
    ALLOCATE log deltas for that request.
  * donations.verification_status -- DONOR_REPORTED for every existing row
    (none was ever verified by anyone; claiming otherwise would be false).
  * verification_requests.version / supersedes_request_id -- per-(user,
    kind) ordinal by (created_at, id) and the previous row's id. Existing
    documents get NO sha256 (none was recorded at submission; computing one
    now would misrepresent when it was taken) and are served as
    "unverified-legacy".
  * audit_logs -- every existing row is sealed into the chain in
    (created_at, id) order using hash version 1 (app.core.audit_chain).
    The chain can only attest to these rows' content AS OF THIS MIGRATION;
    any change made before it is not detectable. Stated in
    docs/LIFELINK_AUDIT_CHAIN.md.
  * blood_units.expiry_rule_ref -- left NULL for existing units: which
    rule produced their expires_at (the Phase 34 config placeholder or the
    Phase 35 cited table) was never recorded, so none is claimed.
  * shipments.reconciliation_status -- REQUIRED for every existing FAILED
    shipment (each genuinely still owes a decision), NULL otherwise.

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-10-05
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d3e4f5a6b7c8"
down_revision: str | None = "c2d3e4f5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _seal_existing_audit_rows(bind) -> int:
    from app.core import audit_chain as ac

    rows = bind.execute(sa.text(
        "SELECT id, actor_user_id, action, target_type, target_id, ip, user_agent, meta, "
        "created_at FROM audit_logs ORDER BY created_at, id"
    )).all()
    prev, seq = ac.GENESIS_HASH, 0
    for r in rows:
        seq += 1
        fields = ac.ChainFields(
            id=r.id, actor_user_id=r.actor_user_id, action=r.action,
            target_type=r.target_type, target_id=r.target_id, ip=r.ip,
            user_agent=r.user_agent, meta=r.meta or {}, created_at=r.created_at,
        )
        h = ac.entry_hash(fields, seq=seq, prev_hash=prev)
        bind.execute(
            sa.text(
                "UPDATE audit_logs SET chain_seq=:s, prev_hash=:p, entry_hash=:h, "
                "hash_version=:v, sealed_at=now() WHERE id=:id"
            ),
            {"s": seq, "p": prev, "h": h, "v": ac.HASH_VERSION, "id": r.id},
        )
        prev = h
    return seq


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------ #
    # 1. Fulfilment quantities
    # ------------------------------------------------------------------ #
    # ADD VALUE must be committed before the new value can be used (the
    # backfill below may set it) -- Postgres rejects an "unsafe use of new
    # value" inside the same transaction. Same autocommit_block pattern as
    # migrations 0004 / a1b2c3d4e5f6.
    with op.get_context().autocommit_block():
        op.execute(sa.text(
            "ALTER TYPE blood_request_status ADD VALUE IF NOT EXISTS 'PARTIALLY_FULFILLED'"
        ))
    op.add_column("blood_requests", sa.Column(
        "fulfilled_units", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("hospital_request_responses", sa.Column(
        "units_allocated", sa.Integer(), nullable=False, server_default="0"))

    op.execute(sa.text(
        "UPDATE hospital_request_responses hr SET units_allocated = sub.total "
        "FROM (SELECT hospital_id, request_id, SUM(ABS(delta_units))::int AS total "
        "      FROM hospital_inventory_logs WHERE action = 'ALLOCATE' AND request_id IS NOT NULL "
        "      GROUP BY hospital_id, request_id) sub "
        "WHERE hr.hospital_id = sub.hospital_id AND hr.request_id = sub.request_id"
    ))
    op.execute(sa.text(
        "UPDATE blood_requests SET fulfilled_units = units_needed WHERE status = 'FULFILLED'"
    ))
    op.execute(sa.text(
        "UPDATE blood_requests br SET fulfilled_units = LEAST(sub.total, br.units_needed) "
        "FROM (SELECT request_id, SUM(ABS(delta_units))::int AS total "
        "      FROM hospital_inventory_logs WHERE action = 'ALLOCATE' AND request_id IS NOT NULL "
        "      GROUP BY request_id) sub "
        "WHERE br.id = sub.request_id AND br.status IN ('PENDING', 'MATCHING')"
    ))
    # Bring any now-partly/fully-met open request's status in line, with a
    # visible event per row -- never silent.
    op.execute(sa.text(
        "INSERT INTO blood_request_events (id, request_id, actor_user_id, event_type, note, created_at) "
        "SELECT gen_random_uuid(), id, NULL, 'phase_e_fulfilment_backfill', "
        "       'Migration: ' || fulfilled_units || '/' || units_needed || "
        "       ' unit(s) already allocated before quantity tracking existed', now() "
        "FROM blood_requests WHERE status IN ('PENDING', 'MATCHING') AND fulfilled_units > 0"
    ))
    op.execute(sa.text(
        "UPDATE blood_requests SET status = 'FULFILLED', fulfilled_at = COALESCE(fulfilled_at, now()) "
        "WHERE status IN ('PENDING', 'MATCHING') AND fulfilled_units >= units_needed "
        "AND fulfilled_units > 0"
    ))
    op.execute(sa.text(
        "UPDATE blood_requests SET status = 'PARTIALLY_FULFILLED' "
        "WHERE status IN ('PENDING', 'MATCHING') AND fulfilled_units > 0"
    ))
    op.create_check_constraint(
        "ck_blood_request_fulfilled_non_negative", "blood_requests", "fulfilled_units >= 0")
    op.create_check_constraint(
        "ck_blood_request_fulfilled_not_over", "blood_requests", "fulfilled_units <= units_needed")

    # ------------------------------------------------------------------ #
    # 2. Donation verification
    # ------------------------------------------------------------------ #
    dvs = sa.Enum("DONOR_REPORTED", "VERIFIED", "REJECTED", name="donation_verification_status")
    dvs.create(bind, checkfirst=True)
    op.add_column("donations", sa.Column(
        "verification_status", dvs, nullable=False, server_default="DONOR_REPORTED"))
    op.create_index("ix_donations_verification_status", "donations", ["verification_status"])
    op.add_column("donations", sa.Column(
        "hospital_id", sa.Uuid(),
        sa.ForeignKey("hospital_profiles.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_donations_hospital_id", "donations", ["hospital_id"])
    op.add_column("donations", sa.Column(
        "verified_by_user_id", sa.Uuid(),
        sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    op.add_column("donations", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("donations", sa.Column("verification_note", sa.String(1000), nullable=True))

    # ------------------------------------------------------------------ #
    # 3. Verification-document versioning
    # ------------------------------------------------------------------ #
    op.add_column("verification_requests", sa.Column(
        "version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("verification_requests", sa.Column(
        "supersedes_request_id", sa.Uuid(),
        sa.ForeignKey("verification_requests.id", ondelete="SET NULL"), nullable=True))
    op.execute(sa.text(
        "UPDATE verification_requests vr SET version = sub.rn, supersedes_request_id = sub.prev "
        "FROM (SELECT id, ROW_NUMBER() OVER w AS rn, LAG(id) OVER w AS prev "
        "      FROM verification_requests "
        "      WINDOW w AS (PARTITION BY user_id, kind ORDER BY created_at, id)) sub "
        "WHERE vr.id = sub.id"
    ))
    op.create_unique_constraint(
        "uq_verification_version", "verification_requests", ["user_id", "kind", "version"])

    # ------------------------------------------------------------------ #
    # 4. Audit hash chain
    # ------------------------------------------------------------------ #
    op.add_column("audit_logs", sa.Column("chain_seq", sa.BigInteger(), nullable=True))
    op.add_column("audit_logs", sa.Column("prev_hash", sa.String(64), nullable=True))
    op.add_column("audit_logs", sa.Column("entry_hash", sa.String(64), nullable=True))
    op.add_column("audit_logs", sa.Column("hash_version", sa.SmallInteger(), nullable=True))
    op.add_column("audit_logs", sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_unique_constraint("audit_logs_chain_seq_key", "audit_logs", ["chain_seq"])
    op.create_index(
        "ix_audit_logs_unsealed", "audit_logs", ["created_at", "id"],
        postgresql_where=sa.text("chain_seq IS NULL"),
    )
    # Serialize with any live sealer (same advisory key) while backfilling.
    from app.core.audit_chain import ADVISORY_LOCK_KEY

    bind.execute(sa.text("SELECT pg_advisory_xact_lock(:k)"), {"k": ADVISORY_LOCK_KEY})
    _seal_existing_audit_rows(bind)

    # ------------------------------------------------------------------ #
    # 5. Expiry rule provenance
    # ------------------------------------------------------------------ #
    op.add_column("blood_units", sa.Column("expiry_rule_ref", sa.String(80), nullable=True))

    # ------------------------------------------------------------------ #
    # 6. Failed-shipment reconciliation
    # ------------------------------------------------------------------ #
    srs = sa.Enum(
        "REQUIRED", "RETURNED_TO_ORIGIN", "REDIRECTED", "QUARANTINED",
        name="shipment_reconciliation_status",
    )
    srs.create(bind, checkfirst=True)
    op.add_column("shipments", sa.Column("reconciliation_status", srs, nullable=True))
    op.create_index("ix_shipments_reconciliation_status", "shipments", ["reconciliation_status"])
    op.add_column("shipments", sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("shipments", sa.Column(
        "reconciled_by_user_id", sa.Uuid(),
        sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    op.add_column("shipments", sa.Column("reconciliation_note", sa.String(1000), nullable=True))
    op.add_column("shipments", sa.Column(
        "redirected_to_facility_id", sa.Uuid(),
        sa.ForeignKey("hospital_profiles.id", ondelete="SET NULL"), nullable=True))
    op.execute(sa.text(
        "UPDATE shipments SET reconciliation_status = 'REQUIRED' WHERE status = 'FAILED'"
    ))


def downgrade() -> None:
    bind = op.get_bind()

    # 6
    op.drop_column("shipments", "redirected_to_facility_id")
    op.drop_column("shipments", "reconciliation_note")
    op.drop_column("shipments", "reconciled_by_user_id")
    op.drop_column("shipments", "reconciled_at")
    op.drop_index("ix_shipments_reconciliation_status", table_name="shipments")
    op.drop_column("shipments", "reconciliation_status")
    sa.Enum(name="shipment_reconciliation_status").drop(bind, checkfirst=True)

    # 5
    op.drop_column("blood_units", "expiry_rule_ref")

    # 4 -- dropping the chain loses only derived hashes; rows are untouched.
    op.drop_index("ix_audit_logs_unsealed", table_name="audit_logs")
    op.drop_constraint("audit_logs_chain_seq_key", "audit_logs", type_="unique")
    for col in ("sealed_at", "hash_version", "entry_hash", "prev_hash", "chain_seq"):
        op.drop_column("audit_logs", col)

    # 3
    op.drop_constraint("uq_verification_version", "verification_requests", type_="unique")
    op.drop_column("verification_requests", "supersedes_request_id")
    op.drop_column("verification_requests", "version")

    # 2 -- VERIFIED/REJECTED decisions would be lost; refuse rather than
    # silently discard real human review history.
    reviewed = bind.execute(sa.text(
        "SELECT count(*) FROM donations WHERE verification_status <> 'DONOR_REPORTED'"
    )).scalar()
    if reviewed:
        raise RuntimeError(
            f"Refusing to downgrade: {reviewed} donation(s) carry a real verification "
            "decision that the pre-Phase-E schema cannot represent."
        )
    op.drop_column("donations", "verification_note")
    op.drop_column("donations", "verified_at")
    op.drop_column("donations", "verified_by_user_id")
    op.drop_index("ix_donations_hospital_id", table_name="donations")
    op.drop_column("donations", "hospital_id")
    op.drop_index("ix_donations_verification_status", table_name="donations")
    op.drop_column("donations", "verification_status")
    sa.Enum(name="donation_verification_status").drop(bind, checkfirst=True)

    # 1 -- same refusal rule for a state the old enum cannot hold.
    partial = bind.execute(sa.text(
        "SELECT count(*) FROM blood_requests WHERE status = 'PARTIALLY_FULFILLED'"
    )).scalar()
    if partial:
        raise RuntimeError(
            f"Refusing to downgrade: {partial} request(s) are PARTIALLY_FULFILLED, "
            "which the pre-Phase-E status enum cannot represent."
        )
    op.drop_constraint("ck_blood_request_fulfilled_not_over", "blood_requests", type_="check")
    op.drop_constraint("ck_blood_request_fulfilled_non_negative", "blood_requests", type_="check")
    op.drop_column("hospital_request_responses", "units_allocated")
    op.drop_column("blood_requests", "fulfilled_units")
    op.execute(sa.text("DELETE FROM blood_request_events WHERE event_type = 'phase_e_fulfilment_backfill'"))
    op.execute(sa.text("ALTER TYPE blood_request_status RENAME TO blood_request_status_old"))
    op.execute(sa.text(
        "CREATE TYPE blood_request_status AS ENUM "
        "('PENDING', 'MATCHING', 'FULFILLED', 'CANCELLED', 'EXPIRED')"
    ))
    # blood_requests.status has no server default (verified against the
    # live schema), so none is dropped or restored here.
    op.execute(sa.text(
        "ALTER TABLE blood_requests ALTER COLUMN status TYPE blood_request_status "
        "USING status::text::blood_request_status"
    ))
    op.execute(sa.text("DROP TYPE blood_request_status_old"))
