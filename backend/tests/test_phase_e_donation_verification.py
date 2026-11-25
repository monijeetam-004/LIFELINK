"""Phase E -- donation verification lifecycle, authorization, and the
certificate-issuance gate.

What this proves (all against the real HTTP API + real Postgres):
  * a newly logged donation is DONOR_REPORTED -- never VERIFIED by default
  * the donor can never verify/reject their own donation (role gate)
  * a hospital can review only a donation linked (by `hospital_id`) to its
    OWN facility; an unrelated hospital is refused (403)
  * a donation with no `hospital_id` (free text only) is admin-review-only
  * same-state verify/reject is a clean 409 (stale/duplicate action)
  * a mistaken decision can be corrected, each correction audited
  * certificate issuance is refused (409) until VERIFIED, then succeeds
  * an already-issued certificate keeps resolving after a later reject
    (rejection is not revocation -- see donation_review.reject_donation)
  * two reviewers racing to verify the same donation: exactly one 200,
    one 409 (row lock + same-state rejection, the codebase convention)
"""
from __future__ import annotations

import threading
import uuid
from datetime import date

from sqlalchemy import select

from app.models.audit_log import AuditLog
from tests.helpers import auth_client


def _hospital(name: str = "St Jude", city: str = "Metro"):
    c, _ = auth_client("HOSPITAL")
    r = c.put("/hospitals/me", json={"name": name, "city": city})
    assert r.status_code == 200, r.text
    return c, c.get("/hospitals/me").json()["id"]


