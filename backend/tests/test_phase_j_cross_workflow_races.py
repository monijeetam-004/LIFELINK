"""Phase J -- concurrency races the earlier phases did not cover.

Existing real-thread races (Phases 10-35, D, E, F) cover: double
reserve/allocate, release vs allocate, reservation vs lazy expiry,
shipment double-dispatch / double-receive / dispatch-vs-cancel /
double-create / allocation-vs-shipment, request races A-D, concurrent
partials, concurrent verify, concurrent resubmission, reconciliation
restock-once, audit-chain fork, gap-free event publication.

Added here, at the forks those tests do not reach:

  R1  receive vs fail on an ARRIVED shipment (ARRIVED has two outgoing
      edges: RECEIVED and FAILED).
  R2  verify vs reject on the same donation (two reviewers deciding
      opposite things at the same moment). VERIFIED<->REJECTED is a legal,
      audited correction (Phase E), so both calls can succeed -- the
      requirement is that they SERIALIZE: the final state is the decision
      recorded last in the audit chain, and the certificate gate follows it.
  R3  the Phase G admin facility detail -- which now WRITES (lazy expiry
      sweep) -- racing the facility's own reserve and allocate on the
      same stock: no deadlock, no 500, no double count.
  R4  many concurrent unit additions + reservations on one facility while
      the hospital's own inventory GET (also sweeping) runs.

Every scenario uses real PostgreSQL and real threads behind a barrier,
each thread with its own TestClient/session, repeated for several
rounds, and ends with the full domain invariant checker
(`app.services.invariant_check.violations`) -- the same checks the
operational `scripts/check_invariants.py` runs -- returning no violations.
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, date, datetime, timedelta
from functools import partial

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.audit_log import AuditLog
from app.models.blood_unit import BloodUnit
from app.models.donation import Donation
from app.models.enums import BloodUnitState as US
from app.models.enums import ShipmentReconciliationStatus as RS
from app.models.enums import ShipmentStatus as SS
from app.models.hospital import HospitalProfile
from app.models.shipment import Shipment
from app.services import invariant_check
from tests.helpers import auth_client

ROUNDS = 4


def _no_violations() -> None:
    db = SessionLocal()
    try:
        bad = invariant_check.violations(db)
    finally:
        db.close()
    assert bad == {}, bad


def _race(*calls):
    """Run each zero-arg callable in its own thread, released together."""
    barrier = threading.Barrier(len(calls))
    out: list = [None] * len(calls)
    errors: list = []

    def run(i, fn):
        try:
            barrier.wait()
            out[i] = fn()
        except Exception as exc:  # surfaced below, never swallowed
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate(calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads), "a racing request never returned (deadlock?)"
    assert errors == [], errors
    return out


def _facility(name: str, verified: bool = False):
    c, _ = auth_client("HOSPITAL")
    r = c.put("/hospitals/me", json={"name": f"{name}-{uuid.uuid4().hex[:6]}", "city": "Metro"})
    assert r.status_code == 200, r.text
    fid = r.json()["id"]
    if verified:
        db = SessionLocal()
        try:
            db.get(HospitalProfile, uuid.UUID(fid)).verified = True
            db.commit()
        finally:
            db.close()
    return c, fid


# --------------------------------------------------------------------------- #
# R1 -- receive vs fail on an ARRIVED shipment
# --------------------------------------------------------------------------- #


def _arrived_shipment():
    origin, oid = _facility("R1-origin")
    dest, did = _facility("R1-dest", verified=True)
    added = origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 3})
    assert added.status_code == 200
    r = origin.post(
        "/shipments",
        json={
            "origin_facility_id": oid,
            "destination_facility_id": did,
            "items": [{"blood_type": "O_NEG", "units": 2}],
        },
    )
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    for step, actor in (("dispatch", origin), ("in-transit", origin), ("arrive", dest)):
        resp = actor.post(f"/shipments/{sid}/{step}", json={})
        assert resp.status_code == 200, (step, resp.text)
    return origin, oid, dest, did, sid


def test_r1_receive_vs_fail_on_arrived_shipment_has_exactly_one_outcome() -> None:
    outcomes = set()
    for _ in range(ROUNDS):
        origin, oid, dest, did, sid = _arrived_shipment()
        rec, fail = _race(
            partial(dest.post, f"/shipments/{sid}/receive", json={}),
            partial(origin.post, f"/shipments/{sid}/fail", json={"reason": "cold chain alarm"}),
        )
        codes = sorted([rec.status_code, fail.status_code])
        assert codes == [200, 409], (rec.text, fail.text)

        db = SessionLocal()
        try:
            sh = db.get(Shipment, uuid.UUID(sid))
            dest_units = db.scalars(
                select(BloodUnit).where(BloodUnit.hospital_id == uuid.UUID(did))
            ).all()
            if rec.status_code == 200:
                outcomes.add("received")
                assert sh.status == SS.RECEIVED
                assert sh.reconciliation_status is None
                assert len(dest_units) == 2
            else:
                outcomes.add("failed")
                assert sh.status == SS.FAILED
                assert sh.reconciliation_status == RS.REQUIRED
                assert dest_units == []  # nothing transferred on failure
            origin_states = sorted(
                u.state.value
                for u in db.scalars(
                    select(BloodUnit).where(BloodUnit.hospital_id == uuid.UUID(oid))
                ).all()
            )
            # 2 units left on the shipment (ALLOCATED either way), 1 stays AVAILABLE.
            assert origin_states == ["ALLOCATED", "ALLOCATED", "AVAILABLE"]
        finally:
            db.close()
        _no_violations()
    assert outcomes  # at least one real outcome observed; both are legal


# --------------------------------------------------------------------------- #
# R2 -- verify vs reject on the same donation
# --------------------------------------------------------------------------- #


def test_r2_verify_vs_reject_same_donation_serializes_and_last_decision_wins() -> None:
    for _ in range(ROUNDS):
        donor, _ = auth_client("DONOR")
        donor.put("/donors/me", json={"blood_type": "O_NEG"})
        d = donor.post(
            "/donors/me/donations",
            json={
                "donated_on": date.today().isoformat(),
                "units": 1,
                "hospital_name": "Free Text Clinic",
            },
        )
        assert d.status_code == 201, d.text
        did = d.json()["id"]
        a1, _ = auth_client("ADMIN")
        a2, _ = auth_client("ADMIN")
        v, r = _race(
            partial(a1.post, f"/donations/{did}/verify", json={}),
            partial(a2.post, f"/donations/{did}/reject", json={"note": "no record found"}),
        )
        # Each acts on the state left by the other (row lock), so neither is
        # a same-state duplicate: both succeed, one after the other.
        assert (v.status_code, r.status_code) == (200, 200), (v.text, r.text)
        db = SessionLocal()
        try:
            status = db.get(Donation, uuid.UUID(did)).verification_status.value
            decisions = db.scalars(
                select(AuditLog.action)
                .where(
                    AuditLog.target_id == uuid.UUID(did),
                    AuditLog.action.in_(["donation.verified", "donation.rejected"]),
                )
                .order_by(AuditLog.chain_seq)
            ).all()
        finally:
            db.close()
        assert sorted(decisions) == ["donation.rejected", "donation.verified"]
        assert (
            status
            == {"donation.verified": "VERIFIED", "donation.rejected": "REJECTED"}[decisions[-1]]
        )
        # The certificate gate follows the winner.
        cert = donor.post("/certificates", json={"donation_id": did})
        assert cert.status_code == (201 if status == "VERIFIED" else 409), cert.text
        _no_violations()


# --------------------------------------------------------------------------- #
# R3 -- admin facility detail (writing sweep) vs the facility's own writes
# --------------------------------------------------------------------------- #


def _expire_some(fid: str, n: int) -> None:
    db = SessionLocal()
    try:
        units = db.scalars(
            select(BloodUnit)
            .where(BloodUnit.hospital_id == uuid.UUID(fid), BloodUnit.state == US.AVAILABLE)
            .limit(n)
        ).all()
        for u in units:
            u.expires_at = datetime.now(UTC) - timedelta(hours=1)
        db.commit()
    finally:
        db.close()


def test_r3_admin_sweep_vs_hospital_reserve_and_allocate_no_deadlock_no_double_count() -> None:
    admin, _ = auth_client("ADMIN")
    for _ in range(ROUNDS):
        hosp, fid = _facility("R3")
        added = hosp.post("/hospitals/me/inventory/add", json={"blood_type": "A_POS", "units": 8})
        assert added.status_code == 200
        _expire_some(fid, 3)  # 5 genuinely usable remain
        detail, reserve, allocate, detail2 = _race(
            partial(admin.get, f"/admin/facilities/{fid}"),
            partial(
                hosp.post,
                "/hospitals/me/inventory/reserve",
                json={"blood_type": "A_POS", "units": 2},
            ),
            partial(
                hosp.post,
                "/hospitals/me/inventory/allocate",
                json={"blood_type": "A_POS", "units": 2},
            ),
            partial(admin.get, f"/admin/facilities/{fid}"),
        )
        for resp in (detail, reserve, allocate, detail2):
            assert resp.status_code in (200, 409), resp.text  # never a 500
        assert detail.status_code == 200 and detail2.status_code == 200

        db = SessionLocal()
        try:
            states = [
                u.state
                for u in db.scalars(
                    select(BloodUnit).where(BloodUnit.hospital_id == uuid.UUID(fid))
                ).all()
            ]
        finally:
            db.close()
        # Expired units are never handed out, whichever request ran first.
        assert states.count(US.ALLOCATED) <= 5
        final = admin.get(f"/admin/facilities/{fid}").json()
        line = [x for x in final["inventory"] if x["blood_type"] == "A_POS"][0]
        assert line["units"] == states.count(US.AVAILABLE) + states.count(US.RESERVED)
        assert line["reserved_units"] == states.count(US.RESERVED)
        _no_violations()


# --------------------------------------------------------------------------- #
# R4 -- many concurrent adds/reserves while the hospital's own GET sweeps
# --------------------------------------------------------------------------- #


def test_r4_concurrent_adds_reserves_and_sweeping_reads_keep_aggregates_exact() -> None:
    hosp, fid = _facility("R4")
    added = hosp.post("/hospitals/me/inventory/add", json={"blood_type": "B_NEG", "units": 4})
    assert added.status_code == 200
    _expire_some(fid, 2)
    calls = []
    for _ in range(3):
        calls.append(
            partial(
                hosp.post, "/hospitals/me/inventory/add", json={"blood_type": "B_NEG", "units": 2}
            )
        )
        calls.append(
            partial(
                hosp.post,
                "/hospitals/me/inventory/reserve",
                json={"blood_type": "B_NEG", "units": 1},
            )
        )
        calls.append(partial(hosp.get, "/hospitals/me/inventory/components"))
    results = _race(*calls)
    assert all(r.status_code in (200, 409) for r in results), [
        r.text for r in results if r.status_code >= 500
    ]
    adds_ok = sum(1 for r in results[0::3] if r.status_code == 200)
    assert adds_ok == 3  # additions never conflict

    db = SessionLocal()
    try:
        states = [
            u.state
            for u in db.scalars(
                select(BloodUnit).where(BloodUnit.hospital_id == uuid.UUID(fid))
            ).all()
        ]
    finally:
        db.close()
    assert len(states) == 4 + 3 * 2
    comp = [
        c
        for c in hosp.get("/hospitals/me/inventory/components").json()
        if c["blood_type"] == "B_NEG" and c["component"] == "WHOLE_BLOOD"
    ][0]
    assert comp["units"] == states.count(US.AVAILABLE) + states.count(US.RESERVED)
    assert comp["reserved_units"] == states.count(US.RESERVED)
    assert states.count(US.EXPIRED) == 2
    _no_violations()
