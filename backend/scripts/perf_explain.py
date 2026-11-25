"""Phase K -- capture the SQL the real endpoints run, then EXPLAIN ANALYZE it.

Against the volume database built by `scripts.perf_seed` (never production):

    DATABASE_URL=.../lifelink_perf REDIS_URL=redis://localhost:6379/7 \
        python -m scripts.perf_explain > perf_report.txt

For each endpoint in PROBES (matching, reservation, allocation, emergency,
notification, admin, map, audit, realtime replay, hospital and patient
reads) it:
  1. calls the real route in-process (FastAPI TestClient, signed in as a
     real seeded account), recording every SQL statement with its bound
     parameters and wall time via SQLAlchemy cursor events;
  2. reports request wall time, statement count, and the slowest
     statements;
  3. re-runs each statement slower than THRESHOLD_MS -- and every
     statement on the hot paths -- under EXPLAIN (ANALYZE, BUFFERS) inside a
     transaction that is ROLLED BACK (so EXPLAIN ANALYZE of a write changes
     nothing), printing the plan.

Nothing here edits indexes or data; it only measures.
"""

from __future__ import annotations

import re
import statistics
import sys
import time
from contextlib import contextmanager

from fastapi.testclient import TestClient
from sqlalchemy import event, select, text

from app.core.config import settings
from app.db.session import SessionLocal, engine
from app.main import app
from app.models.emergency import Emergency
from app.models.enums import EmergencyStatus
from app.models.user import User
from app.services import realtime_service

PW = "LifeLink-Demo-2026!"
ORIGIN = {"Origin": settings.cors_origin_list[0]}
THRESHOLD_MS = 5.0
REPEAT = 5

captured: list[tuple[str, object, float]] = []
_t0: dict[int, float] = {}


@event.listens_for(engine, "before_cursor_execute")
def _before(conn, cursor, statement, params, context, executemany):
    _t0[id(cursor)] = time.perf_counter()


@event.listens_for(engine, "after_cursor_execute")
def _after(conn, cursor, statement, params, context, executemany):
    if capture_on[0]:
        captured.append(
            (
                statement,
                params,
                (time.perf_counter() - _t0.pop(id(cursor), time.perf_counter())) * 1000,
            )
        )


capture_on = [False]


@contextmanager
def capturing():
    captured.clear()
    capture_on[0] = True
    try:
        yield captured
    finally:
        capture_on[0] = False


def login(email: str) -> TestClient:
    c = TestClient(app, base_url="http://perf.local")
    c.headers.update(ORIGIN)
    r = c.post("/auth/login", json={"email": email, "password": PW})
    if r.status_code != 200:
        raise SystemExit(f"login failed for {email}: {r.status_code} {r.text}")
    return c


def explain(statement: str, params) -> str:
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            rows = conn.exec_driver_sql("EXPLAIN (ANALYZE, BUFFERS) " + statement, params).all()
        finally:
            trans.rollback()
    return "\n".join(r[0] for r in rows)


