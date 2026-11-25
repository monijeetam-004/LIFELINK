"""Phase 14 -- Production Verification System.

Dedicated hardening coverage that isn't already exercised by the pre-existing
`tests/test_verifications.py` (24 tests: upload validation, role/kind
matching, duplicate-pending + rate limiting, /verifications/me isolation,
admin-route 403s, document round-trip, approve/reject + already-decided
conflicts, admin listing filters) or `tests/test_certificates.py` (9 tests:
issue idempotency, cookie + signed-token download, public-verify safe
fields, ownership/role authorization, real-donation requirement).

This file adds: real file-content (magic-byte) validation against a
mismatched/spoofed Content-Type, malicious-filename handling, reviewer
-identity spoofing resistance, row-locked concurrent approve/reject/
double-approve races, an explicit unauthenticated-access sweep, and an
end-to-end donor/hospital verification-to-capability scenario tying
HOSPITAL_FACILITY approval to the existing Phase 12 target_facility_id
hard gate.
"""
from __future__ import annotations

import threading
import uuid

from fastapi.testclient import TestClient

from app.main import app
from tests.helpers import auth_client

REAL_PDF = b"%PDF-1.4 fake donor id document"
REAL_PNG = b"\x89PNG\r\n\x1a\nfakepngbytes"
FAKE_EXE = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff"


def _submit(client, kind: str, files=None, note=None):
    files = files or [("id.pdf", REAL_PDF, "application/pdf")]
    upload = [("files", f) for f in files]
    data = {"kind": kind}
    if note is not None:
        data["note"] = note
    return client.post("/verifications", data=data, files=upload)


# --------------------------------------------------------------------------- #
# File security: real content-type sniffing, not just the client's claim
# --------------------------------------------------------------------------- #
def test_executable_disguised_as_pdf_is_rejected() -> None:
    """The client declares application/pdf (an allowed type) but the bytes
    are a Windows PE header. Before Phase 14 this would have been accepted
    outright -- the server only checked the client-supplied Content-Type,
    never the file's own bytes."""
    donor, _ = auth_client("DONOR")
    r = _submit(donor, "DONOR_IDENTITY", files=[("id.pdf", FAKE_EXE, "application/pdf")])
    assert r.status_code == 422, r.text


def test_html_payload_disguised_as_png_is_rejected() -> None:
    donor, _ = auth_client("DONOR")
    html = b"<script>alert(1)</script>"
    r = _submit(donor, "DONOR_IDENTITY", files=[("photo.png", html, "image/png")])
    assert r.status_code == 422, r.text


def test_content_type_swap_between_two_allowed_types_is_rejected() -> None:
    """A real PNG's bytes declared as application/pdf (both individually
    allowed types, but they must actually agree)."""
    donor, _ = auth_client("DONOR")
    r = _submit(donor, "DONOR_IDENTITY", files=[("id.pdf", REAL_PNG, "application/pdf")])
    assert r.status_code == 422, r.text


def test_genuine_files_of_each_allowed_type_still_accepted() -> None:
    """The new sniffing is a real check, not a blanket new rejection --
    real PDF/PNG/JPEG content with a matching declared type still works."""
    donor, _ = auth_client("DONOR")
    jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIFfakejpegbytes"
    r = _submit(
        donor, "DONOR_IDENTITY",
        files=[
            ("id.pdf", REAL_PDF, "application/pdf"),
            ("id.png", REAL_PNG, "image/png"),
            ("id.jpg", jpeg, "image/jpeg"),
        ],
    )
    assert r.status_code == 201, r.text
    assert len(r.json()["documents"]) == 3


# --------------------------------------------------------------------------- #
# File security: malicious / traversal / unicode filenames
# --------------------------------------------------------------------------- #
def test_path_traversal_filename_is_sanitized() -> None:
    donor, _ = auth_client("DONOR")
    r = _submit(
        donor, "DONOR_IDENTITY", files=[("../../../etc/passwd", REAL_PDF, "application/pdf")]
    )
    assert r.status_code == 201, r.text
    stored_name = r.json()["documents"][0]["filename"]
    assert "/" not in stored_name and ".." not in stored_name


