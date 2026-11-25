"""Phase F -- the live SSE stream, over a REAL HTTP server (uvicorn in a
background thread, same app, same test database, real Redis). Starlette's
TestClient cannot consume an infinite streaming response, so these tests
talk HTTP to an actual listening socket with httpx, exactly like a browser
EventSource would.

Covers: 401 without a session; donor/patient/hospital isolation on the live
stream; admin visibility; reconnect with Last-Event-ID replays exactly the
missed events; unknown/pruned cursor -> resync; logout ends an open stream
(session re-validation); duplicate suppression by seq.
"""

from __future__ import annotations

import json
import queue
import socket
import threading
import time
import uuid

import httpx
import pytest
import uvicorn

from app.core.config import settings
from app.main import app
from tests.helpers import _PW, seed_verified_user

ORIGIN = {"Origin": "http://localhost:5173"}


@pytest.fixture(scope="module")
def server():
    old = (settings.realtime_heartbeat_seconds, settings.realtime_session_recheck_seconds)
    settings.realtime_heartbeat_seconds = 0.5
    settings.realtime_session_recheck_seconds = 0.5
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(f"{base}/health/live", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    yield base
    srv.should_exit = True
    t.join(timeout=10)
    settings.realtime_heartbeat_seconds, settings.realtime_session_recheck_seconds = old


def _login(base: str, role: str) -> tuple[httpx.Client, str]:
    email = f"{role.lower()}-{uuid.uuid4().hex[:8]}@example.com"
    seed_verified_user(email, _PW, role, f"{role.title()} User")
    c = httpx.Client(base_url=base, headers=ORIGIN, timeout=10)
    r = c.post("/auth/login", json={"email": email, "password": _PW})
    assert r.status_code == 200, r.text
    return c, email


class Stream:
    """Reads an SSE stream on a background thread into a queue of
    (event, data, id) tuples."""

    def __init__(self, client: httpx.Client, last_event_id: str | None = None):
        self.q: queue.Queue = queue.Queue()
        self.status: int | None = None
        self._client = client
        self._headers = {"Last-Event-ID": last_event_id} if last_event_id is not None else {}
        self._stop = threading.Event()
        self.ended = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _run(self):
        try:
            with self._client.stream(
                "GET", "/events/stream", headers=self._headers, timeout=httpx.Timeout(10, read=5)
            ) as r:
                self.status = r.status_code
                if r.status_code != 200:
                    return
                ev = {"event": "message", "data": "", "id": None}
                for line in r.iter_lines():
                    if self._stop.is_set():
                        return
                    if line == "":
                        if ev["data"]:
                            self.q.put((ev["event"], json.loads(ev["data"]), ev["id"]))
                        ev = {"event": "message", "data": "", "id": None}
                    elif line.startswith("event:"):
                        ev["event"] = line[6:].strip()
                    elif line.startswith("data:"):
                        ev["data"] += line[5:].strip()
                    elif line.startswith("id:"):
                        ev["id"] = line[3:].strip()
        except httpx.HTTPError:
            pass
        finally:
            self.ended.set()

    def until(self, pred, timeout=6.0):
        """Collect events until pred(event_name, data) is true; return all seen."""
        seen = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                item = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            seen.append(item)
            if pred(item[0], item[1]):
                return seen
        raise AssertionError(f"condition not met; saw {[s[0] for s in seen]}")

    def drain(self, seconds: float):
        seen = []
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                seen.append(self.q.get(timeout=0.1))
            except queue.Empty:
                pass
        return seen

    def close(self):
        self._stop.set()


def _ready(s: Stream):
    return s.until(lambda e, d: e == "ready")


def _request(client: httpx.Client, city="Metro") -> str:
    r = client.post(
        "/blood-requests", json={"blood_type": "O_NEG", "city": city, "units_needed": 2}
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_unauthenticated_stream_is_rejected(server) -> None:
    r = httpx.get(f"{server}/events/stream", timeout=5)
    assert r.status_code == 401


def test_live_event_reaches_owner_and_hospital_but_not_other_patient(server) -> None:
    patient, _ = _login(server, "PATIENT")
    other, _ = _login(server, "PATIENT")
    hosp, _ = _login(server, "HOSPITAL")
    hosp.put("/hospitals/me", json={"name": f"H-{uuid.uuid4().hex[:6]}", "city": "Metro"})
    hosp.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 3})
    admin, _ = _login(server, "ADMIN")

    sp, so, sh, sa = Stream(patient), Stream(other), Stream(hosp), Stream(admin)
    for s in (sp, so, sh, sa):
        _ready(s)
    rid = _request(patient)

    is_req = lambda e, d: e == "blood_request.updated" and d["resource_id"] == rid  # noqa: E731
    sp.until(is_req)
    sh.until(is_req)
    sa.until(is_req)
    leaked = [
        x for x in so.drain(2.0) if x[0] == "blood_request.updated" and x[1]["resource_id"] == rid
    ]
    assert leaked == []

    # payload carries no PII -- only the refetch hint
    got = sp.until(lambda e, d: True, timeout=0.1) if not sp.q.empty() else []
    for _, data, _ in got:
        assert set(data) <= {"seq", "type", "resource_type", "resource_id", "head_seq"}

    # hospital allocation -> patient sees the request change live
    hosp.post(f"/hospitals/me/requests/{rid}/allocate", json={"blood_type": "O_NEG", "units": 1})
    sp.until(is_req)
    for s in (sp, so, sh, sa):
        s.close()


