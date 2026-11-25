"""Gap elimination sections 1-3: the pathway engine, route persistence,
scoped re-planning and backup pathways -- against real PostgreSQL rows.

Each test builds its own small network with tests/gap_fixtures.py and runs
the real planner / pathway service. Nothing is mocked."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core import compatibility
from app.core.time import utcnow
from app.models.blood_unit import BloodUnit
from app.models.emergency import EmergencyOverride
from app.models.emergency_pathway import EmergencyPathway
from app.models.enums import (
    BloodComponent,
    BloodType,
    BloodUnitState,
    EmergencyOverrideKind,
    EmergencyPathwayStatus,
    PathwayFeasibility,
    RouteBasis,
    TransportRouteStatus,
    Urgency,
)
from app.services import emergency_pathway_service as eps
from app.services import inventory_service
from app.services import pathway_planner as planner
from tests import gap_fixtures as gf


@pytest.fixture
def db():
    s = gf.session()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _req(
    dest,
    *,
    blood_type="A_POS",
    component="WHOLE_BLOOD",
    units=1,
    urgency=Urgency.CRITICAL,
    deadline_minutes=None,
    emergency_id=None,
    **kw,
):
    now = utcnow()
    return planner.PlanRequest(
        recipient_type=BloodType(blood_type),
        component=BloodComponent(component),
        units_needed=units,
        urgency=urgency,
        deadline=(
            now + timedelta(minutes=deadline_minutes) if deadline_minutes is not None else None
        ),
        destination_facility_id=dest.id,
        now=now,
        emergency_id=emergency_id,
        handling_minutes=kw.pop("handling_minutes", 30),
        risk_minutes=kw.pop("risk_minutes", 15),
        **kw,
    )


# --------------------------------------------------------------------------- #
# 1.1 Compatibility through the central service
# --------------------------------------------------------------------------- #
def test_compatibility_service_orders_identical_type_first_and_never_lists_incompatible():
    order = compatibility.compatible_source_types(BloodType.A_POS, BloodComponent.RED_BLOOD_CELLS)
    assert order[0] == BloodType.A_POS
    assert set(order) == {BloodType.A_POS, BloodType.A_NEG, BloodType.O_POS, BloodType.O_NEG}
    assert compatibility.compatible_source_types(BloodType.O_NEG, BloodComponent.WHOLE_BLOOD) == [
        BloodType.O_NEG
    ]
    # Plasma follows the inverse ABO rule: an AB patient needs AB plasma.
    assert set(compatibility.compatible_source_types(BloodType.AB_POS, BloodComponent.PLASMA)) == {
        BloodType.AB_POS,
        BloodType.AB_NEG,
    }
    # Platelets are not ABO-restricted (same type only preferred).
    plt = compatibility.compatible_source_types(BloodType.B_NEG, BloodComponent.PLATELETS)
    assert plt[0] == BloodType.B_NEG and len(plt) == 8


def test_planner_uses_only_compatible_types(db):
    dest = gf.facility(db, "Dest", 12.0, 77.0)
    src = gf.facility(db, "Src", 12.1, 77.1)
    gf.route(db, dest, src, 12, 20)
    gf.stock(db, src, "A_POS", 5)  # incompatible with an O_NEG patient
    result = planner.plan(db, _req(dest, blood_type="O_NEG"))
    assert result.primary.feasibility == PathwayFeasibility.IMPOSSIBLE
    gf.stock(db, src, "O_NEG", 1)
    result = planner.plan(db, _req(dest, blood_type="O_NEG"))
    assert result.primary.feasibility == PathwayFeasibility.VIABLE
    assert result.primary.legs[0].units_by_type == {BloodType.O_NEG: 1}


def test_identical_type_is_preferred_over_universal_donor_stock(db):
    dest = gf.facility(db, "Dest", 12.0, 77.0)
    src = gf.facility(db, "Src", 12.1, 77.1)
    gf.route(db, dest, src, 12, 20)
    gf.stock(db, src, "O_NEG", 3)
    gf.stock(db, src, "A_POS", 2)
    result = planner.plan(db, _req(dest, blood_type="A_POS", units=3))
    taken = result.primary.legs[0].units_by_type
    assert taken[BloodType.A_POS] == 2 and taken[BloodType.O_NEG] == 1


# --------------------------------------------------------------------------- #
# 1.2 Quantity, reserved stock, multi-facility and partial fulfilment
# --------------------------------------------------------------------------- #
def test_reserved_units_are_never_counted_as_available(db):
    dest = gf.facility(db, "Dest", 12.0, 77.0)
    src = gf.facility(db, "Src", 12.1, 77.1)
    gf.route(db, dest, src, 12, 20)
    gf.stock(db, src, "B_POS", 3)
    other = gf.emergency(db, blood_type="B_POS")
    inventory_service.reserve_stock(
        db, src.id, BloodType.B_POS, 2, actor_user_id=None, emergency_id=other.id
    )
    result = planner.plan(db, _req(dest, blood_type="B_POS", units=2))
    assert result.primary.units_planned == 1
    assert result.primary.feasibility == PathwayFeasibility.PARTIAL
    # ...but units reserved FOR THIS emergency do count for it.
    result_own = planner.plan(db, _req(dest, blood_type="B_POS", units=2, emergency_id=other.id))
    assert result_own.primary.units_planned == 2


def test_multi_facility_plan_fulfils_quantity_no_single_source_can(db):
    dest = gf.facility(db, "Dest", 12.0, 77.0)
    a = gf.facility(db, "A", 12.1, 77.1)
    b = gf.facility(db, "B", 12.2, 77.2)
    gf.route(db, dest, a, 12, 20)
    gf.route(db, dest, b, 25, 35)
    gf.stock(db, a, "O_POS", 2)
    gf.stock(db, b, "O_POS", 2)
    result = planner.plan(db, _req(dest, blood_type="O_POS", units=3))
    assert result.primary.feasibility == PathwayFeasibility.VIABLE
    assert result.primary.units_planned == 3
    assert {leg.option.facility_id for leg in result.primary.legs} == {a.id, b.id}


def test_partial_when_the_network_cannot_supply_everything(db):
    dest = gf.facility(db, "Dest", 12.0, 77.0)
    a = gf.facility(db, "A", 12.1, 77.1)
    gf.route(db, dest, a, 12, 20)
    gf.stock(db, a, "O_POS", 2)
    result = planner.plan(db, _req(dest, blood_type="O_POS", units=5))
    assert result.primary.feasibility == PathwayFeasibility.PARTIAL
    assert result.primary.units_planned == 2


# --------------------------------------------------------------------------- #
# 1.3 Component isolation
# --------------------------------------------------------------------------- #
def test_components_are_never_substituted(db):
    dest = gf.facility(db, "Dest", 12.0, 77.0)
    a = gf.facility(db, "A", 12.1, 77.1)
    gf.route(db, dest, a, 12, 20)
    gf.stock(db, a, "AB_POS", 4, component="PLASMA")
    wb = planner.plan(db, _req(dest, blood_type="AB_POS", component="WHOLE_BLOOD"))
    assert wb.primary.feasibility == PathwayFeasibility.IMPOSSIBLE
    plasma = planner.plan(db, _req(dest, blood_type="AB_POS", component="PLASMA"))
    assert plasma.primary.feasibility == PathwayFeasibility.VIABLE


# --------------------------------------------------------------------------- #
# 1.4 Deadline-aware classification, boundaries
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "deadline, expected",
    [
        (None, PathwayFeasibility.VIABLE),
        (60 + 15 + 1, PathwayFeasibility.VIABLE),  # slack 16 > risk 15
        (60 + 15, PathwayFeasibility.VIABLE),  # slack == risk margin -> not "risk"
        (60 + 14, PathwayFeasibility.DEADLINE_RISK),  # slack 14 < 15
        (60, PathwayFeasibility.DEADLINE_RISK),  # arrives exactly at the deadline
        (59, PathwayFeasibility.IMPOSSIBLE),  # arrives 1 min late
        (0, PathwayFeasibility.IMPOSSIBLE),  # deadline is now
        (-5, PathwayFeasibility.IMPOSSIBLE),  # deadline already passed
    ],
)
def test_deadline_classification_boundaries(db, deadline, expected):
    dest = gf.facility(db, "Dest", 12.0, 77.0)
    a = gf.facility(db, "A", 12.1, 77.1)
    gf.route(db, dest, a, 20, 30)  # ETA = 30 handling + 30 transit = 60 min
    gf.stock(db, a, "O_POS", 1)
    req = _req(dest, blood_type="O_POS", deadline_minutes=deadline)
    # pin "now" and the deadline exactly (no clock drift inside the test)
    if deadline is not None:
        req.deadline = req.now + timedelta(minutes=deadline)
    result = planner.plan(db, req)
    assert result.primary.feasibility == expected


def test_units_expiring_before_arrival_are_not_usable(db):
    dest = gf.facility(db, "Dest", 12.0, 77.0)
    a = gf.facility(db, "A", 12.1, 77.1)
    gf.route(db, dest, a, 20, 30)  # ETA 60
    units = gf.stock(db, a, "O_POS", 2)
    gf.set_expiry(db, units, 45)  # in date now, expired on arrival
    result = planner.plan(db, _req(dest, blood_type="O_POS"))
    assert result.primary.feasibility == PathwayFeasibility.IMPOSSIBLE
    rejected = next(o for o in result.options if o.facility_id == a.id)
    assert "in date on arrival" in rejected.rejected_reason
    gf.set_expiry(db, units, 61)
    assert (
        planner.plan(db, _req(dest, blood_type="O_POS")).primary.feasibility
        == PathwayFeasibility.VIABLE
    )


# --------------------------------------------------------------------------- #
# 1.5 Urgency-driven, deterministic strategies
# --------------------------------------------------------------------------- #
def test_critical_picks_fastest_routine_picks_shortest(db):
    dest = gf.facility(db, "Dest", 12.0, 77.0)
    fast = gf.facility(db, "Fast", 12.3, 77.3)
    short = gf.facility(db, "Short", 12.05, 77.05)
    gf.route(db, dest, fast, 40, 25)  # longer but faster (highway)
    gf.route(db, dest, short, 10, 50)  # shorter but slow
    gf.stock(db, fast, "A_NEG", 1)
    gf.stock(db, short, "A_NEG", 1)
    crit = planner.plan(db, _req(dest, blood_type="A_NEG", urgency=Urgency.CRITICAL))
    routine = planner.plan(db, _req(dest, blood_type="A_NEG", urgency=Urgency.ROUTINE))
    assert crit.strategy.name == "CRITICAL_FASTEST_FEASIBLE"
    assert crit.primary.legs[0].option.facility_id == fast.id
    assert routine.strategy.name == "ROUTINE_EFFICIENT"
    assert routine.primary.legs[0].option.facility_id == short.id
    # deterministic: same input -> same answer
    again = planner.plan(db, _req(dest, blood_type="A_NEG", urgency=Urgency.CRITICAL))
    assert again.primary.legs[0].option.facility_id == fast.id


def test_unverified_and_inactive_facilities_are_not_sources(db):
    dest = gf.facility(db, "Dest", 12.0, 77.0)
    unv = gf.facility(db, "Unverified", 12.1, 77.1, verified=False)
    gf.route(db, dest, unv, 5, 10)
    gf.stock(db, unv, "O_POS", 3)
    result = planner.plan(db, _req(dest, blood_type="O_POS"))
    assert result.primary.feasibility == PathwayFeasibility.IMPOSSIBLE
    assert (
        next(o for o in result.options if o.facility_id == unv.id).rejected_reason
        == "facility is not verified"
    )
    allowed = planner.plan(db, _req(dest, blood_type="O_POS", allow_unverified=True))
    assert allowed.primary.feasibility == PathwayFeasibility.VIABLE


# --------------------------------------------------------------------------- #
# 2. Complete route persistence (multi-hop)
# --------------------------------------------------------------------------- #
def _chain(db):
    """dest - m1 - m2 - src : a 3-hop pathway, plus an unrelated route m1-x."""
    dest = gf.facility(db, "Dest", 12.0, 77.0)
    m1 = gf.facility(db, "M1", 12.1, 77.1)
    m2 = gf.facility(db, "M2", 12.2, 77.2)
    src = gf.facility(db, "Src", 12.3, 77.3)
    x = gf.facility(db, "X", 12.4, 77.0)
    r1 = gf.route(db, dest, m1, 10, 15)
    r2 = gf.route(db, m1, m2, 10, 15)
    r3 = gf.route(db, m2, src, 10, 15)
    rx = gf.route(db, m1, x, 10, 15)
    gf.stock(db, src, "O_POS", 2)
    return dest, m1, m2, src, x, (r1, r2, r3, rx)


def test_full_route_is_stored_in_order(db):
    dest, m1, m2, src, _x, (r1, r2, r3, _rx) = _chain(db)
    em = gf.emergency(db, blood_type="O_POS", target=dest)
    primary, _backups, _res = eps.create_plan_round(
        db, em, requested_by_user_id=None, reason="test"
    )
    legs = eps.legs_of(db, primary.id)
    assert len(legs) == 1
    assert [str(n) for n in (src.id, m2.id, m1.id, dest.id)] == legs[0].node_ids
    assert eps.route_ids_of(db, primary.id) == [r3.id, r2.id, r1.id]
    assert legs[0].route_basis == RouteBasis.ROUTED and legs[0].hops == 3
    assert legs[0].transit_minutes == 45 and legs[0].distance_km == 30
    assert primary.eta_minutes == 75 and primary.feasibility == PathwayFeasibility.VIABLE


@pytest.mark.parametrize(
    "which", [0, 1, 2], ids=["A-first-route", "B-middle-route", "C-last-route"]
)
def test_failure_of_any_route_on_the_path_replans(db, which):
    dest, _m1, _m2, _src, _x, routes = _chain(db)
    em = gf.emergency(db, blood_type="O_POS", target=dest)
    primary, _b, _r = eps.create_plan_round(db, em, requested_by_user_id=None, reason="t")
    failed = routes[which]
    failed.status = TransportRouteStatus.DISRUPTED
    db.flush()
    outcomes = eps.handle_route_disrupted(db, failed, admin_user_id=None)
    assert len(outcomes) == 1 and outcomes[0].previous_version == primary.version
    db.refresh(primary)
    assert primary.status == EmergencyPathwayStatus.INVALIDATED
    assert str(failed.id) in primary.invalidated_reason


def test_D_unrelated_route_changes_nothing(db):
    dest, *_rest, routes = _chain(db)
    em = gf.emergency(db, blood_type="O_POS", target=dest)
    primary, _b, _r = eps.create_plan_round(db, em, requested_by_user_id=None, reason="t")
    rx = routes[3]  # touches M1 (a node on the path) but is not on the path
    rx.status = TransportRouteStatus.DISRUPTED
    db.flush()
    assert eps.handle_route_disrupted(db, rx, admin_user_id=None) == []
    db.refresh(primary)
    assert primary.status == EmergencyPathwayStatus.ACTIVE


def test_E_route_of_another_emergency_does_not_touch_this_one(db):
    dest, *_rest, routes = _chain(db)
    em1 = gf.emergency(db, blood_type="O_POS", target=dest)
    p1, _b, _r = eps.create_plan_round(db, em1, requested_by_user_id=None, reason="t")
    other_dest = gf.facility(db, "Other", 13.0, 78.0)
    other_src = gf.facility(db, "OtherSrc", 13.1, 78.1)
    r_other = gf.route(db, other_dest, other_src, 5, 10)
    gf.stock(db, other_src, "A_POS", 1)
    em2 = gf.emergency(db, blood_type="A_POS", target=other_dest)
    eps.create_plan_round(db, em2, requested_by_user_id=None, reason="t")
    r_other.status = TransportRouteStatus.DISRUPTED
    db.flush()
    outcomes = eps.handle_route_disrupted(db, r_other, admin_user_id=None)
    assert [o.emergency_id for o in outcomes] == [em2.id]
    db.refresh(p1)
    assert p1.status == EmergencyPathwayStatus.ACTIVE


# --------------------------------------------------------------------------- #
# 3. Backups: limit, route-disjointness, validation on failover
# --------------------------------------------------------------------------- #
def _star(db, n_sources=5):
    dest = gf.facility(db, "Dest", 12.0, 77.0)
    srcs, routes = [], []
    for i in range(n_sources):
        s = gf.facility(db, f"S{i}", 12.0 + 0.05 * (i + 1), 77.0)
        routes.append(gf.route(db, dest, s, 5 + i, 10 + 5 * i))
        gf.stock(db, s, "O_POS", 2)
        srcs.append(s)
    return dest, srcs, routes


def test_backups_are_stored_up_to_the_configured_maximum(db, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "pathway_max_backups", 2)
    dest, srcs, _routes = _star(db)
    em = gf.emergency(db, blood_type="O_POS", target=dest)
    primary, backups, _r = eps.create_plan_round(db, em, requested_by_user_id=None, reason="t")
    assert primary.selected_facility_id == srcs[0].id
    assert [b.backup_rank for b in backups] == [1, 2]
    assert all(b.status == EmergencyPathwayStatus.BACKUP for b in backups)
    assert backups[0].selected_facility_id == srcs[1].id


def test_F_failure_of_a_backup_only_route_invalidates_just_that_backup(db):
    dest, srcs, routes = _star(db, 3)
    em = gf.emergency(db, blood_type="O_POS", target=dest)
    primary, backups, _r = eps.create_plan_round(db, em, requested_by_user_id=None, reason="t")
    routes[1].status = TransportRouteStatus.DISRUPTED  # used only by backup 1
    db.flush()
    outcomes = eps.handle_route_disrupted(db, routes[1], admin_user_id=None)
    assert outcomes[0].kind == "BACKUPS_INVALIDATED"
    db.refresh(primary)
    db.refresh(backups[0])
    assert primary.status == EmergencyPathwayStatus.ACTIVE
    assert backups[0].status == EmergencyPathwayStatus.INVALIDATED


def test_failover_promotes_a_still_valid_backup(db):
    dest, srcs, routes = _star(db, 3)
    em = gf.emergency(db, blood_type="O_POS", target=dest)
    primary, backups, _r = eps.create_plan_round(db, em, requested_by_user_id=None, reason="t")
    routes[0].status = TransportRouteStatus.DISRUPTED
    db.flush()
    [outcome] = eps.handle_route_disrupted(db, routes[0], admin_user_id=None)
    assert outcome.kind == "BACKUP_PROMOTED" and outcome.new_version == backups[0].version
    db.refresh(backups[0])
    assert backups[0].status == EmergencyPathwayStatus.ACTIVE and backups[0].promoted_at


def test_failover_rejects_a_backup_whose_stock_is_gone_and_replans(db):
    dest, srcs, routes = _star(db, 2)
    em = gf.emergency(db, blood_type="O_POS", target=dest)
    primary, backups, _r = eps.create_plan_round(db, em, requested_by_user_id=None, reason="t")
    # backup 1's source loses its stock (used elsewhere) after planning
    for u in db.scalars(select(BloodUnit).where(BloodUnit.hospital_id == srcs[1].id)).all():
        inventory_service.discard_unit(
            db, srcs[1].id, u.id, actor_user_id=None, reason="damaged in storage"
        )
    routes[0].status = TransportRouteStatus.DISRUPTED
    db.flush()
    [outcome] = eps.handle_route_disrupted(db, routes[0], admin_user_id=None)
    assert outcome.kind == "REPLANNED"
    assert backups[0].version in outcome.rejected_backups
    assert any("usable" in p for p in outcome.rejected_backups[backups[0].version])
    assert outcome.still_reachable is False


def test_failover_rejects_a_backup_from_a_facility_that_lost_verification(db):
    dest, srcs, routes = _star(db, 3)
    em = gf.emergency(db, blood_type="O_POS", target=dest)
    primary, backups, _r = eps.create_plan_round(db, em, requested_by_user_id=None, reason="t")
    srcs[1].verified = False
    routes[0].status = TransportRouteStatus.DISRUPTED
    db.flush()
    [outcome] = eps.handle_route_disrupted(db, routes[0], admin_user_id=None)
    assert outcome.kind == "BACKUP_PROMOTED"
    assert backups[0].version in outcome.rejected_backups
    assert outcome.new_version == backups[1].version


def test_facility_revocation_fails_over_pathways_sourcing_from_it(db):
    dest, srcs, _routes = _star(db, 2)
    em = gf.emergency(db, blood_type="O_POS", target=dest)
    primary, backups, _r = eps.create_plan_round(db, em, requested_by_user_id=None, reason="t")
    srcs[0].verified = False
    db.flush()
    [outcome] = eps.handle_facility_unverified(db, srcs[0].id, admin_user_id=None)
    assert outcome.kind == "BACKUP_PROMOTED"
    db.refresh(primary)
    assert primary.status == EmergencyPathwayStatus.INVALIDATED


def test_override_keeps_unverified_source_and_is_respected_on_revocation(db):
    dest = gf.facility(db, "Dest", 12.0, 77.0)
    unv = gf.facility(db, "Unv", 12.1, 77.1, verified=False)
    gf.route(db, dest, unv, 5, 10)
    gf.stock(db, unv, "O_POS", 1)
    em = gf.emergency(db, blood_type="O_POS", target=dest)
    db.add(
        EmergencyOverride(
            emergency_id=em.id,
            kind=EmergencyOverrideKind.UNVERIFIED_FACILITY,
            reason="regional blood bank pending paperwork",
        )
    )
    db.flush()
    primary, _b, _r = eps.create_plan_round(db, em, requested_by_user_id=None, reason="t")
    assert primary.selected_facility_id == unv.id
    assert eps.handle_facility_unverified(db, unv.id, admin_user_id=None) == []


def test_reservation_takes_only_units_in_date_at_arrival_and_is_all_or_nothing(db):
    dest = gf.facility(db, "Dest", 12.0, 77.0)
    src = gf.facility(db, "Src", 12.1, 77.1)
    gf.route(db, dest, src, 20, 30)  # ETA 60
    short = gf.stock(db, src, "O_POS", 1)
    gf.set_expiry(db, short, 100)  # usable at arrival
    em = gf.emergency(db, blood_type="O_POS", units=1, target=dest)
    primary, _b, _r = eps.create_plan_round(db, em, requested_by_user_id=None, reason="t")
    eps.reserve_pathway(db, em, primary, actor_user_id=None)
    reserved = db.scalars(
        select(BloodUnit).where(BloodUnit.reserved_for_emergency_id == em.id)
    ).all()
    assert len(reserved) == 1 and reserved[0].state == BloodUnitState.RESERVED
    # releasing gives exactly those units back
    assert eps.release_pathway_reservations(db, primary, actor_user_id=None, reason="t")
    db.refresh(reserved[0])
    assert reserved[0].state == BloodUnitState.AVAILABLE


def test_release_never_frees_another_emergencys_reservation(db):
    hp = gf.facility(db, "H", 12.0, 77.0)
    gf.stock(db, hp, "O_POS", 2)
    em1 = gf.emergency(db, blood_type="O_POS")
    em2 = gf.emergency(db, blood_type="O_POS")
    inventory_service.reserve_stock(
        db, hp.id, BloodType.O_POS, 1, actor_user_id=None, emergency_id=em1.id
    )
    with pytest.raises(inventory_service.InventoryActionError):
        inventory_service.release_reservation(
            db, hp.id, BloodType.O_POS, 1, actor_user_id=None, emergency_id=em2.id
        )
    held = db.scalars(select(BloodUnit).where(BloodUnit.reserved_for_emergency_id == em1.id)).all()
    assert len(held) == 1


def test_network_is_authoritative_when_both_ends_are_on_it(db):
    dest = gf.facility(db, "Dest", 12.0, 77.0)
    src = gf.facility(db, "Src", 12.1, 77.1)
    r = gf.route(db, dest, src, 12, 20, status=TransportRouteStatus.DISRUPTED)
    gf.stock(db, src, "O_POS", 1)
    result = planner.plan(db, _req(dest, blood_type="O_POS"))
    assert result.primary.feasibility == PathwayFeasibility.IMPOSSIBLE
    assert (
        "no operational route"
        in next(o for o in result.options if o.facility_id == src.id).rejected_reason
    )
    assert r.id  # the disrupted route is what made it unreachable


def test_off_network_source_gets_a_labelled_straight_line_estimate(db):
    dest = gf.facility(db, "Dest", 12.0, 77.0)
    src = gf.facility(db, "Src", 12.2, 77.2)  # no routes recorded at all
    gf.stock(db, src, "O_POS", 1)
    result = planner.plan(db, _req(dest, blood_type="O_POS", deadline_minutes=600))
    opt = result.primary.legs[0].option
    assert opt.route_basis == RouteBasis.STRAIGHT_LINE_ESTIMATE
    assert opt.straight_line_km == pytest.approx(opt.distance_km)
    # an estimate is never classified better than DEADLINE_RISK under a deadline
    assert result.primary.feasibility == PathwayFeasibility.DEADLINE_RISK
    assert any(
        "estimate" in line.lower() or "no recorded" in line.lower()
        for line in result.primary.explanation
    )


def test_plan_round_supersedes_previous_live_rows_and_keeps_one_active(db):
    dest, srcs, _routes = _star(db, 3)
    em = gf.emergency(db, blood_type="O_POS", target=dest)
    eps.create_plan_round(db, em, requested_by_user_id=None, reason="first")
    eps.create_plan_round(db, em, requested_by_user_id=None, reason="second")
    rows = db.scalars(select(EmergencyPathway).where(EmergencyPathway.emergency_id == em.id)).all()
    assert sum(1 for r in rows if r.status == EmergencyPathwayStatus.ACTIVE) == 1
    assert all(r.status == EmergencyPathwayStatus.SUPERSEDED for r in rows if r.plan_round == 1)