def _donation(donor, **overrides) -> dict:
    payload = {"donated_on": date.today().isoformat(), "units": 1}
    payload.update(overrides)
    r = donor.post("/donors/me/donations", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _donor():
    c, _ = auth_client("DONOR")
    c.put("/donors/me", json={"blood_type": "O_NEG"})
    return c


def _audits(db_session, action: str, target_id: str) -> list[AuditLog]:
    return list(db_session.scalars(
        select(AuditLog).where(
            AuditLog.action == action, AuditLog.target_id == uuid.UUID(target_id)
        )
    ).all())


# --------------------------------------------------------------------------- #
# Default state + honest naming
# --------------------------------------------------------------------------- #
def test_new_donation_is_donor_reported_not_verified() -> None:
    donor = _donor()
    d = _donation(donor, hospital_name="Somewhere")
    assert d["verification_status"] == "DONOR_REPORTED"
    assert d["verified_by_user_id"] is None and d["verified_at"] is None


def test_client_cannot_self_declare_verified_on_create() -> None:
    donor = _donor()
    d = _donation(donor, verification_status="VERIFIED", verified_by_user_id=str(uuid.uuid4()))
    assert d["verification_status"] == "DONOR_REPORTED"
    assert d["verified_by_user_id"] is None


def test_hospital_id_links_to_real_facility_and_defaults_name() -> None:
    _, hid = _hospital(name="Linked General")
    donor = _donor()
    d = _donation(donor, hospital_id=hid)
    assert d["hospital_id"] == hid
    assert d["hospital_name"] == "Linked General"


def test_unknown_hospital_id_is_rejected() -> None:
    donor = _donor()
    r = donor.post("/donors/me/donations", json={
        "donated_on": date.today().isoformat(), "units": 1, "hospital_id": str(uuid.uuid4()),
    })
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# Authorization
# --------------------------------------------------------------------------- #
def test_donor_cannot_verify_or_reject_own_donation() -> None:
    _, hid = _hospital()
    donor = _donor()
    d = _donation(donor, hospital_id=hid)
    assert donor.post(f"/donations/{d['id']}/verify", json={}).status_code == 403
    assert donor.post(f"/donations/{d['id']}/reject", json={}).status_code == 403
    assert donor.get(f"/donations/{d['id']}").status_code == 403


def test_patient_and_family_cannot_review() -> None:
    donor = _donor()
    d = _donation(donor, hospital_name="X")
    for role in ("PATIENT", "FAMILY"):
        c, _ = auth_client(role)
        assert c.post(f"/donations/{d['id']}/verify", json={}).status_code == 403


def test_linked_hospital_can_verify() -> None:
    hosp, hid = _hospital()
    donor = _donor()
    d = _donation(donor, hospital_id=hid)
    r = hosp.post(f"/donations/{d['id']}/verify", json={"note": "Confirmed in register"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verification_status"] == "VERIFIED"
    assert body["verified_by_user_id"] is not None
    assert body["verified_at"] is not None
    assert body["verification_note"] == "Confirmed in register"


def test_unrelated_hospital_cannot_verify_another_facilitys_donation() -> None:
    _, hid_a = _hospital(name="A")
    hosp_b, _ = _hospital(name="B")
    donor = _donor()
    d = _donation(donor, hospital_id=hid_a)
    assert hosp_b.post(f"/donations/{d['id']}/verify", json={}).status_code == 403
    assert hosp_b.post(f"/donations/{d['id']}/reject", json={}).status_code == 403
    assert hosp_b.get(f"/donations/{d['id']}").status_code == 403


def test_unlinked_donation_is_admin_review_only() -> None:
    hosp, _ = _hospital(name="Metro General")
    donor = _donor()
    # Free-text name matches the hospital exactly -- still NOT enough to
    # grant review authority (no fuzzy/free-text attribution).
    d = _donation(donor, hospital_name="Metro General")
    assert hosp.post(f"/donations/{d['id']}/verify", json={}).status_code == 403
    admin, _ = auth_client("ADMIN")
    assert admin.post(f"/donations/{d['id']}/verify", json={}).status_code == 200


def test_nonexistent_donation_is_404() -> None:
    admin, _ = auth_client("ADMIN")
    assert admin.post(f"/donations/{uuid.uuid4()}/verify", json={}).status_code == 404


# --------------------------------------------------------------------------- #
# Transitions
# --------------------------------------------------------------------------- #
def test_double_verify_and_double_reject_are_409() -> None:
    admin, _ = auth_client("ADMIN")
    donor = _donor()
    d = _donation(donor, hospital_name="X")
    assert admin.post(f"/donations/{d['id']}/verify", json={}).status_code == 200
    assert admin.post(f"/donations/{d['id']}/verify", json={}).status_code == 409
    assert admin.post(f"/donations/{d['id']}/reject", json={}).status_code == 200
    assert admin.post(f"/donations/{d['id']}/reject", json={}).status_code == 409


def test_corrections_are_each_audited_and_notified(db_session) -> None:
    admin, _ = auth_client("ADMIN")
    donor = _donor()
    d = _donation(donor, hospital_name="X")
    admin.post(f"/donations/{d['id']}/reject", json={"note": "not in register"})
    admin.post(f"/donations/{d['id']}/verify", json={"note": "found late entry"})
    assert len(_audits(db_session, "donation.rejected", d["id"])) == 1
    assert len(_audits(db_session, "donation.verified", d["id"])) == 1
    types = [n["type"] for n in donor.get("/notifications").json()]
    assert "donation.rejected" in types and "donation.verified" in types


def test_donor_sees_real_status_in_own_history() -> None:
    admin, _ = auth_client("ADMIN")
    donor = _donor()
    d = _donation(donor, hospital_name="X")
    admin.post(f"/donations/{d['id']}/verify", json={})
    rows = donor.get("/donors/me/donations").json()
    row = next(r for r in rows if r["id"] == d["id"])
    assert row["verification_status"] == "VERIFIED"


def test_concurrent_verify_yields_exactly_one_success() -> None:
    donor = _donor()
    d = _donation(donor, hospital_name="X")
    admins = [auth_client("ADMIN")[0] for _ in range(2)]
    results: list[int] = []
    barrier = threading.Barrier(2)

    def go(c):
        barrier.wait()
        results.append(c.post(f"/donations/{d['id']}/verify", json={}).status_code)

    threads = [threading.Thread(target=go, args=(c,)) for c in admins]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [200, 409], results


# --------------------------------------------------------------------------- #
# Certificate gate
# --------------------------------------------------------------------------- #
def test_certificate_refused_until_verified_then_issued() -> None:
    admin, _ = auth_client("ADMIN")
    donor = _donor()
    d = _donation(donor, hospital_name="X")

    r = donor.post("/certificates", json={"donation_id": d["id"]})
    assert r.status_code == 409
    assert "verified" in r.json()["detail"].lower()
    assert donor.get("/certificates").json() == []

    admin.post(f"/donations/{d['id']}/verify", json={})
    assert donor.post("/certificates", json={"donation_id": d["id"]}).status_code == 201


def test_rejected_donation_cannot_get_a_certificate() -> None:
    admin, _ = auth_client("ADMIN")
    donor = _donor()
    d = _donation(donor, hospital_name="X")
    admin.post(f"/donations/{d['id']}/reject", json={})
    assert donor.post("/certificates", json={"donation_id": d["id"]}).status_code == 409


def test_existing_certificate_still_resolves_after_later_reject() -> None:
    """Rejection is not revocation: the already-issued certificate is
    re-fetched idempotently, and public verify still reports it valid
    until an admin explicitly revokes it."""
    admin, _ = auth_client("ADMIN")
    donor = _donor()
    d = _donation(donor, hospital_name="X")
    admin.post(f"/donations/{d['id']}/verify", json={})
    cert = donor.post("/certificates", json={"donation_id": d["id"]}).json()

    admin.post(f"/donations/{d['id']}/reject", json={})
    again = donor.post("/certificates", json={"donation_id": d["id"]})
    assert again.status_code == 201
    assert again.json()["id"] == cert["id"]


# --------------------------------------------------------------------------- #
# Review queue
# --------------------------------------------------------------------------- #
def test_hospital_queue_shows_only_its_own_linked_donations() -> None:
    hosp_a, hid_a = _hospital(name="A")
    hosp_b, hid_b = _hospital(name="B")
    donor = _donor()
    da = _donation(donor, hospital_id=hid_a)
    db_ = _donation(donor, hospital_id=hid_b)
    du = _donation(donor, hospital_name="free text only")

    ids_a = {d["id"] for d in hosp_a.get("/donations").json()}
    assert da["id"] in ids_a and db_["id"] not in ids_a and du["id"] not in ids_a

    admin, _ = auth_client("ADMIN")
    unlinked = {d["id"] for d in admin.get("/donations", params={"unlinked_only": True}).json()}
    assert du["id"] in unlinked and da["id"] not in unlinked

    hosp_a.post(f"/donations/{da['id']}/verify", json={})
    assert da["id"] not in {d["id"] for d in hosp_a.get("/donations").json()}
    verified = hosp_a.get("/donations", params={"verification_status": "VERIFIED"}).json()
    assert [d["id"] for d in verified] == [da["id"]]


def test_donor_cannot_list_review_queue() -> None:
    donor = _donor()
    assert donor.get("/donations").status_code == 403
