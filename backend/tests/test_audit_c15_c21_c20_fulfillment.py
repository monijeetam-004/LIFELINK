"""Audit findings C15, C21, C20 (2026-10-31) -- against real PostgreSQL.

C15  A backup is promoted only together with a successful reservation of its
     units; otherwise the next backup / a fresh round is tried, and if nothing
     can be secured the plan is RECOVERY_REQUIRED -- never "active and
     secured" without units.
C21  Plan -> shipment hand-off transfers the reservation (units stay
     RESERVED throughout) and links the shipment to the emergency/pathway.
C20  A failed or cancelled emergency shipment re-plans its emergency for the
     units still missing; a repeated callback changes nothing.
"""
from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.blood_unit import BloodUnit
from app.models.emergency_pathway import EmergencyPathway, PathwayReservation
from app.models.enums import (
    BloodUnitState,
    EmergencyPathwayStatus,
    HospitalInventoryAction,
    TransportRouteStatus,
)
from app.models.hospital import HospitalInventory, HospitalInventoryLog
from app.models.notification import Notification
from app.models.shipment import Shipment
from app.schemas.pathway import stored_pathway_out
from app.services import emergency_pathway_service as eps
from app.services import invariant_check, inventory_service
from app.services import pathway_handoff_service as handoff
from tests import gap_fixtures as gf
from tests.helpers import auth_client, stock_units, verify_facility_of


@pytest.fixture
def db():
    s = gf.session()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _star(db, n=3, units=2):
    dest = gf.facility(db, "Dest", 12.0, 77.0)
    srcs, routes = [], []
    for i in range(n):
        s = gf.facility(db, f"S{i}", 12.0 + 0.05 * (i + 1), 77.0)
        routes.append(gf.route(db, dest, s, 5 + i, 10 + 5 * i))
        gf.stock(db, s, "O_POS", units)
        srcs.append(s)
    return dest, srcs, routes


def _reserved_for(db, em_id) -> list[BloodUnit]:
    return list(db.scalars(select(BloodUnit).where(
        BloodUnit.reserved_for_emergency_id == em_id,
        BloodUnit.state == BloodUnitState.RESERVED)).all())


def _fail_reservations_at(monkeypatch, facility_ids):
    real = inventory_service.reserve_stock

    def flaky(db, hospital_id, *a, **kw):
        if facility_ids is None or hospital_id in facility_ids:
            raise inventory_service.InventoryActionError(f"simulated stock race at {hospital_id}")
        return real(db, hospital_id, *a, **kw)

    monkeypatch.setattr(inventory_service, "reserve_stock", flaky)


# --------------------------------------------------------------------------- #
# C15
# --------------------------------------------------------------------------- #
def test_backup_whose_units_cannot_be_reserved_is_skipped_for_the_next(db, monkeypatch):
    dest, srcs, routes = _star(db, 3)
    em = gf.emergency(db, blood_type="O_POS", target=dest)
    primary, backups, _r = eps.create_plan_round(db, em, requested_by_user_id=None, reason="t")
    eps.reserve_pathway(db, em, primary, actor_user_id=None)
    _fail_reservations_at(monkeypatch, {srcs[1].id})
    routes[0].status = TransportRouteStatus.DISRUPTED
    db.flush()
    [out] = eps.handle_route_disrupted(db, routes[0], admin_user_id=None)
    assert out.kind == "BACKUP_PROMOTED" and out.new_version == backups[1].version
    assert "could not be reserved" in out.rejected_backups[backups[0].version][0]
    db.refresh(backups[0])
    db.refresh(backups[1])
    assert backups[0].status == EmergencyPathwayStatus.INVALIDATED
    assert backups[1].status == EmergencyPathwayStatus.ACTIVE
    held = _reserved_for(db, em.id)
    assert len(held) == 1 and held[0].hospital_id == srcs[2].id
    assert stored_pathway_out(db, backups[1]).reservation_status == "RESERVED"
    assert invariant_check.violations(db) == {}