def test_double_extension_filename_is_still_handled_safely() -> None:
    """resume.pdf.exe with genuine PDF bytes: not blocked (it's never
    executed from storage, only served as an opaque blob with the sniffed
    content type), but the filename itself must still come back sanitized."""
    donor, _ = auth_client("DONOR")
    r = _submit(donor, "DONOR_IDENTITY", files=[("resume.pdf.exe", REAL_PDF, "application/pdf")])
    assert r.status_code == 201, r.text
    assert r.json()["documents"][0]["filename"] == "resume.pdf.exe"


def test_unicode_and_control_character_filename_is_sanitized() -> None:
    # A right-to-left override, a zero-width space, and an embedded null
    # byte -- built with explicit escapes so the *test source file* itself
    # never contains a literal null byte (which Python's own parser refuses
    # to load as source at all).
    donor, _ = auth_client("DONOR")
    weird = "id" + "\u202e" + "\x00" + "\u200b" + ".pdf"
    r = _submit(donor, "DONOR_IDENTITY", files=[(weird, REAL_PDF, "application/pdf")])
    assert r.status_code == 201, r.text
    stored_name = r.json()["documents"][0]["filename"]
    assert all(c.isalnum() or c in "._-" for c in stored_name)



# --------------------------------------------------------------------------- #
# Reviewer-identity spoofing
# --------------------------------------------------------------------------- #
def test_client_cannot_spoof_reviewer_identity() -> None:
    """Neither approve nor reject accepts a reviewer/actor field from the
    client -- the schema has no such field, and the real authenticated admin
    is always what gets recorded, regardless of what extra JSON is sent."""
    donor, _ = auth_client("DONOR")
    admin, admin_email = auth_client("ADMIN")
    impostor_id = str(uuid.uuid4())
    vid = _submit(donor, "DONOR_IDENTITY").json()["id"]

    r = admin.post(
        f"/admin/verifications/{vid}/approve",
        json={"note": "ok", "reviewer_user_id": impostor_id, "reviewed_by": "admin123"},
    )
    assert r.status_code == 200, r.text
    admin_id = admin.get("/auth/me").json()["id"]
    assert r.json()["reviewer_user_id"] == admin_id
    assert r.json()["reviewer_user_id"] != impostor_id


def test_client_cannot_spoof_reviewer_identity_on_reject() -> None:
    donor, _ = auth_client("DONOR")
    admin, _ = auth_client("ADMIN")
    impostor_id = str(uuid.uuid4())
    vid = _submit(donor, "DONOR_IDENTITY").json()["id"]

    r = admin.post(
        f"/admin/verifications/{vid}/reject",
        json={"reason": "not clear", "reviewer_user_id": impostor_id},
    )
    assert r.status_code == 200, r.text
    admin_id = admin.get("/auth/me").json()["id"]
    assert r.json()["reviewer_user_id"] == admin_id


# --------------------------------------------------------------------------- #
# Unauthenticated access sweep
# --------------------------------------------------------------------------- #
def test_unauthenticated_requests_are_rejected() -> None:
    anon = TestClient(app)
    donor, _ = auth_client("DONOR")
    admin, _ = auth_client("ADMIN")
    vid = _submit(donor, "DONOR_IDENTITY").json()["id"]

    assert anon.get("/verifications/me").status_code == 401
    assert anon.post(
        "/verifications", data={"kind": "DONOR_IDENTITY"},
        files=[("files", ("id.pdf", REAL_PDF, "application/pdf"))],
    ).status_code == 401
    assert anon.get("/admin/verifications").status_code == 401
    assert anon.get(f"/admin/verifications/{vid}").status_code == 401
    assert anon.post(f"/admin/verifications/{vid}/approve", json={}).status_code == 401
    assert anon.post(f"/admin/verifications/{vid}/reject", json={"reason": "no"}).status_code == 401


