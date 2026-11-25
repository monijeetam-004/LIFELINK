"""Phase 19 -- admin free-text search added to /admin/blood-requests,
/admin/emergencies, and /admin/verifications, matching the ilike-search
convention already established by /admin/users and /admin/certificates
(see LIFELINK_PHASE_19_AUDIT.md section on search). These three endpoints
already had a real, bounded `limit` and status filter before this phase;
what was missing was a text search, which an admin moderating a queue
needs far more than the underlying UUID. Real HTTP calls against the real
Postgres test database throughout.
"""
from __future__ import annotations

from tests.helpers import auth_client


def test_admin_blood_requests_search_matches_patient_name() -> None:
    admin, _ = auth_client("ADMIN")
    patient, _ = auth_client("PATIENT", name="Searchable Quincy")
    other, _ = auth_client("PATIENT", name="Someone Else")
    patient.post("/blood-requests", json={"blood_type": "O_NEG", "city": "Metro"})
    other.post("/blood-requests", json={"blood_type": "A_POS", "city": "Metro"})

    r = admin.get("/admin/blood-requests", params={"query": "Quincy"})
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["patient_user_id"]


def test_admin_blood_requests_search_matches_city() -> None:
    admin, _ = auth_client("ADMIN")
    patient, _ = auth_client("PATIENT")
    patient.post("/blood-requests", json={"blood_type": "O_NEG", "city": "Distinctocity"})

    r = admin.get("/admin/blood-requests", params={"query": "Distinctocity"})
    assert r.status_code == 200
    assert any(item["city"] == "Distinctocity" for item in r.json())


def test_admin_blood_requests_search_with_no_match_returns_empty() -> None:
    admin, _ = auth_client("ADMIN")
    patient, _ = auth_client("PATIENT")
    patient.post("/blood-requests", json={"blood_type": "O_NEG"})

    r = admin.get("/admin/blood-requests", params={"query": "no-such-substring-anywhere-xyz"})
    assert r.status_code == 200
    assert r.json() == []


def test_admin_emergencies_search_matches_creator_email() -> None:
    admin, _ = auth_client("ADMIN")
    patient, email = auth_client("PATIENT", email="findme-emergency@example.com")
    patient.post("/emergencies", json={"blood_type": "O_NEG"})

    r = admin.get("/admin/emergencies", params={"query": "findme-emergency"})
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_admin_verifications_search_matches_submitter_email() -> None:
    admin, _ = auth_client("ADMIN")
    donor, email = auth_client("DONOR", email="findme-verify@example.com")
    donor.post(
        "/verifications",
        data={"kind": "DONOR_IDENTITY"},
        files={"files": ("id.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )

    r = admin.get("/admin/verifications", params={"query": "findme-verify"})
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["user_id"]


def test_search_query_param_is_length_bounded() -> None:
    admin, _ = auth_client("ADMIN")
    r = admin.get("/admin/blood-requests", params={"query": "x" * 201})
    assert r.status_code == 422


def test_search_and_status_filter_combine() -> None:
    admin, _ = auth_client("ADMIN")
    patient, _ = auth_client("PATIENT", name="Combo Filter Test")
    r = patient.post("/blood-requests", json={"blood_type": "O_NEG"})
    rid = r.json()["id"]
    patient.post(f"/blood-requests/{rid}/cancel", json={})

    still_pending = admin.get(
        "/admin/blood-requests", params={"query": "Combo Filter Test", "status": "PENDING"}
    )
    assert still_pending.json() == []

    cancelled = admin.get(
        "/admin/blood-requests", params={"query": "Combo Filter Test", "status": "CANCELLED"}
    )
    assert len(cancelled.json()) == 1


def test_non_admin_cannot_use_admin_search_endpoints() -> None:
    donor, _ = auth_client("DONOR")
    assert donor.get("/admin/blood-requests", params={"query": "anything"}).status_code == 403
    assert donor.get("/admin/emergencies", params={"query": "anything"}).status_code == 403
    assert donor.get("/admin/verifications", params={"query": "anything"}).status_code == 403
