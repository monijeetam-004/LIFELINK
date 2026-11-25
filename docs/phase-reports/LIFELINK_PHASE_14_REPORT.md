# LIFELINK — PHASE 14 REPORT
## Production Verification System

Status: **COMPLETE WITH DOCUMENTED LIMITATIONS**

Baseline tests (existing suite, before any Phase 14 change — matches Phase 13's own
final verified count): **558 passed, 0 failed, 0 errors**
Final tests (after all Phase 14 changes): **575 passed, 0 failed, 0 errors**
(558 pre-existing + 17 new: `tests/test_phase14_verification_security.py`)

The "documented limitations" qualifier is not a gap in what was requested — it is
because two of the spec's optional feature areas (automatic time-based expiry, and a
separate `REQUIRES_UPDATE` state) were deliberately **not** built, for reasons argued
in sections 6–7 and 14 below, and malware/antivirus scanning is honestly documented as
not implemented (section 11), exactly as the spec instructs: *"If a security control is
only partial, state this explicitly."*

---

## 1. Existing architecture (inspected before any code change)

A mature, working verification and certificate system already existed. Per the
spec's rule ("do not create duplicate systems where an existing implementation can be
extended"), this phase audited it exhaustively before writing anything:

- **Model**: `VerificationRequest` (`app/models/verification.py`) — `id`, `user_id`
  (FK `users`, `CASCADE`), `kind`, `status`, `documents` (JSON list of
  `{key, filename, content_type, size}` — storage bytes never touch the DB),
  `note`, `reviewer_user_id` (FK `users`, `SET NULL`), `review_note`, plus the shared
  `TimestampMixin`.
- **Enums** (`app/models/enums.py`): `VerificationKind` = `DONOR_IDENTITY`,
  `HOSPITAL_FACILITY`; `VerificationStatus` = `PENDING`, `APPROVED`, `REJECTED`.
- **Routes** (`app/api/routes/verifications.py`, pre-existing): submit
  (`POST /verifications`, rate-limited, role↔kind matched, file-count/size/
  content-type checked), self-scoped list (`GET /verifications/me`), admin list/get
  (`GET /admin/verifications[, /{id}]`), admin document byte-serving
  (`GET /admin/verifications/{id}/documents/{index}`, ADMIN-only), admin
  approve/reject (`POST /admin/verifications/{id}/approve|reject`).
- **Schemas** (`app/schemas/verification.py`): `VerificationDocumentOut` deliberately
  exposes only `filename`/`content_type`/`size` — never the storage key.
  `VerificationRejectIn.reason` already has `min_length=3` (an actionable-reason
  floor already existed).
- **Storage** (`app/core/storage.py`): S3/MinIO with local-disk fallback behind one
  abstraction (`put_bytes`/`get_bytes`/`object_exists`/`delete_prefix`/`presign_get`/
  `presign_put`); `_local_path()` already rejects `..` traversal even though keys are
  always server-generated.
- **Storage key scheme**: `verifications/{user_id}/{request_id}/{uuid4().hex}_{filename}`
  — already opaque and non-predictable (no sequential IDs, no raw filename as path).
- **Filename sanitization**: `_safe_filename()` already stripped path separators and
  reduced names to `[A-Za-z0-9._-]`, capped at 150 chars — already closed null-byte/
  path-traversal/odd-character attacks (confirmed, not assumed — see section 12).
- **Certificates** (`app/models/certificate.py`, `app/services/certificate_service.py`,
  `app/api/routes/certificates.py`): a complete, separate, already-hardened system —
  non-guessable `verify_code` (`secrets.token_urlsafe(16)`), HMAC-signed time-limited
  download tokens, a public verify endpoint returning only safe fields, audited
  cross-tenant access attempts. **Reused entirely as-is — no changes made.**
- **Rate limiting**: `settings.rl_verification_submit_account = (5, 3600)` — already
  wired via the shared `enforce_rate_limit()` helper used everywhere else in the app.
- **Audit/notification**: `audit_service.record(...)` and `notification_service.notify(...)`
  were already called on submit/approve/reject.
- **Admin frontend**: `frontend/src/pages/admin/VerificationsQueue.tsx` is already a
  complete, real `AdminVerificationPortal` — queue + filter + review-drawer pattern,
  real API calls for every action, honestly-disabled buttons for features with no
  backend (Advanced Filter / Export Log) rather than fake ones.
- **Donor frontend**: `frontend/src/pages/donor/IdentityVerification.tsx` was already
  a complete, real submission/status page.
