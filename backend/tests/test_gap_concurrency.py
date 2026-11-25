"""Gap elimination section 35: concurrency against real PostgreSQL with real
threads (separate connections, real row locks)."""

from __future__ import annotations

import threading

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.blood_unit import BloodUnit
from app.models.emergency import Emergency
from app.models.emergency_pathway import EmergencyPathway
from app.models.enums import BloodUnitState, EmergencyPathwayStatus, TransportRouteStatus
from app.services import emergency_pathway_service as eps
from app.services import invariant_check
from app.services.inventory_service import InventoryActionError
from tests import gap_fixtures as gf


def _run(n, fn):
    barrier = threading.Barrier(n)
    results, errors = [], []

    def worker(i):
        db = SessionLocal()
        try:
            barrier.wait()
            results.append(fn(db, i))
            db.commit()
        except Exception as exc:  # noqa: BLE001 - collected and asserted on
            db.rollback()
            errors.append(exc)
        finally:
            db.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return results, errors


def test_two_emergencies_cannot_reserve_the_same_last_units():
    with SessionLocal() as db:
        dest1 = gf.facility(db, "D1", 12.0, 77.0)
        dest2 = gf.facility(db, "D2", 12.0, 77.1)
        src = gf.facility(db, "Src", 12.1, 77.05)
        gf.route(db, dest1, src, 5, 10)
        gf.route(db, dest2, src, 5, 10)
        gf.stock(db, src, "O_POS", 2)
        ems = [gf.emergency(db, blood_type="O_POS", units=2, target=d) for d in (dest1, dest2)]
        pws = [eps.create_plan_round(db, e, requested_by_user_id=None, reason="t")[0] for e in ems]
        ids = [(e.id, p.id) for e, p in zip(ems, pws, strict=True)]
        db.commit()

    def reserve(db, i):
        em = db.get(Emergency, ids[i][0])
        pw = db.get(EmergencyPathway, ids[i][1])
        return eps.reserve_pathway(db, em, pw, actor_user_id=None)

    results, errors = _run(2, reserve)
    assert len(results) == 1 and len(errors) == 1
    assert isinstance(errors[0], InventoryActionError)
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(BloodUnit)
                .where(BloodUnit.state == BloodUnitState.RESERVED)
            )
            == 2
        )
        assert invariant_check.violations(db) == {}


def test_concurrent_replans_leave_exactly_one_active_pathway():
    with SessionLocal() as db:
        dest = gf.facility(db, "Dest", 12.0, 77.0)
        for i in range(3):
            s = gf.facility(db, f"S{i}", 12.1 + i * 0.05, 77.0)
            gf.route(db, dest, s, 5 + i, 10 + i)
            gf.stock(db, s, "A_POS", 2)
        em = gf.emergency(db, blood_type="A_POS", target=dest)
        eid = em.id
        db.commit()

    def replan(db, i):
        return eps.create_plan_round(
            db, db.get(Emergency, eid), requested_by_user_id=None, reason=f"concurrent {i}"
        )[0].version

    results, errors = _run(4, replan)
    assert errors == [] and len(results) == 4
    with SessionLocal() as db:
        active = db.scalar(
            select(func.count())
            .select_from(EmergencyPathway)
            .where(
                EmergencyPathway.emergency_id == eid,
                EmergencyPathway.status == EmergencyPathwayStatus.ACTIVE,
            )
        )
        assert active == 1
        assert invariant_check.violations(db) == {}


def test_concurrent_disruptions_of_two_routes_fail_over_consistently():
    with SessionLocal() as db:
        dest = gf.facility(db, "Dest", 12.0, 77.0)
        routes = []
        for i in range(3):
            s = gf.facility(db, f"S{i}", 12.1 + i * 0.05, 77.0)
            routes.append(gf.route(db, dest, s, 5 + i, 10 + i))
            gf.stock(db, s, "B_POS", 2)
        em = gf.emergency(db, blood_type="B_POS", target=dest)
        eps.create_plan_round(db, em, requested_by_user_id=None, reason="t")
        rids = [r.id for r in routes[:2]]
        eid = em.id
        db.commit()

    def disrupt(db, i):
        from app.models.transport import TransportRoute

        r = db.get(TransportRoute, rids[i], with_for_update=True)
        r.status = TransportRouteStatus.DISRUPTED
        db.flush()
        return eps.handle_route_disrupted(db, r, admin_user_id=None)

    _results, errors = _run(2, disrupt)
    assert errors == []
    with SessionLocal() as db:
        rows = db.scalars(
            select(EmergencyPathway).where(EmergencyPathway.emergency_id == eid)
        ).all()
        active = [p for p in rows if p.status == EmergencyPathwayStatus.ACTIVE]
        assert len(active) == 1
        # the surviving plan never uses a disrupted route
        assert not set(eps.route_ids_of(db, active[0].id)) & set(rids)
        assert invariant_check.violations(db) == {}


def test_concurrent_first_requests_create_exactly_one_profile_and_never_500():
    """Found by the full E2E run: the SPA's parallel first calls for a new
    donor (/donors/me and /donors/me/responses) both inserted a profile and
    the loser failed with a 500 on the unique user_id index."""
    from app.models.profile import DonorProfile
    from app.services.domain_service import get_or_create_donor_profile
    from tests.helpers import auth_client

    _, email = auth_client("DONOR")
    with SessionLocal() as db:
        from app.models.user import User

        user_id = db.scalar(select(User.id).where(User.email == email))
        db.execute(DonorProfile.__table__.delete().where(DonorProfile.user_id == user_id))
        db.commit()

    def first_call(db, _i):
        from app.models.user import User

        return get_or_create_donor_profile(db, db.get(User, user_id)).id

    results, errors = _run(6, first_call)
    assert errors == []
    assert len(set(results)) == 1
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(DonorProfile)
                .where(DonorProfile.user_id == user_id)
            )
            == 1
        )