# --------------------------------------------------------------------------- #
# Family-link access must not bypass verification document authorization
# --------------------------------------------------------------------------- #
def test_family_link_does_not_grant_access_to_donor_verification_or_documents() -> None:
    """Phase 14 spec's explicit non-negotiable concern: an approved Phase 13
    family relationship must not leak into verification/document access.

    Two independent guarantees are checked here:
    1. Structural: FamilyLinkCreate is validated against `patient.role ==
       PATIENT` (app/api/routes/family.py) -- there is no data path by which
       a FAMILY user can even form a link "to" a DONOR or HOSPITAL account,
       so the family-link table can never name a donor/hospital at all.
    2. Behavioral: even a FAMILY user holding a real, ACCEPTED link to a
       PATIENT gets the same 403/401 as any other non-admin stranger when it
       tries to reach a completely unrelated donor's verification record,
       that donor's uploaded document, or the admin queue -- /verifications/me
       is unconditionally scoped to `VerificationRequest.user_id == caller.id`
       and the only document-bytes route requires the ADMIN role, so neither
       route has a family-relationship code path to bypass in the first
       place.
    """
    family, _ = auth_client("FAMILY")
    patient, patient_email = auth_client("PATIENT")
    donor, _ = auth_client("DONOR")

    # (1) Attempting to link FAMILY -> DONOR is structurally impossible: the
    # only account that exists to link to here is a PATIENT, and the route
    # itself would 404 "No patient with that email" for a donor's email.
    donor_email = donor.get("/auth/me").json()["email"]
    r = family.post(
        "/family/links", json={"patient_email": donor_email, "relationship": "sibling"}
    )
    assert r.status_code == 404, r.text

    # (2) A genuine, ACCEPTED family link to an actual patient still grants
    # nothing against an unrelated donor's verification data.
    link = family.post(
        "/family/links", json={"patient_email": patient_email, "relationship": "sibling"}
    ).json()
    accepted = patient.post(f"/family/links/{link['id']}/accept").json()
    assert accepted["status"] == "ACCEPTED"

    vid = _submit(donor, "DONOR_IDENTITY").json()["id"]

    # FAMILY has no verification endpoints of its own beyond the universally
    # self-scoped /verifications/me (empty for a FAMILY caller -- it never
    # submitted anything) and is flatly forbidden from every admin route,
    # link or no link.
    assert family.get("/verifications/me").json() == []
    assert family.get(f"/admin/verifications/{vid}").status_code == 403
    assert family.get(f"/admin/verifications/{vid}/documents/0").status_code == 403
    assert family.get("/admin/verifications").status_code == 403
    assert family.post(f"/admin/verifications/{vid}/approve", json={}).status_code == 403