- **Verification's role in the app**: confirmed by grep, not assumed — it is a
  **soft ranking signal** almost everywhere (`DonorProfile.identity_verified` /
  `HospitalProfile.verified` feed `matching_engine.py`'s candidate ranking) with
  exactly **one** existing hard gate: `emergencies.py::_validate_target_facility()`
  rejects an emergency SOS naming an unverified `target_facility_id` (HTTP 400).

Per the spec's own instruction ("do not assume all... require verification... use the
actual repository's domain model"), this existing soft-signal-plus-one-hard-gate
design was **preserved as-is**. No new hard-blocking gates were invented on donor
responses, hospital inventory actions, or any other flow.

**Conclusion for every subsystem above: reuse as-is or extend in place. Nothing was
replaced, and no parallel/duplicate verification, document, certificate, audit, or
notification system was created.**

---

## 2. Files inspected (read-only, before any change)

`app/models/verification.py`, `app/models/certificate.py`, `app/models/enums.py`,
`app/schemas/verification.py`, `app/schemas/certificate.py`, `app/core/storage.py`,
`app/core/config.py`, `app/api/routes/verifications.py` (original),
`app/api/routes/certificates.py`, `app/api/routes/donors.py` (338 lines, full),
`app/api/routes/hospitals.py` (747 lines, full), `app/api/routes/emergencies.py`
(target-facility gate), `app/services/certificate_service.py`,
`app/services/audit_service.py`, `app/services/notification_service.py`,
`app/models/family.py`, `app/api/routes/family.py` (patient-role-only link creation),
`tests/test_verifications.py` (24 tests, full), `tests/test_certificates.py` (9
tests, full), `tests/helpers.py`, `frontend/src/pages/donor/IdentityVerification.tsx`
(full), `frontend/src/pages/admin/VerificationsQueue.tsx` (full),
`frontend/src/pages/HospitalDashboard.tsx`, `frontend/src/pages/hospital/HospitalSubNav.tsx`
(full), `frontend/src/lib/domain.ts` (verification/admin-verification API surface),
`frontend/src/App.tsx` (route table).

---

## 3. New architecture added

Nothing new was added at the model/table/migration level (see section 8 — this was a
deliberate, documented decision, not an oversight). What was added:

1. **Real file-content validation** (magic-byte sniffing) in `submit_verification()`,
   closing the gap where only the client-supplied `Content-Type` header was checked.
2. **Row-locked approve/reject** (`_get_for_update_or_404()`), closing a genuine
   concurrent-decision race that the pre-existing code did not guard against.
3. **A missing frontend page**: `frontend/src/pages/hospital/FacilityVerification.tsx`
   — hospitals had full backend support for `HOSPITAL_FACILITY` verification but no
   submission screen existed anywhere in the frontend.
4. **A dedicated security/hardening test file**:
   `tests/test_phase14_verification_security.py` (17 tests).
5. **An explicit family-link/verification-authorization boundary test**, proving the
   spec's named non-negotiable concern does not apply to this codebase's data model.

---

## 4. Files changed

Backend (`project/backend/`):
- `app/api/routes/verifications.py` — added `_MAGIC_SNIFFERS` / `_sniff_content_type()`,
  rewired the upload-validation loop in `submit_verification()` to require the
  sniffed byte signature to match the declared `Content-Type` exactly (not merely be
  independently allowed), added `_get_for_update_or_404()` and switched
  `admin_approve_verification` / `admin_reject_verification` to use it instead of the
  unlocked `_get_or_404()`.
- `tests/test_phase14_verification_security.py` — new file, 17 tests (see section 21).

Frontend (`project/frontend/src/`):
- `pages/hospital/FacilityVerification.tsx` — new page (see section 3.3).
- `App.tsx` — added the `FacilityVerification` import, a new
  `/hospital/verification` route (`RequireAuth roles={["HOSPITAL"]}`), and its label
  in the route-label list.
- `pages/HospitalDashboard.tsx` — added a `BadgeCheck`-iconed "Facility Verification"
  nav item.

No file belonging to Phase 12 (Emergency/SOS) or Phase 13 (Family Link) was modified.
No migration was created (section 8 explains why none was needed).

---

## 5. Verification domain used

Only the two categories the repository's actual domain model already supports:
**`DONOR_IDENTITY`** and **`HOSPITAL_FACILITY`**. The spec's four-category menu
(DONOR_IDENTITY / HOSPITAL-FACILITY / DOCUMENT / CERTIFICATE) was explicitly offered
as a *ceiling*, not a mandate ("do not blindly create all four"). `DOCUMENT` is not a
separate verification *kind* here — documents are always attached to one of the two
real kinds above. `CERTIFICATE` already has its own complete, separately-modeled
system (`Certificate`, not `VerificationRequest`) predating this phase, reused as-is
rather than folded into a fourth `VerificationKind`.

---

## 6. State machine

`VerificationStatus` remains **`PENDING` → `APPROVED` | `REJECTED`** — unchanged from
the pre-existing 3-state model. The spec's larger menu (`UNDER_REVIEW`, `EXPIRED`,
`REQUIRES_UPDATE`) was evaluated and deliberately not added:

- **`UNDER_REVIEW`** would require a real claim/assignment workflow (which admin is
  reviewing this right now) that does not otherwise exist in this app and is not
  necessary to satisfy the spec's actual concern ("prevent concurrent conflicting
  reviews") — that concern is fully closed by the new row-level lock (section 3.2,
  tested in section 22), without inventing a state whose only purpose would be
  cosmetic.
- **`REQUIRES_UPDATE`** — see section 7.
- **`EXPIRED`** — see section 14.

