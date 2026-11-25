from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.api.routes.admin import _classify_severity
from tests.helpers import auth_client

NON_ADMIN_ROLES = ["PATIENT", "FAMILY", "DONOR", "HOSPITAL"]


def _hospital(city="Metro", name="St Jude", lat=40.0, lng=-73.0):
    c, _ = auth_client("HOSPITAL")
    c.put("/hospitals/me", json={"name": name, "city": city, "latitude": lat, "longitude": lng})
    return c


def _facility_id(admin, name: str) -> str:
    """`/hospitals/me` only ever returns `user_id` -- the admin facilities
    endpoints key on the HospitalProfile row's own `id`, so tests that need
    it look it up via the admin directory by the facility's (unique-enough
    for a test) name.
    """
    rows = admin.get("/admin/facilities", params={"query": name}).json()["items"]
    return next(f["id"] for f in rows if f["name"] == name)


def _donor(blood_type="O_NEG", city="Metro"):
    c, _ = auth_client("DONOR")
    c.put("/donors/me", json={"blood_type": blood_type, "city": city})
    return c


# --- Facilities -------------------------------------------------------------- #


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_facilities_list_requires_admin(role: str) -> None:
    c, _ = auth_client(role)
    assert c.get("/admin/facilities").status_code == 403


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_facility_detail_requires_admin(role: str) -> None:
    admin, _ = auth_client("ADMIN")
    _hospital(name=f"Detail-{role}")
    fid = _facility_id(admin, f"Detail-{role}")
    c, _ = auth_client(role)
    assert c.get(f"/admin/facilities/{fid}").status_code == 403


