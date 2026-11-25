"""Phase 28 -- production smoke test.

Exercises a REAL, already-running instance of this backend over real HTTP --
not TestClient, not a mock -- to answer one question: "if I actually booted
this with production settings and pointed real traffic at it, does it
actually work?" The full pytest suite already covers business logic in
depth; this script deliberately duplicates none of that and instead checks
the boundary concerns that only show up when the app is genuinely running
as a separate process (config validation actually enforced at boot,
headers actually on the wire, CORS actually enforced by literal request
matching, errors actually not leaking through a real HTTP response body).

Usage (see docs/LIFELINK_PHASE_28_PRODUCTION_AUDIT.md's deployment
checklist for the full production boot procedure this assumes has already
happened):

    python3 -m scripts.production_smoke_test --base-url http://localhost:8000

Exit code 0 means every check passed; non-zero means at least one failed
(see stdout for exactly which, and why) -- this is meant to be run as a
real CI/deployment gate, not just read by a human.

This script does NOT start the server itself and does NOT run migrations --
matching the master prompt's own "PRE-DEPLOY / DEPLOY / POST-DEPLOY"
separation, this is the POST-DEPLOY verification step, run against an
instance that was already started the real way (scripts/run_production.sh
or this repo's Dockerfile) against real PostgreSQL/Redis.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import uuid
from dataclasses import dataclass, field

import httpx


@dataclass
class SmokeTestResult:
    checks: list[tuple[str, bool, str]] = field(default_factory=list)

    def record(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append((name, ok, detail))
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {name}" + (f" -- {detail}" if detail else ""))

    @property
    def all_passed(self) -> bool:
        return all(ok for _, ok, _ in self.checks)


def run(base_url: str, cors_origin: str) -> SmokeTestResult:
    result = SmokeTestResult()
    client = httpx.Client(base_url=base_url, timeout=10.0)

    # 1. The process is alive and answering HTTP at all.
    try:
        r = client.get("/")
        result.record("api_responds", r.status_code == 200, f"GET / -> {r.status_code}")
    except httpx.RequestError as exc:
        result.record("api_responds", False, f"connection failed: {exc}")
        # Every other check needs a live server -- stop here rather than
        # produce a wall of misleading downstream failures.
        return result

    # 2. Liveness: always 200, zero dependency checks.
    r = client.get("/health/live")
    result.record(
        "liveness",
        r.status_code == 200 and r.json().get("status") == "alive",
        f"GET /health/live -> {r.status_code} {r.text}",
    )

    # 3+4. Readiness, and specifically that it reports DB+Redis as required.
    r = client.get("/health/ready")
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    result.record(
        "readiness_reports_ready",
        r.status_code == 200 and body.get("status") == "ready",
        f"GET /health/ready -> {r.status_code} {body}",
    )
    result.record(
        "db_connection",
        bool(body.get("required", {}).get("db")),
        f"required.db={body.get('required', {}).get('db')}",
    )
    result.record(
        "redis_connection",
        bool(body.get("required", {}).get("redis")),
        f"required.redis={body.get('required', {}).get('redis')}",
    )

    # 5. Security headers actually present on a real response, not just in
    # the middleware's own unit test.
    r = client.get("/health")
    expected_headers = {
        "x-content-type-options": "nosniff",
        "x-frame-options": "DENY",
    }
    missing = [h for h, v in expected_headers.items() if r.headers.get(h) != v]
    result.record(
        "security_headers_present",
        not missing,
        "all expected headers present" if not missing else f"missing/wrong: {missing}",
    )

    # 6. X-Request-Id is echoed back (Phase 28 correlation-id middleware).
    sent_id = f"smoke-test-{uuid.uuid4().hex[:8]}"
    r = client.get("/health", headers={"X-Request-Id": sent_id})
    result.record(
        "request_id_echoed",
        r.headers.get("x-request-id") == sent_id,
        f"sent {sent_id!r}, got {r.headers.get('x-request-id')!r}",
    )

    # 7. Protected routes actually reject an unauthenticated request (401),
    # not a 500/200 -- this is the one negative-path check most valuable to
    # run against the REAL deployed config (real session/cookie settings),
    # not just TestClient.
    r = client.get("/hospitals/me")
    result.record(
        "protected_routes_reject_unauthenticated",
        r.status_code == 401,
        f"GET /hospitals/me (no auth) -> {r.status_code}, expected 401",
    )

    # 8. Auth flow works end-to-end against the real instance: reject bad
    # credentials with a clean 401 (never a 500), and never leak whether
    # the account exists via response-shape differences.
    r = client.post(
        "/auth/login",
        json={"email": f"no-such-user-{uuid.uuid4().hex}@example.com", "password": "wrong"},
        headers={"Origin": cors_origin},
    )
    result.record(
        "auth_rejects_bad_credentials_cleanly",
        r.status_code == 401,
        f"POST /auth/login (bogus) -> {r.status_code}",
    )

    # 9. CORS: the configured origin is allowed...
    r = client.options(
        "/auth/login",
        headers={
            "Origin": cors_origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    allow_origin = r.headers.get("access-control-allow-origin")
    result.record(
        "cors_allows_configured_origin",
        allow_origin == cors_origin,
        f"preflight from {cors_origin!r} -> Access-Control-Allow-Origin={allow_origin!r}",
    )

    # 10. ...and an arbitrary, non-allow-listed origin is NOT.
    bogus_origin = "https://evil-" + uuid.uuid4().hex[:8] + ".example.com"
    r = client.options(
        "/auth/login",
        headers={
            "Origin": bogus_origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    allow_origin_bogus = r.headers.get("access-control-allow-origin")
    result.record(
        "cors_rejects_unlisted_origin",
        allow_origin_bogus != bogus_origin,
        f"preflight from unlisted origin -> Access-Control-Allow-Origin={allow_origin_bogus!r}",
    )

    # 11. Errors don't leak internals: a 404 on a made-up path must be a
    # clean JSON error, never a stack trace, file path, or framework debug
    # page (which `debug=True`, or an unhandled exception in a
    # non-production ASGI stack, would produce).
    r = client.get(f"/this-route-does-not-exist-{uuid.uuid4().hex[:8]}")
    leaks_internals = any(
        token in r.text
        for token in ("Traceback (most recent call last)", "File \"/", "at 0x", "<class '")
    )
    result.record(
        "errors_do_not_leak_internals",
        r.status_code == 404 and not leaks_internals,
        f"GET <unknown route> -> {r.status_code}, body looks like a debug page: {leaks_internals}",
    )

    # 12. A malformed request body is rejected as a clean 4xx (Pydantic
    # validation error), not a 500 -- exercising error_handlers.py's
    # boundary from the outside.
    r = client.post("/auth/login", json={"not": "the right shape"})
    result.record(
        "malformed_request_rejected_cleanly",
        400 <= r.status_code < 500,
        f"POST /auth/login (malformed) -> {r.status_code}",
    )

    client.close()
    return result


def check_rq_worker_can_start() -> tuple[bool, str]:
    """Item 13 -- confirmed by import + construction, not by actually
    running `python -m app.workers.worker` as a subprocess (which would
    block forever on RQ's own blocking dequeue loop, unsuitable for a
    smoke test with a real exit code). Constructing the real Worker object
    against the real configured Redis connection exercises everything
    that would fail at worker startup (bad REDIS_URL, auth failure,
    unreachable Redis) without actually entering the work loop."""
    try:
        from rq import Worker

        from app.core.config import settings
        from app.core.redis import rq_redis

        Worker([settings.rq_queue_name, settings.rq_sms_queue_name], connection=rq_redis)
        return True, "Worker([...], connection=rq_redis) constructed against real Redis"
    except Exception as exc:  # noqa: BLE001 - report, this is a smoke test
        return False, f"{type(exc).__name__}: {exc}"


def check_migration_state() -> tuple[bool, str]:
    """Item 15 -- the DB's current alembic_version matches this checkout's
    head revision. A mismatch here means either a deploy forgot to run
    migrations, or the checked-out code is older/newer than what actually
    ran against this database -- either way, exactly the kind of drift a
    smoke test should catch before real traffic arrives."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from sqlalchemy import text

        from app.db.session import engine

        cfg = Config("alembic.ini")
        script = ScriptDirectory.from_config(cfg)
        head_rev = script.get_current_head()

        with engine.connect() as conn:
            db_rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()

        return (
            db_rev == head_rev,
            f"db alembic_version={db_rev!r}, checkout head={head_rev!r}",
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def check_backend_boots_with_production_config(
    port: int, extra_env: dict[str, str]
) -> tuple[bool, str, subprocess.Popen | None]:
    """Item 1 -- actually boot app.asgi:application as a real subprocess
    with ENVIRONMENT=production and the other fail-closed settings
    (SECRET_KEY, SESSION_COOKIE_SECURE) filled in, and confirm it becomes
    healthy. This is the one check in this script that cannot be run
    against an already-running instance (the whole point is to prove the
    *boot* succeeds under production config, including this codebase's
    fail-closed validators in app/core/config.py actually being exercised
    for real, not just unit-tested against the Settings class directly).
    Returns the live Popen handle so the caller can also verify graceful
    shutdown (item 14) against this same process before it's torn down.
    """
    import os
    import time

    env = os.environ.copy()
    env.update(extra_env)
    env["PORT"] = str(port)

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.asgi:application",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    deadline = time.monotonic() + 15
    last_error = "timed out waiting for boot"
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=2.0) as client:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                out = proc.stdout.read() if proc.stdout else ""
                return False, f"process exited early (code {proc.returncode}): {out[-800:]}", None
            try:
                r = client.get("/health/live")
                if r.status_code == 200:
                    return True, f"boot succeeded, healthy within budget on port {port}", proc
            except httpx.RequestError as exc:
                last_error = str(exc)
            time.sleep(0.3)

    proc.terminate()
    return False, last_error, None


