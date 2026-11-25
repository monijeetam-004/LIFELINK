"""Phase E -- sealing and verifying the audit hash chain (DB-touching half;
the pure half is app.core.audit_chain).

WHY SEAL AFTER COMMIT INSTEAD OF INSIDE `audit_service.record`

The obvious design -- take a global lock inside `record()`, read the latest
hash, insert the new row chained to it -- has two real problems in this
codebase:

  1. The naive version (SELECT latest hash, then INSERT, no lock) races:
     two concurrent transactions read the same "latest" hash and fork the
     chain. Explicitly forbidden by the Phase E brief.
  2. The locked version holds a GLOBAL lock from the first audit write
     until the business transaction commits. Almost every write route in
     this app audits while it already holds row locks (`SELECT ... FOR
     UPDATE` on a blood request, shipment, donation...). Transaction A
     (holding the audit lock) waiting on a row that transaction B holds,
     while B waits on the audit lock, is a deadlock -- and it serializes
     every write in the app behind one lock for the whole request.

So business transactions only INSERT plain rows (chain columns NULL), and a
separate, short sealing transaction chains them:

    BEGIN;
    SELECT pg_advisory_xact_lock(<fixed key>);   -- one sealer at a time
    read head (max chain_seq) ; read unsealed rows ORDER BY created_at, id
    UPDATE each with chain_seq / prev_hash / entry_hash / sealed_at
    COMMIT;

The sealer only ever waits on the advisory lock and on audit rows nobody
else locks, so it cannot deadlock with business locks; and because the
head is read and advanced only while holding the lock, two sealers can
never fork the chain. Sealing runs automatically right after any commit
that wrote audit rows (`audit_service`'s after_commit hook), and any later
sealing run picks up stragglers (e.g. if the process died between commit
and seal) -- `verify_chain` reports how many rows are still unsealed rather
than silently treating them as verified.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator

from sqlalchemy import Connection, select, text, update
from sqlalchemy.orm import Session

from app.core import audit_chain as ac
from app.core.time import utcnow
from app.models.audit_log import AuditLog

log = logging.getLogger("lifelink.audit_chain")

_T = AuditLog.__table__
_SEAL_BATCH = 1000
_VERIFY_PAGE = 2000


def _fields(row) -> ac.ChainFields:
    return ac.ChainFields(
        id=row.id, actor_user_id=row.actor_user_id, action=row.action,
        target_type=row.target_type, target_id=row.target_id, ip=row.ip,
        user_agent=row.user_agent, meta=row.meta or {}, created_at=row.created_at,
    )


def seal_pending(conn: Connection, *, max_rows: int | None = None) -> int:
    """Chain every currently-committed unsealed row, in (created_at, id)
    order, under the advisory lock. Must be called inside a transaction on
    `conn` (e.g. `with engine.begin() as conn:`); the lock is released when
    that transaction ends. Returns the number of rows sealed."""
    conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": ac.ADVISORY_LOCK_KEY})
    head = conn.execute(
        select(_T.c.chain_seq, _T.c.entry_hash)
        .where(_T.c.chain_seq.is_not(None))
        .order_by(_T.c.chain_seq.desc())
        .limit(1)
    ).first()
    seq = head.chain_seq if head else 0
    prev = head.entry_hash if head else ac.GENESIS_HASH

    sealed = 0
    now = utcnow()
    while max_rows is None or sealed < max_rows:
        batch = _SEAL_BATCH if max_rows is None else min(_SEAL_BATCH, max_rows - sealed)
        rows = conn.execute(
            select(_T).where(_T.c.chain_seq.is_(None))
            .order_by(_T.c.created_at, _T.c.id).limit(batch)
        ).all()
        if not rows:
            break
        for row in rows:
            seq += 1
            h = ac.entry_hash(_fields(row), seq=seq, prev_hash=prev)
            conn.execute(
                update(_T).where(_T.c.id == row.id, _T.c.chain_seq.is_(None)).values(
                    chain_seq=seq, prev_hash=prev, entry_hash=h,
                    hash_version=ac.HASH_VERSION, sealed_at=now,
                )
            )
            prev = h
            sealed += 1
    return sealed


def seal_pending_now(bind) -> int:
    """Run one sealing transaction on its own connection. Never raises --
    a failure leaves rows unsealed (reported by verify, sealed by the next
    run) and is logged; it must never turn an already-committed business
    request into an error."""
    engine = getattr(bind, "engine", bind)
    try:
        with engine.begin() as conn:
            return seal_pending(conn)
    except Exception:  # pragma: no cover - defensive; exercised via logs only
        log.warning("audit chain sealing failed; rows remain unsealed", exc_info=True)
        return 0


def _iter_sealed(db: Session, start_seq: int) -> Iterator:
    last = start_seq - 1
    while True:
        rows = db.execute(
            select(_T).where(_T.c.chain_seq > last)
            .order_by(_T.c.chain_seq).limit(_VERIFY_PAGE)
        ).all()
        if not rows:
            return
        yield from rows
        last = rows[-1].chain_seq


def verify_chain(
    db: Session, *, expected_head_seq: int | None = None, expected_head_hash: str | None = None,
    after_seq: int = 0, after_hash: str = ac.GENESIS_HASH,
) -> ac.VerifyResult:
    """Recompute every sealed entry from seq 1. Detects: content edits
    (hash mismatch), deletions/insertions (sequence gap or prev_hash
    mismatch), re-linking. If an externally-recorded head is supplied
    (`expected_head_seq`/`expected_head_hash`), also detects tail
    truncation or a wholesale recomputed chain -- the one class of
    tampering the chain cannot catch on its own.

    Hardening -- incremental mode: `after_seq`/`after_hash` start the walk
    after a TRUSTED point (the latest HMAC-verified checkpoint, whose row
    the caller has confirmed still carries `after_hash`). Only rows after
    it are recomputed. Rows at or before it are NOT re-hashed in this mode:
    an edit to an old row that was not followed by a recompute is only
    caught by a full verification, so run full verification on a schedule
    too (docs/LIFELINK_AUDIT_CHAIN.md)."""
    expected_seq, expected_prev = after_seq + 1, after_hash
    checked = 0
    first_break: ac.ChainBreak | None = None
    anchor_seen = expected_head_seq is None

    for row in _iter_sealed(db, after_seq + 1):
        reason = ac.check_link(
            expected_seq=expected_seq, expected_prev=expected_prev,
            seq=row.chain_seq, prev_hash=row.prev_hash, stored_hash=row.entry_hash,
            hash_version=row.hash_version, fields=_fields(row),
        )
        if reason is not None:
            first_break = ac.ChainBreak(seq=row.chain_seq, audit_id=str(row.id), reason=reason)
            break
        if expected_head_seq is not None and row.chain_seq == expected_head_seq:
            anchor_seen = True
            if row.entry_hash != expected_head_hash:
                first_break = ac.ChainBreak(
                    seq=row.chain_seq, audit_id=str(row.id),
                    reason="entry_hash differs from the externally anchored head hash "
                           "(chain was recomputed after the anchor was taken)",
                )
                break
        checked += 1
        expected_seq = row.chain_seq + 1
        expected_prev = row.entry_hash

    if first_break is None and not anchor_seen and (
        expected_head_seq is None or expected_head_seq > after_seq
    ):
        first_break = ac.ChainBreak(
            seq=expected_head_seq, audit_id=None,
            reason="anchored head entry is missing (chain truncated after the anchor was taken)",
        )

    unsealed = db.scalar(
        select(text("count(*)")).select_from(_T).where(_T.c.chain_seq.is_(None))
    ) or 0
    return ac.VerifyResult(
        ok=first_break is None,
        checked=checked,
        head_seq=expected_seq - 1 if (checked or after_seq) else None,
        head_hash=expected_prev if (checked or after_seq) else None,
        unsealed=int(unsealed),
        first_break=first_break,
    )
