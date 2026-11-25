"""Phase E -- tamper-evident audit hash chain.

All against the real Postgres test database. Proves:
  * every audit row written through `audit_service.record` is sealed into
    the chain right after its transaction commits (contiguous seq 1..N)
  * content edits, deletions, re-linking and reordering are detected, and
    the break is located at the right row
  * the honest limitation: a full recomputation of the chain, or a tail
    truncation, is NOT detectable without an external anchor -- and IS
    detected once an anchor (head seq + hash recorded elsewhere) is given
  * rolled-back audit rows never enter the chain
  * concurrency: many sessions committing audit rows at once never fork
    the chain (no duplicate seq, no gap, verify passes) -- the case the
    naive "SELECT latest hash then INSERT" design gets wrong
  * a committed-but-unsealed row is reported as unsealed, never as verified,
    and is picked up by the next sealing run
  * the admin endpoints are admin-only and record their own result
"""
from __future__ import annotations

import threading
import uuid

from sqlalchemy import func, select, text

from app.core import audit_chain as ac
from app.core import audit_guard
from app.db.session import SessionLocal, engine
from app.models.audit_log import AuditLog
from app.services import audit_chain_service, audit_service
from tests.helpers import auth_client


def _write(n: int, action: str = "test.event") -> list[uuid.UUID]:
    ids = []
    db = SessionLocal()
    try:
        for i in range(n):
            e = audit_service.record(db, action, meta={"i": i, "ref": uuid.uuid4()})
            ids.append(e.id)
        db.commit()
    finally:
        db.close()
    return ids


def _verify(**kw) -> ac.VerifyResult:
    db = SessionLocal()
    try:
        return audit_chain_service.verify_chain(db, **kw)
    finally:
        db.close()


def _seqs() -> list[int | None]:
    db = SessionLocal()
    try:
        return list(db.scalars(select(AuditLog.chain_seq).order_by(AuditLog.chain_seq)).all())
    finally:
        db.close()


def _sql(stmt: str, **params) -> None:
    """Tamper as an attacker holding the table OWNER role would: the
    append-only guard (app.core.audit_guard) is lifted for this statement.
    Without that, every one of these statements is refused -- see
    tests/test_hardening_audit_chain.py."""
    with engine.begin() as conn:
        conn.execute(text(audit_guard.DISABLE_SQL))
        conn.execute(text(stmt), params)
        conn.execute(text(audit_guard.ENABLE_SQL))


# --------------------------------------------------------------------------- #
# Sealing
# --------------------------------------------------------------------------- #
def test_rows_are_sealed_after_commit_and_chain_verifies() -> None:
    _write(5)
    assert _seqs() == [1, 2, 3, 4, 5]
    r = _verify()
    assert r.ok and r.checked == 5 and r.unsealed == 0 and r.head_seq == 5
    db = SessionLocal()
    try:
        first = db.scalar(select(AuditLog).where(AuditLog.chain_seq == 1))
        assert first.prev_hash == ac.GENESIS_HASH and first.hash_version == 1
        assert len(first.entry_hash) == 64 and first.sealed_at is not None
    finally:
        db.close()


def test_real_http_traffic_is_chained() -> None:
    admin, _ = auth_client("ADMIN")
    donor, _ = auth_client("DONOR")
    donor.get("/admin/users")  # role-denied -> audited
    admin.get("/admin/users")
    r = _verify()
    assert r.ok and r.checked >= 1 and r.unsealed == 0


def test_meta_is_normalized_and_forbidden_keys_still_stripped() -> None:
    db = SessionLocal()
    try:
        ref = uuid.uuid4()
        e = audit_service.record(db, "test.meta", meta={"ref": ref, "password": "x"})
        db.commit()
        db.refresh(e)
        assert e.meta == {"ref": str(ref)}
    finally:
        db.close()
    assert _verify().ok


def test_rolled_back_rows_never_enter_the_chain() -> None:
    _write(2)
    db = SessionLocal()
    try:
        audit_service.record(db, "test.rolled_back")
        db.rollback()
    finally:
        db.close()
    _write(1)
    db = SessionLocal()
    try:
        assert db.scalar(select(func.count()).select_from(AuditLog)
                         .where(AuditLog.action == "test.rolled_back")) == 0
    finally:
        db.close()
    assert _seqs() == [1, 2, 3] and _verify().ok


