"""Phase 19 -- security audit for new/touched surfaces: hospital-vs-hospital
IDOR on blood-request visibility, admin analytics authorization (had no
test at all before this phase), and bounds/validation on the new search
and idempotency parameters. Everything else section 17 of the Phase 19
spec asks about (patient A vs patient B, donor vs patient-only data,
family-link overreach) was already covered by existing test files from
earlier phases -- re-verified passing, not re-tested here to avoid
duplicate coverage; see LIFELINK_PHASE_19_AUDIT.md for the full mapping
of what already existed vs what was genuinely new.

Real HTTP calls against the real Postgres test database throughout.
"""
from __future__ import annotations

from tests.helpers import auth_client


def _ready_hospital(city: str, name: str):
    c, _ = auth_client("HOSPITAL")
    c.put("/hospitals/me", json={"name": name, "city": city})
    return c


# --------------------------------------------------------------------------- #
# Hospital A vs hospital B -- a request only visible to A must 404 for B
# --------------------------------------------------------------------------- #
def test_hospital_cannot_see_another_hospitals_out_of_city_request_events() -> None:
    hosp_a = _ready_hospital("CityA", "Hospital A")
    hosp_b = _ready_hospital("CityB", "Hospital B")
    patient, _ = auth_client("PATIENT")
    rid = patient.post(
        "/blood-requests",
        json={"blood_type": "O_NEG", "city": "CityA", "hospital_name": "Hospital A"},
    ).json()["id"]

    # Hospital A (same city) can see it.
    assert hosp_a.get(f"/hospitals/me/requests/{rid}/events").status_code == 200
    # Hospital B (different city, different name) cannot.
    assert hosp_b.get(f"/hospitals/me/requests/{rid}/events").status_code == 404


def test_hospital_cannot_allocate_to_another_hospitals_request() -> None:
    _ready_hospital("CityA2", "Hospital Alpha")
    hosp_b = _ready_hospital("CityB2", "Hospital Bravo")
    patient, _ = auth_client("PATIENT")
    rid = patient.post(
        "/blood-requests",
        json={"blood_type": "O_NEG", "city": "CityA2", "hospital_name": "Hospital Alpha"},
    ).json()["id"]

    r = hosp_b.post(
        f"/hospitals/me/requests/{rid}/allocate", json={"blood_type": "O_NEG", "units": 1}
    )
    assert r.status_code == 404


def test_hospital_inventory_is_scoped_to_me_only_no_id_parameter_exists() -> None:
    """Documents, rather than merely asserting, that inventory has no
    cross-hospital read surface at all: every inventory route is
    /hospitals/me/inventory* (never /hospitals/{id}/inventory), so there is
    no endpoint shape through which hospital B could even address hospital
    A's inventory by id in the first place."""
    hosp_a = _ready_hospital("CityA3", "Hospital One")
    hosp_a.put("/hospitals/me/inventory", json=[{"blood_type": "O_NEG", "units": 42}])
    hosp_b = _ready_hospital("CityB3", "Hospital Two")

    inv_b = hosp_b.get("/hospitals/me/inventory").json()
    # Hospital B's own inventory was never touched by A's write.
    assert all(item["units"] == 0 for item in inv_b if item["blood_type"] == "O_NEG")


# --------------------------------------------------------------------------- #
# Admin analytics -- had zero authorization test coverage before this phase
# --------------------------------------------------------------------------- #
def test_analytics_overview_requires_admin() -> None:
    for role in ["PATIENT", "FAMILY", "DONOR", "HOSPITAL"]:
        c, _ = auth_client(role)
        assert c.get("/admin/analytics/overview").status_code == 403


def test_analytics_overview_is_real_and_bounded_for_admin() -> None:
    admin, _ = auth_client("ADMIN")
    r = admin.get("/admin/analytics/overview")
    assert r.status_code == 200
    body = r.json()
    assert "request_status_breakdown" in body
    assert "requests_per_day" in body

    # `days` is bounded (ge=1, le=90) -- not an unbounded/expensive query.
    assert admin.get("/admin/analytics/overview", params={"days": 0}).status_code == 422
    assert admin.get("/admin/analytics/overview", params={"days": 91}).status_code == 422
    assert admin.get("/admin/analytics/overview", params={"days": 90}).status_code == 200


# --------------------------------------------------------------------------- #
# Pagination bounds on the existing /admin/users search+pagination endpoint
# --------------------------------------------------------------------------- #
def test_admin_users_pagination_rejects_invalid_values() -> None:
    admin, _ = auth_client("ADMIN")
    assert admin.get("/admin/users", params={"page": 0}).status_code == 422
    assert admin.get("/admin/users", params={"page_size": 0}).status_code == 422
    assert admin.get("/admin/users", params={"page_size": 101}).status_code == 422
    assert admin.get("/admin/users", params={"page_size": 100}).status_code == 200


def test_admin_blood_requests_limit_is_bounded() -> None:
    admin, _ = auth_client("ADMIN")
    assert admin.get("/admin/blood-requests", params={"limit": 0}).status_code == 422
    assert admin.get("/admin/blood-requests", params={"limit": 201}).status_code == 422
    assert admin.get("/admin/blood-requests", params={"limit": 200}).status_code == 200


# --------------------------------------------------------------------------- #
# Malformed IDs -- a non-UUID path parameter is a clean 422, not a 500
# --------------------------------------------------------------------------- #
def test_malformed_request_id_is_a_clean_422_not_a_500() -> None:
    patient, _ = auth_client("PATIENT")
    r = patient.get("/blood-requests/not-a-real-uuid")
    assert r.status_code == 422


def test_malformed_request_id_on_cancel_is_a_clean_422() -> None:
    patient, _ = auth_client("PATIENT")
    r = patient.post("/blood-requests/not-a-real-uuid/cancel", json={})
    assert r.status_code == 422