def short(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()[:160]


def probe(name: str, call, hot: bool = False) -> None:
    times, counts = [], []
    stmts: list = []
    status = None
    for _ in range(REPEAT):
        with capturing() as cap:
            t = time.perf_counter()
            resp = call()
            times.append((time.perf_counter() - t) * 1000)
            counts.append(len(cap))
            stmts = list(cap)
        status = getattr(resp, "status_code", "fn")
    med = statistics.median(times)
    print(
        f"\n## {name}\nstatus={status} median={med:.1f}ms p_max={max(times):.1f}ms "
        f"statements={counts[-1]} db_ms={sum(s[2] for s in stmts):.1f}"
    )
    seen = set()
    for sql, params, ms in sorted(stmts, key=lambda s: -s[2])[:4]:
        key = short(sql)
        if key in seen:
            continue
        seen.add(key)
        print(f"  {ms:7.2f} ms  {key}")
        executemany = (
            isinstance(params, (list, tuple))
            and params
            and isinstance(params[0], (dict, tuple, list))
        )
        if (
            (ms >= THRESHOLD_MS or hot)
            and not executemany
            and sql.lstrip().upper().startswith(("SELECT", "WITH", "UPDATE", "DELETE"))
        ):
            plan = explain(sql, params)
            first = plan.splitlines()
            print("    PLAN: " + "\n    PLAN: ".join(first[:18]))
            if len(first) > 18:
                print(f"    PLAN: ... ({len(first) - 18} more lines)")
                print("    PLAN: " + "\n    PLAN: ".join(first[-2:]))


def main() -> None:
    if settings.is_production:
        raise SystemExit("never against production")
    db = SessionLocal()
    d = "lifelink-demo.example.com"
    try:
        busiest_donor = db.execute(
            text(
                "select u.email from notifications n join users u on u.id=n.user_id "
                "where u.role='DONOR' and u.email like '%@lifelink-demo.example.com' "
                "group by u.email order by count(*) desc limit 1"
            )
        ).scalar_one()
        # The demo patient's own open request (seed_demo_data activity):
        # match candidates are only shown to the request's own side.
        open_req = db.execute(
            text(
                "select r.id from blood_requests r join users u on u.id=r.requester_user_id "
                f"where u.email='demo.patient@{d}' and r.status in ('PENDING','MATCHING') limit 1"
            )
        ).scalar_one()
        open_em = db.scalar(
            select(Emergency.id).where(Emergency.status == EmergencyStatus.ACTIVE).limit(1)
        )
        demo_hosp_fid = db.execute(
            text(
                "select h.id from hospital_profiles h join users u on u.id=h.user_id "
                f"where u.email='demo.hospital@{d}'"
            )
        ).scalar_one()
        admin_id = db.scalar(select(User.id).where(User.email == f"demo.admin@{d}"))
        counts = {
            t: db.execute(text(f"select count(*) from {t}")).scalar_one()
            for t in (
                "users",
                "blood_units",
                "hospital_inventory",
                "blood_requests",
                "donor_responses",
                "emergencies",
                "notifications",
                "audit_logs",
                "realtime_events",
                "hospital_inventory_logs",
            )
        }
        head = realtime_service.head_seq(db)
    finally:
        db.close()
    print("# Volume\n" + "\n".join(f"{k}: {v}" for k, v in counts.items()))
    print(f"busiest donor: {busiest_donor}")

    admin = login(f"demo.admin@{d}")
    hosp = login(f"demo.hospital@{d}")
    donor = login(busiest_donor)
    patient = login(f"demo.patient@{d}")

    # Matching
    probe(
        "matching: request match-candidates (requester)",
        lambda: patient.get(f"/blood-requests/{open_req}/match-candidates"),
        hot=True,
    )
    probe(
        "matching: emergency match-candidates",
        lambda: admin.get(f"/emergencies/{open_em}/match-candidates"),
        hot=True,
    )
    probe(
        "matching: donor feed /donors/me/requests",
        lambda: donor.get("/donors/me/requests"),
        hot=True,
    )
    # Reservation / allocation (each call really reserves/allocates on the demo facility)
    probe(
        "reservation: POST /hospitals/me/inventory/reserve",
        lambda: hosp.post(
            "/hospitals/me/inventory/reserve", json={"blood_type": "O_POS", "units": 1}
        ),
        hot=True,
    )
    probe(
        "allocation: POST /hospitals/me/inventory/allocate",
        lambda: hosp.post(
            "/hospitals/me/inventory/allocate", json={"blood_type": "O_POS", "units": 1}
        ),
        hot=True,
    )
    probe(
        "inventory: GET /hospitals/me/inventory/components (sweeps)",
        lambda: hosp.get("/hospitals/me/inventory/components"),
        hot=True,
    )
    probe(
        "inventory: GET /hospitals/me/inventory/log",
        lambda: hosp.get("/hospitals/me/inventory/log"),
    )
    # Emergency
    probe(
        "emergency: GET /hospitals/me/emergencies",
        lambda: hosp.get("/hospitals/me/emergencies"),
        hot=True,
    )
    probe(
        "emergency: GET /emergencies/active (donor)",
        lambda: donor.get("/emergencies/active"),
        hot=True,
    )
    probe(
        "hospital: GET /hospitals/me/requests", lambda: hosp.get("/hospitals/me/requests"), hot=True
    )
    # Notifications
    probe("notifications: GET /notifications", lambda: donor.get("/notifications"), hot=True)
    probe(
        "notifications: GET /notifications?unread_only=true",
        lambda: donor.get("/notifications", params={"unread_only": "true"}),
        hot=True,
    )
    probe(
        "notifications: GET /notifications/unread-count",
        lambda: donor.get("/notifications/unread-count"),
        hot=True,
    )
    # Patient
    probe("patient: GET /blood-requests", lambda: patient.get("/blood-requests"))
    # Admin
    probe("admin: GET /admin/facilities", lambda: admin.get("/admin/facilities"), hot=True)
    probe(
        "admin: GET /admin/facilities/{id} (sweeps)",
        lambda: admin.get(f"/admin/facilities/{demo_hosp_fid}"),
        hot=True,
    )
    probe("admin: GET /admin/blood-requests", lambda: admin.get("/admin/blood-requests"), hot=True)
    probe("admin: GET /admin/emergencies", lambda: admin.get("/admin/emergencies"))
    probe("admin: GET /admin/users", lambda: admin.get("/admin/users"))
    probe("admin: GET /admin/donors", lambda: admin.get("/admin/donors"))
    probe(
        "admin: GET /admin/analytics/overview",
        lambda: admin.get("/admin/analytics/overview"),
        hot=True,
    )
    probe("admin: GET /admin/users/stats", lambda: admin.get("/admin/users/stats"))
    # Map
    probe("map: GET /map/data (hospital)", lambda: hosp.get("/map/data"), hot=True)
    # Audit
    probe("audit: GET /admin/audit-log", lambda: admin.get("/admin/audit-log"), hot=True)
    probe("audit: GET /admin/audit-log/head", lambda: admin.get("/admin/audit-log/head"), hot=True)
    probe(
        "audit: POST /admin/audit-log/verify (full chain)",
        lambda: admin.post("/admin/audit-log/verify", json={}),
        hot=True,
    )

    # Realtime replay (the SQL the SSE endpoint runs for Last-Event-ID)
    def _replay():
        s = SessionLocal()
        try:
            return realtime_service.replay(s, admin_id, "ADMIN", max(0, head - 400), head, 500)
        finally:
            s.close()

    probe("realtime: replay last 400 events (admin)", _replay, hot=True)
    print("\n# end")


if __name__ == "__main__":
    sys.exit(main())