def test_unsealed_row_is_reported_then_sealed_by_next_run() -> None:
    _write(2)
    # A committed row that bypassed the after-commit hook (e.g. the process
    # died between commit and seal).
    _sql("INSERT INTO audit_logs (id, action, meta, created_at) "
         "VALUES (:id, 'test.straggler', '{}'::jsonb, now())", id=uuid.uuid4())
    r = _verify()
    assert r.ok and r.checked == 2 and r.unsealed == 1
    _write(1)  # any later commit seals stragglers too
    r = _verify()
    assert r.ok and r.checked == 4 and r.unsealed == 0


# --------------------------------------------------------------------------- #
# Tamper detection
# --------------------------------------------------------------------------- #
def test_content_edit_is_detected_at_the_right_row() -> None:
    _write(6)
    _sql("UPDATE audit_logs SET meta = '{\"i\": 999}'::jsonb WHERE chain_seq = 4")
    r = _verify()
    assert not r.ok
    assert r.first_break.seq == 4 and "content changed" in r.first_break.reason
    assert r.checked == 3


def test_action_rename_is_detected() -> None:
    _write(3)
    _sql("UPDATE audit_logs SET action = 'test.innocuous' WHERE chain_seq = 2")
    r = _verify()
    assert not r.ok and r.first_break.seq == 2


def test_deleted_row_is_detected() -> None:
    _write(5)
    _sql("DELETE FROM audit_logs WHERE chain_seq = 3")
    r = _verify()
    assert not r.ok and r.first_break.seq == 4 and "gap" in r.first_break.reason


def test_relinked_row_is_detected() -> None:
    """Delete row 3 and renumber 4->3 so the sequence looks contiguous:
    prev_hash no longer matches."""
    _write(5)
    _sql("DELETE FROM audit_logs WHERE chain_seq = 3")
    _sql("UPDATE audit_logs SET chain_seq = chain_seq - 1 WHERE chain_seq > 3")
    r = _verify()
    assert not r.ok and r.first_break.seq == 3 and "prev_hash" in r.first_break.reason


def test_hash_overwrite_alone_is_detected_downstream() -> None:
    """Editing a row AND fixing its own entry_hash still breaks the next
    row's prev_hash link."""
    _write(4)
    _sql("UPDATE audit_logs SET meta = '{\"i\": 7}'::jsonb WHERE chain_seq = 2")
    db = SessionLocal()
    try:
        row = db.scalar(select(AuditLog).where(AuditLog.chain_seq == 2))
        new_hash = ac.entry_hash(audit_chain_service._fields(row), seq=2, prev_hash=row.prev_hash)
    finally:
        db.close()
    _sql("UPDATE audit_logs SET entry_hash = :h WHERE chain_seq = 2", h=new_hash)
    r = _verify()
    assert not r.ok and r.first_break.seq == 3


def _recompute_whole_chain() -> None:
    """What an attacker with full DB write access could do."""
    with engine.begin() as conn:
        conn.execute(text(audit_guard.DISABLE_SQL))  # attacker holds the owner role
        rows = conn.execute(select(AuditLog.__table__).order_by(text("chain_seq"))).all()
        prev = ac.GENESIS_HASH
        for row in rows:
            h = ac.entry_hash(audit_chain_service._fields(row), seq=row.chain_seq, prev_hash=prev)
            conn.execute(text("UPDATE audit_logs SET prev_hash=:p, entry_hash=:h WHERE id=:id"),
                         {"p": prev, "h": h, "id": row.id})
            prev = h
        conn.execute(text(audit_guard.ENABLE_SQL))


def test_full_recompute_is_NOT_detectable_without_anchor_but_is_with_one() -> None:
    _write(4)
    anchor = _verify()
    _sql("UPDATE audit_logs SET meta = '{\"i\": 42}'::jsonb WHERE chain_seq = 2")
    _recompute_whole_chain()
    # Honest limitation: internally consistent again.
    assert _verify().ok
    # With the head recorded outside the DB beforehand, it is caught.
    r = _verify(expected_head_seq=anchor.head_seq, expected_head_hash=anchor.head_hash)
    assert not r.ok and "anchored head" in r.first_break.reason


