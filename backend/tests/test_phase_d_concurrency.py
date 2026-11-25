"""Phase D Section 12 -- real PostgreSQL, real-thread concurrency races for
the shipment lifecycle. No mocks: every test here reuses the same real
Postgres-backed `TestClient` across multiple real `threading.Thread`s,
exactly the pattern already established by
`tests/test_phase34_blood_unit_lifecycle.py`'s own concurrency section and
`tests/test_concurrency.py`.

The five races the Phase D spec names, and how each maps onto this
implementation's actual state machine:

  (A) Two users dispatch the same shipment simultaneously.
      -> test_two_simultaneous_dispatches_exactly_one_succeeds

  (B) Two receivers receive the same shipment simultaneously.
      -> test_two_simultaneous_receives_exactly_one_inventory_transfer

  (C) "Receipt vs cancellation race -> deterministic valid outcome."
      In THIS design, CANCELLED is reachable only from CREATED and
      RECEIVED only from ARRIVED -- the two preconditions are never both
      satisfiable on the same shipment at the same time (a shipment
      cannot be CREATED and ARRIVED simultaneously), so a literal
      receive-vs-cancel race cannot occur here by construction, not by
      omission. The real fork in this state machine is CREATED's two
      outgoing edges (DISPATCHED vs CANCELLED) -- two actors racing to
      either dispatch or cancel the same still-CREATED shipment. That is
      what this test exercises, as the closest genuine analogue: exactly
      one of {dispatch, cancel} wins, the other gets a deterministic 409,
      and the resulting inventory state matches whichever one actually won.
      -> test_dispatch_vs_cancel_race_on_a_created_shipment

  (D) The same unit (more precisely, the same limited pool of units)
      claimed by two shipments created at once -> only one claim can
      succeed for the contended units.
      -> test_two_concurrent_shipment_creations_cannot_both_claim_the_same_units

  (E) Allocation (a direct, non-shipment dispatch) vs shipment creation
      concurrently -> no unit simultaneously promised to both.
      -> test_direct_allocation_vs_shipment_creation_cannot_double_commit_stock
"""
from __future__ import annotations

import threading
import uuid

from sqlalchemy import select

from app.models.blood_unit import BloodUnit
from app.models.enums import BloodUnitState as S
from app.models.hospital import HospitalInventory, HospitalProfile
from tests.helpers import auth_client


def _facility(name: str):
    c, _ = auth_client("HOSPITAL")
    r = c.put("/hospitals/me", json={"name": name, "city": "Metro"})
    assert r.status_code == 200, r.text
    return c, r.json()["id"]


def _verify(db_session, facility_id: str) -> None:
    hp = db_session.get(HospitalProfile, uuid.UUID(facility_id))
    hp.verified = True
    db_session.commit()


def _two_facilities(db_session):
    origin, oid = _facility("Conc Origin")
    dest, did = _facility("Conc Dest")
    _verify(db_session, did)
    return origin, oid, dest, did


def _available(db_session, hospital_id: str, blood_type: str = "O_NEG") -> HospitalInventory:
    return db_session.scalar(
        select(HospitalInventory).where(
            HospitalInventory.hospital_id == uuid.UUID(hospital_id),
            HospitalInventory.blood_type == blood_type,
        )
    )


def _create_shipment(client, oid: str, did: str, units: int, blood_type: str = "O_NEG"):
    return client.post(
        "/shipments",
        json={
            "origin_facility_id": oid, "destination_facility_id": did,
            "items": [{"blood_type": blood_type, "units": units}],
        },
    )


# --------------------------------------------------------------------------- #
# Race A -- two simultaneous dispatches of the same shipment
# --------------------------------------------------------------------------- #

