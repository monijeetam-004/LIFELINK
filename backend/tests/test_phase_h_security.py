"""Phase H -- security/privacy hardening pass.

Earlier phases (7, 8, 13-16, 19, 20, 23, D, E, F) each verified the
authorization of the endpoints they added, one endpoint at a time. This
file adds what none of them had: SYSTEMIC checks over the real, mounted
route table, so a route added later cannot silently skip a guard.

  1. Route inventory (static): every route is either on an explicit,
     reviewed PUBLIC list or depends on `get_current_user`; every
     state-changing route depends on `verify_origin` (CSRF); every
     /admin route is gated to ADMIN only.
  2. Unauthenticated sweep (live): every non-public route answers 401 to
     a request with no session -- never 2xx, never 500.
  3. Role sweep (live): every role-gated route answers 403 to every role
     it does not allow -- before any object lookup.
  4. CSRF sweep (live): every state-changing route, called by a user who
     WOULD be allowed, answers 403 "Origin not allowed" for a foreign
     Origin.
  5. Mass assignment (static): no request body model accepts a
     server-owned field, except a short, reviewed list.
  6. Cross-tenant reads added after Phase 23 (shipment by code, unit
     trace, shipment list, inventory log): an unrelated hospital sees
     nothing.
  7. Sensitive data never reaches logs or the audit trail (passwords,
     reset OTPs, session ids).
"""

from __future__ import annotations

import logging
import re
import uuid

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import select

from app.db.session import SessionLocal
from app.main import app
from app.models.audit_log import AuditLog
from app.models.hospital import HospitalProfile
from app.models.user import UserRole
from tests.helpers import auth_client, extract_otp, register, verify_user

GOOD_ORIGIN = "http://localhost:5173"
EVIL_ORIGIN = "https://evil.example"
STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}

# Reviewed public surface. Each entry is public by design:
#  - health probes; the API root banner
#  - the pre-authentication auth flows
#  - certificate verification by public code (the whole point of a
#    verifiable certificate) and download, which authorizes in-handler by
#    owner/admin session OR an HMAC download token (Phase 15)
#  - the SSE stream, which authenticates in-handler from the session
#    cookie because it must not hold a DB session open (Phase F); its 401
#    is asserted by the unauthenticated sweep below like every other route.
PUBLIC = {
    ("GET", "/"),
    ("GET", "/health"),
    ("GET", "/health/live"),
    ("GET", "/health/ready"),
    ("POST", "/auth/register"),
    ("POST", "/auth/login"),
    ("POST", "/auth/logout"),
    # QA: answers 200 {authenticated:false} anonymously, so the SPA's start-up
    # check no longer logs a 401 on every public page.
    ("GET", "/auth/session"),
    ("POST", "/auth/verify-email"),
    ("POST", "/auth/verify-email/resend"),
    ("POST", "/auth/forgot-password"),
    ("POST", "/auth/reset-password"),
    ("GET", "/certificates/verify/{code}"),
    ("GET", "/certificates/{certificate_id}/download"),
}
# Public but authenticating in-handler: must still 401 without a session.
SELF_AUTHENTICATING = {("GET", "/events/stream")}


def _routes() -> list[tuple[str, str, APIRoute]]:
    def walk(routes):
        for r in routes:
            if type(r).__name__ == "_IncludedRouter":
                yield from walk(r.original_router.routes)
            elif isinstance(r, APIRoute):
                yield r

    out = []
    for r in walk(app.routes):
        for m in sorted(r.methods):
            if m in {"HEAD", "OPTIONS"}:
                continue
            out.append((m, r.path, r))
    return out


def _calls(dependant) -> list:
    found = []
    for d in dependant.dependencies:
        found.append(d.call)
        found.extend(_calls(d))
    return found


def _names(route: APIRoute) -> set[str]:
    return {getattr(c, "__qualname__", "") for c in _calls(route.dependant)}


def _allowed_roles(route: APIRoute) -> set[UserRole] | None:
    """The role set closed over by `require_roles(...)`, or None."""
    for call in _calls(route.dependant):
        if getattr(call, "__qualname__", "").startswith("require_roles.<locals>"):
            for cell in call.__closure__ or ():
                v = cell.cell_contents
                if isinstance(v, set) and v and all(isinstance(x, UserRole) for x in v):
                    return v
    return None


ROUTES = _routes()