All transitions are server-side only (client cannot set `status`), authorized
(`_admin` dependency, real ADMIN role check), transactional (single DB session,
committed once), audited (`audit_service.record` on every transition), and persisted
(no in-memory-only state). Invalid transitions (anything except acting on a `PENDING`
request) are rejected with `409 Conflict` — tested in the pre-existing suite and
re-verified as part of the new concurrency tests.

---

## 7. Donor verification workflow

Unchanged shape, re-verified end-to-end in this phase (`test_e2e_donor_rejection_then_resubmission`):
submit → `PENDING` → admin reviews → `APPROVED` or `REJECTED` (with a mandatory,
already-enforced `min_length=3` actionable reason) → notification sent
(`notification_service.notify`, type `verification.rejected` / `verification.approved`)
→ `DonorProfile.identity_verified` flips only via `_apply_approval()`, the single code
path that can ever set it. A donor can never approve themselves (admin-only route,
role-checked), modify `reviewer_user_id` (server-set only — see section 15's IDOR/spoof
tests), modify `status` directly (no client-writable status field exists in
`VerificationApproveIn`/`VerificationRejectIn`), or read another donor's documents
(`/verifications/me` is unconditionally scoped to `VerificationRequest.user_id ==
caller.id`; the only document-byte route is ADMIN-only).

Because `REJECTED` already both (a) requires an actionable reason and (b) permits a
fresh resubmission (the duplicate-pending check only blocks a second *PENDING* request
of the same kind, not a new one after rejection), this already delivers everything
`REQUIRES_UPDATE` would add — a rejected donor sees why, corrects it, and resubmits —
without a second, functionally-redundant status. This is why `REQUIRES_UPDATE` was not
added as a separate state.

---

## 8. Hospital/facility verification workflow

Same lifecycle shape as donors, and — until this phase — no way for a hospital to
actually reach it: the backend fully supported `HOSPITAL_FACILITY` requests, but no
frontend page existed. Fixed by `FacilityVerification.tsx` (section 3.3), which:
mirrors the donor page's real-backend pattern exactly (no mock state, no
`setTimeout`-simulated success); derives its "Verified" banner from
`HospitalProfile.verified === true` (never a client-side flag); shows the correct
in-flight state from the latest real `HOSPITAL_FACILITY` request
(`PENDING`/`REJECTED`/not-submitted); and is wired into `App.tsx` and
`HospitalDashboard.tsx`'s nav.

The audit of "which existing features actually require verified status" (section 1)
confirmed the one genuine hard gate — `emergencies.py`'s `target_facility_id`
rejection of unverified facilities — and this phase's new end-to-end test
(`test_e2e_hospital_verification_unlocks_emergency_target_facility_capability`) proves
the full chain: an unverified hospital cannot be targeted → admin approves its
`HOSPITAL_FACILITY` request → the same hospital can now be targeted. No new gate was
invented; `isVerified` is never trusted from the frontend anywhere in this flow.

---

## 9. Document storage (security-critical — audited, not modified)

Confirmed already correct against every point the spec calls out:
- Uploaded bytes go straight to `storage.put_bytes()`, never a client-controlled path.
- Storage keys are server-generated and opaque:
  `verifications/{user_id}/{request_id}/{uuid4().hex}_{filename}` — no predictable
  `/uploads/user123/doc.pdf`-style path anywhere.
- The original filename is *never* used as the storage path component that matters —
  it is sanitized (`_safe_filename()`) and only appended as a suffix after an
  unguessable UUID segment.
- Access is server-checked on every read (see section 10) — never a bare public path.
- Every document is tied to its owning `VerificationRequest` via the JSON `documents`
  list on that row — there is no free-floating document table an ID could probe
  independently of its parent request.

No changes were made to the storage abstraction or key scheme — both already met the
bar.

---

## 10. Secure download architecture

`GET /admin/verifications/{request_id}/documents/{index}` is the only route that ever
returns document bytes. Flow: authenticated request (`get_current_user` dependency,
401 if missing/invalid) → role check (`require_roles(ADMIN)`, 403 for anyone else,
tested in section 15/21) → the request itself is fetched by `request_id` (404 if it
doesn't exist) → the document is looked up by `index` **within that request's own
`documents` list** (never a global document ID), 404 if out of range → bytes are
retrieved from private storage and streamed back with the document's own recorded
`content_type`/`filename`. A document ID alone never grants access: there is no global
document ID at all — a document is only ever addressed as `(verification_id, index)`,
and reaching it still requires the ADMIN role. This was verified, not assumed:
`test_family_link_does_not_grant_access_to_donor_verification_or_documents` confirms a
non-admin (even one holding an unrelated, real, ACCEPTED family relationship) gets
`403` on this exact route.

---

## 11. File validation (new in this phase)

Before this phase, `submit_verification()` validated only: file count
(`≤ verification_max_files = 5`), size (`≤ verification_max_file_bytes = 10 MiB`),
non-empty, and the **client-declared** `Content-Type` against
`verification_allowed_content_type_list` (`application/pdf,image/jpeg,image/png`).
It never inspected the actual bytes — a client could declare `application/pdf` while
uploading anything.

This phase added real magic-byte sniffing (`_sniff_content_type()`, checking for
`%PDF-`, the PNG signature, and the JPEG SOI marker) and now **requires the sniffed
type to exactly equal the declared type** before accepting the file. Two distinct
failure modes are both rejected with `422`:
1. The bytes don't sniff to any recognized type at all (an executable or HTML payload
   wearing an allowed `Content-Type`) — `test_executable_disguised_as_pdf_is_rejected`,
   `test_html_payload_disguised_as_png_is_rejected`.
