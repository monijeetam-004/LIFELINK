"""Audit finding E4 (2026-10-31): tamper-evidence claims must match the
configuration actually running.

The cryptographic behaviour (recomputation, truncation, forged/edited
checkpoints, key rotation, incremental mode) is covered by
tests/test_hardening_audit_chain.py. This file covers what E4 added: fail
closed when checkpoints are required but missing, an honest
`tamper_evidence` level on every verification, readiness reporting (never
the key), and checkpoints written by the deadline monitor."""
from __future__ import annotations

import pytest

from app.core.config import Settings, settings
from app.db.session import SessionLocal
from app.services import audit_checkpoint_service
from app.workers import deadline_monitor
from tests.helpers import auth_client

KEY = "e4-test-checkpoint-key-0123456789abcdef"


@pytest.fixture
def checkpoints(tmp_path, monkeypatch):
    path = tmp_path / "audit-checkpoints.log"
    monkeypatch.setattr(settings, "audit_checkpoint_key", KEY)
    monkeypatch.setattr(settings, "audit_checkpoint_key_id", "k1")
    monkeypatch.setattr(settings, "audit_checkpoint_retired_keys", "")
    monkeypatch.setattr(settings, "audit_checkpoint_path", str(path))
    return path


def test_required_but_missing_refuses_to_start():
    with pytest.raises(ValueError, match="AUDIT_CHECKPOINT_REQUIRED"):
        Settings(audit_checkpoint_required=True)
    ok = Settings(audit_checkpoint_required=True, audit_checkpoint_key=KEY,
                  audit_checkpoint_path="/var/lib/x/log")
    assert ok.audit_checkpoints_enabled


def test_verification_states_what_it_can_vouch_for(checkpoints):
    admin, _ = auth_client("ADMIN")
    body = admin.post("/admin/audit-log/verify").json()
    # checkpoints configured but none written yet: internal consistency only
    assert body["tamper_evidence"] == "INTERNAL_CONSISTENCY_ONLY" and body["warning"]
    assert admin.post("/admin/audit-log/checkpoint").status_code in (200, 201)
    body = admin.post("/admin/audit-log/verify").json()
    assert body["ok"] and body["tamper_evidence"] == "CHECKPOINTED" and body["warning"] is None
    head = admin.get("/admin/audit-log/head").json()
    body = admin.post("/admin/audit-log/verify", params={
        "anchor_seq": head["head_seq"], "anchor_hash": head["head_hash"]}).json()
    assert body["tamper_evidence"] == "EXTERNALLY_ANCHORED"


def test_unconfigured_verification_carries_the_warning():
    assert not settings.audit_checkpoints_enabled
    admin, _ = auth_client("ADMIN")
    body = admin.post("/admin/audit-log/verify").json()
    assert body["tamper_evidence"] == "INTERNAL_CONSISTENCY_ONLY"
    assert "recomputes the whole chain" in body["warning"]


def test_readiness_reports_checkpoints_without_the_key_and_fails_closed(
        checkpoints, client, monkeypatch):
    r = client.get("/health/ready")
    assert r.status_code == 200
    assert r.json()["audit_checkpoints"]["enabled"] is True
    assert KEY not in r.text
    # a required checkpoint log that no longer verifies -> not ready
    monkeypatch.setattr(settings, "audit_checkpoint_required", True)
    admin, _ = auth_client("ADMIN")
    admin.post("/admin/audit-log/checkpoint")
    lines = checkpoints.read_text().splitlines()
    checkpoints.write_text(lines[0].replace('"seq":', '"seq":9') + "\n")
    r = client.get("/health/ready")
    assert r.status_code == 503 and r.json()["audit_checkpoints"]["warning"]


def test_readiness_warns_when_disabled(client):
    body = client.get("/health/ready").json()
    assert body["audit_checkpoints"]["enabled"] is False
    assert "DISABLED" in body["audit_checkpoints"]["warning"]


def test_deadline_monitor_writes_due_checkpoints(checkpoints, monkeypatch):
    auth_client("ADMIN")  # creates some audit activity
    monkeypatch.setattr(settings, "audit_checkpoint_interval_minutes", 15)
    db = SessionLocal()
    try:
        from app.services import audit_chain_service

        audit_chain_service.seal_pending_now(db.get_bind())
        run = deadline_monitor.run_once(db)
        assert run.audit_checkpoint and run.audit_checkpoint.startswith("checkpoint at seq")
        # not due again within the interval
        assert deadline_monitor.run_once(db).audit_checkpoint is None
        assert audit_checkpoint_service.verify_against_db(db).ok
    finally:
        db.close()


def test_every_compose_service_that_reads_env_has_a_consistent_checkpoint_config():
    """Container clean start 2026-10-31: the worker read AUDIT_CHECKPOINT_KEY
    from .env but had no AUDIT_CHECKPOINT_PATH, so Settings() refused to load
    and the worker exited. Every service that loads .env must either get a
    path (it writes checkpoints) or have the key explicitly blanked."""
    import os

    import yaml

    compose = yaml.safe_load(open(os.path.join(os.path.dirname(__file__), "..",
                                               "docker-compose.yml")))
    readers = {n: s for n, s in compose["services"].items() if s.get("env_file")}
    assert {"api", "worker", "deadline-monitor"} <= set(readers)
    for name, svc in readers.items():
        env = svc.get("environment") or {}
        path = env.get("AUDIT_CHECKPOINT_PATH")
        if path:
            assert "AUDIT_CHECKPOINT_KEY" in env, name
            assert any("lifelink-audit" in v for v in svc.get("volumes", [])), name
        else:
            assert env.get("AUDIT_CHECKPOINT_KEY") == "", (
                f"{name} would inherit the key from .env without a checkpoint path")
    # the writers are exactly the API and the deadline monitor
    writers = {n for n, s in readers.items() if (s.get("environment") or {}).get(
        "AUDIT_CHECKPOINT_PATH")}
    assert writers == {"api", "deadline-monitor"}
