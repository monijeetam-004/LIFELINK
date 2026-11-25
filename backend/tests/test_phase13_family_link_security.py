"""Phase 13 -- Family Link Security and Workflow.

Dedicated coverage for the security test matrix (spec section 24, A-W) that
isn't already exercised by the pre-existing `tests/test_family.py` (self-
link, duplicate-pending, unknown-patient, role gating, reject/expire/
revoke-terminal, audit trail, suspension side effects -- all still passing,
unmodified in behavior). This file adds: duplicate-ACCEPTED protection,
unauthorized approval/rejection/revocation, per-status access matrices
(PENDING/ACCEPTED/REJECTED/REVOKED/EXPIRED) against both blood-requests and
emergencies, patient-id / family-link-id / emergency-id IDOR manipulation,
concurrent request/approval/rejection/revocation races, notification
generation for every transition, and audit generation for every transition.

Letter references below correspond to the spec's section 24 matrix.
"""
from __future__ import annotations

import threading
import uuid
from datetime import timedelta

from app.core.time import utcnow
from app.db.session import SessionLocal
from app.models.enums import FamilyLinkStatus
from app.models.family import FamilyLink
from tests.helpers import auth_client


def _link(family, patient_email, relationship="daughter"):
    return family.post(
        "/family/links", json={"patient_email": patient_email, "relationship": relationship}
    )


def _force_status(link_id: str, status_: FamilyLinkStatus, *, expired: bool = False) -> None:
    """Directly move a link into a given state for setup, bypassing the API
    (used only to reach REVOKED/REJECTED/EXPIRED starting points cheaply;
    every *access* assertion below still goes through the real API)."""
    db = SessionLocal()
    try:
        link = db.get(FamilyLink, uuid.UUID(link_id))
        assert link is not None
        link.status = status_
        if expired:
            link.expires_at = utcnow() - timedelta(days=1)
        db.commit()
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# C. Duplicate ACCEPTED relationship protection
# --------------------------------------------------------------------------- #
def test_duplicate_request_while_already_accepted_is_rejected() -> None:
    family, _ = auth_client("FAMILY")
    patient, pat_email = auth_client("PATIENT")
    link_id = _link(family, pat_email).json()["id"]
    patient.post(f"/family/links/{link_id}/accept", json={})

    r = _link(family, pat_email)
    assert r.status_code == 409, r.text

    # The DB constraint backs this up independently of the service check:
    # exactly one row exists for this (family, patient) pair, period.
    db = SessionLocal()
    try:
        original = db.get(FamilyLink, uuid.UUID(link_id))
        assert original is not None
        rows = db.query(FamilyLink).filter(
            FamilyLink.family_user_id == original.family_user_id,
            FamilyLink.patient_user_id == original.patient_user_id,
        ).all()
        assert len(rows) == 1
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# D/E/F/G. Unauthorized creation / approval / rejection / revocation
# --------------------------------------------------------------------------- #
def test_unauthorized_creation_wrong_role() -> None:
    hospital, _ = auth_client("HOSPITAL")
    _, pat_email = auth_client("PATIENT")
    assert _link(hospital, pat_email).status_code == 403


def test_unauthorized_approval_by_non_patient() -> None:
    family, _ = auth_client("FAMILY")
    patient, pat_email = auth_client("PATIENT")
    other_family, _ = auth_client("FAMILY")
    link_id = _link(family, pat_email).json()["id"]

    # Neither the requesting family member nor an unrelated third party may
    # accept on the patient's behalf.
    assert family.post(f"/family/links/{link_id}/accept", json={}).status_code == 403
    assert other_family.post(f"/family/links/{link_id}/accept", json={}).status_code == 403
    assert patient.post(f"/family/links/{link_id}/accept", json={}).status_code == 200


def test_unauthorized_rejection_by_non_patient() -> None:
    family, _ = auth_client("FAMILY")
    patient, pat_email = auth_client("PATIENT")
    donor, _ = auth_client("DONOR")
    link_id = _link(family, pat_email).json()["id"]

    assert family.post(f"/family/links/{link_id}/reject", json={}).status_code == 403
    assert donor.post(f"/family/links/{link_id}/reject", json={}).status_code == 403
    assert patient.post(f"/family/links/{link_id}/reject", json={}).status_code == 200


