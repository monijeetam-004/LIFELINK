"""Black-box security probe of a running, disposable LifeLink stack.

    python security_probe.py --api http://localhost:8000 --front http://localhost:4173 --out FILE

Complements the backend security suites (tests/test_phase23_security_hardening.py,
tests/test_phase_h_security.py, tests/test_qa_attack_paths.py, which run
in-process): this script talks to the real uvicorn process over HTTP, as an
attacker would, using the demo seed accounts. It changes no data except a
few rejected requests. Every check records the observed status and a short,
secret-free excerpt; any failed check makes the exit code 1.
"""

from __future__ import annotations

import argparse
import json
import re
import uuid
from datetime import UTC, datetime

import httpx

ORIGIN = {"Origin": "http://localhost:5173"}
PW = "LifeLink-Demo-2026!"  # documented, non-secret demo seed password
lines: list[str] = []
ok_all: list[bool] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    text = detail if isinstance(detail, str) else json.dumps(detail, default=str)
    line = f"[{'PASS' if ok else 'FAIL'}] {name}: {text[:240]}"
    print(line, flush=True)
    lines.append(line)
    ok_all.append(ok)


def login(api: str, role: str) -> httpx.Client:
    c = httpx.Client(base_url=api, headers=ORIGIN, timeout=30)
    r = c.post("/auth/login", json={"email": f"demo.{role}@lifelink-demo.example.com", "password": PW})
    r.raise_for_status()
    return c