def _fill(path: str) -> str:
    def sub(m: re.Match) -> str:
        name = m.group(1)
        if name == "index":
            return "0"
        if name.endswith("_id"):
            return str(uuid.uuid4())
        return "NOPE-" + uuid.uuid4().hex[:8]

    return re.sub(r"\{([^}]+)\}", sub, path)


def _call(client, method: str, path: str, **kw):
    if method in STATE_CHANGING:
        return client.request(method, path, json={}, **kw)
    return client.request(method, path, **kw)


# --------------------------------------------------------------------------- #
# 1. Static route inventory
# --------------------------------------------------------------------------- #


def test_route_table_is_what_this_file_reviewed() -> None:
    # Guards the sweeps below against silently testing nothing.
    assert len(ROUTES) >= 138  # measured at Phase H
    keys = {(m, p) for m, p, _ in ROUTES}
    assert PUBLIC <= keys and SELF_AUTHENTICATING <= keys


def test_every_non_public_route_requires_a_session() -> None:
    missing = [
        f"{m} {p}"
        for m, p, r in ROUTES
        if (m, p) not in PUBLIC | SELF_AUTHENTICATING and "get_current_user" not in _names(r)
    ]
    assert missing == [], f"routes with no authentication dependency: {missing}"


def test_every_state_changing_route_has_csrf_origin_check() -> None:
    missing = [
        f"{m} {p}" for m, p, r in ROUTES if m in STATE_CHANGING and "verify_origin" not in _names(r)
    ]
    assert missing == [], f"state-changing routes without verify_origin: {missing}"


def test_every_admin_route_is_admin_only() -> None:
    wrong = []
    for m, p, r in ROUTES:
        if p.startswith("/admin"):
            roles = _allowed_roles(r)
            if roles != {UserRole.ADMIN}:
                wrong.append(f"{m} {p} -> {roles}")
    assert wrong == [], wrong


# --------------------------------------------------------------------------- #
# 2-4. Live sweeps
# --------------------------------------------------------------------------- #


def test_unauthenticated_sweep_every_protected_route_is_401(client) -> None:
    client.headers["Origin"] = GOOD_ORIGIN
    bad = []
    checked = 0
    for m, p, _ in ROUTES:
        if (m, p) in PUBLIC:
            continue
        resp = _call(client, m, _fill(p))
        checked += 1
        if resp.status_code != 401:
            bad.append(f"{m} {p} -> {resp.status_code}")
    assert checked >= 125
    assert bad == [], bad


@pytest.fixture
def sessions() -> dict[UserRole, object]:
    return {role: auth_client(role.value)[0] for role in UserRole}


def test_role_sweep_every_gated_route_rejects_every_other_role(sessions) -> None:
    bad = []
    checked = 0
    for m, p, r in ROUTES:
        allowed = _allowed_roles(r)
        if allowed is None:
            continue
        for role, c in sessions.items():
            if role in allowed:
                continue
            resp = _call(c, m, _fill(p))
            checked += 1
            if resp.status_code != 403:
                bad.append(f"{role.value} {m} {p} -> {resp.status_code}")
    assert checked >= 300
    assert bad == [], bad


def test_csrf_sweep_foreign_origin_rejected_even_for_an_allowed_user(sessions, client) -> None:
    bad = []
    checked = 0
    for m, p, r in ROUTES:
        if m not in STATE_CHANGING:
            continue
        allowed = _allowed_roles(r)
        if (m, p) in PUBLIC:
            c = client
        else:
            role = sorted(allowed, key=lambda x: x.value)[0] if allowed else UserRole.DONOR
            c = sessions[role]
        resp = _call(c, m, _fill(p), headers={"Origin": EVIL_ORIGIN})
        checked += 1
        if resp.status_code != 403 or resp.json().get("detail") != "Origin not allowed":
            bad.append(f"{m} {p} -> {resp.status_code} {resp.text[:80]}")
    assert checked >= 68  # every state-changing route at Phase H
    assert bad == [], bad


def test_csrf_foreign_origin_does_not_log_the_user_out(sessions) -> None:
    c = sessions[UserRole.DONOR]
    assert c.post("/auth/logout", headers={"Origin": EVIL_ORIGIN}).status_code == 403
    assert c.get("/auth/me").status_code == 200


# --------------------------------------------------------------------------- #
# 5. Mass assignment
# --------------------------------------------------------------------------- #

