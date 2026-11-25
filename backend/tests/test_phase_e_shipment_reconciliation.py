"""Phase E -- failed-shipment reconciliation (the gap Phase D disclosed).

Proves the safety policy of POST /shipments/{id}/reconcile:
  * FAILED sets reconciliation_status=REQUIRED; non-failed shipments have none
  * only the origin facility or an admin may decide; destination/unrelated 403
  * exactly one decision, row-locked: a second (or concurrent) call is 409
  * RETURNED_TO_ORIGIN restocks only with full unit-code confirmation AND a
    cold-chain attestation; partial lists / no attestation are refused with
    zero inventory change
  * restocked units are NEW rows linked to the originals, keep the ORIGINAL
    expiry, and any unit already expired comes back EXPIRED (never AVAILABLE)
  * the original ALLOCATED rows are never mutated (ALLOCATED stays terminal)
  * QUARANTINED / REDIRECTED change no inventory
  * every decision is in the shipment timeline and the audit trail
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.blood_unit import BloodUnit
from app.models.enums import BloodUnitProvenance
from app.models.enums import BloodUnitState as S
from tests.helpers import auth_client
from tests.test_phase_d_shipment import (
    _available,
    _create_shipment,
    _facility,
    _two_facilities,
    _units,
)

NOTE = "Driver returned all bags in validated cooler"


def _failed_shipment(db_session, units: int = 3, stock: int = 5):
    origin, oid, dest, did = _two_facilities(db_session)
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": stock})
    body = _create_shipment(origin, oid, did, units=units).json()
    sid = body["id"]
    codes = sorted(i["unit_code"] for i in body["items"])
    assert origin.post(f"/shipments/{sid}/dispatch", json={}).status_code == 200
    r = origin.post(f"/shipments/{sid}/fail", json={"reason": "vehicle breakdown"})
    assert r.status_code == 200, r.text
    return origin, oid, dest, did, sid, codes


def _return_body(codes, **kw):
    body = {
        "outcome": "RETURNED_TO_ORIGIN",
        "note": NOTE,
        "unit_codes": codes,
        "cold_chain_maintained": True,
    }
    body.update(kw)
    return body


def test_failed_shipment_requires_reconciliation(db_session) -> None:
    origin, *_, sid, _ = _failed_shipment(db_session)
    body = origin.get(f"/shipments/{sid}").json()
    assert body["reconciliation_status"] == "REQUIRED"
    assert body["reconciled_at"] is None


def test_non_failed_shipment_has_no_reconciliation_and_cannot_be_reconciled(db_session) -> None:
    origin, oid, dest, did = _two_facilities(db_session)
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 2})
    sid = _create_shipment(origin, oid, did, units=1).json()["id"]
    assert origin.get(f"/shipments/{sid}").json()["reconciliation_status"] is None
    r = origin.post(f"/shipments/{sid}/reconcile", json={"outcome": "QUARANTINED", "note": NOTE})
    assert r.status_code == 409


def test_returned_to_origin_restocks_with_original_expiry_and_lineage(db_session) -> None:
    origin, oid, dest, did, sid, codes = _failed_shipment(db_session)
    db_session.expire_all()
    originals = {u.unit_code: u for u in _units(db_session, oid) if u.state == S.ALLOCATED}
    assert sorted(originals) == codes
    assert _available(db_session, oid).units == 2

    r = origin.post(f"/shipments/{sid}/reconcile", json=_return_body(codes))
    assert r.status_code == 200, r.text
    assert r.json()["reconciliation_status"] == "RETURNED_TO_ORIGIN"
    assert r.json()["reconciliation_note"] == NOTE

    db_session.expire_all()
    assert _available(db_session, oid).units == 5
    units = _units(db_session, oid)
    returned = [u for u in units if u.provenance == BloodUnitProvenance.TRANSFERRED_IN]
    assert len(returned) == 3
    by_source = {u.source_unit_id: u for u in returned}
    for code, orig in originals.items():
        new = by_source[orig.id]
        assert new.state == S.AVAILABLE
        assert new.expires_at == orig.expires_at  # no fresh shelf-life clock
        assert str(new.source_shipment_id) == sid
        assert new.unit_code != code
    # Originals untouched: still ALLOCATED (terminal).
    assert sum(1 for u in units if u.state == S.ALLOCATED) == 3


def test_returned_unit_already_past_expiry_comes_back_expired(db_session) -> None:
    origin, oid, dest, did, sid, codes = _failed_shipment(db_session, units=2)
    db_session.expire_all()
    stale = next(u for u in _units(db_session, oid) if u.state == S.ALLOCATED)
    stale.expires_at = datetime.now(UTC) - timedelta(hours=1)
    db_session.commit()

    r = origin.post(f"/shipments/{sid}/reconcile", json=_return_body(codes))
    assert r.status_code == 200, r.text
    db_session.expire_all()
    returned = [
        u for u in _units(db_session, oid) if u.provenance == BloodUnitProvenance.TRANSFERRED_IN
    ]
    assert sorted(u.state.value for u in returned) == ["AVAILABLE", "EXPIRED"]
    assert _available(db_session, oid).units == 3 + 1  # 3 never shipped + 1 usable return


def test_return_without_cold_chain_attestation_is_refused(db_session) -> None:
    origin, oid, dest, did, sid, codes = _failed_shipment(db_session)
    r = origin.post(
        f"/shipments/{sid}/reconcile", json=_return_body(codes, cold_chain_maintained=False)
    )
    assert r.status_code == 422
    db_session.expire_all()
    assert _available(db_session, oid).units == 2
    assert origin.get(f"/shipments/{sid}").json()["reconciliation_status"] == "REQUIRED"


def test_partial_or_wrong_unit_list_is_refused(db_session) -> None:
    origin, oid, dest, did, sid, codes = _failed_shipment(db_session)
    for bad in (codes[:-1], codes + ["LL-FAKE-1"], [codes[0]] * 3, []):
        r = origin.post(f"/shipments/{sid}/reconcile", json=_return_body(bad))
        assert r.status_code == 422, bad
    db_session.expire_all()
    assert _available(db_session, oid).units == 2


def test_quarantine_changes_no_inventory(db_session) -> None:
    origin, oid, dest, did, sid, codes = _failed_shipment(db_session)
    r = origin.post(
        f"/shipments/{sid}/reconcile",
        json={"outcome": "QUARANTINED", "note": "Cooler breached, bags destroyed"},
    )
    assert r.status_code == 200 and r.json()["reconciliation_status"] == "QUARANTINED"
    db_session.expire_all()
    assert _available(db_session, oid).units == 2
    assert sum(1 for u in _units(db_session, oid) if u.state == S.ALLOCATED) == 3


def test_redirected_records_facility_without_moving_units(db_session) -> None:
    origin, oid, dest, did, sid, codes = _failed_shipment(db_session)
    _, other_id = _facility("Diverted To")
    r = origin.post(
        f"/shipments/{sid}/reconcile",
        json={
            "outcome": "REDIRECTED",
            "note": "Diverted mid-route to nearer trauma centre",
            "redirected_to_facility_id": other_id,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["redirected_to_facility_id"] == other_id
    db_session.expire_all()
    assert _units(db_session, other_id) == []
    assert _available(db_session, oid).units == 2


def test_redirected_facility_only_valid_for_redirected(db_session) -> None:
    origin, oid, dest, did, sid, codes = _failed_shipment(db_session)
    r = origin.post(
        f"/shipments/{sid}/reconcile",
        json={
            "outcome": "QUARANTINED",
            "note": NOTE,
            "redirected_to_facility_id": did,
        },
    )
    assert r.status_code == 422


def test_note_is_mandatory_and_required_is_not_an_outcome(db_session) -> None:
    origin, oid, dest, did, sid, codes = _failed_shipment(db_session)
    assert (
        origin.post(
            f"/shipments/{sid}/reconcile", json={"outcome": "QUARANTINED", "note": "short"}
        ).status_code
        == 422
    )
    assert (
        origin.post(
            f"/shipments/{sid}/reconcile", json={"outcome": "REQUIRED", "note": NOTE}
        ).status_code
        == 422
    )


def test_only_origin_or_admin_can_reconcile(db_session) -> None:
    origin, oid, dest, did, sid, codes = _failed_shipment(db_session)
    stranger, _ = _facility("Stranger")
    assert dest.post(f"/shipments/{sid}/reconcile", json=_return_body(codes)).status_code == 403
    assert stranger.post(f"/shipments/{sid}/reconcile", json=_return_body(codes)).status_code == 403
    for role in ("DONOR", "PATIENT"):
        c, _ = auth_client(role)
        assert c.post(f"/shipments/{sid}/reconcile", json=_return_body(codes)).status_code == 403
    admin, _ = auth_client("ADMIN")
    assert (
        admin.post(
            f"/shipments/{sid}/reconcile", json={"outcome": "QUARANTINED", "note": NOTE}
        ).status_code
        == 200
    )


def test_second_decision_is_409(db_session) -> None:
    origin, oid, dest, did, sid, codes = _failed_shipment(db_session)
    assert origin.post(f"/shipments/{sid}/reconcile", json=_return_body(codes)).status_code == 200
    r = origin.post(f"/shipments/{sid}/reconcile", json=_return_body(codes))
    assert r.status_code == 409
    db_session.expire_all()
    assert _available(db_session, oid).units == 5  # restocked once, not twice


def test_concurrent_returns_restock_exactly_once(db_session) -> None:
    origin, oid, dest, did, sid, codes = _failed_shipment(db_session)
    admin, _ = auth_client("ADMIN")
    barrier = threading.Barrier(2)
    codes_out: list[int] = []

    def go(c):
        barrier.wait()
        codes_out.append(
            c.post(f"/shipments/{sid}/reconcile", json=_return_body(codes)).status_code
        )

    ts = [threading.Thread(target=go, args=(c,)) for c in (origin, admin)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(codes_out) == [200, 409], codes_out
    db_session.expire_all()
    assert _available(db_session, oid).units == 5
    assert (
        sum(
            1 for u in _units(db_session, oid) if u.provenance == BloodUnitProvenance.TRANSFERRED_IN
        )
        == 3
    )


def test_reconciliation_is_in_timeline_and_audit(db_session) -> None:
    origin, oid, dest, did, sid, codes = _failed_shipment(db_session)
    origin.post(f"/shipments/{sid}/reconcile", json=_return_body(codes))
    kinds = [e["event_type"] for e in origin.get(f"/shipments/{sid}/timeline").json()]
    assert "reconciled_returned_to_origin" in kinds
    entry = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "shipment.reconciled", AuditLog.target_id == uuid.UUID(sid)
        )
    )
    assert entry.meta["outcome"] == "RETURNED_TO_ORIGIN"
    assert entry.meta["cold_chain_maintained"] is True
    assert entry.meta["unit_codes"] == codes and entry.meta["restocked"] == 3
    types = [n["type"] for n in dest.get("/notifications").json()]
    assert "shipment.reconciled" in types


def test_returned_units_are_traceable_back_to_failed_shipment(db_session) -> None:
    origin, oid, dest, did, sid, codes = _failed_shipment(db_session, units=1)
    origin.post(f"/shipments/{sid}/reconcile", json=_return_body(codes))
    db_session.expire_all()
    new = db_session.scalar(
        select(BloodUnit).where(
            BloodUnit.hospital_id == uuid.UUID(oid),
            BloodUnit.provenance == BloodUnitProvenance.TRANSFERRED_IN,
        )
    )
    trace = origin.get(f"/shipments/units/{new.unit_code}").json()
    assert trace["source_shipment_id"] == sid
    assert trace["state"] == "AVAILABLE"