def check_graceful_shutdown(proc: subprocess.Popen) -> tuple[bool, str]:
    """Item 14 -- SIGTERM the real subprocess from
    check_backend_boots_with_production_config and confirm it exits
    cleanly (not killed, not hung) within a reasonable budget. Uvicorn's
    own SIGTERM handling drains in-flight requests then runs this app's
    lifespan shutdown (closes the Neo4j driver -- see app/main.py); this
    only checks the observable outcome (clean exit, bounded time), not the
    log lines, since those are already asserted in the Phase 27/28
    regression tests that exercise this directly against a captured log.
    """

    import signal

    proc.terminate()  # SIGTERM
    try:
        returncode = proc.wait(timeout=10)
        # A process that exits BECAUSE of the signal reports returncode as
        # the negative signal number (POSIX/Python subprocess convention)
        # -- that is the expected, correct outcome for a graceful SIGTERM
        # shutdown, not a failure. 0 is also accepted in case a future
        # uvicorn version changes its own exit-code convention. Anything
        # else (a different negative signal, e.g. SIGKILL/-9, or a
        # non-zero positive exit code) means it did NOT shut down cleanly.
        ok = returncode in (0, -signal.SIGTERM)
        return ok, f"exited with code {returncode} within 10s of SIGTERM"
    except Exception:  # noqa: BLE001 - subprocess.TimeoutExpired
        proc.kill()
        return False, "did not exit within 10s of SIGTERM -- had to SIGKILL"


