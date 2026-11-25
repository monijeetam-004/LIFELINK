# LifeLink — Verification Lifecycles (donations and identity/facility documents)

Introduced/extended in Phase E. Two separate things are "verified" in LifeLink, and they are kept separate on purpose.

## 1. Donation verification

Code: `backend/app/models/donation.py`, `backend/app/models/enums.py::DonationVerificationStatus`, `backend/app/api/routes/donation_review.py`, the certificate gate in `backend/app/api/routes/certificates.py::issue_certificate`. Tests: `backend/tests/test_phase_e_donation_verification.py`.

### States

| State | Meaning |
|---|---|
| `DONOR_REPORTED` | The donor said it happened. Default for every new donation and for **every** pre-Phase-E row (none was ever confirmed by anyone). Shown as "Self-reported · not yet verified", never as "verified". |
| `VERIFIED` | A facility (or an admin) confirmed it against its own records. Records who and when. |
| `REJECTED` | A facility (or an admin) could not confirm it. Shown as "Disputed". |

`VERIFIED ↔ REJECTED` corrections are allowed, each one a fresh, audited, attributed decision. Same-state calls are 409 (stale/duplicate action), and two reviewers racing get exactly one 200 and one 409.

There is deliberately **no** "clinically verified" tier: LifeLink has no lab or clinical-staff workflow, so "verified" here means "this donation happened", not a test result. The reviewer UI says so.

### Who may review

- Never the donor (`/donations/*` accepts only HOSPITAL and ADMIN; invariant 32 also checks no row was ever reviewed by its own donor).
- A hospital may review **only** donations whose `hospital_id` is **its own** facility. `hospital_id` is a new, optional structural link the donor sets by picking a facility from the directory when logging. A free-text `hospital_name` that happens to match a facility's name grants nothing (`test_unlinked_donation_is_admin_review_only`) — fuzzy-matching text to authority would be guessing.
- An admin may review anything, including unlinked donations (every historical row).
- Review queue: `GET /donations` — a hospital sees only its own linked donations; an admin sees all and can narrow to unlinked ones.

### What verification gates

Reconnaissance found that logging a donation has **no** coupling to inventory: `BloodUnit` rows are only ever created by a hospital's own ADD/receive actions. So "an unverified donation creates inventory" is not a risk that exists in this codebase, and nothing was invented to "address" it.

The one place a self-reported fact became an authoritative-looking artifact was **certificate issuance** — a PDF with a public verification URL. `POST /certificates` now returns 409 unless the donation is `VERIFIED`. Already-issued certificates keep resolving: the gate applies to *first* issuance only, and rejecting a donation later does **not** revoke its certificate (revocation is a separate, explicit admin action). Certificates issued before Phase E were issued under the old rule; they were not retroactively revoked, because that would destroy a real record on the basis of a policy that didn't exist when it was issued.

## 2. Identity / facility document verification (versioning)

Code: `backend/app/models/verification.py`, `backend/app/api/routes/verifications.py`. Tests: `backend/tests/test_phase_e_document_versioning.py`.

### Append-only versions

Submissions were already never overwritten (no replace/delete endpoint; every file under a fresh uuid-prefixed storage key). Phase E makes the history explicit:

- `version` — 1, 2, 3 … per `(user, kind)`, unique (`uq_verification_version`). A concurrent pair of resubmissions can't share a version: the loser gets a clean 409.
- `supersedes_request_id` — the previous submission of the same kind.
- An earlier version's documents, checksums, decision and reviewer are never touched by a later one (`test_resubmission_after_rejection_is_new_version_old_untouched` reads the old bytes back from storage).

### Integrity

- Every document now records `sha256` of the exact bytes written at submission.
- Every admin download recomputes it. A mismatch is refused (409, "integrity check failed") and audited as `verification.document_integrity_failed` (CRITICAL in the audit feed). Matching downloads carry `X-Document-Integrity: sha256-verified`.
- Documents submitted before Phase E have no recorded checksum and none was computed after the fact (that would misrepresent when it was taken). They are served with `X-Document-Integrity: unverified-legacy` and labelled in the admin UI.
- Approve/reject audit entries record the `version` and the `document_sha256` list actually reviewed, so a decision is bound to specific bytes.

This is tamper-*evidence* for the stored files, not access control: anyone able to rewrite both the object store and the database row could still make them agree. The audit chain (see `LIFELINK_AUDIT_CHAIN.md`) makes the database half of that detectable.
