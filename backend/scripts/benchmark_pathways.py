"""Reproducible pathway-engine benchmark on SYNTHETIC networks.

    python -m scripts.benchmark_pathways --database-url postgresql+psycopg://.../lifelink_bench \
        --sizes small medium large --requests 50 --seed 7 --out ../docs/final-verification

What it does, per network size (small = 20 facilities, medium = 100,
large = 400 -- override with --facilities):

1. Recreates the schema in the given database (it REFUSES to run against a
   database whose name does not contain "bench" -- it drops everything).
2. Builds a deterministic synthetic network: facilities on a jittered grid,
   a ring of routes plus random chords (average degree ~4), ~5% of routes
   DISRUPTED, and per-unit stock through the real inventory service.
3. Runs the real planner (pathway_planner.plan, relational path engine) for
   `--requests` random (destination, blood type, component, urgency,
   deadline) requests and records wall-clock time per plan.
4. Captures EXPLAIN (ANALYZE, BUFFERS) for the two SQL statements the
   planner issues (candidate facilities, usable-units-at-arrival).

Everything is synthetic and labelled as such in the output. Timings depend
on the machine; the script prints the machine and versions it ran on.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import random
import statistics
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SIZES = {"small": 20, "medium": 100, "large": 400}


def build(db, n: int, rng: random.Random):
    from app.core.security import hash_password
    from app.core.time import utcnow
    from app.models.enums import (
        BloodComponent,
        BloodType,
        FacilityType,
        TransportRouteStatus,
    )
    from app.models.hospital import HospitalProfile
    from app.models.transport import TransportRoute
    from app.models.user import User, UserRole
    from app.services import inventory_service

    pw = hash_password("benchmark-only-password")
    facilities = []
    side = math.ceil(math.sqrt(n))
    for i in range(n):
        u = User(
            email=f"bench-{i}@bench.example.com",
            password_hash=pw,
            full_name=f"Bench {i}",
            role=UserRole.HOSPITAL,
            is_email_verified=True,
            is_active=True,
            password_changed_at=utcnow(),
        )
        db.add(u)
        db.flush()
        lat = 12.0 + (i // side) * 0.15 + rng.uniform(-0.03, 0.03)
        lon = 77.0 + (i % side) * 0.15 + rng.uniform(-0.03, 0.03)
        hp = HospitalProfile(
            user_id=u.id,
            name=f"Bench Facility {i}",
            city=f"Grid{i // side}",
            latitude=lat,
            longitude=lon,
            verified=rng.random() > 0.05,
            facility_type=FacilityType.HOSPITAL,
        )
        db.add(hp)
        facilities.append(hp)
    db.flush()

    def km(a, b):
        from app.models.enums import haversine_km

        return max(0.5, haversine_km(a.latitude, a.longitude, b.latitude, b.longitude) * 1.3)

    pairs = set()
    for i in range(n):
        pairs.add(tuple(sorted((i, (i + 1) % n))))
    while len(pairs) < 2 * n:
        a, b = rng.randrange(n), rng.randrange(n)
        if a != b:
            pairs.add(tuple(sorted((a, b))))
    routes = 0
    for a, b in sorted(pairs):
        fa, fb = facilities[a], facilities[b]
        o, d = sorted((fa.id, fb.id), key=str)
        dist = km(fa, fb)
        db.add(
            TransportRoute(
                origin_facility_id=o,
                destination_facility_id=d,
                distance_km=round(dist, 2),
                transit_minutes=max(2, round(dist / 40 * 60)),
                status=(
                    TransportRouteStatus.DISRUPTED
                    if rng.random() < 0.05
                    else TransportRouteStatus.OPERATIONAL
                ),
            )
        )
        routes += 1
    db.flush()
    types = list(BloodType)
    units = 0
    for hp in facilities:
        for bt in rng.sample(types, 3):
            k = rng.randint(1, 6)
            inventory_service.reconcile_inventory(
                db, hp.id, bt, k, actor_user_id=None, note="synthetic benchmark stock"
            )
            units += k
        if rng.random() < 0.3:
            k = rng.randint(1, 4)
            inventory_service.reconcile_inventory(
                db,
                hp.id,
                rng.choice(types),
                k,
                actor_user_id=None,
                component=BloodComponent.PLASMA,
                note="synthetic benchmark stock",
            )
            units += k
    db.commit()
    return facilities, routes, units


def explain(db, sql_text, params) -> list[str]:
    from sqlalchemy import text

    rows = db.execute(
        text("EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) " + sql_text.text), params
    ).all()
    return [r[0] for r in rows]


def run_size(url: str, name: str, n: int, requests: int, seed: int) -> dict:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.core import compatibility
    from app.core.time import utcnow
    from app.db.base import Base
    from app.models.enums import BloodComponent, BloodType, Urgency
    from app.services import pathway_planner as planner

    engine = create_engine(url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    rng = random.Random(seed)
    t0 = time.perf_counter()
    facilities, routes, units = build(db, n, rng)
    build_s = time.perf_counter() - t0

    timings, feas = [], {}
    for _ in range(requests):
        dest = rng.choice(facilities)
        req = planner.PlanRequest(
            recipient_type=rng.choice(list(BloodType)),
            component=rng.choice(
                [BloodComponent.WHOLE_BLOOD, BloodComponent.WHOLE_BLOOD, BloodComponent.PLASMA]
            ),
            units_needed=rng.randint(1, 4),
            urgency=rng.choice(list(Urgency)),
            deadline=utcnow() + timedelta(minutes=rng.choice([60, 120, 240, 600])),
            destination_facility_id=dest.id,
            now=utcnow(),
            use_graph=False,
        )
        s = time.perf_counter()
        result = planner.plan(db, req)
        timings.append((time.perf_counter() - s) * 1000)
        feas[result.primary.feasibility.value] = feas.get(result.primary.feasibility.value, 0) + 1
    timings.sort()
    dest = facilities[0]
    compat = compatibility.compatible_source_types(BloodType.O_POS, BloodComponent.WHOLE_BLOOD)
    plan_cand = explain(
        db,
        planner._CANDIDATE_SQL,
        {
            "component": "WHOLE_BLOOD",
            "types": [t.value for t in compat],
            "em": None,
            "now": utcnow(),
        },
    )
    ids = [str(f.id) for f in facilities[: min(50, n)]]
    plan_usable = explain(
        db,
        planner._USABLE_AT_ARRIVAL_SQL,
        {
            "hids": ids,
            "cutoffs": [utcnow() + timedelta(minutes=90)] * len(ids),
            "component": "WHOLE_BLOOD",
            "types": [t.value for t in compat],
            "em": None,
        },
    )
    db.close()
    engine.dispose()

    def pct(p):
        return round(timings[min(len(timings) - 1, int(p / 100 * len(timings)))], 2)

    return {
        "size": name,
        "facilities": n,
        "routes": routes,
        "units": units,
        "build_seconds": round(build_s, 2),
        "requests": requests,
        "plan_ms": {
            "p50": pct(50),
            "p95": pct(95),
            "max": round(timings[-1], 2),
            "mean": round(statistics.mean(timings), 2),
        },
        "feasibility": feas,
        "dest_example": str(dest.id),
        "explain_candidates": plan_cand,
        "explain_usable_at_arrival": plan_usable,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--database-url", required=True)
    ap.add_argument("--sizes", nargs="+", default=["small", "medium", "large"])
    ap.add_argument("--facilities", type=int, default=None, help="override the size's count")
    ap.add_argument("--requests", type=int, default=50)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    if "bench" not in args.database_url.rsplit("/", 1)[-1]:
        sys.exit("refusing: the database name must contain 'bench' (it is dropped and rebuilt)")
    os.environ["DATABASE_URL"] = args.database_url
    import logging

    logging.disable(logging.WARNING)
    results = [
        run_size(args.database_url, s, args.facilities or SIZES[s], args.requests, args.seed)
        for s in args.sizes
    ]
    import sqlalchemy

    meta = {
        "data": f"SYNTHETIC (generated by this script, seed {args.seed})",
        "python": platform.python_version(),
        "machine": platform.machine(),
        "processor": platform.processor() or "unknown",
        "cpu_count": os.cpu_count(),
        "sqlalchemy": sqlalchemy.__version__,
        "path_engine": "RELATIONAL_ROUTES",
    }
    out = {"meta": meta, "results": results}
    print(json.dumps(dict(out.items()), indent=1, default=str)[:4000])
    if args.out:
        Path(args.out).mkdir(parents=True, exist_ok=True)
        (Path(args.out) / "benchmark_pathways.json").write_text(
            json.dumps(out, indent=1, default=str)
        )


if __name__ == "__main__":
    main()