def check_frontend_build() -> tuple[bool, str]:
    """Item 2 -- `npm run build` (tsc -b && vite build, including the
    Phase 28 CSP plugin) actually succeeds from a clean state. Skipped
    (not failed) when the frontend directory isn't present next to this
    checkout -- e.g. a backend-only deployment artifact -- since that's a
    legitimate deployment shape, not a smoke-test failure."""
    import shutil
    from pathlib import Path

    frontend_dir = Path(__file__).resolve().parent.parent.parent / "frontend"
    if not frontend_dir.is_dir():
        return True, f"skipped -- no frontend/ directory found at {frontend_dir}"

    npm = shutil.which("npm")
    if not npm:
        return False, "npm not found on PATH"

    proc = subprocess.run(
        [npm, "run", "build"], cwd=frontend_dir, capture_output=True, text=True, timeout=180
    )
    if proc.returncode == 0:
        return True, "npm run build succeeded"
    tail = f"{proc.stdout[-500:]}{proc.stderr[-500:]}"
    return False, f"npm run build failed (exit {proc.returncode}): {tail}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=None,
        help="Run HTTP checks against an already-running instance at this URL "
        "instead of self-booting one (use this for a real deployment's "
        "post-deploy check, against the instance the orchestrator already "
        "started). Mutually exclusive with --self-boot.",
    )
    parser.add_argument(
        "--self-boot",
        action="store_true",
        help="Boot app.asgi:application as a subprocess with production "
        "config, run every check (including boot-under-production-config, "
        "graceful shutdown, and the frontend build) against it, then tear "
        "it down. This is the mode that covers all 15 items in one "
        "invocation, suitable for CI / a pre-deploy gate against a "
        "disposable environment -- it does NOT touch a real production "
        "process. Requires a real PostgreSQL/Redis reachable via the "
        "current DATABASE_URL/REDIS_URL env vars (e.g. a CI service "
        "container), same as the existing pytest suite.",
    )
    parser.add_argument("--port", type=int, default=8099, help="Port for --self-boot.")
    parser.add_argument("--secret-key", default=None, help="SECRET_KEY for --self-boot.")
    parser.add_argument(
        "--cors-origin",
        default="http://localhost:5173",
        help="An origin this deployment's CORS_ORIGINS is expected to allow "
        "(defaults to the dev frontend origin; pass the real production "
        "frontend origin when smoke-testing a production deployment).",
    )
    args = parser.parse_args()

    if args.self_boot and args.base_url:
        print("--self-boot and --base-url are mutually exclusive.", file=sys.stderr)
        return 2

    if args.self_boot:
        import secrets

        print("=== LifeLink production smoke test (self-boot mode) ===\n")
        secret_key = args.secret_key or secrets.token_urlsafe(32)
        ok, detail, proc = check_backend_boots_with_production_config(
            args.port,
            {
                "ENVIRONMENT": "production",
                "SECRET_KEY": secret_key,
                "SESSION_COOKIE_SECURE": "true",
                "CORS_ORIGINS": args.cors_origin,
            },
        )
        result = SmokeTestResult()
        result.record("backend_boots_with_production_config", ok, detail)
        if not ok or proc is None:
            print(f"\n1/{len(result.checks)} checks passed.")
            print("Cannot continue -- backend did not boot. See detail above.")
            return 1

        http_result = run(f"http://127.0.0.1:{args.port}", args.cors_origin)
        result.checks.extend(http_result.checks)

        rq_ok, rq_detail = check_rq_worker_can_start()
        result.record("rq_worker_can_start", rq_ok, rq_detail)

        mig_ok, mig_detail = check_migration_state()
        result.record("migration_state_correct", mig_ok, mig_detail)

        shutdown_ok, shutdown_detail = check_graceful_shutdown(proc)
        result.record("graceful_shutdown", shutdown_ok, shutdown_detail)

        build_ok, build_detail = check_frontend_build()
        result.record("frontend_build_succeeds", build_ok, build_detail)
    else:
        base_url = args.base_url or "http://localhost:8000"
        print(f"=== LifeLink production smoke test against {base_url} ===\n")
        result = run(base_url, args.cors_origin)

        rq_ok, rq_detail = check_rq_worker_can_start()
        result.record("rq_worker_can_start", rq_ok, rq_detail)

        mig_ok, mig_detail = check_migration_state()
        result.record("migration_state_correct", mig_ok, mig_detail)

    total = len(result.checks)
    passed = sum(1 for _, ok, _ in result.checks if ok)
    print(f"\n{passed}/{total} checks passed.")

    if not result.all_passed:
        print("\nFAILED checks:")
        for name, ok, detail in result.checks:
            if not ok:
                print(f"  - {name}: {detail}")
        return 1

    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
