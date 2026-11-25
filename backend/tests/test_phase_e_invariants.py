"""Phase E -- cross-domain invariants (28-38), checked against the real
database after a real end-to-end scenario that exercises every Phase E
area at once, plus proofs that each check actually detects a violation
(a check that can never fail proves nothing).
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text

from app.core import audit_guard
from app.db.session import SessionLocal, engine
from app.services import invariant_check
from app.services.audit_chain_service import verify_chain
from tests.helpers import auth_client
from tests.test_phase_d_shipment import _create_shipment, _two_facilities


def _violations() -> dict:
    db = SessionLocal()
    try:
        return invariant_check.violations(db)
    finally:
        db.close()


def _chain_ok() -> bool:
    db = SessionLocal()
    try:
        r = verify_chain(db)
        return r.ok and r.unsealed == 0
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


def test_end_to_end_scenario_holds_every_invariant(db_session) -> None:
    """Partial -> full fulfilment across two hospitals, a donation linked
    to a facility and verified there, a certificate, a document
    resubmission, a failed shipment returned to origin, and an admin
    chain verification -- then every invariant and the audit chain."""
    origin, oid, dest, did = _two_facilities(db_session)
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 6})
    dest.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 2})

    patient, _ = auth_client("PATIENT")
    rid = patient.post(
        "/blood-requests", json={"blood_type": "O_NEG", "city": "Metro", "units_needed": 3}
    ).json()["id"]
    def alloc(c, n):
        return c.post(
            f"/hospitals/me/requests/{rid}/allocate",
            json={"blood_type": "O_NEG", "units": n},
        )

    assert alloc(origin, 1).status_code == 200
    assert patient.get(f"/blood-requests/{rid}").json()["status"] == "PARTIALLY_FULFILLED"
    assert alloc(dest, 2).status_code == 200
    assert patient.get(f"/blood-requests/{rid}").json()["status"] == "FULFILLED"
    assert alloc(dest, 1).status_code == 409

    donor, _ = auth_client("DONOR")
    donor.put("/donors/me", json={"blood_type": "O_NEG"})
    don = donor.post(
        "/donors/me/donations",
        json={"donated_on": date.today().isoformat(), "units": 1, "hospital_id": oid},
    ).json()
    assert donor.post("/certificates", json={"donation_id": don["id"]}).status_code == 409
    assert (
        origin.post(f"/donations/{don['id']}/verify", json={"note": "register ok"}).status_code
        == 200
    )
    assert donor.post("/certificates", json={"donation_id": don["id"]}).status_code == 201

    admin, _ = auth_client("ADMIN")
    files = [("files", ("id.pdf", b"%PDF-1.4 a", "application/pdf"))]
    v1 = donor.post("/verifications", data={"kind": "DONOR_IDENTITY"}, files=files).json()
    admin.post(f"/admin/verifications/{v1['id']}/reject", json={"reason": "blurry"})
    v2 = donor.post("/verifications", data={"kind": "DONOR_IDENTITY"}, files=files).json()
    assert v2["version"] == 2

    body = _create_shipment(origin, oid, did, units=2).json()
    sid, codes = body["id"], sorted(i["unit_code"] for i in body["items"])
    origin.post(f"/shipments/{sid}/dispatch", json={})
    origin.post(f"/shipments/{sid}/fail", json={"reason": "breakdown"})
    r = origin.post(
        f"/shipments/{sid}/reconcile",
        json={
            "outcome": "RETURNED_TO_ORIGIN",
            "note": "All bags back in validated cooler",
            "unit_codes": codes,
            "cold_chain_maintained": True,
        },
    )
    assert r.status_code == 200, r.text

    assert admin.post("/admin/audit-log/verify").json()["ok"] is True
    assert _violations() == {}
    assert _chain_ok()


# --------------------------------------------------------------------------- #
# Each check detects what it claims to (corrupt directly, expect a hit)
# --------------------------------------------------------------------------- #
def _request_id() -> str:
    patient, _ = auth_client("PATIENT")
    return patient.post(
        "/blood-requests", json={"blood_type": "O_NEG", "city": "Metro", "units_needed": 3}
    ).json()["id"]


def test_check_29_detects_status_quantity_disagreement() -> None:
    rid = _request_id()
    _sql("UPDATE blood_requests SET fulfilled_units = 2 WHERE id = :id", id=rid)
    assert 29 in _violations()


def test_check_30_detects_per_hospital_over_attribution(db_session) -> None:
    origin, oid, dest, did = _two_facilities(db_session)
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 3})
    rid = _request_id()
    origin.post(f"/hospitals/me/requests/{rid}/allocate", json={"blood_type": "O_NEG", "units": 1})
    _sql("UPDATE hospital_request_responses SET units_allocated = 3 WHERE request_id = :id", id=rid)
    assert 30 in _violations()


def test_check_32_detects_self_review() -> None:
    donor, _ = auth_client("DONOR")
    d = donor.post(
        "/donors/me/donations", json={"donated_on": date.today().isoformat(), "units": 1}
    ).json()
    _sql(
        "UPDATE donations SET verification_status='VERIFIED', verified_by_user_id=donor_user_id, "
        "verified_at=now() WHERE id = :id",
        id=d["id"],
    )
    v = _violations()
    assert 32 in v and 31 not in v


def test_check_34_detects_failed_shipment_without_reconciliation(db_session) -> None:
    origin, oid, dest, did = _two_facilities(db_session)
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 2})
    sid = _create_shipment(origin, oid, did, units=1).json()["id"]
    origin.post(f"/shipments/{sid}/dispatch", json={})
    origin.post(f"/shipments/{sid}/fail", json={"reason": "x"})
    assert _violations() == {}
    _sql("UPDATE shipments SET reconciliation_status = NULL WHERE id = :id", id=sid)
    assert 34 in _violations()


def test_check_37_detects_chain_gap() -> None:
    auth_client("ADMIN")  # writes + seals at least the login audit rows
    _sql(
        "UPDATE audit_logs SET chain_seq = chain_seq + 100 WHERE chain_seq = "
        "(SELECT MAX(chain_seq) FROM audit_logs)"
    )
    assert 37 in _violations()


@pytest.mark.parametrize("check", invariant_check.CHECKS, ids=lambda c: f"inv{c.number}")
def test_every_check_is_valid_sql_and_holds_on_empty_db(check) -> None:
    db = SessionLocal()
    try:
        assert db.execute(text(check.sql)).all() == []
    finally:
        db.close()
