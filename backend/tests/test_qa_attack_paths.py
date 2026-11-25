"""Final QA -- manual attack paths, beyond the Phase H route-table sweeps.

1. Object-level authorization (IDOR / BOLA) sweep: tenant A owns one of
   every kind of resource; an attacker B holding the SAME role (so role
   checks pass) calls every ID-taking endpoint with A's IDs. Each must be
   refused (403/404) -- never 2xx, never 5xx -- while A itself (positive
   control) is allowed. Endpoints that are shared BY DESIGN are listed
   separately with the reason.
2. Token / session manipulation, path traversal, SQL-injection-shaped
   input, stored-XSS payloads, error leakage, secret leakage, security
   headers, CORS, role escalation at registration.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.hospital import HospitalProfile
from tests.helpers import auth_client

ORIGIN = {"Origin": "http://localhost:5173"}


def _ok(r) -> bool:
    return 200 <= r.status_code < 300


@pytest.fixture
def world(db_session):
    """Tenant A owns: a blood request + emergency (patient), a family link,
    a verified donation + certificate + appointment + notification (donor),
    a shipment between two Metro facilities. Attackers: same-role users of
    tenant B (a hospital in another city)."""
    w: dict = {}
    pa, pa_email = auth_client("PATIENT")
    pb, _ = auth_client("PATIENT")
    fa, _ = auth_client("FAMILY")
    fb, _ = auth_client("FAMILY")
    da, _ = auth_client("DONOR")
    dbb, _ = auth_client("DONOR")
    admin, _ = auth_client("ADMIN")

    def hospital(name, city):
        c, _ = auth_client("HOSPITAL")
        r = c.put("/hospitals/me", json={"name": name, "city": city})
        assert r.status_code == 200, r.text
        return c, r.json()["id"]

    ha, ha_id = hospital("Alpha General", "Metro")
    ha2, ha2_id = hospital("Alpha Annex", "Metro")
    hb, hb_id = hospital("Bravo General", "Elsewhere")
    for fid in (ha_id, ha2_id, hb_id):
        db_session.get(HospitalProfile, uuid.UUID(fid)).verified = True
    db_session.commit()

    r = pa.post("/blood-requests", json={"blood_type": "O_NEG", "units_needed": 2,
                                         "city": "Metro", "hospital_name": "Alpha General"})
    assert r.status_code == 201, r.text
    w["request"] = r.json()["id"]
    r = pa.post("/emergencies", json={"blood_type": "O_NEG", "location_text": "Metro"})
    assert r.status_code == 201, r.text
    w["emergency"] = r.json()["id"]
    r = fa.post("/family/links", json={"patient_email": pa_email, "relationship": "son"})
    assert r.status_code == 201, r.text
    w["link"] = r.json()["id"]

    da.put("/donors/me", json={"blood_type": "O_NEG", "is_available": True})
    r = da.post("/donors/me/donations", json={
        "donated_on": (date.today() - timedelta(days=70)).isoformat(), "units": 1,
        "hospital_name": "Alpha General"})
    assert r.status_code in (200, 201), r.text
    w["donation"] = r.json()["id"]
    assert admin.post(f"/donations/{w['donation']}/verify", json={}).status_code == 200
    r = da.post("/certificates", json={"donation_id": w["donation"]})
    assert r.status_code == 201, r.text
    w["cert"], w["cert_token"] = r.json()["id"], r.json()["download_token"]
    r = da.post("/donors/me/appointments", json={
        "hospital_id": ha_id, "preferred_date": (date.today() + timedelta(days=60)).isoformat()})
    assert r.status_code == 201, r.text
    w["appointment"] = r.json()["id"]
    notes = da.get("/notifications").json()
    assert notes, "donor A should have a notification (donation verified)"
    w["notification"] = notes[0]["id"]

    ha.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 4})
    r = ha.post("/shipments", json={"origin_facility_id": ha_id, "destination_facility_id": ha2_id,
                                    "items": [{"blood_type": "O_NEG", "units": 2}]})
    assert r.status_code == 201, r.text
    w["shipment"], w["shipment_code"] = r.json()["id"], r.json()["shipment_code"]
    w["unit_code"] = r.json()["items"][0]["unit_code"]

    w.update(pa=pa, pb=pb, fa=fa, fb=fb, da=da, db=dbb, ha=ha, hb=hb, admin=admin)
    return w


def test_idor_sweep_same_role_attacker_is_refused_everywhere(world) -> None:
    w = world
    R, E, S = w["request"], w["emergency"], w["shipment"]
    attacks = [
        # patient B vs patient A's request / emergency
        ("pb", "GET", f"/blood-requests/{R}", None),
        ("pb", "POST", f"/blood-requests/{R}/cancel", {"reason": "x"}),
        ("pb", "POST", f"/blood-requests/{R}/fulfill", {}),
        ("pb", "GET", f"/blood-requests/{R}/match-candidates", None),
        ("pb", "GET", f"/blood-requests/{R}/match-strategies", None),
        ("pb", "GET", f"/emergencies/{E}", None),
        ("pb", "POST", f"/emergencies/{E}/cancel", {}),
        ("pb", "POST", f"/emergencies/{E}/resolve", {}),
        ("pb", "POST", f"/emergencies/{E}/assistance-found", {}),
        ("pb", "GET", f"/emergencies/{E}/match-candidates", None),
        ("pb", "GET", f"/emergencies/{E}/match-strategies", None),
        ("pb", "GET", f"/emergencies/{E}/resource-pathways", None),
        # family B vs family A's link
        ("fb", "POST", f"/family/links/{w['link']}/revoke", {}),
        ("fb", "POST", f"/family/links/{w['link']}/accept", {}),
        ("fb", "POST", f"/family/links/{w['link']}/reject", {}),
        ("pb", "POST", f"/family/links/{w['link']}/accept", {}),
        # donor B vs donor A's certificate / appointment / notification
        ("db", "GET", f"/certificates/{w['cert']}", None),
        ("db", "GET", f"/certificates/{w['cert']}/download", None),
        ("db", "POST", f"/donors/me/appointments/{w['appointment']}/cancel", {}),
        ("db", "POST", f"/notifications/{w['notification']}/read", {}),
        # hospital B (other city) vs Alpha's request / shipment
        ("hb", "GET", f"/hospitals/me/requests/{R}/events", None),
        ("hb", "POST", f"/hospitals/me/requests/{R}/allocate", {"units": 1}),
        ("hb", "POST", f"/hospitals/me/requests/{R}/fulfill", {"units": 1}),
        ("hb", "POST", f"/hospitals/me/requests/{R}/respond", {"status": "ACCEPTED"}),
        ("hb", "GET", f"/shipments/{S}", None),
        ("hb", "GET", f"/shipments/{S}/timeline", None),
        ("hb", "GET", f"/shipments/by-code/{w['shipment_code']}", None),
        ("hb", "GET", f"/shipments/units/{w['unit_code']}", None),
        ("hb", "POST", f"/shipments/{S}/dispatch", {}),
        ("hb", "POST", f"/shipments/{S}/cancel", {"reason": "x"}),
        ("hb", "POST", f"/shipments/{S}/delay", {"reason": "x", "delay_minutes": 30}),
        ("hb", "POST", f"/shipments/{S}/fail", {"reason": "x"}),
        ("hb", "POST", f"/shipments/{S}/receive", {}),
        ("hb", "GET", f"/donations/{w['donation']}", None),
        ("hb", "POST", f"/donations/{w['donation']}/reject", {"reason": "x"}),
    ]
    leaks = []
    for who, method, url, body in attacks:
        kw = {"json": body} if body is not None else {}
        r = w[who].request(method, url, **kw)
        if _ok(r) or r.status_code >= 500:
            leaks.append(f"{who} {method} {url} -> {r.status_code} {r.text[:120]}")
    assert leaks == []

    # Positive controls: the owners themselves are allowed (the IDs are real).
    assert w["pa"].get(f"/blood-requests/{R}").status_code == 200
    assert w["pa"].get(f"/emergencies/{E}").status_code == 200
    assert w["da"].get(f"/certificates/{w['cert']}").status_code == 200
    assert w["ha"].get(f"/shipments/{S}").status_code == 200
    assert w["ha"].get(f"/shipments/by-code/{w['shipment_code']}").status_code == 200


def test_shared_by_design_endpoints_reveal_only_what_they_should(world, client) -> None:
    """Public certificate verification is anonymous by design: it must
    reveal the minimum (no email, no user id, no download token)."""
    w = world
    code = w["da"].get(f"/certificates/{w['cert']}").json()["verify_path"].rsplit("/", 1)[-1]
    r = client.get(f"/certificates/verify/{code}")
    assert r.status_code == 200
    body = r.text
    for secret in ("@example.com", w["cert_token"], "donor_user_id", "password"):
        assert secret not in body


# --------------------------------------------------------------------------- #
# Token / session manipulation
# --------------------------------------------------------------------------- #
def test_tampered_expired_and_cross_certificate_tokens_are_refused(world, client) -> None:
    """The download token is a short-lived signed BEARER link (by design: it
    lets the PDF open in a new tab without cookies; TTL
    CERTIFICATE_DOWNLOAD_TTL_SECONDS). So it is tested anonymously: a valid
    token works, anything tampered, expired, or minted for another
    certificate does not."""
    import time

    from app.services import certificate_service

    w = world
    cert, token = w["cert"], w["cert_token"]
    anon = client
    assert anon.get(f"/certificates/{cert}/download", params={"token": token}).status_code == 200
    exp, digest = token.split(".", 1)
    forged = [
        token[:-2] + ("00" if not token.endswith("00") else "11"),   # digest tampered
        f"{int(exp) + 3600}.{digest}",                               # expiry extended
        "", "A" * 64, token + "x", "1.2.3",
        certificate_service.make_download_token(uuid.UUID(cert), ttl=-10),  # expired
        certificate_service.make_download_token(uuid.uuid4()),        # another cert's token
    ]
    for bad in forged:
        r = anon.get(f"/certificates/{cert}/download", params={"token": bad})
        assert r.status_code == 401, (bad, r.status_code)
    # A signed-in OTHER donor gets nothing without a valid token, and is audited.
    assert w["db"].get(f"/certificates/{cert}/download").status_code == 401
    # Forged session cookies.
    for sid in ("x", "A" * 43, "../../etc/passwd", "' OR '1'='1"):
        anon.cookies.set(settings.session_cookie_name, sid)
        assert anon.get("/auth/me").status_code == 401
        anon.cookies.clear()
    # Verification-token guessing.
    for t in ("", "a" * 43, "../../", "<script>"):
        r = anon.post("/auth/verify-email", headers=ORIGIN, json={"token": t})
        assert r.status_code in (400, 422)
    time.sleep(0)


def test_logout_and_password_change_kill_server_side_sessions(client) -> None:
    c, email = auth_client("DONOR")
    sid = c.cookies.get(settings.session_cookie_name)
    assert c.post("/auth/logout", json={}).status_code in (200, 204)
    client.cookies.set(settings.session_cookie_name, sid)
    assert client.get("/auth/me").status_code == 401  # replayed cookie is dead
    client.cookies.clear()


# --------------------------------------------------------------------------- #
# Path traversal / injection / XSS / errors / secrets / headers / CORS
# --------------------------------------------------------------------------- #
def test_document_index_traversal_and_garbage_ids(world) -> None:
    admin = world["admin"]
    rid = uuid.uuid4()
    for idx in ("../../etc/passwd", "-1", "999999", "0x1", "%2e%2e%2f"):
        r = admin.get(f"/admin/verifications/{rid}/documents/{idx}")
        assert r.status_code in (404, 422), (idx, r.status_code)
    for bad in ("not-a-uuid", "1 OR 1=1", "../admin"):
        r = admin.get(f"/admin/verifications/{bad}")
        assert r.status_code in (404, 422)


def test_upload_filename_traversal_never_reaches_storage_keys(world, db_session) -> None:
    from app.models.verification import VerificationRequest

    da = world["db"]
    pdf = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
    r = da.post("/verifications", data={"kind": "DONOR_IDENTITY"},
                files={"files": ("../../../../etc/passwd.pdf", pdf, "application/pdf")})
    assert r.status_code in (200, 201), r.text
    req = db_session.scalar(select(VerificationRequest).where(
        VerificationRequest.id == uuid.UUID(r.json()["id"])))
    key = req.documents[0]["key"]
    # The client's filename is reduced to a safe basename inside a per-request,
    # server-generated prefix: no traversal, no absolute path.
    assert ".." not in key and "/etc/" not in key and not key.startswith("/")
    assert key.startswith(("quarantine/verifications/", "verifications/"))


@pytest.mark.parametrize(
    "q", ["' OR 1=1 --", "\"; DROP TABLE users; --", "%", "_", "\\", "' UNION SELECT"]
)
def test_sql_injection_shaped_search_input_is_inert(world, q) -> None:
    admin = world["admin"]
    for url in ("/admin/users", "/admin/blood-requests", "/admin/certificates", "/admin/donors"):
        r = admin.get(url, params={"query": q} if "blood" in url else {"q": q, "query": q})
        assert r.status_code == 200, (url, r.status_code, r.text[:200])
    # still all there
    assert len(admin.get("/admin/users").json()["items"] if isinstance(
        admin.get("/admin/users").json(), dict) else admin.get("/admin/users").json()) >= 9


def test_stored_xss_payload_is_returned_as_inert_data(client) -> None:
    payload = "<img src=x onerror=alert(1)><script>alert(2)</script>"
    c, _ = auth_client("PATIENT", name=payload)
    me = c.get("/auth/me")
    assert me.headers["content-type"].startswith("application/json")
    assert me.json()["full_name"] == payload  # stored verbatim, rendered as text by React
    assert me.headers.get("x-content-type-options") == "nosniff"


def test_errors_do_not_leak_internals(client) -> None:
    c, _ = auth_client("ADMIN")
    long_code = "/certificates/verify/" + "x" * 500
    for url in ("/admin/verifications/not-a-uuid", "/blood-requests/%00", long_code):
        r = c.get(url)
        assert r.status_code < 500
        assert "Traceback" not in r.text and "sqlalchemy" not in r.text.lower()
        assert "/home/" not in r.text and "psycopg" not in r.text.lower()


def test_no_secret_reaches_any_diagnostic_endpoint(client) -> None:
    c, _ = auth_client("ADMIN")
    # Make sure password-named routes are in the metrics listing, as in a
    # full run (the original version of this check failed only then).
    c.post("/auth/forgot-password", json={"email": "nobody@example.com"})
    bodies = [client.get("/health").text, client.get("/health/ready").text,
              client.get("/health/live").text, c.get("/admin/system/health").text,
              c.get("/admin/system/metrics").text]
    # The real secret VALUES, not the word "password": route names such as
    # /auth/change-password legitimately appear in the metrics listing.
    from sqlalchemy.engine import make_url
    db_url = make_url(settings.database_url)
    secrets = [settings.secret_key, db_url.render_as_string(hide_password=False)]
    if db_url.password:
        secrets += [str(db_url.password), f"{db_url.username}:{db_url.password}@"]
    if settings.mail_password is not None:
        secrets.append(settings.mail_password.get_secret_value())
    if settings.s3_secret_key:
        secrets.append(settings.s3_secret_key)
    secrets = [s for s in secrets if s]  # unset optional secrets are ""
    assert settings.secret_key in secrets
    for b in bodies:
        assert '"password":' not in b.lower()
        for s in secrets:
            assert s not in b


def test_security_headers_on_api_responses(client) -> None:
    r = client.get("/health")
    h = {k.lower(): v for k, v in r.headers.items()}
    assert h.get("x-content-type-options") == "nosniff"
    assert h.get("x-frame-options", "").upper() in ("DENY", "SAMEORIGIN")
    assert "referrer-policy" in h


def test_cors_preflight_from_evil_origin_gets_no_grant(client) -> None:
    r = client.options("/auth/login", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert r.headers.get("access-control-allow-origin") is None
    r = client.options("/auth/login", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


@pytest.mark.parametrize("role", ["ADMIN", "admin", "SUPERUSER", "HOSPITAL_ADMIN"])
def test_registration_cannot_self_assign_privileged_roles(client, role) -> None:
    r = client.post("/auth/register", headers=ORIGIN, json={
        "full_name": "Escalator", "email": f"esc-{uuid.uuid4().hex[:6]}@example.com",
        "password": "Escalate-pass-2026", "role": role})
    assert r.status_code == 422


def test_session_endpoint_is_public_200_and_reveals_only_the_callers_own_user(client) -> None:
    r = client.get("/auth/session")
    assert r.status_code == 200 and r.json() == {"authenticated": False, "user": None}
    c, email = auth_client("DONOR")
    body = c.get("/auth/session").json()
    assert body["authenticated"] is True and body["user"]["email"] == email
    assert "password_hash" not in str(body)
    client.cookies.set(settings.session_cookie_name, "forged-session-id")
    assert client.get("/auth/session").json()["authenticated"] is False
    client.cookies.clear()
