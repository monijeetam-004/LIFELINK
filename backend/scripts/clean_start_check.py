"""Clean-start verification: prove a freshly initialised LifeLink stack is
USABLE, not just that its processes are up.

    python -m scripts.clean_start_check --api http://127.0.0.1:8000 \
        --frontend http://127.0.0.1:5173 --mail-api http://127.0.0.1:8025/api/v1/messages \
        --out clean_start_result.json

Reads the same environment as the API (DATABASE_URL, REDIS_URL, S3_*, CLAMD_*,
NEO4J_*). Every check really exercises its dependency:

  api_ready         GET /health/ready answers 200 with status "ready"
  database          SELECT through DATABASE_URL
  alembic_head      the database revision equals the repository's single head
                    (scripts/migration_doctor), and the schema equals the models
  redis             PING through REDIS_URL
  neo4j             a real query through the app's driver; BLOCKED when it is not
                    configured or not reachable. Never reported as PASS untested.
  object_storage    the bucket exists; an object is written, read back byte for
                    byte, then deleted; the server's identity is recorded
                    (MinIO, or a named S3 stand-in)
  clamav            clamd answers PING and VERSION, and really scans: a clean
                    payload comes back OK and the EICAR test string comes back
                    FOUND
  worker_and_mail   POST /auth/register enqueues the verification email on RQ;
                    the separate worker process sends it over SMTP; the mail
                    catcher's HTTP API shows it. The token in that email then
                    verifies the account.
  seed_data         row counts of the synthetic seed (users by role, facilities,
                    routes, blood units)
  frontend          the built SPA is served, and its bundle targets this API
                    (a browser check of the same is in
                    frontend/e2e/clean_start.spec.ts)
  critical_workflow the emergency-to-delivery workflow over HTTP, with the demo
                    accounts: SOS, plan, reserve, route failure and backup
                    failover, release, shipment with cold-chain telemetry,
                    receipt into stock, resolve, audit-chain verification
  document_upload   the verified account uploads a clean PDF (scanned by clamd,
                    stored in object storage) and an EICAR PDF, which is refused

Statuses: PASS, FAIL, BLOCKED (the dependency is unavailable here; external
validation required). Exit code 0 only if nothing FAILED.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ORIGIN_HEADER = "Origin"
RESULTS: list[dict] = []
# The EICAR anti-malware TEST string (harmless, industry standard), assembled
# at run time so this file is not itself flagged by scanners.
EICAR = ("X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + "EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*").encode()
CLEAN_PDF = (
    b"%PDF-1.4\n1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
    b"2 0 obj << /Type /Pages /Kids [] /Count 0 >> endobj\n"
    b"trailer << /Root 1 0 R >>\n%%EOF\n"
)


def first(items, what: str):
    """next() that fails with a readable reason instead of StopIteration."""
    for x in items:
        return x
    raise AssertionError(f"not found: {what}")


def record(name: str, status: str, detail: str, **data) -> None:
    RESULTS.append(
        {
            "check": name,
            "status": status,
            "detail": detail,
            "at": datetime.now(UTC).isoformat(),
            **data,
        }
    )
    print(f"[{status:7}] {name}: {detail}", flush=True)


def check(name):
    def deco(fn):
        def run(*a, **k):
            try:
                fn(*a, **k)
            except Exception as exc:  # a crash is a FAIL with its reason, never hidden
                record(name, "FAIL", f"{type(exc).__name__}: {exc}")

        return run

    return deco


def client(api: str, origin: str) -> httpx.Client:
    return httpx.Client(base_url=api, headers={ORIGIN_HEADER: origin}, timeout=30.0)


def login(api: str, origin: str, email: str, password: str) -> httpx.Client:
    c = client(api, origin)
    r = c.post("/auth/login", json={"email": email, "password": password})
    if r.status_code != 200:
        raise RuntimeError(f"login {email}: {r.status_code} {r.text[:200]}")
    return c


# --------------------------------------------------------------------------- #


@check("api_ready")
def c_api(api):
    r = httpx.get(f"{api}/health/ready", timeout=10)
    body = r.json()
    ok = r.status_code == 200 and body.get("status") == "ready"
    record(
        "api_ready",
        "PASS" if ok else "FAIL",
        f"HTTP {r.status_code} status={body.get('status')}",
        response=body,
    )


@check("database")
def c_db():
    from sqlalchemy import text

    from app.db.session import engine

    with engine.connect() as conn:
        v = conn.execute(text("select version()")).scalar()
    record("database", "PASS", v.split(",")[0])


@check("alembic_head")
def c_alembic():
    from scripts import migration_doctor

    d = migration_doctor.diagnose()
    ok = (
        d.state == "AT_HEAD"
        and d.db_revisions == [d.repo_head]
        and d.schema_vs_models.get("matches_current_models") is True
    )
    record(
        "alembic_head",
        "PASS" if ok else "FAIL",
        f"state={d.state} db={d.db_revisions} repo_head={d.repo_head} "
        f"schema_matches_models={d.schema_vs_models.get('matches_current_models')}",
        revision=d.db_revisions,
    )


@check("redis")
def c_redis():
    import redis

    from app.core.config import settings

    r = redis.Redis.from_url(settings.redis_url)
    pong = r.ping()
    info = r.info("server")
    record(
        "redis",
        "PASS" if pong else "FAIL",
        f"PING={pong} redis_version={info.get('redis_version')}",
    )


@check("neo4j")
def c_neo4j():
    from app.core import graph

    h = graph.graph_health()
    if not h.get("configured"):
        record(
            "neo4j",
            "BLOCKED",
            "NEO4J_URI not configured: no Neo4j server obtainable in this environment; the "
            "planner runs on the relational engine (RELATIONAL_ROUTES). External validation "
            "required.",
            health=h,
        )
        return
    with graph.graph_session() as s:
        n = s.run("RETURN 1 AS ok").single()["ok"]
    record(
        "neo4j",
        "PASS" if n == 1 and h.get("connected") else "FAIL",
        f"query RETURN 1 -> {n}; health={h}",
    )


@check("object_storage")
def c_storage():
    from app.core import storage
    from app.core.config import settings

    if storage.storage_backend() != "s3":
        record("object_storage", "FAIL", "S3 not configured (local-disk fallback in use)")
        return
    s3 = storage.get_s3_client()
    head = s3.head_bucket(Bucket=settings.s3_bucket)
    server = head.get("ResponseMetadata", {}).get("HTTPHeaders", {}).get("server", "?")
    key = f"clean-start/{uuid.uuid4()}.bin"
    payload = uuid.uuid4().bytes * 64
    storage.put_bytes(key, payload)
    back = storage.get_bytes(key)
    storage.delete_key(key)
    gone = not storage.object_exists(key)
    ok = back == payload and gone
    is_minio = "minio" in server.lower()
    status = "PASS" if ok and is_minio else ("BLOCKED" if ok else "FAIL")
    record(
        "object_storage",
        status,
        f"bucket {settings.s3_bucket!r} exists; {len(payload)} B written, read back identical="
        f"{back == payload}, deleted={gone}; server={server!r}"
        + (
            ""
            if is_minio
            else " -- an S3 STAND-IN, not MinIO: the S3 code path works, "
            "MinIO itself still needs external validation"
        ),
        endpoint=settings.s3_endpoint_url,
    )


@check("clamav")
def c_clamav():
    import socket

    from app.core.config import settings
    from app.services.document_scanner import ClamdScanner

    def cmd(c: bytes) -> str:
        with socket.create_connection((settings.clamd_host, settings.clamd_port), timeout=5) as s:
            s.sendall(c)
            return s.recv(4096).decode().strip("\x00\n ")

    ping, version = cmd(b"zPING\0"), cmd(b"zVERSION\0")
    sc = ClamdScanner(settings.clamd_host, settings.clamd_port, settings.clamd_timeout_seconds)
    clean, bad = sc.scan(CLEAN_PDF), sc.scan(EICAR)
    ok = ping == "PONG" and clean.verdict.value == "CLEAN" and bad.verdict.value == "INFECTED"
    record(
        "clamav",
        "PASS" if ok else "FAIL",
        f"PING={ping}; {version}; clean payload -> {clean.verdict.value}; EICAR -> "
        f"{bad.verdict.value} ({bad.signature})",
    )


@check("worker_and_mail")
def c_mail(api, origin, mail_api, state):
    import redis
    from rq import Worker

    from app.core.config import settings

    workers = Worker.all(connection=redis.Redis.from_url(settings.redis_url))
    if settings.rq_sync:
        record("worker_and_mail", "FAIL", "RQ_SYNC=true: e-mail would bypass the worker")
        return
    if not workers:
        record("worker_and_mail", "FAIL", "no RQ worker registered in Redis")
        return
    email = f"clean-start-{uuid.uuid4().hex[:8]}@example.com"
    password = "Clean-Start-" + uuid.uuid4().hex[:10] + "!"
    c = client(api, origin)
    r = c.post(
        "/auth/register",
        json={
            "full_name": "Clean Start Donor",
            "email": email,
            "password": password,
            "role": "DONOR",
        },
    )
    if r.status_code != 201:
        record("worker_and_mail", "FAIL", f"register: {r.status_code} {r.text[:200]}")
        return
    t0, found = time.time(), None
    while time.time() - t0 < 60 and found is None:
        msgs = httpx.get(mail_api, timeout=10).json()
        msgs = msgs.get("messages", msgs) if isinstance(msgs, dict) else msgs
        for m in msgs:
            to = json.dumps(m.get("to") or m.get("To") or "")
            if email in to:
                found = m
                break
        time.sleep(1)
    if found is None:
        record("worker_and_mail", "FAIL", f"no e-mail to {email} within 60 s")
        return
    body = found.get("html") or found.get("text") or ""
    if not body and found.get("ID"):  # Mailpit: fetch the message body
        body = (
            httpx.get(mail_api.replace("/messages", f"/message/{found['ID']}"), timeout=10)
            .json()
            .get("HTML", "")
        )
    token = re.search(r"verify-email\?token=([A-Za-z0-9_\-\.]+)", body)
    v = c.post("/auth/verify-email", json={"token": token.group(1)}) if token else None
    ok = v is not None and v.status_code == 200
    state.update(donor_email=email, donor_password=password, donor_client=c)
    record(
        "worker_and_mail",
        "PASS" if ok else "FAIL",
        f"{len(workers)} RQ worker(s); verification e-mail for {email} delivered by the worker "
        f"in {time.time() - t0:.1f} s (subject {found.get('subject') or found.get('Subject')!r}); "
        f"token from that e-mail verified the account: {ok}",
    )


@check("document_upload")
def c_upload(api, origin, state):
    c = state.get("donor_client")
    if c is None:
        record("document_upload", "FAIL", "no verified donor (worker_and_mail failed)")
        return
    login_c = login(api, origin, state["donor_email"], state["donor_password"])
    bad = login_c.post(
        "/verifications",
        data={"kind": "DONOR_IDENTITY"},
        files=[("files", ("id.pdf", b"%PDF-1.4\n" + EICAR, "application/pdf"))],
    )
    good = login_c.post(
        "/verifications",
        data={"kind": "DONOR_IDENTITY"},
        files=[("files", ("id.pdf", CLEAN_PDF, "application/pdf"))],
    )
    from app.core import storage
    from app.db.session import SessionLocal
    from app.models.verification import VerificationRequest

    stored, scan = False, None
    if good.status_code == 201:
        with SessionLocal() as db:
            vr = db.get(VerificationRequest, uuid.UUID(good.json()["id"]))
            keys = [d["key"] for d in (vr.documents or []) if isinstance(d, dict) and "key" in d]
            stored = bool(keys) and all(storage.object_exists(k) for k in keys)
            scan = [d.get("scan_status") for d in (vr.documents or []) if isinstance(d, dict)]
    mine = login_c.get("/verifications/me").json()  # the refused upload left no request
    ok = good.status_code == 201 and stored and bad.status_code == 422 and len(mine) == 1
    scan_shown = scan if good.status_code == 201 else "-"
    bad_detail = bad.json().get("detail") if bad.status_code != 201 else ""
    record(
        "document_upload",
        "PASS" if ok else "FAIL",
        f"clean PDF -> {good.status_code}, stored in object storage={stored}, "
        f"scan={scan_shown}; EICAR PDF (sent first) -> {bad.status_code} {bad_detail}; "
        f"verification requests on the account: {len(mine)}",
    )


@check("seed_data")
def c_seed():
    from sqlalchemy import text

    from app.db.session import engine

    q = {
        "users_by_role": "select role, count(*) from users group by role order by role",
        "facilities": (
            "select facility_type, count(*), sum(verified::int) from hospital_profiles group by 1"
        ),
        "transport_routes": "select status, count(*) from transport_routes group by 1",
        "blood_units_by_state": "select state, count(*) from blood_units group by 1",
        "donations": "select count(*) from donations",
    }
    out = {}
    with engine.connect() as conn:
        for k, sql in q.items():
            out[k] = [list(map(str, r)) for r in conn.execute(text(sql)).all()]
    users = sum(int(r[1]) for r in out["users_by_role"])
    ok = users >= 1000 and out["transport_routes"] and out["facilities"]
    record("seed_data", "PASS" if ok else "FAIL", f"{users} users; {out}", counts=out)


@check("frontend")
def c_frontend(frontend, api):
    r = httpx.get(frontend, timeout=10)
    html = r.text
    js = re.findall(r'src="(/assets/[^"]+\.js)"', html)
    targets = False
    for path in js:
        if api in httpx.get(frontend + path, timeout=20).text:
            targets = True
    has_root = 'id="root"' in html
    ok = r.status_code == 200 and has_root and targets
    record(
        "frontend",
        "PASS" if ok else "FAIL",
        f"GET {frontend} -> {r.status_code}; SPA root present={has_root}; "
        f"bundle points at {api}: {targets} "
        "(browser login check: frontend/e2e/clean_start.spec.ts)",
    )


def c_workflow(api, origin):
    steps: list[str] = []
    try:
        _workflow(api, origin, steps)
    except Exception as exc:
        record(
            "critical_workflow",
            "FAIL",
            f"{type(exc).__name__}: {exc} | steps completed: " + " | ".join(steps),
        )


def _workflow(api, origin, steps):
    from sqlalchemy import text

    from app.db.session import engine
    from scripts.seed_demo_data import DEMO_PASSWORD

    dom = "lifelink-demo.example.com"
    admin = login(api, origin, f"demo.admin@{dom}", DEMO_PASSWORD)
    patient = login(api, origin, f"demo.patient@{dom}", DEMO_PASSWORD)
    er = login(api, origin, f"demo.hospital@{dom}", DEMO_PASSWORD)
    with engine.connect() as conn:
        demo = conn.execute(
            text(
                "select h.id, h.city from hospital_profiles h join users u on u.id=h.user_id "
                f"where u.email='demo.hospital@{dom}'"
            )
        ).one()
        local = conn.execute(
            text(
                "select count(*) from blood_units where hospital_id=:h and state='AVAILABLE' "
                "and blood_type='O_NEG' and component='WHOLE_BLOOD'"
            ),
            {"h": demo.id},
        ).scalar()
        # The two nearest verified facilities holding >= 4 usable O_NEG units
        # (any city: the seed may not have two such partners in one city).
        partners = conn.execute(
            text(
                "select h.id, h.name, u.email, h.latitude, h.longitude from hospital_profiles h "
                "join users u on u.id=h.user_id "
                "where h.verified and h.id<>:h and h.latitude is not null "
                "and (select count(*) from blood_units b "
                "where b.hospital_id=h.id and b.state='AVAILABLE' and b.blood_type='O_NEG' "
                "and b.component='WHOLE_BLOOD') >= 4"
            ),
            {"h": demo.id},
        ).all()
        dpos = conn.execute(
            text("select latitude, longitude from hospital_profiles where id=:h"), {"h": demo.id}
        ).one()
    from app.models.enums import haversine_km

    def km(p):
        return haversine_km(dpos.latitude, dpos.longitude, p.latitude, p.longitude)

    partners = sorted(partners, key=km)[:2]
    if len(partners) < 2:
        record("critical_workflow", "FAIL", "seed has fewer than two verified partner facilities")
        return
    units = local + 3  # more than the receiving hospital holds: a delivery is required
    steps.append(f"receiving hospital holds {local} O_NEG; request {units} units")
    routes = []
    for p in partners:
        r = admin.post(
            "/admin/transport-routes",
            json={
                "origin_facility_id": str(demo.id),
                "destination_facility_id": str(p.id),
                # road distance ~1.3 x straight line, 40 km/h
                "distance_km": round(max(3.0, 1.3 * km(p)), 1),
                "transit_minutes": max(8, round(1.3 * km(p) / 40 * 60)),
            },
        )
        assert r.status_code in (200, 201), r.text
        routes.append(r.json()["id"])
    steps.append(
        "admin added routes to " + ", ".join(f"{p.name} ({1.3 * km(p):.1f} km)" for p in partners)
    )
    deadline = (datetime.now(UTC) + timedelta(hours=8)).isoformat()
    em = patient.post(
        "/emergencies",
        json={
            "blood_type": "O_NEG",
            "units_needed": units,
            "target_facility_id": str(demo.id),
            "latitude": 19.076,
            "longitude": 72.8777,
            "deadline": deadline,
            "location_text": "clean-start verification",
        },
    )
    assert em.status_code == 201, em.text
    eid = em.json()["id"]
    steps.append(f"patient SOS {eid} (geocode_source={em.json().get('geocode_source')})")
    plan = er.post(f"/emergencies/{eid}/pathway", json={})
    assert plan.status_code == 200, plan.text
    stored = er.get(f"/emergencies/{eid}/pathways").json()
    active = first((p for p in stored if p["status"] == "ACTIVE"), "ACTIVE pathway after planning")
    backups = [p for p in stored if p["status"] == "BACKUP"]
    delivery = [leg for leg in active["legs"] if leg["steps"]]
    assert delivery, "no inter-facility leg planned"
    steps.append(
        f"plan: {active['feasibility']}, {active['units_planned']}/{units} units, "
        f"{len(active['legs'])} legs, ETA {active['eta_minutes']} min, "
        f"{len(backups)} backup(s), engine {active['pathway_source']}"
    )
    res = er.post(f"/emergencies/{eid}/pathway/reserve")
    assert res.status_code == 200, res.text
    steps.append(f"reserved {res.json()['reserved_units']} units")
    failed_route = delivery[0]["steps"][0]["route_id"]
    d = admin.post(
        f"/admin/transport-routes/{failed_route}/disrupt", json={"reason": "clean-start drill"}
    )
    assert d.status_code == 200, d.text
    out = first(
        (o for o in d.json()["affected_emergencies"] if str(o["emergency_id"]) == eid),
        f"this emergency among the disruption outcomes {d.json()['affected_emergencies']}",
    )
    steps.append(
        f"route {failed_route} disrupted -> {out['kind']}, reachable={out['still_reachable']}"
    )
    assert out["still_reachable"], out
    now_plan = first(
        (p for p in er.get(f"/emergencies/{eid}/pathways").json() if p["status"] == "ACTIVE"),
        "ACTIVE pathway after failover",
    )
    leg = first((leg for leg in now_plan["legs"] if leg["steps"]), "delivery leg after failover")
    assert failed_route not in [s["route_id"] for s in leg["steps"]]
    # Audit C21 (2026-10-31): the reservation is handed to the shipment in one
    # step (never released in between); the shipment is linked to the plan.
    ho = er.post(f"/emergencies/{eid}/pathway/handoff")
    assert ho.status_code == 200, ho.text
    assert ho.json()["reservation_status"] == "HANDED_OFF", ho.json()
    ship_row = first(
        (s for s in ho.json()["shipments"] if s["origin_facility_id"] == leg["source_facility_id"]),
        "shipment created by the hand-off for the delivery leg",
    )
    with engine.connect() as conn:  # the failover source may be any facility on the network
        src_email = conn.execute(
            text(
                "select u.email from hospital_profiles h join users u on u.id=h.user_id "
                "where h.id=:f"
            ),
            {"f": leg["source_facility_id"]},
        ).scalar_one()
    steps.append(f"delivery now from {leg['source_facility_name']} over {leg['hops']} route(s)")
    src = login(api, origin, src_email, DEMO_PASSWORD)
    ship = src.get(f"/shipments/{ship_row['id']}")
    assert ship.status_code == 200, ship.text
    assert ship.json()["emergency_id"] == eid, ship.json()
    sid, n_ship = ship_row["id"], ship_row["units"]
    assert src.post(f"/shipments/{sid}/dispatch", json={}).status_code == 200
    now = datetime.now(UTC)
    tele = src.post(
        f"/shipments/{sid}/telemetry",
        json={
            "readings": [
                {
                    "recorded_at": (now - timedelta(minutes=m)).isoformat(),
                    "temperature_c": 4.2,
                    "sensor_id": "CLEAN-START-LOGGER",
                }
                for m in (20, 10, 0)
            ]
        },
    )
    assert tele.status_code in (200, 201), tele.text
    assert src.post(f"/shipments/{sid}/in-transit", json={}).status_code == 200
    assert er.post(f"/shipments/{sid}/arrive", json={}).status_code == 200

    def o_neg_units() -> int:
        return first(
            (i for i in er.get("/hospitals/me/inventory").json() if i["blood_type"] == "O_NEG"),
            "O_NEG inventory row",
        )["units"]

    before = o_neg_units()
    rec = er.post(f"/shipments/{sid}/receive", json={"items": []})
    assert rec.status_code == 200, rec.text
    after = o_neg_units()
    steps.append(
        f"shipment {ship.json()['shipment_code']}: {n_ship} units, cold chain "
        f"{tele.json()['report']['status']}, received; O_NEG stock {before} -> {after}"
    )
    assert after == before + n_ship
    assert patient.post(f"/emergencies/{eid}/resolve", json={}).status_code == 200
    events = [e["event_type"] for e in patient.get(f"/emergencies/{eid}").json()["events"]]
    verify = admin.post("/admin/audit-log/verify").json()
    steps.append(f"resolved; {len(events)} timeline events; audit chain ok={verify['ok']}")
    assert verify["ok"] is True
    record("critical_workflow", "PASS", " | ".join(steps), emergency_id=eid, shipment_id=sid)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", required=True)
    ap.add_argument("--frontend", required=True)
    ap.add_argument("--origin", default=None)
    ap.add_argument("--mail-api", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    origin = a.origin or a.frontend
    import logging

    logging.disable(logging.WARNING)
    state: dict = {}
    started = datetime.now(UTC).isoformat()
    c_api(a.api)
    c_db()
    c_alembic()
    c_redis()
    c_neo4j()
    c_storage()
    c_clamav()
    c_mail(a.api, origin, a.mail_api, state)
    c_upload(a.api, origin, state)
    c_seed()
    c_frontend(a.frontend, a.api)
    c_workflow(a.api, origin)
    statuses = [r["status"] for r in RESULTS]
    verdict = (
        "FAIL"
        if "FAIL" in statuses
        else "PASS WITH EXTERNAL VALIDATION REQUIRED"
        if "BLOCKED" in statuses
        else "PASS"
    )
    Path(a.out).write_text(
        json.dumps(
            {
                "started": started,
                "finished": datetime.now(UTC).isoformat(),
                "verdict": verdict,
                "checks": RESULTS,
            },
            indent=1,
            default=str,
        )
    )
    print(f"\nVERDICT: {verdict}")
    sys.exit(1 if verdict == "FAIL" else 0)


if __name__ == "__main__":
    main()