SERVER_OWNED = {
    "id",
    "user_id",
    "role",
    "verified",
    "identity_verified",
    "is_active",
    "is_email_verified",
    "verification_status",
    "fulfilled_units",
    "state",
    "status",
    "target_status",
    "seq",
    "created_at",
    "updated_at",
    "password_hash",
    "entry_hash",
    "prev_hash",
    "reconciliation_status",
    "donor_user_id",
    "requester_user_id",
    "reviewed_by_user_id",
    "published_at",
    "expires_at",
    "unit_code",
    "shipment_code",
}
# Reviewed exceptions: each field is a legitimate input, validated server-side.
REVIEWED = {
    ("POST", "/auth/register", "role"),  # restricted to self-registrable roles (Phase 11)
    ("PATCH", "/admin/users/{user_id}/role", "role"),  # admin-only role management
    ("PUT", "/donors/me/availability", "state"),  # donor's own availability toggle
    (
        "POST",
        "/hospitals/me/requests/{request_id}/respond",
        "status",
    ),  # validator excludes ALLOCATED/FULFILLED
    (
        "POST",
        "/admin/blood-requests/{request_id}/transition",
        "target_status",
    ),  # admin, transition-table checked
}


def test_no_request_body_accepts_a_server_owned_field() -> None:
    offending = []
    for m, p, r in ROUTES:
        for param in r.dependant.body_params:
            model = getattr(param, "type_", None) or param.field_info.annotation
            fields = getattr(model, "model_fields", None) or {}
            for f in fields:
                if f in SERVER_OWNED and (m, p, f) not in REVIEWED:
                    offending.append(f"{m} {p}: {f}")
    assert offending == [], offending


def test_extra_fields_are_ignored_not_applied() -> None:
    c, _ = auth_client("HOSPITAL")
    r = c.put(
        "/hospitals/me",
        json={
            "name": "Mass Assign Hosp",
            "verified": True,
            "user_id": str(uuid.uuid4()),
            "id": str(uuid.uuid4()),
        },
    )
    assert r.status_code == 200, r.text
    db = SessionLocal()
    try:
        hp = db.get(HospitalProfile, uuid.UUID(r.json()["id"]))
        assert hp.verified is False
        assert hp.name == "Mass Assign Hosp"
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# 6. Cross-tenant reads added after the Phase 23 audit
# --------------------------------------------------------------------------- #


def _facility(name: str):
    c, _ = auth_client("HOSPITAL")
    r = c.put("/hospitals/me", json={"name": name, "city": "Metro"})
    assert r.status_code == 200, r.text
    return c, r.json()["id"]


def test_unrelated_hospital_sees_nothing_of_another_facilitys_shipments_and_units() -> None:
    origin, oid = _facility(f"Origin-{uuid.uuid4().hex[:6]}")
    dest, did = _facility(f"Dest-{uuid.uuid4().hex[:6]}")
    stranger, _ = _facility(f"Stranger-{uuid.uuid4().hex[:6]}")
    db = SessionLocal()
    try:
        db.get(HospitalProfile, uuid.UUID(did)).verified = True
        db.commit()
    finally:
        db.close()
    assert (
        origin.post(
            "/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 3}
        ).status_code
        == 200
    )
    r = origin.post(
        "/shipments",
        json={
            "origin_facility_id": oid,
            "destination_facility_id": did,
            "items": [{"blood_type": "O_NEG", "units": 2}],
        },
    )
    assert r.status_code == 201, r.text
    sh = r.json()
    code = sh["shipment_code"]
    unit_code = sh["items"][0]["unit_code"]

    # Parties can read.
    assert origin.get(f"/shipments/by-code/{code}").status_code == 200
    assert dest.get(f"/shipments/by-code/{code}").status_code == 200
    assert origin.get(f"/shipments/units/{unit_code}").status_code == 200
    # A stranger cannot -- by id, by code, by unit code, or by listing.
    assert stranger.get(f"/shipments/by-code/{code}").status_code == 403
    assert stranger.get(f"/shipments/units/{unit_code}").status_code == 403
    assert stranger.get("/shipments").json() == []
    assert stranger.get("/shipments", params={"direction": "outgoing"}).json() == []
    # The stranger's inventory log does not contain the origin's movements.
    log = stranger.get("/hospitals/me/inventory/log").json()
    assert all(str(e.get("hospital_id", "")) != oid for e in log)
    assert log == []


# --------------------------------------------------------------------------- #
# 7. Sensitive data never logged or audited
# --------------------------------------------------------------------------- #