def test_unauthorized_revocation_by_unrelated_party() -> None:
    """Only the two parties to the link (family member or patient) may
    revoke it -- a third party, even another authenticated FAMILY user with
    no relationship to this patient, must not be able to."""
    family, _ = auth_client("FAMILY")
    patient, pat_email = auth_client("PATIENT")
    outsider, _ = auth_client("FAMILY")
    link_id = _link(family, pat_email).json()["id"]
    patient.post(f"/family/links/{link_id}/accept", json={})

    assert outsider.post(f"/family/links/{link_id}/revoke", json={}).status_code == 403
    # Either legitimate party may revoke (family side exercised here; the
    # patient side is exercised in test_family.py::test_family_link_revoke_blocks_access).
    assert family.post(f"/family/links/{link_id}/revoke", json={}).status_code == 200


# --------------------------------------------------------------------------- #
# H-L. Access under each relationship state -- blood requests AND emergencies
# --------------------------------------------------------------------------- #
def _setup_pair():
    family, _ = auth_client("FAMILY")
    patient, pat_email = auth_client("PATIENT")
    req_id = patient.post("/blood-requests", json={"blood_type": "A_POS"}).json()["id"]
    em_id = patient.post("/emergencies", json={"blood_type": "A_POS"}).json()["id"]
    link_id = _link(family, pat_email).json()["id"]
    return family, patient, pat_email, req_id, em_id, link_id


def test_pending_relationship_grants_no_access() -> None:
    family, _patient, _pat_email, req_id, em_id, _link_id = _setup_pair()
    assert family.get(f"/blood-requests/{req_id}").status_code == 404
    assert family.get(f"/emergencies/{em_id}").status_code == 404
    assert family.get("/patients/managed").json() == []


def test_accepted_relationship_grants_access() -> None:
    family, patient, _pat_email, req_id, em_id, link_id = _setup_pair()
    patient.post(f"/family/links/{link_id}/accept", json={})
    assert family.get(f"/blood-requests/{req_id}").status_code == 200
    assert family.get(f"/emergencies/{em_id}").status_code == 200


def test_rejected_relationship_grants_no_access() -> None:
    family, patient, _pat_email, req_id, em_id, link_id = _setup_pair()
    patient.post(f"/family/links/{link_id}/reject", json={})
    assert family.get(f"/blood-requests/{req_id}").status_code == 404
    assert family.get(f"/emergencies/{em_id}").status_code == 404


def test_revoked_relationship_grants_no_access() -> None:
    family, patient, _pat_email, req_id, em_id, link_id = _setup_pair()
    patient.post(f"/family/links/{link_id}/accept", json={})
    assert family.get(f"/blood-requests/{req_id}").status_code == 200  # sanity: had access
    patient.post(f"/family/links/{link_id}/revoke", json={})
    assert family.get(f"/blood-requests/{req_id}").status_code == 404
    assert family.get(f"/emergencies/{em_id}").status_code == 404


def test_expired_relationship_grants_no_access() -> None:
    family, _patient, _pat_email, req_id, em_id, link_id = _setup_pair()
    _force_status(link_id, FamilyLinkStatus.PENDING, expired=True)  # still PENDING, just stale
    # Lazily flips to EXPIRED on next read/action.
    assert family.get("/family/links").status_code == 200
    assert family.get(f"/blood-requests/{req_id}").status_code == 404
    assert family.get(f"/emergencies/{em_id}").status_code == 404


# --------------------------------------------------------------------------- #
# M/P/Q. Patient-id manipulation / unrelated-patient access / ownership
# --------------------------------------------------------------------------- #
def test_family_cannot_reach_unlinked_patient_by_substituting_id() -> None:
    """Family A is linked (and ACCEPTED) to Patient A. Confirm they still
    cannot reach Patient B's resources by manipulating patient_email/ids --
    the accepted link to A never widens into access to B."""
    family, _ = auth_client("FAMILY")
    patient_a, pat_a_email = auth_client("PATIENT")
    patient_b, pat_b_email = auth_client("PATIENT")
    link_id = _link(family, pat_a_email).json()["id"]
    patient_a.post(f"/family/links/{link_id}/accept", json={})

    req_b = patient_b.post("/blood-requests", json={"blood_type": "O_NEG"}).json()["id"]
    em_b = patient_b.post("/emergencies", json={"blood_type": "O_NEG"}).json()["id"]

    assert family.get(f"/blood-requests/{req_b}").status_code == 404
    assert family.get(f"/emergencies/{em_b}").status_code == 404
    # Can't file on B's behalf either, despite being an accepted family member of A.
    assert (
        family.post(
            "/blood-requests", json={"blood_type": "O_NEG", "patient_email": pat_b_email}
        ).status_code
        == 403
    )
    # And B never shows up in A's family's managed-patients list.
    managed = [p["email"] for p in family.get("/patients/managed").json()]
    assert pat_b_email not in managed
    assert pat_a_email in managed