LEAK = re.compile(r"Traceback|sqlalchemy|psycopg|File \"/|SECRET|password_hash", re.I)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--front", default="http://localhost:4173")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    api = a.api
    lines.append(f"# QA security probe -- {datetime.now(UTC).isoformat(timespec='seconds')} -- {api}")

    # 1. Session cookie flags
    c = httpx.Client(base_url=api, headers=ORIGIN, timeout=30)
    r = c.post("/auth/login", json={"email": "demo.donor@lifelink-demo.example.com", "password": PW})
    sc = r.headers.get("set-cookie", "")
    check("session cookie is HttpOnly and SameSite (Secure is off only in development)",
          "httponly" in sc.lower() and "samesite" in sc.lower(),
          re.sub(r"lifelink_session=[^;]*", "lifelink_session=<redacted>", sc))
    donor = c

    # 2. Security headers on API and frontend
    h = httpx.get(f"{api}/health/live").headers
    want = ["x-content-type-options", "x-frame-options", "referrer-policy"]
    check("API sends nosniff, frame and referrer headers", all(k in h for k in want),
          {k: h.get(k) for k in want + ["content-security-policy"]})
    fh = httpx.get(a.front).headers
    check("frontend (vite preview) headers recorded", True,
          {k: fh.get(k) for k in ["content-security-policy", "x-frame-options"]})

    # 3. CORS: a foreign origin gets no CORS grant
    pre = httpx.options(f"{api}/auth/login", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    check("CORS preflight from a foreign origin is not granted",
          pre.headers.get("access-control-allow-origin") not in ("*", "https://evil.example"),
          {"status": pre.status_code, "acao": pre.headers.get("access-control-allow-origin")})

    # 4. Origin check on state-changing requests (CSRF defence for cookie auth)
    r = donor.post("/blood-requests", json={"blood_type": "O_NEG"},
                   headers={"Origin": "https://evil.example"})
    check("state change with a foreign Origin is refused (403)", r.status_code == 403, r.status_code)
    r = httpx.post(f"{api}/blood-requests", json={"blood_type": "O_NEG"},
                   cookies=donor.cookies, timeout=30)
    check("state change with no Origin header is refused (403)", r.status_code == 403, r.status_code)

    # 5. Vertical privilege escalation
    for path in ["/admin/users", "/admin/verifications", "/admin/transport-routes"]:
        r = donor.get(path)
        check(f"donor cannot read {path}", r.status_code == 403, r.status_code)
    r = donor.post("/admin/audit-log/verify")
    check("donor cannot run the audit-chain verification", r.status_code == 403, r.status_code)
    r = donor.post("/admin/transport-routes", json={})
    check("donor cannot create transport routes", r.status_code in (403, 422) and r.status_code != 200,
          r.status_code)

    # 6. Horizontal (object-level) access
    patient = login(api, "patient")
    mine = patient.get("/blood-requests").json()
    items = mine["items"] if isinstance(mine, dict) and "items" in mine else mine
    if items:
        rid = items[0]["id"]
        family = login(api, "family")
        hosp = login(api, "hospital")
        other = httpx.Client(base_url=api, headers=ORIGIN, timeout=30)
        other.post("/auth/login", json={"email": "demo.donor@lifelink-demo.example.com", "password": PW})
        r = other.post(f"/blood-requests/{rid}/cancel", json={"reason": "probe"})
        check("a donor cannot cancel another user's blood request", r.status_code in (403, 404),
              r.status_code)
        r = hosp.post(f"/emergencies/{uuid.uuid4()}/cancel", json={"reason": "probe"})
        check("unknown emergency id is 404, not 500", r.status_code in (403, 404), r.status_code)
        _ = family
    r = donor.get(f"/certificates/{uuid.uuid4()}")
    check("certificate of nobody / not mine is 403/404", r.status_code in (403, 404), r.status_code)

    # 7. Injection and malformed input
    for payload in ["' OR 1=1 --", "<script>alert(1)</script>", "../../etc/passwd"]:
        r = donor.get("/blood-requests", params={"q": payload, "city": payload})
        check(f"query string {payload!r} handled without error or leak",
              r.status_code < 500 and not LEAK.search(r.text), r.status_code)
    r = donor.post("/blood-requests", content=b"{not json", headers={"content-type": "application/json"})
    check("malformed JSON is 422 with no stack trace", r.status_code == 422 and not LEAK.search(r.text),
          r.status_code)
    r = donor.get("/blood-requests/not-a-uuid")
    check("non-UUID path parameter is 422, not 500", r.status_code == 422, r.status_code)
    admin = login(api, "admin")
    r = admin.get(f"/admin/verifications/{uuid.uuid4()}/documents/..%2F..%2Fetc%2Fpasswd")
    check("path traversal in a document index is rejected (422/404)", r.status_code in (404, 422),
          r.status_code)

    # 9. Logout invalidates the session server-side
    t = login(api, "donor")
    cookies = dict(t.cookies)
    t.post("/auth/logout")
    r = httpx.get(f"{api}/auth/me", cookies=cookies)
    check("a captured session cookie is useless after logout", r.status_code == 401, r.status_code)

    # 10. Unauthenticated access
    for path in ["/notifications", "/admin/users", "/hospitals/me/units", "/events/stream"]:
        r = httpx.get(f"{api}{path}", timeout=10)
        check(f"anonymous {path} is 401", r.status_code == 401, r.status_code)

    # 11. Error responses do not leak internals
    r = donor.get(f"/emergencies/{uuid.uuid4()}")
    check("not-found error body has no internals", not LEAK.search(r.text), r.status_code)

    # 12. (last: it exhausts the login budget) Rate-limit bypass through a forged X-Forwarded-For
    codes = []
    for i in range(14):
        r = httpx.post(f"{api}/auth/login", headers={**ORIGIN, "X-Forwarded-For": f"10.0.0.{i}"},
                       json={"email": f"nobody{i}@example.com", "password": "wrong-password-123"})
        codes.append(r.status_code)
    check("forged X-Forwarded-For does not reset the per-IP login limit (429 reached)",
          429 in codes, codes)

    passed = sum(ok_all)
    lines.append(f"\nSUMMARY: {passed}/{len(ok_all)} checks passed")
    print(lines[-1])
    with open(a.out, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return 0 if passed == len(ok_all) else 1


if __name__ == "__main__":
    raise SystemExit(main())
