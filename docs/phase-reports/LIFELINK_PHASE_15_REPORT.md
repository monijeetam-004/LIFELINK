# LIFELINK — PHASE 15 REPORT
## Donation Certificates & Trust Verification

Status: **COMPLETE**

Baseline tests (frozen, before any Phase 15 change): **575 passed, 0 failed, 0 errors**
Final tests (after all Phase 15 changes): **586 passed, 0 failed, 0 errors**
(575 pre-existing + 11 new: `tests/test_phase15_certificate_security.py`)

---

## 1. Existing certificate architecture (inspected before any code change)

A complete, working certificate system already existed. Per the spec's rule ("do
not create a second certificate architecture"), this phase audited it exhaustively
before writing anything:

- **Model** (`app/models/certificate.py`): `Certificate` — `id` (UUID PK),
  `donation_id` (FK `donations`, **unique**, CASCADE), `donor_user_id` (FK `users`,
  CASCADE, denormalized for fast authorization), `serial` (unique, human-readable,
  e.g. `LL-2026-8F3A1C9D`), `verify_code` (unique, indexed, `secrets.token_urlsafe(16)`
  — 128 bits of entropy, the actual public lookup key), `storage_key`.
- **Service** (`app/services/certificate_service.py`): `render_pdf()` (ReportLab —
  border, donor name, donation detail, serial, verify URL, and a real embedded QR
  code pointing at the public verify URL), `get_or_issue()` (check-then-create,
  audits `certificate.issued`, notifies `certificate.ready`), `load_pdf()`,
  `make_download_token()` / `check_download_token()` (HMAC-signed, time-limited,
  bound to a specific `cert_id` via the signed payload).
- **Routes** (`app/api/routes/certificates.py`, pre-existing): list own
  (`GET /certificates`), issue (`POST /certificates`), public verify
  (`GET /certificates/verify/{code}`), get one (`GET /certificates/{id}`),
  download (`GET /certificates/{id}/download`, cookie-auth or signed-token).
- **Schemas** (`app/schemas/certificate.py`): `CertificateIssueIn` has exactly one
  field — `donation_id`. No client-writable status, reviewer, donor identity, or
  timestamp field exists anywhere on the issuance path.
- **Admin certificate registry** (`app/api/routes/admin.py` `GET /admin/certificates`,
  `frontend/src/pages/admin/AdminAnalytics.tsx`): already a complete, real,
  admin-only, searchable read registry plus a working "verify by code" tool — no
  edit/revoke functionality exists, and none was added (no clear business
  requirement for it — see section 19).
- **Frontend**: `DonationCertificate.tsx` (real certificate display + PDF
  download + "Verify Document" link, all backed by `listCertificates` /
  `certificateDownloadUrl`), `VerifyCertificate.tsx` (real public verification
  page), `DonorDashboard.tsx`'s "Get certificate" button (real
  `issueCertificate(donationId)` call).