def test_nothing_reservable_means_recovery_required_never_a_false_secured_state(
        db, monkeypatch):
    dest, srcs, routes = _star(db, 3)
    em = gf.emergency(db, blood_type="O_POS", target=dest)
    primary, _b, _r = eps.create_plan_round(db, em, requested_by_user_id=None, reason="t")
    eps.reserve_pathway(db, em, primary, actor_user_id=None)
    _fail_reservations_at(monkeypatch, None)  # every reservation attempt fails
    routes[0].status = TransportRouteStatus.DISRUPTED
    db.flush()
    [out] = eps.handle_route_disrupted(db, routes[0], admin_user_id=None)
    assert out.kind == "RECOVERY_REQUIRED"
    active = db.scalars(select(EmergencyPathway).where(
        EmergencyPathway.emergency_id == em.id,
        EmergencyPathway.status == EmergencyPathwayStatus.ACTIVE)).all()
    assert len(active) == 1
    view = stored_pathway_out(db, active[0])
    assert view.reservation_status == "RECOVERY_REQUIRED" and view.reserved_units == 0
    assert "simulated stock race" in view.recovery_required_reason
    assert _reserved_for(db, em.id) == []  # nothing half-reserved by failed attempts
    assert db.scalar(select(func.count()).select_from(Notification).where(
        Notification.type == "emergency.pathway_recovery_required")) >= 1
    assert invariant_check.violations(db) == {}

    # A later successful reservation clears the recovery state.
    monkeypatch.undo()
    eps.reserve_pathway(db, em, active[0], actor_user_id=None)
    assert stored_pathway_out(db, active[0]).reservation_status == "RESERVED"


def test_partial_reservation_inside_a_failed_attempt_is_rolled_back(db, monkeypatch):
    """A two-leg backup whose SECOND leg fails must not keep the first leg's
    units reserved."""
    dest = gf.facility(db, "Dest", 12.0, 77.0)
    a = gf.facility(db, "A", 12.05, 77.0)
    b = gf.facility(db, "B", 12.10, 77.0)
    c = gf.facility(db, "C", 12.15, 77.0)
    route_a = gf.route(db, dest, a, 5, 10)
    gf.route(db, dest, b, 6, 12)
    gf.route(db, dest, c, 7, 14)
    gf.stock(db, a, "O_POS", 2)
    gf.stock(db, b, "O_POS", 1)
    gf.stock(db, c, "O_POS", 1)
    em = gf.emergency(db, blood_type="O_POS", units=2, target=dest)
    primary, _b, _r = eps.create_plan_round(db, em, requested_by_user_id=None, reason="t")
    assert primary.selected_facility_id == a.id
    eps.reserve_pathway(db, em, primary, actor_user_id=None)
    _fail_reservations_at(monkeypatch, {c.id})
    route_a.status = TransportRouteStatus.DISRUPTED
    db.flush()
    [out] = eps.handle_route_disrupted(db, route_a, admin_user_id=None)
    assert out.kind == "RECOVERY_REQUIRED"
    assert _reserved_for(db, em.id) == []
    rows = db.scalars(select(HospitalInventory).where(
        HospitalInventory.hospital_id.in_([b.id, c.id]))).all()
    assert all(r.reserved_units == 0 for r in rows)


def test_unreserved_plans_fail_over_exactly_as_before(db):
    dest, srcs, routes = _star(db, 2)
    em = gf.emergency(db, blood_type="O_POS", target=dest)
    _p, backups, _r = eps.create_plan_round(db, em, requested_by_user_id=None, reason="t")
    routes[0].status = TransportRouteStatus.DISRUPTED
    db.flush()
    [out] = eps.handle_route_disrupted(db, routes[0], admin_user_id=None)
    assert out.kind == "BACKUP_PROMOTED"
    db.refresh(backups[0])
    assert stored_pathway_out(db, backups[0]).reservation_status == "UNRESERVED"


# --------------------------------------------------------------------------- #
# C21
# --------------------------------------------------------------------------- #
def test_hand_off_transfers_the_reservation_without_releasing_it(db):
    dest, srcs, _routes = _star(db, 1, units=3)
    em = gf.emergency(db, blood_type="O_POS", units=2, target=dest)
    primary, _b, _r = eps.create_plan_round(db, em, requested_by_user_id=None, reason="t")
    eps.reserve_pathway(db, em, primary, actor_user_id=None)
    held = {u.id for u in _reserved_for(db, em.id)}
    inv = db.scalars(select(HospitalInventory).where(
        HospitalInventory.hospital_id == srcs[0].id)).one()
    assert inv.reserved_units == 2

    ships, created = handoff.hand_off(db, em, primary, actor_user_id=None)
    assert created and len(ships) == 1
    sh = ships[0]
    assert sh.emergency_id == em.id and sh.pathway_id == primary.id
    moved = db.scalars(select(BloodUnit).where(BloodUnit.reserved_for_shipment_id == sh.id)).all()
    assert {u.id for u in moved} == held  # exactly the units the plan held
    assert all(u.state == BloodUnitState.RESERVED and u.reserved_for_emergency_id is None
               for u in moved)
    db.refresh(inv)
    assert inv.reserved_units == 2  # never dipped: no release, no re-reserve
    assert not db.scalars(select(HospitalInventoryLog).where(
        HospitalInventoryLog.emergency_id == em.id,
        HospitalInventoryLog.action == HospitalInventoryAction.RELEASE)).all()
    res = db.scalars(select(PathwayReservation).where(
        PathwayReservation.pathway_id == primary.id)).all()
    assert all(not r.active and r.handed_off_shipment_id == sh.id for r in res)
    view = stored_pathway_out(db, primary)
    assert view.reservation_status == "HANDED_OFF" and view.shipments[0].units == 2
    # idempotent
    again, created2 = handoff.hand_off(db, em, primary, actor_user_id=None)
    assert not created2 and [s.id for s in again] == [sh.id]
    assert invariant_check.violations(db) == {}


