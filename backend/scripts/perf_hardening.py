"""Production hardening -- before/after measurements on the volume database.

Never against production. Typical run (see docs/LIFELINK_HARDENING_REPORT.md):

    export DATABASE_URL=.../lifelink_perf REDIS_URL=redis://localhost:6379/7 RQ_SYNC=false
    python -m scripts.perf_hardening measure admin          # admin list + analytics
    python -m scripts.perf_hardening scale-requests 100000   # grow request history
    python -m scripts.perf_hardening measure audit           # full vs incremental verify
    python -m scripts.perf_hardening measure realtime        # inline vs background publish

`measure` prints, per probe: median/max wall time over REPEAT calls of the
real endpoint (in-process, as a real seeded account), statement count, DB
time, and EXPLAIN (ANALYZE, BUFFERS) of the slowest statement (inside a
rolled-back transaction).

`scale-requests N` adds historical (FULFILLED / CANCELLED / EXPIRED)
requests -- copies of existing rows with new ids, spread over the past
365 days -- until the table holds N rows. Open requests are not copied, so
donor/hospital feeds are unaffected; only history-scanning queries grow.
"""

from __future__ import annotations

import statistics
import sys
import tempfile
import time
from pathlib import Path

from sqlalchemy import text

from app.core.config import settings
from app.db.session import SessionLocal, engine
from scripts.perf_explain import capturing, explain, login, short

REPEAT = 7
D = "lifelink-demo.example.com"