def test_tail_truncation_needs_an_anchor_to_detect() -> None:
    _write(5)
    anchor = _verify()
    _sql("DELETE FROM audit_logs WHERE chain_seq >= 4")
    assert _verify().ok  # shorter chain is internally consistent
    r = _verify(expected_head_seq=anchor.head_seq, expected_head_hash=anchor.head_hash)
    assert not r.ok and "truncated" in r.first_break.reason


# --------------------------------------------------------------------------- #
# Concurrency (real Postgres)
# --------------------------------------------------------------------------- #
def test_concurrent_writers_never_fork_the_chain() -> None:
    n_threads, per_thread = 16, 5
    barrier = threading.Barrier(n_threads)
    errors: list[BaseException] = []

    def worker(t: int) -> None:
        db = SessionLocal()
        try:
            barrier.wait()
            for i in range(per_thread):
                audit_service.record(db, "test.concurrent", meta={"t": t, "i": i})
                db.commit()  # many small commits => many concurrent sealers
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)
        finally:
            db.close()

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors

    total = n_threads * per_thread
    assert _seqs() == list(range(1, total + 1))
    r = _verify()
    assert r.ok and r.checked == total and r.unsealed == 0


def test_concurrent_business_writes_with_row_locks_do_not_deadlock() -> None:
    """Business transactions that hold row locks while auditing (the
    pattern the in-transaction design would deadlock on) run concurrently
    with sealing without errors."""
    from tests.test_phase_e_fulfillment import _alloc, _hospital, _race, _request

    hospitals = [_hospital(3) for _ in range(6)]
    _, rid = _request(4)
    results = _race(*[(lambda h=h: _alloc(h, rid, 1)) for h in hospitals])
    assert sorted(r.status_code for r in results) == [200, 200, 200, 200, 409, 409]
    r = _verify()
    assert r.ok and r.unsealed == 0


# --------------------------------------------------------------------------- #
# Admin endpoints
# --------------------------------------------------------------------------- #
def test_admin_verify_and_head_endpoints() -> None:
    admin, _ = auth_client("ADMIN")
    _write(3)
    head = admin.get("/admin/audit-log/head").json()
    assert head["head_seq"] >= 3 and len(head["head_hash"]) == 64

    r = admin.post("/admin/audit-log/verify")
    assert r.status_code == 200 and r.json()["ok"] is True

    r = admin.post("/admin/audit-log/verify",
                   params={"anchor_seq": head["head_seq"], "anchor_hash": head["head_hash"]})
    assert r.status_code == 200 and r.json()["ok"] and r.json()["anchored"]

    items = admin.get("/admin/audit-log", params={"action": "admin.audit_chain_verified"}).json()
    assert items["items"] and items["items"][0]["chain_seq"] is not None


def test_admin_verify_reports_tampering_and_audits_failure() -> None:
    admin, _ = auth_client("ADMIN")
    _write(3)
    _sql("UPDATE audit_logs SET action = 'x' WHERE chain_seq = 2")
    body = admin.post("/admin/audit-log/verify").json()
    assert body["ok"] is False and body["first_break"]["seq"] == 2
    failed = admin.get("/admin/audit-log",
                       params={"action": "admin.audit_chain_verify_failed"}).json()["items"]
    assert failed and failed[0]["severity"] == "CRITICAL"


def test_anchor_params_must_come_together() -> None:
    admin, _ = auth_client("ADMIN")
    assert admin.post("/admin/audit-log/verify", params={"anchor_seq": 1}).status_code == 422


def test_non_admin_cannot_verify_or_read_head() -> None:
    for role in ("PATIENT", "DONOR", "HOSPITAL", "FAMILY"):
        c, _ = auth_client(role)
        assert c.post("/admin/audit-log/verify").status_code == 403
        assert c.get("/admin/audit-log/head").status_code == 403