def test_local_stock_leg_is_not_shipped_and_keeps_its_reservation(db):
    dest = gf.facility(db, "Dest", 12.0, 77.0)
    src = gf.facility(db, "Src", 12.05, 77.0)
    gf.route(db, dest, src, 5, 10)
    gf.stock(db, dest, "O_POS", 1)
    gf.stock(db, src, "O_POS", 1)  # neither alone suffices -> a local leg + a delivery leg
    em = gf.emergency(db, blood_type="O_POS", units=2, target=dest)
    primary, _b, _r = eps.create_plan_round(db, em, requested_by_user_id=None, reason="t")
    legs = eps.legs_of(db, primary.id)
    assert {leg.source_facility_id for leg in legs} == {dest.id, src.id}
    eps.reserve_pathway(db, em, primary, actor_user_id=None)
    ships, _ = handoff.hand_off(db, em, primary, actor_user_id=None)
    assert len(ships) == 1 and ships[0].origin_facility_id == src.id
    local = db.scalars(select(PathwayReservation).where(
        PathwayReservation.pathway_id == primary.id,
        PathwayReservation.facility_id == dest.id)).all()
    assert local and all(r.active for r in local)
    assert stored_pathway_out(db, primary).reservation_status == "HANDED_OFF"
    assert invariant_check.violations(db) == {}


def test_hand_off_requires_a_secured_valid_plan_and_is_all_or_nothing(db):
    dest, srcs, routes = _star(db, 1, units=2)
    em = gf.emergency(db, blood_type="O_POS", units=2, target=dest)
    primary, _b, _r = eps.create_plan_round(db, em, requested_by_user_id=None, reason="t")
    with pytest.raises(handoff.HandoffError, match="Reserve"):
        handoff.hand_off(db, em, primary, actor_user_id=None)
    eps.reserve_pathway(db, em, primary, actor_user_id=None)
    # one reserved unit now expires before the shipment would arrive
    units = _reserved_for(db, em.id)
    gf.set_expiry(db, units[:1], 5)
    sp = db.begin_nested()
    with pytest.raises((handoff.HandoffError, inventory_service.InventoryActionError)):
        handoff.hand_off(db, em, primary, actor_user_id=None)
    sp.rollback()
    assert db.scalar(select(func.count()).select_from(Shipment)) == 0
    assert len(_reserved_for(db, em.id)) == 2


# --------------------------------------------------------------------------- #
# C21 + C20 through the API, including dispatch, failure and a duplicate call
# --------------------------------------------------------------------------- #
def _api_network():
    admin, _ = auth_client("ADMIN")
    dest_c, dest_email = auth_client("HOSPITAL", name="Dest General")
    dest_c.put("/hospitals/me", json={"name": "Dest General", "city": "Metro",
                                      "latitude": 12.0, "longitude": 77.0})
    dest_id = verify_facility_of(dest_email)
    srcs = []
    for i in range(2):
        c, email = auth_client("HOSPITAL", name=f"Bank {i}")
        c.put("/hospitals/me", json={"name": f"Bank {i}", "city": "Metro",
                                     "latitude": 12.0 + 0.05 * (i + 1), "longitude": 77.0})
        fid = verify_facility_of(email)
        stock_units(fid, "O_NEG", 2)
        r = admin.post("/admin/transport-routes", json={
            "origin_facility_id": str(dest_id), "destination_facility_id": str(fid),
            "distance_km": 6.0 + i, "transit_minutes": 10 + 5 * i})
        assert r.status_code in (200, 201), r.text
        srcs.append((c, fid, r.json()["id"]))
    return admin, dest_c, dest_id, srcs


