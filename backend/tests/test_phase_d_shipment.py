"""Phase D -- shipment / transport lifecycle + chain of custody.

Covers (via the real HTTP API, same convention as every other phase's
test file in this suite -- direct DB inspection only for state no public
endpoint exposes):

  * Creation reserves (not allocates) specific origin units; idempotent
    create (replay + conflicting-payload rejection), facility validation,
    insufficient-stock rejection.
  * The full happy-path lifecycle (CREATED -> DISPATCHED -> IN_TRANSIT ->
    ARRIVED -> RECEIVED), and that it performs a REAL inventory-ownership
    transfer: origin units end ALLOCATED (terminal), destination gets new
    TRANSFERRED_IN units, HospitalInventory aggregates agree with the real
    per-unit rows on both ends.
  * Every illegal transition the Phase D spec names explicitly is rejected
    (RECEIVED->DISPATCHED, CANCELLED->RECEIVED, FAILED->RECEIVED,
    dispatch-after-cancel, double-receive, mutate-after-terminal).
  * Cancellation (only from CREATED) releases the reservation; failure
    (only after dispatch) does NOT release the now-ALLOCATED units.
  * Invariant 23 (a unit belongs to at most one ACTIVE shipment) enforced
    at the DB level via the partial unique index, not just application
    logic.
  * Route disruption is informational and does not block or corrupt a
    shipment's own lifecycle (Section 16).
  * A unit that expires in transit is received honestly as EXPIRED, never
    counted as available stock (Section 15).
  * Authorization: only the origin/destination facility (or an admin) can
    act on a shipment.
  * Delay annotation, timeline ordering, unit-code traceability lookup.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.models.blood_unit import BloodUnit
from app.models.enums import BloodUnitProvenance
from app.models.enums import BloodUnitState as S
from app.models.enums import ShipmentStatus as SS
from app.models.hospital import HospitalInventory, HospitalProfile
from app.models.shipment import Shipment, ShipmentItem
from app.models.transport import TransportRoute
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
    origin, oid = _facility("Origin Hosp")
    dest, did = _facility("Destination Hosp")
    _verify(db_session, did)
    return origin, oid, dest, did


def _available(db_session, hospital_id: str, blood_type: str = "O_NEG") -> HospitalInventory | None:
    return db_session.scalar(
        select(HospitalInventory).where(
            HospitalInventory.hospital_id == uuid.UUID(hospital_id),
            HospitalInventory.blood_type == blood_type,
        )
    )


def _units(db_session, hospital_id: str, blood_type: str = "O_NEG") -> list[BloodUnit]:
    return list(
        db_session.scalars(
            select(BloodUnit).where(
                BloodUnit.hospital_id == uuid.UUID(hospital_id),
                BloodUnit.blood_type == blood_type,
            )
        ).all()
    )


def _create_shipment(
    origin, oid: str, did: str, units: int = 3, blood_type: str = "O_NEG", **extra
):
    payload = {
        "origin_facility_id": oid,
        "destination_facility_id": did,
        "items": [{"blood_type": blood_type, "units": units}],
    }
    payload.update(extra)
    return origin.post("/shipments", json=payload)


# --------------------------------------------------------------------------- #
# Creation
# --------------------------------------------------------------------------- #


def test_create_shipment_reserves_not_allocates(db_session):
    origin, oid, dest, did = _two_facilities(db_session)
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 5})

    r = _create_shipment(origin, oid, did, units=3)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "CREATED"
    assert len(body["items"]) == 3
    assert body["allowed_transitions"] == ["CANCELLED", "DISPATCHED"]

    db_session.expire_all()
    row = _available(db_session, oid)
    assert row.units == 5  # unchanged -- not yet dispatched
    assert row.reserved_units == 3
    units = _units(db_session, oid)
    reserved = [u for u in units if u.state == S.RESERVED]
    assert len(reserved) == 3
    assert all(str(u.reserved_for_shipment_id) == body["id"] for u in reserved)


def test_create_shipment_rejects_same_origin_and_destination(db_session):
    origin, oid, _dest, _did = _two_facilities(db_session)
    r = origin.post(
        "/shipments",
        json={
            "origin_facility_id": oid,
            "destination_facility_id": oid,
            "items": [{"blood_type": "O_NEG", "units": 1}],
        },
    )
    assert r.status_code == 400, r.text


def test_create_shipment_rejects_unverified_destination(db_session):
    origin, oid = _facility("Origin Only")
    dest, did = _facility("Unverified Dest")  # not verified
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 5})
    r = _create_shipment(origin, oid, did, units=1)
    assert r.status_code == 400, r.text


def test_create_shipment_rejects_shipping_from_someone_elses_facility(db_session):
    origin, oid, dest, did = _two_facilities(db_session)
    other, _other_id = _facility("Unrelated Hosp")
    r = other.post(
        "/shipments",
        json={
            "origin_facility_id": oid,
            "destination_facility_id": did,
            "items": [{"blood_type": "O_NEG", "units": 1}],
        },
    )
    assert r.status_code == 403, r.text


def test_create_shipment_insufficient_stock_is_409_and_reserves_nothing(db_session):
    origin, oid, dest, did = _two_facilities(db_session)
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 2})
    r = _create_shipment(origin, oid, did, units=5)
    assert r.status_code == 409, r.text
    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(Shipment)) == 0
    row = _available(db_session, oid)
    assert row.reserved_units == 0


def test_create_shipment_idempotent_replay_returns_same_shipment(db_session):
    origin, oid, dest, did = _two_facilities(db_session)
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 5})
    key = f"ship-{uuid.uuid4().hex}"
    r1 = _create_shipment(origin, oid, did, units=2, idempotency_key=key)
    assert r1.status_code == 201, r1.text
    r2 = _create_shipment(origin, oid, did, units=2, idempotency_key=key)
    assert r2.status_code == 200, r2.text
    assert r2.json()["id"] == r1.json()["id"]
    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(Shipment)) == 1
    row = _available(db_session, oid)
    assert row.reserved_units == 2  # not double-reserved by the replay


def test_create_shipment_same_key_different_payload_is_409(db_session):
    origin, oid, dest, did = _two_facilities(db_session)
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 5})
    key = f"ship-{uuid.uuid4().hex}"
    r1 = _create_shipment(origin, oid, did, units=2, idempotency_key=key)
    assert r1.status_code == 201, r1.text
    r2 = _create_shipment(origin, oid, did, units=3, idempotency_key=key)
    assert r2.status_code == 409, r2.text


# --------------------------------------------------------------------------- #
# Full happy-path lifecycle -- real inventory ownership transfer
# --------------------------------------------------------------------------- #


def test_full_happy_path_lifecycle_transfers_ownership(db_session):
    origin, oid, dest, did = _two_facilities(db_session)
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 5})

    created = _create_shipment(origin, oid, did, units=3).json()
    sid = created["id"]

    r = origin.post(f"/shipments/{sid}/dispatch", json={})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "DISPATCHED"
    assert r.json()["dispatched_at"] is not None

    db_session.expire_all()
    origin_row = _available(db_session, oid)
    assert origin_row.units == 2  # 5 - 3 dispatched
    assert origin_row.reserved_units == 0
    origin_units = _units(db_session, oid)
    allocated = [u for u in origin_units if u.state == S.ALLOCATED]
    assert len(allocated) == 3
    assert all(str(u.reserved_for_shipment_id) == sid for u in allocated)

    r = origin.post(f"/shipments/{sid}/in-transit", json={})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "IN_TRANSIT"

    r = dest.post(f"/shipments/{sid}/arrive", json={})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ARRIVED"

    r = dest.post(f"/shipments/{sid}/receive", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "RECEIVED"
    assert body["received_at"] is not None
    assert all(not item["is_active"] for item in body["items"])

    db_session.expire_all()
    dest_row = _available(db_session, did)
    assert dest_row.units == 3
    assert dest_row.reserved_units == 0
    dest_units = _units(db_session, did)
    assert len(dest_units) == 3
    assert all(u.state == S.AVAILABLE for u in dest_units)
    assert all(u.provenance == BloodUnitProvenance.TRANSFERRED_IN for u in dest_units)
    assert all(str(u.source_shipment_id) == sid for u in dest_units)
    assert {str(u.source_unit_id) for u in dest_units} == {str(u.id) for u in allocated}
    # Origin units remain permanently ALLOCATED -- never un-allocated by receipt.
    origin_units_after = _units(db_session, oid)
    assert all(
        u.state == S.ALLOCATED
        for u in origin_units_after
        if str(u.id) in {str(u.id) for u in allocated}
    )

    timeline = origin.get(f"/shipments/{sid}/timeline").json()
    event_types = [e["event_type"] for e in timeline]
    # Gap elimination: receipt also records a per-unit outcome summary event.
    assert event_types == ["created", "dispatched", "in_transit", "arrived", "received",
                           "receipt_outcomes"]
    outcome_note = timeline[-1]["note"]
    assert "RECEIVED_OK: 3" in outcome_note and "cold chain" in outcome_note
    # Every timestamp strictly non-decreasing -- server-written truth.
    created_ats = [datetime.fromisoformat(e["created_at"]) for e in timeline]
    assert created_ats == sorted(created_ats)


# --------------------------------------------------------------------------- #
# Illegal transitions
# --------------------------------------------------------------------------- #


def test_cannot_receive_a_shipment_still_created(db_session):
    origin, oid, dest, did = _two_facilities(db_session)
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 2})
    sid = _create_shipment(origin, oid, did, units=1).json()["id"]
    r = dest.post(f"/shipments/{sid}/receive", json={})
    assert r.status_code == 409, r.text


def test_cannot_dispatch_a_received_shipment(db_session):
    origin, oid, dest, did = _two_facilities(db_session)
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 2})
    sid = _create_shipment(origin, oid, did, units=1).json()["id"]
    origin.post(f"/shipments/{sid}/dispatch", json={})
    origin.post(f"/shipments/{sid}/in-transit", json={})
    dest.post(f"/shipments/{sid}/arrive", json={})
    dest.post(f"/shipments/{sid}/receive", json={})
    r = origin.post(f"/shipments/{sid}/dispatch", json={})
    assert r.status_code == 409, r.text


def test_cannot_receive_a_cancelled_shipment(db_session):
    origin, oid, dest, did = _two_facilities(db_session)
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 2})
    sid = _create_shipment(origin, oid, did, units=1).json()["id"]
    origin.post(f"/shipments/{sid}/cancel", json={})
    r = dest.post(f"/shipments/{sid}/receive", json={})
    assert r.status_code == 409, r.text


def test_cannot_receive_a_failed_shipment(db_session):
    origin, oid, dest, did = _two_facilities(db_session)
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 2})
    sid = _create_shipment(origin, oid, did, units=1).json()["id"]
    origin.post(f"/shipments/{sid}/dispatch", json={})
    origin.post(f"/shipments/{sid}/fail", json={"reason": "truck broke down"})
    r = dest.post(f"/shipments/{sid}/receive", json={})
    assert r.status_code == 409, r.text


def test_dispatch_after_cancel_is_rejected(db_session):
    origin, oid, dest, did = _two_facilities(db_session)
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 2})
    sid = _create_shipment(origin, oid, did, units=1).json()["id"]
    origin.post(f"/shipments/{sid}/cancel", json={})
    r = origin.post(f"/shipments/{sid}/dispatch", json={})
    assert r.status_code == 409, r.text


def test_double_receive_does_not_double_apply(db_session):
    origin, oid, dest, did = _two_facilities(db_session)
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 2})
    sid = _create_shipment(origin, oid, did, units=2).json()["id"]
    origin.post(f"/shipments/{sid}/dispatch", json={})
    origin.post(f"/shipments/{sid}/in-transit", json={})
    dest.post(f"/shipments/{sid}/arrive", json={})
    r1 = dest.post(f"/shipments/{sid}/receive", json={})
    assert r1.status_code == 200, r1.text
    r2 = dest.post(f"/shipments/{sid}/receive", json={})
    assert r2.status_code == 409, r2.text

    db_session.expire_all()
    dest_units = _units(db_session, did)
    assert len(dest_units) == 2  # not 4 -- the second call created nothing
    dest_row = _available(db_session, did)
    assert dest_row.units == 2


def test_cannot_cancel_after_dispatch(db_session):
    origin, oid, dest, did = _two_facilities(db_session)
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 2})
    sid = _create_shipment(origin, oid, did, units=1).json()["id"]
    origin.post(f"/shipments/{sid}/dispatch", json={})
    r = origin.post(f"/shipments/{sid}/cancel", json={})
    assert r.status_code == 409, r.text


# --------------------------------------------------------------------------- #
# Cancellation / failure inventory effects
# --------------------------------------------------------------------------- #


def test_cancel_before_dispatch_releases_reservation(db_session):
    origin, oid, dest, did = _two_facilities(db_session)
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 5})
    sid = _create_shipment(origin, oid, did, units=3).json()["id"]

    r = origin.post(f"/shipments/{sid}/cancel", json={"reason": "no longer needed"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "CANCELLED"
    assert r.json()["cancel_reason"] == "no longer needed"

    db_session.expire_all()
    row = _available(db_session, oid)
    assert row.units == 5
    assert row.reserved_units == 0
    units = _units(db_session, oid)
    assert all(u.state == S.AVAILABLE for u in units)
    assert all(u.reserved_for_shipment_id is None for u in units)
    items = db_session.scalars(
        select(ShipmentItem).where(ShipmentItem.shipment_id == uuid.UUID(sid))
    ).all()
    assert all(not i.is_active for i in items)


def test_fail_after_dispatch_does_not_release_units(db_session):
    origin, oid, dest, did = _two_facilities(db_session)
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 5})
    sid = _create_shipment(origin, oid, did, units=3).json()["id"]
    origin.post(f"/shipments/{sid}/dispatch", json={})

    db_session.expire_all()
    before = _available(db_session, oid)
    assert before.units == 2

    r = origin.post(f"/shipments/{sid}/fail", json={"reason": "vehicle accident"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "FAILED"
    assert r.json()["failure_reason"] == "vehicle accident"

    db_session.expire_all()
    after = _available(db_session, oid)
    # Never silently returned to AVAILABLE (Section 14).
    assert after.units == 2
    assert after.reserved_units == 0
    units = _units(db_session, oid)
    allocated = [u for u in units if u.state == S.ALLOCATED]
    assert len(allocated) == 3


# --------------------------------------------------------------------------- #
# Invariant 23 -- a unit belongs to at most one ACTIVE shipment (DB-enforced)
# --------------------------------------------------------------------------- #


def test_unit_cannot_be_added_to_two_active_shipments_at_db_level(db_session):
    from sqlalchemy.exc import IntegrityError

    origin, oid, dest, did = _two_facilities(db_session)
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 1})
    sid1 = _create_shipment(origin, oid, did, units=1).json()["id"]

    db_session.expire_all()
    item = db_session.scalars(
        select(ShipmentItem).where(ShipmentItem.shipment_id == uuid.UUID(sid1))
    ).first()
    unit_id = item.blood_unit_id

    other_dest, other_did = _facility("Other Dest")
    _verify(db_session, other_did)
    shipment2 = Shipment(
        shipment_code="SHP-TESTCOLLIDE",
        origin_facility_id=uuid.UUID(oid),
        destination_facility_id=uuid.UUID(other_did),
        status=SS.CREATED,
    )
    db_session.add(shipment2)
    db_session.flush()
    db_session.add(ShipmentItem(shipment_id=shipment2.id, blood_unit_id=unit_id))
    try:
        db_session.flush()
    except IntegrityError:
        db_session.rollback()
    else:
        raise AssertionError("expected the partial unique index to reject a second active item")


# --------------------------------------------------------------------------- #
# Route condition vs shipment lifecycle (Section 16)
# --------------------------------------------------------------------------- #


def test_route_disruption_does_not_block_or_corrupt_shipment_lifecycle(db_session):
    origin, oid, dest, did = _two_facilities(db_session)
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 2})

    route = TransportRoute(
        origin_facility_id=uuid.UUID(oid),
        destination_facility_id=uuid.UUID(did),
        distance_km=50.0,
        transit_minutes=60,
    )
    db_session.add(route)
    db_session.commit()

    created = _create_shipment(origin, oid, did, units=1, transport_route_id=str(route.id)).json()
    sid = created["id"]
    assert created["route_status"] == "OPERATIONAL"

    from app.models.enums import TransportRouteStatus

    route.status = TransportRouteStatus.DISRUPTED
    route.disrupted_reason = "flooding"
    db_session.commit()

    r = origin.post(f"/shipments/{sid}/dispatch", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "DISPATCHED"  # route disruption never auto-cancels a shipment
    assert body["route_status"] == "DISRUPTED"
    assert body["route_disrupted_reason"] == "flooding"


# --------------------------------------------------------------------------- #
# Expiry during transport (Section 15)
# --------------------------------------------------------------------------- #


def test_unit_expired_in_transit_is_received_as_expired_not_available(db_session):
    origin, oid, dest, did = _two_facilities(db_session)
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 1})
    sid = _create_shipment(origin, oid, did, units=1).json()["id"]
    origin.post(f"/shipments/{sid}/dispatch", json={})
    origin.post(f"/shipments/{sid}/in-transit", json={})

    db_session.expire_all()
    origin_unit = _units(db_session, oid)[0]
    assert origin_unit.state == S.ALLOCATED
    origin_unit.expires_at = datetime.now(UTC) - timedelta(days=1)
    db_session.commit()

    dest.post(f"/shipments/{sid}/arrive", json={})
    r = dest.post(f"/shipments/{sid}/receive", json={})
    assert r.status_code == 200, r.text

    db_session.expire_all()
    dest_units = _units(db_session, did)
    assert len(dest_units) == 1
    assert dest_units[0].state == S.EXPIRED
    dest_row = _available(db_session, did)
    assert dest_row.units == 0  # never counted as available stock


# --------------------------------------------------------------------------- #
# Authorization
# --------------------------------------------------------------------------- #


def test_unrelated_hospital_cannot_act_on_a_shipment(db_session):
    origin, oid, dest, did = _two_facilities(db_session)
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 2})
    sid = _create_shipment(origin, oid, did, units=1).json()["id"]
    other, _other_id = _facility("Unrelated Hosp")

    assert other.post(f"/shipments/{sid}/dispatch", json={}).status_code == 403
    assert other.get(f"/shipments/{sid}").status_code == 403
    assert other.get(f"/shipments/{sid}/timeline").status_code == 403


def test_destination_cannot_dispatch_and_origin_cannot_receive(db_session):
    origin, oid, dest, did = _two_facilities(db_session)
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 2})
    sid = _create_shipment(origin, oid, did, units=1).json()["id"]
    assert dest.post(f"/shipments/{sid}/dispatch", json={}).status_code == 403
    origin.post(f"/shipments/{sid}/dispatch", json={})
    origin.post(f"/shipments/{sid}/in-transit", json={})
    dest.post(f"/shipments/{sid}/arrive", json={})
    assert origin.post(f"/shipments/{sid}/receive", json={}).status_code == 403


# --------------------------------------------------------------------------- #
# Delay annotation + traceability lookups
# --------------------------------------------------------------------------- #


def test_delay_annotation_recorded_and_history_preserved(db_session):
    origin, oid, dest, did = _two_facilities(db_session)
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 2})
    sid = _create_shipment(origin, oid, did, units=1).json()["id"]
    origin.post(f"/shipments/{sid}/dispatch", json={})

    r = origin.post(f"/shipments/{sid}/delay", json={"reason": "traffic"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "DISPATCHED"  # annotation only, not a transition
    assert r.json()["delay_reason"] == "traffic"

    r2 = dest.post(f"/shipments/{sid}/delay", json={"reason": "weather"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["delay_reason"] == "weather"

    timeline = origin.get(f"/shipments/{sid}/timeline").json()
    delay_events = [e for e in timeline if e["event_type"] == "delayed"]
    assert len(delay_events) == 2
    assert [e["note"] for e in delay_events] == ["traffic", "weather"]


def test_cannot_report_delay_before_dispatch_or_after_terminal(db_session):
    origin, oid, dest, did = _two_facilities(db_session)
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 2})
    sid = _create_shipment(origin, oid, did, units=1).json()["id"]
    assert origin.post(f"/shipments/{sid}/delay", json={"reason": "x"}).status_code == 409
    origin.post(f"/shipments/{sid}/cancel", json={})
    assert origin.post(f"/shipments/{sid}/delay", json={"reason": "x"}).status_code == 409


def test_unit_trace_lookup_by_code(db_session):
    origin, oid, dest, did = _two_facilities(db_session)
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 1})
    sid = _create_shipment(origin, oid, did, units=1).json()["id"]

    db_session.expire_all()
    origin_unit = _units(db_session, oid)[0]
    r = origin.get(f"/shipments/units/{origin_unit.unit_code}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["carrying_shipment_id"] == sid

    origin.post(f"/shipments/{sid}/dispatch", json={})
    origin.post(f"/shipments/{sid}/in-transit", json={})
    dest.post(f"/shipments/{sid}/arrive", json={})
    dest.post(f"/shipments/{sid}/receive", json={})

    db_session.expire_all()
    dest_unit = _units(db_session, did)[0]
    r2 = dest.get(f"/shipments/units/{dest_unit.unit_code}")
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2["source_shipment_id"] == sid
    assert body2["source_unit_id"] == str(origin_unit.id)
    assert body2["donation_id"] is None

    # The origin facility cannot trace a unit now on the destination's books.
    r3 = origin.get(f"/shipments/units/{dest_unit.unit_code}")
    assert r3.status_code == 403, r3.text


def test_list_shipments_filters_by_direction(db_session):
    origin, oid, dest, did = _two_facilities(db_session)
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 2})
    _create_shipment(origin, oid, did, units=1)

    outgoing = origin.get("/shipments", params={"direction": "outgoing"}).json()
    assert len(outgoing) == 1
    incoming = dest.get("/shipments", params={"direction": "incoming"}).json()
    assert len(incoming) == 1
    none_for_origin_incoming = origin.get("/shipments", params={"direction": "incoming"}).json()
    assert none_for_origin_incoming == []


# --------------------------------------------------------------------------- #
# Section 32 -- one complete, realistic end-to-end scenario
# --------------------------------------------------------------------------- #


def test_e2e_full_scenario_allocate_ship_disrupt_delay_recover_receive(db_session):
    """One continuous story, exercised entirely through the real HTTP API
    (plus direct DB reads only for state no endpoint exposes), matching
    the master prompt's Section 32 scenario:

    Hospital A allocates stock -> creates a shipment to Hospital B ->
    dispatches -> in-transit -> the TRANSPORT ROUTE between them is
    disrupted (admin) -> Hospital A reports a shipment delay citing the
    disruption -> the route recovers -> the shipment arrives -> Hospital
    B receives it -> inventory reflects a REAL destination-side ownership
    transfer -> the shipment completes (RECEIVED) -> its full chain-of-
    custody timeline is visible, in order, start to finish.

    Deliberately checks, at every step, the thing Section 16 insists on:
    the route's own OPERATIONAL/DISRUPTED condition and the shipment's
    own lifecycle status never contaminate each other -- disrupting the
    route does not move, block, or cancel the shipment, and the shipment
    keeps moving through its own states while the route is still
    disrupted.
    """
    origin, _ = auth_client("HOSPITAL")
    r = origin.put(
        "/hospitals/me",
        json={
            "name": "St. Jude Blood Bank (A)",
            "city": "Springfield",
            "latitude": 39.78,
            "longitude": -89.65,
        },
    )
    assert r.status_code == 200, r.text
    oid = r.json()["id"]

    dest, _ = auth_client("HOSPITAL")
    r = dest.put(
        "/hospitals/me",
        json={
            "name": "Shelbyville General Hospital (B)",
            "city": "Shelbyville",
            "latitude": 39.79,
            "longitude": -88.95,
        },
    )
    assert r.status_code == 200, r.text
    did = r.json()["id"]
    _verify(db_session, did)

    admin, _ = auth_client("ADMIN")

    # -- Hospital A allocates (adds) real stock it will ship. --
    add = origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 10})
    assert add.status_code == 200, add.text

    # -- A registered transport route exists between the two facilities
    #    (purely informational -- Section 16). --
    route_r = admin.post(
        "/admin/transport-routes",
        json={
            "origin_facility_id": oid,
            "destination_facility_id": did,
        },
    )
    assert route_r.status_code == 201, route_r.text
    route_id = route_r.json()["id"]
    assert route_r.json()["status"] == "OPERATIONAL"

    # -- Hospital A creates a shipment of 4 O_NEG units to Hospital B,
    #    referencing that route. Creating RESERVES, not ALLOCATES. --
    create_r = origin.post(
        "/shipments",
        json={
            "origin_facility_id": oid,
            "destination_facility_id": did,
            "transport_route_id": route_id,
            "items": [{"blood_type": "O_NEG", "units": 4}],
            "note": "Scheduled restock transfer",
        },
    )
    assert create_r.status_code == 201, create_r.text
    shipment = create_r.json()
    sid = shipment["id"]
    assert shipment["status"] == "CREATED"
    assert len(shipment["items"]) == 4
    assert shipment["route_status"] == "OPERATIONAL"

    db_session.expire_all()
    origin_stock = _available(db_session, oid)
    assert origin_stock.units == 10  # unchanged -- reservation only
    assert origin_stock.reserved_units == 4

    # -- Dispatch: physical departure. Units RESERVED -> ALLOCATED
    #    (terminal, origin-side). --
    dispatch_r = origin.post(
        f"/shipments/{sid}/dispatch", json={"reason": "Courier departed at 08:00"}
    )
    assert dispatch_r.status_code == 200, dispatch_r.text
    assert dispatch_r.json()["status"] == "DISPATCHED"

    db_session.expire_all()
    origin_stock = _available(db_session, oid)
    assert origin_stock.units == 6  # 10 - 4, physically gone
    assert origin_stock.reserved_units == 0  # no longer merely reserved

    # -- In transit. --
    transit_r = origin.post(f"/shipments/{sid}/in-transit", json={})
    assert transit_r.status_code == 200, transit_r.text
    assert transit_r.json()["status"] == "IN_TRANSIT"
    assert transit_r.json()["estimated_arrival_at"] is not None  # derived from the route

    # -- The ROUTE is disrupted mid-transit (e.g. a storm closes the
    #    highway). This must NOT touch the shipment's own status. --
    disrupt_r = admin.post(
        f"/admin/transport-routes/{route_id}/disrupt",
        json={"reason": "Highway closure -- severe weather"},
    )
    assert disrupt_r.status_code == 200, disrupt_r.text
    assert disrupt_r.json()["route"]["status"] == "DISRUPTED"

    db_session.expire_all()
    mid_disruption = origin.get(f"/shipments/{sid}").json()
    assert mid_disruption["status"] == "IN_TRANSIT"  # untouched
    assert mid_disruption["route_status"] == "DISRUPTED"  # reflects the route
    assert mid_disruption["route_disrupted_reason"] == "Highway closure -- severe weather"

    # -- Hospital A reports a delay on the shipment itself, citing the
    #    disruption -- DELAYED is an annotation, not a new status. --
    delay_r = origin.post(
        f"/shipments/{sid}/delay",
        json={
            "reason": "Rerouting around closed highway per route disruption",
        },
    )
    assert delay_r.status_code == 200, delay_r.text
    delayed_body = delay_r.json()
    assert delayed_body["status"] == "IN_TRANSIT"  # still IN_TRANSIT, not a distinct state
    assert delayed_body["delayed_at"] is not None
    assert delayed_body["delay_reason"] == "Rerouting around closed highway per route disruption"

    # -- The route recovers. Still must not touch the shipment. --
    recover_r = admin.post(f"/admin/transport-routes/{route_id}/recover")
    assert recover_r.status_code == 200, recover_r.text
    assert recover_r.json()["route"]["status"] == "OPERATIONAL"

    db_session.expire_all()
    after_recovery = origin.get(f"/shipments/{sid}").json()
    assert after_recovery["status"] == "IN_TRANSIT"
    assert after_recovery["route_status"] == "OPERATIONAL"
    assert (
        after_recovery["delay_reason"] == "Rerouting around closed highway per route disruption"
    )  # history kept

    # -- Arrival (either party may confirm physical arrival). --
    arrive_r = dest.post(f"/shipments/{sid}/arrive", json={})
    assert arrive_r.status_code == 200, arrive_r.text
    assert arrive_r.json()["status"] == "ARRIVED"

    # -- Hospital B formally receives it: the real ownership transfer. --
    receive_r = dest.post(
        f"/shipments/{sid}/receive", json={"reason": "Counted and accepted at dock"}
    )
    assert receive_r.status_code == 200, receive_r.text
    received_body = receive_r.json()
    assert received_body["status"] == "RECEIVED"
    assert all(not item["is_active"] for item in received_body["items"])

    # -- Inventory reflects destination ownership: origin units are
    #    terminal ALLOCATED, destination has brand-new TRANSFERRED_IN
    #    units, and both facilities' aggregate counters agree with the
    #    real per-unit rows. --
    db_session.expire_all()
    origin_units = _units(db_session, oid)
    allocated_origin = [u for u in origin_units if u.state == S.ALLOCATED]
    assert len(allocated_origin) == 4
    assert all(str(u.reserved_for_shipment_id) == sid for u in allocated_origin)

    dest_units = _units(db_session, did)
    assert len(dest_units) == 4
    assert all(u.provenance == BloodUnitProvenance.TRANSFERRED_IN for u in dest_units)
    assert all(str(u.source_shipment_id) == sid for u in dest_units)
    assert {str(u.source_unit_id) for u in dest_units} == {str(u.id) for u in allocated_origin}
    assert all(u.received_at is not None for u in dest_units)
    # expires_at copied from the origin unit, never recomputed on arrival.
    origin_by_id = {u.id: u for u in allocated_origin}
    for du in dest_units:
        assert du.expires_at == origin_by_id[du.source_unit_id].expires_at

    dest_stock = _available(db_session, did)
    assert dest_stock is not None
    assert dest_stock.units == 4
    origin_stock = _available(db_session, oid)
    assert origin_stock.units == 6  # unchanged since dispatch -- those 4 are gone for good

    # -- The shipment is now terminal; nothing further can act on it. --
    stale_dispatch = origin.post(f"/shipments/{sid}/dispatch", json={})
    assert stale_dispatch.status_code == 409, stale_dispatch.text
    stale_cancel = origin.post(f"/shipments/{sid}/cancel", json={})
    assert stale_cancel.status_code == 409, stale_cancel.text

    # -- The full chain-of-custody timeline is visible, in order, from
    #    creation to completion -- the complete story, start to finish. --
    timeline = dest.get(f"/shipments/{sid}/timeline").json()
    event_types = [e["event_type"] for e in timeline]
    assert event_types == [
        "created",
        "dispatched",
        "in_transit",
        "delayed",
        "arrived",
        "received",
        "receipt_outcomes",
    ]
    timestamps = [e["created_at"] for e in timeline]
    assert timestamps == sorted(timestamps)  # strictly chronological

    # -- And the destination-side units are independently traceable back
    #    to this exact shipment and origin unit via the unit-code lookup
    #    (Section 25), with no donor PII exposed. --
    trace = dest.get(f"/shipments/units/{dest_units[0].unit_code}").json()
    assert trace["source_shipment_code"] == shipment["shipment_code"]
    assert trace["hospital_id"] == did
    assert "donor" not in trace  # no donor identity surfaced at all
