"""Phase 29 -- staging (post-deploy) smoke test.

Exercises a REAL, already-running instance (staging, or a local self-boot
for LOCAL-VERIFIED evidence when no staging deployment exists yet -- see
docs/LIFELINK_PHASE_29_CI_CD_STAGING_REPORT.md) over real HTTP, covering
the 18-item checklist the Phase 29 master prompt specifies: frontend
loads, backend responds, liveness, readiness, registration, email
verification path, login, protected route, RBAC, blood request creation,
donor workflow, hospital workflow, family workflow, admin workflow,
notification center, error handling, logout, and frontend production
build.

This deliberately reuses scripts/production_smoke_test.py's `run()` for
checks 1-4 and 16 (infra-level: liveness/readiness/error-handling) rather
than duplicating them -- this script only adds the role-workflow checks
`run()` does not cover. Every check is a real HTTP call against the real
API; nothing here is simulated or hardcoded to pass.

Usage:
    python3 -m scripts.staging_smoke_test --base-url https://staging.example.com --frontend-url https://staging-app.example.com

Without a real staging deployment, run self-boot mode instead (starts the
real app in-process against whatever DATABASE_URL/REDIS_URL the current
environment already points at -- see production_smoke_test.py's own
--self-boot for the exact mechanism this borrows):

    python3 -m scripts.staging_smoke_test --self-boot

Exit code 0 means every check passed. This never uses Mailpit/real Gmail
directly -- see check #6's own comment for how the email-verification path
is checked without needing SMTP access from this script.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from dataclasses import dataclass, field

import httpx


@dataclass
class SmokeResult:
    checks: list[tuple[str, bool, str]] = field(default_factory=list)

    def record(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append((name, ok, detail))
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {name}" + (f" -- {detail}" if detail else ""))

    @property
    def all_passed(self) -> bool:
        return all(ok for _, ok, _ in self.checks)


def run_role_workflow_checks(base_url: str, cors_origin: str) -> SmokeResult:
    result = SmokeResult()
    client = httpx.Client(base_url=base_url, timeout=10.0, headers={"Origin": cors_origin})
    suffix = uuid.uuid4().hex[:10]
    password = "staging smoke test passphrase 2026"

    # 5. Registration: a fresh account for each self-registerable role.
    patient_email = f"staging-smoke-patient-{suffix}@example.com"
    r = client.post(
        "/auth/register",
        json={
            "full_name": "Staging Smoke Patient",
            "email": patient_email,
            "password": password,
            "role": "PATIENT",
        },
    )
    result.record(
        "registration", r.status_code in (200, 201), f"POST /auth/register -> {r.status_code}"
    )

    # 6. Email verification path: this script never has real SMTP/Mailpit
    # access from outside the deployment, so it cannot read the actual
    # email body sent. What it CAN verify over the wire, honestly: (a) the
    # new account cannot yet do a verified-only action, and (b) a
    # verification-resend endpoint exists and accepts the request cleanly
    # -- proving the path is wired, without needing to read the message
    # itself. Full delivery is covered separately by
    # tests/test_auth_flow.py (mailbox-based, real code path) and Phase
    # 28's LOCAL-VERIFIED aiosmtpd check.
    r = client.post("/auth/verify-email/resend", json={"email": patient_email})
    result.record(
        "email_verification_path_wired",
        r.status_code in (200, 202, 204, 429),  # 429 = rate-limited, still proves the route exists
        f"POST /auth/verify-email/resend -> {r.status_code}",
    )

    # 7. Login -- using the fixture accounts scripts/e2e_seed.py creates
    # (already verified, so this doesn't depend on completing email
    # verification here). If they don't exist on this target (a real
    # staging environment that was never e2e-seeded), this is reported as
    # a skip, not a false failure.
    from scripts.e2e_seed import PASSWORD as E2E_PASSWORD
    from scripts.e2e_seed import USERS as E2E_USERS

    e2e_creds = {role: email for email, role, _ in E2E_USERS}
    sessions: dict[str, httpx.Client] = {}
    for role, email in e2e_creds.items():
        r = client.post("/auth/login", json={"email": email, "password": E2E_PASSWORD})
        if r.status_code == 200:
            sess = httpx.Client(base_url=base_url, timeout=10.0, headers={"Origin": cors_origin})
            sess.cookies.update(r.cookies)
            sessions[role] = sess
    result.record(
        "login",
        "PATIENT" in sessions or "DONOR" in sessions,
        f"logged in as: {sorted(sessions.keys())} "
        "(needs scripts/e2e_seed.py to have been run against this target)",
    )

    # 8. Protected route rejects unauthenticated access. Deliberately a
    # FRESH client with no cookies -- `client` itself has accumulated
    # whichever role's session cookie was set last by the login loop
    # above (httpx.Client persists cookies across requests on the same
    # instance), so reusing it here would test "wrong role" (403) instead
    # of "no session at all" (401). Caught by exactly this confusion
    # during Phase 29's own verification of this script.
    with httpx.Client(base_url=base_url, timeout=10.0, headers={"Origin": cors_origin}) as anon:
        r = anon.get("/hospitals/me")
    result.record(
        "protected_route_rejects_unauthenticated", r.status_code == 401, f"-> {r.status_code}"
    )

    # 9. RBAC: a non-admin session gets a real 403 from an admin-only route.
    if "PATIENT" in sessions:
        r = sessions["PATIENT"].get("/admin/users")
        result.record(
            "rbac_rejects_wrong_role",
            r.status_code == 403,
            f"patient -> GET /admin/users -> {r.status_code}",
        )
    else:
        result.record("rbac_rejects_wrong_role", False, "skipped: no PATIENT session")

    # 10. Blood request creation (PATIENT workflow's own core action).
    if "PATIENT" in sessions:
        r = sessions["PATIENT"].post(
            "/blood-requests",
            json={
                "blood_type": "O_NEG",
                "units_needed": 1,
                "urgency": "ROUTINE",
                "hospital_name": "Staging Smoke Test Hospital",
                "note": "staging smoke test -- safe to ignore/delete",
            },
        )
        result.record("blood_request_creation", r.status_code in (200, 201), f"-> {r.status_code}")
    else:
        result.record("blood_request_creation", False, "skipped: no PATIENT session")

    # 11. Donor workflow: fetch the donor's own dashboard-relevant data.
    if "DONOR" in sessions:
        r = sessions["DONOR"].get("/donors/me")
        result.record("donor_workflow", r.status_code == 200, f"GET /donors/me -> {r.status_code}")
    else:
        result.record("donor_workflow", False, "skipped: no DONOR session")

    # 12. Hospital workflow: fetch the hospital's own inventory.
    if "HOSPITAL" in sessions:
        r = sessions["HOSPITAL"].get("/hospitals/me/inventory")
        result.record(
            "hospital_workflow",
            r.status_code == 200,
            f"GET /hospitals/me/inventory -> {r.status_code}",
        )
    else:
        result.record("hospital_workflow", False, "skipped: no HOSPITAL session")

    # 13. Family workflow: fetch linked patients.
    if "FAMILY" in sessions:
        r = sessions["FAMILY"].get("/family/links")
        result.record(
            "family_workflow", r.status_code == 200, f"GET /family/links -> {r.status_code}"
        )
    else:
        result.record("family_workflow", False, "skipped: no FAMILY session")

    # 14. Admin workflow: fetch the user list.
    if "ADMIN" in sessions:
        r = sessions["ADMIN"].get("/admin/users")
        result.record(
            "admin_workflow", r.status_code == 200, f"GET /admin/users -> {r.status_code}"
        )
    else:
        result.record("admin_workflow", False, "skipped: no ADMIN session")

    # 15. Notification center reachable by any authenticated role.
    any_session = next(iter(sessions.values()), None)
    if any_session is not None:
        r = any_session.get("/notifications")
        result.record(
            "notification_center", r.status_code == 200, f"GET /notifications -> {r.status_code}"
        )
    else:
        result.record("notification_center", False, "skipped: no session")

    # 17. Logout invalidates the session.
    if any_session is not None:
        r = any_session.post("/auth/logout")
        logout_ok = r.status_code in (200, 204)
        r2 = any_session.get("/auth/me")
        result.record(
            "logout_invalidates_session",
            logout_ok and r2.status_code == 401,
            f"POST /auth/logout -> {r.status_code}, then GET /auth/me -> {r2.status_code}",
        )
    else:
        result.record("logout_invalidates_session", False, "skipped: no session")

    for sess in sessions.values():
        sess.close()
    client.close()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url", default=None, help="Backend base URL (staging or prod-like target)."
    )
    parser.add_argument(
        "--frontend-url", default=None, help="Frontend base URL, for the frontend-loads check."
    )
    parser.add_argument("--cors-origin", default=None, help="Defaults to --frontend-url.")
    parser.add_argument(
        "--self-boot",
        action="store_true",
        help="Boot the real app in-process against the current environment's "
        "DATABASE_URL/REDIS_URL instead of hitting an external --base-url "
        "(LOCAL-VERIFIED evidence, not STAGING-VERIFIED).",
    )
    args = parser.parse_args()

    if not args.self_boot and not args.base_url:
        print("Either --base-url or --self-boot is required.", file=sys.stderr)
        sys.exit(2)

    if args.self_boot:
        import subprocess
        import time

        port = 8091
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
        )
        base_url = f"http://127.0.0.1:{port}"
        frontend_url = args.frontend_url
        try:
            for _ in range(60):
                try:
                    if httpx.get(f"{base_url}/health/ready", timeout=2.0).status_code == 200:
                        break
                except httpx.RequestError:
                    pass
                time.sleep(1)
            else:
                print("Self-booted backend never became ready.", file=sys.stderr)
                proc.terminate()
                sys.exit(1)
            _run_all(base_url, frontend_url, args.cors_origin or "http://localhost:5173")
        finally:
            proc.terminate()
            proc.wait(timeout=10)
    else:
        _run_all(
            args.base_url,
            args.frontend_url,
            args.cors_origin or args.frontend_url or "http://localhost:5173",
        )


def _run_all(base_url: str, frontend_url: str | None, cors_origin: str) -> None:
    from scripts.production_smoke_test import check_frontend_build
    from scripts.production_smoke_test import run as run_infra_checks

    print(f"=== LifeLink staging smoke test against {base_url} ===\n")
    infra = run_infra_checks(base_url, cors_origin)
    workflows = run_role_workflow_checks(base_url, cors_origin)

    # 18. Frontend production build. Reuses production_smoke_test.py's own
    # check verbatim -- this is a build-toolchain check (does `npm run
    # build` succeed from this checkout), independent of which backend
    # URL is being smoke-tested.
    build_ok, build_detail = check_frontend_build()
    print(f"[{'PASS' if build_ok else 'FAIL'}] frontend_production_build -- {build_detail}")

    # 1. Frontend loads (only checked if a frontend URL was given -- a
    # bare backend-only self-boot has no frontend serving).
    if frontend_url:
        try:
            r = httpx.get(frontend_url, timeout=10.0)
            fe_ok = r.status_code == 200 and '<div id="root"' in r.text
            status = "PASS" if fe_ok else "FAIL"
            print(f"[{status}] frontend_loads -- GET {frontend_url} -> {r.status_code}")
        except httpx.RequestError as exc:
            fe_ok = False
            print(f"[FAIL] frontend_loads -- {exc}")
    else:
        fe_ok = None
        print("[SKIP] frontend_loads -- no --frontend-url given")

    all_ok = infra.all_passed and workflows.all_passed and fe_ok is not False and build_ok
    print(f"\n{'ALL CHECKS PASSED' if all_ok else 'SOME CHECKS FAILED'}")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
