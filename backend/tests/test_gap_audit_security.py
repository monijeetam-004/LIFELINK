"""Gap elimination section 26: audit-chain attack simulations and admin
access logging. The attacker here holds the table-owner role (the strongest
in-database attacker the design is meant to DETECT, not prevent)."""

from __future__ import annotations

from sqlalchemy import select, text

from app.core import audit_guard
from app.db.session import SessionLocal, engine
from app.models.audit_log import AuditLog
from tests.helpers import auth_client
from tests.test_phase_e_audit_chain import _verify, _write


def _owner(sql: str) -> None:
    """Run tampering SQL with the guard switched off (owner rights)."""
    with engine.begin() as conn:
        conn.execute(text(audit_guard.DISABLE_SQL))
        conn.execute(text(sql))
        conn.execute(text(audit_guard.ENABLE_SQL))


def test_owner_modifying_a_sealed_entry_is_detected():
    _write(5)
    assert _verify().ok
    _owner("UPDATE audit_logs SET action = 'innocent.action' WHERE chain_seq = 3")
    r = _verify()
    assert not r.ok and r.first_break.seq == 3


def test_owner_deleting_a_middle_entry_is_detected():
    _write(5)
    _owner("DELETE FROM audit_logs WHERE chain_seq = 2")
    r = _verify()
    assert not r.ok and r.first_break.seq is not None


def test_disabled_guard_trigger_is_reported_and_fails_verification():
    admin, _ = auth_client("ADMIN")
    ok = admin.post("/admin/audit-log/verify").json()
    assert ok["ok"] is True
    assert set(ok["guard"].values()) == {"ENABLED"}
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE audit_logs DISABLE TRIGGER {audit_guard.ROW_TRIGGER}"))
    try:
        bad = admin.post("/admin/audit-log/verify").json()
        assert bad["ok"] is False
        assert bad["guard"][audit_guard.ROW_TRIGGER] == "DISABLED"
        assert "append-only guard is not active" in bad["first_break"]["reason"]
    finally:
        with engine.begin() as conn:
            conn.execute(text(audit_guard.ENABLE_SQL))
    with SessionLocal() as db:
        assert (
            db.scalar(select(AuditLog).where(AuditLog.action == "admin.audit_chain_verify_failed"))
            is not None
        )


def test_admin_reads_of_the_audit_log_are_themselves_audited():
    admin, _ = auth_client("ADMIN")
    assert admin.get("/admin/audit-log").status_code == 200
    with SessionLocal() as db:
        row = db.scalar(select(AuditLog).where(AuditLog.action == "admin.audit_log_viewed"))
        assert row is not None and row.actor_user_id is not None


def test_non_admins_cannot_read_or_verify_the_audit_log():
    for role in ("PATIENT", "DONOR", "HOSPITAL", "FAMILY"):
        c, _ = auth_client(role)
        assert c.get("/admin/audit-log").status_code == 403
        assert c.post("/admin/audit-log/verify").status_code == 403