# --------------------------------------------------------------------------- #
# Concurrency: row-locked approve/reject
# --------------------------------------------------------------------------- #
def test_concurrent_approve_and_reject_only_one_wins() -> None:
    donor, _ = auth_client("DONOR")
    admin, _ = auth_client("ADMIN")
    vid = _submit(donor, "DONOR_IDENTITY").json()["id"]

    results: list[int] = []
    barrier = threading.Barrier(2)

    def _approve() -> None:
        barrier.wait()
        results.append(admin.post(f"/admin/verifications/{vid}/approve", json={}).status_code)

    def _reject() -> None:
        barrier.wait()
        resp = admin.post(f"/admin/verifications/{vid}/reject", json={"reason": "conflict"})
        results.append(resp.status_code)

    threads = [threading.Thread(target=_approve), threading.Thread(target=_reject)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [200, 409], results
    assert donor.get("/verifications/me").json()[0]["status"] in ("APPROVED", "REJECTED")


def test_concurrent_double_approve_only_one_succeeds() -> None:
    hospital, _ = auth_client("HOSPITAL")
    admin, _ = auth_client("ADMIN")
    vid = _submit(hospital, "HOSPITAL_FACILITY").json()["id"]

    results: list[int] = []
    barrier = threading.Barrier(2)

    def _approve() -> None:
        barrier.wait()
        results.append(admin.post(f"/admin/verifications/{vid}/approve", json={}).status_code)

    threads = [threading.Thread(target=_approve) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [200, 409], results
    # Exactly one approval ever took effect -- verified flips true, once.
    assert hospital.get("/hospitals/me").json()["verified"] is True


# --------------------------------------------------------------------------- #
# Certificate ID / verify-code enumeration resistance
# --------------------------------------------------------------------------- #
def test_verify_codes_are_not_sequential_or_predictable() -> None:
    from datetime import date

    donor, _ = auth_client("DONOR")
    donor.put("/donors/me", json={"blood_type": "O_NEG"})
    d1 = donor.post(
        "/donors/me/donations",
        json={"donated_on": date.today().isoformat(), "units": 1, "hospital_name": "A"},
    ).json()["id"]
    d2 = donor.post(
        "/donors/me/donations",
        json={"donated_on": date.today().isoformat(), "units": 1, "hospital_name": "B"},
    ).json()["id"]
    # Phase E: certificate issuance requires verification_status ==
    # VERIFIED -- these donations have no hospital_id, so only an admin
    # can review them (see tests/test_certificates.py's
    # _donor_with_donation for the same note).
    admin, _ = auth_client("ADMIN")
    assert admin.post(f"/donations/{d1}/verify", json={}).status_code == 200
    assert admin.post(f"/donations/{d2}/verify", json={}).status_code == 200
    c1 = donor.post("/certificates", json={"donation_id": d1}).json()
    c2 = donor.post("/certificates", json={"donation_id": d2}).json()

    # Not equal, not simple increments of one another, and long enough to
    # resist brute force (secrets.token_urlsafe(16) -> 22 chars).
    assert c1["verify_code"] != c2["verify_code"]
    assert len(c1["verify_code"]) >= 20 and len(c2["verify_code"]) >= 20
    assert c1["serial"] != c2["serial"]


def test_certificate_id_manipulation_across_donors() -> None:
    from datetime import date

    donor_a, _ = auth_client("DONOR")
    donor_b, _ = auth_client("DONOR")
    did = donor_a.post(
        "/donors/me/donations",
        json={"donated_on": date.today().isoformat(), "units": 1, "hospital_name": "X"},
    ).json()["id"]
    admin, _ = auth_client("ADMIN")
    assert admin.post(f"/donations/{did}/verify", json={}).status_code == 200
    cert = donor_a.post("/certificates", json={"donation_id": did}).json()

    # Donor B substitutes Donor A's real certificate id -- must not resolve.
    assert donor_b.get(f"/certificates/{cert['id']}").status_code == 404
    assert donor_b.get(f"/certificates/{cert['id']}/download").status_code == 401
    # And a random, well-formed but nonexistent id is likewise a plain 404.
    assert donor_a.get(f"/certificates/{uuid.uuid4()}").status_code == 404


# --------------------------------------------------------------------------- #
# End-to-end: HOSPITAL_FACILITY approval unlocks the real Phase 12 hard gate
# --------------------------------------------------------------------------- #
def test_e2e_hospital_verification_unlocks_emergency_target_facility_capability() -> None:
    """submit -> PENDING -> admin review -> approve -> verified facility
    capability: an unverified facility cannot be targeted by an emergency's
    target_facility_id (the one genuine hard gate this codebase already
    enforces -- emergencies.py::_validate_target_facility); approval is the
    only thing that flips it, and once flipped the same facility can be
    targeted for real."""
    hospital, _ = auth_client("HOSPITAL")
    patient, _ = auth_client("PATIENT")
    admin, _ = auth_client("ADMIN")

    hospital.put("/hospitals/me", json={"name": "Metro General", "city": "Metro"})
    facility_id = hospital.get("/hospitals/me").json()["id"]

    # Not yet verified -- targeting it is rejected by the existing hard gate.
    blocked = patient.post(
        "/emergencies", json={"blood_type": "O_NEG", "target_facility_id": facility_id}
    )
    assert blocked.status_code == 400, blocked.text

    vid = _submit(hospital, "HOSPITAL_FACILITY").json()["id"]
    assert hospital.get("/hospitals/me").json()["verified"] is False

    r = admin.post(f"/admin/verifications/{vid}/approve", json={"note": "license confirmed"})
    assert r.status_code == 200
    assert hospital.get("/hospitals/me").json()["verified"] is True

    allowed = patient.post(
        "/emergencies", json={"blood_type": "O_NEG", "target_facility_id": facility_id}
    )
    assert allowed.status_code == 201, allowed.text
    assert allowed.json()["target_facility_id"] == facility_id


def test_e2e_donor_rejection_then_resubmission() -> None:
    """submit -> review -> reject -> actionable reason visible -> resubmit
    (a fresh PENDING request; the existing single-active-request-per-kind
    design means resubmission after a decided request is just a normal new
    submission, already allowed since only a *pending* duplicate conflicts)."""
    donor, _ = auth_client("DONOR")
    admin, _ = auth_client("ADMIN")
    vid = _submit(donor, "DONOR_IDENTITY").json()["id"]

    admin.post(f"/admin/verifications/{vid}/reject", json={"reason": "Document is blurry"})
    mine = donor.get("/verifications/me").json()
    assert mine[0]["status"] == "REJECTED"
    assert mine[0]["review_note"] == "Document is blurry"

    resubmit = _submit(donor, "DONOR_IDENTITY", note="retrying with a clearer scan")
    assert resubmit.status_code == 201, resubmit.text
    assert resubmit.json()["status"] == "PENDING"
    assert donor.get("/donors/me").json()["identity_verified"] is False