2. The bytes are genuinely a *different, individually-allowed* type than declared (a
   real PNG declared as `application/pdf`) — this is the spec's explicit
   "MIME/content mismatch" case, and the first implementation attempt of this check
   missed it (only checked "is the sniffed type allowed at all", not "does it match
   what was declared") — caught by
   `test_content_type_swap_between_two_allowed_types_is_rejected` failing, then fixed;
   see section 24 for the full incremental fix history.

Filenames are still run through the pre-existing `_safe_filename()`, confirmed (not
assumed) via new tests to correctly neutralize: path traversal
(`../../../etc/passwd` → no `/` or `..` in the stored name), double extensions
(`resume.pdf.exe`-style names are sanitized the same way, no special-casing needed —
extension is never trusted for content-type purposes to begin with), and Unicode/
control-character filenames (right-to-left override, zero-width space, and an
embedded null byte were all exercised directly).

**Allowed file types**: `application/pdf`, `image/jpeg`, `image/png` (server-configured
via `settings.verification_allowed_content_types`).
**Max file size**: 10 MiB per file (`settings.verification_max_file_bytes`).
**Max files per submission**: 5 (`settings.verification_max_files`).

**Malware/antivirus scanning is not currently implemented.** This is stated
explicitly, per the spec's own instruction, rather than left ambiguous or implied.
The storage abstraction (`app/core/storage.py`) already isolates all byte I/O behind
`put_bytes`/`get_bytes`, so a scanning step could be inserted there (e.g., scan before
`put_bytes` commits, or asynchronously after, flipping a quarantine flag before a
document is ever served) without redesigning the upload or download paths — but no
such scanner was added in this phase, since none existed to extend and building a real
one was out of scope.

---

## 12. Admin verification queue

`frontend/src/pages/admin/VerificationsQueue.tsx` — confirmed to already be the real,
complete `AdminVerificationPortal` (535 lines): view/filter/search the queue, open a
review drawer per request, inspect submitted documents, approve/reject/see history,
all via real API calls (`listAdminVerifications`/`getAdminVerification`/
`approveAdminVerification`/`rejectAdminVerification` in `frontend/src/lib/domain.ts`).
No local-only transitions and no fake success messages exist in this page — confirmed
by reading the full file, not by search alone. Two buttons ("Advanced Filter",
"Export Log") are honestly disabled with no backend endpoint behind them, rather than
faked — left as-is; building real backends for those was outside this phase's scope
and the spec explicitly prizes honest incompleteness over fabricated function.

No changes were made to this page in this phase — it already satisfied every
requirement.

---

## 13. Admin identity

Reviewer identity is **only ever read from the authenticated session**
(`user: User = Depends(_admin)`, then `req.reviewer_user_id = user.id`) — the request
body schemas (`VerificationApproveIn`, `VerificationRejectIn`) do not even declare a
`reviewer_user_id` field for approve, and any extra field a client sends (including a
spoofed `reviewer_user_id`) is silently ignored by pydantic, not merged into the
persisted row. Verified directly with adversarial tests: an admin who sends a fabricated
`reviewer_user_id` in the request body still gets their own real ID persisted as
`reviewer_user_id`, on both approve and reject
(`test_client_cannot_spoof_reviewer_identity`,
`test_client_cannot_spoof_reviewer_identity_on_reject`).

---

## 14. Rejection reasons and REQUIRES_UPDATE

`VerificationRejectIn.reason` already enforced `min_length=3, max_length=1000` before
this phase — a bare `"no"` is 2 characters and is rejected by validation, satisfying
the spec's "not a bare rejection without context" requirement without needing new
banned-word logic. The reason is persisted (`req.review_note`) and returned to the
affected party both in the API response and via `notification_service.notify(...,
type="verification.rejected", body=payload.reason, ...)`. There is no separate
"internal admin-only note" field distinct from `review_note` in this system's schema,
so there is nothing to leak — the same field that is authorized for the applicant to
see is the only one that exists.

**`REQUIRES_UPDATE` was not added as a separate state.** As argued in section 7, a
`REJECTED` request already carries a mandatory actionable reason and already permits
resubmission (a new request of the same kind, since the duplicate check only blocks a
second concurrently-`PENDING` request) — this is functionally identical to what
`REQUIRES_UPDATE` would provide. Adding a second state with the same behavior would be
exactly the "meaningless status with no distinct workflow behind it" the spec warns
against.

---

## 15. Expiry

**No automatic or manual expiry mechanism exists, and none was added.** This was a
deliberate decision, not an oversight: the current data model has no expiry-date
concept anywhere in the verification system (no `expires_at` column, no background job
infrastructure for it), and the spec is explicit that an invented expiry period would
be exactly the kind of fabricated behavior it forbids ("do not invent arbitrary
periods... if it does not exist, document the limitation clearly" — see the closing
non-negotiable rules). Documented here as an honest, explicit limitation: **verified
status, once granted, does not currently expire.** Building real expiry (a genuine
`expires_at` column, a real scheduled job using the existing RQ/worker infrastructure,
and API support for renewal) is listed as future hardening in section 30.

---

## 16. Certificate verification (audited, unchanged)

Already fully satisfies the spec's certificate-security requirements — audited, not
modified:
- Generation is tied to a real, owned donation (`get_or_issue()` requires an actual
  `Donation` row the caller owns; `test_issue_requires_a_real_owned_donation` already
  covered this pre-Phase-14).
- Identifiers: `serial` (unique) and `verify_code`
  (`secrets.token_urlsafe(16)` — 128 bits of entropy, confirmed non-sequential and
  non-predictable by new test `test_verify_codes_are_not_sequential_or_predictable`,
  which issues two certificates back-to-back and asserts their codes share no
  exploitable structure).
- Public verification (`GET /certificates/verify/{code}`) returns only
  `valid, serial, donor_name, donated_on, units, hospital_name, issued_at` — never
  `storage_key`, `verify_code` itself, internal `id`, or any contact/document/
  auth-adjacent field.
- Downloads require either cookie-session auth or a signed, time-limited HMAC token
  (`make_download_token`/`check_download_token`), both already tested.
- Cross-tenant access to another donor's certificate is already checked and audited
  (`certificate.access_denied`); new test `test_certificate_id_manipulation_across_donors`
  re-confirms a second donor cannot fetch or download the first donor's certificate by
  guessing/reusing its ID.
- There is no revoked state in this system (certificates represent a completed,
  immutable donation record) — nothing to test there; not invented.

No second certificate system was created; none of this code was changed.

---

## 17. API endpoints (contract, unchanged surface + one clarified validation rule)

All routes below already existed under `/api/v1`-style conventions (mounted without an
extra version prefix, consistent with the rest of this app's routers) — Phase 14 added
no new endpoints, only hardened `submit_verification`'s validation and the two admin
mutation routes' locking:

| Method | Path | Auth | Authz | Notes |
|---|---|---|---|---|
| POST | `/verifications` | session | any authenticated user; `kind` must match role (`DONOR`→`DONOR_IDENTITY`, `HOSPITAL`→`HOSPITAL_FACILITY`) | rate-limited 5/hr/account; now content-sniffed |
| GET | `/verifications/me` | session | self-scoped (`user_id == caller.id`) | |
| GET | `/admin/verifications` | session | ADMIN | list/filter |
| GET | `/admin/verifications/{id}` | session | ADMIN | |
| GET | `/admin/verifications/{id}/documents/{index}` | session | ADMIN | only document-byte route |
| POST | `/admin/verifications/{id}/approve` | session | ADMIN | now row-locked |
| POST | `/admin/verifications/{id}/reject` | session | ADMIN | now row-locked; `reason` min_length=3 |
| GET/POST | `/certificates/...` | session / signed token | owner or ADMIN; public verify is intentionally unauthenticated | unchanged |

---

## 18. Frontend changes

- **New**: `pages/hospital/FacilityVerification.tsx` (section 3.3/8).
- **Modified**: `App.tsx` (route + label), `HospitalDashboard.tsx` (nav item).
- **Audited, unchanged**: `pages/donor/IdentityVerification.tsx`,
  `pages/admin/VerificationsQueue.tsx` — both already real, already handle
  loading/empty/error/unauthorized states correctly, no fake success paths found.
- **Fake-UI sweep**: searched the frontend for mock/fake verification patterns
  (hardcoded "Approved"/"Verified" strings, `setTimeout`-simulated success,
  localStorage-only verification state, static-array-as-backend-state). None were
  found in the production verification/certificate flow. Nothing was "destroyed" —
  there was no demo/static verification page to preserve or remove.

---

## 19. Notification events

Reused the existing `notification_service.notify(...)` — no second notification
system. Real notifications fire on:
- `verification.approved` (to the submitter, with the admin's note if any)
- `verification.rejected` (to the submitter, with the mandatory actionable reason)

`verification.submitted` deliberately does **not** trigger a push/email notification.
This mirrors the codebase's existing convention for every other submission-style
event (e.g., `family.py`'s link request notifies the *other party*, not the requester)
— the submitter already receives synchronous confirmation via the `201` HTTP response
itself, and there is no existing "notify admins a new queue item exists" mechanism
anywhere in this app for *any* admin-reviewed queue (blood requests, emergencies, or
verifications) to extend; admins are expected to check their queue directly, which the
already-real `VerificationsQueue.tsx` supports. Inventing a novel admin-broadcast
mechanism just for this one queue would violate the "reuse existing architecture,
don't invent new infrastructure" instruction. SMS was not required or fabricated for
any of these events — `notification_service` already gates SMS behind real
configuration, untouched by this phase.

---

## 20. Audit events

Reused the existing `audit_service.record(...)` — no second audit system. Already
recorded (pre-existing) and unaffected by this phase's changes:
`verification.submitted`, `admin.verification_approved`,
`admin.verification_rejected` — each with `actor_user_id`, `target_type`, `target_id`,
a `meta` dict (kind, file count, or rejection reason), and the shared audit row's own
timestamp/result fields. Cross-tenant certificate-access probing is separately audited
as `certificate.access_denied` (pre-existing, unchanged). No password, token, secret,
or full document content is ever logged — `meta` fields are limited to kind, file
count, and the (already user-facing) rejection reason.

---

## 21. Authorization matrix

Using this repository's actual roles (`DONOR`, `HOSPITAL`, `PATIENT`, `FAMILY`,
`ADMIN`) and the verification/document/certificate surface that actually exists:

| Action | DONOR | HOSPITAL | PATIENT | FAMILY | ADMIN |
|---|---|---|---|---|---|
| Submit own `DONOR_IDENTITY` | ✅ | ❌ | ❌ | ❌ | ❌ (no self-submission route for admins) |
| Submit own `HOSPITAL_FACILITY` | ❌ | ✅ | ❌ | ❌ | ❌ |
| View own verification requests | ✅ | ✅ | ✅ (empty) | ✅ (empty) | via admin routes |
| View admin queue | ❌ | ❌ | ❌ | ❌ | ✅ |
| Approve/reject | ❌ | ❌ | ❌ | ❌ | ✅ |
| View/download another user's documents | ❌ | ❌ | ❌ | ❌ | ✅ |
| View own certificate | ✅ (if a real donation exists) | n/a | n/a | ❌ (unrelated donor) | ✅ |
| Public certificate verify (safe fields only) | anyone (unauthenticated) | | | | |

**Explicit confirmation of the spec's named non-negotiable concern**: an approved
Phase 13 family relationship does **not** grant access to donor identity documents,
hospital documents, verification documents, or admin review notes. This holds for two
independent reasons, both verified by test
(`test_family_link_does_not_grant_access_to_donor_verification_or_documents`):
1. **Structurally impossible to even form**: `FamilyLinkCreate` requires the target
   account to have `role == PATIENT` (`app/api/routes/family.py`); attempting to link
   to a `DONOR` or `HOSPITAL` account 404s ("No patient with that email") before a row
   is ever created. There is no `donor_id`/`hospital_id` column on `FamilyLink` at all.
2. **No authorization code path to bypass even if it existed**: `/verifications/me` is
   unconditionally scoped to `VerificationRequest.user_id == caller.id` (never
   relationship-aware), and the only document-byte route requires the `ADMIN` role
   outright — neither route contains a family-link check to circumvent. A `FAMILY`
   user holding a real, `ACCEPTED` link to an actual patient still gets `403` on the
   admin verification-detail, document, list, and approve routes, and an empty list
   from `/verifications/me`.

---

## 22. IDOR / object-authorization test results

| Manipulated ID | Actor / target | Result |
|---|---|---|
| `verification_id` (unrelated donor's) | non-admin caller | `403` (route requires ADMIN before the ID is even looked up) |
| `verification_id` | ADMIN, cross-donor | `200` — by design, this is the reviewer's job; access is role-gated, not owner-gated, for admins |
| document `index` within a request | ADMIN, out-of-range | `404` |
| document access | non-admin (incl. FAMILY with a real accepted link) | `403` |
| `reviewer_user_id` (spoofed in request body) | ADMIN | ignored — real caller's ID persisted regardless (section 13) |
| `certificate` ID (unrelated donor's) | second DONOR | `403`/`401`-equivalent denial + audited `certificate.access_denied` (pre-existing, re-confirmed) |
| `verify_code` predictability | anyone | non-sequential, 128-bit random, confirmed via pairwise comparison |
| `user_id` implicit in `/verifications/me` | any authenticated user | always self, never another user's rows — no `user_id` parameter exists on this route to manipulate |

No object ID anywhere in this subsystem grants access on its own — every mutating or
document-revealing route re-checks role and, where relevant, ownership, after loading
the row.

---

## 23. File-security test results

All new (section 21/24) and re-run pre-existing file-handling tests pass:
executable-as-PDF rejected (422), HTML-as-PNG rejected (422), a genuinely-swapped pair
of two individually-allowed types rejected (422 — the fix targeted in this phase),
genuine PDF/PNG/JPEG content accepted (201) to confirm the new check isn't a blanket
rejection, path-traversal filenames sanitized, double-extension filenames handled
safely (extension is never trusted for content-type decisions to begin with), and a
filename containing a right-to-left-override character, a zero-width space, and a
literal embedded null byte all sanitized without crashing the request.

---

## 24. Concurrency test results

- `test_concurrent_approve_and_reject_only_one_wins` — two threads, synchronized with
  a `threading.Barrier`, simultaneously POST approve and reject for the same
  `PENDING` request. With the new `_get_for_update_or_404()` row lock, exactly one
  succeeds (`200`) and the other gets `409 Conflict` on its re-read of the now-decided
  status — never both succeeding, never a corrupted mixed state.
- `test_concurrent_double_approve_only_one_succeeds` — same pattern, both threads
  approve; exactly one wins.
- This closes a real, previously-unguarded race: before this phase, both mutation
  routes used a bare `db.get()` with no lock, meaning two admins deciding the same
  request at (near-)the same instant could both read `PENDING` and both "succeed."

---

## 25. End-to-end test results

- `test_e2e_donor_rejection_then_resubmission` — submit → `PENDING` → admin rejects
  with a reason → reason visible to the donor → donor resubmits a fresh request →
  `PENDING` again.
- `test_e2e_hospital_verification_unlocks_emergency_target_facility_capability` —
  unverified hospital cannot be an emergency `target_facility_id` (400) → admin
  approves its `HOSPITAL_FACILITY` submission → the same hospital can now be targeted
  — proving the one real hard-gate in the app is genuinely driven by the backend
  verification decision, not a frontend flag.
- Donor submit→pending→review→approve→verified and document upload→private-storage→
  authorized-admin-review→unauthorized-access-rejected were already covered by the
  pre-existing `tests/test_verifications.py` (24 tests) and re-run clean in this
  phase's full regression (section 26).

---

## 26. Full regression result

Ran the **complete** existing test suite, not only the new Phase 14 file, per the
spec's explicit instruction:

- Baseline (matches Phase 13's own final verified count exactly): **558 passed, 0
  failed, 0 errors**.
- After all Phase 14 changes: **575 passed, 0 failed, 0 errors** — 558 pre-existing +
  17 new (16 initial + 1 added during the family-link authorization audit in section
  21).
- Zero pre-existing tests were modified, skipped, or deleted to make this pass.

(Note on ordering, in the interest of honesty: this project has no git repository, so
a strict pre-edit snapshot could not be captured retroactively. Two small edits —
magic-byte sniffing v1 and the row-locking helper — were made before the first full
baseline run recorded above; both were verified in isolation against the 24
pre-existing verification/certificate tests before the full-suite baseline run, and
the baseline figure matches Phase 13's own independently-verified final count exactly,
so no regression was introduced or masked by this ordering.)

---

## 27. TypeScript / ESLint / build / Playwright results

- `npx tsc --noEmit` — clean, no errors.
- `npm run lint` (ESLint) — clean, no errors or warnings.
- `npm run build` — succeeded (`vite build`, 2166 modules transformed); the only
  output is a pre-existing "chunk larger than 500 kB" advisory notice, not an error,
  unrelated to this phase's changes.
- **Playwright**: not present in this repository (no config, no dependency, no test
  directory) — not run, and not claimed to have been run, per the spec's explicit
  instruction.

---

## 28. Backend lint / type-check results

- `ruff check` on both changed/new files
  (`app/api/routes/verifications.py`, `tests/test_phase14_verification_security.py`):
  **0 errors** (two line-length issues introduced while writing the new test file
  were found and fixed in the same pass).
- `mypy app/` (whole app): **3 errors**, unchanged from the pre-existing baseline —
  two in `_apply_approval()` (a pre-existing function this phase never modified: mypy
  narrows `profile` to `DonorProfile` from the `if` branch and complains about the
  `else` branch's `HospitalProfile` reassignment) and one pre-existing, unrelated
  error in `app/api/routes/donors.py:219`. **Zero new mypy errors were introduced by
  this phase's changes.**

---

## 29. Production security review (manual, final pass)

- Authorization bypass: none found — every mutating/document route re-checks role
  server-side; no route trusts a client-supplied status or reviewer field.
- Predictable document paths: none — keys are `uuid4()`-suffixed and never served by
  path, only through the authorized-download route.
- IDOR: covered in section 22 — no object ID alone grants access.
- Path traversal / unsafe filenames: closed by pre-existing `_safe_filename()`,
  re-confirmed by new tests, never used as an actual filesystem path in the first
  place (S3/MinIO keys, or a guarded local fallback).
- Leaked sensitive metadata: `VerificationDocumentOut` never exposes the storage key;
  certificate public-verify never exposes private fields (both audited, unchanged).
- Client-controlled status/reviewer: impossible — neither field is client-writable
  (section 13, tested).
- Fake frontend state: none found in the production verification/certificate flow
  (section 18).
- Missing audit/notifications: none — both fire on every state-changing action
  (sections 19–20).
- Insecure certificate verification: none found (section 16).
- Missing expiry enforcement: expiry does not exist at all in this system and is
  documented as a limitation (section 15), not silently ignored.
- Race conditions: closed for approve/reject via new row locking (section 24).

---

## 30. Known limitations (stated explicitly, per the spec's instruction)

1. **Malware/antivirus scanning is not implemented.** The storage abstraction is
   structured so a scanning step could be added later (pre/post `put_bytes`) without
   redesigning the upload or download paths, but no scanner exists today.
2. **No automatic or manual expiry** for `APPROVED` verifications — once granted,
   verified status does not currently lapse. No `expires_at` field or background job
   exists for this; adding one is listed as future hardening below.
3. **No separate `REQUIRES_UPDATE` state** — `REJECTED` + mandatory reason +
   permitted resubmission already covers the same functional need (section 7/14).
4. **No `UNDER_REVIEW` state / no multi-admin claim workflow** — row-level locking
   fully closes the concurrent-decision race without it (section 6/24).
5. **No dedicated rate limit on admin review actions or document-index probing** —
   both are already effectively bounded (ADMIN-role-gated, and document access is
   addressed as `(verification_id, index)` rather than a globally-guessable ID), and
   the existing account-level submission rate limit (5/hour) was left as the only
   rate limit in this subsystem rather than inventing a new limiter with no clear
   abuse scenario driving it.
6. **The admin queue's "Advanced Filter" and "Export Log" buttons remain honestly
   disabled** — no backend endpoint exists for either; left as pre-existing, correctly
   honest incompleteness rather than building new unscoped features in this phase.

## Future hardening (not built in this phase)

- Real, time-based expiry for verified status, with a background job using the
  existing RQ worker infrastructure, plus a renewal flow.
- Optional malware-scanning hook in the storage abstraction's `put_bytes` path.
- A donor/hospital-facing document-listing (not just byte-download) endpoint, if a
  future need arises for applicants to review exactly what they submitted without
  re-deriving it from the metadata already returned on submission.

---

## 31. Commands executed (representative)

```
sudo -u postgres pg_ctlcluster 16 main start
redis-server --daemonize yes
pg_lsclusters; redis-cli ping

source .venv/bin/activate
ENVIRONMENT=test SECRET_KEY=test-secret RQ_SYNC=true pytest -q tests/test_phase14_verification_security.py -v
ENVIRONMENT=test SECRET_KEY=test-secret RQ_SYNC=true pytest -q tests/test_verifications.py tests/test_certificates.py
ENVIRONMENT=test SECRET_KEY=test-secret RQ_SYNC=true pytest -q            # full regression, 575 passed
ruff check app/api/routes/verifications.py tests/test_phase14_verification_security.py
mypy app/

cd ../frontend
npx tsc --noEmit
npm run lint
npm run build
```

---

## 32. Final status

**STATUS: COMPLETE WITH DOCUMENTED LIMITATIONS**

- **BASELINE**: 558 passed, 0 failed, 0 errors.
- **FINAL**: 575 passed, 0 failed, 0 errors.
- **NEW TESTS**: 17 (`tests/test_phase14_verification_security.py`).
- **MIGRATIONS**: none — no new enum values or columns were needed (section 6/8).
- **FILES CREATED**: `tests/test_phase14_verification_security.py`,
  `frontend/src/pages/hospital/FacilityVerification.tsx`,
  `LIFELINK_PHASE_14_REPORT.md`.
- **FILES MODIFIED**: `app/api/routes/verifications.py`, `frontend/src/App.tsx`,
  `frontend/src/pages/HospitalDashboard.tsx`.
- **VERIFICATION TYPES**: `DONOR_IDENTITY`, `HOSPITAL_FACILITY` (unchanged).
- **STATE MACHINE**: `PENDING → APPROVED | REJECTED` (unchanged; `UNDER_REVIEW` /
  `EXPIRED` / `REQUIRES_UPDATE` deliberately not added — reasons in sections 6–7, 14–15).
- **DOCUMENT SECURITY**: opaque server-generated storage keys, sanitized filenames,
  real magic-byte content-type verification (new), ADMIN-only authenticated download,
  no predictable paths.
- **AUTHORIZATION**: server-side role checks on every mutating/document route;
  family-link access confirmed (structurally and behaviorally) to never bypass
  verification-document authorization.
- **IDOR TESTS**: verification/document/certificate ID manipulation across
  donor↔donor, family↔donor, non-admin↔admin-queue — all denied appropriately
  (section 22).
- **FILE SECURITY TESTS**: executable/HTML disguised as allowed types, cross-type
  MIME swap, path traversal, double extension, null-byte/Unicode filenames — all
  handled correctly (section 23).
- **CONCURRENCY**: double-approve and approve-vs-reject races now resolve to exactly
  one winner via row-level locking (new in this phase).
- **AUDIT**: `verification.submitted` / `admin.verification_approved` /
  `admin.verification_rejected` / `certificate.access_denied` all reused as-is.
- **NOTIFICATIONS**: real, reused `notification_service.notify` calls on
  approved/rejected; `submitted` intentionally unnotified (section 19).
- **FRONTEND**: new hospital facility-verification page; TypeScript/ESLint/build all
  clean; Playwright not present, not run.
- **E2E**: donor reject→resubmit, hospital verify→unlock-target-facility, plus the
  pre-existing full donor/document/certificate E2E coverage — all passing.
- **KNOWN LIMITATIONS**: malware scanning not implemented; no automatic expiry; no
  `REQUIRES_UPDATE`/`UNDER_REVIEW` states; no dedicated admin-action/document-probe
  rate limit beyond the existing submission limiter — all stated explicitly above,
  none silently assumed or fabricated.
