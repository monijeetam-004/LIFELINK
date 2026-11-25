"""Phase E -- quantity-aware blood-request fulfilment.

Three layers, all real:
  1. Pure unit tests of `app.core.request_fulfillment` (no DB).
  2. HTTP tests of the partial -> full progression, over-allocation
     rejection (with zero inventory side effects), and the per-hospital
     breakdown invariant.
  3. Real-PostgreSQL concurrency races (threads + a barrier, each thread
     its own TestClient/session -- nothing mocked):

     Race A -- two hospitals each try to allocate the FINAL remaining
               units at the same moment. Exactly one succeeds; the other
               gets a clean 409 and its inventory is untouched.
     Race B -- two partial allocations that together fit land at the same
               moment. Both succeed; no lost update -- fulfilled_units is
               the exact sum and the bucket is correct.
     Race C -- an allocation races the requester's cancel. Whatever the
               serialization order, the outcome is one of the two legal
               histories and inventory matches it exactly.
     Race D -- an allocation races a hospital's manual "/fulfill". Final
               state is FULFILLED with fulfilled_units == units_needed,
               never above it, and the per-hospital breakdown still sums.

Invariant checked after every scenario (INV-E1/E2 in LIFELINK_INVARIANTS.md):
  0 <= fulfilled_units <= units_needed, and
  sum(HospitalRequestResponse.units_allocated) <= fulfilled_units, with
  equality whenever every unit was supplied through a hospital path.
"""
from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy import func, select

from app.core import request_fulfillment as rf
from app.db.session import SessionLocal
from app.models.blood_request import BloodRequest
from app.models.enums import BloodRequestStatus as S
from app.models.hospital import HospitalRequestResponse
from tests.helpers import auth_client


# --------------------------------------------------------------------------- #
# 1. Pure unit tests
# --------------------------------------------------------------------------- #
def test_validate_allocation_accepts_exact_remaining() -> None:
    rf.validate_allocation(4, 1, 3)


@pytest.mark.parametrize("fulfilled,units", [(0, 5), (3, 2), (4, 1)])
def test_validate_allocation_rejects_over_allocation(fulfilled: int, units: int) -> None:
    with pytest.raises(rf.OverAllocationError):
        rf.validate_allocation(4, fulfilled, units)


@pytest.mark.parametrize("fulfilled", [-1, 5])
def test_validate_allocation_refuses_already_corrupt_state(fulfilled: int) -> None:
    with pytest.raises(ValueError):
        rf.validate_allocation(4, fulfilled, 1)


def test_validate_allocation_rejects_non_positive_units() -> None:
    with pytest.raises(ValueError):
        rf.validate_allocation(4, 0, 0)


def test_bucket_and_target_status() -> None:
    assert rf.bucket_for(4, 1) == S.PARTIALLY_FULFILLED
    assert rf.bucket_for(4, 4) == S.FULFILLED
    assert rf.target_status_after_allocation(S.PENDING, 4, 1) == S.PARTIALLY_FULFILLED
    assert rf.target_status_after_allocation(S.MATCHING, 4, 4) == S.FULFILLED
    assert rf.target_status_after_allocation(S.PARTIALLY_FULFILLED, 4, 3) is None
    assert rf.target_status_after_allocation(S.PARTIALLY_FULFILLED, 4, 4) == S.FULFILLED
    assert rf.target_status_after_allocation(S.PENDING, 4, 0) is None
    assert rf.remaining_units(4, 1) == 3 and rf.remaining_units(4, 9) == 0


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _hospital(stock: int, name: str | None = None):
    c, _ = auth_client("HOSPITAL")
    c.put("/hospitals/me", json={"name": name or f"H-{uuid.uuid4().hex[:6]}", "city": "Metro"})
    if stock:
        r = c.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": stock})
        assert r.status_code == 200, r.text
    return c


def _stock(c) -> int:
    return next(i for i in c.get("/hospitals/me/inventory").json()
                if i["blood_type"] == "O_NEG")["units"]


def _request(units: int):
    patient, _ = auth_client("PATIENT")
    r = patient.post("/blood-requests", json={
        "blood_type": "O_NEG", "city": "Metro", "units_needed": units,
    })
    assert r.status_code == 201, r.text
    return patient, r.json()["id"]


def _alloc(c, rid: str, units: int):
    return c.post(f"/hospitals/me/requests/{rid}/allocate",
                  json={"blood_type": "O_NEG", "units": units})