def _quiet() -> None:
    import logging

    for name in ("httpx", "lifelink", "lifelink.audit_checkpoint"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _guard() -> None:
    if settings.is_production:
        raise SystemExit("never against production")


def probe(name: str, call, repeat: int = REPEAT, plan: bool = True) -> float:
    times, stmts = [], []
    status = None
    for _ in range(repeat):
        with capturing() as cap:
            t = time.perf_counter()
            resp = call()
            times.append((time.perf_counter() - t) * 1000)
            stmts = list(cap)
        status = getattr(resp, "status_code", "fn")
    med = statistics.median(times)
    print(f"\n## {name}\nstatus={status} median={med:.1f}ms max={max(times):.1f}ms "
          f"statements={len(stmts)} db_ms={sum(s[2] for s in stmts):.1f}")
    if plan and stmts:
        sql, params, ms = max(stmts, key=lambda s: s[2])
        print(f"  slowest {ms:.2f} ms: {short(sql)}")
        if sql.lstrip().upper().startswith(("SELECT", "WITH")):
            lines = explain(sql, params).splitlines()
            print("    PLAN: " + "\n    PLAN: ".join(lines[:14]))
            if len(lines) > 14:
                print("    PLAN: " + "\n    PLAN: ".join(lines[-2:]))
    return med


def counts() -> None:
    with engine.connect() as conn:
        for t in ("blood_requests", "audit_logs", "realtime_events", "notifications",
                  "blood_units", "users"):
            print(f"{t}: {conn.execute(text(f'select count(*) from {t}')).scalar_one()}")


def measure_admin() -> None:
    admin = login(f"demo.admin@{D}")
    probe("admin: GET /admin/blood-requests (newest 50)",
          lambda: admin.get("/admin/blood-requests"))
    probe("admin: GET /admin/blood-requests?status=FULFILLED",
          lambda: admin.get("/admin/blood-requests", params={"status": "FULFILLED"}))
    probe("admin: GET /admin/analytics/overview (14 days)",
          lambda: admin.get("/admin/analytics/overview"))
    probe("admin: GET /admin/analytics/overview (90 days)",
          lambda: admin.get("/admin/analytics/overview", params={"days": 90}))


def measure_audit() -> None:
    admin = login(f"demo.admin@{D}")
    tmp = Path(tempfile.mkdtemp(prefix="lifelink-perf-anchor-")) / "checkpoints.jsonl"
    settings.audit_checkpoint_key = "p" * 48
    settings.audit_checkpoint_path = str(tmp)
    try:
        r = admin.post("/admin/audit-log/checkpoint")
        print(f"checkpoint: {r.status_code} {r.json()}")
        probe("audit: POST /admin/audit-log/verify (full, with checkpoints)",
              lambda: admin.post("/admin/audit-log/verify"), repeat=3, plan=False)
        probe("audit: POST /admin/audit-log/verify?mode=incremental",
              lambda: admin.post("/admin/audit-log/verify", params={"mode": "incremental"}),
              repeat=REPEAT, plan=False)
        body = admin.post("/admin/audit-log/verify", params={"mode": "incremental"}).json()
        print(f"incremental result: ok={body['ok']} checked={body['checked']} "
              f"checkpoints={body['checkpoints']}")
    finally:
        settings.audit_checkpoint_key = ""
        settings.audit_checkpoint_path = ""


def measure_realtime() -> None:
    from app.services import realtime_service

    patient = login(f"demo.patient@{D}")
    with engine.connect() as conn:
        conn.execute(text("select 1"))

    def create_request():
        return patient.post("/blood-requests", json={"blood_type": "A_POS", "units_needed": 1})

    def cancel_open() -> None:
        # keep the demo patient's open requests bounded between runs
        with engine.begin() as conn:
            conn.execute(text(
                "update blood_requests set status='CANCELLED', cancelled_at=now() "
                "where requester_user_id=(select id from users where email=:e) "
                "and status in ('PENDING','MATCHING') and note is null "
                "and created_at > now() - interval '1 hour'"
            ), {"e": f"demo.patient@{D}"})

    # Measurement-only: lift the per-account creation limit so 30 requests in
    # a row are not throttled (429) -- the limit itself is tested elsewhere.
    settings.rl_blood_request_create_account = (100000, 60)
    from app.core.redis import redis_client

    for k in redis_client.scan_iter("rl:blood_request_create:*"):
        redis_client.delete(k)
    res: dict[str, list[float]] = {"inline": [], "background": []}
    events: dict[str, list[int]] = {"inline": [], "background": []}
    flush_ms: list[float] = []
    for i in range(30):  # interleaved, so warm-up/drift hits both modes equally
        mode = ("inline", "background")[i % 2]
        settings.realtime_publish_mode = mode
        realtime_service.flush()
        with engine.connect() as conn:
            before = conn.execute(text("select count(*) from realtime_events")).scalar_one()
        t = time.perf_counter()
        r = create_request()
        res[mode].append((time.perf_counter() - t) * 1000)
        assert r.status_code == 201, r.text
        t = time.perf_counter()
        realtime_service.flush()
        if mode == "background":
            flush_ms.append((time.perf_counter() - t) * 1000)
        with engine.connect() as conn:
            after = conn.execute(text("select count(*) from realtime_events")).scalar_one()
        events[mode].append(after - before)
        cancel_open()
    for mode in ("inline", "background"):
        v = res[mode]
        print(f"\n## realtime [{mode}]: POST /blood-requests, request-thread latency (n={len(v)})\n"
              f"median={statistics.median(v):.1f}ms p90={sorted(v)[int(len(v) * .9) - 1]:.1f}ms "
              f"max={max(v):.1f}ms events/request median={statistics.median(events[mode])}")
    print(f"  background: wait for publisher after response, median "
          f"{statistics.median(flush_ms):.1f}ms max {max(flush_ms):.1f}ms")
    settings.realtime_publish_mode = "background"


def scale_requests(target: int) -> None:
    with engine.begin() as conn:
        have = conn.execute(text("select count(*) from blood_requests")).scalar_one()
        need = target - have
        print(f"blood_requests: {have} -> {target} (+{max(need, 0)})")
        while need > 0:
            batch = min(need, 20000)
            conn.execute(text("""
                insert into blood_requests (
                    id, requester_user_id, patient_user_id, blood_type, units_needed,
                    urgency, status, hospital_name, city, note, needed_by, fulfilled_at,
                    created_at, updated_at, latitude, longitude, cancelled_at, expired_at,
                    fulfilled_units)
                select gen_random_uuid(), requester_user_id, patient_user_id, blood_type,
                    units_needed, urgency, status, hospital_name, city, note, needed_by,
                    case when status = 'FULFILLED' then ts end,
                    ts, ts, latitude, longitude,
                    case when status = 'CANCELLED' then ts end,
                    case when status = 'EXPIRED' then ts end,
                    case when status = 'FULFILLED' then units_needed else 0 end
                from (
                    select r.*, now() - (random() * interval '365 days') as ts
                    from blood_requests r
                    where r.status in ('FULFILLED', 'CANCELLED', 'EXPIRED')
                    order by random() limit :n
                ) src
            """), {"n": batch})
            need -= batch
    with engine.connect() as conn:
        conn.execution_options(isolation_level="AUTOCOMMIT").execute(
            text("vacuum analyze blood_requests"))
    counts()


def main(argv: list[str]) -> None:
    _guard()
    _quiet()
    if argv[:1] == ["scale-requests"]:
        scale_requests(int(argv[1]))
        return
    if argv[:1] != ["measure"] or len(argv) < 2:
        raise SystemExit(__doc__)
    db = SessionLocal()
    db.close()
    print("# Volume")
    counts()
    for what in argv[1:]:
        {"admin": measure_admin, "audit": measure_audit, "realtime": measure_realtime}[what]()


if __name__ == "__main__":
    main(sys.argv[1:])