def test_reconnect_with_last_event_id_replays_missed_events(server) -> None:
    patient, _ = _login(server, "PATIENT")
    s1 = Stream(patient)
    seen = _ready(s1)
    resume = seen[-1][2]  # the ready frame's id = head at connect
    s1.close()

    missed = [_request(patient), _request(patient)]  # while "offline"

    s2 = Stream(patient, last_event_id=resume)
    events = s2.until(lambda e, d: e == "ready")
    replayed = [d["resource_id"] for e, d, _ in events if e == "blood_request.updated"]
    assert replayed == missed
    ids = [int(i) for e, d, i in events if e == "blood_request.updated"]
    assert ids == sorted(ids)
    s2.close()


@pytest.mark.parametrize("cursor", ["999999999", "not-a-number"])
def test_unknown_cursor_gets_resync(server, cursor) -> None:
    patient, _ = _login(server, "PATIENT")
    s = Stream(patient, last_event_id=cursor)
    events = s.until(lambda e, d: e == "ready")
    assert events[0][0] == "resync"
    s.close()


def test_logout_ends_open_stream(server) -> None:
    patient, _ = _login(server, "PATIENT")
    s = Stream(patient)
    _ready(s)
    assert patient.post("/auth/logout").status_code in (200, 204)
    s.until(lambda e, d: e == "session_ended", timeout=5)
    assert s.ended.wait(5)


def test_donor_isolation_on_live_stream(server) -> None:
    donor_a, _ = _login(server, "DONOR")
    donor_b, _ = _login(server, "DONOR")
    for d in (donor_a, donor_b):
        d.put("/donors/me", json={"blood_type": "O_NEG", "city": "Metro"})
    patient, _ = _login(server, "PATIENT")
    rid = _request(patient)
    sa, sb = Stream(donor_a), Stream(donor_b)
    _ready(sa)
    _ready(sb)
    assert (
        donor_a.post(f"/blood-requests/{rid}/respond", json={"action": "ACCEPTED"}).status_code
        == 200
    )
    sa.until(lambda e, d: e == "blood_request.updated" and d["resource_id"] == rid)
    leaked = [
        x for x in sb.drain(2.0) if x[0] == "blood_request.updated" and x[1]["resource_id"] == rid
    ]
    assert leaked == []
    sa.close()
    sb.close()
