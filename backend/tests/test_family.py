from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import patch

from app.core.time import utcnow
from app.db.session import SessionLocal
from app.models.enums import FamilyLinkStatus
from app.models.family import FamilyLink
from app.models.user import User, UserRole
from tests.helpers import auth_client


def _link(family, patient_email):
    return family.post(
        "/family/links", json={"patient_email": patient_email, "relationship": "daughter"}
    )


def test_family_link_request_accept_and_manage() -> None:
    family, fam_email = auth_client("FAMILY")
    patient, pat_email = auth_client("PATIENT")

    r = _link(family, pat_email)
    assert r.status_code == 201, r.text
    link_id = r.json()["id"]
    assert r.json()["status"] == "PENDING"

    # patient sees a notification + the pending link
    notes = patient.get("/notifications").json()
    assert any(n["type"] == "family.link_requested" for n in notes)
    assert patient.get("/family/links").json()[0]["status"] == "PENDING"

    # family cannot yet act for the patient
    assert family.get("/patients/managed").json() == []
    assert (
        family.post(
            "/blood-requests",
            json={"blood_type": "A_POS", "patient_email": pat_email},
        ).status_code
        == 403
    )

    # patient accepts
    r = patient.post(f"/family/links/{link_id}/accept", json={})
    assert r.status_code == 200
    assert r.json()["status"] == "ACCEPTED"

    managed = family.get("/patients/managed").json()
    assert len(managed) == 1 and managed[0]["email"] == pat_email

    # now family can file for the patient
    r = family.post(
        "/blood-requests",
        json={"blood_type": "A_POS", "urgency": "URGENT", "patient_email": pat_email},
    )
    assert r.status_code == 201
    assert r.json()["patient_user_id"] != r.json()["requester_user_id"]

    # patient sees it in their own list
    assert len(patient.get("/blood-requests").json()) == 1


def test_family_link_revoke_blocks_access() -> None:
    family, _ = auth_client("FAMILY")
    patient, pat_email = auth_client("PATIENT")
    link_id = _link(family, pat_email).json()["id"]
    patient.post(f"/family/links/{link_id}/accept", json={})

    r = patient.post(f"/family/links/{link_id}/revoke", json={})
    assert r.status_code == 200 and r.json()["status"] == "REVOKED"
    assert family.get("/patients/managed").json() == []
    assert (
        family.post(
            "/blood-requests", json={"blood_type": "A_POS", "patient_email": pat_email}
        ).status_code
        == 403
    )


def test_link_to_unknown_patient_404() -> None:
    family, _ = auth_client("FAMILY")
    assert _link(family, "nobody@example.com").status_code == 404


def test_only_family_role_can_request_link() -> None:
    donor, _ = auth_client("DONOR")
    _, pat_email = auth_client("PATIENT")
    assert _link(donor, pat_email).status_code == 403


def test_duplicate_link_rejection() -> None:
    family, _ = auth_client("FAMILY")
    _, pat_email = auth_client("PATIENT")
    assert _link(family, pat_email).status_code == 201
    # A second request while the first is still PENDING is a real duplicate.
    r = _link(family, pat_email)
    assert r.status_code == 409, r.text


def test_self_link_rejection() -> None:
    """`request_link` guards `patient.id == user.id` (app/api/routes/family.py).

    That branch can never actually fire through the HTTP API: a single user
    account has exactly one role, so the same id can't simultaneously pass
    `require_roles(FAMILY)` on the request and resolve as a PATIENT-role
    account by email. Rather than skip the guard untested, this calls the
    route function directly with the email lookup patched to return the
    caller's own row -- the one way this situation could occur if that
    single-role invariant were ever violated elsewhere.
    """
    from types import SimpleNamespace

    from fastapi import HTTPException
    from sqlalchemy import select

    from app.api.routes.family import request_link
    from app.schemas.family import FamilyLinkCreate

    family, fam_email = auth_client("FAMILY")
    db = SessionLocal()
    try:
        caller = db.scalar(select(User).where(User.email == fam_email.lower()))
        assert caller is not None and caller.role == UserRole.FAMILY
        # A stand-in for what get_user_by_email would need to return for the
        # id-equality branch to matter: the *same id* as the caller, but
        # resolved as a PATIENT-role row (the caller itself stays FAMILY, as
        # `require_roles(FAMILY)` already enforced on the real request).
        # is_active=True: a real User row always has this attribute (Phase 13's
        # request_link added an is_active check ahead of the self-link check
        # this test targets), so the stand-in needs it too to reach that branch.
        same_id_as_patient = SimpleNamespace(id=caller.id, role=UserRole.PATIENT, is_active=True)
        with patch("app.api.routes.family.get_user_by_email", return_value=same_id_as_patient):
            try:
                request_link(
                    FamilyLinkCreate(patient_email=fam_email, relationship="self"),
                    request_=None,
                    user=caller,
                    db=db,
                )
                raise AssertionError("expected HTTPException for self-link")
            except HTTPException as exc:
                assert exc.status_code == 400
    finally:
        db.close()


