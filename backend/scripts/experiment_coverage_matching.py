"""Paper experiments E5 and E6, run with the REAL code against the seeded
demo database (scripts/seed_demo_data.py, seed 1337 -- SYNTHETIC).

    DATABASE_URL=postgresql+psycopg://.../lifelink \
        python -m scripts.experiment_coverage_matching OUT_DIR [--trials 10]

E5  Coverage under random route disruption, real planner
    (pathway_planner.plan, relational engine). For every verified hospital on
    the transport network and every blood type, a CRITICAL request for
    2 units of whole blood delivered to that hospital, with the hospital's
    own stock withheld (inside a rolled-back savepoint) so a delivery is
    needed. Disruption is simulated with PlanRequest.excluded_route_ids:
    0 % (seeded state) and 10-50 % of the operational routes chosen at
    random (`--trials` trials per level). Recorded per level: share of
    requests fully planned (VIABLE; and VIABLE over recorded routes only,
    i.e. with no labelled straight-line estimate leg from an off-network
    facility), PARTIAL, IMPOSSIBLE; share of planned
    legs that used recorded routes vs. a labelled straight-line estimate;
    median ETA; median plan time.

E6  Donor ranking with and without the donor distance limit, real code
    (matching._donor_candidate_inputs, matching_engine.apply_distance_limit,
    rank_candidates) for every city x recipient blood type: top-10
    composition (eligible, verified, share with a known distance, median of
    the known distances, slots filled) for all four
    strategies.

Nothing is written: everything runs inside transactions that are rolled back.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics as st
import sys
import time
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, update  # noqa: E402

from app.api.routes.matching import _donor_candidate_inputs  # noqa: E402
from app.core.blood_compatibility import compatible_donor_types  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.matching_engine import (  # noqa: E402
    MatchStrategy,
    apply_distance_limit,
    rank_candidates,
)
from app.core.time import utcnow  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.blood_request import BloodRequest  # noqa: E402
from app.models.blood_unit import BloodUnit  # noqa: E402
from app.models.enums import (  # noqa: E402
    BloodComponent,
    BloodType,
    BloodUnitState,
    FacilityType,
    TransportRouteStatus,
    Urgency,
)
from app.models.hospital import HospitalProfile  # noqa: E402
from app.models.transport import TransportRoute  # noqa: E402
from app.services import pathway_planner as planner  # noqa: E402


def e5(db, trials: int) -> dict:
    routes = db.scalars(select(TransportRoute)).all()
    operational = [r.id for r in routes if r.status == TransportRouteStatus.OPERATIONAL]
    on_net = {r.origin_facility_id for r in routes} | {r.destination_facility_id for r in routes}
    hospitals = [
        h
        for h in db.scalars(select(HospitalProfile)).all()
        if h.verified and h.facility_type == FacilityType.HOSPITAL and h.id in on_net
    ]
    levels = [0, 10, 20, 30, 40, 50]
    acc = {
        lvl: {
            "n": 0,
            "feas": defaultdict(int),
            "legs_route": 0,
            "legs_est": 0,
            "routes_only": 0,
            "eta": [],
            "ms": [],
        }
        for lvl in levels
    }
    per_trial = {lvl: defaultdict(lambda: [0, 0]) for lvl in levels}
    for h in hospitals:
        sp = db.begin_nested()
        db.execute(
            update(BloodUnit)
            .where(BloodUnit.hospital_id == h.id, BloodUnit.state == BloodUnitState.AVAILABLE)
            .values(state=BloodUnitState.QUARANTINED)
        )
        db.flush()
        for lvl in levels:
            for trial in range(1 if lvl == 0 else trials):
                rng = random.Random(1000 * lvl + trial)
                down = frozenset(rng.sample(operational, round(len(operational) * lvl / 100)))
                for bt in BloodType:
                    req = planner.PlanRequest(
                        recipient_type=bt,
                        component=BloodComponent.WHOLE_BLOOD,
                        units_needed=2,
                        urgency=Urgency.CRITICAL,
                        deadline=None,
                        destination_facility_id=h.id,
                        now=utcnow(),
                        excluded_route_ids=down,
                        use_graph=False,
                    )
                    t = time.perf_counter()
                    res = planner.plan(db, req)
                    a = acc[lvl]
                    a["ms"].append((time.perf_counter() - t) * 1000)
                    a["n"] += 1
                    f = res.primary.feasibility.value
                    a["feas"][f] += 1
                    per_trial[lvl][trial][0] += 1
                    per_trial[lvl][trial][1] += f == "VIABLE"
                    if res.primary.eta_minutes is not None and f == "VIABLE":
                        a["eta"].append(res.primary.eta_minutes)
                    est = 0
                    for leg in res.primary.legs:
                        if leg.option.steps:
                            a["legs_route"] += 1
                        elif leg.option.facility_id != h.id:
                            a["legs_est"] += 1
                            est += 1
                    # fully planned WITHOUT any straight-line estimate leg
                    a["routes_only"] += f == "VIABLE" and est == 0
        sp.rollback()
    out = []
    for lvl in levels:
        a = acc[lvl]
        trial_cov = [100 * v / n for n, v in per_trial[lvl].values()]
        legs = a["legs_route"] + a["legs_est"]
        out.append(
            {
                "disrupted_pct": lvl,
                "trials": len(trial_cov),
                "requests": a["n"],
                "viable_pct": round(100 * a["feas"]["VIABLE"] / a["n"], 1),
                "viable_pct_min_trial": round(min(trial_cov), 1),
                "viable_pct_max_trial": round(max(trial_cov), 1),
                "viable_on_recorded_routes_only_pct": round(100 * a["routes_only"] / a["n"], 1),
                "partial_pct": round(100 * a["feas"]["PARTIAL"] / a["n"], 1),
                "impossible_pct": round(100 * a["feas"]["IMPOSSIBLE"] / a["n"], 1),
                "legs_on_recorded_routes_pct": (
                    round(100 * a["legs_route"] / legs, 1) if legs else None
                ),
                "eta_median_min": st.median(a["eta"]) if a["eta"] else None,
                "plan_ms_median": round(st.median(a["ms"]), 1),
            }
        )
    return {
        "hospitals": len(hospitals),
        "routes_total": len(routes),
        "routes_operational_seeded": len(operational),
        "request": "CRITICAL, 2 units WHOLE_BLOOD, destination stock withheld",
        "levels": out,
    }


def e6(db) -> dict:
    hospitals = [
        h
        for h in db.scalars(select(HospitalProfile)).all()
        if h.facility_type == FacilityType.HOSPITAL and h.latitude is not None
    ]
    city_pos: dict[str, tuple[float, float]] = {}
    for h in sorted(hospitals, key=lambda x: (x.city or "", str(x.id))):
        if h.city:
            city_pos.setdefault(h.city, (h.latitude, h.longitude))
    today, now = date.today(), datetime.now(UTC)
    limit = settings.max_donor_distance_km
    agg = {(s, lim): defaultdict(list) for s in MatchStrategy for lim in (False, True)}
    excluded_counts, ncand = [], []
    for city, (la, lo) in city_pos.items():
        for bt in BloodType:
            req = BloodRequest(id=uuid.uuid4(), blood_type=bt, latitude=la, longitude=lo, city=city)
            inputs = _donor_candidate_inputs(db, req, compatible_donor_types(bt))
            ncand.append(len(inputs))
            policy = apply_distance_limit(inputs, limit_km=limit)
            excluded_counts.append(len(policy.excluded_refs))
            for lim, cand in ((False, inputs), (True, policy.kept)):
                for s in MatchStrategy:
                    top = rank_candidates(
                        cand, strategy=s, urgency=Urgency.URGENT, today=today, now=now
                    )[:10]
                    a = agg[(s, lim)]
                    a["filled"].append(len(top))
                    if not top:
                        continue
                    f = [r.factors for r in top]
                    a["eligible"].append(100 * sum(bool(x.eligible) for x in f) / len(f))
                    a["verified"].append(100 * sum(bool(x.verified) for x in f) / len(f))
                    a["known"].append(100 * sum(x.distance_km is not None for x in f) / len(f))
                    d = [x.distance_km for x in f if x.distance_km is not None]
                    if d:
                        a["km"].append(st.median(d))
    rows = []
    for s in MatchStrategy:
        for lim in (False, True):
            a = agg[(s, lim)]

            def mean(key, a=a):
                return round(st.mean(a[key]), 1) if a[key] else None

            rows.append(
                {
                    "strategy": s.value,
                    "distance_limit_km": limit if lim else None,
                    "top10_eligible_pct": mean("eligible"),
                    "top10_verified_pct": mean("verified"),
                    "top10_known_distance_pct": mean("known"),
                    "top10_median_km": round(st.median(a["km"]), 1) if a["km"] else None,
                    "top10_slots_filled_mean": round(st.mean(a["filled"]), 2),
                }
            )
    return {
        "scenarios": len(ncand),
        "cities": len(city_pos),
        "candidates_median": st.median(ncand),
        "excluded_by_limit_median": st.median(excluded_counts),
        "rows": rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--trials", type=int, default=10)
    args = ap.parse_args()
    import logging

    logging.disable(logging.WARNING)
    db = SessionLocal()
    try:
        result = {
            "data": "SYNTHETIC demo seed (seed 1337)",
            "generated_at": utcnow().isoformat(),
            "E5_coverage_real_planner": e5(db, args.trials),
            "E6_matching_distance_limit": e6(db),
        }
    finally:
        db.rollback()
        db.close()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "e5_e6_coverage_matching.json").write_text(json.dumps(result, indent=1, default=str))
    print(json.dumps(result, indent=1, default=str))


if __name__ == "__main__":
    main()