def test_passwords_otps_and_session_ids_never_reach_logs_or_audit(client, mailbox, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    client.headers["Origin"] = GOOD_ORIGIN
    email = f"h-{uuid.uuid4().hex[:8]}@example.com"
    pw1, pw2, pw3 = "first secret passphrase", "second secret passphrase", "third secret passphrase"

    assert register(client, email=email, password=pw1).status_code == 201
    verify_user(client, mailbox)
    client.post("/auth/login", json={"email": email, "password": "wrong wrong wrong"})
    assert client.post("/auth/login", json={"email": email, "password": pw1}).status_code == 200
    sid = client.cookies.get("lifelink_session")
    assert sid
    assert (
        client.post(
            "/auth/change-password",
            json={
                "current_password": pw1,
                "new_password": pw2,
            },
        ).status_code
        == 200
    )
    client.post("/auth/forgot-password", json={"email": email})
    otp = extract_otp(mailbox)
    assert (
        client.post(
            "/auth/reset-password",
            json={
                "email": email,
                "otp": otp,
                "new_password": pw3,
            },
        ).status_code
        == 200
    )

    secrets = [pw1, pw2, pw3, sid, "wrong wrong wrong"]
    logged = "\n".join(
        f"{rec.getMessage()} {getattr(rec, '__dict__', {})}" for rec in caplog.records
    )
    assert len(caplog.records) > 0
    for s in secrets:
        assert s not in logged, "secret material reached a log record"
    # The OTP is a 6-digit number; check it is never logged as a value.
    assert not re.search(rf"\b{otp}\b", logged)

    db = SessionLocal()
    try:
        rows = db.scalars(select(AuditLog)).all()
        assert rows
        dumped = "\n".join(f"{r.action} {r.meta}" for r in rows)
    finally:
        db.close()
    for s in secrets:
        assert s not in dumped, "secret material reached the audit trail"
    assert not re.search(rf"\b{otp}\b", dumped)


# --------------------------------------------------------------------------- #
# 8. CORS configuration fails closed (Phase H finding)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("value", ["*", "http://localhost:5173,*", "https://*.example.org"])
def test_wildcard_cors_origin_is_refused_at_startup(value) -> None:
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError, match="wildcard"):
        Settings(cors_origins=value)


@pytest.mark.parametrize(
    "value", ["https://app.example.org/", "https://app.example.org/path", "app.example.org"]
)
def test_non_origin_cors_entries_are_refused(value) -> None:
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError, match="bare origins"):
        Settings(cors_origins=value)


def test_exact_origins_are_accepted_and_foreign_origins_get_no_cors_grant(client) -> None:
    from app.core.config import Settings

    assert Settings(
        cors_origins="https://app.example.org,http://localhost:4173"
    ).cors_origin_list == [
        "https://app.example.org",
        "http://localhost:4173",
    ]
    r = client.get("/health", headers={"Origin": EVIL_ORIGIN})
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}
    r = client.get("/health", headers={"Origin": GOOD_ORIGIN})
    assert r.headers.get("access-control-allow-origin") == GOOD_ORIGIN


# --------------------------------------------------------------------------- #
# 9. Session fixation
# --------------------------------------------------------------------------- #


def test_login_never_adopts_a_client_supplied_session_id(client) -> None:
    from tests.helpers import seed_verified_user

    email = f"fix-{uuid.uuid4().hex[:8]}@example.com"
    seed_verified_user(email, "a decent passphrase", "DONOR")
    planted = "attacker-chosen-" + uuid.uuid4().hex
    client.headers["Origin"] = GOOD_ORIGIN
    r = client.post(
        "/auth/login",
        json={"email": email, "password": "a decent passphrase"},
        headers={"Cookie": f"lifelink_session={planted}"},
    )
    assert r.status_code == 200
    issued = r.cookies.get("lifelink_session")
    assert issued and issued != planted

    from fastapi.testclient import TestClient

    attacker = TestClient(app, base_url="http://testserver")
    attacker.cookies.set("lifelink_session", planted)
    assert attacker.get("/auth/me").status_code == 401


def test_a_new_login_issues_a_fresh_session_id_each_time(client) -> None:
    from tests.helpers import seed_verified_user

    email = f"rot-{uuid.uuid4().hex[:8]}@example.com"
    seed_verified_user(email, "a decent passphrase", "DONOR")
    client.headers["Origin"] = GOOD_ORIGIN
    client.post("/auth/login", json={"email": email, "password": "a decent passphrase"})
    first = client.cookies.get("lifelink_session")
    client.post("/auth/login", json={"email": email, "password": "a decent passphrase"})
    second = client.cookies.get("lifelink_session")
    assert first and second and first != second