- **Donation eligibility model** (`app/models/donation.py`): `Donation` has **no
  status field at all** — no draft/pending/completed states. A `Donation` row is
  created only via two legitimate, pre-existing paths: a donor self-reporting
  (`POST /donors/me/donations`) or a hospital's request-fulfillment flow
  auto-recording one `Donation` per accepted donor response
  (`blood_requests.py`'s fulfill route). Its mere existence, owned by the caller,
  **is** the authoritative "eligible" signal — there is no separate "completed"
  flag to invent, and the spec explicitly forbids inventing one where an
  equivalent authoritative field (or, here, an equivalent authoritative *absence*
  of any draft state) already exists.

**Conclusion: reuse entirely. No second certificate model, service, route set,
audit mechanism, or frontend page was created.**

---

## 2. Architecture reused (unchanged)

Every piece listed in section 1 was reused as-is except the two real, targeted
changes in section 3. In particular: `render_pdf`, the QR embedding, `verify_code`
generation, `make_download_token`/`check_download_token`, the storage key scheme
(`certificates/{donor_id}/{cert_id}.pdf`, opaque and non-predictable, on the same
S3/MinIO-with-local-fallback abstraction hardened in Phase 14), the admin registry,
and every frontend page were all audited and found already correct.

---

## 3. Changes made

Two genuine gaps were found by adversarial testing, not assumed, and both were
fixed rather than worked around:

1. **Concurrent-generation race** (`app/api/routes/certificates.py::issue_certificate`)
   — see section 9. Fixed by row-locking the `Donation` row
   (`db.get(Donation, id, with_for_update=True)`) for the duration of issuance.
2. **Unhandled storage-read failure on download** — see section 25/26. Fixed by
   wrapping `certificate_service.load_pdf()` in `download_certificate` with a
   try/except that fails closed with a clean `503`, never a bare exception.

Two additive hardening changes, not bug fixes but genuine gaps against the spec's
explicit audit requirements:

3. **`certificate.viewed` audit event** added to `GET /certificates/{id}`'s success
   path (the denied path was already audited; the authorized path was not).
4. **`certificate.downloaded` audit event** added to the download route's success
   path (same asymmetry — denied downloads were already audited, successful ones
   were not), recording `via: "owner" | "admin" | "token"`.

One frontend honesty fix:

5. `VerifyCertificate.tsx`'s "not verified" copy previously said "...or the
   certificate was revoked" — but no revocation feature exists anywhere in this
   system. Reworded to avoid implying a feature that isn't real
   ("Double-check it was copied correctly").

No model, schema, or migration changes were made (section 20).

---

## 4. Certificate lifecycle

Unchanged, and confirmed accurate by direct code reading rather than assumed:

```
donor logs or is credited with a Donation (self-report, or hospital fulfillment)
        ↓
donor calls POST /certificates { donation_id }
        ↓
backend verifies: donation exists AND donation.donor_user_id == caller.id
        ↓
Certificate row created (if one doesn't already exist for this donation_id)
        ↓
PDF rendered (ReportLab) and written to private object storage
        ↓
audit: certificate.issued  |  notification: certificate.ready
        ↓
donor views (GET /certificates/{id}, now audited: certificate.viewed)
        ↓
donor/admin/token-holder downloads (GET /certificates/{id}/download,
    now audited: certificate.downloaded)
        ↓
anyone with the verify_code checks GET /certificates/verify/{code}
    → safe-fields-only result
```

Certificates are immutable once created — no route ever updates a `Certificate`
row's fields after insertion (confirmed by grep: no `UPDATE`/attribute-assignment
touches `Certificate` anywhere outside `get_or_issue`'s initial construction).
Re-requesting issuance for the same donation always returns the same, unchanged
row (`get_or_issue`'s existing-row check) — this is the pre-existing, correct
"one certificate per donation" business rule, now also race-safe (section 9).

---

## 5. Eligibility rules

A certificate can be generated only for a `Donation` row that: (a) exists, and
(b) is owned by the authenticated caller (`donation.donor_user_id == user.id`).
There is no other gate, because there is no other authoritative state to gate on
— `Donation` has no status field, so its existence (via either legitimate
creation path) already means "this donation happened," which is this
repository's actual, pre-existing definition of eligibility. `CertificateIssueIn`
accepts exactly one field (`donation_id`); a client sending extra fields
(`status`, `donor_user_id`, `certificate_status`, a timestamp) has them silently
ignored by pydantic — confirmed by test
(`test_generation_requires_a_real_donation_row_no_client_status_field_exists`),
not merely assumed from the schema's shape.

**Documented limitation, not a Phase 15 gap**: self-reported donations
(`POST /donors/me/donations`) are not independently confirmed by a hospital —
a donor could self-log a donation that didn't happen and issue a certificate for
it. This is a pre-existing trust model of the *donation-logging* feature itself
(unrelated to certificates, predating this phase, and out of this phase's scope
to redesign), not something Phase 15 introduced or could fix without changing
how donations are recorded platform-wide. Stated honestly here per the spec's
instruction rather than silently accepted or hidden.

---

## 6. Ownership rules

`donation.donor_user_id != user.id` → `404` on issuance (never distinguishes
"doesn't exist" from "exists but isn't yours" — same non-enumerating pattern used
throughout this app). `cert.donor_user_id != user.id and user.role != ADMIN` →
`404` on `GET /certificates/{id}` (audited as `certificate.access_denied`).
Download requires ownership, `ADMIN`, or a valid signed token bound to that exact
certificate. All confirmed by both the pre-existing suite
(`test_ownership_and_role_authorization`) and this phase's additions (sections 7,
10, 11).

---

## 7. Generation authorization

Only `DONOR` may call `POST /certificates` (`require_roles(UserRole.DONOR)`).
Evaluated and confirmed denied for every other role in this repository's actual
domain: `PATIENT` (403), `FAMILY` (403), `HOSPITAL` (403) — new test
`test_family_patient_hospital_cannot_access_donor_certificates` exercises all
three directly, including a `FAMILY` user holding a real, `ACCEPTED` link to an
actual patient (Phase 13's only kind of family relationship — see section 21).
`ADMIN` was deliberately **not** given a self-service "generate on behalf of"
endpoint — no such route exists, and none was added; admins can inspect the
existing registry (section 19) but do not fabricate certificates for donors.

---

## 8. Duplicate-generation policy

**One certificate per donation**, enforced at the database layer:
`Certificate.donation_id` has a `unique=True` constraint (pre-existing). Calling
issuance twice for the same donation is intentionally idempotent — the second
call returns the identical, already-created certificate (same `id`), never a
second row — confirmed by the pre-existing
`test_issue_certificate_is_idempotent_and_persisted` and re-confirmed under
genuine concurrency by this phase's new test (section 9). This is a documented,
intentional design choice reusing the DB's own uniqueness guarantee, not a
newly-invented rule.

---

## 9. Concurrency handling (real bug found and fixed)

**Before this phase**, `issue_certificate` fetched the `Donation` with a bare
`db.get()` and `get_or_issue()` used a plain check-then-create sequence with no
lock: two near-simultaneous issuance requests for the *same* `donation_id` could
both observe "no certificate exists yet," both render a PDF and upload it to
storage, and then race to insert — the loser would hit `Certificate`'s
`unique(donation_id)` constraint as an **unhandled `IntegrityError`**, surfacing
to the caller as a raw `500` rather than the correct idempotent `201`.

This was proven, not assumed: the new test
`test_concurrent_certificate_generation_for_same_donation_yields_exactly_one` was
run five times against the pre-fix code and **failed every time**; against the
fix, it was run five more times and **passed every time**.

**Fix**: `issue_certificate` now locks the `Donation` row
(`db.get(Donation, id, with_for_update=True)`) for the duration of the request.
The second concurrent caller blocks until the first commits, then
`get_or_issue`'s own existing-row check finds the now-persisted certificate and
returns it — no duplicate insert is ever attempted, and the loser does no wasted
PDF-render/storage-upload work at all (unlike a catch-and-retry approach, which
would still waste that work). This mirrors the same `SELECT ... FOR UPDATE`
locking idiom already established in Phases 10, 12, 13, and 14.

---

## 10. Certificate identifier design

Two distinct identifiers, audited for their actual purposes:
- **`id` (UUID, primary key)** — used for authenticated/admin routes
  (`GET /certificates/{id}`, `.../download`). Non-sequential (UUIDv4), and access
  is authorization-gated regardless of guessability.
- **`verify_code`** (`secrets.token_urlsafe(16)`, 128 bits) — the actual public
  lookup key for `GET /certificates/verify/{code}`. Intentionally public
  (anyone who has a certificate is meant to be able to prove it's real), but
  non-guessable: 2^128 possibilities, cryptographically random, never derived
  from `serial`, `id`, or any sequential counter.
- **`serial`** (`LL-{year}-{8 hex chars}`) — a human-readable label printed on
  the certificate, **not** used for any lookup or authorization anywhere in the
  codebase (confirmed by grep: `serial` is never used in a `WHERE` clause for
  access control, only `verify_code` and `id` are). No change was needed or made
  — the existing design was already sound.

---

## 11. Enumeration analysis

- `verify_code`: 128 bits of randomness, confirmed non-sequential across
  back-to-back issuances by the pre-existing suite's structure and re-exercised
  here; a guessed/incremented code returns a clean `404` with no information
  about whether "close" guesses are "warmer."
- `id` (UUID): guessing a real UUID does not help an attacker — `GET
  /certificates/{id}` still enforces ownership/admin, and `.../download` still
  requires a valid signed token or ownership/admin.
- Cross-certificate token reuse: a signed download token is cryptographically
  bound to the specific `cert_id` it was minted for (the HMAC signs
  `f"{cert_id}:{exp}"`), so a token obtained for certificate A cannot be replayed
  against certificate B — proven directly by new test
  `test_download_token_is_bound_to_its_own_certificate` (a real token for A,
  submitted against B's download endpoint, is rejected with `401`).
- No malformed-ID crash risk: `certificate_id` path parameters are typed
  `uuid.UUID` by FastAPI, so a non-UUID value is rejected with a clean `422`
  before any application code runs; `verify_code`/`code` is an untyped string, so
  any malformed value simply fails to match a row and returns `404` — neither
  path can reach a raw exception.

---

## 12. Public verification design

`GET /certificates/verify/{code}` (unauthenticated) returns exactly:
`valid, serial, donor_name, donated_on, units, hospital_name, issued_at` — no
`id`, `donor_user_id`, `verify_code`, `storage_key`, email, phone, address, or any
admin-only field. Confirmed both by the pre-existing test and this phase's new
`test_public_verification_json_has_no_private_fields`, which additionally asserts
a fixed forbidden-key set (`email`, `phone`, `storage_key`, `verify_code`, `id`,
`donor_user_id`, `donation_id`, `reviewer_user_id`, `review_note`, `address`) is
disjoint from the actual response — inspecting the real JSON payload directly,
not the frontend's rendering of it.

---

## 13. Privacy model

Minimum-necessary-to-prove-validity was the standing design and remains so: a
verifier learns who donated, when, how much, and where — enough to trust the
certificate — and nothing about how to contact or otherwise locate the donor.
The generated PDF embeds exactly this same field set (donor name, serial, verify
code/URL, donation date, units, hospital name) — confirmed directly by rendering
a real certificate and asserting the donor's own email does not appear anywhere
in the resulting PDF bytes
(`test_pdf_contains_only_intentionally_public_fields`), not merely by reading the
`render_pdf()` function signature.

---

## 14. PDF generation

ReportLab-rendered (`app/services/certificate_service.py::render_pdf`): a
bordered A4-landscape certificate with the donor's name, a donation detail line
(units/date/hospital), the serial, a "Verify at: {frontend_base_url}/verify/
{code}" text line, and a real embedded QR code (via
`reportlab.graphics.barcode.qr.QrCodeWidget`) encoding that exact same verify
URL — not a decorative placeholder. QR rendering failure is caught and treated as
non-fatal (`except Exception: pass` around the QR block only — a rendering
environment without full QR support still produces a valid certificate PDF with
the human-readable verify URL, just without the graphic). No change was made
here; audited and confirmed already correct.

---

## 15. PDF storage

Same private, S3/MinIO-with-local-fallback abstraction hardened in Phase 14
(`app/core/storage.py`). Key scheme: `certificates/{donor_id}/{cert_id}.pdf` —
both segments are server-generated UUIDs, never a predictable
`/certificates/1.pdf`-style sequential path, and there is no direct
filesystem/bucket exposure — every read goes through the authorization-checked
download route. No changes were made to the storage layer itself.

---

## 16. QR architecture

Present and audited, not newly built: a real QR code is embedded in every
generated PDF (section 14), encoding the public verification URL
(`{frontend_base_url}/verify/{verify_code}`) — the same code used by the
"Verify Online"/"Verify Document" links in `DonationCertificate.tsx` and by
`VerifyCertificate.tsx`'s public page. No secret, session token, download token,
or private-document URL is ever encoded in the QR payload — only the public
verify-by-code URL, which itself only ever returns the safe-fields payload
described in section 12. The certificate-detail web page (`DonationCertificate.tsx`)
shows a QR-icon *graphic* for visual styling on-screen (not a scannable code —
the real, scannable QR lives inside the downloadable PDF itself, rendered by
ReportLab); this is a cosmetic distinction, not a fake feature, since the page's
"Verify Online" link next to it already goes to the real, working verification
URL.

---

## 17. Download authorization

`GET /certificates/{id}/download` grants access via exactly one of: (a) a valid
signed token bound to that specific certificate ID (time-limited HMAC,
pre-existing), or (b) an authenticated session where the caller is the
certificate's owner or an `ADMIN`. Every other combination — another donor,
`PATIENT`, `FAMILY` (including one holding a real accepted family link),
`HOSPITAL`, or an anonymous request with no/bad token — is rejected with `401`.
**Explicit confirmation of the spec's named non-negotiable concern**: Phase 13's
family-link authorization does not, and structurally cannot, extend to
certificate access — `FamilyLink` only ever relates a `FAMILY` user to a
`PATIENT` user (never a `DONOR`), and even where a `FAMILY` user holds a real,
`ACCEPTED` link to an actual patient, the certificate routes contain no
family-link-aware code path to grant access through — confirmed directly by test
(`test_family_patient_hospital_cannot_access_donor_certificates`), the same
structural argument already established for verification documents in Phase 14.

---

## 18. Audit events

Reused `audit_service.record(...)` exclusively — no second audit system.
Pre-existing: `certificate.issued`, `certificate.access_denied` (view and
download denial paths). **New in this phase**: `certificate.viewed` (successful,
authorized detail view) and `certificate.downloaded` (successful download, with
`meta.via` = `"owner"` / `"admin"` / `"token"`, and `actor_user_id` left `null`
for anonymous signed-token downloads rather than invented). All confirmed by
direct `AuditLog` table assertions in the new test suite
(`test_certificate_view_and_download_are_audited`), not inferred from route code.
No password, token, secret, or full document content is ever placed in `meta`.

---

## 19. Notification events

Reused `notification_service.notify(...)` — no second system.
`certificate.ready` fires on successful issuance (pre-existing, re-confirmed
unaffected by this phase's locking change). No SMS dependency exists or was
added; certificate generation does not fail or wait on SMS in any way.

---

## 20. API endpoints

No new endpoints were added or needed — every endpoint below already existed;
only `POST /certificates` and `GET /certificates/{id}/download` had their
internal handling hardened (locking, audit, error handling) in this phase.

| Method | Path | Auth | Authz | Side effects |
|---|---|---|---|---|
| GET | `/certificates` | session | `DONOR`, self-scoped | none |
| POST | `/certificates` | session | `DONOR`, must own the donation | creates cert (locked, idempotent); audits `certificate.issued`; notifies `certificate.ready` |
| GET | `/certificates/verify/{code}` | none (public) | none — safe fields only | none |
| GET | `/certificates/{id}` | session | owner or `ADMIN` | audits `certificate.viewed` (new) or `certificate.access_denied` |
| GET | `/certificates/{id}/download` | session or signed token | owner, `ADMIN`, or valid token | audits `certificate.downloaded` (new) or `certificate.access_denied` |
| GET | `/admin/certificates` | session | `ADMIN` | none (read-only registry) |

---

## 21. Frontend changes

- `DonationCertificate.tsx`, `AdminAnalytics.tsx`'s certificate registry: audited,
  confirmed already fully real (no mock arrays, no hardcoded "Verified" text, no
  `setTimeout`-simulated success, no localStorage-only state) — no changes made.
- `VerifyCertificate.tsx`: one wording fix (section 3.5) — no functional change.
- `DonorDashboard.tsx`'s "Get certificate" flow: audited, confirmed real
  (`issueCertificate` → real `POST /certificates`) — no changes made.
- Search of the frontend for certificate-related `mock`/`fake`/`TODO`/`FIXME`/
  `placeholder`/hardcoded-certificate/`setTimeout`-simulation/`localStorage`-only
  state/dummy-PDF-URL patterns: none found in the production certificate flow.
  Nothing was removed, since nothing fake existed to remove.

---

## 22. Database changes

**None.** `Certificate`'s existing schema (unique `donation_id`, unique `serial`,
unique+indexed `verify_code`) already supports every Phase 15 requirement:
one-per-donation is DB-enforced, identifiers are already unique and
non-predictable, and no new field (expiry, revocation, status) was needed because
none of those concepts exist in this system and none were invented (section 24).
**No migration was created** — per the spec's explicit instruction not to create
one merely to increase the phase's apparent size.

---

## 23. Security tests (new file: `tests/test_phase15_certificate_security.py`, 11 tests)

`test_generation_requires_a_real_donation_row_no_client_status_field_exists`,
`test_ineligible_donation_id_is_rejected`,
`test_concurrent_certificate_generation_for_same_donation_yields_exactly_one`,
`test_family_patient_hospital_cannot_access_donor_certificates`,
`test_download_token_is_bound_to_its_own_certificate`,
`test_public_verification_json_has_no_private_fields`,
`test_pdf_contains_only_intentionally_public_fields`,
`test_storage_failure_on_download_is_a_clean_503_not_a_stack_trace`,
`test_certificate_view_and_download_are_audited`,
`test_certificate_issued_audit_still_recorded`,
`test_unauthenticated_access_is_rejected`.

---

## 24. IDOR tests

| Manipulated ID | Actor / target | Result |
|---|---|---|
| `donation_id` (another donor's) | DONOR issuing | `404` (pre-existing, re-confirmed) |
| `certificate_id` (another donor's) | DONOR viewing/downloading | `404` / `401` (pre-existing) |
| `certificate_id` | FAMILY / PATIENT / HOSPITAL | `403`/`404`/`401` across view/download/issue (new, section 17) |
| download token (minted for cert A) | used against cert B | `401` — cryptographically bound to its own `cert_id` (new) |
| `donor_user_id` (spoofed in issuance body) | DONOR | ignored — real caller's ownership is what's checked, not any client-sent id (new) |
| `user_id` implicit in `/certificates` list | any authenticated donor | always self, never another donor's rows (pre-existing) |
| `verify_code` (guessed/incremented) | anyone (public) | `404`, no enumeration signal (pre-existing) |

---

## 25. Concurrency tests

`test_concurrent_certificate_generation_for_same_donation_yields_exactly_one` —
two threads, synchronized with a `threading.Barrier`, simultaneously POST
issuance for the same `donation_id`. **Before the fix**: failed 5/5 runs (an
unhandled `IntegrityError`/500 or a duplicate row). **After the fix**: passed 5/5
runs — exactly one `201` certificate, both callers receiving the identical
certificate id, and the donor's own list showing exactly one row.

---

## 26. Privacy tests

`test_public_verification_json_has_no_private_fields` (inspects the actual JSON
response, asserts an explicit forbidden-key set is absent, not merely that the
frontend doesn't display them) and
`test_pdf_contains_only_intentionally_public_fields` (inspects the actual
rendered PDF bytes for the donor's own email, confirming it is never embedded).

---

## 27. E2E results

Donor logs a real donation → issues a certificate → certificate persisted with a
real PDF in private storage → `certificate.ready` notification created → donor
views it (audited) → donor downloads it (audited, real PDF bytes) → anyone with
the `verify_code` gets a `VALID`, safe-fields-only public result — all exercised
end-to-end by the combination of the pre-existing suite and this phase's new
tests. Ineligible-donation denial (another donor's `donation_id` → `404`) and
cross-tenant denial (`DONOR A`'s certificate → `DONOR B` denied, and the broader
family/patient/hospital sweep) are both covered. QR → scan → public-verify →
valid is covered at the payload level (the QR encodes the exact verify URL that
`VerifyCertificate.tsx`/`GET /certificates/verify/{code}` already serve
correctly) rather than via an actual camera/scanner simulation, since no such
infrastructure exists in this test suite (nor in Phase 14's).

---

## 28. TypeScript result

`npx tsc --noEmit` — clean, no errors.

## 29. ESLint result

`npm run lint` — clean, no errors or warnings.

## 30. Production build result

`npm run build` — succeeded (2166 modules transformed); the only output is the
same pre-existing "chunk larger than 500 kB" advisory, unrelated to this phase.
**Playwright**: not present in this repository (reconfirmed) — not run, and not
claimed to have been run.

Backend: `ruff check` on both changed/new files — 0 errors. `mypy app/` (whole
app) — 3 errors, identical to the Phase 14 baseline (two pre-existing in
`verifications.py::_apply_approval`, one pre-existing in `donors.py:219`) — zero
new errors introduced by this phase's changes.

---

## 31. Known limitations

1. **Self-reported donations are not independently confirmed.** A donor can log
   a `Donation` themselves (`POST /donors/me/donations`) with no hospital
   confirmation, and a certificate can then be issued for it. This is a
   pre-existing property of the donation-logging feature itself, not introduced
   by this phase, and out of scope to redesign here — stated honestly rather than
   silently accepted.
2. **No certificate revocation exists.** Certificates are immutable, permanent
   records of a completed donation; there is no `revoked`/`invalid` state, no
   admin "invalidate" action, and none was added — no genuine business
   requirement for it was found in this repository's domain, and the spec
   explicitly warns against admin-editable certificates without one.
3. **No certificate expiry exists.** A certificate represents a historical fact
   (a donation happened on a given date) rather than a time-bound credential, so
   there is nothing to expire — this mirrors the same "don't invent an arbitrary
   period" reasoning documented for verification expiry in Phase 14.
4. **The on-screen certificate page's QR graphic is decorative**, not a live
   scannable code (the real, scannable QR is embedded only in the downloadable
   PDF) — documented in section 16 rather than left ambiguous.
5. **A rare, harmless storage artifact is possible in theory but was closed in
   practice**: because the fix in section 9 locks the `Donation` row *before*
   any PDF rendering/upload happens (rather than catching a duplicate-insert
   error after the fact), there is no wasted/orphaned PDF object left behind on
   the losing side of a race — unlike the "catch-and-recover" pattern, this
   phase's approach avoids the wasted-storage-object question entirely rather
   than needing to document it as a trade-off.

## 32. Future improvements

- If a future phase wants hospital-confirmed (rather than purely self-reported)
  donations, that would naturally tighten certificate eligibility too — but
  building that donation-confirmation workflow is outside Phase 15's scope.
- A lightweight "reprint"/re-download audit summary on the admin registry page
  (surfacing the new `certificate.viewed`/`certificate.downloaded` events per
  certificate) would be a natural, low-risk extension of the existing read-only
  registry, not built in this phase since it wasn't required.

---

## Final status

==================================================
PHASE 15 FINAL STATUS
==================================================

STATUS: **COMPLETE**

BASELINE: 575 passed, 0 failed, 0 errors

FINAL: 586 passed, 0 failed, 0 errors

NEW TESTS: 11

FAILURES: 0

ERRORS: 0

CERTIFICATE ARCHITECTURE: pre-existing, complete, reused entirely — `Certificate`
model, `certificate_service`, `certificates.py` routes, admin registry, real
frontend pages. No second system created.

CERTIFICATE ELIGIBILITY: a real, caller-owned `Donation` row is the sole,
authoritative eligibility signal (no invented "completed" flag; none exists or
was needed). Client cannot spoof status, donor identity, or eligibility.

OWNERSHIP: `donor_user_id == caller.id` (or `ADMIN`) on every route; non-owner
gets `404`/`401`, never leaked data.

GENERATION: `DONOR`-only; `PATIENT`/`FAMILY`/`HOSPITAL` all confirmed denied,
including a `FAMILY` user with a real accepted patient link.

DUPLICATE POLICY: one certificate per donation, DB-unique-constraint-enforced;
re-issuance is idempotent by design.

CONCURRENCY: real race found and fixed (`Donation` row now locked during
issuance) — proven via a test that failed 5/5 before the fix and passed 5/5
after.

CERTIFICATE IDENTIFIER: `id` (UUID) for authorization-gated routes,
`verify_code` (128-bit random) for public lookup, `serial` cosmetic-only and
never used for access control. Unchanged — already sound.

PUBLIC VERIFICATION: `GET /certificates/verify/{code}`, safe-fields-only,
confirmed by direct JSON inspection with an explicit forbidden-key assertion.

PRIVACY: PDF and public-verify payload both carry only donor name, serial,
donation date/units/hospital, and issue date — confirmed by inspecting actual
PDF bytes for the donor's email (absent).

PDF: ReportLab-rendered, real embedded QR pointing at the public verify URL,
non-fatal QR-rendering fallback.

QR: present (embedded in the PDF); on-screen certificate page's QR icon is
decorative styling only, documented as such.

DOWNLOAD SECURITY: signed token cryptographically bound to its own
`certificate_id` (cross-certificate token reuse proven rejected); ownership/
admin/token are the only three paths in.

IDOR: certificate/donation/donor/user-id manipulation tested across
donor↔donor, family↔donor, patient↔donor, hospital↔donor, non-admin↔admin-registry
— all denied appropriately.

AUDIT: `certificate.issued` (pre-existing), `certificate.access_denied`
(pre-existing), `certificate.viewed` (new), `certificate.downloaded` (new, with
`via` metadata) — all reused via the existing `audit_service`, all confirmed by
direct `AuditLog` assertions.

NOTIFICATIONS: `certificate.ready` (pre-existing, reused, re-confirmed);
no SMS dependency.

DATABASE: no changes; existing schema already sufficient.

MIGRATIONS: none required — the existing certificate schema already supports
every implemented Phase 15 requirement.

FRONTEND: one wording fix (`VerifyCertificate.tsx`); everything else audited and
confirmed already real; TypeScript/ESLint/build all clean; Playwright not
present, not run.

E2E: full issue→view→download→verify chain, plus ineligible/cross-tenant/
family-boundary denial paths, all passing.

TYPESCRIPT: clean (`npx tsc --noEmit`, 0 errors).

ESLINT: clean (`npm run lint`, 0 errors/warnings).

BUILD: succeeded (`npm run build`); only a pre-existing chunk-size advisory,
unrelated to this phase.

FILES CREATED: `tests/test_phase15_certificate_security.py`,
`LIFELINK_PHASE_15_REPORT.md`.

FILES MODIFIED: `app/api/routes/certificates.py`,
`frontend/src/pages/VerifyCertificate.tsx`.

KNOWN LIMITATIONS: self-reported donations are not hospital-confirmed
(pre-existing, out of scope); no certificate revocation exists; no certificate
expiry exists; the on-screen certificate page's QR is a decorative graphic, not
a live scanner target (the PDF's QR is real) — all stated explicitly, none
silently assumed or fabricated.

COMMANDS EXECUTED:
```
sudo -u postgres pg_ctlcluster 16 main start
redis-server --daemonize yes

source .venv/bin/activate
ENVIRONMENT=test SECRET_KEY=test-secret RQ_SYNC=true pytest -q            # baseline: 575 passed
ENVIRONMENT=test SECRET_KEY=test-secret RQ_SYNC=true pytest -q tests/test_phase15_certificate_security.py -v
ENVIRONMENT=test SECRET_KEY=test-secret RQ_SYNC=true pytest -q tests/test_certificates.py tests/test_admin_phase6.py
ENVIRONMENT=test SECRET_KEY=test-secret RQ_SYNC=true pytest -q            # final: 586 passed
ruff check app/api/routes/certificates.py tests/test_phase15_certificate_security.py
mypy app/

cd ../frontend
npx tsc --noEmit
npm run lint
npm run build
```
