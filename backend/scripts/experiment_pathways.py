"""Paper experiment E4: route-exact re-planning with stored backups, run
with the REAL pathway service against the seeded demo database
(scripts/seed_demo_data.py, seed 1337 -- SYNTHETIC).

    DATABASE_URL=postgresql+psycopg://.../lifelink python -m scripts.experiment_pathways OUT_DIR

For every verified hospital on the transport network and every blood type
(CRITICAL, 2 units of whole blood, delivered to that hospital):

1. withhold the destination's own stock (so a delivery is needed) and
   plan a round (primary + up to 3 backups) with
   emergency_pathway_service.create_plan_round;
2. for EACH route on the primary's stored path, in its own savepoint:
   disrupt it, run handle_route_disrupted, record the outcome
   (BACKUP_PROMOTED / REPLANNED-reachable / REPLANNED-unreachable), the ETA
   change and the wall-clock time; then roll back;
3. disrupt one route NOT on any live pathway and confirm nothing changes;
4. count how many of the step-2 failures the pre-gap-elimination scoping
   (only routes touching the origin or the selected facility) would have
   missed -- i.e. failures of intermediate routes.

Everything runs inside transactions that are rolled back; the database is
left unchanged.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, update  # noqa: E402

from app.core.time import utcnow  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.blood_unit import BloodUnit  # noqa: E402
from app.models.emergency import Emergency  # noqa: E402
from app.models.enums import (  # noqa: E402
    BloodComponent,
    BloodType,
    BloodUnitState,
    EmergencyPathwayStatus,
    EmergencyStatus,
    FacilityType,
    TransportRouteStatus,
    Urgency,
)
from app.models.hospital import HospitalProfile  # noqa: E402
from app.models.transport import TransportRoute  # noqa: E402
from app.models.user import User, UserRole  # noqa: E402
from app.services import emergency_pathway_service as eps  # noqa: E402


def main(out_dir: str) -> None:
    import logging

    logging.disable(logging.WARNING)
    db = SessionLocal()
    routes = db.scalars(select(TransportRoute)).all()
    on_network = {r.origin_facility_id for r in routes} | {
        r.destination_facility_id for r in routes
    }
    hospitals = [
        h
        for h in db.scalars(select(HospitalProfile)).all()
        if h.verified and h.facility_type == FacilityType.HOSPITAL and h.id in on_network
    ]
    actor = db.scalar(select(User).where(User.role == UserRole.ADMIN))
    rows, unrelated_checks, plan_ms = [], {"checked": 0, "changed": 0}, []
    feas = {}
    for h in hospitals:
        for bt in BloodType:
            outer = db.begin_nested()
            # Withhold the destination's own stock (inside the rolled-back
            # savepoint) so every request needs an inter-facility delivery
            # -- otherwise most plans are LOCAL and use no route at all.
            db.execute(
                update(BloodUnit)
                .where(
                    BloodUnit.hospital_id == h.id,
                    BloodUnit.state == BloodUnitState.AVAILABLE,
                )
                .values(state=BloodUnitState.QUARANTINED)
            )
            em = Emergency(
                created_by_user_id=actor.id,
                patient_user_id=actor.id,
                blood_type=bt,
                urgency=Urgency.CRITICAL,
                status=EmergencyStatus.ACTIVE,
                units_needed=2,
                component=BloodComponent.WHOLE_BLOOD,
                target_facility_id=h.id,
            )
            db.add(em)
            db.flush()
            t = time.perf_counter()
            primary, backups, _res = eps.create_plan_round(
                db, em, requested_by_user_id=None, reason="experiment"
            )
            plan_ms.append((time.perf_counter() - t) * 1000)
            f = primary.feasibility.value if primary.feasibility else "NONE"
            feas[f] = feas.get(f, 0) + 1
            path_routes = eps.route_ids_of(db, primary.id)
            legs = eps.legs_of(db, primary.id)
            live_routes = set(path_routes)
            for b in backups:
                live_routes |= set(eps.route_ids_of(db, b.id))
            for idx, rid in enumerate(path_routes):
                sp = db.begin_nested()
                route = db.get(TransportRoute, rid)
                route.status = TransportRouteStatus.DISRUPTED
                db.flush()
                t = time.perf_counter()
                outcomes = eps.handle_route_disrupted(db, route, admin_user_id=None)
                outcome = next(o for o in outcomes if o.emergency_id == em.id)
                ms = (time.perf_counter() - t) * 1000
                new_eta = None
                if outcome.still_reachable:
                    active = db.scalar(
                        select(type(primary)).where(
                            type(primary).emergency_id == em.id,
                            type(primary).status == EmergencyPathwayStatus.ACTIVE,
                        )
                    )
                    new_eta = active.eta_minutes if active else None
                touches_ends = route.origin_facility_id in (
                    h.id,
                    legs[0].source_facility_id,
                ) or route.destination_facility_id in (h.id, legs[0].source_facility_id)
                rows.append(
                    {
                        "hospital": str(h.id),
                        "blood_type": bt.value,
                        "hops": len(path_routes),
                        "position": "first"
                        if idx == 0
                        else ("last" if idx == len(path_routes) - 1 else "middle"),
                        "kind": outcome.kind,
                        "reachable": outcome.still_reachable,
                        "eta_before": primary.eta_minutes,
                        "eta_after": new_eta,
                        "handle_ms": round(ms, 2),
                        "old_scoping_would_detect": bool(touches_ends),
                        "backups_available": len(backups),
                    }
                )
                sp.rollback()
            other = next(
                (
                    r
                    for r in routes
                    if r.id not in live_routes and r.status == TransportRouteStatus.OPERATIONAL
                ),
                None,
            )
            if other is not None:
                sp = db.begin_nested()
                other.status = TransportRouteStatus.DISRUPTED
                db.flush()
                unrelated_checks["checked"] += 1
                if any(
                    o.emergency_id == em.id
                    for o in eps.handle_route_disrupted(db, other, admin_user_id=None)
                ):
                    unrelated_checks["changed"] += 1
                sp.rollback()
            outer.rollback()
    db.rollback()
    db.close()

    def share(pred):
        return round(100 * sum(1 for r in rows if pred(r)) / len(rows), 1) if rows else None

    deltas = [
        r["eta_after"] - r["eta_before"]
        for r in rows
        if r["reachable"] and r["eta_after"] is not None and r["eta_before"] is not None
    ]
    summary = {
        "data": "SYNTHETIC demo seed (seed 1337)",
        "generated_at": utcnow().isoformat(),
        "hospitals": len(hospitals),
        "requests": len(hospitals) * len(BloodType),
        "primary_feasibility": feas,
        "plan_ms": {
            "median": round(statistics.median(plan_ms), 1),
            "p95": round(sorted(plan_ms)[int(0.95 * len(plan_ms)) - 1], 1),
        }
        if plan_ms
        else None,
        "route_failures_simulated": len(rows),
        "by_position": {
            p: sum(1 for r in rows if r["position"] == p) for p in ("first", "middle", "last")
        },
        "backup_promoted_pct": share(lambda r: r["kind"] == "BACKUP_PROMOTED"),
        "replanned_reachable_pct": share(lambda r: r["kind"] == "REPLANNED" and r["reachable"]),
        "unreachable_pct": share(lambda r: not r["reachable"]),
        "old_scoping_missed_pct": share(lambda r: not r["old_scoping_would_detect"]),
        "eta_delta_minutes": {"median": statistics.median(deltas), "max": max(deltas)}
        if deltas
        else None,
        "handle_ms_median": round(statistics.median(r["handle_ms"] for r in rows), 2)
        if rows
        else None,
        "unrelated_route_checks": unrelated_checks,
    }
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "e4_route_exact_replanning.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, indent=1)
    )
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
