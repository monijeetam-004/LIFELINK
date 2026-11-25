"""QA round: success paths for the API operations that the full-suite route
log showed were only ever exercised negatively (never a 2xx).
"""

from __future__ import annotations

from datetime import date

from tests.helpers import auth_client


def test_linked_hospital_reads_a_donation_and_others_cannot() -> None:
    hosp, _ = auth_client("HOSPITAL")
    r = hosp.put("/hospitals/me", json={"name": "QA Gap Hospital", "city": "Metro"})
    assert r.status_code == 200, r.text
    hospital_id = hosp.get("/hospitals/me").json()["id"]

    donor, _ = auth_client("DONOR")
    donor.put("/donors/me", json={"blood_type": "O_NEG"})
    r = donor.post("/donors/me/donations", json={
        "donated_on": date.today().isoformat(), "units": 1, "hospital_id": hospital_id,
    })
    assert r.status_code == 201, r.text
    donation_id = r.json()["id"]

    r = hosp.get(f"/donations/{donation_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == donation_id
    assert body["verification_status"] == "DONOR_REPORTED"

    admin, _ = auth_client("ADMIN")
    assert admin.get(f"/donations/{donation_id}").status_code == 200

    other, _ = auth_client("HOSPITAL")
    other.put("/hospitals/me", json={"name": "QA Other Hospital", "city": "Elsewhere"})
    assert other.get(f"/donations/{donation_id}").status_code in (403, 404)
    assert donor.get(f"/donations/{donation_id}").status_code == 403


def test_admin_lists_transport_routes() -> None:
    admin, _ = auth_client("ADMIN")
    r = admin.get("/admin/transport-routes")
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)
    hosp, _ = auth_client("HOSPITAL")
    assert hosp.get("/admin/transport-routes").status_code == 403
