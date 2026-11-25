"""QA failure injection against a running, disposable LifeLink stack.

    python failure_injection.py --api http://127.0.0.1:8400 --mailpit http://127.0.0.1:8025 \
        --worker-cmd "..." --out failure_injection.txt

Prerequisites (see docs/QA/REPRODUCE_TESTS.md, "Failure injection"):

* an API process started with RQ_SYNC=false, real Redis, PostgreSQL, MinIO,
  clamd and Mailpit, on a DISPOSABLE database seeded with
  `python -m scripts.seed_demo_data` (the demo accounts are used to sign in);
* the RQ worker NOT running (this script starts and stops it itself, with
  --worker-cmd, writing its pid to --worker-pid);
* the service start/stop commands below must match the machine. The defaults
  are those of the verification sandbox, where every service is a local
  process. Nothing here deletes data; services are only stopped and started.

Every check asserts the HTTP status AND the persisted outcome (no row
half-written, the queued job delivered, the quarantine status, ...). The
transcript is written to --out; any failed check makes the exit code 1.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import threading
import time
import uuid
from datetime import UTC, datetime

import httpx

ORIGIN = {"Origin": "http://localhost:5173"}
DEMO_PASSWORD = "LifeLink-Demo-2026!"  # documented, non-secret demo seed password
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"

SERVICES = {
    "redis": ("redis-cli shutdown nosave", "redis-server --daemonize yes --save '' --appendonly no"),
    "postgres": ("service postgresql stop", "service postgresql start"),
    "minio": ("kill $(cat /srv/realsvc/minio.pid)", "/srv/realsvc/services_up.sh"),
    "clamd": ("kill $(pidof clamd)", "/srv/realsvc/services_up.sh"),
    "mailpit": ("kill $(cat /srv/realsvc/mailpit.pid)", "/srv/realsvc/services_up.sh"),
}

log_lines: list[str] = []
results: list[bool] = []


def log(line: str) -> None:
    print(line, flush=True)
    log_lines.append(line)


def check(name: str, ok: bool, detail: object = "") -> bool:
    text = detail if isinstance(detail, str) else json.dumps(detail, default=str)
    log(f"[{'PASS' if ok else 'FAIL'}] {name}: {text[:260]}")
    results.append(ok)
    return ok


def sh(cmd: str) -> None:
    subprocess.run(cmd, shell=True, check=False, capture_output=True, timeout=120)


def stop(svc: str) -> None:
    sh(SERVICES[svc][0])
    time.sleep(2)


def start(svc: str) -> None:
    sh(SERVICES[svc][1])
    time.sleep(2)


def wait_ready(api: str, want: int = 200, timeout: float = 60) -> int:
    code = 0
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            code = httpx.get(f"{api}/health/ready", timeout=5).status_code
        except httpx.HTTPError:
            code = 0
        if code == want:
            return code
        time.sleep(1)
    return code


def login(api: str, email: str) -> httpx.Client:
    c = httpx.Client(base_url=api, headers=ORIGIN, timeout=30)
    r = c.post("/auth/login", json={"email": email, "password": DEMO_PASSWORD})
    r.raise_for_status()
    return c


def mailpit_messages(mailpit: str) -> list[dict]:
    return httpx.get(f"{mailpit}/api/v1/messages", timeout=10).json().get("messages", [])


def mail_for(mailpit: str, email: str) -> list[dict]:
    return [m for m in mailpit_messages(mailpit) if any(t["Address"] == email for t in m["To"])]


def queue_len(name: str) -> int:
    import redis

    r = redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"))
    return int(r.llen(f"rq:queue:{name}"))


def failed_count(name: str) -> int:
    import redis

    r = redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"))
    return int(r.zcard(f"rq:failed:{name}"))


def register(api: str) -> tuple[httpx.Response, str]:
    email = f"qa.fi.{uuid.uuid4().hex[:10]}@example.com"
    c = httpx.Client(base_url=api, headers=ORIGIN, timeout=30)
    r = c.post(
        "/auth/register",
        json={
            "email": email,
            "password": "Qa-Failure-Injection-2026!x",
            "full_name": "QA Failure Injection",
            "role": "DONOR",
        },
    )
    return r, email


def fresh_verified_donor(api: str, mailpit: str) -> httpx.Client:
    """Register a new donor, read the real verification e-mail from Mailpit,
    verify, and return a signed-in client (needs the worker running)."""
    r, email = register(api)
    r.raise_for_status()
    token = None
    for _ in range(40):
        for m in mail_for(mailpit, email):
            full = httpx.get(f"{mailpit}/api/v1/message/{m['ID']}", timeout=10).json()
            hit = re.search(r"verify-email\?token=([A-Za-z0-9_\-.~%]+)", full.get("HTML", ""))
            if hit:
                token = hit.group(1)
        if token:
            break
        time.sleep(1)
    if not token:
        raise RuntimeError("verification e-mail did not arrive")
    c = httpx.Client(base_url=api, headers=ORIGIN, timeout=30)
    c.post("/auth/verify-email", json={"token": token}).raise_for_status()
    c.post("/auth/login", json={"email": email, "password": "Qa-Failure-Injection-2026!x"}
           ).raise_for_status()
    return c


class Worker:
    def __init__(self, cmd: str, pidfile: str):
        self.cmd, self.pidfile, self.proc = cmd, pidfile, None

    def start(self) -> None:
        self.proc = subprocess.Popen(
            shlex.split(self.cmd),
            stdout=open(self.pidfile + ".log", "a"),
            stderr=subprocess.STDOUT,
        )
        open(self.pidfile, "w").write(str(self.proc.pid))
        time.sleep(4)

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None


def upload(client: httpx.Client) -> httpx.Response:
    return client.post(
        "/verifications",
        data={"kind": "DONOR_IDENTITY"},
        files=[("files", ("id.pdf", PDF, "application/pdf"))],
    )


# --------------------------------------------------------------------------- #
def redis_down(api: str) -> None:
    log("\n## Redis unavailable")
    donor = login(api, "demo.donor@lifelink-demo.example.com")
    stop("redis")
    r = httpx.get(f"{api}/health/ready", timeout=10)
    check("readiness reports Redis down (503, redis=false)", r.status_code == 503
          and r.json()["required"]["redis"] is False, r.json())
    r = httpx.post(f"{api}/auth/login", headers=ORIGIN, timeout=15,
                   json={"email": "demo.donor@lifelink-demo.example.com", "password": DEMO_PASSWORD})
    check("login is a 503 with a safe message, not a 500", r.status_code == 503
          and "Traceback" not in r.text and "redis" not in r.text.lower(), r.text)
    r = donor.get("/auth/me")
    check("authenticated read is 503 (sessions live in Redis)", r.status_code == 503, r.text)
    start("redis")
    check("readiness 200 again after Redis returns", wait_ready(api) == 200, "")
    r = donor.get("/auth/me")
    check("old session is gone (Redis restarted without persistence) -> 401, not 500",
          r.status_code == 401, r.status_code)
    again = login(api, "demo.donor@lifelink-demo.example.com")
    check("sign-in works after recovery", again.get("/auth/me").status_code == 200, "")


def postgres_down(api: str) -> None:
    log("\n## PostgreSQL unavailable")
    admin = login(api, "demo.admin@lifelink-demo.example.com")
    before = admin.get("/admin/users").status_code
    stop("postgres")
    r = httpx.get(f"{api}/health/ready", timeout=15)
    check("readiness reports DB down (503, db=false)", r.status_code == 503
          and r.json()["required"]["db"] is False, r.json())
    r = admin.get("/admin/users")
    check("read is a 503 without internals", r.status_code == 503
          and not re.search(r"psycopg|sqlalchemy|Traceback|password", r.text, re.I), r.text)
    start("postgres")
    check("readiness 200 again after PostgreSQL returns", wait_ready(api) == 200, "")
    r = admin.get("/admin/users")
    check("read works after recovery (pool re-established)", r.status_code == before == 200,
          r.status_code)


def storage_down(api: str, mailpit: str) -> None:
    log("\n## Object storage (MinIO) unavailable")
    donor = fresh_verified_donor(api, mailpit)
    mine_before = len(donor.get("/verifications/me").json())
    stop("minio")
    r = upload(donor)
    check("upload with storage down is a clean 503", r.status_code == 503, r.text)
    mine_after = len(donor.get("/verifications/me").json())
    check("nothing half-written (no verification row added)", mine_after == mine_before,
          {"before": mine_before, "after": mine_after})
    start("minio")
    r = upload(donor)
    check("upload succeeds after MinIO returns", r.status_code == 201, r.status_code)
    if r.status_code == 201:
        admin = login(api, "demo.admin@lifelink-demo.example.com")
        rid = r.json()["id"]
        d = admin.get(f"/admin/verifications/{rid}/documents/0")
        check("admin downloads the stored document byte-identical",
              d.status_code == 200 and d.content == PDF, d.status_code)
        check("the owner cannot use the admin download route",
              donor.get(f"/admin/verifications/{rid}/documents/0").status_code == 403, "")


def scanner_down(api: str, mailpit: str) -> None:
    log("\n## Malware scanner (clamd) unavailable")
    donor = fresh_verified_donor(api, mailpit)
    admin = login(api, "demo.admin@lifelink-demo.example.com")
    stop("clamd")
    r = upload(donor)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    status = (body.get("documents") or [{}])[0].get("scan_status")
    check("scanner down: accepted into quarantine as SCAN_FAILED, never CLEAN",
          r.status_code == 201 and status == "SCAN_FAILED", {"code": r.status_code, "scan": status})
    if r.status_code != 201:
        start("clamd")
        return
    rid = body["id"]
    d = admin.get(f"/admin/verifications/{rid}/documents/0")
    check("quarantined document is not downloadable (409)", d.status_code == 409, d.text)
    a = admin.post(f"/admin/verifications/{rid}/approve", json={})
    check("a request with an unscanned document cannot be approved (409)", a.status_code == 409,
          a.text)
    start("clamd")
    rs = admin.post(f"/admin/verifications/{rid}/documents/0/rescan")
    after = (rs.json().get("documents") or [{}])[0].get("scan_status") if rs.status_code == 200 else None
    check("rescan after clamd returns releases it as CLEAN", after == "CLEAN",
          {"code": rs.status_code, "scan": after})


def worker_and_smtp(api: str, mailpit: str, worker: Worker) -> None:
    log("\n## Email worker unavailable, then SMTP (Mailpit) unavailable")
    worker.stop()
    q0 = queue_len("emails")
    r, email = register(api)
    q1 = queue_len("emails")
    check("worker down: registration still 201; e-mail job waits in the queue",
          r.status_code == 201 and q1 == q0 + 1, {"register": r.status_code, "queue": [q0, q1]})
    time.sleep(3)
    check("no e-mail was sent while the worker was down", mail_for(mailpit, email) == [], "")
    worker.start()
    got = []
    for _ in range(30):
        got = mail_for(mailpit, email)
        if got:
            break
        time.sleep(1)
    check("worker restarted: the queued e-mail is delivered once",
          len(got) == 1 and got[0]["Subject"] == "Verify your LifeLink email",
          [m["Subject"] for m in got])

    stop("mailpit")
    f0 = failed_count("emails")
    r, email2 = register(api)
    f1 = f0
    for _ in range(30):
        f1 = failed_count("emails")
        if f1 > f0:
            break
        time.sleep(1)
    check("SMTP down: registration 201; the job fails into the failed registry (no false success)",
          r.status_code == 201 and f1 == f0 + 1, {"register": r.status_code, "failed": [f0, f1]})
    start("mailpit")
    c = httpx.Client(base_url=api, headers=ORIGIN, timeout=30)
    rr = c.post("/auth/verify-email/resend", json={"email": email2})
    got = []
    for _ in range(30):
        got = mail_for(mailpit, email2)
        if got:
            break
        time.sleep(1)
    check("SMTP back: resend delivers a fresh verification link",
          rr.status_code in (200, 202) and len(got) == 1, {"resend": rr.status_code, "mails": len(got)})


def graceful_shutdown(api_pid: int, api: str) -> None:
    log("\n## Graceful shutdown with an open SSE stream")
    donor = login(api, "demo.donor@lifelink-demo.example.com")
    opened = threading.Event()

    def hold():
        try:
            with donor.stream("GET", "/events/stream", timeout=httpx.Timeout(10, read=60)) as r:
                opened.set()
                for _ in r.iter_lines():
                    pass
        except httpx.HTTPError:
            pass

    t = threading.Thread(target=hold, daemon=True)
    t.start()
    opened.wait(10)
    check("SSE stream open before shutdown", opened.is_set(), "")
    t0 = time.time()
    os.kill(api_pid, signal.SIGTERM)
    for _ in range(300):
        try:
            os.kill(api_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    took = time.time() - t0
    alive = True
    try:
        os.kill(api_pid, 0)
    except ProcessLookupError:
        alive = False
    check("API (production entrypoint) exits on SIGTERM within the bounded grace period despite the open stream (< 10 s, Docker's default stop timeout)",
          not alive and took < 10, f"{took:.2f} s")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8400")
    ap.add_argument("--api-pid", type=int, required=True)
    ap.add_argument("--mailpit", default="http://127.0.0.1:8025")
    ap.add_argument("--worker-cmd", required=True)
    ap.add_argument("--worker-pid", default="/tmp/qa_fi_worker.pid")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    log(f"# QA failure injection -- {datetime.now(UTC).isoformat(timespec='seconds')}")
    log(f"# API {a.api}; real PostgreSQL, Redis, MinIO, clamd, Mailpit; RQ worker managed here")
    worker = Worker(a.worker_cmd, a.worker_pid)
    worker.start()
    check("stack ready before injection", wait_ready(a.api) == 200, "")
    for fn, args in (
        (redis_down, (a.api,)),
        (postgres_down, (a.api,)),
        (storage_down, (a.api, a.mailpit)),
        (scanner_down, (a.api, a.mailpit)),
    ):
        try:
            fn(*args)
        except Exception as exc:  # noqa: BLE001 - recorded as a failure, never hidden
            check(f"{fn.__name__} completed without an unexpected error", False, repr(exc))
    try:
        worker_and_smtp(a.api, a.mailpit, worker)
    except Exception as exc:  # noqa: BLE001
        check("worker_and_smtp completed without an unexpected error", False, repr(exc))
    worker.stop()
    graceful_shutdown(a.api_pid, a.api)
    passed = sum(results)
    log(f"\nSUMMARY: {passed}/{len(results)} checks passed")
    with open(a.out, "w") as fh:
        fh.write("\n".join(log_lines) + "\n")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