def test_handoff_dispatch_fail_replans_and_duplicate_failure_is_harmless():
    admin, dest_c, dest_id, srcs = _api_network()
    patient, _ = auth_client("PATIENT")
    eid = patient.post("/emergencies", json={
        "blood_type": "O_NEG", "units_needed": 2, "target_facility_id": str(dest_id),
        "latitude": 12.0, "longitude": 77.0,
        "deadline": (datetime.now(UTC) + timedelta(hours=8)).isoformat()}).json()["id"]
    assert dest_c.post(f"/emergencies/{eid}/pathway", json={}).status_code == 200
    # handing off before reserving is refused
    assert dest_c.post(f"/emergencies/{eid}/pathway/handoff").status_code == 409
    assert dest_c.post(f"/emergencies/{eid}/pathway/reserve").status_code == 200
    # only the destination facility or an admin may hand off
    assert srcs[1][0].post(f"/emergencies/{eid}/pathway/handoff").status_code == 403
    ho = dest_c.post(f"/emergencies/{eid}/pathway/handoff")
    assert ho.status_code == 200, ho.text
    view = ho.json()
    assert view["reservation_status"] == "HANDED_OFF" and len(view["shipments"]) == 1
    ship = view["shipments"][0]
    src_client = next(c for c, fid, _r in srcs if str(fid) == ship["origin_facility_id"])
    # a second hand-off is idempotent
    assert dest_c.post(f"/emergencies/{eid}/pathway/handoff").json()["shipments"] == [ship]
    # dispatch allocates exactly the transferred units
    d = src_client.post(f"/shipments/{ship['id']}/dispatch", json={})
    assert d.status_code == 200, d.text
    assert d.json()["emergency_id"] == eid
    # the shipment fails in transit -> the emergency is re-planned and secured
    f = src_client.post(f"/shipments/{ship['id']}/fail", json={"reason": "vehicle breakdown"})
    assert f.status_code == 200, f.text
    plans = dest_c.get(f"/emergencies/{eid}/pathways").json()
    active = [p for p in plans if p["status"] == "ACTIVE"]
    assert len(active) == 1 and active[0]["id"] != view["id"]
    assert active[0]["reservation_status"] in ("RESERVED", "RECOVERY_REQUIRED")
    timeline = [e["event_type"] for e in
                src_client.get(f"/shipments/{ship['id']}/timeline").json()]
    assert "emergency_replanned" in timeline
    # duplicate failure callback: refused by the state machine, nothing changes
    again = src_client.post(f"/shipments/{ship['id']}/fail", json={"reason": "dup"})
    assert again.status_code == 409
    assert len([p for p in dest_c.get(f"/emergencies/{eid}/pathways").json()
                if p["status"] == "ACTIVE"]) == 1
    db = SessionLocal()
    try:
        assert invariant_check.violations(db) == {}
    finally:
        db.close()


def test_route_disruption_during_execution_does_not_replan_silently():
    admin, dest_c, dest_id, srcs = _api_network()
    patient, _ = auth_client("PATIENT")
    eid = patient.post("/emergencies", json={
        "blood_type": "O_NEG", "units_needed": 1, "target_facility_id": str(dest_id),
        "latitude": 12.0, "longitude": 77.0}).json()["id"]
    dest_c.post(f"/emergencies/{eid}/pathway", json={})
    dest_c.post(f"/emergencies/{eid}/pathway/reserve")
    view = dest_c.post(f"/emergencies/{eid}/pathway/handoff").json()
    route_id = view["legs"][0]["steps"][0]["route_id"]
    d = admin.post(f"/admin/transport-routes/{route_id}/disrupt", json={"reason": "flood"})
    assert d.status_code == 200, d.text
    outcome = next(o for o in d.json()["affected_emergencies"] if o["emergency_id"] == eid)
    assert outcome["kind"] == "IN_EXECUTION"
    active = [p for p in dest_c.get(f"/emergencies/{eid}/pathways").json()
              if p["status"] == "ACTIVE"]
    assert [p["id"] for p in active] == [view["id"]]


def test_concurrent_handoffs_create_one_set_of_shipments():
    admin, dest_c, dest_id, srcs = _api_network()
    patient, _ = auth_client("PATIENT")
    eid = patient.post("/emergencies", json={
        "blood_type": "O_NEG", "units_needed": 1, "target_facility_id": str(dest_id),
        "latitude": 12.0, "longitude": 77.0}).json()["id"]
    dest_c.post(f"/emergencies/{eid}/pathway", json={})
    dest_c.post(f"/emergencies/{eid}/pathway/reserve")
    results: list[int] = []

    def go():
        results.append(dest_c.post(f"/emergencies/{eid}/pathway/handoff").status_code)

    threads = [threading.Thread(target=go) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [200, 200, 200]
    db = SessionLocal()
    try:
        assert db.scalar(select(func.count()).select_from(Shipment).where(
            Shipment.emergency_id == eid)) == 1
        assert invariant_check.violations(db) == {}
    finally:
        db.close()