def _db_state(rid: str) -> tuple[S, int, int, int]:
    """(status, fulfilled_units, units_needed, sum(units_allocated)) from a
    fresh session -- the database's own view, not the API's."""
    db = SessionLocal()
    try:
        req = db.get(BloodRequest, uuid.UUID(rid))
        total = db.scalar(
            select(func.coalesce(func.sum(HospitalRequestResponse.units_allocated), 0))
            .where(HospitalRequestResponse.request_id == req.id)
        )
        return req.status, req.fulfilled_units, req.units_needed, int(total)
    finally:
        db.close()


def _assert_invariants(rid: str, *, hospital_only: bool = True) -> None:
    _, fulfilled, needed, total = _db_state(rid)
    assert 0 <= fulfilled <= needed
    assert total <= fulfilled
    if hospital_only:
        assert total == fulfilled


def _race(*calls):
    barrier = threading.Barrier(len(calls))
    results: list = [None] * len(calls)

    def run(i, fn):
        barrier.wait()
        results[i] = fn()

    threads = [threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate(calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


# --------------------------------------------------------------------------- #
# 2. HTTP behaviour
# --------------------------------------------------------------------------- #
def test_partial_then_full_progression() -> None:
    hosp = _hospital(10)
    patient, rid = _request(4)

    r = _alloc(hosp, rid, 1)
    assert r.status_code == 200 and r.json()["units_allocated"] == 1
    d = patient.get(f"/blood-requests/{rid}").json()
    assert d["status"] == "PARTIALLY_FULFILLED" and d["fulfilled_units"] == 1
    assert set(d["allowed_transitions"]) == {"FULFILLED", "CANCELLED", "EXPIRED"}

    # Same bucket -- an event, not a transition; still PARTIALLY_FULFILLED.
    assert _alloc(hosp, rid, 2).status_code == 200
    d = patient.get(f"/blood-requests/{rid}").json()
    assert d["status"] == "PARTIALLY_FULFILLED" and d["fulfilled_units"] == 3
    assert [e["event_type"] for e in d["events"]].count("hospital_allocated") == 2

    assert _alloc(hosp, rid, 1).status_code == 200
    d = patient.get(f"/blood-requests/{rid}").json()
    assert d["status"] == "FULFILLED" and d["fulfilled_units"] == 4
    assert d["fulfilled_at"] is not None
    assert _stock(hosp) == 6
    _assert_invariants(rid)


def test_over_allocation_is_409_with_no_inventory_side_effect() -> None:
    hosp = _hospital(10)
    patient, rid = _request(2)
    r = _alloc(hosp, rid, 3)
    assert r.status_code == 409
    assert "remaining" in r.json()["detail"]
    assert _stock(hosp) == 10
    assert patient.get(f"/blood-requests/{rid}").json()["fulfilled_units"] == 0
    _assert_invariants(rid)


def test_partially_fulfilled_cannot_reopen_to_pending() -> None:
    """A donor withdrawing must not drag a partially-met request back to
    PENDING -- units already dispatched are real."""
    hosp = _hospital(5)
    patient, rid = _request(3)
    _alloc(hosp, rid, 1)
    d = patient.get(f"/blood-requests/{rid}").json()
    assert "PENDING" not in d["allowed_transitions"]
    assert "MATCHING" not in d["allowed_transitions"]


def test_partially_fulfilled_request_can_be_cancelled_and_keeps_its_history() -> None:
    hosp = _hospital(5)
    patient, rid = _request(3)
    _alloc(hosp, rid, 1)
    r = patient.post(f"/blood-requests/{rid}/cancel", json={"reason": "transferred"})
    assert r.status_code == 200 and r.json()["status"] == "CANCELLED"
    # Dispatched units are not silently returned to stock on cancel.
    assert _stock(hosp) == 4
    assert _db_state(rid)[1] == 1
    _assert_invariants(rid)


def test_two_hospitals_partial_contributions_sum() -> None:
    a, b = _hospital(5), _hospital(5)
    patient, rid = _request(3)
    assert _alloc(a, rid, 1).status_code == 200
    assert _alloc(b, rid, 2).status_code == 200
    assert patient.get(f"/blood-requests/{rid}").json()["status"] == "FULFILLED"
    _assert_invariants(rid)


def test_manual_fulfil_after_partial_attributes_gap_to_acting_hospital() -> None:
    a, b = _hospital(5), _hospital(0)
    patient, rid = _request(4)
    _alloc(a, rid, 1)
    r = b.post(f"/hospitals/me/requests/{rid}/fulfill")
    assert r.status_code == 200 and r.json()["units_allocated"] == 3
    status, fulfilled, needed, total = _db_state(rid)
    assert status == S.FULFILLED and fulfilled == needed == 4 and total == 4


def test_requester_fulfil_path_brings_quantity_to_full() -> None:
    """The donor-matched path (POST /blood-requests/{id}/fulfill) has no
    hospital to attribute units to -- fulfilled_units still becomes
    units_needed, so the breakdown is <= rather than ==."""
    hosp = _hospital(5)
    patient, rid = _request(3)
    _alloc(hosp, rid, 1)
    r = patient.post(f"/blood-requests/{rid}/fulfill")
    assert r.status_code == 200, r.text
    status, fulfilled, needed, total = _db_state(rid)
    assert status == S.FULFILLED and fulfilled == needed == 3 and total == 1
    _assert_invariants(rid, hospital_only=False)


def test_client_cannot_set_fulfilled_units_directly() -> None:
    patient, _ = auth_client("PATIENT")
    r = patient.post("/blood-requests", json={
        "blood_type": "O_NEG", "city": "Metro", "units_needed": 3, "fulfilled_units": 3,
        "status": "FULFILLED",
    })
    assert r.status_code == 201
    assert r.json()["fulfilled_units"] == 0 and r.json()["status"] == "PENDING"


# --------------------------------------------------------------------------- #
# 3. Real-Postgres concurrency races
# --------------------------------------------------------------------------- #
def test_race_a_two_hospitals_race_for_final_units() -> None:
    a, b = _hospital(5), _hospital(5)
    _, rid = _request(3)
    assert _alloc(a, rid, 1).status_code == 200  # 2 remaining

    ra, rb = _race(lambda: _alloc(a, rid, 2), lambda: _alloc(b, rid, 2))
    codes = sorted([ra.status_code, rb.status_code])
    assert codes == [200, 409], (ra.text, rb.text)

    status, fulfilled, needed, _ = _db_state(rid)
    assert status == S.FULFILLED and fulfilled == needed == 3
    # Loser's inventory untouched; winner's reduced exactly once.
    stocks = sorted([_stock(a), _stock(b)])
    if ra.status_code == 200:
        assert (_stock(a), _stock(b)) == (2, 5)
    else:
        assert (_stock(a), _stock(b)) == (4, 3)
    assert sum(stocks) == 7
    _assert_invariants(rid)


def test_race_b_concurrent_partials_no_lost_update() -> None:
    a, b = _hospital(5), _hospital(5)
    _, rid = _request(5)
    ra, rb = _race(lambda: _alloc(a, rid, 1), lambda: _alloc(b, rid, 2))
    assert ra.status_code == 200 and rb.status_code == 200, (ra.text, rb.text)
    status, fulfilled, _, _ = _db_state(rid)
    assert fulfilled == 3
    assert status == S.PARTIALLY_FULFILLED
    assert _stock(a) == 4 and _stock(b) == 3
    _assert_invariants(rid)


def test_race_b_many_concurrent_partials_never_over_allocate() -> None:
    hospitals = [_hospital(3) for _ in range(5)]
    _, rid = _request(4)
    results = _race(*[(lambda h=h: _alloc(h, rid, 1)) for h in hospitals])
    oks = [r for r in results if r.status_code == 200]
    assert len(oks) == 4 and sum(r.status_code == 409 for r in results) == 1
    status, fulfilled, needed, _ = _db_state(rid)
    assert status == S.FULFILLED and fulfilled == needed == 4
    assert sum(3 - _stock(h) for h in hospitals) == 4
    _assert_invariants(rid)


def test_race_c_allocate_vs_cancel() -> None:
    hosp = _hospital(5)
    patient, rid = _request(3)
    r_alloc, r_cancel = _race(
        lambda: _alloc(hosp, rid, 2),
        lambda: patient.post(f"/blood-requests/{rid}/cancel", json={"reason": "race"}),
    )
    status, fulfilled, _, _ = _db_state(rid)
    assert status == S.CANCELLED
    assert r_cancel.status_code == 200
    if r_alloc.status_code == 200:
        # allocate serialized first, then cancel: dispatched units stay recorded.
        assert fulfilled == 2 and _stock(hosp) == 3
    else:
        # cancel serialized first: allocate refused, nothing deducted.
        assert r_alloc.status_code == 409
        assert fulfilled == 0 and _stock(hosp) == 5
    _assert_invariants(rid)


def test_race_d_allocate_vs_manual_fulfil() -> None:
    a, b = _hospital(5), _hospital(5)
    _, rid = _request(3)
    r_alloc, r_fulfil = _race(
        lambda: _alloc(a, rid, 2),
        lambda: b.post(f"/hospitals/me/requests/{rid}/fulfill"),
    )
    assert r_fulfil.status_code == 200, r_fulfil.text
    status, fulfilled, needed, total = _db_state(rid)
    assert status == S.FULFILLED and fulfilled == needed == 3 and total == 3
    if r_alloc.status_code == 200:
        # allocate first (2), then fulfil declared the 1-unit gap.
        assert _stock(a) == 3 and r_fulfil.json()["units_allocated"] == 1
    else:
        # fulfil first (whole 3), allocate refused against a FULFILLED request.
        assert r_alloc.status_code == 409 and _stock(a) == 5
        assert r_fulfil.json()["units_allocated"] == 3
    _assert_invariants(rid)


def test_db_check_constraint_blocks_over_fulfilment() -> None:
    """Defence in depth: even a code path that bypassed the route's check
    could not persist fulfilled_units > units_needed."""
    from sqlalchemy.exc import IntegrityError

    _, rid = _request(2)
    db = SessionLocal()
    try:
        req = db.get(BloodRequest, uuid.UUID(rid))
        req.fulfilled_units = 3
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
        req = db.get(BloodRequest, uuid.UUID(rid))
        req.fulfilled_units = -1
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Phase E regression: an allocation must never consume a reservation held
# for a DIFFERENT shipment/request (found via an intermittent failure of
# tests/test_phase_d_concurrency.py::
# test_direct_allocation_vs_shipment_creation_cannot_double_commit_stock in
# the full regression run -- deterministic reproduction below).
# --------------------------------------------------------------------------- #
def test_direct_allocation_cannot_take_units_reserved_for_a_shipment(db_session) -> None:
    from tests.test_phase_d_shipment import _create_shipment, _two_facilities

    origin, oid, dest, did = _two_facilities(db_session)
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 5})
    sid = _create_shipment(origin, oid, did, units=3).json()["id"]  # reserves 3 of 5

    r = origin.post("/hospitals/me/inventory/allocate", json={"blood_type": "O_NEG", "units": 3})
    assert r.status_code == 409, r.text  # only 2 are genuinely unreserved
    assert "reserved for other" in r.json()["detail"]
    ok = origin.post("/hospitals/me/inventory/allocate", json={"blood_type": "O_NEG", "units": 2})
    assert ok.status_code == 200

    # The shipment still owns its 3 reserved units and can dispatch them.
    assert origin.post(f"/shipments/{sid}/dispatch", json={}).status_code == 200


def test_request_allocation_cannot_take_another_requests_reservation() -> None:
    hosp = _hospital(4)
    _, rid_a = _request(3)
    _, rid_b = _request(3)
    r = hosp.post("/hospitals/me/inventory/reserve",
                  json={"blood_type": "O_NEG", "units": 3, "request_id": rid_a})
    assert r.status_code == 200, r.text
    # Request B can only get the 1 genuinely free unit, not A's held 3.
    assert _alloc(hosp, rid_b, 2).status_code == 409
    assert _alloc(hosp, rid_b, 1).status_code == 200
    # Request A's own allocation consumes its own reservation.
    assert _alloc(hosp, rid_a, 3).status_code == 200
    _assert_invariants(rid_a)
    _assert_invariants(rid_b)


def test_generic_reservation_is_still_consumable() -> None:
    """Context-free holds keep their pre-Phase-E behaviour."""
    hosp = _hospital(3)
    assert hosp.post("/hospitals/me/inventory/reserve",
                     json={"blood_type": "O_NEG", "units": 3}).status_code == 200
    r = hosp.post("/hospitals/me/inventory/allocate", json={"blood_type": "O_NEG", "units": 3})
    assert r.status_code == 200, r.text