def test_two_simultaneous_dispatches_exactly_one_succeeds(db_session):
    origin, oid, dest, did = _two_facilities(db_session)
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 5})
    sid = _create_shipment(origin, oid, did, units=3).json()["id"]

    results: list[int] = []
    barrier = threading.Barrier(2)

    def _dispatch() -> None:
        barrier.wait()
        r = origin.post(f"/shipments/{sid}/dispatch", json={})
        results.append(r.status_code)

    threads = [threading.Thread(target=_dispatch) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [200, 409], results
    db_session.expire_all()
    row = _available(db_session, oid)
    assert row.units == 2  # dispatched exactly once, never twice
    allocated = db_session.scalars(
        select(BloodUnit).where(
            BloodUnit.hospital_id == uuid.UUID(oid), BloodUnit.state == S.ALLOCATED,
        )
    ).all()
    assert len(allocated) == 3  # never double-allocated by the loser


# --------------------------------------------------------------------------- #
# Race B -- two simultaneous receipts of the same shipment
# --------------------------------------------------------------------------- #

def test_two_simultaneous_receives_exactly_one_inventory_transfer(db_session):
    origin, oid, dest, did = _two_facilities(db_session)
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 3})
    sid = _create_shipment(origin, oid, did, units=3).json()["id"]
    origin.post(f"/shipments/{sid}/dispatch", json={})
    origin.post(f"/shipments/{sid}/in-transit", json={})
    dest.post(f"/shipments/{sid}/arrive", json={})

    results: list[int] = []
    barrier = threading.Barrier(2)

    def _receive() -> None:
        barrier.wait()
        r = dest.post(f"/shipments/{sid}/receive", json={})
        results.append(r.status_code)

    threads = [threading.Thread(target=_receive) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [200, 409], results
    db_session.expire_all()
    dest_units = db_session.scalars(
        select(BloodUnit).where(BloodUnit.hospital_id == uuid.UUID(did))
    ).all()
    assert len(dest_units) == 3  # not 6 -- the loser never created a second transfer
    dest_row = _available(db_session, did)
    assert dest_row.units == 3


# --------------------------------------------------------------------------- #
# Race C -- the real fork in this state machine: dispatch vs cancel, both
# only legal from CREATED (see module docstring for why a literal
# receive-vs-cancel race cannot occur in this design).
# --------------------------------------------------------------------------- #

def test_dispatch_vs_cancel_race_on_a_created_shipment(db_session):
    origin, oid, dest, did = _two_facilities(db_session)
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 3})
    sid = _create_shipment(origin, oid, did, units=3).json()["id"]

    results: dict[str, int] = {}
    barrier = threading.Barrier(2)

    def _dispatch() -> None:
        barrier.wait()
        r = origin.post(f"/shipments/{sid}/dispatch", json={})
        results["dispatch"] = r.status_code

    def _cancel() -> None:
        barrier.wait()
        r = origin.post(f"/shipments/{sid}/cancel", json={"reason": "racing"})
        results["cancel"] = r.status_code

    threads = [threading.Thread(target=_dispatch), threading.Thread(target=_cancel)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly one of the two legal CREATED-state transitions wins; the
    # other sees a non-CREATED status and gets a deterministic 409.
    assert sorted(results.values()) == [200, 409], results

    db_session.expire_all()
    row = _available(db_session, oid)
    units = db_session.scalars(
        select(BloodUnit).where(BloodUnit.hospital_id == uuid.UUID(oid))
    ).all()
    if results["dispatch"] == 200:
        assert row.units == 0 and row.reserved_units == 0
        assert all(u.state == S.ALLOCATED for u in units)
    else:
        assert results["cancel"] == 200
        assert row.units == 3 and row.reserved_units == 0
        assert all(u.state == S.AVAILABLE for u in units)
    # Whichever won, the outcome is internally consistent -- never a mix of
    # "some units allocated, some released" from the same race.


# --------------------------------------------------------------------------- #
# Race D -- two shipments created at once cannot both claim the same,
# limited pool of units.
# --------------------------------------------------------------------------- #

def test_two_concurrent_shipment_creations_cannot_both_claim_the_same_units(db_session):
    origin, oid, dest, did = _two_facilities(db_session)
    other_dest, other_did = _facility("Conc Other Dest")
    _verify(db_session, other_did)
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 5})

    results: list[int] = []
    barrier = threading.Barrier(2)

    def _create(destination_id: str, units: int) -> None:
        barrier.wait()
        r = _create_shipment(origin, oid, destination_id, units=units)
        results.append(r.status_code)

    threads = [
        threading.Thread(target=_create, args=(did, 3)),
        threading.Thread(target=_create, args=(other_did, 3)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 5 units on hand, two concurrent requests for 3 each (6 total) can
    # never both fully succeed.
    assert sorted(results) == [201, 409], results
    db_session.expire_all()
    row = _available(db_session, oid)
    assert row.reserved_units == 3  # exactly one shipment's reservation, never both/overlapping
    units = db_session.scalars(
        select(BloodUnit).where(
            BloodUnit.hospital_id == uuid.UUID(oid), BloodUnit.state == S.RESERVED,
        )
    ).all()
    assert len(units) == 3
    assert len({u.reserved_for_shipment_id for u in units}) == 1  # all earmarked for one shipment


# --------------------------------------------------------------------------- #
# Race E -- a direct inventory allocation vs a shipment reservation,
# concurrently, cannot double-commit the same stock.
# --------------------------------------------------------------------------- #

def test_direct_allocation_vs_shipment_creation_cannot_double_commit_stock(db_session):
    origin, oid, dest, did = _two_facilities(db_session)
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 5})

    results: dict[str, int] = {}
    barrier = threading.Barrier(2)

    def _direct_allocate() -> None:
        barrier.wait()
        r = origin.post(
            "/hospitals/me/inventory/allocate", json={"blood_type": "O_NEG", "units": 3}
        )
        results["allocate"] = r.status_code

    def _create() -> None:
        barrier.wait()
        r = _create_shipment(origin, oid, did, units=3)
        results["create"] = r.status_code

    threads = [threading.Thread(target=_direct_allocate), threading.Thread(target=_create)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 5 on hand, two concurrent 3-unit commitments (6 total) can never both
    # fully succeed -- regardless of which mechanism (direct ALLOCATE vs
    # shipment RESERVE) got there first.
    # Exactly one side wins; either may (direct ALLOCATE answers 200, a
    # created shipment 201) -- the branches below check each outcome.
    assert (results["allocate"], results["create"]) in {(200, 409), (409, 201)}, results

    db_session.expire_all()
    row = _available(db_session, oid)
    if results["allocate"] == 200:
        # 5 - 3 allocated = 2 on hand, 0 reserved (the shipment create lost).
        assert row.units == 2 and row.reserved_units == 0
    else:
        assert results["create"] == 201
        # 5 on hand (nothing allocated), 3 reserved for the shipment.
        assert row.units == 5 and row.reserved_units == 3
    total_committed = (5 - row.units) + row.reserved_units
    assert total_committed == 3  # exactly one side's worth of stock, never both
