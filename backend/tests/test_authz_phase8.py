"""Phase 8 -- RBAC and object-level authorization.

Exercises the spec's explicit attack matrix (GET /requests/123 vs 124-style
substitution across every ownership-scoped resource; patient->patient,
donor->donor, family->unrelated patient, hospital A->hospital B, admin-only
endpoint->every non-admin role) and confirms each denial is also recorded to
the audit log, per the spec's AUDIT requirement.

Coverage that already existed before this phase and is not duplicated here:
  - patient->patient on blood requests: tests/test_blood_requests.py
  - patient->patient on emergencies: tests/test_emergencies_and_notifications.py
  - donor->donor on certificates: tests/test_certificates.py
  - family->unrelated patient: tests/test_family.py
  - admin-only endpoints, every non-admin role, on every admin route:
    tests/test_admin.py, tests/test_admin_phase6.py
This file adds: the previously-untested hospital-A-vs-hospital-B case (a
genuine enforcement gap in test coverage, though the check itself already
existed), donor->donor on appointments, and an audit-record assertion
layered on top of several of the above so the AUDIT requirement is verified
directly rather than assumed from the access-denial behavior alone.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select

from app.models.audit_log import AuditLog
from tests.helpers import auth_client


def _latest_audit(db_session, action: str, target_id=None):
    stmt = select(AuditLog).where(AuditLog.action == action)
    if target_id is not None:
        stmt = stmt.where(AuditLog.target_id == target_id)
    stmt = stmt.order_by(AuditLog.created_at.desc())
    return db_session.scalars(stmt).first()


# --------------------------------------------------------------------------- #
# Admin-only endpoint -> every non-admin role, with audit verification
# --------------------------------------------------------------------------- #
def test_admin_endpoint_denial_is_audited(db_session) -> None:
    donor, _ = auth_client("DONOR")
    resp = donor.get("/admin/users")
    assert resp.status_code == 403

    entry = _latest_audit(db_session, "authz.role_denied")
    assert entry is not None
    assert entry.meta["path"] == "/admin/users"
    assert entry.meta["actor_role"] == "DONOR"
    assert "ADMIN" in entry.meta["required_roles"]


def test_role_denial_audited_for_every_non_admin_role(db_session) -> None:
    for role in ("PATIENT", "FAMILY", "DONOR", "HOSPITAL"):
        c, _ = auth_client(role)
        assert c.get("/admin/audit-log").status_code == 403
        entry = _latest_audit(db_session, "authz.role_denied")
        assert entry is not None
        assert entry.meta["actor_role"] == role
        assert entry.meta["path"] == "/admin/audit-log"


# --------------------------------------------------------------------------- #
# GET /blood-requests/123 vs /124 -- patient -> patient, with audit
# --------------------------------------------------------------------------- #
def test_patient_cannot_view_another_patients_request_and_is_audited(db_session) -> None:
    owner, _ = auth_client("PATIENT")
    other, _ = auth_client("PATIENT")
    rid = owner.post("/blood-requests", json={"blood_type": "A_POS"}).json()["id"]

    assert other.get(f"/blood-requests/{rid}").status_code == 404
    assert other.post(f"/blood-requests/{rid}/cancel", json={}).status_code == 404
    assert other.post(f"/blood-requests/{rid}/fulfill", json={}).status_code == 404

    entry = _latest_audit(db_session, "blood_request.access_denied", target_id=uuid.UUID(rid))
    assert entry is not None
    assert entry.meta["attempted_action"] == "fulfill"  # last of the three calls above

    # A genuinely bad id is never audited -- only a real cross-tenant probe is.
    before = db_session.scalars(select(AuditLog).where(
        AuditLog.action == "blood_request.access_denied"
    )).all()
    assert other.get(f"/blood-requests/{uuid.uuid4()}").status_code == 404
    after = db_session.scalars(select(AuditLog).where(
        AuditLog.action == "blood_request.access_denied"
    )).all()
    assert len(after) == len(before)


# --------------------------------------------------------------------------- #
# GET /emergencies/123 vs /124 -- patient -> patient, family -> unrelated
# --------------------------------------------------------------------------- #
def test_patient_and_unrelated_family_cannot_manage_emergency_and_are_audited(db_session) -> None:
    owner, _ = auth_client("PATIENT")
    other_patient, _ = auth_client("PATIENT")
    unrelated_family, _ = auth_client("FAMILY")

    eid = owner.post("/emergencies", json={"blood_type": "O_NEG"}).json()["id"]

    assert other_patient.get(f"/emergencies/{eid}").status_code == 404
    assert other_patient.post(f"/emergencies/{eid}/cancel", json={}).status_code == 404
    assert unrelated_family.get(f"/emergencies/{eid}").status_code == 404
    assert unrelated_family.post(f"/emergencies/{eid}/resolve", json={}).status_code == 404

    entries = db_session.scalars(
        select(AuditLog).where(
            AuditLog.action == "emergency.access_denied",
            AuditLog.target_id == uuid.UUID(eid),
        )
    ).all()
    # One per denied call above (view + cancel for other_patient, view +
    # resolve for unrelated_family).
    assert len(entries) == 4
    actions_attempted = {e.meta["attempted_action"] for e in entries}
    assert actions_attempted == {"view", "cancel", "resolve"}


# --------------------------------------------------------------------------- #
# Donor -> donor: certificates (enforcement already tested in
# test_certificates.py) and appointments (new)
# --------------------------------------------------------------------------- #
def test_donor_cannot_cancel_another_donors_appointment_and_is_audited(db_session) -> None:
    owner, _ = auth_client("DONOR")
    other, _ = auth_client("DONOR")

    appt = owner.post(
        "/donors/me/appointments",
        json={"hospital_name": "Metro General", "preferred_date": date.today().isoformat()},
    )
    assert appt.status_code == 201, appt.text
    appt_id = appt.json()["id"]

    resp = other.post(f"/donors/me/appointments/{appt_id}/cancel")
    assert resp.status_code == 404

    entry = _latest_audit(
        db_session, "donor_appointment.access_denied", target_id=uuid.UUID(appt_id)
    )
    assert entry is not None
    assert entry.meta["attempted_action"] == "cancel"

    # The owner can still cancel their own appointment -- the check is real
    # ownership scoping, not a blanket lockout.
    assert owner.post(f"/donors/me/appointments/{appt_id}/cancel").status_code == 200


def test_certificate_cross_donor_denial_is_audited(db_session) -> None:
    owner, _ = auth_client("DONOR")
    other, _ = auth_client("DONOR")
    owner.put("/donors/me", json={"blood_type": "O_NEG"})
    donation = owner.post(
        "/donors/me/donations",
        json={"donated_on": date.today().isoformat(), "units": 1, "hospital_name": "Metro"},
    ).json()
    # Phase E: certificate issuance requires verification_status ==
    # VERIFIED -- this donation has no hospital_id, so only an admin can
    # review it (see tests/test_certificates.py's _donor_with_donation
    # for the same note).
    admin, _ = auth_client("ADMIN")
    assert admin.post(f"/donations/{donation['id']}/verify", json={}).status_code == 200
    cert = owner.post("/certificates", json={"donation_id": donation["id"]}).json()

    assert other.get(f"/certificates/{cert['id']}").status_code == 404

    entry = _latest_audit(db_session, "certificate.access_denied", target_id=uuid.UUID(cert["id"]))
    assert entry is not None
    assert entry.meta["attempted_action"] == "view"


# --------------------------------------------------------------------------- #
# Hospital A -> hospital B: resource-pathways is the one endpoint where a
# hospital passes another facility's id explicitly. Enforcement already
# existed (origin.user_id != user.id -> 403); this test is the previously
# missing coverage for it, plus the new audit record.
# --------------------------------------------------------------------------- #
def test_hospital_cannot_query_another_hospitals_resource_pathways(db_session) -> None:
    hospital_a, _ = auth_client("HOSPITAL")
    hospital_b, _ = auth_client("HOSPITAL")
    hospital_a.put("/hospitals/me", json={"name": "Facility A Pathways Test"})

    # /hospitals/me only ever returns the caller's own user_id, not the
    # HospitalProfile primary key the resource-pathways endpoint keys on --
    # look facility A's id up via the directory instead (same workaround
    # used in tests/test_admin_phase6.py for the same underlying reason).
    directory = hospital_b.get("/hospitals/directory").json()
    facility_a_id = next(
        h["id"] for h in directory if h["name"] == "Facility A Pathways Test"
    )

    patient, _ = auth_client("PATIENT")
    em = patient.post("/emergencies", json={"blood_type": "O_NEG"})
    assert em.status_code == 201
    eid = em.json()["id"]

    # Hospital B can view the (open) emergency -- open emergencies are
    # visible to every hospital by design -- but must not be able to query
    # resource pathways as though facility A were its own.
    resp = hospital_b.get(
        f"/emergencies/{eid}/resource-pathways",
        params={"origin_facility_id": facility_a_id},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Not your facility"

    entry = _latest_audit(db_session, "hospital.facility_access_denied")
    assert entry is not None
    assert entry.meta["attempted_action"] == "resource_pathways"

    # Hospital A querying its own facility for the same emergency succeeds
    # (or at least isn't blocked by ownership) -- confirms this is a real,
    # narrowly-scoped ownership check, not a blanket failure.
    own = hospital_a.get(
        f"/emergencies/{eid}/resource-pathways",
        params={"origin_facility_id": facility_a_id},
    )
    assert own.status_code == 200


# --------------------------------------------------------------------------- #
# Hospital A cannot act on a blood request outside its own
# city/name visibility scope (the other hospital-facing IDOR surface --
# HospitalRequestResponse rows and inventory are always "me"-scoped by
# construction so this is the one place a request id crosses hospitals).
# --------------------------------------------------------------------------- #
def test_hospital_cannot_act_on_request_outside_its_visibility_and_is_audited(db_session) -> None:
    hospital_a, _ = auth_client("HOSPITAL", name="Hospital A")
    hospital_a.put("/hospitals/me", json={"name": "Hospital A", "city": "Metropolis"})

    hospital_b, _ = auth_client("HOSPITAL", name="Hospital B")
    hospital_b.put("/hospitals/me", json={"name": "Hospital B", "city": "Gotham"})

    patient, _ = auth_client("PATIENT")
    rid = patient.post(
        "/blood-requests", json={"blood_type": "A_POS", "city": "Metropolis"}
    ).json()["id"]

    # Hospital B (a different city) cannot respond to, allocate against, or
    # view the event log for a request outside its visibility.
    assert hospital_b.post(
        f"/hospitals/me/requests/{rid}/respond", json={"status": "ACKNOWLEDGED"}
    ).status_code == 404
    assert hospital_b.post(
        f"/hospitals/me/requests/{rid}/allocate",
        json={"blood_type": "A_POS", "units": 1},
    ).status_code == 404
    assert hospital_b.get(f"/hospitals/me/requests/{rid}/events").status_code == 404

    entries = db_session.scalars(
        select(AuditLog).where(
            AuditLog.action == "hospital.request_access_denied",
            AuditLog.target_id == uuid.UUID(rid),
        )
    ).all()
    assert len(entries) == 3
    assert {e.meta["attempted_action"] for e in entries} == {"respond", "allocate", "view_events"}

    # Hospital A (the matching city) can act on it -- confirms this is a
    # genuine scoping check, not a broken endpoint.
    assert hospital_a.post(
        f"/hospitals/me/requests/{rid}/respond", json={"status": "ACKNOWLEDGED"}
    ).status_code == 200


# --------------------------------------------------------------------------- #
# Family link actions: a real link, wrong party -- audited unconditionally
# --------------------------------------------------------------------------- #
def test_family_link_action_by_wrong_party_is_audited(db_session) -> None:
    family, fam_email = auth_client("FAMILY")
    patient, pat_email = auth_client("PATIENT")
    outsider, _ = auth_client("PATIENT")

    link_id = family.post("/family/links", json={
        "patient_email": pat_email, "relationship": "Sibling",
    }).json()["id"]

    # Someone who is neither the family member nor the target patient tries
    # to accept/reject the pending request.
    assert outsider.post(f"/family/links/{link_id}/accept").status_code == 403
    assert outsider.post(f"/family/links/{link_id}/reject").status_code == 403

    entries = db_session.scalars(
        select(AuditLog).where(
            AuditLog.action == "family_link.access_denied",
            AuditLog.target_id == uuid.UUID(link_id),
        )
    ).all()
    assert len(entries) == 2
    assert {e.meta["attempted_action"] for e in entries} == {"accept", "reject"}

    # The actual patient can still accept it -- real scoping, not a lockout.
    assert patient.post(f"/family/links/{link_id}/accept").status_code == 200