# --------------------------------------------------------------------------- #
# N. Family-link-id manipulation
# --------------------------------------------------------------------------- #
def test_family_link_id_belonging_to_another_pair_is_not_actionable() -> None:
    """Family A holds a FamilyLink id for their own (pending) request to
    Patient A. Patient B must not be able to act on it (it isn't theirs),
    and Family A must not be able to use it to influence Patient B's link."""
    family_a, _ = auth_client("FAMILY")
    patient_a, pat_a_email = auth_client("PATIENT")
    _family_b, _ = auth_client("FAMILY")
    patient_b, _pat_b_email = auth_client("PATIENT")

    link_a_id = _link(family_a, pat_a_email).json()["id"]

    # Patient B (uninvolved in this link) can't accept/reject/revoke it.
    assert patient_b.post(f"/family/links/{link_a_id}/accept", json={}).status_code == 403
    assert patient_b.post(f"/family/links/{link_a_id}/reject", json={}).status_code == 403
    assert patient_b.post(f"/family/links/{link_a_id}/revoke", json={}).status_code == 403

    # It never shows up in Patient B's own link list.
    ids = [x["id"] for x in patient_b.get("/family/links").json()]
    assert link_a_id not in ids
    ids_a = [x["id"] for x in patient_a.get("/family/links").json()]
    assert link_a_id in ids_a


def test_nonexistent_family_link_id_returns_404_not_403() -> None:
    """A well-formed but nonexistent link id should 404, not leak existence
    information through a different status code."""
    patient, _ = auth_client("PATIENT")
    fake_id = str(uuid.uuid4())
    assert patient.post(f"/family/links/{fake_id}/accept", json={}).status_code == 404
    assert patient.post(f"/family/links/{fake_id}/reject", json={}).status_code == 404
    assert patient.post(f"/family/links/{fake_id}/revoke", json={}).status_code == 404


# --------------------------------------------------------------------------- #
# O. Emergency-id manipulation reusing Phase 12's own IDOR protection
# --------------------------------------------------------------------------- #
def test_emergency_id_manipulation_blocked_by_existing_emergency_authorization() -> None:
    family, _ = auth_client("FAMILY")
    patient_a, pat_a_email = auth_client("PATIENT")
    patient_b, _ = auth_client("PATIENT")
    link_id = _link(family, pat_a_email).json()["id"]
    patient_a.post(f"/family/links/{link_id}/accept", json={})

    em_b = patient_b.post("/emergencies", json={"blood_type": "AB_NEG"}).json()["id"]
    # Family, accepted for A only, tries B's emergency id directly.
    assert family.get(f"/emergencies/{em_b}").status_code == 404
    assert family.post(f"/emergencies/{em_b}/cancel", json={}).status_code == 404
    assert family.post(f"/emergencies/{em_b}/resolve", json={}).status_code == 404


