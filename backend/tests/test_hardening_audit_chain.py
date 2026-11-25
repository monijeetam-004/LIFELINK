"""Production hardening -- audit hash chain.

Threat model (docs/LIFELINK_AUDIT_CHAIN.md): the chain alone detects
modify / delete / insert / reorder of sealed entries; it cannot detect a
full recompute or tail truncation by someone with DB write access. This
round adds, and these tests prove:

1. An append-only guard in the database: UPDATE of sealed entries, DELETE,
   TRUNCATE and content edits of unsealed entries are refused for the
   application's ordinary statements (an owner can still lift it -- the
   tamper tests below do exactly that, as an attacker would).
2. HMAC-signed checkpoints written OUTSIDE the database. With them a full
   recompute and a truncation below the latest checkpoint ARE detected;
   forging, editing, removing or reordering checkpoints is detected; a
   rotated (retired) key still verifies old checkpoints.
3. Incremental verification from the latest verified checkpoint, and its
   stated limitation (an un-recomputed edit before the checkpoint is only
   found by full verification).
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core import audit_chain as ac
from app.core import audit_guard
from app.core.config import Settings, settings
from app.db.session import SessionLocal, engine
from app.models.audit_log import AuditLog
from app.models.user import User
from app.services import audit_chain_service, audit_checkpoint_service
from tests.helpers import auth_client
from tests.test_phase_e_audit_chain import _recompute_whole_chain, _sql, _verify, _write

KEY = "k" * 40


@pytest.fixture
def checkpoints(tmp_path, monkeypatch):
    path = tmp_path / "anchors" / "audit-checkpoints.jsonl"
    monkeypatch.setattr(settings, "audit_checkpoint_key", KEY)
    monkeypatch.setattr(settings, "audit_checkpoint_key_id", "k1")
    monkeypatch.setattr(settings, "audit_checkpoint_retired_keys", "")
    monkeypatch.setattr(settings, "audit_checkpoint_path", str(path))
    return path


def _checkpoint() -> audit_checkpoint_service.Checkpoint:
    db = SessionLocal()
    try:
        cp = audit_checkpoint_service.write_checkpoint(db)
        assert cp is not None
        return cp
    finally:
        db.close()


def _cp_verify() -> audit_checkpoint_service.CheckpointVerifyResult:
    db = SessionLocal()
    try:
        return audit_checkpoint_service.verify_against_db(db)
    finally:
        db.close()


def _refused(stmt: str, **params) -> str:
    with pytest.raises(DBAPIError) as exc:
        with engine.begin() as conn:
            conn.execute(text(stmt), params)
    return str(exc.value.orig)


# --------------------------------------------------------------------------- #
# 1. Append-only guard
# --------------------------------------------------------------------------- #
def test_guard_refuses_update_delete_truncate_of_audit_entries() -> None:
    _write(4)
    assert _verify().ok
    assert "sealed entry 2 cannot be modified" in _refused(
        "UPDATE audit_logs SET action = 'x' WHERE chain_seq = 2"
    )
    assert "sealed entry" in _refused("UPDATE audit_logs SET entry_hash = repeat('0', 64)")
    assert "DELETE of entry 3 refused" in _refused("DELETE FROM audit_logs WHERE chain_seq = 3")
    assert "TRUNCATE refused" in _refused("TRUNCATE audit_logs")
    assert "TRUNCATE refused" in _refused("TRUNCATE users CASCADE")
    r = _verify()
    assert r.ok and r.checked == 4


def test_guard_freezes_unsealed_content_but_lets_the_sealer_seal() -> None:
    db = SessionLocal()
    try:
        # Insert an unsealed row without triggering the after-commit sealer.
        db.execute(text(
            "INSERT INTO audit_logs (id, action, meta, created_at) "
            "VALUES (gen_random_uuid(), 'raw.insert', '{}'::jsonb, now())"
        ))
        db.commit()
    finally:
        db.close()
    assert "content cannot be modified" in _refused(
        "UPDATE audit_logs SET action = 'changed' WHERE chain_seq IS NULL"
    )
    assert "DELETE" in _refused("DELETE FROM audit_logs WHERE chain_seq IS NULL")
    assert audit_chain_service.seal_pending_now(engine) == 1
    assert _verify().ok


def test_hard_deleting_a_user_referenced_by_sealed_audit_is_refused() -> None:
    """The FK's ON DELETE SET NULL would rewrite a sealed entry (and break
    the chain). The guard refuses it; the app deactivates users instead."""
    c, email = auth_client("PATIENT")
    c.post("/auth/logout")
    db = SessionLocal()
    try:
        uid = db.scalar(select(User.id).where(User.email == email))
        assert db.scalar(
            select(AuditLog.id).where(
                AuditLog.actor_user_id == uid, AuditLog.chain_seq.is_not(None)
            )
        )
    finally:
        db.close()
    assert "sealed entry" in _refused("DELETE FROM users WHERE id = :u", u=uid)


# --------------------------------------------------------------------------- #
# 2. Signed checkpoints outside the database
# --------------------------------------------------------------------------- #
def test_checkpoint_is_signed_external_and_verifies(checkpoints) -> None:
    _write(5)
    cp = _checkpoint()
    assert checkpoints.exists()
    line = json.loads(checkpoints.read_text().splitlines()[0])
    assert line["seq"] == cp.seq == 5 and len(line["mac"]) == 64
    assert line["prev_mac"] == audit_checkpoint_service.GENESIS_MAC
    _write(2)
    cp2 = _checkpoint()
    assert cp2.prev_mac == cp.mac and cp2.seq == 7
    r = _cp_verify()
    assert r.ok and r.checked == 2 and r.latest_seq == 7


def test_full_recompute_is_detected_by_checkpoint(checkpoints) -> None:
    _write(6)
    _checkpoint()
    _sql("UPDATE audit_logs SET meta = '{\"i\": 999}'::jsonb WHERE chain_seq = 3")
    _recompute_whole_chain()
    assert _verify().ok  # the chain alone is fooled (the documented limitation)
    r = _cp_verify()
    assert not r.ok and "no longer has the hash in signed checkpoint 1" in r.reason


def test_truncation_below_checkpoint_is_detected(checkpoints) -> None:
    _write(6)
    _checkpoint()
    _sql("DELETE FROM audit_logs WHERE chain_seq >= 5")
    assert _verify().ok  # shorter but internally consistent
    r = _cp_verify()
    assert not r.ok and "audit entry 6 named by signed checkpoint 1 is missing" in r.reason


def test_reordering_rows_is_detected_by_the_chain() -> None:
    _write(5)
    _sql("UPDATE audit_logs SET chain_seq = -1 WHERE chain_seq = 2")
    _sql("UPDATE audit_logs SET chain_seq = 2 WHERE chain_seq = 3")
    _sql("UPDATE audit_logs SET chain_seq = 3 WHERE chain_seq = -1")
    r = _verify()
    assert not r.ok and r.first_break.seq == 2


def test_forged_edited_removed_or_reordered_checkpoints_are_detected(checkpoints) -> None:
    for _ in range(3):
        _write(2)
        _checkpoint()
    good = checkpoints.read_text().splitlines()

    def check(lines: list[str]) -> str:
        r = audit_checkpoint_service.verify_log(lines)
        assert not r.ok
        return r.reason

    # Edited: point checkpoint 2 at a different (recomputed) hash.
    edited = json.loads(good[1])
    edited["entry_hash"] = "f" * 64
    assert "signature invalid" in check([good[0], json.dumps(edited), good[2]])
    # Forged with a key the attacker chose.
    forged = json.loads(good[2])
    forged["seq"] = 999
    payload = audit_checkpoint_service._canonical(
        "k1", 999, forged["entry_hash"], forged["created_at"], forged["prev_mac"]
    )
    forged["mac"] = audit_checkpoint_service._mac(b"attacker-key" * 4, payload)
    assert "signature invalid" in check([good[0], good[1], json.dumps(forged)])
    # Removed from the middle; reordered.
    assert "prev_mac does not match" in check([good[0], good[2]])
    assert "prev_mac does not match" in check([good[1], good[0], good[2]])
    # Unknown key id; garbage.
    unknown = json.loads(good[0])
    unknown["key_id"] = "nope"
    assert "unknown checkpoint key id" in check([json.dumps(unknown)])
    assert "malformed" in check(["{not json"])
    # A broken log is never extended.
    checkpoints.write_text("\n".join([good[0], good[2]]) + "\n")
    with pytest.raises(RuntimeError, match="refusing to extend"):
        _checkpoint()


def test_rotated_key_keeps_old_checkpoints_verifiable(checkpoints, monkeypatch) -> None:
    _write(2)
    _checkpoint()
    monkeypatch.setattr(settings, "audit_checkpoint_retired_keys", f"k1={KEY}")
    monkeypatch.setattr(settings, "audit_checkpoint_key", "n" * 40)
    monkeypatch.setattr(settings, "audit_checkpoint_key_id", "k2")
    _write(2)
    cp = _checkpoint()
    assert cp.key_id == "k2"
    assert _cp_verify().ok
    # Without the retired key, the old checkpoint can no longer be verified.
    monkeypatch.setattr(settings, "audit_checkpoint_retired_keys", "")
    r = _cp_verify()
    assert not r.ok and "unknown checkpoint key id 'k1'" in r.reason


# --------------------------------------------------------------------------- #
# 3. Incremental verification + endpoints
# --------------------------------------------------------------------------- #
def test_incremental_verification_and_its_limitation(checkpoints) -> None:
    _write(20)
    cp = _checkpoint()
    _write(5)
    db = SessionLocal()
    try:
        r = audit_chain_service.verify_chain(db, after_seq=cp.seq, after_hash=cp.entry_hash)
    finally:
        db.close()
    assert r.ok and r.checked == 5 and r.head_seq == 25

    # An edit AFTER the checkpoint is caught incrementally.
    _sql("UPDATE audit_logs SET action = 'x' WHERE chain_seq = 23")
    db = SessionLocal()
    try:
        r = audit_chain_service.verify_chain(db, after_seq=cp.seq, after_hash=cp.entry_hash)
    finally:
        db.close()
    assert not r.ok and r.first_break.seq == 23
    _sql("UPDATE audit_logs SET action = 'test.event' WHERE chain_seq = 23")

    # An un-recomputed edit BEFORE the checkpoint is NOT caught incrementally
    # (stated limitation) -- and IS caught by full verification.
    _sql("UPDATE audit_logs SET action = 'x' WHERE chain_seq = 4")
    db = SessionLocal()
    try:
        inc = audit_chain_service.verify_chain(db, after_seq=cp.seq, after_hash=cp.entry_hash)
    finally:
        db.close()
    assert inc.ok
    full = _verify()
    assert not full.ok and full.first_break.seq == 4


def test_endpoints_checkpoint_and_verify_modes(checkpoints) -> None:
    admin, _ = auth_client("ADMIN")
    _write(3)
    r = admin.post("/admin/audit-log/checkpoint")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] and r.json()["checked"] == 1
    body = admin.post("/admin/audit-log/verify").json()
    assert body["ok"] and body["mode"] == "full"
    assert body["checkpoints"] == {
        "enabled": True, "ok": True, "checked": 1, "latest_seq": r.json()["latest_seq"],
        "reason": None,
    }
    inc = admin.post("/admin/audit-log/verify", params={"mode": "incremental"}).json()
    assert inc["ok"] and inc["mode"] == "incremental"
    assert inc["checked"] < body["checked"]
    assert admin.post(
        "/admin/audit-log/verify",
        params={"mode": "incremental", "anchor_seq": 1, "anchor_hash": "a" * 64},
    ).status_code == 422

    _sql("UPDATE audit_logs SET meta = '{\"i\": 7}'::jsonb WHERE chain_seq = 2")
    _recompute_whole_chain()
    bad = admin.post("/admin/audit-log/verify").json()
    assert not bad["ok"] and bad["checkpoints"]["ok"] is False
    assert bad["first_break"]["reason"].startswith("signed checkpoint check failed")

    for role in ("DONOR", "HOSPITAL", "PATIENT"):
        c, _ = auth_client(role)
        assert c.post("/admin/audit-log/checkpoint").status_code == 403


def test_endpoints_when_checkpoints_are_not_configured() -> None:
    assert not settings.audit_checkpoints_enabled
    admin, _ = auth_client("ADMIN")
    assert admin.post("/admin/audit-log/checkpoint").status_code == 409
    assert admin.post("/admin/audit-log/verify", params={"mode": "incremental"}).status_code == 409
    body = admin.post("/admin/audit-log/verify").json()
    assert body["ok"] and body["checkpoints"] == {
        "enabled": False, "ok": None, "checked": 0, "latest_seq": None, "reason": None,
    }


def test_checkpoint_config_validation() -> None:
    with pytest.raises(ValueError, match="must be set together"):
        Settings(audit_checkpoint_key=KEY, audit_checkpoint_path="")
    with pytest.raises(ValueError, match="at least 32"):
        Settings(audit_checkpoint_key="short", audit_checkpoint_path="/x")
    with pytest.raises(ValueError, match="differ from SECRET_KEY"):
        Settings(secret_key=KEY, audit_checkpoint_key=KEY, audit_checkpoint_path="/x")
    s = Settings(audit_checkpoint_key=KEY, audit_checkpoint_path="/x")
    assert s.audit_checkpoints_enabled


def test_chain_genesis_constant_unchanged() -> None:
    # Checkpoint verification relies on the chain's v1 hashing; guard it.
    assert ac.GENESIS_HASH == "0" * 64 and ac.HASH_VERSION == 1
    assert audit_guard.ROW_TRIGGER == "audit_logs_append_only"