def test_unauthorized_patient_access_returns_404() -> None:
    family, _ = auth_client("FAMILY")  # no link to anyone
    patient, pat_email = auth_client("PATIENT")
    req = patient.post("/blood-requests", json={"blood_type": "A_POS"})
    assert req.status_code == 201
    request_id = req.json()["id"]

    # An unlinked family member can't see the patient's request by id.
    assert family.get(f"/blood-requests/{request_id}").status_code == 404

    em = patient.post("/emergencies", json={"blood_type": "O_NEG"})
    assert em.status_code == 201
    assert family.get(f"/emergencies/{em.json()['id']}").status_code == 404


def test_reject_pending_link() -> None:
    family, fam_email = auth_client("FAMILY")
    patient, pat_email = auth_client("PATIENT")
    link_id = _link(family, pat_email).json()["id"]

    # Only the patient may reject, and only while PENDING.
    assert family.post(f"/family/links/{link_id}/reject", json={}).status_code == 403

    r = patient.post(f"/family/links/{link_id}/reject", json={})
    assert r.status_code == 200 and r.json()["status"] == "REJECTED"

    # A REJECTED link can't be re-rejected or accepted.
    assert patient.post(f"/family/links/{link_id}/reject", json={}).status_code == 409
    assert patient.post(f"/family/links/{link_id}/accept", json={}).status_code == 409

    # Family still has no access.
    assert family.get("/patients/managed").json() == []

    # The family member sees the decline notification.
    notes = family.get("/notifications").json()
    assert any(n["type"] == "family.link_rejected" for n in notes)

    # Re-requesting after a rejection reopens the same relationship as PENDING.
    r2 = _link(family, pat_email)
    assert r2.status_code == 201
    assert r2.json()["id"] == link_id
    assert r2.json()["status"] == "PENDING"


def test_revoke_rejects_already_terminal_link() -> None:
    family, _ = auth_client("FAMILY")
    patient, pat_email = auth_client("PATIENT")
    link_id = _link(family, pat_email).json()["id"]
    patient.post(f"/family/links/{link_id}/reject", json={})

    # Revoking a REJECTED link is a no-op that should fail loudly, not silently.
    assert family.post(f"/family/links/{link_id}/revoke", json={}).status_code == 409


def test_expired_link() -> None:
    family, fam_email = auth_client("FAMILY")
    patient, pat_email = auth_client("PATIENT")
    link_id = _link(family, pat_email).json()["id"]

    # Force the request into the past, as if it had never been actioned.
    db = SessionLocal()
    try:
        link = db.get(FamilyLink, uuid.UUID(link_id))
        assert link is not None
        link.expires_at = utcnow() - timedelta(days=1)
        db.commit()
    finally:
        db.close()

    # Lazily expired on the next read.
    links = patient.get("/family/links").json()
    assert any(
        link_item["id"] == link_id and link_item["status"] == "EXPIRED" for link_item in links
    )

    # An expired request can no longer be accepted or rejected.
    assert patient.post(f"/family/links/{link_id}/accept", json={}).status_code == 409
    assert patient.post(f"/family/links/{link_id}/reject", json={}).status_code == 409

    # Re-requesting is allowed, same as after a rejection/revocation.
    assert _link(family, pat_email).status_code == 201


def test_family_member_suspended_loses_access() -> None:
    admin, _ = auth_client("ADMIN")
    family, fam_email = auth_client("FAMILY")
    patient, pat_email = auth_client("PATIENT")
    link_id = _link(family, pat_email).json()["id"]
    patient.post(f"/family/links/{link_id}/accept", json={})
    assert family.get("/patients/managed").json() != []

    fam_id = family.get("/auth/me").json()["id"]
    r = admin.post(f"/admin/users/{fam_id}/suspend", json={"reason": "review"})
    assert r.status_code == 200

    # The suspended family member is rejected on their very next request --
    # the link itself is untouched (preserved for history/audit) but they
    # can no longer use it.
    assert family.get("/patients/managed").status_code == 401
    assert family.get("/family/links").status_code == 401