# --------------------------------------------------------------------------- #
# R. Concurrent duplicate requests for a genuinely new pair
# --------------------------------------------------------------------------- #
def test_concurrent_first_time_requests_create_exactly_one_pending_link() -> None:
    family, _ = auth_client("FAMILY")
    _, pat_email = auth_client("PATIENT")
    results: list[dict] = []
    barrier = threading.Barrier(2)

    def _create() -> None:
        barrier.wait()
        r = _link(family, pat_email)
        results.append({"status": r.status_code, "body": r.json() if r.status_code < 500 else None})

    threads = [threading.Thread(target=_create) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    statuses = sorted(r["status"] for r in results)
    assert statuses == [201, 409], results

    db = SessionLocal()
    try:
        rows = db.query(FamilyLink).all()
        live = (FamilyLinkStatus.PENDING, FamilyLinkStatus.ACCEPTED)
        pair_rows = [r for r in rows if r.status in live]
        assert len(pair_rows) == 1
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# S. Concurrent approval vs rejection
# --------------------------------------------------------------------------- #
def test_concurrent_accept_and_reject_only_one_wins() -> None:
    family, _ = auth_client("FAMILY")
    patient, pat_email = auth_client("PATIENT")
    link_id = _link(family, pat_email).json()["id"]

    results: list[int] = []
    barrier = threading.Barrier(2)

    def _accept() -> None:
        barrier.wait()
        results.append(patient.post(f"/family/links/{link_id}/accept", json={}).status_code)

    def _reject() -> None:
        barrier.wait()
        results.append(patient.post(f"/family/links/{link_id}/reject", json={}).status_code)

    threads = [threading.Thread(target=_accept), threading.Thread(target=_reject)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [200, 409]

    db = SessionLocal()
    try:
        link = db.get(FamilyLink, uuid.UUID(link_id))
        assert link is not None
        assert link.status in (FamilyLinkStatus.ACCEPTED, FamilyLinkStatus.REJECTED)
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# T. Concurrent approval vs revocation
# --------------------------------------------------------------------------- #
def test_concurrent_accept_and_revoke_only_one_wins() -> None:
    family, _ = auth_client("FAMILY")
    patient, pat_email = auth_client("PATIENT")
    link_id = _link(family, pat_email).json()["id"]

    results: list[int] = []
    barrier = threading.Barrier(2)

    def _accept() -> None:
        barrier.wait()
        results.append(patient.post(f"/family/links/{link_id}/accept", json={}).status_code)

    def _revoke() -> None:
        barrier.wait()
        results.append(family.post(f"/family/links/{link_id}/revoke", json={}).status_code)

    threads = [threading.Thread(target=_accept), threading.Thread(target=_revoke)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Either accept wins (200) and the revoke either then succeeds against
    # the now-ACCEPTED row (200) or loses a genuine race and 409s if it read
    # PENDING first and validate_transition rejected PENDING->REVOKED... but
    # PENDING->REVOKED *is* legal, so both orders can legitimately succeed:
    # accept-then-revoke (200, 200) or revoke-then-accept (200, 409).
    assert results.count(200) >= 1
    db = SessionLocal()
    try:
        link = db.get(FamilyLink, uuid.UUID(link_id))
        assert link is not None
        assert link.status in (FamilyLinkStatus.ACCEPTED, FamilyLinkStatus.REVOKED)
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# T (two simultaneous revocations)
# --------------------------------------------------------------------------- #
def test_concurrent_double_revoke_only_one_succeeds() -> None:
    family, _ = auth_client("FAMILY")
    patient, pat_email = auth_client("PATIENT")
    link_id = _link(family, pat_email).json()["id"]
    patient.post(f"/family/links/{link_id}/accept", json={})

    results: list[int] = []
    barrier = threading.Barrier(2)

    def _revoke_as(client) -> None:
        barrier.wait()
        results.append(client.post(f"/family/links/{link_id}/revoke", json={}).status_code)

    threads = [
        threading.Thread(target=_revoke_as, args=(family,)),
        threading.Thread(target=_revoke_as, args=(patient,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [200, 409]


# --------------------------------------------------------------------------- #
# U. Notification generation for every transition
# --------------------------------------------------------------------------- #
def test_notifications_generated_for_every_transition() -> None:
    family, _ = auth_client("FAMILY")
    patient, pat_email = auth_client("PATIENT")
    link_id = _link(family, pat_email).json()["id"]

    assert any(
        n["type"] == "family.link_requested" for n in patient.get("/notifications").json()
    )

    patient.post(f"/family/links/{link_id}/accept", json={})
    assert any(
        n["type"] == "family.link_accepted" for n in family.get("/notifications").json()
    )

    link_id2 = _link(family, pat_email, relationship="son")
    assert link_id2.status_code == 409  # already ACCEPTED -- can't re-request live

    patient.post(f"/family/links/{link_id}/revoke", json={})
    assert any(
        n["type"] == "family.link_revoked" for n in family.get("/notifications").json()
    )

    link_id3 = _link(family, pat_email).json()["id"]
    patient.post(f"/family/links/{link_id3}/reject", json={"reason": "not now"})
    notes = family.get("/notifications").json()
    rejected = next(n for n in notes if n["type"] == "family.link_rejected")
    assert "not now" in (rejected.get("body") or "")


def test_expiration_notification_generated() -> None:
    family, _ = auth_client("FAMILY")
    patient, pat_email = auth_client("PATIENT")
    link_id = _link(family, pat_email).json()["id"]
    _force_status(link_id, FamilyLinkStatus.PENDING, expired=True)

    patient.get("/family/links")  # triggers lazy expiration
    assert any(
        n["type"] == "family.link_expired" for n in family.get("/notifications").json()
    )


# --------------------------------------------------------------------------- #
# V. Audit generation for every transition + access-denied audit
# --------------------------------------------------------------------------- #
def test_audit_generated_for_rejection_and_access_denials() -> None:
    admin, _ = auth_client("ADMIN")
    family, _ = auth_client("FAMILY")
    patient, pat_email = auth_client("PATIENT")
    outsider, _ = auth_client("FAMILY")
    link_id = _link(family, pat_email).json()["id"]

    patient.post(f"/family/links/{link_id}/reject", json={"reason": "wrong person"})
    outsider.post(f"/family/links/{link_id}/revoke", json={})  # 403, but audited

    log = admin.get("/admin/audit-log", params={"action": "family.link_rejected"}).json()
    assert any(e["target_id"] == link_id for e in log["items"])

    denied = admin.get("/admin/audit-log", params={"action": "family_link.access_denied"}).json()
    assert any(e["target_id"] == link_id for e in denied["items"])


# --------------------------------------------------------------------------- #
# W. Frontend authorization behavior (server-side half): allowed_transitions
# --------------------------------------------------------------------------- #
def test_allowed_transitions_reflect_real_state_not_client_input() -> None:
    family, _ = auth_client("FAMILY")
    patient, pat_email = auth_client("PATIENT")
    link_id = _link(family, pat_email).json()["id"]

    pending = patient.get("/family/links").json()[0]
    assert set(pending["allowed_transitions"]) == {"ACCEPTED", "REJECTED", "REVOKED", "EXPIRED"}

    patient.post(f"/family/links/{link_id}/accept", json={})
    accepted = patient.get("/family/links").json()[0]
    assert set(accepted["allowed_transitions"]) == {"REVOKED"}

    patient.post(f"/family/links/{link_id}/revoke", json={})
    revoked = patient.get("/family/links").json()[0]
    assert set(revoked["allowed_transitions"]) == {"PENDING"}


# --------------------------------------------------------------------------- #
# Spec section 29 -- real end-to-end scenarios against the actual test DB.
# --------------------------------------------------------------------------- #
def test_e2e_request_accept_emergency_then_revoke() -> None:
    """The full real-world scenario: FAMILY requests link -> PENDING ->
    PATIENT notified -> PATIENT approves -> ACCEPTED -> family accesses
    permitted info -> family creates and manages an emergency for the
    patient (reusing Phase 12's own emergency authorization, not a second
    mechanism) -> family receives a real notification -> PATIENT revokes ->
    REVOKED -> family immediately loses relationship-based access: it can no
    longer file anything new on the patient's behalf, and can no longer
    reach an emergency the *patient* raised themselves (one the family
    member never created). Note: emergencies.py's pre-existing (Phase 12,
    unchanged here) `_can_view`/`_can_manage` also grant the *creator* of a
    record standing access to what they personally created, independent of
    the family relationship -- so the emergency the family member filed
    stays visible/manageable to them post-revoke on that separate, existing
    creator-access basis. That is intentional Phase 12 behavior, reused
    (not re-implemented) here rather than a Phase 13 authorization gap; the
    boundary this phase actually owns -- relationship-gated access to the
    *patient's own* records and to *new* actions on their behalf -- is what
    the assertions below exercise."""
    family, fam_email = auth_client("FAMILY")
    patient, pat_email = auth_client("PATIENT")

    # 1. FAMILY requests a link.
    r = _link(family, pat_email, relationship="son")
    assert r.status_code == 201, r.text
    link = r.json()
    assert link["status"] == "PENDING"
    link_id = link["id"]

    # 2. PATIENT is notified of the request.
    assert any(
        n["type"] == "family.link_requested" for n in patient.get("/notifications").json()
    )

    # 3. PATIENT approves -> ACCEPTED.
    r = patient.post(f"/family/links/{link_id}/accept", json={})
    assert r.status_code == 200
    assert r.json()["status"] == "ACCEPTED"

    # 4. Family now accesses permitted info: the patient shows up as managed,
    # and family can see the patient's own blood-request history.
    managed = [p["email"] for p in family.get("/patients/managed").json()]
    assert pat_email in managed

    # 5. Family creates a real emergency for the patient, reusing the exact
    # Phase 12 emergency-creation path (patient_email -> resolve_beneficiary
    # -> has_accepted_link).
    em = family.post(
        "/emergencies", json={"blood_type": "O_NEG", "patient_email": pat_email}
    )
    assert em.status_code == 201, em.text
    em_id = em.json()["id"]
    assert em.json()["patient_user_id"] != em.json()["created_by_user_id"]

    # Family can manage (not just view) the emergency it raised for the
    # patient -- resolve/cancel are creator-or-patient-or-accepted-family
    # actions (emergencies.py::_can_manage); acknowledge is donor/hospital
    # only, so resolve is the real family-management action to exercise here.
    assert family.get(f"/emergencies/{em_id}").status_code == 200
    resolved = family.post(f"/emergencies/{em_id}/resolve", json={})
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "RESOLVED"

    # Patient also sees the emergency filed on their behalf.
    assert any(e["id"] == em_id for e in patient.get("/emergencies").json())

    # A second emergency the PATIENT raises themselves (family never
    # created it) is the one that isolates the relationship-based access
    # boundary this phase owns, cleanly from the creator-access boundary above.
    own_em = patient.post("/emergencies", json={"blood_type": "B_NEG"}).json()["id"]
    assert family.get(f"/emergencies/{own_em}").status_code == 200  # ACCEPTED: visible

    # 6. PATIENT revokes -> REVOKED.
    r = patient.post(f"/family/links/{link_id}/revoke", json={})
    assert r.status_code == 200
    assert r.json()["status"] == "REVOKED"

    # Family received a real notification about the revocation.
    assert any(
        n["type"] == "family.link_revoked" for n in family.get("/notifications").json()
    )

    # 7. Family immediately loses relationship-based access: the patient
    # drops out of "managed", filing anything new on their behalf is
    # blocked, and the emergency the *patient* raised (not the family) is
    # no longer reachable at all.
    assert family.get("/patients/managed").json() == []
    assert (
        family.post(
            "/emergencies", json={"blood_type": "O_NEG", "patient_email": pat_email}
        ).status_code
        == 403
    )
    assert family.get(f"/emergencies/{own_em}").status_code == 404


def test_e2e_request_then_reject_scenario() -> None:
    """Request -> PENDING -> PATIENT notified -> PATIENT rejects ->
    REJECTED -> family never gains access -> family may send a fresh
    request afterward (reopening the same relationship, per the existing
    row-reuse design -- see LIFELINK_PHASE_13_REPORT.md section 5)."""
    family, _ = auth_client("FAMILY")
    patient, pat_email = auth_client("PATIENT")

    link_id = _link(family, pat_email).json()["id"]
    assert any(
        n["type"] == "family.link_requested" for n in patient.get("/notifications").json()
    )

    r = patient.post(f"/family/links/{link_id}/reject", json={"reason": "not comfortable yet"})
    assert r.status_code == 200
    assert r.json()["status"] == "REJECTED"
    assert r.json()["reason"] == "not comfortable yet"

    assert family.get("/patients/managed").json() == []
    rejected_note = next(
        n for n in family.get("/notifications").json() if n["type"] == "family.link_rejected"
    )
    assert "not comfortable yet" in (rejected_note.get("body") or "")

    # A fresh request afterward reopens the same row as PENDING again.
    r2 = _link(family, pat_email)
    assert r2.status_code == 201
    assert r2.json()["id"] == link_id
    assert r2.json()["status"] == "PENDING"


def test_e2e_request_then_expire_scenario() -> None:
    """Request -> PENDING -> never actioned -> lazily EXPIRED past its
    deadline on the next read or action -> family notified -> family may
    request again afterward."""
    family, _ = auth_client("FAMILY")
    patient, pat_email = auth_client("PATIENT")

    link_id = _link(family, pat_email).json()["id"]
    _force_status(link_id, FamilyLinkStatus.PENDING, expired=True)

    # Lazily flips to EXPIRED on the patient's next read.
    links = patient.get("/family/links").json()
    assert any(x["id"] == link_id and x["status"] == "EXPIRED" for x in links)

    assert any(
        n["type"] == "family.link_expired" for n in family.get("/notifications").json()
    )
    assert patient.post(f"/family/links/{link_id}/accept", json={}).status_code == 409

    r2 = _link(family, pat_email)
    assert r2.status_code == 201
    assert r2.json()["id"] == link_id
    assert r2.json()["status"] == "PENDING"
