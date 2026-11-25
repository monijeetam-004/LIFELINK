"""QA audit round (2026-10-20) -- races from the audit brief that the suite
did not yet exercise concurrently.

The brief lists seven races. Five were already covered by concurrent tests
(see docs/QA/FEATURE_TEST_MATRIX.md, section "Concurrency"). These tests
add the remaining ones:

* race 2 -- one donor accepts two different open requests at the same
  moment: the double-booking guard must hold under concurrency, not only
  for sequential calls (tests/test_phase31_domain_fixes.py covers those);
* race 5 -- two deadline-monitor workers run a pass at the same moment:
  each emergency is warned, flagged and escalated exactly once;
* race 7 -- a realtime event that reaches Redis twice (a publisher retry
  after a lost acknowledgement) is delivered to a live stream once.

Every test uses real PostgreSQL and Redis, real threads and a barrier, so
the requests really overlap.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import timedelta

import redis
from sqlalchemy import func, select

from app.core.config import settings
from app.core.time import utcnow
from app.db.session import SessionLocal
from app.models.blood_request import DonorResponse
from app.models.emergency import Emergency
from app.models.enums import DonorResponseStatus
from app.models.realtime_event import RealtimeEvent
from app.services import realtime_service
from tests.helpers import auth_client
from tests.test_phase_f_realtime_stream import (  # noqa: F401  (pytest fixture)
    Stream,
    _login,
    _ready,
    _request,
    server,
)


def _run_together(*calls):
    """Start every call at the same instant (barrier) and return the results."""
    barrier = threading.Barrier(len(calls))
    results: list = [None] * len(calls)
    errors: list = []

    def go(i, fn):
        try:
            barrier.wait()
            results[i] = fn()
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=go, args=(i, fn)) for i, fn in enumerate(calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors, errors
    return results


# --------------------------------------------------------------------------- #
# Race 2: two requests compete for the same donor commitment
# --------------------------------------------------------------------------- #
def test_one_donor_accepting_two_requests_at_once_commits_to_exactly_one() -> None:
    patient, _ = auth_client("PATIENT")
    for attempt in range(5):  # repeat: a race that is lost once must never be lost
        donor, _ = auth_client("DONOR")
        donor_id = uuid.UUID(donor.get("/auth/me").json()["id"])
        assert (
            donor.put("/donors/me", json={"blood_type": "O_NEG", "is_available": True}).status_code
            == 200
        )
        r1 = patient.post("/blood-requests", json={"blood_type": "O_NEG"}).json()["id"]
        r2 = patient.post("/blood-requests", json={"blood_type": "O_NEG"}).json()["id"]

        a, b = _run_together(
            lambda r=r1, d=donor: d.post(
                f"/blood-requests/{r}/respond", json={"action": "ACCEPTED"}
            ),
            lambda r=r2, d=donor: d.post(
                f"/blood-requests/{r}/respond", json={"action": "ACCEPTED"}
            ),
        )
        codes = sorted([a.status_code, b.status_code])
        assert codes == [200, 409], (attempt, a.text, b.text)

        # persisted state agrees with the responses: exactly one ACCEPTED row
        with SessionLocal() as db:
            accepted = db.scalar(
                select(func.count())
                .select_from(DonorResponse)
                .where(
                    DonorResponse.donor_user_id == donor_id,
                    DonorResponse.request_id.in_([uuid.UUID(r1), uuid.UUID(r2)]),
                    DonorResponse.status == DonorResponseStatus.ACCEPTED,
                )
            )
        assert accepted == 1


def test_one_donor_accepting_two_emergencies_at_once_commits_to_exactly_one() -> None:
    patient, _ = auth_client("PATIENT")
    donor, _ = auth_client("DONOR")
    donor.put("/donors/me", json={"blood_type": "O_NEG", "is_available": True})
    e1 = patient.post("/emergencies", json={"blood_type": "O_NEG"}).json()["id"]
    e2 = patient.post("/emergencies", json={"blood_type": "O_NEG"}).json()["id"]

    a, b = _run_together(
        lambda: donor.post(f"/emergencies/{e1}/respond", json={"action": "ACCEPTED"}),
        lambda: donor.post(f"/emergencies/{e2}/respond", json={"action": "ACCEPTED"}),
    )
    assert sorted([a.status_code, b.status_code]) == [200, 409], (a.text, b.text)


# --------------------------------------------------------------------------- #
# Race 5: two workers process the same deadline event
# --------------------------------------------------------------------------- #
def test_two_deadline_monitor_passes_at_once_warn_and_escalate_exactly_once() -> None:
    from app.workers import deadline_monitor

    admin, _ = auth_client("ADMIN")
    p, _ = auth_client("PATIENT")
    soon = p.post(
        "/emergencies",
        json={"blood_type": "O_NEG", "deadline": (utcnow() + timedelta(minutes=10)).isoformat()},
    ).json()["id"]
    late = p.post(
        "/emergencies",
        json={"blood_type": "O_NEG", "deadline": (utcnow() + timedelta(hours=5)).isoformat()},
    ).json()["id"]
    with SessionLocal() as db:
        db.get(Emergency, uuid.UUID(late)).deadline = utcnow() - timedelta(minutes=1)
        db.commit()

    def one_pass():
        with SessionLocal() as db:
            return deadline_monitor.run_once(db)

    runs = _run_together(one_pass, one_pass, one_pass)
    warned = [eid for r in runs for eid in r.warned if eid == soon]
    exceeded = [eid for r in runs for eid in r.exceeded if eid == late]
    escalated = [eid for r in runs for eid in r.escalated if eid == late]
    assert (len(warned), len(exceeded), len(escalated)) == (1, 1, 1), runs

    # persisted side effects happened once, not once per worker
    events = [e["event_type"] for e in p.get(f"/emergencies/{late}").json()["events"]]
    assert events.count("deadline_exceeded") == 1
    assert events.count("deadline_escalated") == 1
    warn_notes = [
        n
        for n in p.get("/notifications").json()
        if n["type"] == "emergency.deadline_warning" and n["data"].get("emergency_id") == soon
    ]
    assert len(warn_notes) == 1, warn_notes
    esc_notes = [
        n
        for n in admin.get("/notifications").json()
        if n["type"] == "emergency.deadline_escalated" and n["data"].get("emergency_id") == late
    ]
    assert len(esc_notes) == 1, esc_notes


# --------------------------------------------------------------------------- #
# Race 7: an outbox event delivered more than once
# --------------------------------------------------------------------------- #
def test_event_published_twice_reaches_a_live_stream_once(server) -> None:  # noqa: F811
    patient, _ = _login(server, "PATIENT")
    raw = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    sub = raw.pubsub()
    sub.subscribe(realtime_service.CHANNEL)
    sub.get_message(timeout=1)

    stream = Stream(patient)
    _ready(stream)
    rid = _request(patient)
    first = stream.until(lambda e, d: e == "blood_request.updated" and d["resource_id"] == rid)[-1]
    seq = first[1]["seq"]

    # capture the exact message the publisher sent, then deliver it again
    # (what a publisher retry after a lost Redis acknowledgement would do)
    original = None
    for _ in range(50):
        m = sub.get_message(ignore_subscribe_messages=True, timeout=0.2)
        if m and json.loads(m["data"])["seq"] == seq:
            original = m["data"]
            break
    assert original is not None, "the published message was not observed"
    raw.publish(realtime_service.CHANNEL, original)
    raw.publish(realtime_service.CHANNEL, original)

    later = stream.drain(2.0)
    dupes = [x for x in later if x[1].get("seq") == seq]
    assert dupes == [], f"seq {seq} delivered again: {dupes}"

    # the stream is still live after the duplicates: a new event arrives
    rid2 = _request(patient)
    stream.until(lambda e, d: e == "blood_request.updated" and d["resource_id"] == rid2)
    stream.close()
    sub.close()

    with SessionLocal() as db:
        n = db.scalar(
            select(func.count()).select_from(RealtimeEvent).where(RealtimeEvent.seq == seq)
        )
        assert n == 1  # one stored event; only the delivery was duplicated


# --------------------------------------------------------------------------- #
# Static-analysis finding (bandit B110): a storage failure at start-up was
# swallowed without a trace.
# --------------------------------------------------------------------------- #
def test_storage_failure_at_startup_is_logged_not_swallowed(monkeypatch, caplog) -> None:
    import asyncio
    import logging

    from app.core import storage
    from app.main import app, lifespan

    def boom():
        raise ConnectionError("object store unreachable")

    monkeypatch.setattr(storage, "ensure_bucket", boom)

    async def start_and_stop():
        async with lifespan(app):
            pass

    with caplog.at_level(logging.WARNING, logger="lifelink.storage"):
        asyncio.run(start_and_stop())  # start-up must still complete
    msgs = [r.getMessage() for r in caplog.records if r.name == "lifelink.storage"]
    assert any("object storage not ready at start-up" in m for m in msgs), msgs
    assert all("unreachable" not in m for m in msgs)  # exception type only, no detail


# --------------------------------------------------------------------------- #
# Failure-injection finding: the image's default CMD did not bound graceful
# shutdown (only scripts/run_production.sh did), so an open SSE stream kept a
# SIGTERM'd API alive past the container runtime's stop timeout.
# --------------------------------------------------------------------------- #
def test_every_production_api_entrypoint_bounds_graceful_shutdown() -> None:
    import re
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1]
    cmd = re.search(r"^CMD (\[.*\])$", (backend / "Dockerfile").read_text(), re.M)
    assert cmd, "Dockerfile has no exec-form CMD"
    args = json.loads(cmd.group(1))
    assert "--timeout-graceful-shutdown" in args, args
    assert 0 < int(args[args.index("--timeout-graceful-shutdown") + 1]) < 10  # < docker's 10 s
    script = (backend / "scripts/run_production.sh").read_text()
    assert '--timeout-graceful-shutdown "$GRACEFUL_SHUTDOWN_SECONDS"' in script
    assert 'GRACEFUL_SHUTDOWN_SECONDS="${GRACEFUL_SHUTDOWN_SECONDS:-5}"' in script


# --------------------------------------------------------------------------- #
# Brief E: multiple successive route failures on one emergency, with the
# reservations following the plan at every step (not covered as a sequence).
# --------------------------------------------------------------------------- #
def test_successive_route_failures_promote_backups_then_replan_and_reservations_follow():
    from app.models.blood_unit import BloodUnit
    from app.models.emergency_pathway import EmergencyPathway
    from app.models.enums import BloodUnitState, EmergencyPathwayStatus, TransportRouteStatus
    from app.services import emergency_pathway_service as eps
    from tests import gap_fixtures as gf

    db = gf.session()
    try:
        dest = gf.facility(db, "QA-Dest", 12.0, 77.0)
        srcs, routes = [], []
        for i in range(5):  # primary + 3 backups + 1 source left for the replan
            s = gf.facility(db, f"QA-S{i}", 12.0 + 0.05 * (i + 1), 77.0)
            routes.append(gf.route(db, dest, s, 5 + i, 10 + 5 * i))
            gf.stock(db, s, "O_POS", 2)
            srcs.append(s)
        em = gf.emergency(db, blood_type="O_POS", units=2, target=dest)
        primary, backups, _r = eps.create_plan_round(db, em, requested_by_user_id=None, reason="t")
        assert len(backups) == 3
        eps.reserve_pathway(db, em, primary, actor_user_id=None)

        def state():
            live = db.scalars(
                select(EmergencyPathway).where(
                    EmergencyPathway.emergency_id == em.id,
                    EmergencyPathway.status == EmergencyPathwayStatus.ACTIVE,
                )
            ).all()
            held = db.scalars(
                select(BloodUnit).where(
                    BloodUnit.reserved_for_emergency_id == em.id,
                    BloodUnit.state == BloodUnitState.RESERVED,
                )
            ).all()
            return live, held

        kinds = []
        current = primary
        for step in range(4):  # 3 promotions, then the backups are exhausted
            failed_route = routes[
                srcs.index(next(s for s in srcs if s.id == current.selected_facility_id))
            ]
            failed_route.status = TransportRouteStatus.DISRUPTED
            db.flush()
            [outcome] = eps.handle_route_disrupted(db, failed_route, admin_user_id=None)
            kinds.append(outcome.kind)
            live, held = state()
            assert len(live) == 1, f"step {step}: exactly one ACTIVE plan"
            current = live[0]
            # reservations moved with the plan: 2 units, all at the new source,
            # none left at the source whose route failed
            assert len(held) == 2, f"step {step}: {len(held)} units held"
            assert {u.hospital_id for u in held} == {current.selected_facility_id}
            assert failed_route.id not in eps.route_ids_of(db, current.id)
        assert kinds == ["BACKUP_PROMOTED", "BACKUP_PROMOTED", "BACKUP_PROMOTED", "REPLANNED"]
        assert current.selected_facility_id == srcs[4].id  # the only source still reachable

        # one more failure: nothing is reachable any more -> honest outcome,
        # every reservation released, still exactly one ACTIVE (empty) plan
        routes[4].status = TransportRouteStatus.DISRUPTED
        db.flush()
        [last] = eps.handle_route_disrupted(db, routes[4], admin_user_id=None)
        assert last.kind == "REPLANNED" and last.still_reachable is False
        live, held = state()
        assert held == []
        from app.models.enums import PathwayFeasibility

        assert [(p.feasibility, p.selected_facility_id) for p in live] == [
            (PathwayFeasibility.IMPOSSIBLE, None)
        ]
    finally:
        db.rollback()
        db.close()


# --------------------------------------------------------------------------- #
# Security probe finding: the shipped frontend image (nginx) sent no framing
# protection, and a <meta> CSP cannot carry frame-ancestors.
# --------------------------------------------------------------------------- #
def test_frontend_nginx_sends_framing_and_sniffing_protection_everywhere() -> None:
    import re
    from pathlib import Path

    conf = (Path(__file__).resolve().parents[2] / "frontend" / "nginx.conf").read_text()
    required = [
        'add_header X-Frame-Options "DENY" always;',
        "add_header Content-Security-Policy \"frame-ancestors 'none'\" always;",
        'add_header X-Content-Type-Options "nosniff" always;',
    ]
    server_level = re.split(r"^\s*location ", conf, maxsplit=1, flags=re.M)[0]
    for h in required:
        assert h in server_level, h
    # nginx drops inherited add_header in a location that sets its own, so
    # every such location must repeat the protection headers
    for block in re.findall(r"^\s*location [^{]+\{([^}]*)\}", conf, re.M):
        if "add_header" in block:
            for h in required:
                assert h in block, (h, block)