def test_patient_deactivation_blocks_family_action() -> None:
    admin, _ = auth_client("ADMIN")
    family, fam_email = auth_client("FAMILY")
    patient, pat_email = auth_client("PATIENT")
    link_id = _link(family, pat_email).json()["id"]
    patient.post(f"/family/links/{link_id}/accept", json={})
    assert len(family.get("/patients/managed").json()) == 1

    pat_id = patient.get("/auth/me").json()["id"]
    r = admin.post(f"/admin/users/{pat_id}/suspend", json={"reason": "review"})
    assert r.status_code == 200

    # The FamilyLink row itself isn't deleted or changed...
    db = SessionLocal()
    try:
        link = db.get(FamilyLink, uuid.UUID(link_id))
        assert link is not None and link.status == FamilyLinkStatus.ACCEPTED
    finally:
        db.close()

    # ...but a deactivated patient is no longer manageable by their family:
    # they drop out of "managed", and filing on their behalf is blocked.
    assert family.get("/patients/managed").json() == []
    r2 = family.post("/blood-requests", json={"blood_type": "A_POS", "patient_email": pat_email})
    assert r2.status_code == 403


def test_family_sees_patients_own_filed_requests_and_emergencies() -> None:
    """The family dashboard needs the patient's real status, including
    things the patient filed themselves -- not only requests/emergencies
    the family member personally created."""
    family, fam_email = auth_client("FAMILY")
    patient, pat_email = auth_client("PATIENT")
    link_id = _link(family, pat_email).json()["id"]
    patient.post(f"/family/links/{link_id}/accept", json={})

    req = patient.post("/blood-requests", json={"blood_type": "B_POS"})
    assert req.status_code == 201
    em = patient.post("/emergencies", json={"blood_type": "B_POS"})
    assert em.status_code == 201

    fam_requests = family.get("/blood-requests").json()
    assert any(r["id"] == req.json()["id"] for r in fam_requests)
    assert next(r for r in fam_requests if r["id"] == req.json()["id"])["filed_by_family"] is False

    fam_emergencies = family.get("/emergencies").json()
    assert any(e["id"] == em.json()["id"] for e in fam_emergencies)

    # And a family member can view and manage (e.g. resolve) an emergency
    # the patient raised themselves, since they're an accepted family link.
    resolve = family.post(f"/emergencies/{em.json()['id']}/resolve", json={})
    assert resolve.status_code == 200, resolve.text
    assert resolve.json()["status"] == "RESOLVED"


def test_family_filed_request_labelled_and_visible_to_patient() -> None:
    family, fam_email = auth_client("FAMILY")
    patient, pat_email = auth_client("PATIENT")
    link_id = _link(family, pat_email).json()["id"]
    patient.post(f"/family/links/{link_id}/accept", json={})

    r = family.post("/blood-requests", json={"blood_type": "A_POS", "patient_email": pat_email})
    assert r.status_code == 201
    assert r.json()["filed_by_family"] is True
    assert r.json()["requester_name"]

    seen = patient.get(f"/blood-requests/{r.json()['id']}").json()
    assert seen["filed_by_family"] is True


def test_audit_trail_covers_link_lifecycle() -> None:
    admin, _ = auth_client("ADMIN")
    family, fam_email = auth_client("FAMILY")
    patient, pat_email = auth_client("PATIENT")
    fam_id = family.get("/auth/me").json()["id"]

    link_id = _link(family, pat_email).json()["id"]
    patient.post(f"/family/links/{link_id}/accept", json={})
    patient.post(f"/family/links/{link_id}/revoke", json={})

    def _has(action: str) -> bool:
        log = admin.get(
            "/admin/audit-log", params={"action": action, "actor_user_id": fam_id}
        ).json()
        if any(e["target_id"] == link_id for e in log["items"]):
            return True
        # accept/revoke are actor'd by the patient, not the family member.
        log2 = admin.get("/admin/audit-log", params={"action": action}).json()
        return any(e["target_id"] == link_id for e in log2["items"])

    assert _has("family.link_requested")
    assert _has("family.link_accepted")
    assert _has("family.link_revoked")