def test_facility_list_reflects_real_rows_and_inventory_totals() -> None:
    admin, _ = auth_client("ADMIN")
    h = _hospital(city="Riverside", name="Riverside General")
    h.put(
        "/hospitals/me/inventory",
        json={"items": [{"blood_type": "O_NEG", "units": 5}, {"blood_type": "A_POS", "units": 3}]},
    )

    r = admin.get("/admin/facilities", params={"query": "Riverside"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    facility = body["items"][0]
    assert facility["name"] == "Riverside General"
    assert facility["city"] == "Riverside"
    assert facility["facility_type"] == "HOSPITAL"
    assert facility["verified"] is False
    assert facility["total_units"] == 8
    assert facility["total_reserved_units"] == 0


def test_facility_filter_by_verified_and_type() -> None:
    admin, _ = auth_client("ADMIN")
    _hospital(name="Unverified House")

    unverified_only = admin.get("/admin/facilities", params={"verified": False}).json()
    assert any(f["name"] == "Unverified House" for f in unverified_only["items"])

    verified_only = admin.get("/admin/facilities", params={"verified": True}).json()
    assert all(f["verified"] is True for f in verified_only["items"])

    blood_banks = admin.get("/admin/facilities", params={"facility_type": "BLOOD_BANK"}).json()
    assert all(f["facility_type"] == "BLOOD_BANK" for f in blood_banks["items"])


def test_facility_detail_returns_per_blood_type_inventory() -> None:
    admin, _ = auth_client("ADMIN")
    h = _hospital(name="Detail Test Hospital")
    h.put("/hospitals/me/inventory", json={"items": [{"blood_type": "AB_POS", "units": 4}]})
    h.post("/hospitals/me/inventory/reserve", json={"blood_type": "AB_POS", "units": 1})
    fid = _facility_id(admin, "Detail Test Hospital")

    r = admin.get(f"/admin/facilities/{fid}")
    assert r.status_code == 200
    body = r.json()
    assert body["facility"]["name"] == "Detail Test Hospital"
    line = next(x for x in body["inventory"] if x["blood_type"] == "AB_POS")
    assert line["units"] == 4
    assert line["reserved_units"] == 1
    assert line["available_units"] == 3


def test_facility_detail_404_for_unknown_id() -> None:
    admin, _ = auth_client("ADMIN")
    import uuid

    assert admin.get(f"/admin/facilities/{uuid.uuid4()}").status_code == 404


# --- Donor governance --------------------------------------------------------- #


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_donors_list_requires_admin(role: str) -> None:
    c, _ = auth_client(role)
    assert c.get("/admin/donors").status_code == 403


def test_donor_governance_excludes_medical_data() -> None:
    admin, _ = auth_client("ADMIN")
    donor, _ = auth_client("DONOR", email="gotham-donor@example.com", name="Gotham Donor")
    donor.put(
        "/donors/me",
        json={
            "blood_type": "B_POS",
            "city": "Gotham",
            "weight_kg": 70,
            "medical_conditions": "asthma",
        },
    )

    r = admin.get("/admin/donors", params={"query": "Gotham Donor"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    row = next(d for d in body["items"] if d["city"] == "Gotham")
    assert row["blood_type"] == "B_POS"
    assert row["identity_verified"] is False
    # The point of this test: neither field is ever sent to an admin caller.
    assert "medical_conditions" not in row
    assert "weight_kg" not in row
    assert "latitude" not in row
    assert "longitude" not in row


def test_donor_filters_by_blood_type_and_availability() -> None:
    admin, _ = auth_client("ADMIN")
    _donor(blood_type="AB_NEG", city="FilterCity")

    r = admin.get("/admin/donors", params={"blood_type": "AB_NEG"})
    assert r.status_code == 200
    assert all(d["blood_type"] == "AB_NEG" for d in r.json()["items"])

    r2 = admin.get("/admin/donors", params={"is_available": True})
    assert r2.status_code == 200
    assert all(d["is_available"] is True for d in r2.json()["items"])

    r3 = admin.get("/admin/donors", params={"identity_verified": True})
    assert r3.status_code == 200
    assert all(d["identity_verified"] is True for d in r3.json()["items"])


# --- Certificate registry ------------------------------------------------------ #


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_certificates_registry_requires_admin(role: str) -> None:
    c, _ = auth_client(role)
    assert c.get("/admin/certificates").status_code == 403


def test_certificate_registry_search_across_donors() -> None:
    admin, _ = auth_client("ADMIN")
    donor, _ = auth_client("DONOR", email="cert-registry@example.com", name="Cert Registry Donor")
    donor.put("/donors/me", json={"blood_type": "O_POS"})
    did = donor.post(
        "/donors/me/donations",
        json={
            "donated_on": date.today().isoformat(),
            "units": 2,
            "hospital_name": "Registry Hospital",
        },
    ).json()["id"]
    # Phase E: certificate issuance now requires verification_status ==
    # VERIFIED -- this donation has no hospital_id, so only an admin can
    # review it (see tests/test_certificates.py's _donor_with_donation
    # for the same note). `admin` is already in scope here.
    v = admin.post(f"/donations/{did}/verify", json={})
    assert v.status_code == 200, v.text
    cert = donor.post("/certificates", json={"donation_id": did}).json()

    by_serial = admin.get("/admin/certificates", params={"query": cert["serial"]}).json()
    assert any(c["id"] == cert["id"] for c in by_serial["items"])

    by_donor_name = admin.get("/admin/certificates", params={"query": "Cert Registry Donor"}).json()
    assert any(c["id"] == cert["id"] for c in by_donor_name["items"])

    by_hospital = admin.get("/admin/certificates", params={"query": "Registry Hospital"}).json()
    assert any(c["id"] == cert["id"] for c in by_hospital["items"])

    no_match = admin.get("/admin/certificates", params={"query": "zzz-no-such-thing-zzz"}).json()
    assert no_match["items"] == []


# --- Analytics ------------------------------------------------------------------ #


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_analytics_overview_requires_admin(role: str) -> None:
    c, _ = auth_client(role)
    assert c.get("/admin/analytics/overview").status_code == 403


def test_analytics_overview_reflects_real_counts() -> None:
    admin, _ = auth_client("ADMIN")
    before = admin.get("/admin/analytics/overview").json()
    before_pending = before["request_status_breakdown"].get("PENDING", 0)

    patient, _ = auth_client("PATIENT")
    patient.post("/blood-requests", json={"blood_type": "O_NEG", "city": "Metro"})

    after = admin.get("/admin/analytics/overview").json()
    assert after["request_status_breakdown"]["PENDING"] == before_pending + 1
    assert "O_NEG" in after["request_blood_type_breakdown"]
    assert isinstance(after["requests_per_day"], list)
    # Today's bucket should include the request just filed.
    today = date.today().isoformat()
    assert any(d["day"] == today and d["count"] >= 1 for d in after["requests_per_day"])


def test_analytics_overview_days_window_is_respected() -> None:
    admin, _ = auth_client("ADMIN")
    r = admin.get("/admin/analytics/overview", params={"days": 1})
    assert r.status_code == 200
    for d in r.json()["requests_per_day"]:
        assert date.fromisoformat(d["day"]) >= date.today() - timedelta(days=1)


# --- Audit feed: severity classification + new filters -------------------------- #


def test_severity_classification_is_deterministic() -> None:
    assert _classify_severity("admin.graph_resync_failed") == "CRITICAL"
    assert _classify_severity("admin.user_suspended") == "WARNING"
    assert _classify_severity("admin.verification_rejected") == "WARNING"
    assert _classify_severity("admin.transport_route_disrupted") == "WARNING"
    assert _classify_severity("admin.user_role_changed") == "WARNING"
    assert _classify_severity("admin.user_reactivated") == "INFO"
    assert _classify_severity("verification.submitted") == "INFO"
    assert _classify_severity("admin.graph_resync_succeeded") == "INFO"


def test_audit_log_severity_field_and_filter() -> None:
    admin, _ = auth_client("ADMIN")
    target, _ = auth_client("PATIENT")
    target_id = target.get("/auth/me").json()["id"]
    admin.post(f"/admin/users/{target_id}/suspend", json={"reason": "severity test"})

    r = admin.get("/admin/audit-log", params={"action": "admin.user_suspended"})
    assert r.status_code == 200
    entries = r.json()["items"]
    assert entries and all(e["severity"] == "WARNING" for e in entries)
    assert entries[0]["actor_email"] is not None
    assert entries[0]["actor_role"] == "ADMIN"

    warnings_only = admin.get("/admin/audit-log", params={"severity": "WARNING"}).json()
    assert all(e["severity"] == "WARNING" for e in warnings_only["items"])
    info_only = admin.get("/admin/audit-log", params={"severity": "INFO"}).json()
    assert all(e["severity"] == "INFO" for e in info_only["items"])


def test_audit_log_filters_by_target_type_actor_role_and_date_range() -> None:
    admin, _ = auth_client("ADMIN")
    target, _ = auth_client("DONOR")
    target_id = target.get("/auth/me").json()["id"]
    admin.patch(f"/admin/users/{target_id}/role", json={"role": "PATIENT"})

    by_target = admin.get("/admin/audit-log", params={"target_type": "user"}).json()
    assert all(e["target_type"] == "user" for e in by_target["items"])

    by_role = admin.get("/admin/audit-log", params={"actor_role": "ADMIN"}).json()
    assert all(e["actor_role"] == "ADMIN" for e in by_role["items"])

    future_since = (date.today() + timedelta(days=1)).isoformat()
    nothing_yet = admin.get("/admin/audit-log", params={"since": future_since}).json()
    assert nothing_yet["items"] == []

    past_since = (date.today() - timedelta(days=1)).isoformat()
    everything = admin.get("/admin/audit-log", params={"since": past_since}).json()
    assert len(everything["items"]) >= 1
